#!/usr/bin/env python3
"""Unit tests for commands/remote_hosts.py — the aw-workspace-cli contributed
command that replaced the pre-v0.8.0 `aw-remote-hosts` shim.

The contract this file protects is the one aw-workspace's
src/cli/discovery.py actually enforces: a module exposing COMMAND (str),
DESCRIPTION (str) and run(args) -> int, loaded BY FILE PATH (importlib
spec_from_file_location) rather than as part of any package. So the tests
load it the same way discovery does — importing it as `commands.remote_hosts`
would test a path that never happens in production and would hide a missing
sys.path insertion.

Run: python -m pytest tests/test_command.py -q
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
COMMAND_FILE = ROOT / "commands" / "remote_hosts.py"


def _load_like_discovery():
    """Load the command module exactly the way aw-workspace-cli does."""
    spec = importlib.util.spec_from_file_location(
        "_aw_app_command_remote-host-cli_remote_hosts", COMMAND_FILE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CommandContractTest(unittest.TestCase):
    def test_exposes_the_discovery_contract(self):
        mod = _load_like_discovery()

        self.assertEqual(mod.COMMAND, "remote-hosts")
        self.assertIsInstance(mod.DESCRIPTION, str)
        self.assertTrue(mod.DESCRIPTION.strip())
        self.assertTrue(callable(mod.run))

    def test_app_dir_points_at_the_package_root_not_the_commands_dir(self):
        """APP_DIR is what the old shim exported as PYTHONPATH — the dir that
        CONTAINS remote_host_cli_app/, not commands/. Getting this off by one
        level is the whole failure mode this file exists to catch."""
        mod = _load_like_discovery()

        self.assertEqual(Path(mod.APP_DIR), ROOT)
        self.assertTrue((Path(mod.APP_DIR) / "remote_host_cli_app" / "cli.py").is_file())


class CommandRunTest(unittest.TestCase):
    def test_run_puts_the_app_dir_on_syspath_and_delegates_to_cli_main(self):
        mod = _load_like_discovery()
        saved = list(sys.path)
        try:
            while mod.APP_DIR in sys.path:
                sys.path.remove(mod.APP_DIR)

            with patch("remote_host_cli_app.cli.main", return_value=0) as main:
                rc = mod.run(["status"])

            self.assertEqual(rc, 0)
            self.assertIn(mod.APP_DIR, sys.path)
            main.assert_called_once_with(["status"], prog=mod.PROG)
        finally:
            sys.path[:] = saved

    def test_run_forwards_the_exit_code_from_cli_main(self):
        """NotConfigured is exit 2 — a caller scripting against this command
        must see that, not a flattened 0/1."""
        mod = _load_like_discovery()

        with patch("remote_host_cli_app.cli.main", return_value=2):
            self.assertEqual(mod.run(["status"]), 2)

    def test_run_accepts_no_args(self):
        mod = _load_like_discovery()

        with patch("remote_host_cli_app.cli.main", return_value=0) as main:
            mod.run([])

        main.assert_called_once_with([], prog=mod.PROG)

    def test_run_reports_a_broken_install_instead_of_raising(self):
        """A missing httpx (or a half-copied app dir) must surface as a
        readable CLI error + non-zero exit, not an ImportError traceback out
        of aw-workspace-cli itself."""
        mod = _load_like_discovery()
        real_import = __import__

        def boom(name, *a, **kw):
            if name.startswith("remote_host_cli_app"):
                raise ImportError("No module named 'httpx'")
            return real_import(name, *a, **kw)

        # Evicting the package is what makes `boom` reachable at all (an
        # already-imported module never hits __import__'s slow path). It must
        # be put BACK afterwards: a later test that imports the package again
        # would otherwise get a second, distinct set of module objects, and
        # `except ShellUnavailable` in cli.py stops matching the class a test
        # raised from the first copy. That failure lands in an unrelated test
        # file and looks like a bug in the code under test.
        evicted = {m: sys.modules[m] for m in list(sys.modules)
                   if m.startswith("remote_host_cli_app")}
        for mod_name in evicted:
            del sys.modules[mod_name]

        try:
            with patch("builtins.__import__", side_effect=boom):
                rc = mod.run(["status"])
        finally:
            sys.modules.update(evicted)

        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
