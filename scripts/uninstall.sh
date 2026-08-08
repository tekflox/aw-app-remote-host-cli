#!/usr/bin/env bash
# Reverses install_remote_hosts_cli.sh. Called on app uninstall (journal
# replay per the ADR's Decision 7 — this script IS the revert action for the
# commands:install journal entry).
set -euo pipefail

AW_BIN_DIR="${AW_WORKSPACE_HOME:-$HOME/.aw-workspace}/bin"
rm -f "$AW_BIN_DIR/aw-remote-hosts"
