"""``aw-remote-hosts`` — CLI over ``client.RemoteHostClient``. Installed by
``scripts/install_remote_hosts_cli.sh`` as a thin bash shim that runs this
module with the app's own package dir on ``PYTHONPATH`` (Tier-1 apps load
under a synthetic ``aw_apps.<id>`` namespace inside the workspace process —
see aw-workspace's ``src/apps/runtime.py:_import_plugin`` — so this file is
NOT importable as a plain ``remote_host_cli_app`` package from an installed
CLI's own separate process without that explicit PYTHONPATH).

Every subcommand just calls the matching ``RemoteHostClient`` method and
prints its JSON result — this is a thin, scriptable wrapper, not a rich TUI.
"""

from __future__ import annotations

import argparse
import json
import sys

from .client import NotConfigured, RemoteHostClient, RemoteHostError


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

    args = parser.parse_args(argv)
    client = RemoteHostClient()

    try:
        if args.cmd == "status":
            _print(client.status())
        elif args.cmd == "exec":
            _print(client.exec_start(args.command, timeout_s=args.timeout_s))
        elif args.cmd == "exec-status":
            _print(client.exec_status(args.job_id))
        elif args.cmd == "wait":
            _print(client.exec_wait(args.job_id, timeout_s=args.timeout_s))
        elif args.cmd == "kill":
            _print(client.exec_kill(args.job_id))
        elif args.cmd == "ps":
            _print(client.list_processes())
    except NotConfigured as e:
        print(f"aw-remote-hosts: {e}", file=sys.stderr)
        return 2
    except RemoteHostError as e:
        print(f"aw-remote-hosts: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
