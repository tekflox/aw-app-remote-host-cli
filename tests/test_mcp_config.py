#!/usr/bin/env python3
"""Unit tests for remote_host_cli_app/mcp_config.py — the mcp.json this app
writes on activate() so aw-mcp-gateway's app-scan discovers the 21
remote_host_* tools (mcp_server/server.py) as a stdio upstream.

Run: python -m pytest tests/test_mcp_config.py -q
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remote_host_cli_app.client import ENV_VARS  # noqa: E402
from remote_host_cli_app.mcp_config import SERVER_NAME, build_mcp_servers, write_mcp_json  # noqa: E402


class BuildMcpServersTest(unittest.TestCase):
    def test_entry_has_the_shape_the_gateway_expects(self):
        servers = build_mcp_servers()
        entry = servers[SERVER_NAME]
        self.assertEqual(entry["type"], "stdio")
        self.assertTrue(entry["enabled"])
        self.assertEqual(entry["command"], "python3")
        self.assertEqual(entry["args"], ["-m", "mcp_server.server"])

    def test_uses_cwd_app_dir_not_a_hardcoded_path(self):
        """mcp_server/server.py imports remote_host_cli_app as a sibling
        top-level package — spawn cwd must be THIS app's own installed dir,
        wherever that is, not a path baked in at write time."""
        entry = build_mcp_servers()[SERVER_NAME]
        self.assertTrue(entry.get("cwd_app_dir"))
        self.assertNotIn("cwd", entry)

    def test_server_name_is_stable(self):
        self.assertEqual(SERVER_NAME, "aw-app-remote-host-cli")
        self.assertEqual(build_mcp_servers(), build_mcp_servers())

    def test_bakes_the_three_credentials_into_the_upstream_env(self):
        """aw-mcp-gateway spawns this upstream in a container that has
        neither these vars nor <AW_WORKSPACE_HOME>/.env — client.py's own
        env/file fallbacks can't reach it, so the values have to travel in
        the upstream's own mcp.json env block instead (see mcp_config.py's
        module docstring)."""
        creds = {"AW_BACKEND_URL": "http://127.0.0.1:9025",
                  "AW_WORKSPACE": "test-workspace",
                  "AW_WORKSPACE_HOST_TOKEN": "awlk_testtoken"}
        with patch.dict(os.environ, creds, clear=False):
            env = build_mcp_servers()[SERVER_NAME]["env"]
        for key, value in creds.items():
            self.assertEqual(env[key], value)
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")

    def test_omits_a_credential_that_is_unset_rather_than_writing_empty(self):
        with patch.dict(os.environ, {k: "" for k in ENV_VARS}, clear=False):
            for k in ENV_VARS:
                os.environ.pop(k, None)
            env = build_mcp_servers()[SERVER_NAME]["env"]
        for key in ENV_VARS:
            self.assertNotIn(key, env)


class WriteMcpJsonTest(unittest.TestCase):
    def test_writes_a_valid_mcp_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = write_mcp_json(tmp)

            path = Path(tmp) / "mcp.json"
            self.assertTrue(path.is_file())
            on_disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk, doc)
            self.assertIn(SERVER_NAME, on_disk["mcpServers"])

    def test_second_call_with_unchanged_content_does_not_touch_mtime(self):
        """The gateway reloads on mtime, and activate() runs on every boot
        AND every reconcile — an unconditional rewrite is a reload loop."""
        with tempfile.TemporaryDirectory() as tmp:
            write_mcp_json(tmp)
            path = Path(tmp) / "mcp.json"
            first_mtime = path.stat().st_mtime_ns

            write_mcp_json(tmp)

            self.assertEqual(path.stat().st_mtime_ns, first_mtime)

    def test_rewrites_when_content_actually_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mcp.json"
            path.write_text('{"mcpServers": {}}\n', encoding="utf-8")
            stale_mtime = path.stat().st_mtime_ns

            doc = write_mcp_json(tmp)

            self.assertNotEqual(path.stat().st_mtime_ns, stale_mtime)
            self.assertIn(SERVER_NAME, doc["mcpServers"])


if __name__ == "__main__":
    unittest.main()
