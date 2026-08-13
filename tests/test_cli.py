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


class ExecWaitTest(unittest.TestCase):
    """`exec-wait` (alias `run`) — exec + wait in one call, rendered like a
    local command instead of as a JSON envelope."""

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_starts_then_waits_on_the_job_it_started(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.exec_start.return_value = {"job_id": "job1"}
        c.exec_wait.return_value = {"status": "exited", "exit_code": 0, "stdout": "hi\n", "stderr": ""}

        code = cli.main(["exec-wait", "echo hi", "--timeout", "7", "--host", "1111aaaa2222bbbb"])

        self.assertEqual(code, 0)
        c.exec_start.assert_called_once_with("echo hi", timeout_s=7.0, host_id="1111aaaa2222bbbb")
        c.exec_wait.assert_called_once_with("job1", timeout_s=7.0, host_id="1111aaaa2222bbbb")

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_run_alias_hits_the_same_path(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.exec_start.return_value = {"job_id": "job1"}
        c.exec_wait.return_value = {"status": "exited", "exit_code": 0, "stdout": "", "stderr": ""}

        self.assertEqual(cli.main(["run", "true"]), 0)
        c.exec_start.assert_called_once()

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_writes_raw_stdout_and_stderr_not_json(self, mock_client_cls):
        """The point of the command: `ps aux` output must come out readable,
        not as an escaped \\n-laden JSON string."""
        c = mock_client_cls.return_value
        c.exec_start.return_value = {"job_id": "job1"}
        c.exec_wait.return_value = {
            "status": "exited", "exit_code": 0,
            "stdout": "USER  PID\nroot  1\n", "stderr": "warn\n",
        }

        with patch("sys.stdout") as out, patch("sys.stderr") as err:
            cli.main(["exec-wait", "ps aux"])

        self.assertEqual("".join(c.args[0] for c in out.write.call_args_list if c.args),
                         "USER  PID\nroot  1\n")
        self.assertIn("warn\n", "".join(c.args[0] for c in err.write.call_args_list if c.args))

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_forwards_the_remote_exit_code(self, mock_client_cls):
        """Must be usable in an `if` / `&&` — a failing remote command cannot
        report success locally."""
        c = mock_client_cls.return_value
        c.exec_start.return_value = {"job_id": "job1"}
        c.exec_wait.return_value = {"status": "exited", "exit_code": 42, "stdout": "", "stderr": ""}

        self.assertEqual(cli.main(["exec-wait", "exit 42"]), 42)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_unfinished_job_exits_124_and_reports_the_job_id(self, mock_client_cls):
        """A wait that times out has no exit code to forward; returning 0 would
        call a still-running command a success. The job_id must survive so the
        caller can resume with `wait`."""
        c = mock_client_cls.return_value
        c.exec_start.return_value = {"job_id": "job99"}
        c.exec_wait.return_value = {"status": "running", "stdout": "", "stderr": ""}

        with patch("sys.stderr") as err:
            code = cli.main(["exec-wait", "sleep 600", "--timeout", "1"])

        self.assertEqual(code, cli.EXIT_TIMEOUT)
        self.assertIn("job99", "".join(x.args[0] for x in err.write.call_args_list if x.args))

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_json_flag_prints_the_envelope_instead(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.exec_start.return_value = {"job_id": "job1"}
        c.exec_wait.return_value = {"status": "exited", "exit_code": 0, "stdout": "hi\n", "stderr": ""}

        with patch("sys.stdout") as out:
            code = cli.main(["exec-wait", "echo hi", "--json"])

        self.assertEqual(code, 0)
        payload = json.loads("".join(x.args[0] for x in out.write.call_args_list if x.args))
        self.assertEqual(payload["job_id"], "job1")
        self.assertEqual(payload["stdout"], "hi\n")

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_dispatch_always_carries_the_job_id_back(self, mock_client_cls):
        """exec_wait's payload may omit job_id; dispatch backfills it from the
        id it started, otherwise a timed-out run is unrecoverable."""
        c = mock_client_cls.return_value
        c.exec_start.return_value = {"job_id": "job7"}
        c.exec_wait.return_value = {"status": "exited", "exit_code": 0}

        result = cli.dispatch("exec-wait", command="true")

        self.assertEqual(result["job_id"], "job7")

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_returns_exec_start_result_when_no_job_id_came_back(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.exec_start.return_value = {"error": "host offline"}

        result = cli.dispatch("exec-wait", command="true")

        self.assertEqual(result, {"error": "host offline"})
        c.exec_wait.assert_not_called()

    def test_dispatch_requires_a_command(self):
        with self.assertRaises(ValueError):
            cli.dispatch("exec-wait", client=MagicMock(), command=None)


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
        mock_client_cls.return_value.exec_start.assert_called_once_with("echo hi", timeout_s=5.0, host_id=None)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_exec_passes_host_id_when_given(self, mock_client_cls):
        mock_client_cls.return_value.exec_start.return_value = {"job_id": "abc123"}

        code = cli.main(["exec", "echo hi", "--host", "3333cccc4444dddd"])

        self.assertEqual(code, 0)
        mock_client_cls.return_value.exec_start.assert_called_once_with("echo hi", timeout_s=None, host_id="3333cccc4444dddd")

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_wait_passes_job_id_and_timeout(self, mock_client_cls):
        mock_client_cls.return_value.exec_wait.return_value = {"status": "exited"}

        code = cli.main(["wait", "abc123", "--timeout", "10"])

        self.assertEqual(code, 0)
        mock_client_cls.return_value.exec_wait.assert_called_once_with("abc123", timeout_s=10.0, host_id=None)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_kill_and_ps_and_exec_status(self, mock_client_cls):
        mock_client_cls.return_value.exec_kill.return_value = {"killed": True}
        self.assertEqual(cli.main(["kill", "abc123"]), 0)
        mock_client_cls.return_value.exec_kill.assert_called_once_with("abc123", host_id=None)

        mock_client_cls.return_value.list_processes.return_value = {"count": 0, "processes": []}
        self.assertEqual(cli.main(["ps"]), 0)
        mock_client_cls.return_value.list_processes.assert_called_once_with(host_id=None)

        mock_client_cls.return_value.exec_status.return_value = {"status": "running"}
        self.assertEqual(cli.main(["exec-status", "abc123"]), 0)
        mock_client_cls.return_value.exec_status.assert_called_once_with("abc123", host_id=None)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_kill_and_ps_and_exec_status_pass_host_id_when_given(self, mock_client_cls):
        mock_client_cls.return_value.exec_kill.return_value = {"killed": True}
        self.assertEqual(cli.main(["kill", "abc123", "--host", "3333cccc4444dddd"]), 0)
        mock_client_cls.return_value.exec_kill.assert_called_once_with("abc123", host_id="3333cccc4444dddd")

        mock_client_cls.return_value.list_processes.return_value = {"count": 0, "processes": []}
        self.assertEqual(cli.main(["ps", "--host", "3333cccc4444dddd"]), 0)
        mock_client_cls.return_value.list_processes.assert_called_once_with(host_id="3333cccc4444dddd")

        mock_client_cls.return_value.exec_status.return_value = {"status": "running"}
        self.assertEqual(cli.main(["exec-status", "abc123", "--host", "3333cccc4444dddd"]), 0)
        mock_client_cls.return_value.exec_status.assert_called_once_with("abc123", host_id="3333cccc4444dddd")

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
