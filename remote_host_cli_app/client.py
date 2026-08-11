"""HTTP client for aw-backend's ``/api/workspaces/{slug}/remote-host*``
routes (see ``aw-backend/src/api/routes/host_link.py``) — shared by both the
installed ``aw-remote-hosts`` CLI (``cli.py``) and the standalone MCP server
(``mcp_server/server.py``), so the request-building/auth logic lives in
exactly one place.

Auth: this workspace's own ``AW_WORKSPACE_HOST_TOKEN`` (the ``awlk_``
credential the aw-remote-host ``/link`` handshake minted for this workspace —
same credential ``src/apps/registry_client.py`` already uses for the
app-installs registry). aw-backend accepts it via ``require_workspace_actor``
(``src/api/identity_guard.py``), which verifies the token belongs to exactly
this workspace's own ``slug`` before honoring it — so this client can only
ever reach hosts linked to THIS account, never another workspace's.

Reads ``os.environ`` first; when a value is missing there, falls back to
``<AW_WORKSPACE_HOME>/.env`` (``AW_WORKSPACE_ENV_FILE`` overrides the exact
path; default resolves against ``AW_WORKSPACE_CONTAINER_DIR``, NOT
``Path.home()`` — a caller reaching this file cross-container, e.g. an
agent's own spawned container, shares the workspace's ``/opt/aw-workspace``
filesystem tree but has its own unrelated ``$HOME``, so ``~/.aw-workspace``
would resolve to nothing there even though the real file is sitting right
there on the shared mount) — the same fallback ``docs/app-workspace-api-
auth.md``'s "external process" pattern documents for
``AW_WORKSPACE_API_KEY`` (see ``aw-app-whiteboard``'s ``mcp_server/`` for
the reference implementation this adapts, with that one difference).
``RemoteHostCliAppPlugin`` publishes all three vars there on every activate
(see ``plugin.py``), so ANY process that can read this workspace's shared
filesystem — not just the aw-workspace process itself, or a container the
Runner specially mounts creds into — gets a working client for free, no
per-agent wiring.
"""

from __future__ import annotations

import os

import httpx

DEFAULT_BACKEND_URL = "http://127.0.0.1:9025"
DEFAULT_WORKSPACE_CONTAINER_DIR = "/opt/aw-workspace"
ENV_VARS = ("AW_BACKEND_URL", "AW_WORKSPACE", "AW_WORKSPACE_HOST_TOKEN")


def _default_env_file() -> str:
    home = os.environ.get("AW_WORKSPACE_HOME") or os.path.join(
        os.environ.get("AW_WORKSPACE_CONTAINER_DIR", DEFAULT_WORKSPACE_CONTAINER_DIR), ".aw-workspace"
    )
    return os.path.join(home, ".env")


def _read_env_file_value(key: str) -> str | None:
    path = os.environ.get("AW_WORKSPACE_ENV_FILE") or _default_env_file()
    prefix = f"{key}="
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(prefix):
                    return line[len(prefix):].strip() or None
    except FileNotFoundError:
        return None
    return None


def _resolve(explicit: str | None, key: str, default: str = "") -> str:
    if explicit:
        return explicit
    return os.environ.get(key) or _read_env_file_value(key) or default


class NotConfigured(RuntimeError):
    """Raised when AW_BACKEND_URL / AW_WORKSPACE / AW_WORKSPACE_HOST_TOKEN
    aren't all present — this client only runs inside an aw-workspace
    container that has completed the aw-remote-host ``/link`` handshake."""


class RemoteHostError(RuntimeError):
    """Raised for any non-2xx response from aw-backend, with the parsed
    error message (falls back to the raw HTTP status) as the message."""


