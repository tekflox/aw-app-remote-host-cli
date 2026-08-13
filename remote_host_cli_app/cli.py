"""CLI over ``client.RemoteHostClient``, surfaced as ``aw-workspace-cli
remote-hosts`` (see this repo's ``commands/remote_hosts.py``, auto-discovered
by aw-workspace-cli from the installed app dir).

Until v0.8.0 this was instead a standalone ``aw-remote-hosts`` binary, a bash
shim dropped into ``<AW_WORKSPACE_HOME>/bin`` by ``contributes.system_clis``.
That shim is gone; the app-contributed-command path replaces it. Either way
the same import problem has to be solved by the caller: Tier-1 apps load
under a synthetic ``aw_apps.<id>`` namespace inside the workspace process
(see aw-workspace's ``src/apps/runtime.py:_import_plugin``), so this file is
NOT importable as a plain ``remote_host_cli_app`` package from a separate
process without the app's package dir on ``sys.path``/``PYTHONPATH``.

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
from .hosts import AmbiguousHost, HostNotFound, resolve_host_ref

COMMANDS = ("status", "exec", "exec-wait", "exec-status", "wait", "kill", "ps", "hosts")

# `shell` is deliberately NOT in COMMANDS/dispatch(): dispatch's contract is
# "one operation -> one result dict", which the MCP server relies on. An
# interactive PTY streams for minutes and returns nothing, so it lives in
# shell.py and is handled directly in main() — see that module's docstring.

# Conventional "the thing you waited for never finished" exit code (timeout(1),
# and what a shell reports for a SIGTERM'd child). Distinct from any real remote
# exit code we'd otherwise forward, and from this CLI's own 1/2.
EXIT_TIMEOUT = 124


def dispatch(cmd: str, *, client: RemoteHostClient | None = None, command: str | None = None,
             job_id: str | None = None, timeout_s: float | None = None,
             host_id: str | None = None) -> dict:
    """Run one remote-host operation and return its raw result dict.

    ``host_id`` (optional) targets a SPECIFIC host anywhere in the caller's
    account instead of this workspace's own linked host. Despite the name it
    accepts any reference ``hosts`` reports — id, workspace slug, or hostname
    — since nobody remembers a 16-hex id. aw-backend enforces same-account
    ownership server-side, so this is just which URL gets called, not a
    trust boundary this client itself needs to police.

    Raises ``NotConfigured``/``RemoteHostError``/``HostNotFound``/
    ``AmbiguousHost`` straight through — callers (``main()`` below, and the
    MCP server) decide how to surface those.
    """
    client = client or RemoteHostClient()
    # --host takes an id, a workspace slug or a hostname, same as `shell`'s
    # positional. Resolved once here rather than per-verb so the two can't
    # drift apart; an id-shaped ref costs no extra request (see hosts.py).
    if host_id:
        host_id = resolve_host_ref(client, host_id)
    if cmd == "status":
        return client.status()
    if cmd == "exec":
        if not command:
            raise ValueError("exec requires 'command'")
        return client.exec_start(command, timeout_s=timeout_s, host_id=host_id)
    if cmd == "exec-wait":
        if not command:
            raise ValueError("exec-wait requires 'command'")
        started = client.exec_start(command, timeout_s=timeout_s, host_id=host_id)
        job_id = started.get("job_id")
        if not job_id:
            # Nothing to wait on — hand back whatever exec_start said rather
            # than inventing a result. Shouldn't happen; a host that returns
            # no job_id has already failed in a way only it can explain.
            return started
        result = client.exec_wait(job_id, timeout_s=timeout_s, host_id=host_id)
        # exec_wait's own payload already carries job_id in practice, but a
        # timed-out wait is only recoverable if the caller definitely has it —
        # that's the whole point of this command not losing the two-step
        # escape hatch. Set it unconditionally, from the id we started.
        result.setdefault("job_id", job_id)
        return result
    if cmd == "exec-status":
        if not job_id:
            raise ValueError("exec-status requires 'job_id'")
        return client.exec_status(job_id, host_id=host_id)
    if cmd == "wait":
        if not job_id:
            raise ValueError("wait requires 'job_id'")
        return client.exec_wait(job_id, timeout_s=timeout_s, host_id=host_id)
    if cmd == "kill":
        if not job_id:
            raise ValueError("kill requires 'job_id'")
        return client.exec_kill(job_id, host_id=host_id)
    if cmd == "ps":
        return client.list_processes(host_id=host_id)
    if cmd == "hosts":
        return client.list_account_hosts()
    raise ValueError(f"unknown command: {cmd!r} (expected one of {COMMANDS})")


def _print(data: dict) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _render_run(result: dict, prog: str) -> int:
    """Render an ``exec-wait`` result the way a local command would behave:
    stdout to stdout, stderr to stderr, exit with the REMOTE exit code.

    That forwarding is the whole point of this subcommand — `... exec-wait
    "test -f /x"` has to be usable in an `if`, which a JSON envelope on stdout
    and a hardcoded 0 can never be. It does mean a remote exit code of 1 or 2
    is indistinguishable from this CLI's own error codes; that's the same
    trade-off ssh makes, and the alternative (never forwarding) is worse.
    """
    sys.stdout.write(result.get("stdout") or "")
    sys.stderr.write(result.get("stderr") or "")
    sys.stdout.flush()

    if result.get("status") != "exited":
        # Timed out or still running: there is no exit code to forward, and
        # silently returning 0 would report success for a command that may
        # still be running. Hand back the job_id so the caller can resume with
        # `wait`/`exec-status` rather than losing the job entirely.
        job_id = result.get("job_id") or "?"
        print(f"{prog}: command did not finish (status: "
              f"{result.get('status') or 'unknown'}); resume with: "
              f"{prog} wait {job_id}", file=sys.stderr)
        return EXIT_TIMEOUT

    code = result.get("exit_code")
    return code if isinstance(code, int) else 0


def main(argv: list[str] | None = None, prog: str = "aw-workspace-cli remote-hosts") -> int:
    """``prog`` is what usage/error lines call this command. It is a parameter
    rather than a constant because the same parser is reached under more than
    one name — ``aw-workspace-cli remote-hosts`` (the contributed command, the
    normal path) and plain ``python -m remote_host_cli_app.cli`` (direct
    invocation, e.g. in tests) — and a usage string that doesn't match what
    the user actually typed is worse than useless."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Interact with the remote hosts linked to this aw-workspace account.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    host_help = ("Target a specific host instead of this workspace's own linked one — "
                 "its id, its workspace slug, or its hostname (all from 'hosts').")

    sub.add_parser("status", help="Show the linked host's hostname/connected state.")

    p_exec = sub.add_parser("exec", help="Start a command on the linked host (returns a job_id, does NOT block).")
    p_exec.add_argument("command", help="Shell command to run on the remote host.")
    p_exec.add_argument("--timeout", type=float, default=None, dest="timeout_s",
                         help="Optional host-side timeout in seconds.")
    p_exec.add_argument("--host", default=None, dest="host_id", help=host_help)

    p_run = sub.add_parser(
        "exec-wait", aliases=["run"],
        help="Run a command and block for its output — exec + wait in one call.",
        description="Start a command on the remote host and block until it finishes, "
                    "then print its stdout/stderr and exit with ITS exit code — so a "
                    "remote command behaves like a local one. Use 'exec' + 'wait' "
                    "instead when you want the job_id back immediately.",
    )
    p_run.add_argument("command", help="Shell command to run on the remote host.")
    p_run.add_argument("--timeout", type=float, default=None, dest="timeout_s",
                        help="Max seconds to wait (default: host-side default).")
    p_run.add_argument("--host", default=None, dest="host_id", help=host_help)
    p_run.add_argument("--json", action="store_true", dest="as_json",
                        help="Print the full result envelope as JSON instead of raw output.")

    p_status = sub.add_parser("exec-status", help="Check a job's status.")
    p_status.add_argument("job_id")
    p_status.add_argument("--host", default=None, dest="host_id", help=host_help)

    p_wait = sub.add_parser("wait", help="Block until a job finishes.")
    p_wait.add_argument("job_id")
    p_wait.add_argument("--timeout", type=float, default=None, dest="timeout_s",
                         help="Max seconds to wait (default: host-side default).")
    p_wait.add_argument("--host", default=None, dest="host_id", help=host_help)

    p_kill = sub.add_parser("kill", help="Kill a running job.")
    p_kill.add_argument("job_id")
    p_kill.add_argument("--host", default=None, dest="host_id", help=host_help)

    p_ps = sub.add_parser("ps", help="List processes started via exec on the linked host.")
    p_ps.add_argument("--host", default=None, dest="host_id", help=host_help)

    sub.add_parser("hosts", help="List every remote host linked across this account's workspaces.")

    p_shell = sub.add_parser(
        "shell",
        help="Open an interactive shell (PTY) on a linked host.",
        description="Attach a real interactive bash/sh to a linked remote host — "
                    "arrow keys, job control, vim, the lot. Needs a terminal; use "
                    "'exec-wait' for anything scripted. Press Ctrl-] to disconnect.",
    )
    p_shell.add_argument("host_id", nargs="?", default=None, metavar="HOST",
                          help="Host id, workspace slug or hostname from 'hosts' "
                               "(default: this workspace's own linked host).")
    p_shell.add_argument("--target", default="host", choices=("host", "workspace"),
                          help="Which machine: 'host' (default) is the box running "
                               "aw-remote-host — the same place exec/exec-wait run; "
                               "'workspace' is that host's workspace container.")

    args = parser.parse_args(argv)

    if args.cmd == "shell":
        from .shell import ShellUnavailable, run_shell

        try:
            return run_shell(args.host_id, args.target)
        except (NotConfigured, ShellUnavailable, HostNotFound, AmbiguousHost) as e:
            print(f"{prog}: {e}", file=sys.stderr)
            return 2
        except RemoteHostError as e:
            print(f"{prog}: {e}", file=sys.stderr)
            return 1
        except ImportError as e:
            print(f"{prog}: interactive shell needs the 'websockets' package "
                  f"({e}). Every other subcommand works without it.", file=sys.stderr)
            return 2

    # argparse reports the alias the user typed, but dispatch() only knows the
    # canonical names in COMMANDS.
    cmd = "exec-wait" if args.cmd == "run" else args.cmd

    try:
        result = dispatch(
            cmd,
            command=getattr(args, "command", None),
            job_id=getattr(args, "job_id", None),
            timeout_s=getattr(args, "timeout_s", None),
            host_id=getattr(args, "host_id", None),
        )
        if cmd == "exec-wait" and not getattr(args, "as_json", False):
            return _render_run(result, prog)
        _print(result)
    except (NotConfigured, HostNotFound, AmbiguousHost) as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 2
    except RemoteHostError as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
