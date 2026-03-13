"""SSH provisioning: install Docker and ContainerLab on a fresh Ubuntu droplet."""

from __future__ import annotations

import socket
import time

import paramiko
from rich.console import Console

console = Console()

_INSTALL_SCRIPT = """\
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# Wait for system to be ready and update package lists
echo "[1/5] Waiting for system initialization..."
for i in {1..30}; do
  if apt-get update 2>&1 | grep -q "packages can be upgraded"; then
    echo "[1/5] ✓ System ready"
    break
  fi
  echo "[1/5] Waiting... (attempt $i/30)"
  sleep 10
done

# Ensure GPG keys are up to date
echo "[2/5] Updating GPG keys..."
apt-get install -y ubuntu-keyring 2>/dev/null || true
apt-get update --allow-insecure-repositories --allow-unauthenticated || true
echo "[2/5] ✓ GPG keys updated"

# Docker
echo "[3/5] Installing Docker..."
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker
echo "[3/5] ✓ Docker installed and started"

# ContainerLab
echo "[4/5] Installing ContainerLab..."
bash -c "$(curl -sL https://get.containerlab.dev)"
echo "[4/5] ✓ ContainerLab installed"

echo "[5/5] ✓ All dependencies installed successfully"
"""


class SSHClient:
    def __init__(self, ip: str, username: str, pkey) -> None:
        self._ip = ip
        self._username = username
        self._pkey = pkey
        self._client: paramiko.SSHClient | None = None

    def connect(self, timeout: int = 180) -> None:
        """Retry until SSH is reachable (droplet cloud-init takes ~30-60s)."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        deadline = time.time() + timeout
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                client.connect(
                    self._ip,
                    username=self._username,
                    pkey=self._pkey,
                    timeout=10,
                    banner_timeout=30,
                    auth_timeout=30,
                )
                self._client = client
                console.log(f"[dim]SSH connected to {self._ip}[/dim]")
                return
            except (paramiko.ssh_exception.NoValidConnectionsError, OSError, socket.timeout) as e:
                last_error = e
                time.sleep(8)
        raise TimeoutError(f"SSH to {self._ip} timed out: {last_error}")

    def run(self, cmd: str, timeout: int = 600) -> tuple[int, str, str]:
        """Run command, return (exit_code, stdout, stderr)."""
        assert self._client, "Not connected"
        _, stdout, stderr = self._client.exec_command(cmd, timeout=timeout, get_pty=True)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        code = stdout.channel.recv_exit_status()
        return code, out, err

    def run_checked(self, cmd: str, timeout: int = 600) -> str:
        code, out, err = self.run(cmd, timeout=timeout)
        if code != 0:
            raise RuntimeError(f"Command failed (exit {code}): {cmd}\n{out}\n{err}")
        return out

    def put_file(self, local_path: str, remote_path: str) -> None:
        assert self._client
        sftp = self._client.open_sftp()
        try:
            sftp.put(local_path, remote_path)
        finally:
            sftp.close()

    def put_bytes(self, data: bytes, remote_path: str) -> None:
        import io
        assert self._client
        sftp = self._client.open_sftp()
        try:
            sftp.putfo(io.BytesIO(data), remote_path)
        finally:
            sftp.close()

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None


def install_dependencies(ssh: SSHClient) -> None:
    console.log("Installing Docker and ContainerLab (this takes ~2 minutes)...")
    console.log("[dim]Step 1: Waiting for system to be ready...[/dim]")
    try:
        output = ssh.run_checked(_INSTALL_SCRIPT, timeout=300)
        # Print progress output from the script
        for line in output.split('\n'):
            if line.strip():
                console.log(f"[dim]{line}[/dim]")
        console.log("[green]Dependencies installed successfully.[/green]")
    except RuntimeError as e:
        console.log("[red]Installation failed![/red]")
        raise
