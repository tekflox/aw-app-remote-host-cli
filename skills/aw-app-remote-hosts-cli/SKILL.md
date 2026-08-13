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
BYOD bootstrap client. Everything here is scoped to **this account's own
linked hosts** — aw-backend enforces that server-side (see
`docs/backend-auth.md` in this app's repo), so there is no way to
accidentally address another account's machine.

## Two equivalent surfaces

1. **CLI** — `aw-workspace-cli remote-hosts`, installed on the workspace's PATH by this
   app. Prefer this from a shell/terminal context.
2. **MCP tools** — `remote_host_status`, `remote_host_exec_run`,
   `remote_host_exec_start`,
   `remote_host_exec_status`, `remote_host_exec_wait`, `remote_host_exec_kill`,
   `remote_host_list_processes`, `remote_host_list_hosts`. Prefer these when
   driving from an agent that already has MCP tool access (no subprocess/shell
   needed).

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

### Targeting a specific host

An account can have several linked hosts. `aw-workspace-cli remote-hosts
hosts` (or `remote_host_list_hosts`) lists every one across every workspace
the account owns, with its `id`, `hostname`, `os`/`arch` and `connected`
flag. Every verb above then takes `--host <ref>` to address that host instead
of this workspace's own.

**`<ref>` is an id, a workspace slug, or a hostname** — whichever you can
actually remember. `shell`'s positional argument takes the same three. Slugs
and hostnames match case-insensitively; ids are exact.

Ambiguity is never guessed at. A slug can match more than one host (re-linking
a workspace leaves the previous row behind), so:

- if exactly one match is `connected`, that is the one — the others are dead
  rows;
- otherwise the command fails and lists the candidates for you to name by id.

Opening a session on a machine you didn't name is a worse outcome than making
you type sixteen characters.

Same-account ownership is resolved server-side, so `--host` is only *which
URL gets called* — a host outside the account 404s rather than being a check
this client performs.

### Exit codes from `exec-wait`

It forwards the REMOTE command's exit code, so a remote `1` is
indistinguishable from this CLI's own error `1` (same trade-off `ssh`
makes). Exit **124** means the wait timed out — the command may still be
running; the message names the `job_id` to resume with `wait`.

## Interactive shell (`shell`) — humans only

`aw-workspace-cli remote-hosts shell [<host>] [--target host|workspace]`
attaches a real PTY — arrow keys, job control, `vim`, `top`, a live
`tail -f`. With no argument it targets this workspace's own linked host;
otherwise name any host in the account by id, workspace slug or hostname
(see "Targeting a specific host" above) — `shell my-workspace-slug` is the
usual way. **Ctrl-]** disconnects.

### `--target` — which machine

- `--target host` (**default**) — the box running the `aw-remote-host`
  process. For a bare metal link that's the metal; for a link that itself
  runs inside a container, that container. This is the same machine
  `exec`/`exec-wait` run on, which is why it's the default: two verbs of one
  command reaching different machines is a trap.
- `--target workspace` — that host's podman-managed workspace container
  (`aw-remote-host-workspace`). This is where the console's browser terminal
  has always landed, and it keeps landing there — the console sends no
  target, and no target means workspace.

Not every host has both. A host that runs no workspace container fails
`--target workspace` with an explicit error naming the target (no such
container, or no `podman` on `PATH`). That is the correct answer, not a bug.

The banner names where you landed, because the two often have identical
prompts and you cannot tell them apart by looking. To confirm from inside a
session, `hostname; id -un` — the two targets normally differ in both.

Two more things to know:

- **There is no MCP tool for this, deliberately.** A PTY streams for minutes
  and returns no result, so it can't be a tool call and isn't in
  `cli.dispatch()`. An agent driving this workspace should use `exec-wait`;
  `shell` is for a human at a terminal. It refuses to run on a non-tty
  (exit 2) rather than half-work.
- **No exit code comes back.** The pty protocol reports a closed session with
  a reason, never a status. `shell` exits 0 on a normal disconnect. Anything
  scriptable stays on `exec-wait`.

Needs the `websockets` package. Core doesn't install an app's
`runtime.pip_requires`, so if it's absent the CLI says so and every other
subcommand keeps working.

