"""HTTP client for aw-backend's ``/api/workspaces/{slug}/remote-host*``
routes (see ``aw-backend/src/api/routes/host_link.py``) — shared by both the
``aw-workspace-cli remote-hosts`` command (``cli.py``) and the standalone MCP server
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

import hashlib
import os

import httpx

DEFAULT_BACKEND_URL = "http://127.0.0.1:9025"
DEFAULT_WORKSPACE_CONTAINER_DIR = "/opt/aw-workspace"
ENV_VARS = ("AW_BACKEND_URL", "AW_WORKSPACE", "AW_WORKSPACE_HOST_TOKEN")
# Default read/write budget for a file transfer, as opposed to the 30s a
# control verb gets. Ten minutes covers a large file over a slow home uplink
# without hanging forever on a dead one.
DEFAULT_TRANSFER_TIMEOUT = 600.0
# Ceiling on an in-memory read (the MCP read_file tool). Anything larger is a
# `pull` to disk, not something to hand an agent as a string.
MAX_INLINE_READ_BYTES = 8 * 1024 * 1024


class _StreamContext:
    """Keeps httpx's streaming context manager alive for as long as the
    caller is reading the response, and closes it on exit — the response
    object alone doesn't own the connection."""

    def __init__(self, ctx, response):
        self._ctx = ctx
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, exc_type, exc, tb):
        return self._ctx.__exit__(exc_type, exc, tb)


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
                 token: str | None = None, timeout: float = 30.0,
                 transfer_timeout: float = DEFAULT_TRANSFER_TIMEOUT) -> None:
        self.backend_url = _resolve(backend_url, "AW_BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/")
        self.workspace = _resolve(workspace, "AW_WORKSPACE")
        self.token = _resolve(token, "AW_WORKSPACE_HOST_TOKEN")
        self.timeout = timeout
        self.transfer_timeout = transfer_timeout

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
                 params: dict | None = None, timeout: float | None = None,
                 host_id: str | None = None) -> dict:
        self._require_configured()
        url = f"{self._base(host_id)}{path}"
        try:
            resp = httpx.request(
                method, url, json=json_body, params=params, headers=self._headers(),
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

    # ---- file transfer (aw-backend's fs_* routes -> the host's fs_* verbs) --
    #
    # sha256 is verified END TO END here, not just trusted: the host hashes
    # what it read/wrote, aw-backend hashes what it relayed, and these methods
    # hash what actually reached/left this machine. Any link that corrupts or
    # truncates a transfer is caught at the last hop rather than producing a
    # silently bad file — which is the whole reason to prefer these over
    # `exec("base64 ...")`.

    def fs_list(self, path: str, host_id: str | None = None) -> dict:
        """``GET .../fs/list?path=`` — entries[] with name/path/is_dir/size."""
        return self._request("GET", "/fs/list", params={"path": path}, host_id=host_id)

    def fs_stat(self, path: str, digest: bool = False, host_id: str | None = None) -> dict:
        """``GET .../fs/stat?path=`` — exists plus, for a regular file,
        size/mode/modified_at (and sha256 when ``digest``)."""
        params = {"path": path}
        if digest:
            params["digest"] = "true"
        return self._request("GET", "/fs/stat", params=params, host_id=host_id)

    def fs_mkdir(self, path: str, host_id: str | None = None) -> dict:
        """``POST .../fs/mkdir`` — creates missing parents, idempotent."""
        return self._request("POST", "/fs/mkdir", json_body={"path": path}, host_id=host_id)

    def fs_delete(self, path: str, recursive: bool = False, host_id: str | None = None) -> dict:
        """``POST .../fs/delete`` — ``recursive`` is required for a non-empty
        directory, so a plain call can never wipe a tree by accident."""
        return self._request("POST", "/fs/delete",
                             json_body={"path": path, "recursive": recursive}, host_id=host_id)

    def _transfer_timeout(self, timeout: float | None) -> "httpx.Timeout":
        """Transfers are bounded by file size and link speed, not by the
        30s a control verb takes, so read/write get their own much larger
        budget while connect stays short (an unreachable backend should still
        fail fast rather than hang for ten minutes)."""
        budget = timeout if timeout is not None else self.transfer_timeout
        return httpx.Timeout(budget, connect=30.0)

    def upload(self, local_path: str, remote_path: str, mode: str | None = None,
               host_id: str | None = None, timeout: float | None = None) -> dict:
        """``POST .../fs/upload?path=`` — streams ``local_path`` off disk in
        chunks rather than reading it into memory, so a multi-GB file costs
        the same RSS as a small one."""
        with open(local_path, "rb") as f:
            return self._upload_content(f, remote_path, mode=mode, host_id=host_id, timeout=timeout)

    def upload_bytes(self, payload: bytes, remote_path: str, mode: str | None = None,
                     host_id: str | None = None, timeout: float | None = None) -> dict:
        """``upload`` for content already in memory — what the MCP
        ``write_file`` tool uses, where there is no local file to stream."""
        return self._upload_content(payload, remote_path, mode=mode, host_id=host_id, timeout=timeout)

    def _upload_content(self, content, remote_path: str, mode: str | None = None,
                        host_id: str | None = None, timeout: float | None = None) -> dict:
        self._require_configured()
        params = {"path": remote_path}
        if mode:
            params["mode"] = mode
        url = f"{self._base(host_id)}/fs/upload"
        try:
            resp = httpx.request(
                "POST", url, params=params, content=content, headers=self._headers(),
                timeout=self._transfer_timeout(timeout),
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

    def download(self, remote_path: str, local_path: str, host_id: str | None = None,
                 timeout: float | None = None) -> dict:
        """``GET .../fs/download?path=`` — streams to ``local_path``.

        Written to a ``.part`` file and renamed only after the digest checks
        out, so an interrupted or corrupted transfer never leaves a truncated
        file sitting at the destination path looking complete.
        """
        part_path = f"{local_path}.part"
        parent = os.path.dirname(os.path.abspath(local_path))
        if parent:
            os.makedirs(parent, exist_ok=True)

        hasher = hashlib.sha256()
        total = 0
        try:
            with self._stream_download(remote_path, host_id, timeout) as resp:
                expected = resp.headers.get("X-Sha256")
                skipped = resp.headers.get("X-Sha256-Skipped") == "1"
                with open(part_path, "wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
                        hasher.update(chunk)
                        total += len(chunk)
            digest = hasher.hexdigest()
            if expected and expected != digest:
                raise RemoteHostError(
                    f"sha256 mismatch after download: host reported {expected}, "
                    f"got {digest} — the file was NOT written to {local_path}"
                )
            os.replace(part_path, local_path)
        except BaseException:
            # A failed transfer must not leave its scratch file behind.
            try:
                os.remove(part_path)
            except OSError:
                pass
            raise
        return {
            "ok": True, "path": local_path, "remote_path": remote_path,
            "bytes": total, "sha256": digest, "verified": bool(expected) and not skipped,
        }

    def download_bytes(self, remote_path: str, host_id: str | None = None,
                       timeout: float | None = None, max_bytes: int | None = None) -> bytes:
        """``download`` into memory — what the MCP ``read_file`` tool uses.

        ``max_bytes`` aborts mid-stream rather than after the fact, so an
        agent that asked for a text file and hit a 4 GB binary doesn't take
        the process down with it.
        """
        buf = bytearray()
        with self._stream_download(remote_path, host_id, timeout) as resp:
            expected = resp.headers.get("X-Sha256")
            for chunk in resp.iter_bytes():
                buf += chunk
                if max_bytes is not None and len(buf) > max_bytes:
                    raise RemoteHostError(
                        f"{remote_path} exceeds the {max_bytes} byte limit for an "
                        f"in-memory read — use `pull` to stream it to a file instead"
                    )
        payload = bytes(buf)
        if expected and expected != hashlib.sha256(payload).hexdigest():
            raise RemoteHostError(f"sha256 mismatch after download of {remote_path}")
        return payload

    def _stream_download(self, remote_path: str, host_id: str | None, timeout: float | None):
        """Opens the streaming response, raising ``RemoteHostError`` for a
        non-2xx BEFORE any body byte is consumed (the error body is JSON, and
        httpx needs an explicit read to see it on a streamed response)."""
        self._require_configured()
        url = f"{self._base(host_id)}/fs/download"
        try:
            ctx = httpx.stream(
                "GET", url, params={"path": remote_path}, headers=self._headers(),
                timeout=self._transfer_timeout(timeout),
            )
            resp = ctx.__enter__()
        except httpx.HTTPError as e:
            raise RemoteHostError(str(e)) from e
        if resp.status_code >= 400:
            try:
                resp.read()
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = {}
            ctx.__exit__(None, None, None)
            raise RemoteHostError(data.get("error") or data.get("detail") or f"HTTP {resp.status_code}")
        return _StreamContext(ctx, resp)

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
