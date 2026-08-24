"""Builds this app's own root ``mcp.json`` — the file aw-mcp-gateway's
app-scan reads directly (``scan_app_mcp_servers()`` in
repos/aw-mcp-gateway/back/gateway/config.py:99-140).

``contributes.mcp.provides`` in aw-app.json registers **nothing**; it is the
marketplace's "what you get" list — see
repos/aw-app-google-maps/google_maps_app/mcp_config.py:1-11 for the same
point made about that app. What actually wires the 21 ``remote_host_*``
tools into the gateway is this file, written on every ``activate()``.

Unlike google-maps (an in-process HTTP server with a per-instance hostname
and API key baked into the URL), this app's server
(``mcp_server/server.py``) is a plain stdio process with no port and no
secret in its own args/env — auth is the three env vars ``activate()``
already publishes to ``<AW_WORKSPACE_HOME>/.env`` (``plugin.py``'s
``_publish_env_vars``), which ``mcp_server/client.py`` reads back as a
fallback. So the entry below is static: same bytes on every boot on a given
checkout, nothing to template.

``mcp_server/server.py`` imports ``remote_host_cli_app`` as a sibling
top-level package (see ``mcp_server/README.md``) — it must be spawned with
cwd = this app's own installed package dir, or the import fails and the
upstream serves zero tools in silence. ``cwd_app_dir: true`` is the opt-in
flag ``scan_app_mcp_servers()`` reads for exactly this (config.py:124-132);
it resolves to whatever directory this app is actually installed under, so
nothing here has to assume ``/opt/aw-workspace/apps/remote-host-cli``.
"""
from __future__ import annotations

import json
from pathlib import Path

SERVER_NAME = "aw-app-remote-host-cli"


def build_mcp_servers() -> dict:
    return {
        SERVER_NAME: {
            "enabled": True,
            "type": "stdio",
            "command": "python3",
            "args": ["-m", "mcp_server.server"],
            "cwd_app_dir": True,
            "env": {"PYTHONUNBUFFERED": "1"},
        }
    }


def write_mcp_json(package_dir: str) -> dict:
    """Regenerate ``<package_dir>/mcp.json``, skipping the write when nothing
    changed.

    The skip matters: aw-mcp-gateway reloads on **mtime**, and this runs on
    every ``activate()`` — boot AND reconcile. An unconditional rewrite is a
    reload loop that briefly drops every tool the gateway proxies, including
    the ones from the session that triggered it.
    """
    doc = {"mcpServers": build_mcp_servers()}
    body = json.dumps(doc, indent=2) + "\n"
    path = Path(package_dir) / "mcp.json"
    try:
        if path.read_text(encoding="utf-8") == body:
            return doc
    except FileNotFoundError:
        pass
    path.write_text(body, encoding="utf-8")
    return doc