### A PTY is not request/response

Bytes written to a pty before the remote shell starts reading are **gone** —
there is no buffering contract and no error. Anything automating this channel
must wait for the shell to print something (its prompt) before sending the
first keystroke, exactly as a human does.

This bites intermittently rather than always: when the shell is slow to spawn
(a container exec, say) the startup delay hides it, and the same code then
loses its input against a fast local shell. If you see a prompt and no echo of
what you sent, this is why — not a dropped connection.

## Host version skew

Host-side features arrive with the `aw-remote-host` binary, which is
**updated independently of this app and of the backend**. A host older than
the feature does not always error — `--target host`, for one, is silently
ignored by a binary that predates it, and you land in the workspace container
instead.

Detect it by comparing, not by trusting: open both targets and run
`hostname; id -un` in each. Two identical answers mean the target field is
being ignored.

### Updating a host binary

`aw-workspace-cli update remote-host` exists, but it is gated on a **user
identity token** minted by a browser login to the console. An agent or a
plain CLI holding only the workspace's host credential cannot call it — this
is a deliberate boundary, not a bug to work around lightly.

When you do need to update a host from this surface, the operation is the
same one the built-in self-update performs, done by hand over `exec-wait`:

1. **Find the running binary.** Do not assume it is on `PATH` — the exec
   shell's environment is often not the service's. Read it out of whatever
   supervises the process (a systemd unit's `ExecStart`, a launchd plist's
   `ProgramArguments`, a container entrypoint script), or resolve
   `/proc/<pid>/exe` on Linux.
2. **Download the asset matching that host's `os`/`arch`** — `hosts` reports
   both. Verify it against the release's published checksums, noting that
   those hash the **archive**, not the binary inside it.
3. **Never write over the binary in place.** The running process holds that
   inode. Copy to a temporary name alongside it, `chmod +x`, keep a
   timestamped backup of the old one, then `mv` over the original. On macOS
   also clear the quarantine attribute before the `mv`.
4. **Restart according to what supervises it**, which differs per install:
   a systemd user unit, a launchd agent (whose label may be suffixed with the
   workspace slug), or a bare supervision loop in a container entrypoint. Find
   out first — `systemctl --user`, `launchctl list`, or what `pid 1` actually
   is.

**Restarting drops the `/link` tunnel**, which is the only way you are
reaching this machine. Confirm something will bring the process back up
before you kill it: a `Restart=`/`KeepAlive` directive, or a `while true`
entrypoint loop. Without that you have locked yourself out. Expect the host
to reappear in `status` within seconds to a minute.

Finally, **verify the restart actually happened.** A successful copy proves
nothing — the old process keeps running until it is replaced. Check the new
process's start time and the executable it is running, then re-run the
comparison from above.

## Errors you'll actually see

- `NotConfigured` / exit code 2 from the CLI — this workspace hasn't
  completed the `aw-remote-host` `/link` handshake yet (no
  `AW_WORKSPACE_HOST_TOKEN`), or `AW_WORKSPACE`/`AW_BACKEND_URL` aren't set.
  Nothing to retry — tell the user to link a host first.
- `"Not found"` (404) — no active (non-revoked) `RemoteHost` row for this
  workspace, or a `--host` id outside this account. Same fix: link a host, or
  take the id from `hosts`.
- `no host matching '<ref>' in this account` / `'<ref>' matches N hosts` —
  the reference didn't resolve. Both messages list the hosts they did see, so
  the next command is always in front of you; no need to call `hosts` again.
- A 409-shaped error (`CommandUnavailable` upstream) — the host row exists
  but isn't currently connected (no live `/link` WebSocket). Check `status`
  first; this is the same "offline" case, not a bug.
- `shell` closing immediately with a reason mentioning a container or
  `podman` — a `--target` the host cannot serve. See `--target` above.

## Out of scope

This app never mints, revokes, or lists bootstrap tokens, and never revokes
the host link itself — those stay owner-only, human-login operations in the
aw-workspace console (Settings → Integrations → Remote Host). This app only
covers the read/exec surface.
