#!/usr/bin/env python3
"""Unit tests for remote_host_cli_app/plugin.py — _publish_env_vars(), the
"durably hand AW_WORKSPACE_HOST_TOKEN to any process on this filesystem"
half of the app (client.py's .env fallback is the read side,
tests/test_client.py), and _remove_legacy_shim(), the v0.7.0 migration.
No ctx/framework needed — pure file I/O.

Run: python -m pytest tests/test_plugin.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remote_host_cli_app.plugin import _publish_env_vars, _remove_legacy_shim  # noqa: E402


class RemoveLegacyShimTest(unittest.TestCase):
    """v0.7.0 migration: the app used to install <home>/bin/aw-remote-hosts
    via contributes.system_clis. An app *update* never triggers the
    framework's uninstall-only journal revert, so activate() deletes it."""

    def test_removes_the_shim_and_reports_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / "bin" / "aw-remote-hosts"
            shim.parent.mkdir()
            shim.write_text("#!/usr/bin/env bash\nexec ...\n")

            with patch.dict(os.environ, {"AW_WORKSPACE_HOME": tmp}, clear=False):
                removed = _remove_legacy_shim()

            self.assertEqual(removed, str(shim))
            self.assertFalse(shim.exists())

    def test_is_a_no_op_on_a_workspace_that_never_had_the_shim(self):
        """Idempotent: every boot after the first must be a silent no-op, not
        an error the plugin has to swallow."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bin").mkdir()

            with patch.dict(os.environ, {"AW_WORKSPACE_HOME": tmp}, clear=False):
                self.assertIsNone(_remove_legacy_shim())
                self.assertIsNone(_remove_legacy_shim())

    def test_leaves_every_other_shim_in_bin_alone(self):
        """bin/ is shared by every app that installed a CLI — this migration
        must touch exactly one filename."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            (bin_dir / "aw-remote-hosts").write_text("legacy")
            (bin_dir / "aw-playwright-mcp").write_text("someone else's")
            (bin_dir / "cgc").write_text("someone else's")

            with patch.dict(os.environ, {"AW_WORKSPACE_HOME": tmp}, clear=False):
                _remove_legacy_shim()

            self.assertEqual(
                sorted(p.name for p in bin_dir.iterdir()), ["aw-playwright-mcp", "cgc"]
            )


class PublishEnvVarsTest(unittest.TestCase):
    def test_writes_all_three_vars_to_a_fresh_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "AW_WORKSPACE_HOME": tmp,
                    "AW_BACKEND_URL": "https://api.aw.tekflox.com",
                    "AW_WORKSPACE": "aw",
                    "AW_WORKSPACE_HOST_TOKEN": "awlk_abc123",
                },
                clear=False,
            ):
                published = _publish_env_vars()

            self.assertEqual(set(published), {"AW_BACKEND_URL", "AW_WORKSPACE", "AW_WORKSPACE_HOST_TOKEN"})
            content = (Path(tmp) / ".env").read_text()
            self.assertIn("AW_BACKEND_URL=https://api.aw.tekflox.com", content)
            self.assertIn("AW_WORKSPACE=aw", content)
            self.assertIn("AW_WORKSPACE_HOST_TOKEN=awlk_abc123", content)

    def test_preserves_unrelated_existing_lines_and_updates_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "AW_WORKSPACE_API_KEY=untouched-should-stay\n"
                "AW_WORKSPACE_HOST_TOKEN=awlk_stale_value\n"
            )

            with patch.dict(
                os.environ,
                {
                    "AW_WORKSPACE_HOME": tmp,
                    "AW_BACKEND_URL": "https://api.aw.tekflox.com",
                    "AW_WORKSPACE": "aw",
                    "AW_WORKSPACE_HOST_TOKEN": "awlk_fresh_value",
                },
                clear=False,
            ):
                _publish_env_vars()

            lines = env_path.read_text().splitlines()
            self.assertIn("AW_WORKSPACE_API_KEY=untouched-should-stay", lines)
            self.assertIn("AW_WORKSPACE_HOST_TOKEN=awlk_fresh_value", lines)
            self.assertNotIn("AW_WORKSPACE_HOST_TOKEN=awlk_stale_value", lines)
            # Updated in place, not duplicated.
            self.assertEqual(
                sum(1 for l in lines if l.startswith("AW_WORKSPACE_HOST_TOKEN=")), 1
            )

    def test_no_op_when_none_of_the_three_vars_are_set(self):
        """Not linked yet (no /link handshake) — must not write empty values
        or clobber a previously-published .env with blanks."""
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("AW_WORKSPACE_HOST_TOKEN=awlk_previously_published\n")

            with patch.dict(os.environ, {"AW_WORKSPACE_HOME": tmp}, clear=False):
                for key in ("AW_BACKEND_URL", "AW_WORKSPACE", "AW_WORKSPACE_HOST_TOKEN"):
                    os.environ.pop(key, None)
                published = _publish_env_vars()

            self.assertEqual(published, [])
            self.assertIn("AW_WORKSPACE_HOST_TOKEN=awlk_previously_published", env_path.read_text())


if __name__ == "__main__":
    unittest.main()
