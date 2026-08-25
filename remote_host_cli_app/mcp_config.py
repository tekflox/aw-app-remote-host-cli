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
secret in its own args — auth is the three env vars ``client.py`` needs
(``ENV_VARS``), which this file bakes directly into the entry's own ``env``
block below, read from ``os.environ`` at write time.

That baking is required, not cosmetic: aw-mcp-gateway (today: the
``aw-app-mcp-gateway`` Tier-2 container) only bind-mounts ``$AW_APPS_ROOT``
(the installed-apps root, read-only) into the process that spawns this
upstream — never ``$AW_WORKSPACE_HOME`` (see ``_container_volumes`` in
``src/apps/runtime.py``), and never sets these three vars in its own
container env either. So the ``<AW_WORKSPACE_HOME>/.env`` fallback
``client.py`` also supports (for callers on this workspace's own shared
filesystem, e.g. a runner agent) can't reach a stdio child the gateway
spawns — that process has neither the env vars nor the file. Putting the
values straight into this upstream's own ``mcp.json`` env block works
because the gateway DOES read that file (it's inside the ro-mounted apps
root) and passes an upstream's ``env`` straight into the child process
(``Upstream._spawn`` in aw-mcp-gateway's ``back/gateway/upstream.py``).

This file runs from ``activate(ctx)`` (``plugin.py``), which executes
in-process in the aw-workspace core — the one process that actually has
these three in ``os.environ`` — same as ``_publish_env_vars`` and re-run on
every activate (boot + reconcile), so a later ``/link`` or a token rotation
gets picked back up automatically: ``write_mcp_json``'s mtime-guarded write
below means the gateway only reloads (and drops the tool briefly) when a
value has actually changed, not on every boot.

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
import os
from pathlib import Path

from .client import ENV_VARS

SERVER_NAME = "aw-app-remote-host-cli"


def build_mcp_servers() -> dict:
    env = {"PYTHONUNBUFFERED": "1"}
    env.update({k: v for k in ENV_VARS if (v := os.environ.get(k))})
    return {
        SERVER_NAME: {
            "enabled": True,
            "type": "stdio",
            "command": "python3",
            "args": ["-m", "mcp_server.server"],
            "cwd_app_dir": True,
            "env": env,
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
