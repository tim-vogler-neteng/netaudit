"""Write diagnostic results to disk."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .diagnostics import LabDiagnostics
from .topology import Topology

console = Console()


def save(diag: LabDiagnostics, topo: Topology, output_dir: Path | None = None) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (output_dir or Path("results")) / f"{topo.name}-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Copy topology file
    (run_dir / "topology.yml").write_bytes(topo.source_path.read_bytes())

    # Per-node files
    nodes_dir = run_dir / "nodes"
    nodes_dir.mkdir()
    for nd in diag.nodes:
        node_dir = nodes_dir / nd.node
        node_dir.mkdir()
        (node_dir / "routing_table.txt").write_text(nd.routing_table)
        (node_dir / "interfaces.txt").write_text(nd.interfaces)
        for key, val in nd.raw.items():
            (node_dir / f"{key}.txt").write_text(val)

    # Ping files
    pings_dir = run_dir / "pings"
    pings_dir.mkdir()
    for p in diag.pings:
        fname = f"{p.src_node}_to_{p.dst_node}.txt"
        (pings_dir / fname).write_text(p.output)

    # Summary JSON
    summary = {
        "topology": topo.name,
        "timestamp": timestamp,
        "nodes": [
            {"name": nd.node, "kind": nd.kind, "mgmt_ip": nd.mgmt_ip}
            for nd in diag.nodes
        ],
        "pings": [
            {
                "src": p.src_node,
                "dst": p.dst_node,
                "dst_ip": p.dst_ip,
                "success": p.success,
            }
            for p in diag.pings
        ],
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    return run_dir


def print_summary(diag: LabDiagnostics, run_dir: Path) -> None:
    console.print()
    console.rule("[bold]Diagnostics Summary")

    # Nodes table
    node_table = Table(title="Nodes", show_header=True, header_style="bold cyan")
    node_table.add_column("Node")
    node_table.add_column("Kind")
    node_table.add_column("Mgmt IP")
    for nd in diag.nodes:
        node_table.add_row(nd.node, nd.kind, nd.mgmt_ip)
    console.print(node_table)

    # Ping table
    if diag.pings:
        ping_table = Table(title="Ping Tests", show_header=True, header_style="bold cyan")
        ping_table.add_column("Source")
        ping_table.add_column("Destination")
        ping_table.add_column("Dest IP")
        ping_table.add_column("Result")
        for p in diag.pings:
            result = "[green]PASS[/green]" if p.success else "[red]FAIL[/red]"
            ping_table.add_row(p.src_node, p.dst_node, p.dst_ip, result)
        console.print(ping_table)

    console.print(f"\nResults saved to: [bold]{run_dir}[/bold]")


def format_summary(diag: LabDiagnostics, run_dir: Path) -> str:
    """Format diagnostics summary as plain text string."""
    lines = ["=== Diagnostics Summary ===", ""]

    # Nodes
    lines.append("Nodes:")
    lines.append(f"{'Node':<20} {'Kind':<15} {'Mgmt IP':<20}")
    lines.append("-" * 55)
    for nd in diag.nodes:
        lines.append(f"{nd.node:<20} {nd.kind:<15} {nd.mgmt_ip:<20}")
    lines.append("")

    # Ping results
    if diag.pings:
        lines.append("Ping Tests:")
        lines.append(f"{'Source':<20} {'Destination':<20} {'Dest IP':<20} {'Result':<10}")
        lines.append("-" * 70)
        for p in diag.pings:
            result = "PASS" if p.success else "FAIL"
            lines.append(f"{p.src_node:<20} {p.dst_node:<20} {p.dst_ip:<20} {result:<10}")
        lines.append("")

    lines.append(f"Results saved to: {run_dir}")
    return "\n".join(lines)
