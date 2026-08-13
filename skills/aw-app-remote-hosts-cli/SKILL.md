---
name: aw-app-remote-hosts-cli
description: >-
  Check status of, run commands on, and manage processes on the remote
  host(s) linked to this aw-workspace account (via the aw-remote-host BYOD
  link), using the `aw-workspace-cli remote-hosts` command or this app's MCP
  tools — including an interactive PTY shell. Use whenever
  asked to run a command on "the linked machine" / "my remote host" / "the
  BYOD box", check whether it's online, manage a job running there, or open
  an interactive shell on it.
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
2. **MCP tools** — `remote_host_status`, `remote_host_exec_run`,
   `remote_host_exec_start`,
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
2. **Run it and get the output, in one call** — this is what you want almost
   every time: `aw-workspace-cli remote-hosts exec-wait "<shell command>"
   [--timeout N]` (or the `remote_host_exec_run` MCP tool). It starts the
   command, blocks for it, prints stdout/stderr raw (not JSON-escaped), and
   exits with the remote command's own exit code. `run` is an alias. Add
   `--json` if you need the full envelope.

   Only drop to the two-step form below when you deliberately want the
   `job_id` back immediately — a long build you'll poll, or something you may
   need to `kill`.

3. **Two-step — start**: `aw-workspace-cli remote-hosts exec "<shell command>" [--timeout N]`
   (or `remote_host_exec_start`). Returns a `job_id` — this does NOT block.
4. **Two-step — wait**: `aw-workspace-cli remote-hosts wait <job_id> [--timeout N]`
   (or `remote_host_exec_wait`) — blocks up to `timeout_s` and returns the
   exit status/output once the job finishes.
5. For a long-running or backgrounded command, poll instead of waiting:
   `aw-workspace-cli remote-hosts exec-status <job_id>` (or `remote_host_exec_status`).
6. **Kill** a job that's misbehaving: `aw-workspace-cli remote-hosts kill <job_id>` (or
   `remote_host_exec_kill`).
7. **List everything currently running** via this path:
   `aw-workspace-cli remote-hosts ps` (or `remote_host_list_processes`).

## Interactive shell (`shell`) — humans only

`aw-workspace-cli remote-hosts shell [<host_id>] [--target host|workspace]`
attaches a real PTY — arrow keys, job control, `vim`, `top`, a live
`tail -f`. With no argument it targets this workspace's own linked host; pass
an id from `hosts` for any other host in the account. **Ctrl-]** disconnects.

### `--target` — which machine

- `--target host` (**default**) — the box running the `aw-remote-host`
  process. For a bare metal link that's the metal; for a link running inside
  a container, that container. This is the same machine `exec`/`exec-wait`
  run on, which is why it's the default: two verbs of one command reaching
  different machines is a trap.
- `--target workspace` — that host's podman-managed workspace container
  (`aw-remote-host-workspace`). This is where the console's browser terminal
  has always landed, and it keeps landing there — the console sends no
  target, and no target means workspace.

The banner names where you landed, because the two often have identical
prompts and you cannot tell them apart by looking.

Two more things to know:

- **There is no MCP tool for this, deliberately.** A PTY streams for minutes
  and returns no result, so it can't be a tool call and isn't in
  `cli.dispatch()`. An agent driving this workspace should use `exec-wait`;
  `shell` is for a human at a terminal. It refuses to run on a non-tty
  (exit 2) rather than half-work.
- **No exit code comes back.** The pty protocol reports a closed session with
  a reason, never a status. `shell` exits 0 on a normal disconnect. Anything
  scriptable stays on `exec-wait`.

`--target host` needs an `aw-remote-host` new enough to understand the
`target` field on `pty_open`. An older binary ignores it and silently opens
the workspace container instead — if the prompt looks like the container when
you asked for the host, update the host binary.

Needs the `websockets` package. Core doesn't install an app's
`runtime.pip_requires`, so if it's absent the CLI says so and every other
subcommand keeps working.

### Exit codes from `exec-wait`

It forwards the REMOTE command's exit code, so a remote `1` is
indistinguishable from this CLI's own error `1` (same trade-off `ssh`
makes). Exit **124** means the wait timed out — the command may still be
running; the message names the `job_id` to resume with `wait`.

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
