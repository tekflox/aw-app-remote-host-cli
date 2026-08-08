#!/usr/bin/env python3
"""Unit tests for remote_host_cli_app/installer.py with subprocess mocked
out — no real filesystem writes/network involved, safe to run in CI (see
aw-marketplace's app-release.yml, which runs this before any version
bump/tag/marketplace sync).

Run: python -m pytest tests/test_installer.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remote_host_cli_app import installer  # noqa: E402


class RemoteHostsCliInstallerTest(unittest.TestCase):
    @patch("remote_host_cli_app.installer.subprocess.run")
    def test_install_runs_script_at_the_correct_path(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="aw-remote-hosts installed at .../bin/aw-remote-hosts\n", stderr=""
        )

        out = installer.install_remote_hosts_cli()

        self.assertIn("aw-remote-hosts installed", out)
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0][-1], str(installer.SCRIPTS_DIR / "install_remote_hosts_cli.sh"))
        self.assertEqual(kwargs["cwd"], installer.APP_ROOT)

    @patch("remote_host_cli_app.installer.subprocess.run")
    def test_install_raises_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")

        with self.assertRaises(installer.InstallError):
            installer.install_remote_hosts_cli()

    @patch("remote_host_cli_app.installer.subprocess.run")
    def test_uninstall_runs_uninstall_script(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        installer.uninstall_remote_hosts_cli()

        args, _ = mock_run.call_args
        self.assertEqual(args[0][-1], str(installer.SCRIPTS_DIR / "uninstall.sh"))


if __name__ == "__main__":
    unittest.main()
