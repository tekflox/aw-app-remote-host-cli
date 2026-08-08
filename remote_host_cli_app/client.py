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

Read fresh from ``os.environ`` on every call (not cached at import time) —
these are plain container-level env vars (not the regenerable workspace API
key), so there's no rotation-propagation concern, but reading fresh keeps
this consistent with every other app-workspace-auth client in this codebase
and costs nothing.
"""

from __future__ import annotations

import os

import httpx

DEFAULT_BACKEND_URL = "http://127.0.0.1:9025"


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
        self.backend_url = (backend_url or os.environ.get("AW_BACKEND_URL", DEFAULT_BACKEND_URL)).rstrip("/")
        self.workspace = workspace or os.environ.get("AW_WORKSPACE", "")
        self.token = token or os.environ.get("AW_WORKSPACE_HOST_TOKEN", "")
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

    def _base(self) -> str:
        return f"{self.backend_url}/api/workspaces/{self.workspace}/remote-host"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 timeout: float | None = None) -> dict:
        self._require_configured()
        url = f"{self._base()}{path}"
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

    def exec_start(self, command: str, timeout_s: float | None = None) -> dict:
        """``POST .../remote-host/exec`` — returns job_id/pid/started."""
        body: dict = {"command": command}
        if timeout_s is not None:
            body["timeout_s"] = timeout_s
        return self._request("POST", "/exec", json_body=body)

    def exec_status(self, job_id: str) -> dict:
        """``GET .../remote-host/exec/{job_id}``."""
        return self._request("GET", f"/exec/{job_id}")

    def exec_wait(self, job_id: str, timeout_s: float | None = None) -> dict:
        """``POST .../remote-host/exec/{job_id}/wait`` — blocks host-side up
        to timeout_s; the HTTP call's own timeout covers that plus headroom,
        mirroring host_link.py's exec_wait relay budget."""
        body: dict = {}
        if timeout_s is not None:
            body["timeout_s"] = timeout_s
        wait_budget = float(timeout_s) if timeout_s else 30.0
        return self._request("POST", f"/exec/{job_id}/wait", json_body=body, timeout=wait_budget + 15.0)

    def exec_kill(self, job_id: str) -> dict:
        """``POST .../remote-host/exec/{job_id}/kill``."""
        return self._request("POST", f"/exec/{job_id}/kill", json_body={})

    def list_processes(self) -> dict:
        """``GET .../remote-host/processes`` — count + processes[]."""
        return self._request("GET", "/processes")
