"""DigitalOcean API: droplet and SSH key lifecycle."""

from __future__ import annotations

import io
import time
import uuid

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from rich.console import Console

console = Console()
DO_API = "https://api.digitalocean.com/v2"


class DropletError(Exception):
    pass


class EphemeralKey:
    """An RSA key pair created for a single session and cleaned up afterwards."""

    def __init__(self) -> None:
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=4096,
        )
        self._private_key = private_key
        self.public_openssh: str = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        ).decode()
        self.private_pem: bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def paramiko_key(self):
        import paramiko
        return paramiko.RSAKey.from_private_key(io.StringIO(self.private_pem.decode()))


class DigitalOceanClient:
    def __init__(self, token: str) -> None:
        self._token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _raise(self, r: requests.Response) -> None:
        try:
            msg = r.json().get("message", r.text)
        except Exception:
            msg = r.text
        raise DropletError(f"DO API {r.status_code}: {msg}")

    def _get(self, path: str) -> dict:
        r = requests.get(f"{DO_API}{path}", headers=self._headers)
        if not r.ok:
            self._raise(r)
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = requests.post(f"{DO_API}{path}", json=body, headers=self._headers)
        if not r.ok:
            self._raise(r)
        return r.json()

    def _delete(self, path: str) -> None:
        r = requests.delete(f"{DO_API}{path}", headers=self._headers)
        if r.status_code not in (200, 204):
            self._raise(r)

    # SSH keys ----------------------------------------------------------------

    def register_ssh_key(self, name: str, public_key: str) -> int:
        data = self._post("/account/keys", {"name": name, "public_key": public_key})
        key_id = data["ssh_key"]["id"]
        console.log(f"[dim]Registered ephemeral SSH key id={key_id}[/dim]")
        return key_id

    def delete_ssh_key(self, key_id: int) -> None:
        self._delete(f"/account/keys/{key_id}")
        console.log(f"[dim]Deleted SSH key id={key_id}[/dim]")

    # Droplets ----------------------------------------------------------------

    def create_droplet(
        self,
        name: str,
        size: str,
        ssh_key_id: int,
        region: str = "nyc3",
    ) -> int:
        body = {
            "name": name,
            "region": region,
            "size": size,
            "image": "ubuntu-22-04-x64",
            "ssh_keys": [ssh_key_id],
            "tags": ["digitaltwin"],
        }
        data = self._post("/droplets", body)
        droplet_id = data["droplet"]["id"]
        console.log(f"[dim]Created droplet id={droplet_id} size={size} region={region}[/dim]")
        return droplet_id

    def wait_for_active(self, droplet_id: int, timeout: int = 300) -> str:
        """Poll until droplet is active; return its public IPv4 address."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self._get(f"/droplets/{droplet_id}")
            droplet = data["droplet"]
            if droplet["status"] == "active":
                for net in droplet["networks"]["v4"]:
                    if net["type"] == "public":
                        return net["ip_address"]
            time.sleep(8)
        raise DropletError(f"Droplet {droplet_id} not active after {timeout}s")

    def destroy_droplet(self, droplet_id: int) -> None:
        self._delete(f"/droplets/{droplet_id}")
        console.log(f"[dim]Destroyed droplet id={droplet_id}[/dim]")

    def available_sizes(self, region: str) -> list[dict]:
        """Return sizes available in the given region, ordered by RAM."""
        data = self._get("/sizes?per_page=200")
        sizes = [
            s for s in data.get("sizes", [])
            if s.get("available") and region in s.get("regions", [])
        ]
        return sorted(sizes, key=lambda s: s["memory"])

    def resolve_size(self, wanted_slug: str, region: str) -> str:
        """Verify slug is available in region.

        If the exact slug isn't found, look for an available slug with the same
        RAM tier (handles old generic slugs like 's-4vcpu-8gb' → 's-4vcpu-8gb-amd').
        Raises DropletError with actionable suggestions if nothing matches.
        """
        available = self.available_sizes(region)
        slugs = {s["slug"] for s in available}

        if wanted_slug in slugs:
            return wanted_slug

        # Try to match by RAM: pull the RAM from the slug name (e.g. "8gb" → 8192)
        import re
        m = re.search(r"(\d+)gb", wanted_slug)
        if m:
            target_gb = int(m.group(1))
            target_mb = target_gb * 1024
            same_ram = [s["slug"] for s in available if s["memory"] == target_mb]
            if same_ram:
                chosen = same_ram[0]
                console.log(
                    f"[yellow]'{wanted_slug}' not found; using equivalent '{chosen}'[/yellow]"
                )
                return chosen

        # Nothing found — list candidates and fail
        sample = [s["slug"] for s in available if 4096 <= s["memory"] <= 32768][:10]
        raise DropletError(
            f"Size '{wanted_slug}' not available in region '{region}'.\n"
            f"  Sizes available (4–32 GB): {sample}"
        )


def make_session_name(topology_name: str) -> str:
    short = uuid.uuid4().hex[:6]
    return f"dt-{topology_name}-{short}"
