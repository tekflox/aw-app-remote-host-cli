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
— the exact same function the ``aw-workspace-cli remote-hosts`` command's own
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
        "description": (
            "Start a shell command on the linked remote host, or on ANY host "
            "id from remote_host_list_hosts belonging to this same account. "
            "Returns a job_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run."},
                "timeout_s": {"type": "number", "description": "Optional host-side timeout in seconds."},
                "host_id": {
                    "type": "string",
                    "description": ("Optional — a specific host to target instead of this "
                                     "workspace's own linked host: its id, workspace slug, or "
                                     "hostname, all from remote_host_list_hosts. Must belong to "
                                     "this same account."),
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "remote_host_exec_run",
        "description": (
            "Run a shell command on the remote host and block until it finishes, "
            "returning stdout/stderr/exit_code in one call. Prefer this over "
            "remote_host_exec_start + remote_host_exec_wait whenever you just "
            "want a command's output. If it times out the result carries the "
            "job_id, so remote_host_exec_wait can resume it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run."},
                "timeout_s": {"type": "number", "description": "Max seconds to wait."},
                "host_id": {
                    "type": "string",
                    "description": ("Optional — a specific host to target instead of this "
                                     "workspace's own linked host: its id, workspace slug, or "
                                     "hostname, all from remote_host_list_hosts."),
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "remote_host_exec_status",
        "description": "Check the status of a job started with remote_host_exec_start.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "host_id": {"type": "string", "description": "Match the host exec_start used, if any (id, workspace slug or hostname)."},
            },
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
                "host_id": {"type": "string", "description": "Match the host exec_start used, if any (id, workspace slug or hostname)."},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "remote_host_exec_kill",
        "description": "Kill a running job on the linked remote host.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "host_id": {"type": "string", "description": "Match the host exec_start used, if any (id, workspace slug or hostname)."},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "remote_host_list_processes",
        "description": "List processes started via exec on the linked remote host, or on a specific host (id, workspace slug or hostname).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host_id": {"type": "string", "description": "Optional — target a specific host (id, workspace slug or hostname) instead of this workspace's own."},
            },
        },
    },
    {
        "name": "remote_host_list_hosts",
        "description": (
            "List every remote host linked across this account's workspaces "
            "(not just the current one) — id, workspace_slug, hostname, os, "
            "arch, last_seen_at, and online/offline (connected) for each."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    # ---- file transfer -------------------------------------------------
    # Prefer these over remote_host_exec_run with cat/base64: they stream
    # (so file size doesn't blow up a command's output cap) and they verify
    # sha256 end to end, which a shell pipeline silently doesn't.
    {
        "name": "remote_host_read_file",
        "description": (
            "Read a file from a remote host and return its CONTENT inline. Use "
            "this to inspect a config/log/source file. Text is returned as-is "
            "(encoding: utf-8); binary comes back base64-encoded. Bounded at "
            "8 MB — for anything larger use remote_host_download_file, which "
            "streams to a local path instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path on the remote host. '~' and paths relative to the host user's home are supported."},
                "host_id": {"type": "string", "description": "Optional — a specific host (id, workspace slug or hostname) from remote_host_list_hosts."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "remote_host_write_file",
        "description": (
            "Write CONTENT to a file on a remote host, creating missing parent "
            "directories. Overwrites an existing file completely. Verifies "
            "sha256 after the write."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination path on the remote host."},
                "content": {"type": "string", "description": "File content."},
                "encoding": {
                    "type": "string", "enum": ["utf-8", "base64"],
                    "description": "How 'content' is encoded (default: utf-8). Use base64 for binary.",
                },
                "mode": {"type": "string", "description": "Optional octal permissions, e.g. '755'."},
                "host_id": {"type": "string", "description": "Optional — a specific host (id, workspace slug or hostname)."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "remote_host_upload_file",
        "description": (
            "Upload a file from THIS workspace's filesystem to a remote host, "
            "streaming it (no size limit beyond patience) and verifying sha256 "
            "on arrival."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "local_path": {"type": "string", "description": "Path of the file on this workspace's filesystem."},
                "path": {"type": "string", "description": "Destination path on the remote host."},
                "mode": {"type": "string", "description": "Optional octal permissions, e.g. '755'."},
                "host_id": {"type": "string", "description": "Optional — a specific host (id, workspace slug or hostname)."},
            },
            "required": ["local_path", "path"],
        },
    },
    {
        "name": "remote_host_download_file",
        "description": (
            "Download a file from a remote host to THIS workspace's filesystem, "
            "streaming it and verifying sha256 before the file is moved into "
            "place. Use .tmp/ for scratch destinations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file on the remote host."},
                "local_path": {"type": "string", "description": "Destination on this workspace's filesystem (default: same basename in the current directory)."},
                "host_id": {"type": "string", "description": "Optional — a specific host (id, workspace slug or hostname)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "remote_host_list_directory",
        "description": "List a directory on a remote host — name, path, is_dir, size, mode and modified_at per entry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "host_id": {"type": "string", "description": "Optional — a specific host (id, workspace slug or hostname)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "remote_host_stat",
        "description": (
            "Check whether a path exists on a remote host and what it is. A "
            "missing path returns exists:false rather than an error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "digest": {"type": "boolean", "description": "Also compute the file's sha256 on the host."},
                "host_id": {"type": "string", "description": "Optional — a specific host (id, workspace slug or hostname)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "remote_host_mkdir",
        "description": "Create a directory (and any missing parents) on a remote host. Succeeds if it already exists.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "host_id": {"type": "string", "description": "Optional — a specific host (id, workspace slug or hostname)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "remote_host_delete",
        "description": "Delete a file or directory on a remote host. A non-empty directory needs recursive:true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "recursive": {"type": "boolean", "description": "Required to delete a non-empty directory."},
                "host_id": {"type": "string", "description": "Optional — a specific host (id, workspace slug or hostname)."},
            },
            "required": ["path"],
        },
    },
    # ---- firewall --------------------------------------------------------
    # A rule always persists even when the push to the host fails (offline,
    # unprivileged, unknown verb) — firewall_capable/in_sync/last_error on
    # every response say whether it's actually enforced, not just saved.
    {
        "name": "remote_host_firewall_list",
        "description": (
            "List inbound firewall rules on a remote host plus its sync state: "
            "backend (nft/iptables/unsupported), whether it's even capable of "
            "applying rules (firewall_capable/firewall_capability_reason), "
            "lockdown, and whether the saved rules match what the host is "
            "actually enforcing (in_sync, revision vs applied_revision)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "host_id": {"type": "string", "description": "Optional — a specific host (id, workspace slug or hostname)."},
            },
        },
    },
    {
        "name": "remote_host_firewall_add_rule",
        "description": (
            "Add an inbound firewall rule on a remote host. If this host sits "
            "behind DNAT/port-forwarding, port_from/port_to must be the "
            "POST-DNAT port — filtering the pre-DNAT port never matches and "
            "traffic silently disappears with no RST. The rule is saved even "
            "if the host is offline or can't apply it yet (see the response's "
            "applied/pending_reason)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "port_from": {"type": "integer", "description": "1-65535. Same as port_to for a single port."},
                "port_to": {"type": "integer", "description": "1-65535, >= port_from."},
                "protocol": {"type": "string", "enum": ["tcp", "udp"], "description": "Default: tcp."},
                "source_cidr": {"type": "string", "description": "Default: 0.0.0.0/0."},
                "action": {"type": "string", "enum": ["allow", "deny"], "description": "Default: allow."},
                "priority": {"type": "integer", "description": "Lower matches first. Default: 100."},
                "comment": {"type": "string"},
                "host_id": {"type": "string", "description": "Optional — a specific host (id, workspace slug or hostname)."},
            },
            "required": ["port_from", "port_to"],
        },
    },
    {
        "name": "remote_host_firewall_remove_rule",
        "description": "Remove a firewall rule from a remote host by its id (from remote_host_firewall_list).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string"},
                "host_id": {"type": "string", "description": "Optional — a specific host (id, workspace slug or hostname)."},
            },
            "required": ["rule_id"],
        },
    },
    {
        "name": "remote_host_firewall_set_lockdown",
        "description": "Toggle lockdown on a remote host — when on, all inbound traffic is denied except explicit allow rules.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lockdown": {"type": "boolean"},
                "host_id": {"type": "string", "description": "Optional — a specific host (id, workspace slug or hostname)."},
            },
            "required": ["lockdown"],
        },
    },
    {
        "name": "remote_host_firewall_status",
        "description": (
            "Force a re-push of the current saved firewall rules/lockdown state "
            "to a remote host and report whether it applied — use this to nudge "
            "a host that just came back online or just got the privilege it was "
            "missing, instead of waiting for its next reconnect."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "host_id": {"type": "string", "description": "Optional — a specific host (id, workspace slug or hostname)."},
            },
        },
    },
]


