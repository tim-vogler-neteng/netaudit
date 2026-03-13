#!/usr/bin/env python3
"""
digitaltwin — spin up a ContainerLab topology on DigitalOcean, collect
diagnostics, then tear everything down.

Usage:
    python main.py run examples/srl-simple.yml
    python main.py run examples/srl-simple.yml --region sfo3 --keep-vm
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
from rich.console import Console

from digitaltwin import cloud, diagnostics, lab, report, topology

console = Console()


def _get_token() -> str:
    for var in ("DO_API_TOKEN", "do_api_token", "DO_API_KEY", "do_api_key"):
        token = os.environ.get(var)
        if token:
            return token
    console.print(
        "[red]Error:[/red] No DigitalOcean API token found.\n"
        "Set one of: DO_API_TOKEN, do_api_token, DO_API_KEY, do_api_key"
    )
    sys.exit(1)


@click.group()
def cli():
    """Digital Twin — ContainerLab on DigitalOcean."""


@cli.command()
@click.argument("topology_file", type=click.Path(exists=True, path_type=Path))
@click.option("--region", default="nyc3", show_default=True, help="DigitalOcean region slug")
@click.option("--size", default=None, help="Override VM size slug (e.g. s-4vcpu-8gb)")
@click.option("--output", default=None, type=click.Path(path_type=Path), help="Results output directory")
@click.option("--keep-vm", is_flag=True, default=False, help="Do not destroy the VM when done")
@click.option("--skip-destroy-lab", is_flag=True, default=False, help="Leave the lab running on the VM")
def run(
    topology_file: Path,
    region: str,
    size: str | None,
    output: Path | None,
    keep_vm: bool,
    skip_destroy_lab: bool,
):
    """Provision a VM, run a ContainerLab topology, collect diagnostics, clean up."""

    token = _get_token()
    do = cloud.DigitalOceanClient(token)

    # 1. Parse topology
    console.rule("[bold]Parsing Topology")
    topo = topology.load(topology_file)
    auto_size = topo.droplet_size()
    size = size or auto_size
    console.print(f"  Topology : [cyan]{topo.name}[/cyan]")
    console.print(f"  Nodes    : {topo.node_count}")
    console.print(f"  RAM est. : {topo.required_ram_gb():.1f} GB")
    console.print(f"  VM size  : [cyan]{size}[/cyan]  region=[cyan]{region}[/cyan]")

    # Verify the size exists in the chosen region before spending time on the SSH key
    console.rule("[bold]Validating VM Size")
    try:
        size = do.resolve_size(size, region)
        console.log(f"[green]Size '{size}' confirmed available in {region}.[/green]")
    except cloud.DropletError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    # 2. Generate ephemeral SSH key
    console.rule("[bold]Creating SSH Key")
    ephem_key = cloud.EphemeralKey()
    session_name = cloud.make_session_name(topo.name)
    ssh_key_id = do.register_ssh_key(session_name, ephem_key.public_openssh)

    droplet_id: int | None = None
    ssh_client: "cloud.SSHClient | None" = None  # noqa: F821 — imported below

    from digitaltwin.provision import SSHClient, install_dependencies

    try:
        # 3. Create droplet
        console.rule("[bold]Creating Droplet")
        droplet_id = do.create_droplet(session_name, size, ssh_key_id, region)

        console.log("Waiting for droplet to become active...")
        ip = do.wait_for_active(droplet_id)
        console.log(f"Droplet active at [cyan]{ip}[/cyan]")

        # 4. SSH + provision
        console.rule("[bold]Provisioning")
        pkey = ephem_key.paramiko_key()
        ssh_client = SSHClient(ip, username="root", pkey=pkey)
        ssh_client.connect()
        install_dependencies(ssh_client)

        # 5. Deploy lab
        console.rule("[bold]Deploying Lab")
        node_ips = lab.deploy(ssh_client, topo)
        console.log(f"Node management IPs: {node_ips}")
        lab.wait_for_nodes(ssh_client, node_ips)

        # 6. Collect diagnostics
        console.rule("[bold]Collecting Diagnostics")
        diag = diagnostics.collect(
            ssh_client,
            topo.name,
            topo.nodes,
            node_ips,
            topo.node_pairs(),
        )

        # 7. Save results
        run_dir = report.save(diag, topo, output)
        report.print_summary(diag, run_dir)

    finally:
        # 8. Tear down lab (unless skipped)
        if ssh_client is not None and not skip_destroy_lab:
            try:
                console.rule("[bold]Tearing Down Lab")
                lab.destroy(ssh_client)
            except Exception as e:
                console.log(f"[yellow]Lab destroy failed: {e}[/yellow]")
            finally:
                ssh_client.close()

        # 9. Destroy VM (unless keeping)
        if droplet_id is not None and not keep_vm:
            console.rule("[bold]Destroying VM")
            do.destroy_droplet(droplet_id)

        # 10. Clean up SSH key
        try:
            do.delete_ssh_key(ssh_key_id)
        except Exception:
            pass

    console.rule("[bold green]Done")


@cli.command("show-size")
@click.argument("topology_file", type=click.Path(exists=True, path_type=Path))
def show_size(topology_file: Path):
    """Print the computed VM size for a topology without running anything."""
    topo = topology.load(topology_file)
    console.print(f"Topology : {topo.name}")
    console.print(f"Nodes    : {topo.node_count}")
    for node in topo.nodes:
        console.print(f"  {node.name:20s}  kind={node.kind}  image={node.image}")
    console.print(f"RAM est. : {topo.required_ram_gb():.1f} GB")
    console.print(f"VM size  : {topo.droplet_size()}")


if __name__ == "__main__":
    cli()
