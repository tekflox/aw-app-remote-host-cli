# aw-app-remote-host-cli

Access the remote host(s) linked to your aw-workspace account (via the
`aw-remote-host` BYOD link) from inside your workspace — as a CLI, or as
MCP tools for an agent. Scoped, server-side, to only hosts linked to your
own account (see [`docs/backend-auth.md`](docs/backend-auth.md)).

## What it installs

- **`aw-remote-hosts`** — a CLI on the workspace's PATH:

  ```
  aw-remote-hosts status
  aw-remote-hosts exec "echo hi" [--timeout 30]
  aw-remote-hosts exec-status <job_id>
  aw-remote-hosts wait <job_id> [--timeout 30]
  aw-remote-hosts kill <job_id>
  aw-remote-hosts ps
  ```

- A **skill** (`skills/aw-app-remote-hosts-cli/`) teaching an agent when and
  how to use the CLI or the MCP tools below.

- A **standalone MCP server** (`mcp_server/`, see its own
  [README](mcp_server/README.md)) exposing the same six operations as MCP
  tools: `remote_host_status`, `remote_host_exec_start`,
  `remote_host_exec_status`, `remote_host_exec_wait`, `remote_host_exec_kill`,
  `remote_host_list_processes`.

## How it's scoped to your account

Both the CLI and the MCP server call **aw-backend** directly (not the local
aw-workspace API), authenticating with this workspace's own
`AW_WORKSPACE_HOST_TOKEN` (already present in every aw-workspace container —
the same credential the reconciler uses for app-installs). aw-backend
verifies that token belongs to exactly this workspace before honoring any
`/api/workspaces/{slug}/remote-host*` call, so there's no way to address
another account's host. See [`docs/backend-auth.md`](docs/backend-auth.md)
for the full trade-off/design note, including the small `aw-backend` change
this app depended on.

## Layout

- `aw-app.json` — the manifest (`id: remote-host-cli`, `tier: inprocess`,
  `commands:install` + `net:outbound`).
- `remote_host_cli_app/client.py` — `RemoteHostClient`, the shared HTTP
  client both the CLI and the MCP server wrap.
- `remote_host_cli_app/cli.py` — the `aw-remote-hosts` CLI itself.
- `remote_host_cli_app/installer.py` / `scripts/install_remote_hosts_cli.sh` /
  `scripts/uninstall.sh` — installs a thin wrapper shim into the workspace's
  persistent bin dir (idempotent, journaled via `commands:install`).
- `remote_host_cli_app/plugin.py` — `RemoteHostCliAppPlugin` entrypoint;
  `activate(ctx)` installs the CLI via the gated `ctx.commands` facade.
- `mcp_server/` — the standalone MCP server (separate process, run via
  `python -m mcp_server.server`).
- `skills/aw-app-remote-hosts-cli/SKILL.md` — usage guide for an agent.
- `docs/backend-auth.md` — the auth pattern this app uses (aw-backend, not
  the local workspace API — different from most `aw-app-*` apps).
- `tests/` — `validate_manifest.py` (manifest/schema), `test_installer.py`
  (subprocess mocked), `test_client.py` (httpx mocked), `test_cli.py`
  (client mocked). All run in CI on every push, gating the release.

## Not covered by this app

Minting/revoking bootstrap tokens and revoking the host link itself stay
owner-only, human-login operations in the aw-workspace console (Settings →
Integrations → Remote Host) — this app only covers the read/exec surface.

## CI/CD

`tests/validate_manifest.py` and `tests/test_*.py` run in
`tekflox/aw-marketplace`'s shared `app-release.yml` reusable workflow on
every push to `master` — a failure stops the release before any version
bump, tag, or marketplace catalog sync happens.
