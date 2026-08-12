---
name: aw-app-remote-hosts-cli
description: >-
  Check status of, run commands on, and manage processes on the remote
  host(s) linked to this aw-workspace account (via the aw-remote-host BYOD
  link), using the `aw-workspace-cli remote-hosts` command or this app's MCP
  tools. Use whenever
  asked to run a command on "the linked machine" / "my remote host" / "the
  BYOD box", check whether it's online, or manage a job running there.
---

# Remote Host CLI

Lets an agent (or a human, from a terminal) interact with the remote
host(s) linked to this aw-workspace account through the `aw-remote-host`
BYOD bootstrap client. Everything here is scoped to **only this account's
own linked host** — aw-backend enforces that server-side (see
`docs/backend-auth.md` in this app's repo), so there is no way to
accidentally address another workspace's machine.

## Two equivalent surfaces

1. **CLI** — `aw-workspace-cli remote-hosts`, installed on the workspace's PATH by this
   app. Prefer this from a shell/terminal context.
2. **MCP tools** — `remote_host_status`, `remote_host_exec_start`,
   `remote_host_exec_status`, `remote_host_exec_wait`, `remote_host_exec_kill`,
   `remote_host_list_processes`. Prefer these when driving from an agent
   that already has MCP tool access (no subprocess/shell needed).

Both wrap the exact same aw-backend routes
(`/api/workspaces/{slug}/remote-host*`) through the same client
(`remote_host_cli_app/client.py`) — pick whichever surface is already
available in your context.

## Workflow

1. **Check the host is online first**: `aw-workspace-cli remote-hosts status` (or
   `remote_host_status`). If `connected` is `false`, nothing else here will
   work — the linked machine's `aw-remote-host` process isn't currently
   dialed in.
2. **Start a command**: `aw-workspace-cli remote-hosts exec "<shell command>" [--timeout N]`
   (or `remote_host_exec_start`). Returns a `job_id` — this does NOT block.
3. **Wait for it to finish**: `aw-workspace-cli remote-hosts wait <job_id> [--timeout N]`
   (or `remote_host_exec_wait`) — blocks up to `timeout_s` and returns the
   exit status/output once the job finishes.
4. For a long-running or backgrounded command, poll instead of waiting:
   `aw-workspace-cli remote-hosts exec-status <job_id>` (or `remote_host_exec_status`).
5. **Kill** a job that's misbehaving: `aw-workspace-cli remote-hosts kill <job_id>` (or
   `remote_host_exec_kill`).
6. **List everything currently running** via this path:
   `aw-workspace-cli remote-hosts ps` (or `remote_host_list_processes`).

## Errors you'll actually see

- `NotConfigured` / exit code 2 from the CLI — this workspace hasn't
  completed the `aw-remote-host` `/link` handshake yet (no
  `AW_WORKSPACE_HOST_TOKEN`), or `AW_WORKSPACE`/`AW_BACKEND_URL` aren't set.
  Nothing to retry — tell the user to link a host first.
- `"Not found"` (404) — no active (non-revoked) `RemoteHost` row for this
  workspace. Same fix: link a host.
- A 409-shaped error (`CommandUnavailable` upstream) — the host row exists
  but isn't currently connected (no live `/link` WebSocket). Check `status`
  first; this is the same "offline" case, not a bug.

## Out of scope

This app never mints, revokes, or lists bootstrap tokens, and never revokes
the host link itself — those stay owner-only, human-login operations in the
aw-workspace console (Settings → Integrations → Remote Host). This app only
covers the read/exec surface.
