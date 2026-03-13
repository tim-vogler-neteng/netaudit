# netaudit — MCP Server

Turn your ContainerLab topology testing into an LLM-powered validation workflow. This MCP (Model Context Protocol) server allows Claude and other LLMs to deploy network topologies and verify their correctness using natural language requests.

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure DigitalOcean API Token

The MCP server needs a DigitalOcean API token. Set one of these environment variables:

```bash
export DO_API_TOKEN="your-token-here"
# or
export do_api_token="your-token-here"
```

### 3. Add to Claude Desktop (if using Claude)

Edit `~/.config/Claude/claude_desktop_config.json` and add:

```json
{
  "mcpServers": {
    "netaudit": {
      "command": "python",
      "args": ["/path/to/digitaltwin/mcp_server.py"]
    }
  }
}
```

Then restart Claude Desktop.

## Usage

### Via Claude Desktop

Once configured, you can ask Claude things like:

> "Deploy the srl-simple topology and run connectivity diagnostics"
> "Set up the network topology and verify all nodes can reach each other"
> "Run tests on the srl-simple topology to check routing"

Claude will:
1. Use the MCP tools to list available topologies
2. Provision a DigitalOcean droplet
3. Deploy your ContainerLab topology
4. Run diagnostics (ping tests, interface info, routing tables)
5. Collect results
6. Clean up resources
7. Report findings back to you

### Via Command Line (for testing)

```bash
python mcp_server.py
```

This starts the server on stdio, which you can interact with directly for debugging.

## Available MCP Tools

### `list_topologies`

Lists all available topology files in the `examples/` directory.

**Example call:**
```
Tool: list_topologies
Arguments: {}
```

### `run_test`

Deploys a topology and runs diagnostics based on your request.

**Parameters:**
- `topology` (required): Name of topology (e.g., `srl-simple`)
- `request` (required): Natural language description of what to test (e.g., "verify all nodes are connected and can ping each other")

**Example call:**
```
Tool: run_test
Arguments: {
  "topology": "srl-simple",
  "request": "verify all nodes can reach each other via ping"
}
```

## Workflow

1. **You ask a question in natural language** → Claude reads your request
2. **Claude calls `list_topologies`** → Gets available options
3. **Claude calls `run_test`** → Provisions VM, deploys lab, runs diagnostics
4. **The server** → Handles entire lifecycle (provision → deploy → test → cleanup)
5. **Claude reports results** → Summarizes findings and insights

## Cost & Safety

- **No long-running resources**: VMs are automatically destroyed after each test (unless `--keep-vm` specified)
- **Ephemeral SSH keys**: Keys are created and destroyed per session
- **Natural language only**: No arbitrary code execution—only predefined workflows
- **Resource limits**: Topology determines VM size automatically

## Troubleshooting

### "No DigitalOcean API token found"
Set your token in environment:
```bash
export DO_API_TOKEN="your-token"
```

### "Topology not found"
Make sure your `.yml` files are in the `examples/` directory.

### SSH connection timeout
Your DigitalOcean droplet may be taking time to boot. The server has exponential backoff built in, but very large topologies can take 2-3 minutes to spin up.

## Architecture

```
Claude Desktop
    ↓ (MCP protocol)
mcp_server.py (this file)
    ↓
digitaltwin framework (existing code)
    ├── cloud.py (DigitalOcean API)
    ├── lab.py (ContainerLab deployment) 
    ├── diagnostics.py (collect test results)
    ├── report.py (format & save results)
    └── topology.py (parse .yml files)
    ↓
DigitalOcean droplet
    ↓
ContainerLab deployment
    ↓
Diagnostics (ping tests, interface state, routing)
```

## Limitations (MVP)

- ✅ Works with existing topologies only (no on-the-fly topology generation)
- ✅ Text requests only (no Python script execution)
- ✅ Default region/sizing (can be extended)
- ✅ Basic diagnostics (ping, interface info, routing tables)

## Future Enhancements

- Custom test scripts via structured descriptions
- Regional preferences and performance tuning
- Multi-topology deployments
- Test result analysis and ML-based recommendations
- Historical tracking and comparison
