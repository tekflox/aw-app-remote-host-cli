"""``aw-workspace-cli remote-hosts shell [<host>]`` — a real interactive
bash/PTY on a linked remote host, not a one-shot ``exec``.

Why a separate module from ``cli.py``: everything else in this app is
request/response over ``httpx`` and returns a dict ``dispatch()`` can hand to
either the CLI or the MCP server. A shell is neither — it is a long-lived
bidirectional stream that only means anything when a human is sitting at a
tty, so it can never be an MCP tool and never returns a result dict. Keeping
it out of ``dispatch()`` keeps that contract honest.

Transport: ``WS /api/workspaces/{slug}/remote-hosts/{host_id}/shell`` on
aw-backend (``src/api/routes/host_link.py``), which bridges to the pty_*
frames over that host's ``/link`` tunnel. Authenticated with the same
``AW_WORKSPACE_HOST_TOKEN`` every other verb here uses, sent as a Bearer
header — NOT as ``?token=``, which the backend also accepts but which would
write the credential into any proxy/access log on the path.

Where the shell lands is a choice, not a given. ``--target host`` (the
default) opens it on the box running aw-remote-host — the same machine
``exec``/``exec-wait`` run on, which is what "shell into my remote host"
means and why it is the default. ``--target workspace`` opens it inside that
host's podman-managed workspace container instead, which is where the
console's browser terminal has always landed.

``websockets`` is imported lazily, inside ``run_shell``: this module is
imported by ``cli.py`` at startup for every subcommand, and a missing
optional dep must not break ``status``/``exec``/``ps`` (core does not install
an app's ``runtime.pip_requires``, so it genuinely can be absent).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys

from .client import RemoteHostClient
from .hosts import resolve_host_ref

# Ctrl-] — telnet's escape, and the reason this is not Ctrl-C or Ctrl-D: in
# raw mode every keystroke belongs to the remote shell, so the local client
# needs one key the remote will never plausibly want. Ctrl-] is unbound in
# bash's default readline map.
ESCAPE_KEY = b"\x1d"

DEFAULT_COLS = 80
DEFAULT_ROWS = 24

# Which machine the PTY lands on. TARGET_HOST is the box running
# aw-remote-host — for a containerised deployment that container, for a bare
# metal one the metal. TARGET_WORKSPACE is that host's podman-managed
# workspace container. Host is this CLI's default because it matches where
# `exec`/`exec-wait` already run: two verbs in the same command that quietly
# reach different machines is the confusion worth designing out.
TARGET_HOST = "host"
TARGET_WORKSPACE = "workspace"
TARGETS = (TARGET_HOST, TARGET_WORKSPACE)


class ShellUnavailable(RuntimeError):
    """The shell could not be opened or the session ended abnormally — the
    message is the host's own reason where there is one."""


def terminal_size() -> tuple[int, int]:
    """Local terminal size, falling back to 80x24 when stdout is not a tty
    (which ``run_shell`` refuses anyway, but ``shell_url`` is also called by
    tests)."""
    try:
        size = shutil.get_terminal_size(fallback=(DEFAULT_COLS, DEFAULT_ROWS))
        return size.columns or DEFAULT_COLS, size.lines or DEFAULT_ROWS
    except (OSError, ValueError):
        return DEFAULT_COLS, DEFAULT_ROWS


def shell_url(client: RemoteHostClient, host_id: str, cols: int, rows: int,
              target: str = TARGET_HOST) -> str:
    """``http(s)://`` backend URL -> the ``ws(s)://`` shell endpoint for
    ``host_id``. Scheme swap is a prefix replace rather than urlparse because
    the only two shapes ``AW_BACKEND_URL`` ever takes are http and https."""
    base = client.backend_url
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return (f"{base}/api/workspaces/{client.workspace}/remote-hosts/{host_id}"
            f"/shell?cols={cols}&rows={rows}&target={target}")


def resolve_host_id(client: RemoteHostClient, host_ref: str | None) -> str:
    """``shell`` with no argument means this workspace's own linked host —
    but the WS route is host-scoped only (there is no ``/remote-host/shell``
    singular sibling), so the id has to be resolved before connecting.
    ``status()`` is the cheapest way to learn it and doubles as the
    is-it-even-online check.

    With an argument, ``host_ref`` is an id, a workspace slug or a hostname —
    see ``hosts.resolve_host_ref``."""
    if host_ref:
        return resolve_host_ref(client, host_ref)
    status = client.status()
    resolved = status.get("id")
    if not resolved:
        raise ShellUnavailable(
            "no remote host is linked to this workspace — run "
            "'aw-workspace-cli remote-hosts hosts' to see the account's hosts "
            "and pass one explicitly."
        )
    if not status.get("connected"):
        raise ShellUnavailable(
            f"host {status.get('hostname') or resolved} is linked but not "
            "currently connected — its aw-remote-host process isn't dialed in."
        )
    return resolved


async def _connect(url: str, token: str):
    """``websockets`` renamed ``extra_headers`` to ``additional_headers`` in
    v14. Try the modern name, fall back to the old one, so this works against
    whatever version happens to be in the workspace image."""
    import websockets

    try:
        return await websockets.connect(url, additional_headers={"Authorization": f"Bearer {token}"})
    except TypeError:
        return await websockets.connect(url, extra_headers={"Authorization": f"Bearer {token}"})


