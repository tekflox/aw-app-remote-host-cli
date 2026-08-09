"""Stdio MCP server for aw-app-remote-host-cli.

Talks to **aw-backend** (the control plane, not the local aw-workspace API —
see ``docs/backend-auth.md``), authenticating as THIS workspace with its own
``AW_WORKSPACE_HOST_TOKEN`` (the ``awlk_`` credential the aw-remote-host
``/link`` handshake minted for this workspace). aw-backend verifies that
token belongs to exactly this workspace before honoring any remote-host
route (``require_workspace_actor``, see ``aw-backend/src/api/identity_guard.py``)
— so every tool below can only ever see/act on hosts linked to THIS
account, never another workspace's.

This is a STANDALONE process (unlike the in-process Tier-1 plugin) — run it
from this repo's root so ``remote_host_cli_app`` (a sibling top-level
package here) is importable:

    AW_BACKEND_URL=http://127.0.0.1:9025 \\
    AW_WORKSPACE=<your-workspace-slug> \\
    AW_WORKSPACE_HOST_TOKEN=<your awlk_ token> \\
    python -m mcp_server.server

Inside a real aw-workspace container all three env vars are already present
(same ones ``src/apps/registry_client.py`` reads) — an agent CLI's
``.mcp.json`` just needs to spawn this with that environment inherited, no
extra config.

Every tool handler below calls THROUGH ``remote_host_cli_app.cli.dispatch()``
— the exact same function the installed ``aw-remote-hosts`` CLI's own
``main()`` calls — rather than talking to ``RemoteHostClient`` a second,
separate way. One implementation of "what does each operation do"; this
file is a thin JSON-RPC/MCP protocol adapter over the CLI, nothing more.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remote_host_cli_app.cli import dispatch  # noqa: E402
from remote_host_cli_app.client import NotConfigured, RemoteHostError  # noqa: E402

_TOOLS = [
    {
        "name": "remote_host_status",
        "description": (
            "Get the hostname / connected state / last-seen time of the remote "
            "host linked to this aw-workspace account."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "remote_host_exec_start",
        "description": "Start a shell command on the linked remote host. Returns a job_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run."},
                "timeout_s": {"type": "number", "description": "Optional host-side timeout in seconds."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "remote_host_exec_status",
        "description": "Check the status of a job started with remote_host_exec_start.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "remote_host_exec_wait",
        "description": "Block until a job finishes (or timeout_s elapses) and return its result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "timeout_s": {"type": "number", "description": "Max seconds to wait."},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "remote_host_exec_kill",
        "description": "Kill a running job on the linked remote host.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "remote_host_list_processes",
        "description": "List processes started via exec on the linked remote host.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


_TOOL_TO_CMD = {
    "remote_host_status": "status",
    "remote_host_exec_start": "exec",
    "remote_host_exec_status": "exec-status",
    "remote_host_exec_wait": "wait",
    "remote_host_exec_kill": "kill",
    "remote_host_list_processes": "ps",
}


def _call(name: str, args: dict) -> dict:
    try:
        data = dispatch(
            _TOOL_TO_CMD[name],
            command=args.get("command"),
            job_id=args.get("job_id"),
            timeout_s=args.get("timeout_s"),
        )
        return {"ok": True, "data": data}
    except NotConfigured as e:
        return {"ok": False, "error": str(e)}
    except RemoteHostError as e:
        return {"ok": False, "error": str(e)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


def handle_request(request: dict) -> dict | None:
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aw-app-remote-host-cli", "version": "1.0.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": _TOOLS}}

    if method == "tools/call":
        name = request.get("params", {}).get("name", "")
        args = request.get("params", {}).get("arguments", {}) or {}

        if name not in _TOOL_TO_CMD:
            return _err(req_id, f"Unknown tool: {name}")

        r = _call(name, args)

        if r["ok"]:
            return _ok(req_id, json.dumps(r["data"], indent=2, ensure_ascii=False))
        return _err(req_id, f"Error: {r['error']}")

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}


def _ok(req_id, text):
    return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}], "isError": False}}


def _err(req_id, text):
    return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}], "isError": True}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
