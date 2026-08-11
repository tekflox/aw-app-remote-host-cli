#!/usr/bin/env python3
"""Unit tests for remote_host_cli_app/client.py — httpx mocked out, no real
network. Run: python -m pytest tests/test_client.py -q
"""
from __future__ import annotations

import os as _os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remote_host_cli_app.client import NotConfigured, RemoteHostClient, RemoteHostError  # noqa: E402


def _configured_client() -> RemoteHostClient:
    return RemoteHostClient(
        backend_url="http://127.0.0.1:9025", workspace="acme", token="awlk_test_secret"
    )


class ConfigurationTest(unittest.TestCase):
    def test_not_configured_raises_before_any_network_call(self):
        """Explicit env-var pop + a nonexistent AW_WORKSPACE_ENV_FILE guarantee
        this stays "not configured" regardless of what's ambient on the
        machine running the test (e.g. a real aw-workspace host with a real
        published .env)."""
        with patch.dict("os.environ", {"AW_WORKSPACE_ENV_FILE": "/nonexistent/.env"}, clear=False):
            for key in ("AW_BACKEND_URL", "AW_WORKSPACE", "AW_WORKSPACE_HOST_TOKEN"):
                _os.environ.pop(key, None)
            client = RemoteHostClient(backend_url="", workspace="", token="")
            self.assertFalse(client.configured)
            with self.assertRaises(NotConfigured):
                client.status()

    def test_reads_from_env_when_no_explicit_args(self):
        with patch.dict(
            "os.environ",
            {
                "AW_BACKEND_URL": "http://backend.example",
                "AW_WORKSPACE": "acme",
                "AW_WORKSPACE_HOST_TOKEN": "awlk_from_env",
            },
            clear=False,
        ):
            client = RemoteHostClient()
            self.assertTrue(client.configured)
            self.assertEqual(client.backend_url, "http://backend.example")

    def test_falls_back_to_workspace_env_file_when_os_environ_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = f"{tmp}/.env"
            with open(env_path, "w") as f:
                f.write("AW_WORKSPACE_API_KEY=unrelated-should-be-ignored\n")
                f.write("AW_BACKEND_URL=https://api.aw.tekflox.com\n")
                f.write("AW_WORKSPACE=aw\n")
                f.write("AW_WORKSPACE_HOST_TOKEN=awlk_from_file\n")

            with patch.dict("os.environ", {"AW_WORKSPACE_ENV_FILE": env_path}, clear=False):
                for key in ("AW_BACKEND_URL", "AW_WORKSPACE", "AW_WORKSPACE_HOST_TOKEN"):
                    _os.environ.pop(key, None)
                client = RemoteHostClient()

            self.assertTrue(client.configured)
            self.assertEqual(client.backend_url, "https://api.aw.tekflox.com")
            self.assertEqual(client.workspace, "aw")
            self.assertEqual(client.token, "awlk_from_file")

    def test_explicit_os_environ_wins_over_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = f"{tmp}/.env"
            with open(env_path, "w") as f:
                f.write("AW_WORKSPACE_HOST_TOKEN=awlk_from_file_should_lose\n")

            with patch.dict(
                "os.environ",
                {"AW_WORKSPACE_ENV_FILE": env_path, "AW_WORKSPACE_HOST_TOKEN": "awlk_from_environ_wins"},
                clear=False,
            ):
                client = RemoteHostClient()

            self.assertEqual(client.token, "awlk_from_environ_wins")


class RequestShapeTest(unittest.TestCase):
    @patch("remote_host_cli_app.client.httpx.request")
    def test_status_hits_the_plain_remote_host_url_with_bearer_auth(self, mock_request):
        mock_request.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "rh1", "hostname": "box1", "connected": True},
        )
        client = _configured_client()

        result = client.status()

        self.assertEqual(result["hostname"], "box1")
        args, kwargs = mock_request.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "http://127.0.0.1:9025/api/workspaces/acme/remote-host")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer awlk_test_secret"})

    @patch("remote_host_cli_app.client.httpx.request")
    def test_exec_start_posts_command_and_optional_timeout(self, mock_request):
        mock_request.return_value = MagicMock(
            status_code=200, json=lambda: {"job_id": "abc123", "pid": 42, "started": True}
        )
        client = _configured_client()

        result = client.exec_start("echo hi", timeout_s=5)

        self.assertEqual(result["job_id"], "abc123")
        args, kwargs = mock_request.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "http://127.0.0.1:9025/api/workspaces/acme/remote-host/exec")
        self.assertEqual(kwargs["json"], {"command": "echo hi", "timeout_s": 5})

    @patch("remote_host_cli_app.client.httpx.request")
    def test_exec_start_omits_timeout_when_not_given(self, mock_request):
        mock_request.return_value = MagicMock(status_code=200, json=lambda: {"job_id": "abc123"})
        client = _configured_client()

        client.exec_start("echo hi")

        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["json"], {"command": "echo hi"})

    @patch("remote_host_cli_app.client.httpx.request")
    def test_exec_wait_widens_http_timeout_beyond_wait_budget(self, mock_request):
        mock_request.return_value = MagicMock(status_code=200, json=lambda: {"status": "exited"})
        client = _configured_client()

        client.exec_wait("abc123", timeout_s=5)

        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["timeout"], 20.0)  # 5 + 15 headroom, mirrors host_link.py

    @patch("remote_host_cli_app.client.httpx.request")
    def test_error_response_raises_with_parsed_message(self, mock_request):
        mock_request.return_value = MagicMock(
            status_code=409, json=lambda: {"error": "host offline"}
        )
        client = _configured_client()

        with self.assertRaises(RemoteHostError) as ctx:
            client.exec_start("echo hi")
        self.assertIn("host offline", str(ctx.exception))

    @patch("remote_host_cli_app.client.httpx.request")
    def test_list_processes_and_kill_use_expected_paths(self, mock_request):
        mock_request.return_value = MagicMock(status_code=200, json=lambda: {"count": 0, "processes": []})
        client = _configured_client()

        client.list_processes()
        args, _ = mock_request.call_args
        self.assertEqual(args, ("GET", "http://127.0.0.1:9025/api/workspaces/acme/remote-host/processes"))

        mock_request.return_value = MagicMock(status_code=200, json=lambda: {"killed": True})
        client.exec_kill("abc123")
        args, _ = mock_request.call_args
        self.assertEqual(args, ("POST", "http://127.0.0.1:9025/api/workspaces/acme/remote-host/exec/abc123/kill"))

    @patch("remote_host_cli_app.client.httpx.request")
    def test_list_account_hosts_hits_the_plural_sibling_path(self, mock_request):
        mock_request.return_value = MagicMock(
            status_code=200,
            json=lambda: {"count": 2, "hosts": [
                {"id": "a", "workspace_slug": "acme", "connected": True},
                {"id": "b", "workspace_slug": "acme-staging", "connected": False},
            ]},
        )
        client = _configured_client()

        result = client.list_account_hosts()

        self.assertEqual(result["count"], 2)
        args, kwargs = mock_request.call_args
        # Plural "remote-hosts", NOT nested under the singular "/remote-host/" path.
        self.assertEqual(args, ("GET", "http://127.0.0.1:9025/api/workspaces/acme/remote-hosts"))
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer awlk_test_secret"})


if __name__ == "__main__":
    unittest.main()
