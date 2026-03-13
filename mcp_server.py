#!/usr/bin/env python3
"""
netaudit — MCP Server for validating ContainerLab topologies via natural language.
Allows Claude/LLMs to deploy and audit network topologies on DigitalOcean.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import Tool, TextContent
import mcp.server.stdio

from digitaltwin import cloud, diagnostics, lab, report, topology
from digitaltwin.provision import SSHClient, install_dependencies
from digitaltwin.cloud import EphemeralKey


# Global state for managing active runs
_active_runs: dict[str, dict[str, Any]] = {}


def _get_token() -> str:
    """Retrieve DigitalOcean API token from environment."""
    for var in ("DO_API_TOKEN", "do_api_token", "DO_API_KEY", "do_api_key"):
        token = os.environ.get(var)
        if token:
            return token
    raise ValueError(
        "No DigitalOcean API token found. "
        "Set one of: DO_API_TOKEN, do_api_token, DO_API_KEY, do_api_key"
    )


def _list_topologies() -> list[str]:
    """Return list of available topology files."""
    examples_dir = Path("examples")
    if not examples_dir.exists():
        return []
    return [f.stem for f in examples_dir.glob("*.yml")]


def _parse_test_request(request: str, topology_name: str) -> dict[str, Any]:
    """
    Parse a natural language test request and extract parameters.
    Returns a dict with topology_file, region, and other relevant options.
    """
    # For now, use sensible defaults. The LLM will get feedback if specifics are wrong.
    return {
        "topology_file": Path("examples") / f"{topology_name}.yml",
        "region": "nyc3",  # Default region
        "size": None,  # Let topology decide
        "output": Path("results"),
        "keep_vm": False,
        "skip_destroy_lab": False,
    }


async def handle_list_topologies(arguments: dict) -> str:
    """Handle list_topologies tool call."""
    try:
        topologies = _list_topologies()
        if not topologies:
            return "No topology files found in examples/ directory."
        else:
            return "Available topologies:\n" + "\n".join(f"  - {t}" for t in topologies)
    except Exception as e:
        return f"Error listing topologies: {e}"


async def handle_run_test(arguments: dict) -> str:
    """Handle run_test tool call."""
    try:
        topology_name = arguments.get("topology")
        request = arguments.get("request", "")

        if not topology_name:
            return "Error: 'topology' parameter required"

        # Validate topology exists
        available = _list_topologies()
        if topology_name not in available:
            return (
                f"Error: topology '{topology_name}' not found. "
                f"Available: {', '.join(available)}"
            )

        # Parse the test request
        params = _parse_test_request(request, topology_name)

        # Get token
        token = _get_token()
        do = cloud.DigitalOceanClient(token)

        # Load topology
        topo = topology.load(params["topology_file"])

        # Generate session name and ephemeral SSH key
        session_name = cloud.make_session_name(topo.name)
        ephem_key = EphemeralKey()
        ssh_key_id = do.register_ssh_key(session_name, ephem_key.public_openssh)

        droplet_id: int | None = None
        ssh_client: SSHClient | None = None

        try:
            # Create droplet
            droplet_id = do.create_droplet(
                session_name,
                params["size"] or topo.droplet_size(),
                ssh_key_id,
                params["region"],
            )

            # Wait for droplet to be active
            ip = do.wait_for_active(droplet_id)

            # SSH and provision
            pkey = ephem_key.paramiko_key()
            ssh_client = SSHClient(ip, username="root", pkey=pkey)
            ssh_client.connect()
            install_dependencies(ssh_client)

            # Deploy lab
            node_ips = lab.deploy(ssh_client, topo)
            lab.wait_for_nodes(ssh_client, node_ips)

            # Collect diagnostics
            diag = diagnostics.collect(
                ssh_client,
                topo.name,
                topo.nodes,
                node_ips,
                topo.node_pairs(),
            )

            # Save results
            run_dir = report.save(diag, topo, params["output"])

            # Store result metadata
            result_id = f"{session_name}_{run_dir.name}"
            _active_runs[result_id] = {
                "topology": topology_name,
                "request": request,
                "status": "completed",
                "result_dir": str(run_dir),
            }

            # Compose summary
            summary = report.format_summary(diag, run_dir)
            return summary

        finally:
            # 8. Tear down lab (unless skipped)
            if ssh_client is not None:
                try:
                    lab.destroy(ssh_client)
                except Exception as e:
                    pass
                finally:
                    ssh_client.close()

            # 9. Destroy VM (unless keeping)
            if droplet_id is not None and not params["keep_vm"]:
                try:
                    do.destroy_droplet(droplet_id)
                except Exception:
                    pass

            # 10. Clean up SSH key
            try:
                do.delete_ssh_key(ssh_key_id)
            except Exception:
                pass

    except Exception as e:
        return f"Error running test: {e}"


def create_server() -> Server:
    """Create and configure the MCP server."""
    server = Server("netaudit")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="list_topologies",
                description="List available ContainerLab topologies that can be deployed.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="run_test",
                description=(
                    "Spin up a ContainerLab topology on DigitalOcean, run diagnostics, "
                    "and collect results. Provide a natural language description of what "
                    "test or validation you want to run on the topology."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "topology": {
                            "type": "string",
                            "description": "Name of the topology to deploy (without .yml extension)",
                        },
                        "request": {
                            "type": "string",
                            "description": "Natural language description of the test or validation to perform",
                        },
                    },
                    "required": ["topology", "request"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        if name == "list_topologies":
            return await handle_list_topologies(arguments)
        elif name == "run_test":
            return await handle_run_test(arguments)
        else:
            return f"Unknown tool: {name}"

    return server


if __name__ == "__main__":
    server = create_server()
    mcp.server.stdio.stdio_server(server)
