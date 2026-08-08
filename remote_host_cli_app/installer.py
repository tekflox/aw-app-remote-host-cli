"""Install/uninstall logic for the `aw-remote-hosts` CLI, as a plain
subprocess-calling module (no framework `ctx` needed) — used by
tests/test_installer.py (subprocess mocked). RemoteHostCliAppPlugin.activate()
goes through ctx.commands.install_system_cli() instead (the gated/journaled
framework path); this module exists purely so the install logic is testable
in plain CI without spinning up the runtime.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = APP_ROOT / "scripts"


class InstallError(RuntimeError):
    pass


def _run_script(script: str) -> str:
    path = SCRIPTS_DIR / script
    result = subprocess.run(
        ["bash", str(path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=APP_ROOT,
    )
    if result.returncode != 0:
        raise InstallError(
            f"{script} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def install_remote_hosts_cli() -> str:
    return _run_script("install_remote_hosts_cli.sh")


def uninstall_remote_hosts_cli() -> None:
    _run_script("uninstall.sh")