async def _session(url: str, token: str, stdin_fd: int) -> None:
    """The pump. Assumes the caller has already put ``stdin_fd`` in raw mode
    and will restore it — doing that here would leave the terminal wedged on
    any exception path this function does not catch."""
    import websockets

    try:
        ws = await _connect(url, token)
    except Exception as e:  # noqa: BLE001 — websockets raises a wide family here
        raise ShellUnavailable(_connect_error(e)) from e

    loop = asyncio.get_running_loop()
    stop = loop.create_future()

    def on_stdin() -> None:
        """Reads whatever is buffered (not one byte at a time) so a paste or a
        fast typist costs one frame, not one frame per character."""
        try:
            data = os.read(stdin_fd, 4096)
        except OSError:
            data = b""
        if not data:
            if not stop.done():
                stop.set_result(None)
            return
        if ESCAPE_KEY in data:
            # Send whatever preceded the escape so a half-typed line isn't
            # silently swallowed, then end the session.
            head = data.split(ESCAPE_KEY, 1)[0]
            if head:
                asyncio.ensure_future(_send_input(ws, head))
            if not stop.done():
                stop.set_result(None)
            return
        asyncio.ensure_future(_send_input(ws, data))

    def on_winch() -> None:
        c, r = terminal_size()
        asyncio.ensure_future(_send(ws, {"op": "resize", "cols": c, "rows": r}))

    async def from_host() -> None:
        out = sys.stdout.buffer
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue
            op = msg.get("op")
            if op == "output":
                out.write((msg.get("data") or "").encode("utf-8", errors="replace"))
                out.flush()
            elif op == "status":
                state = msg.get("state")
                if state in ("offline", "disconnected"):
                    raise ShellUnavailable(msg.get("message") or f"session {state}")

    loop.add_reader(stdin_fd, on_stdin)
    has_winch = hasattr(signal, "SIGWINCH")
    if has_winch:
        loop.add_signal_handler(signal.SIGWINCH, on_winch)

    reader = asyncio.ensure_future(from_host())
    try:
        done, _ = await asyncio.wait([reader, stop], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            # `stop` is a bare future with no exception; only `reader` can
            # carry one, and it must surface (a closed session with a reason
            # is the difference between "you typed exit" and "the host died").
            if task is reader and task.exception():
                exc = task.exception()
                if isinstance(exc, ShellUnavailable):
                    raise exc
                if not isinstance(exc, websockets.exceptions.ConnectionClosed):
                    raise ShellUnavailable(str(exc)) from exc
    finally:
        reader.cancel()
        loop.remove_reader(stdin_fd)
        if has_winch:
            loop.remove_signal_handler(signal.SIGWINCH)
        await ws.close()


async def _send(ws, frame: dict) -> None:
    """Every send is best-effort: the peer closing mid-write is the normal way
    a shell ends (`exit`), not an error worth unwinding the session for."""
    import websockets

    try:
        await ws.send(json.dumps(frame))
    except (websockets.exceptions.ConnectionClosed, RuntimeError):
        pass


async def _send_input(ws, data: bytes) -> None:
    # The browser<->backend hop is UTF-8 strings, not the tunnel's base64
    # (see workspace_shell.py's module docstring); errors="replace" because a
    # keystroke that isn't valid UTF-8 must not kill the session.
    await _send(ws, {"op": "input", "data": data.decode("utf-8", errors="replace")})


def _connect_error(exc: Exception) -> str:
    """Turn the backend's WS close codes into the reason they actually mean —
    a bare 'server rejected WebSocket connection: HTTP 403' tells the user
    nothing about which of the four gates they hit."""
    text = str(exc)
    for code, meaning in (
        ("401", "not authenticated — AW_WORKSPACE_HOST_TOKEN is missing or invalid"),
        ("403", "not authorized — this credential is not an owner of the workspace"),
        ("404", "no such host in this account — check 'remote-hosts hosts'"),
    ):
        if code in text:
            return f"{meaning} (HTTP {code})"
    return text


def run_shell(host_id: str | None = None, target: str = TARGET_HOST, *,
              client: RemoteHostClient | None = None) -> int:
    """Open an interactive shell and block until it ends. Returns the process
    exit code for ``main()``.

    The remote shell's own exit status is NOT recoverable: the pty protocol
    reports a closed session with a reason, never an exit code (a pty has no
    'the process exited 3' frame). So a session that ends normally exits 0 and
    one that could not be established exits non-zero — same as what a terminal
    emulator can report, and the reason ``exec-wait`` still exists for
    anything scriptable.
    """
    import termios
    import tty

    client = client or RemoteHostClient()
    # Checked up front rather than left to the first request: with an explicit
    # host_id nothing else here touches the HTTP client, so an unconfigured
    # workspace would otherwise surface as a confusing WS auth failure.
    client._require_configured()

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("remote-hosts shell: needs an interactive terminal on stdin/stdout. "
              "Use 'remote-hosts exec-wait \"<command>\"' for scripted/non-tty use.",
              file=sys.stderr)
        return 2

    if target not in TARGETS:
        raise ShellUnavailable(f"unknown target {target!r} (expected one of {', '.join(TARGETS)})")

    host_id = resolve_host_id(client, host_id)
    cols, rows = terminal_size()
    url = shell_url(client, host_id, cols, rows, target)

    # Name the target, not just the host id: `host` and `workspace` are two
    # different machines with often-identical prompts, and landing on the
    # wrong one is not something you can tell by looking.
    where = "the host itself" if target == TARGET_HOST else "the workspace container"
    print(f"Connected to {host_id} ({where}) — press Ctrl-] to disconnect.", file=sys.stderr)

    stdin_fd = sys.stdin.fileno()
    saved = termios.tcgetattr(stdin_fd)
    try:
        tty.setraw(stdin_fd)
        asyncio.run(_session(url, client.token, stdin_fd))
    finally:
        # Restore BEFORE anything else can print: leaving the tty raw makes
        # the user's shell look broken (no echo, no newline handling) long
        # after this process is gone.
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved)
        sys.stdout.write("\r\n")
        sys.stdout.flush()
    return 0
