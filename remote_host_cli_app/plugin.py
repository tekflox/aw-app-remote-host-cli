"""
Entrypoint referenced by aw-app.json's runtime.entrypoint
("remote_host_cli_app.plugin:RemoteHostCliAppPlugin").

activate(ctx) installs the aw-remote-hosts CLI THROUGH the gated
ctx.commands facade (capability commands:install), so the install is
journaled and the framework reverts it (via scripts/uninstall.sh) on
uninstall. This app contributes no backend routes/frontend — it's just a
CLI installer + a skill + a standalone MCP server (mcp_server/, not loaded
by this plugin — it's a separate process an agent CLI or the aw-mcp-gateway
spawns on its own, see mcp_server/README.md).

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

import json
import logging
import os

log = logging.getLogger("aw_apps.remote-host-cli")

_ENV_VARS = ("AW_BACKEND_URL", "AW_WORKSPACE", "AW_WORKSPACE_HOST_TOKEN")


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

    home = os.environ.get("AW_WORKSPACE_HOME") or os.path.join(
        os.environ.get("AW_WORKSPACE_CONTAINER_DIR", "/opt/aw-workspace"), ".aw-workspace"
    )
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
        with open(os.path.join(ctx.package_dir, "aw-app.json"), encoding="utf-8") as f:
            manifest = json.load(f)

        clis = manifest.get("contributes", {}).get("system_clis", [])
        installed = []
        for cli in clis:
            ctx.commands.install_system_cli(
                cli["name"], cli["installer"], uninstall="scripts/uninstall.sh"
            )
            installed.append(cli["name"])

        try:
            published = _publish_env_vars()
        except Exception:
            published = []
            log.warning("aw-app-remote-host-cli: publishing env vars to .env failed", exc_info=True)

        log.info("aw-app-remote-host-cli activated: installed %s, published %s",
                  installed, published)

    async def deactivate(self) -> None:
        # Revert is driven by the framework's journal reverse-replay (it runs
        # scripts/uninstall.sh once on uninstall) — nothing to undo here.
        # The published .env values are left in place on purpose (harmless,
        # matches AW_WORKSPACE_API_KEY's own uninstall-independent .env
        # lifetime) — a real revoke already invalidates AW_WORKSPACE_HOST_TOKEN
        # server-side regardless of what's cached in this file.
        log.info("aw-app-remote-host-cli deactivated")
