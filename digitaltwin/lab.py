"""ContainerLab lifecycle: deploy, inspect, destroy."""

from __future__ import annotations

import json
import re
import time

from rich.console import Console

from .provision import SSHClient
from .topology import Topology

console = Console()

_REMOTE_TOPO = "/root/topology.yml"


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    # Remove various ANSI escape patterns
    ansi_escape = re.compile(r'[\x1B\x0E\x0F](?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|[0-9;]*[a-zA-Z])?')
    return ansi_escape.sub('', text)


def _extract_json(text: str) -> dict | list | None:
    """Extract valid JSON from text that may contain other content."""
    text = text.strip()
    
    # Try to find and parse JSON object or array
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start_idx = text.find(start_char)
        if start_idx == -1:
            continue
        
        # Find matching closing bracket
        depth = 0
        for i in range(start_idx, len(text)):
            if text[i] == start_char:
                depth += 1
            elif text[i] == end_char:
                depth -= 1
                if depth == 0:
                    # Try to parse from start_idx to i+1
                    try:
                        return json.loads(text[start_idx:i+1])
                    except json.JSONDecodeError:
                        pass
    
    return None



def deploy(ssh: SSHClient, topo: Topology) -> dict[str, str]:
    """
    Upload topology file, pull images, run clab deploy.
    Returns a mapping of node name → management IP.
    """
    console.log("Uploading topology file...")
    ssh.put_file(str(topo.source_path), _REMOTE_TOPO)

    # Pull images explicitly so we can log progress (clab would do this anyway)
    images = {node.image for node in topo.nodes if node.image}
    for image in images:
        console.log(f"Pulling image [cyan]{image}[/cyan]...")
        ssh.run_checked(f"docker pull {image}", timeout=600)

    console.log(f"Deploying lab [bold]{topo.name}[/bold]...")
    ssh.run_checked(f"clab deploy --topo {_REMOTE_TOPO} --reconfigure", timeout=300)
    console.log("[green]Lab deployed.[/green]")

    return _get_node_ips(ssh, topo.name)


def _get_node_ips(ssh: SSHClient, topo_name: str) -> dict[str, str]:
    """Return {node_name: mgmt_ip} from clab inspect output."""
    out = ssh.run_checked(
        f"clab inspect --name {topo_name} --format json",
        timeout=60,
    )
    
    # Strip ANSI escape sequences that may be in the output
    out = _strip_ansi(out)
    
    # Debug: show what we got
    if not out or not out.strip():
        console.log("[yellow]Warning: clab inspect returned empty output. Lab may not have deployed.[/yellow]")
        return {}
    
    # Extract valid JSON from potentially truncated output
    data = _extract_json(out)
    
    if data is None:
        console.log("[yellow]Warning: Could not extract valid JSON from clab inspect output.[/yellow]")
        console.log(f"[dim]Raw output ({len(out)} chars):[/dim]")
        console.log(f"[dim]{out[:500]}[/dim]")
        return {}

    # Debug: show what we extracted
    console.log(f"[dim]Extracted JSON type: {type(data).__name__}[/dim]")
    if isinstance(data, dict):
        console.log(f"[dim]Dict keys: {list(data.keys())}[/dim]")

    node_ips: dict[str, str] = {}
    containers = data if isinstance(data, list) else data.get("containers", [])
    
    # Handle nested structure like {"srl-simple": [...]}
    if isinstance(data, dict) and not containers:
        # If we have a dict with one key, that might be the topology name
        for key, value in data.items():
            if isinstance(value, list):
                console.log(f"[dim]Found containers under key '{key}'[/dim]")
                containers = value
                break
    
    console.log(f"[dim]Processing {len(containers)} containers[/dim]")
    
    for entry in containers:
        if not isinstance(entry, dict):
            continue
            
        # clab container names: clab-<topo>-<node>
        long_name: str = entry.get("name", "")
        prefix = f"clab-{topo_name}-"
        short = long_name.removeprefix(prefix).lstrip("/")
        
        # Try mgmt_ipv4_address first, then ipv4_address
        ip = entry.get("mgmt_ipv4_address", "").split("/")[0]
        if not ip:
            ip = entry.get("ipv4_address", "").split("/")[0]
        
        if short and ip:
            node_ips[short] = ip
            console.log(f"[dim]  Found: {short} → {ip}[/dim]")
    
    if node_ips:
        console.log(f"[green]Found {len(node_ips)} nodes with IPs:[/green]")
        for name, ip in node_ips.items():
            console.log(f"  [cyan]{name}[/cyan] → {ip}")
    else:
        console.log("[yellow]Warning: No node IPs found in clab output.[/yellow]")
    
    return node_ips


def wait_for_nodes(ssh: SSHClient, node_ips: dict[str, str], timeout: int = 120) -> None:
    """Wait until all node containers are running."""
    console.log("Waiting for nodes to be running...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, out, _ = ssh.run("docker ps --format '{{.Names}}\t{{.Status}}'")
        lines = [l for l in out.splitlines() if l.strip()]
        running = sum(1 for l in lines if "Up" in l and any(n in l for n in node_ips))
        if running >= len(node_ips):
            console.log("[green]All nodes running.[/green]")
            return
        time.sleep(10)
    console.log("[yellow]Warning: not all nodes confirmed running — proceeding anyway.[/yellow]")


def destroy(ssh: SSHClient) -> None:
    console.log("Destroying lab...")
    ssh.run_checked(f"clab destroy --topo {_REMOTE_TOPO} --cleanup", timeout=120)
    console.log("[green]Lab destroyed.[/green]")
