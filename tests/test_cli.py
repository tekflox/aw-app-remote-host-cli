#!/usr/bin/env python3
"""Unit tests for remote_host_cli_app/cli.py — RemoteHostClient mocked out.
Run: python -m pytest tests/test_cli.py -q
"""
from __future__ import annotations

import argparse
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


class FirewallPortRangeParsingTest(unittest.TestCase):
    """--port accepts a single port or a range; anything else is a clean
    argparse error (exit 2), not a traceback."""

    def test_single_port(self):
        self.assertEqual(cli._port_range("8080"), (8080, 8080))

    def test_range(self):
        self.assertEqual(cli._port_range("8080-8090"), (8080, 8090))

    def test_non_numeric_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._port_range("not-a-port")

    def test_out_of_range_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._port_range("70000")

    def test_reversed_range_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            cli._port_range("9090-9080")

    def test_invalid_port_at_the_cli_exits_2_not_a_traceback(self):
        """argparse's own type= validation raises SystemExit(2) straight out
        of parse_args — same as any other bad flag, not a dispatch() error."""
        with patch("sys.stderr"), self.assertRaises(SystemExit) as ctx:
            cli.main(["firewall", "add", "--port", "nope"])
        self.assertEqual(ctx.exception.code, 2)


class FirewallAddTest(unittest.TestCase):
    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_add_parses_a_port_range_and_applies_defaults(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.firewall_add_rule.return_value = {"rule_id": "r1", "applied": True}

        code = cli.main(["firewall", "add", "--port", "8080-8090"])

        self.assertEqual(code, 0)
        c.firewall_add_rule.assert_called_once_with(
            8080, 8090, protocol="tcp", source_cidr="0.0.0.0/0", action="allow",
            priority=100, comment="", host_id=None,
        )

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_from_defaults_to_0000_when_omitted(self, mock_client_cls):
        """--from has no explicit default check elsewhere; pin it here since
        the plan calls it out as the one CIDR default that must hold."""
        c = mock_client_cls.return_value
        c.firewall_add_rule.return_value = {"rule_id": "r1", "applied": True}

        cli.main(["firewall", "add", "--port", "22"])

        self.assertEqual(c.firewall_add_rule.call_args.kwargs["source_cidr"], "0.0.0.0/0")

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_explicit_flags_override_every_default(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.firewall_add_rule.return_value = {"rule_id": "r1", "applied": True}

        cli.main([
            "firewall", "add", "--port", "53", "--proto", "udp", "--from", "10.0.0.0/8",
            "--action", "deny", "--priority", "10", "--comment", "dns", "--host", "aaaa1111bbbb2222",
        ])

        c.firewall_add_rule.assert_called_once_with(
            53, 53, protocol="udp", source_cidr="10.0.0.0/8", action="deny",
            priority=10, comment="dns", host_id="aaaa1111bbbb2222",
        )


class FirewallListRenderingTest(unittest.TestCase):
    """The drift footer is the central requirement of this card: `list` must
    never show a saved rule as if it were live when it isn't."""

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_footer_reports_in_sync_when_applied_matches_revision(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.firewall_get.return_value = {
            "backend": "nft", "privileged": True, "firewall_capable": True,
            "firewall_capability_reason": None, "lockdown": False,
            "revision": 7, "applied_revision": 7, "in_sync": True, "last_error": "",
            "rules": [],
        }

        with patch("sys.stdout") as out:
            code = cli.main(["firewall", "list"])

        self.assertEqual(code, 0)
        printed = "".join(c.args[0] for c in out.write.call_args_list if c.args)
        self.assertIn("revision=7 applied=7 (in sync)", printed)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_footer_changes_when_applied_revision_lags_behind(self, mock_client_cls):
        """A rule saved but not yet pushed (host offline) must not read the
        same as one that's actually enforced."""
        c = mock_client_cls.return_value
        c.firewall_get.return_value = {
            "backend": "nft", "privileged": True, "firewall_capable": True,
            "firewall_capability_reason": None, "lockdown": False,
            "revision": 7, "applied_revision": 6, "in_sync": False, "last_error": "host offline",
            "rules": [],
        }

        with patch("sys.stdout") as out:
            cli.main(["firewall", "list"])

        printed = "".join(c.args[0] for c in out.write.call_args_list if c.args)
        self.assertIn("revision=7 applied=6", printed)
        self.assertIn("PENDING: host offline", printed)
        self.assertNotIn("in sync", printed)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_incapable_host_shows_the_reason_instead_of_a_revision_line(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.firewall_get.return_value = {
            "backend": "", "privileged": False, "firewall_capable": False,
            "firewall_capability_reason": "not yet probed — host has not reported firewall capability",
            "lockdown": False, "revision": 0, "applied_revision": 0, "in_sync": True, "last_error": "",
            "rules": [],
        }

        with patch("sys.stdout") as out:
            cli.main(["firewall", "list"])

        printed = "".join(c.args[0] for c in out.write.call_args_list if c.args)
        self.assertIn("cannot apply firewall rules", printed)
        self.assertIn("not yet probed", printed)
        self.assertNotIn("revision=", printed)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_json_flag_emits_the_raw_envelope_with_no_footer_formatting(self, mock_client_cls):
        c = mock_client_cls.return_value
        envelope = {
            "backend": "nft", "privileged": True, "firewall_capable": True,
            "firewall_capability_reason": None, "lockdown": False,
            "revision": 3, "applied_revision": 3, "in_sync": True, "last_error": "",
            "rules": [{"id": "r1", "action": "allow", "protocol": "tcp", "port_from": 22,
                       "port_to": 22, "source_cidr": "0.0.0.0/0", "priority": 100, "enabled": True}],
        }
        c.firewall_get.return_value = envelope

        with patch("sys.stdout") as out:
            code = cli.main(["firewall", "list", "--json"])

        self.assertEqual(code, 0)
        printed = "".join(c.args[0] for c in out.write.call_args_list if c.args)
        self.assertEqual(json.loads(printed), envelope)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_table_renders_one_row_per_rule(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.firewall_get.return_value = {
            "backend": "nft", "privileged": True, "firewall_capable": True,
            "firewall_capability_reason": None, "lockdown": False,
            "revision": 1, "applied_revision": 1, "in_sync": True, "last_error": "",
            "rules": [
                {"id": "r1", "action": "allow", "protocol": "tcp", "port_from": 8080,
                 "port_to": 8080, "source_cidr": "0.0.0.0/0", "priority": 100, "enabled": True},
                {"id": "r2", "action": "deny", "protocol": "udp", "port_from": 8080,
                 "port_to": 8090, "source_cidr": "10.0.0.0/8", "priority": 50, "enabled": False},
            ],
        }

        with patch("sys.stdout") as out:
            cli.main(["firewall", "list"])

        printed = "".join(c.args[0] for c in out.write.call_args_list if c.args)
        self.assertIn("r1", printed)
        self.assertIn("8080", printed)
        self.assertIn("8080-8090", printed)
        self.assertIn("enabled", printed)
        self.assertIn("disabled", printed)


class FirewallOtherVerbsTest(unittest.TestCase):
    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_remove_dispatches_with_the_rule_id(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.firewall_remove_rule.return_value = {"applied": True}

        code = cli.main(["firewall", "remove", "rule123"])

        self.assertEqual(code, 0)
        c.firewall_remove_rule.assert_called_once_with("rule123", host_id=None)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_lockdown_on_and_off_map_to_booleans(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.firewall_set_lockdown.return_value = {"lockdown": True}

        cli.main(["firewall", "lockdown", "on"])
        c.firewall_set_lockdown.assert_called_once_with(True, host_id=None)

        c.firewall_set_lockdown.reset_mock()
        c.firewall_set_lockdown.return_value = {"lockdown": False}
        cli.main(["firewall", "lockdown", "off"])
        c.firewall_set_lockdown.assert_called_once_with(False, host_id=None)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_status_forces_a_reapply(self, mock_client_cls):
        c = mock_client_cls.return_value
        c.firewall_apply.return_value = {"applied": True, "in_sync": True}

        code = cli.main(["firewall", "status"])

        self.assertEqual(code, 0)
        c.firewall_apply.assert_called_once_with(host_id=None)

    @patch("remote_host_cli_app.cli.RemoteHostClient")
    def test_remote_host_error_exits_1(self, mock_client_cls):
        mock_client_cls.return_value.firewall_get.side_effect = RemoteHostError("Not found")

        code = cli.main(["firewall", "list"])

        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
