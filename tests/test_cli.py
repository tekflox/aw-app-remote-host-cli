#!/usr/bin/env python3
"""Unit tests for remote_host_cli_app/cli.py — RemoteHostClient mocked out.
Run: python -m pytest tests/test_cli.py -q
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remote_host_cli_app import cli  # noqa: E402
from remote_host_cli_app.client import NotConfigured, RemoteHostError  # noqa: E402


class CliDispatchTest(unittest.TestCase):
    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_status_prints_client_result_as_json(self, mock_client_cls):
        mock_client_cls.return_value.status.return_value = {"hostname": "box1", "connected": True}

        with patch("sys.stdout") as mock_stdout:
            code = cli.main(["status"])

        self.assertEqual(code, 0)
        printed = "".join(c.args[0] for c in mock_stdout.write.call_args_list if c.args)
        self.assertEqual(json.loads(printed), {"hostname": "box1", "connected": True})

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_exec_passes_command_and_timeout(self, mock_client_cls):
        mock_client_cls.return_value.exec_start.return_value = {"job_id": "abc123"}

        code = cli.main(["exec", "echo hi", "--timeout", "5"])

        self.assertEqual(code, 0)
        mock_client_cls.return_value.exec_start.assert_called_once_with("echo hi", timeout_s=5.0)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_wait_passes_job_id_and_timeout(self, mock_client_cls):
        mock_client_cls.return_value.exec_wait.return_value = {"status": "exited"}

        code = cli.main(["wait", "abc123", "--timeout", "10"])

        self.assertEqual(code, 0)
        mock_client_cls.return_value.exec_wait.assert_called_once_with("abc123", timeout_s=10.0)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_kill_and_ps_and_exec_status(self, mock_client_cls):
        mock_client_cls.return_value.exec_kill.return_value = {"killed": True}
        self.assertEqual(cli.main(["kill", "abc123"]), 0)
        mock_client_cls.return_value.exec_kill.assert_called_once_with("abc123")

        mock_client_cls.return_value.list_processes.return_value = {"count": 0, "processes": []}
        self.assertEqual(cli.main(["ps"]), 0)

        mock_client_cls.return_value.exec_status.return_value = {"status": "running"}
        self.assertEqual(cli.main(["exec-status", "abc123"]), 0)
        mock_client_cls.return_value.exec_status.assert_called_once_with("abc123")

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_hosts_lists_account_wide_hosts(self, mock_client_cls):
        mock_client_cls.return_value.list_account_hosts.return_value = {
            "count": 2,
            "hosts": [
                {"id": "a", "workspace_slug": "acme", "connected": True},
                {"id": "b", "workspace_slug": "acme-staging", "connected": False},
            ],
        }

        code = cli.main(["hosts"])

        self.assertEqual(code, 0)
        mock_client_cls.return_value.list_account_hosts.assert_called_once_with()

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_not_configured_exits_2(self, mock_client_cls):
        mock_client_cls.return_value.status.side_effect = NotConfigured("missing env")

        code = cli.main(["status"])

        self.assertEqual(code, 2)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_remote_host_error_exits_1(self, mock_client_cls):
        mock_client_cls.return_value.status.side_effect = RemoteHostError("Not found")

        code = cli.main(["status"])

        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
