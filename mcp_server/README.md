# Remote Host CLI MCP server

A standalone stdio MCP server that talks to **aw-backend** (the control
plane, `AW_BACKEND_URL` — default `http://127.0.0.1:9025`), not the local
aw-workspace API. It authenticates as this workspace using its own
`AW_WORKSPACE_HOST_TOKEN` — see [`../docs/backend-auth.md`](../docs/backend-auth.md)
for the full pattern and why it's scoped to only this account's own linked
hosts.

## Requirements

```
pip install httpx
```

## Configuration

| Env var | Required | Default | Purpose |
|---|---|---|---|
| `AW_BACKEND_URL` | no | `http://127.0.0.1:9025` | Base URL of aw-backend. |
| `AW_WORKSPACE` | yes | — | This workspace's slug. |
| `AW_WORKSPACE_HOST_TOKEN` | yes | — | This workspace's `awlk_` host credential. |

Inside a real aw-workspace container all three are already set (the same
ones `src/apps/registry_client.py` reads) — no extra config needed there.

## Run

```bash
AW_WORKSPACE=<your-workspace-slug> \
AW_WORKSPACE_HOST_TOKEN=<your awlk_ token> \
python -m mcp_server.server
```

Wire it into an MCP client's config as a stdio server:

```json
{
  "mcpServers": {
    "aw-app-remote-host-cli": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/aw-app-remote-host-cli",
      "env": {
        "AW_WORKSPACE": "your-workspace-slug",
        "AW_WORKSPACE_HOST_TOKEN": "..."
      }
    }
  }
}
```

## Tools

- `remote_host_status` — hostname / connected / last_seen_at.
- `remote_host_exec_start` — start a shell command, returns a `job_id`.
- `remote_host_exec_status` — check a job's status.
- `remote_host_exec_wait` — block until a job finishes.
- `remote_host_exec_kill` — kill a running job.
- `remote_host_list_processes` — list processes started via exec.
