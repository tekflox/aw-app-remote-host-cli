"""Unit tests for remote_host_cli_app/shell.py — no real WebSocket, no tty.
Run: python -m pytest tests/test_shell.py -q
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remote_host_cli_app import shell  # noqa: E402
from remote_host_cli_app.cli import main  # noqa: E402


def _client(backend="http://127.0.0.1:9025", workspace="ws", token="awlk_x"):
    c = MagicMock()
    c.backend_url = backend
    c.workspace = workspace
    c.token = token
    return c


class ShellUrlTest(unittest.TestCase):
    def test_http_becomes_ws_and_carries_the_terminal_size(self):
        url = shell.shell_url(_client(), "host-1", 120, 40)
        self.assertEqual(
            url,
            "ws://127.0.0.1:9025/api/workspaces/ws/remote-hosts/host-1/shell"
            "?cols=120&rows=40&target=host",
        )

    def test_target_defaults_to_the_host_not_the_container(self):
        """`exec` runs on the host; a `shell` that quietly landed in the
        workspace container instead would be a different machine with an
        identical-looking prompt."""
        self.assertIn("target=host", shell.shell_url(_client(), "h", 80, 24))

    def test_workspace_target_is_carried_through(self):
        url = shell.shell_url(_client(), "h", 80, 24, shell.TARGET_WORKSPACE)
        self.assertIn("target=workspace", url)

    def test_https_becomes_wss(self):
        url = shell.shell_url(_client(backend="https://aw.example.com"), "h", 80, 24)
        self.assertTrue(url.startswith("wss://aw.example.com/api/"))

    def test_token_never_appears_in_the_url(self):
        """It goes in an Authorization header — a ?token= would land in every
        proxy access log between here and the backend."""
        self.assertNotIn("awlk_x", shell.shell_url(_client(), "h", 80, 24))


class ResolveHostTest(unittest.TestCase):
    def test_explicit_host_id_skips_the_status_call(self):
        c = _client()
        self.assertEqual(shell.resolve_host_id(c, "host-9"), "host-9")
        c.status.assert_not_called()

    def test_no_argument_resolves_this_workspaces_own_host(self):
        c = _client()
        c.status.return_value = {"id": "host-own", "connected": True}
        self.assertEqual(shell.resolve_host_id(c, None), "host-own")

    def test_unlinked_workspace_is_a_clear_error_not_a_connect_attempt(self):
        c = _client()
        c.status.return_value = {"connected": False}
        with self.assertRaises(shell.ShellUnavailable) as ctx:
            shell.resolve_host_id(c, None)
        self.assertIn("no remote host is linked", str(ctx.exception))

    def test_offline_host_fails_before_opening_a_socket(self):
        """A shell to a host whose aw-remote-host isn't dialed in would just
        hang on a black screen; say so instead."""
        c = _client()
        c.status.return_value = {"id": "h", "hostname": "box", "connected": False}
        with self.assertRaises(shell.ShellUnavailable) as ctx:
            shell.resolve_host_id(c, None)
        self.assertIn("box", str(ctx.exception))
        self.assertIn("not", str(ctx.exception))


class ConnectErrorTest(unittest.TestCase):
    def test_maps_close_codes_to_what_actually_went_wrong(self):
        self.assertIn("AW_WORKSPACE_HOST_TOKEN",
                      shell._connect_error(Exception("rejected: HTTP 401")))
        self.assertIn("owner", shell._connect_error(Exception("rejected: HTTP 403")))
        self.assertIn("hosts", shell._connect_error(Exception("rejected: HTTP 404")))

    def test_unrecognized_error_passes_through_verbatim(self):
        self.assertEqual(shell._connect_error(Exception("boom")), "boom")


class RunShellTest(unittest.TestCase):
    @patch("remote_host_cli_app.shell.RemoteHostClient")
    def test_refuses_a_non_tty_and_points_at_exec_wait(self, mock_cls):
        """Raw mode on a pipe would corrupt the caller's stream and never
        produce a usable shell — this must fail fast, not half-work."""
        with patch("sys.stdin") as stdin, patch("sys.stdout") as stdout:
            stdin.isatty.return_value = False
            stdout.isatty.return_value = True
            self.assertEqual(shell.run_shell(), 2)
        mock_cls.return_value.status.assert_not_called()


class CliWiringTest(unittest.TestCase):
    @patch("remote_host_cli_app.shell.run_shell")
    def test_shell_subcommand_forwards_the_host_argument(self, run):
        run.return_value = 0
        self.assertEqual(main(["shell", "host-7"]), 0)
        run.assert_called_once_with("host-7", "host")

    @patch("remote_host_cli_app.shell.run_shell")
    def test_host_argument_is_optional_and_target_defaults_to_host(self, run):
        run.return_value = 0
        main(["shell"])
        run.assert_called_once_with(None, "host")

    @patch("remote_host_cli_app.shell.run_shell")
    def test_target_workspace_is_forwarded(self, run):
        run.return_value = 0
        main(["shell", "--target", "workspace"])
        run.assert_called_once_with(None, "workspace")

    def test_an_unknown_target_is_rejected_by_the_parser(self):
        """argparse choices rather than a runtime check: a typo'd target must
        never reach the host, where it would come back as an opaque
        pty_close."""
        with self.assertRaises(SystemExit):
            main(["shell", "--target", "hsot"])

    @patch("remote_host_cli_app.shell.run_shell")
    def test_shell_unavailable_exits_2_with_the_reason(self, run):
        run.side_effect = shell.ShellUnavailable("host is not connected")
        self.assertEqual(main(["shell"]), 2)

    @patch("remote_host_cli_app.shell.run_shell")
    def test_missing_websockets_is_reported_as_optional_not_broken(self, run):
        """Core does not install an app's pip_requires, so this is a real
        deployment state — and it must not read like the app is broken."""
        run.side_effect = ImportError("No module named 'websockets'")
        self.assertEqual(main(["shell"]), 2)

    def test_shell_is_not_reachable_through_dispatch(self):
        """dispatch() must keep returning result dicts — an interactive PTY
        has no result, and the MCP server calls straight into dispatch."""
        from remote_host_cli_app.cli import COMMANDS, dispatch

        self.assertNotIn("shell", COMMANDS)
        with self.assertRaises(ValueError):
            dispatch("shell", client=_client())


if __name__ == "__main__":
    unittest.main()
