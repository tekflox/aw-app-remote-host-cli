#!/usr/bin/env python3
"""Unit tests for mcp_server/server.py — cli.dispatch() mocked out, no real
network. Confirms the MCP layer is a thin protocol adapter over the same
dispatch() the aw-workspace-cli remote-hosts command itself calls (no separate client logic).

Run: python -m pytest tests/test_mcp_server.py -q
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp_server import server  # noqa: E402
from remote_host_cli_app.client import NotConfigured, RemoteHostError  # noqa: E402


class ToolsListTest(unittest.TestCase):
    def test_lists_all_seven_tools(self):
        resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(names, set(server._TOOL_TO_CMD))
        self.assertIn("remote_host_list_hosts", names)



def _assert_dispatched(test, mock_dispatch, dispatch_cmd: str, **expected):
    """Assert WHICH dispatch command ran and the kwargs this tool is about,
    ignoring the rest.

    Pinning the full kwargs signature instead made every one of these tests
    fail the moment dispatch() grew the file-transfer parameters — noise about
    an unrelated feature, not a real regression in exec/status routing.
    """
    test.assertEqual(mock_dispatch.call_count, 1)
    test.assertEqual(mock_dispatch.call_args.args[0], dispatch_cmd)
    for key, value in expected.items():
        test.assertEqual(mock_dispatch.call_args.kwargs.get(key), value,
                         f"dispatch was called with the wrong {key}")

class ToolsCallDelegatesToDispatchTest(unittest.TestCase):
    @patch("mcp_server.server.dispatch")
    def test_status_calls_dispatch_with_status_command(self, mock_dispatch):
        mock_dispatch.return_value = {"hostname": "box1", "connected": True}

        resp = server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "remote_host_status", "arguments": {}},
        })

        _assert_dispatched(self, mock_dispatch, "status")
        self.assertFalse(resp["result"]["isError"])
        self.assertEqual(json.loads(resp["result"]["content"][0]["text"]),
                          {"hostname": "box1", "connected": True})

    @patch("mcp_server.server.dispatch")
    def test_exec_start_passes_command_and_timeout(self, mock_dispatch):
        mock_dispatch.return_value = {"job_id": "abc123"}

        server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "remote_host_exec_start",
                       "arguments": {"command": "echo hi", "timeout_s": 5}},
        })

        _assert_dispatched(self, mock_dispatch, "exec", command="echo hi", timeout_s=5)

    @patch("mcp_server.server.dispatch")
    def test_exec_start_passes_host_id_when_given(self, mock_dispatch):
        mock_dispatch.return_value = {"job_id": "abc123"}

        server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "remote_host_exec_start",
                       "arguments": {"command": "echo hi", "host_id": "rh_other"}},
        })

        _assert_dispatched(self, mock_dispatch, "exec", command="echo hi", host_id="rh_other")

    @patch("mcp_server.server.dispatch")
    def test_exec_status_passes_job_id(self, mock_dispatch):
        mock_dispatch.return_value = {"status": "running"}

        server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "remote_host_exec_status", "arguments": {"job_id": "abc123"}},
        })

        _assert_dispatched(self, mock_dispatch, "exec-status", job_id="abc123")

    @patch("mcp_server.server.dispatch")
    def test_list_hosts_calls_dispatch_with_hosts_command(self, mock_dispatch):
        mock_dispatch.return_value = {
            "count": 2,
            "hosts": [
                {"id": "a", "workspace_slug": "acme", "connected": True},
                {"id": "b", "workspace_slug": "acme-staging", "connected": False},
            ],
        }

        resp = server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "remote_host_list_hosts", "arguments": {}},
        })

        _assert_dispatched(self, mock_dispatch, "hosts")
        self.assertFalse(resp["result"]["isError"])
        self.assertEqual(json.loads(resp["result"]["content"][0]["text"])["count"], 2)

    @patch("mcp_server.server.dispatch")
    def test_not_configured_surfaces_as_mcp_error_content(self, mock_dispatch):
        mock_dispatch.side_effect = NotConfigured("missing env")

        resp = server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "remote_host_status", "arguments": {}},
        })

        self.assertTrue(resp["result"]["isError"])
        self.assertIn("missing env", resp["result"]["content"][0]["text"])

    @patch("mcp_server.server.dispatch")
    def test_remote_host_error_surfaces_as_mcp_error_content(self, mock_dispatch):
        mock_dispatch.side_effect = RemoteHostError("Not found")

        resp = server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "remote_host_status", "arguments": {}},
        })

        self.assertTrue(resp["result"]["isError"])
        self.assertIn("Not found", resp["result"]["content"][0]["text"])

    def test_unknown_tool_returns_error(self):
        resp = server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "not_a_real_tool", "arguments": {}},
        })
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("Unknown tool", resp["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
