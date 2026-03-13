"""Collect diagnostics from running ContainerLab nodes.

Dispatcher pattern: each node kind registers a collector class.
Adding support for a new NOS = subclass NodeCollector and register it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from rich.console import Console

from .provision import SSHClient
from .topology import NodeInfo

console = Console()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class NodeDiagnostics:
    node: str
    kind: str
    mgmt_ip: str
    routing_table: str = ""
    interfaces: str = ""
    raw: dict[str, str] = field(default_factory=dict)


@dataclass
class PingResult:
    src_node: str
    dst_node: str
    dst_ip: str
    output: str
    success: bool


@dataclass
class LabDiagnostics:
    nodes: list[NodeDiagnostics] = field(default_factory=list)
    pings: list[PingResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-kind collectors
# ---------------------------------------------------------------------------

class NodeCollector(Protocol):
    def collect(
        self,
        ssh: SSHClient,
        container_name: str,
        node: NodeInfo,
        mgmt_ip: str,
    ) -> NodeDiagnostics: ...

    def ping(
        self,
        ssh: SSHClient,
        container_name: str,
        src_node: str,
        dst_node: str,
        dst_ip: str,
    ) -> PingResult: ...


class SRLinuxCollector:
    """Nokia SR Linux (kind: srl)."""

    def _cli(self, ssh: SSHClient, container: str, cmd: str, timeout: int = 30) -> str:
        full = f'docker exec {container} sr_cli "{cmd}"'
        _, out, _ = ssh.run(full, timeout=timeout)
        return out

    def collect(self, ssh, container_name, node, mgmt_ip):
        diag = NodeDiagnostics(node=node.name, kind=node.kind, mgmt_ip=mgmt_ip)
        diag.routing_table = self._cli(
            ssh, container_name,
            "show network-instance default route-table"
        )
        diag.interfaces = self._cli(
            ssh, container_name,
            "show interface brief"
        )
        diag.raw["bgp_summary"] = self._cli(
            ssh, container_name,
            "show network-instance default protocols bgp summary"
        )
        return diag

    def ping(self, ssh, container_name, src_node, dst_node, dst_ip):
        cmd = (
            f'docker exec {container_name} sr_cli '
            f'"ping {dst_ip} network-instance mgmt count 5"'
        )
        _, out, _ = ssh.run(cmd, timeout=30)
        success = "5 received" in out or "0% packet loss" in out
        return PingResult(
            src_node=src_node,
            dst_node=dst_node,
            dst_ip=dst_ip,
            output=out,
            success=success,
        )


class LinuxCollector:
    """Generic Linux container (kind: linux, frr, etc.)."""

    def collect(self, ssh, container_name, node, mgmt_ip):
        diag = NodeDiagnostics(node=node.name, kind=node.kind, mgmt_ip=mgmt_ip)
        _, diag.routing_table, _ = ssh.run(
            f"docker exec {container_name} ip route show", timeout=15
        )
        _, diag.interfaces, _ = ssh.run(
            f"docker exec {container_name} ip link show", timeout=15
        )
        return diag

    def ping(self, ssh, container_name, src_node, dst_node, dst_ip):
        cmd = f"docker exec {container_name} ping -c 5 -W 2 {dst_ip}"
        _, out, _ = ssh.run(cmd, timeout=20)
        success = "5 received" in out or "0% packet loss" in out
        return PingResult(
            src_node=src_node,
            dst_node=dst_node,
            dst_ip=dst_ip,
            output=out,
            success=success,
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_COLLECTORS: dict[str, NodeCollector] = {
    "srl": SRLinuxCollector(),
    "linux": LinuxCollector(),
    "frr": LinuxCollector(),
}
_DEFAULT_COLLECTOR = LinuxCollector()


def _collector_for(kind: str) -> NodeCollector:
    return _COLLECTORS.get(kind, _DEFAULT_COLLECTOR)


def _container_name(topo_name: str, node_name: str) -> str:
    return f"clab-{topo_name}-{node_name}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def collect(
    ssh: SSHClient,
    topo_name: str,
    nodes: list[NodeInfo],
    node_ips: dict[str, str],
    node_pairs: list[tuple[str, str]],
) -> LabDiagnostics:
    result = LabDiagnostics()

    # Per-node diagnostics
    for node in nodes:
        mgmt_ip = node_ips.get(node.name, "")
        container = _container_name(topo_name, node.name)
        collector = _collector_for(node.kind)
        console.log(f"Collecting diagnostics from [cyan]{node.name}[/cyan] ({node.kind})...")
        try:
            diag = collector.collect(ssh, container, node, mgmt_ip)
            result.nodes.append(diag)
        except Exception as e:
            console.log(f"[yellow]Warning: diagnostics for {node.name} failed: {e}[/yellow]")

    # Ping tests between linked node pairs
    node_kind_map = {n.name: n.kind for n in nodes}
    for src_name, dst_name in node_pairs:
        dst_ip = node_ips.get(dst_name, "")
        if not dst_ip:
            continue
        src_container = _container_name(topo_name, src_name)
        src_kind = node_kind_map.get(src_name, "linux")
        collector = _collector_for(src_kind)
        console.log(
            f"Ping [cyan]{src_name}[/cyan] → [cyan]{dst_name}[/cyan] ({dst_ip})..."
        )
        try:
            ping = collector.ping(ssh, src_container, src_name, dst_name, dst_ip)
            result.pings.append(ping)
            status = "[green]OK[/green]" if ping.success else "[red]FAIL[/red]"
            console.log(f"  {status}")
        except Exception as e:
            console.log(f"[yellow]Warning: ping {src_name}→{dst_name} failed: {e}[/yellow]")

    return result
