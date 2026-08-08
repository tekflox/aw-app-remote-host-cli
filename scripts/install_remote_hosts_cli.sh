#!/usr/bin/env bash
# Installs `aw-remote-hosts`, a thin wrapper CLI over this app's own
# remote_host_cli_app/cli.py, into the workspace's persistent bin dir
# (~/.aw-workspace/bin, on PATH, survives restarts). Idempotent — safe to
# re-run (on install, and on every reconcile pass after workspace
# recreation).
#
# Run with CWD = this app's installed package dir (see aw-workspace's
# src/apps/commands.py CommandInstaller._run) — captured below as APP_DIR
# and baked into the generated wrapper, since Tier-1 apps load under a
# synthetic aw_apps.<id> namespace inside the workspace process (not a
# plain importable top-level package — see cli.py's module docstring), so a
# separately-invoked CLI process needs an explicit PYTHONPATH to import it.
set -euo pipefail

APP_DIR="$(pwd)"
AW_BIN_DIR="${AW_WORKSPACE_HOME:-$HOME/.aw-workspace}/bin"
mkdir -p "$AW_BIN_DIR"

cat > "$AW_BIN_DIR/aw-remote-hosts" <<SCRIPT
#!/usr/bin/env bash
exec env PYTHONPATH="${APP_DIR}\${PYTHONPATH:+:\$PYTHONPATH}" python3 -m remote_host_cli_app.cli "\$@"
SCRIPT
chmod +x "$AW_BIN_DIR/aw-remote-hosts"

"$AW_BIN_DIR/aw-remote-hosts" --help >/dev/null
echo "aw-remote-hosts installed at $AW_BIN_DIR/aw-remote-hosts"
