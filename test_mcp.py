#!/usr/bin/env python3
"""
Simple test client for the netaudit MCP server.
Useful for local testing without Claude Desktop.
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path


async def run_mcp_tool(tool_name: str, arguments: dict) -> str:
    """
    Call an MCP tool and return the result.
    This simulates what Claude would do.
    """
    # Create the request JSON
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    # Start the MCP server
    proc = subprocess.Popen(
        [sys.executable, "mcp_server.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Send the request
    try:
        stdout, stderr = proc.communicate(
            json.dumps(request) + "\n",
            timeout=300,  # 5 minute timeout
        )
        if stderr:
            print(f"stderr: {stderr}", file=sys.stderr)
        return stdout
    except subprocess.TimeoutExpired:
        proc.kill()
        return "ERROR: timeout"


async def main():
    print("=== netaudit MCP Test Client ===\n")

    # Test 1: List topologies
    print("Test 1: Listing topologies...")
    result = await run_mcp_tool("list_topologies", {})
    print(f"Result:\n{result}\n")

    # Test 2: Run a test (if you have a topology file)
    print("Test 2: Running test on srl-simple topology...")
    result = await run_mcp_tool(
        "run_test",
        {
            "topology": "srl-simple",
            "request": "verify all nodes are reachable and can ping each other",
        },
    )
    print(f"Result:\n{result}\n")


if __name__ == "__main__":
    asyncio.run(main())
