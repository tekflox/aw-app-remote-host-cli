"""
Entrypoint referenced by aw-app.json's runtime.entrypoint
("remote_host_cli_app.plugin:RemoteHostCliAppPlugin").

activate(ctx) installs the aw-remote-hosts CLI THROUGH the gated
ctx.commands facade (capability commands:install), so the install is
journaled and the framework reverts it (via scripts/uninstall.sh) on
uninstall. This app contributes no backend routes/frontend — it's just a
CLI installer + a skill + a standalone MCP server (mcp_server/, not loaded
by this plugin — it's a separate process an agent CLI spawns on its own,
see mcp_server/README.md).
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("aw_apps.remote-host-cli")


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

        log.info("aw-app-remote-host-cli activated: installed %s", installed)

    async def deactivate(self) -> None:
        # Revert is driven by the framework's journal reverse-replay (it runs
        # scripts/uninstall.sh once on uninstall) — nothing to undo here.
        log.info("aw-app-remote-host-cli deactivated")
