"""``aw-workspace-cli remote-hosts`` — this app's own CLI command.

Auto-discovered by aw-workspace-cli from this app's installed directory
(``<apps_root>/remote-host-cli/commands/``, since this file lives at
``commands/`` in this repo's root — see aw-workspace's
``src/cli/discovery.py``, which loads every ``<apps_root>/<slug>/
commands/*.py`` exposing ``COMMAND``/``DESCRIPTION``/``run``).

Replaces the standalone ``aw-remote-hosts`` shim this app used to drop into
``<AW_WORKSPACE_HOME>/bin`` via ``contributes.system_clis`` (removed in
v0.8.0). One namespace for every workspace verb, the command shows up in
``aw-workspace-cli help``, and the app no longer needs the high-risk
``commands:install`` capability just to be reachable from a terminal.

Every flag/parser/behavior stays defined in ``remote_host_cli_app.cli``
(single source of truth, shared with ``mcp_server/server.py``); this file
only puts the app's package dir on ``sys.path`` and calls ``main()``.
That import step is the same one the old bash shim did with an explicit
``PYTHONPATH``: Tier-1 apps load under a synthetic ``aw_apps.<id>``
namespace inside the *workspace* process (see aw-workspace's
``src/apps/runtime.py:_import_plugin``), so ``remote_host_cli_app`` is not
importable as a plain top-level package from the separate
``aw-workspace-cli`` process without this.

Usage:
    aw-workspace-cli remote-hosts status
    aw-workspace-cli remote-hosts hosts
    aw-workspace-cli remote-hosts exec "ps aux" --timeout 30
    aw-workspace-cli remote-hosts wait <job_id>
    aw-workspace-cli remote-hosts ps
"""
from __future__ import annotations

import os
import sys

COMMAND = "remote-hosts"
DESCRIPTION = "Status/exec/ps on the remote hosts linked to this account (BYOD)"

# <this file>/../ — the app package dir, i.e. what the old shim exported as
# PYTHONPATH. Resolved from __file__ rather than apps_root() so this works
# identically from the installed copy and from a checkout under repos/.
APP_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

PROG = "aw-workspace-cli remote-hosts"


def run(args: list[str]) -> int:
    if APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)

    try:
        from remote_host_cli_app.cli import main
    except ImportError as exc:  # missing dep (httpx) or a broken install
        print(f"{PROG}: cannot load the remote-host client from {APP_DIR}: {exc}",
              file=sys.stderr)
        return 1

    return main(list(args or []), prog=PROG)