_TOOL_TO_CMD = {
    "remote_host_status": "status",
    "remote_host_exec_start": "exec",
    "remote_host_exec_run": "exec-wait",
    "remote_host_exec_status": "exec-status",
    "remote_host_exec_wait": "wait",
    "remote_host_exec_kill": "kill",
    "remote_host_list_processes": "ps",
    "remote_host_list_hosts": "hosts",
    "remote_host_read_file": "read",
    "remote_host_write_file": "write",
    "remote_host_upload_file": "push",
    "remote_host_download_file": "pull",
    "remote_host_list_directory": "ls",
    "remote_host_stat": "stat",
    "remote_host_mkdir": "mkdir",
    "remote_host_delete": "rm",
    "remote_host_firewall_list": "firewall-list",
    "remote_host_firewall_add_rule": "firewall-add",
    "remote_host_firewall_remove_rule": "firewall-remove",
    "remote_host_firewall_set_lockdown": "firewall-lockdown",
    "remote_host_firewall_status": "firewall-status",
}


def _call(name: str, args: dict) -> dict:
    try:
        data = dispatch(
            _TOOL_TO_CMD[name],
            command=args.get("command"),
            job_id=args.get("job_id"),
            timeout_s=args.get("timeout_s"),
            host_id=args.get("host_id"),
            path=args.get("path"),
            local_path=args.get("local_path"),
            recursive=bool(args.get("recursive")),
            mode=args.get("mode"),
            digest=bool(args.get("digest")),
            content=args.get("content"),
            encoding=args.get("encoding"),
            port_from=args.get("port_from"),
            port_to=args.get("port_to"),
            protocol=args.get("protocol"),
            source_cidr=args.get("source_cidr"),
            action=args.get("action"),
            priority=args.get("priority"),
            comment=args.get("comment"),
            rule_id=args.get("rule_id"),
            lockdown=args.get("lockdown"),
        )
        return {"ok": True, "data": data}
    except OSError as e:
        # Local-filesystem failure (no such local_path to upload, unwritable
        # download target) — reported as its own thing so the agent doesn't go
        # looking for the problem on the remote machine.
        return {"ok": False, "error": f"local filesystem: {e}"}
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
