# Calling aw-backend's remote-host routes from this app

This app is deliberately different from most `aw-app-*` apps:
`aw-app-template`'s documented auth pattern
(`docs/app-workspace-api-auth.md` there, or `aw-app-whiteboard`'s
`mcp_server/`) is for calling the **local aw-workspace API**
(`/api/apps/<slug>/...`) with the workspace-wide `X-Api-Key`. That's not
what this app does — it calls **aw-backend** (the control plane, a
different service, `AW_BACKEND_URL`, default `http://127.0.0.1:9025`)
directly, because the resource being accessed (the BYOD host linked via
`aw-remote-host`) lives in aw-backend's own domain
(`aw-backend/src/api/routes/host_link.py`), not in this workspace's own
routes.

## Auth: this workspace's own host token

Every aw-workspace container already carries `AW_WORKSPACE_HOST_TOKEN` — the
durable `awlk_` credential the `aw-remote-host` `/link` handshake minted for
this workspace, the same one `src/apps/registry_client.py` already uses to
poll the app-installs registry. This app's `remote_host_cli_app/client.py`
(shared by both the `aw-remote-hosts` CLI and `mcp_server/`) sends it as
`Authorization: Bearer <token>` on every call to
`/api/workspaces/{slug}/remote-host*`.

aw-backend resolves that token via `require_workspace_actor`
(`src/api/identity_guard.py`), which verifies the token's own
`workspace_slug` matches the `{slug}` in the URL **before** honoring it — a
host token can never address any workspace other than its own. That's the
whole isolation guarantee: this app can only ever see/exec-on the host
linked to the account it's running inside, never anyone else's.

## Why this needed an aw-backend change

Before this app existed, `host_link.py`'s remote-host routes
(`get_remote_host`, `exec_start`, `exec_status`, `exec_wait`, `exec_kill`,
`list_processes`) only accepted a human's identity JWT
(`require_identity` + `require_workspace_role(..., "owner")`) — there was no
way for a process running *inside* the workspace (no browser session) to
call them. `aw-backend`'s `app_installs.py` already had the right shape for
this (`require_workspace_actor`, accepting either a human JWT or the
workspace's own host token) for a different route family (the app-installs
registry); that helper was promoted to `identity_guard.py` and the
remote-host routes above were switched to it too — see that repo's commit
message for the full trade-off note (a leaked host token now grants remote
command execution, not just app-install management — accepted
deliberately).

## Not applicable here

- `docs/app-workspace-api-auth.md`'s `X-Api-Key` pattern (this repo doesn't
  ship it) — that's for the *local* workspace API, unrelated to aw-backend.
- `AW_WORKSPACE_API_KEY` — same reason.