class RemoteHostClient:
    def __init__(self, backend_url: str | None = None, workspace: str | None = None,
                 token: str | None = None, timeout: float = 30.0) -> None:
        self.backend_url = _resolve(backend_url, "AW_BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/")
        self.workspace = _resolve(workspace, "AW_WORKSPACE")
        self.token = _resolve(token, "AW_WORKSPACE_HOST_TOKEN")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.backend_url and self.workspace and self.token)

    def _require_configured(self) -> None:
        if not self.configured:
            raise NotConfigured(
                "AW_BACKEND_URL, AW_WORKSPACE and AW_WORKSPACE_HOST_TOKEN must "
                "all be set — this only works inside an aw-workspace container "
                "that has completed the aw-remote-host /link handshake."
            )

    def _base(self, host_id: str | None = None) -> str:
        """``.../remote-host`` targets THIS workspace's own linked host (the
        original, single-host shape). When ``host_id`` is given, targets
        ANY host in the caller's account instead — the ``.../remote-hosts/
        {host_id}/...`` sibling routes ``list_account_hosts`` discovers ids
        from (aw-backend resolves account-ownership server-side; a host_id
        outside the caller's account 404s there, never a client-side check)."""
        if host_id:
            return f"{self.backend_url}/api/workspaces/{self.workspace}/remote-hosts/{host_id}"
        return f"{self.backend_url}/api/workspaces/{self.workspace}/remote-host"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 timeout: float | None = None, host_id: str | None = None) -> dict:
        self._require_configured()
        url = f"{self._base(host_id)}{path}"
        try:
            resp = httpx.request(
                method, url, json=json_body, headers=self._headers(),
                timeout=timeout if timeout is not None else self.timeout,
            )
        except httpx.HTTPError as e:
            raise RemoteHostError(str(e)) from e
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            raise RemoteHostError(data.get("error") or data.get("detail") or f"HTTP {resp.status_code}")
        return data

    # ---- verbs (mirror host_link.py's route shapes exactly) -------------

    def status(self) -> dict:
        """``GET .../remote-host`` — id, hostname, last_seen_at, connected."""
        return self._request("GET", "")

    def exec_start(self, command: str, timeout_s: float | None = None, host_id: str | None = None) -> dict:
        """``POST .../remote-host/exec`` (or ``.../remote-hosts/{host_id}/exec``
        when ``host_id`` is given) — returns job_id/pid/started."""
        body: dict = {"command": command}
        if timeout_s is not None:
            body["timeout_s"] = timeout_s
        return self._request("POST", "/exec", json_body=body, host_id=host_id)

    def exec_status(self, job_id: str, host_id: str | None = None) -> dict:
        """``GET .../remote-host/exec/{job_id}``."""
        return self._request("GET", f"/exec/{job_id}", host_id=host_id)

    def exec_wait(self, job_id: str, timeout_s: float | None = None, host_id: str | None = None) -> dict:
        """``POST .../remote-host/exec/{job_id}/wait`` — blocks host-side up
        to timeout_s; the HTTP call's own timeout covers that plus headroom,
        mirroring host_link.py's exec_wait relay budget."""
        body: dict = {}
        if timeout_s is not None:
            body["timeout_s"] = timeout_s
        wait_budget = float(timeout_s) if timeout_s else 30.0
        return self._request("POST", f"/exec/{job_id}/wait", json_body=body,
                              timeout=wait_budget + 15.0, host_id=host_id)

    def exec_kill(self, job_id: str, host_id: str | None = None) -> dict:
        """``POST .../remote-host/exec/{job_id}/kill``."""
        return self._request("POST", f"/exec/{job_id}/kill", json_body={}, host_id=host_id)

    def list_processes(self, host_id: str | None = None) -> dict:
        """``GET .../remote-host/processes`` — count + processes[]."""
        return self._request("GET", "/processes", host_id=host_id)

    def list_account_hosts(self) -> dict:
        """``GET /api/workspaces/{slug}/remote-hosts`` (plural — a SIBLING
        path, not nested under ``/remote-host/``) — every non-revoked host
        across every workspace this account owns, not just this one. Returns
        ``{count, hosts: [{id, workspace_slug, hostname, os, arch,
        last_seen_at, connected}, ...]}``."""
        self._require_configured()
        url = f"{self.backend_url}/api/workspaces/{self.workspace}/remote-hosts"
        try:
            resp = httpx.request("GET", url, headers=self._headers(), timeout=self.timeout)
        except httpx.HTTPError as e:
            raise RemoteHostError(str(e)) from e
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            raise RemoteHostError(data.get("error") or data.get("detail") or f"HTTP {resp.status_code}")
        return data
