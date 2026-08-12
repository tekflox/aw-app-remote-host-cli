"""
Entrypoint referenced by aw-app.json's runtime.entrypoint
("remote_host_cli_app.plugin:RemoteHostCliAppPlugin").

This app contributes no backend routes/frontend. Its CLI surface is
``commands/remote_hosts.py`` — an aw-workspace-cli *contributed command*
(``aw-workspace-cli remote-hosts``), auto-discovered from the installed app
dir with no plugin involvement at all. Beyond that it ships a skill and a
standalone MCP server (mcp_server/, not loaded by this plugin — it's a
separate process an agent CLI or the aw-mcp-gateway spawns on its own, see
mcp_server/README.md).

Until v0.7.0 the CLI was instead a standalone ``aw-remote-hosts`` binary,
installed by activate() through the gated ctx.commands facade (capability
commands:install) as a bash shim in <AW_WORKSPACE_HOME>/bin. That whole
path is gone — along with the capability, which this app no longer needs
for anything. _remove_legacy_shim() below cleans up the leftover file on
workspaces that ran an older version; see its docstring for why an update
can't rely on the framework's own revert.

activate(ctx) ALSO publishes AW_BACKEND_URL/AW_WORKSPACE/
AW_WORKSPACE_HOST_TOKEN into <AW_WORKSPACE_HOME>/.env (see
_publish_env_vars below) — this Tier-1 plugin runs IN the aw-workspace
process, the only place those three actually live in os.environ, so this
is the one point that can durably hand them to any OTHER process that
shares this workspace's filesystem but not its env (client.py's .env
fallback reads them back). Re-run on every activate (boot + reconcile),
so a later /link (or a token rotation) republishes automatically — no
manual copy-paste, matches src/api/workspace_api_key.py's own
os.environ-and-.env publish pattern for AW_WORKSPACE_API_KEY.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("aw_apps.remote-host-cli")

_ENV_VARS = ("AW_BACKEND_URL", "AW_WORKSPACE", "AW_WORKSPACE_HOST_TOKEN")

# The <AW_WORKSPACE_HOME>/bin shim this app installed until v0.7.0.
_LEGACY_SHIM = "aw-remote-hosts"


def _workspace_home() -> str:
    return os.environ.get("AW_WORKSPACE_HOME") or os.path.join(
        os.environ.get("AW_WORKSPACE_CONTAINER_DIR", "/opt/aw-workspace"), ".aw-workspace"
    )


def _remove_legacy_shim() -> str | None:
    """One-time migration: delete the pre-v0.7.0 ``aw-remote-hosts`` shim.

    The framework reverts a journaled system_cli install by replaying the
    journal in REVERSE ON UNINSTALL only, and that journal is in-memory —
    rebuilt each boot from what activate() actually registers. So an app
    *update* never triggers the revert: dropping the manifest entry stops
    the shim being recreated, but the file already on disk would sit there
    forever, shadowing nothing and lying about how to reach this app.
    Hence removing it here, on activate.

    This deletes exactly one path, one this app itself created, and never
    touches anything else in bin/ — so it needs no capability (we drop
    commands:install in this same version) and is idempotent: after the
    first post-update boot it's a no-op. Returns the path removed, or None.
    """
    path = os.path.join(_workspace_home(), "bin", _LEGACY_SHIM)
    try:
        os.remove(path)
    except FileNotFoundError:
        return None
    return path


def _publish_env_vars() -> list[str]:
    """Upsert this process's AW_BACKEND_URL/AW_WORKSPACE/AW_WORKSPACE_HOST_TOKEN
    into <AW_WORKSPACE_HOME>/.env, preserving every other line/key already
    there. Skips a var entirely if unset here (e.g. no /link yet) rather
    than writing an empty value over a previously-published one. Returns
    the list of keys actually written, for the activate() log line."""
    values = {k: os.environ.get(k) for k in _ENV_VARS}
    values = {k: v for k, v in values.items() if v}
    if not values:
        return []

    home = _workspace_home()
    env_path = os.path.join(home, ".env")

    lines: list[str] = []
    try:
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        pass

    remaining = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}\n"
    for key, val in remaining.items():
        lines.append(f"{key}={val}\n")

    os.makedirs(home, exist_ok=True)
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return list(values)


class RemoteHostCliAppPlugin:
    async def activate(self, ctx) -> None:
        try:
            removed = _remove_legacy_shim()
        except OSError:
            removed = None
            log.warning("aw-app-remote-host-cli: removing the legacy %s shim failed",
                        _LEGACY_SHIM, exc_info=True)
        if removed:
            log.info("aw-app-remote-host-cli: removed legacy shim %s "
                     "(use 'aw-workspace-cli remote-hosts' instead)", removed)

        try:
            published = _publish_env_vars()
        except Exception:
            published = []
            log.warning("aw-app-remote-host-cli: publishing env vars to .env failed", exc_info=True)

        log.info("aw-app-remote-host-cli activated: published %s", published)

    async def deactivate(self) -> None:
        # Nothing to undo: the CLI surface is a contributed command file that
        # goes away with the app dir itself, and there is no journaled side
        # effect left to revert (the system_cli install was dropped in v0.7.0).
        # The published .env values are left in place on purpose (harmless,
        # matches AW_WORKSPACE_API_KEY's own uninstall-independent .env
        # lifetime) — a real revoke already invalidates AW_WORKSPACE_HOST_TOKEN
        # server-side regardless of what's cached in this file.
        log.info("aw-app-remote-host-cli deactivated")
