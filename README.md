# aw-app-remote-host-cli

Access the remote host(s) linked to your aw-workspace account (via the
`aw-remote-host` BYOD link) from inside your workspace — as a CLI, or as
MCP tools for an agent. Scoped, server-side, to only hosts linked to your
own account (see [`docs/backend-auth.md`](docs/backend-auth.md)).

## What it contributes

- **`aw-workspace-cli remote-hosts`** — an aw-workspace-cli command, live the
  moment the app is installed:

  ```
  aw-workspace-cli remote-hosts status
  aw-workspace-cli remote-hosts hosts
  aw-workspace-cli remote-hosts exec "echo hi" [--timeout 30] [--host <id>]
  aw-workspace-cli remote-hosts exec-status <job_id>
  aw-workspace-cli remote-hosts wait <job_id> [--timeout 30]
  aw-workspace-cli remote-hosts kill <job_id>
  aw-workspace-cli remote-hosts ps
  ```

  > **Changed in v0.7.0.** This used to be a standalone `aw-remote-hosts`
  > binary that the app installed into `<AW_WORKSPACE_HOME>/bin` via
  > `contributes.system_clis`. It is now a *contributed command*
  > (`commands/remote_hosts.py`, auto-discovered by aw-workspace-cli from
  > the installed app dir — see aw-workspace's `src/cli/discovery.py`), so
  > every workspace verb lives in one namespace, the command shows up in
  > `aw-workspace-cli help`, and the app no longer needs the high-risk
  > `commands:install` capability. Upgrading deletes the old shim on the
  > first boot after the update; update any script that called
  > `aw-remote-hosts` directly.

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
  `net:outbound`).
- `commands/remote_hosts.py` — the contributed `aw-workspace-cli
  remote-hosts` command. Auto-discovered; just puts the app dir on
  `sys.path` and calls `cli.main()`.
- `remote_host_cli_app/client.py` — `RemoteHostClient`, the shared HTTP
  client both the CLI and the MCP server wrap.
- `remote_host_cli_app/cli.py` — the argparse parser and `dispatch()`, the
  single source of truth shared by the command and the MCP server.
- `remote_host_cli_app/plugin.py` — `RemoteHostCliAppPlugin` entrypoint;
  `activate(ctx)` publishes the backend env vars into
  `<AW_WORKSPACE_HOME>/.env` and removes the pre-v0.7.0 shim if present.
- `mcp_server/` — the standalone MCP server (separate process, run via
  `python -m mcp_server.server`).
- `skills/aw-app-remote-hosts-cli/SKILL.md` — usage guide for an agent.
- `docs/backend-auth.md` — the auth pattern this app uses (aw-backend, not
  the local workspace API — different from most `aw-app-*` apps).
- `tests/` — `validate_manifest.py` (manifest/schema), `test_command.py`
  (the contributed command), `test_client.py` (httpx mocked), `test_cli.py`
  (client mocked), `test_plugin.py`, `test_mcp_server.py`. All run in CI on
  every push, gating the release.

## Not covered by this app

Minting/revoking bootstrap tokens and revoking the host link itself stay
owner-only, human-login operations in the aw-workspace console (Settings →
Integrations → Remote Host) — this app only covers the read/exec surface.

## CI/CD

`tests/validate_manifest.py` and `tests/test_*.py` run in
`tekflox/aw-marketplace`'s shared `app-release.yml` reusable workflow on
every push to `master` — a failure stops the release before any version
bump, tag, or marketplace catalog sync happens.
