#!/usr/bin/env python3
"""Unit tests for the file-transfer half of the app — client.py's fs_*/
upload/download methods, the CLI subcommands over them, and the MCP tools
that route through the same dispatch(). httpx is mocked out; no network.

Run: python -m pytest tests/test_fs_transfer.py -q
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remote_host_cli_app import cli  # noqa: E402
from remote_host_cli_app.client import RemoteHostClient, RemoteHostError  # noqa: E402


def _configured_client() -> RemoteHostClient:
    return RemoteHostClient(
        backend_url="http://127.0.0.1:9025", workspace="acme", token="awlk_test_secret"
    )


class _FakeStreamResponse:
    """Stand-in for the streaming response httpx.stream() yields."""

    def __init__(self, payload: bytes, headers: dict | None = None, status_code: int = 200,
                 chunk_size: int = 7, error_body: dict | None = None):
        self.content = payload
        self.headers = headers if headers is not None else {
            "X-Sha256": hashlib.sha256(payload).hexdigest(),
        }
        self.status_code = status_code
        self._chunk_size = chunk_size
        self._error_body = error_body or {}

    def iter_bytes(self):
        for i in range(0, len(self.content), self._chunk_size):
            yield self.content[i:i + self._chunk_size]

    def read(self):
        return b""

    def json(self):
        return self._error_body


class _FakeStreamCM:
    def __init__(self, response):
        self._response = response
        self.exited = False

    def __enter__(self):
        return self._response

    def __exit__(self, *exc):
        self.exited = True
        return False


class DownloadTest(unittest.TestCase):
    def test_streams_to_disk_and_verifies_digest(self):
        payload = b"the quick brown fox" * 50
        cm = _FakeStreamCM(_FakeStreamResponse(payload))
        client = _configured_client()

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "sub", "out.bin")
            with patch("httpx.stream", return_value=cm) as mock_stream:
                result = client.download("/var/log/x", dest)

            self.assertEqual(Path(dest).read_bytes(), payload)
            self.assertEqual(result["bytes"], len(payload))
            self.assertEqual(result["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertTrue(result["verified"])
            # Parent dirs created, scratch file cleaned up.
            self.assertFalse(Path(f"{dest}.part").exists())
            _, kwargs = mock_stream.call_args
            self.assertEqual(kwargs["params"], {"path": "/var/log/x"})
        self.assertTrue(cm.exited, "the streaming context must be closed")

    def test_digest_mismatch_leaves_no_file_behind(self):
        """The defect that matters: a corrupted transfer must NOT land at the
        destination path looking like a complete file."""
        payload = b"data that arrives corrupted"
        response = _FakeStreamResponse(payload, headers={"X-Sha256": "0" * 64})
        client = _configured_client()

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "out.bin")
            with patch("httpx.stream", return_value=_FakeStreamCM(response)):
                with self.assertRaises(RemoteHostError) as ctx:
                    client.download("/var/log/x", dest)

            self.assertIn("mismatch", str(ctx.exception))
            self.assertFalse(Path(dest).exists())
            self.assertFalse(Path(f"{dest}.part").exists())

    def test_unverified_when_host_skipped_the_digest(self):
        payload = b"huge file, unhashed"
        response = _FakeStreamResponse(payload, headers={"X-Sha256-Skipped": "1"})
        client = _configured_client()

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "out.bin")
            with patch("httpx.stream", return_value=_FakeStreamCM(response)):
                result = client.download("/var/log/x", dest)

            self.assertFalse(result["verified"])
            self.assertEqual(Path(dest).read_bytes(), payload)

    def test_http_error_is_raised_before_any_body_is_consumed(self):
        response = _FakeStreamResponse(b"", status_code=404,
                                       error_body={"error": "read /nope: no such file"})
        client = _configured_client()

        with patch("httpx.stream", return_value=_FakeStreamCM(response)):
            with self.assertRaises(RemoteHostError) as ctx:
                client.download_bytes("/nope")

        self.assertIn("no such file", str(ctx.exception))

    def test_inline_read_aborts_past_the_size_limit(self):
        """Bounded mid-stream, not after the fact — an agent asking for a text
        file must not be able to pull a multi-GB binary into memory."""
        payload = b"x" * 5000
        response = _FakeStreamResponse(payload, chunk_size=1000)
        client = _configured_client()

        with patch("httpx.stream", return_value=_FakeStreamCM(response)):
            with self.assertRaises(RemoteHostError) as ctx:
                client.download_bytes("/big", max_bytes=2048)

        self.assertIn("limit", str(ctx.exception))


class UploadTest(unittest.TestCase):
    def _ok_response(self, payload: bytes) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "ok": True, "path": "/tmp/dest", "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(), "verified": True,
        }
        return resp

    def test_streams_the_file_handle_rather_than_reading_it_into_memory(self):
        payload = b"upload me" * 100
        client = _configured_client()

        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.bin")
            Path(src).write_bytes(payload)

            with patch("httpx.request", return_value=self._ok_response(payload)) as mock_request:
                result = client.upload(src, "/tmp/dest", mode="644")

            args, kwargs = mock_request.call_args
            self.assertEqual(args[0], "POST")
            self.assertTrue(args[1].endswith("/remote-host/fs/upload"))
            self.assertEqual(kwargs["params"], {"path": "/tmp/dest", "mode": "644"})
            # A file OBJECT, not bytes — that's what makes this stream.
            self.assertTrue(hasattr(kwargs["content"], "read"))
            self.assertTrue(result["ok"])

    def test_upload_bytes_sends_the_payload_directly(self):
        client = _configured_client()
        with patch("httpx.request", return_value=self._ok_response(b"inline")) as mock_request:
            client.upload_bytes(b"inline", "/tmp/dest")
        self.assertEqual(mock_request.call_args.kwargs["content"], b"inline")

    def test_error_response_becomes_remote_host_error(self):
        resp = MagicMock()
        resp.status_code = 409
        resp.json.return_value = {"error": "no live /link connection"}
        client = _configured_client()

        with patch("httpx.request", return_value=resp):
            with self.assertRaises(RemoteHostError) as ctx:
                client.upload_bytes(b"x", "/tmp/dest")

        self.assertIn("no live /link connection", str(ctx.exception))

    def test_targets_the_per_host_route_when_host_id_is_given(self):
        client = _configured_client()
        with patch("httpx.request", return_value=self._ok_response(b"x")) as mock_request:
            client.upload_bytes(b"x", "/tmp/dest", host_id="abc123")
        self.assertIn("/remote-hosts/abc123/fs/upload", mock_request.call_args.args[1])


class FsMetadataTest(unittest.TestCase):
    def _json_response(self, body: dict) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = body
        return resp

    def test_stat_forwards_the_digest_flag(self):
        client = _configured_client()
        with patch("httpx.request", return_value=self._json_response({"exists": True})) as mock_request:
            client.fs_stat("/etc/hosts", digest=True)
        self.assertEqual(mock_request.call_args.kwargs["params"], {"path": "/etc/hosts", "digest": "true"})

    def test_delete_defaults_to_non_recursive(self):
        """A plain `rm` must never be able to wipe a tree — the flag is
        explicit in the request body, not implied by the server default."""
        client = _configured_client()
        with patch("httpx.request", return_value=self._json_response({"deleted": True})) as mock_request:
            client.fs_delete("/tmp/d")
        self.assertEqual(mock_request.call_args.kwargs["json"], {"path": "/tmp/d", "recursive": False})


class CliFsTest(unittest.TestCase):
    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_push_forwards_local_and_remote_paths(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.upload.return_value = {"ok": True, "bytes": 3}

        code = cli.main(["push", "/local/f.txt", "/remote/f.txt", "--mode", "755"])

        self.assertEqual(code, 0)
        c.upload.assert_called_once_with("/local/f.txt", "/remote/f.txt",
                                          mode="755", host_id=None, timeout=None)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_pull_defaults_local_name_to_the_remote_basename(self, mock_client_cls):
        """`pull /var/log/syslog` has to be one argument — the scp default."""
        c = mock_client_cls.return_value
        c.download.return_value = {"ok": True}

        cli.main(["pull", "/var/log/syslog"])

        c.download.assert_called_once_with("/var/log/syslog", "syslog", host_id=None, timeout=None)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_pull_into_an_existing_directory_keeps_the_basename(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.download.return_value = {"ok": True}

        with tempfile.TemporaryDirectory() as tmp:
            cli.main(["pull", "/var/log/syslog", tmp])

        c.download.assert_called_once_with(
            "/var/log/syslog", os.path.join(tmp, "syslog"), host_id=None, timeout=None,
        )

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_rm_requires_the_recursive_flag_to_pass_it_on(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.fs_delete.return_value = {"deleted": True}

        cli.main(["rm", "/tmp/d"])
        c.fs_delete.assert_called_with("/tmp/d", recursive=False, host_id=None)

        cli.main(["rm", "-r", "/tmp/d"])
        c.fs_delete.assert_called_with("/tmp/d", recursive=True, host_id=None)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_ls_stat_mkdir_route_to_their_client_methods(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.fs_list.return_value = {"entries": []}
        c.fs_stat.return_value = {"exists": True}
        c.fs_mkdir.return_value = {"created": True}

        cli.main(["ls", "/tmp"])
        cli.main(["stat", "/tmp/f", "--digest"])
        cli.main(["mkdir", "/tmp/new"])

        c.fs_list.assert_called_once()
        c.fs_stat.assert_called_once_with("/tmp/f", digest=True, host_id=None)
        c.fs_mkdir.assert_called_once_with("/tmp/new", host_id=None)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_missing_local_file_exits_2_and_says_so(self, mock_client_cls):
        """A local problem must not be reported as a remote failure, or the
        user goes debugging the wrong machine."""
        mock_client_cls.return_value.upload.side_effect = FileNotFoundError(
            2, "No such file or directory", "/local/missing.txt"
        )
        code = cli.main(["push", "/local/missing.txt", "/remote/f.txt"])
        self.assertEqual(code, 2)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_remote_error_exits_1(self, mock_client_cls):
        mock_client_cls.return_value.fs_list.side_effect = RemoteHostError("host offline")
        self.assertEqual(cli.main(["ls", "/tmp"]), 1)


class DispatchReadWriteTest(unittest.TestCase):
    """`read`/`write` — dispatch-only operations that carry content inline,
    used by the MCP tools (there is no argparse subcommand for them)."""

    def test_read_returns_utf8_text_when_decodable(self):
        client = MagicMock()
        client.download_bytes.return_value = b"hello world"

        result = cli.dispatch("read", client=client, path="/etc/motd")

        self.assertEqual(result["encoding"], "utf-8")
        self.assertEqual(result["content"], "hello world")
        self.assertEqual(result["bytes"], 11)

    def test_read_falls_back_to_base64_for_binary(self):
        """Mangling a binary into replacement characters and calling it the
        file's contents would be worse than saying it's base64."""
        client = MagicMock()
        client.download_bytes.return_value = b"\xff\xfe\x00\x01"

        result = cli.dispatch("read", client=client, path="/bin/thing")

        self.assertEqual(result["encoding"], "base64")
        self.assertEqual(base64.b64decode(result["content"]), b"\xff\xfe\x00\x01")

    def test_write_encodes_utf8_by_default(self):
        client = MagicMock()
        client.upload_bytes.return_value = {"ok": True}

        cli.dispatch("write", client=client, path="/tmp/f", content="olá")

        client.upload_bytes.assert_called_once_with(
            "olá".encode("utf-8"), "/tmp/f", mode=None, host_id=None, timeout=None,
        )

    def test_write_accepts_base64_content(self):
        client = MagicMock()
        client.upload_bytes.return_value = {"ok": True}

        cli.dispatch("write", client=client, path="/tmp/f",
                     content=base64.b64encode(b"\x00binary").decode(), encoding="base64")

        self.assertEqual(client.upload_bytes.call_args.args[0], b"\x00binary")

    def test_write_rejects_an_unknown_encoding(self):
        with self.assertRaises(ValueError):
            cli.dispatch("write", client=MagicMock(), path="/tmp/f",
                         content="x", encoding="rot13")

    def test_fs_commands_require_a_path(self):
        for cmd in ("push", "pull", "ls", "stat", "mkdir", "rm", "read", "write"):
            with self.assertRaises(ValueError, msg=f"{cmd} accepted an empty path"):
                cli.dispatch(cmd, client=MagicMock())


class McpFsToolsTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from mcp_server import server  # noqa: PLC0415

        self.server = server

    def test_every_advertised_tool_maps_to_a_dispatch_command(self):
        """A tool listed but unmapped would KeyError at call time — the exact
        shape of silent breakage this repo's manifest check can't catch."""
        advertised = {t["name"] for t in self.server._TOOLS}
        self.assertEqual(advertised, set(self.server._TOOL_TO_CMD))
        for cmd in self.server._TOOL_TO_CMD.values():
            self.assertIn(cmd, cli.COMMANDS)

    def test_manifest_declares_every_tool_the_server_serves(self):
        """`contributes.mcp.provides` is what the gateway advertises — a tool
        missing there is invisible to every agent even though it works."""
        manifest = json.loads(
            (Path(__file__).resolve().parent.parent / "aw-app.json").read_text()
        )
        declared = set(manifest["contributes"]["mcp"]["provides"])
        self.assertEqual(declared, {t["name"] for t in self.server._TOOLS})

    def test_file_tools_forward_their_arguments(self):
        with patch.object(self.server, "dispatch", return_value={"ok": True}) as mock_dispatch:
            self.server._call("remote_host_write_file", {
                "path": "/tmp/f", "content": "hi", "encoding": "utf-8",
                "mode": "600", "host_id": "box1",
            })

        kwargs = mock_dispatch.call_args.kwargs
        self.assertEqual(mock_dispatch.call_args.args[0], "write")
        self.assertEqual(kwargs["path"], "/tmp/f")
        self.assertEqual(kwargs["content"], "hi")
        self.assertEqual(kwargs["mode"], "600")
        self.assertEqual(kwargs["host_id"], "box1")

    def test_local_filesystem_error_is_labelled_as_local(self):
        with patch.object(self.server, "dispatch",
                          side_effect=FileNotFoundError(2, "No such file", "/x")):
            result = self.server._call("remote_host_upload_file",
                                       {"local_path": "/x", "path": "/tmp/f"})

        self.assertFalse(result["ok"])
        self.assertIn("local filesystem", result["error"])

    def test_every_file_tool_declares_path_as_required(self):
        file_tools = {
            "remote_host_read_file", "remote_host_write_file", "remote_host_upload_file",
            "remote_host_download_file", "remote_host_list_directory", "remote_host_stat",
            "remote_host_mkdir", "remote_host_delete",
        }
        by_name = {t["name"]: t for t in self.server._TOOLS}
        self.assertTrue(file_tools <= set(by_name), "a file tool is missing from _TOOLS")
        for name in file_tools:
            self.assertIn("path", by_name[name]["inputSchema"].get("required", []),
                          f"{name} must require a path")


if __name__ == "__main__":
    unittest.main()
