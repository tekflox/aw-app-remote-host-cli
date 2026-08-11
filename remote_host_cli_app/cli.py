"""``aw-remote-hosts`` — CLI over ``client.RemoteHostClient``. Installed by
``scripts/install_remote_hosts_cli.sh`` as a thin bash shim that runs this
module with the app's own package dir on ``PYTHONPATH`` (Tier-1 apps load
under a synthetic ``aw_apps.<id>`` namespace inside the workspace process —
see aw-workspace's ``src/apps/runtime.py:_import_plugin`` — so this file is
NOT importable as a plain ``remote_host_cli_app`` package from an installed
CLI's own separate process without that explicit PYTHONPATH).

``dispatch()`` is the single source of truth for "command name -> client
call" — ``main()`` (this CLI's own argparse entrypoint) and
``mcp_server/server.py`` (the MCP tool handlers) both call it, so there is
exactly one implementation of what each operation does; the MCP server
imports and runs THROUGH this CLI's own dispatch rather than re-deriving it
against ``RemoteHostClient`` a second time.
"""

from __future__ import annotations

import argparse
import json
import sys

from .client import NotConfigured, RemoteHostClient, RemoteHostError

COMMANDS = ("status", "exec", "exec-status", "wait", "kill", "ps", "hosts")


def dispatch(cmd: str, *, client: RemoteHostClient | None = None, command: str | None = None,
             job_id: str | None = None, timeout_s: float | None = None) -> dict:
    """Run one ``aw-remote-hosts`` operation and return its raw result dict.

    Raises ``NotConfigured``/``RemoteHostError`` straight through — callers
    (``main()`` below, and the MCP server) decide how to surface those.
    """
    client = client or RemoteHostClient()
    if cmd == "status":
        return client.status()
    if cmd == "exec":
        if not command:
            raise ValueError("exec requires 'command'")
        return client.exec_start(command, timeout_s=timeout_s)
    if cmd == "exec-status":
        if not job_id:
            raise ValueError("exec-status requires 'job_id'")
        return client.exec_status(job_id)
    if cmd == "wait":
        if not job_id:
            raise ValueError("wait requires 'job_id'")
        return client.exec_wait(job_id, timeout_s=timeout_s)
    if cmd == "kill":
        if not job_id:
            raise ValueError("kill requires 'job_id'")
        return client.exec_kill(job_id)
    if cmd == "ps":
        return client.list_processes()
    if cmd == "hosts":
        return client.list_account_hosts()
    raise ValueError(f"unknown command: {cmd!r} (expected one of {COMMANDS})")


def _print(data: dict) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aw-remote-hosts",
        description="Interact with the remote host linked to this aw-workspace account.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show the linked host's hostname/connected state.")

    p_exec = sub.add_parser("exec", help="Start a command on the linked host.")
    p_exec.add_argument("command", help="Shell command to run on the remote host.")
    p_exec.add_argument("--timeout", type=float, default=None, dest="timeout_s",
                         help="Optional host-side timeout in seconds.")

    p_status = sub.add_parser("exec-status", help="Check a job's status.")
    p_status.add_argument("job_id")

    p_wait = sub.add_parser("wait", help="Block until a job finishes.")
    p_wait.add_argument("job_id")
    p_wait.add_argument("--timeout", type=float, default=None, dest="timeout_s",
                         help="Max seconds to wait (default: host-side default).")

    p_kill = sub.add_parser("kill", help="Kill a running job.")
    p_kill.add_argument("job_id")

    sub.add_parser("ps", help="List processes started via exec on the linked host.")

    sub.add_parser("hosts", help="List every remote host linked across this account's workspaces.")

    args = parser.parse_args(argv)

    try:
        result = dispatch(
            args.cmd,
            command=getattr(args, "command", None),
            job_id=getattr(args, "job_id", None),
            timeout_s=getattr(args, "timeout_s", None),
        )
        _print(result)
    except NotConfigured as e:
        print(f"aw-remote-hosts: {e}", file=sys.stderr)
        return 2
    except RemoteHostError as e:
        print(f"aw-remote-hosts: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
