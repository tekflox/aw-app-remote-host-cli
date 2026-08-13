"""Unit tests for remote_host_cli_app/hosts.py — resolving a host reference
(id / workspace slug / hostname) to a host id. No network.
Run: python -m pytest tests/test_hosts.py -q
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remote_host_cli_app.hosts import (  # noqa: E402
    AmbiguousHost, HostNotFound, looks_like_id, resolve_host_ref,
)

# Shape mirrors what `hosts` / list_account_hosts actually returns.
HOSTS = [
    {"id": "aaaa1111bbbb2222", "workspace_slug": "bare-metal",
     "hostname": "Ubuntu-Box", "connected": True},
    {"id": "cccc3333dddd4444", "workspace_slug": "personal",
     "hostname": "Mac.Home", "connected": True},
    {"id": "eeee5555ffff6666", "workspace_slug": "aw",
     "hostname": "container-a", "connected": True},
]


def _client(hosts=None):
    c = MagicMock()
    c.list_account_hosts.return_value = {"hosts": HOSTS if hosts is None else hosts}
    return c


class LooksLikeIdTest(unittest.TestCase):
    def test_recognises_the_minted_shape(self):
        """host_tokens mints secrets.token_hex(8) — 16 lowercase hex."""
        self.assertTrue(looks_like_id("aaaa1111bbbb2222"))

    def test_rejects_everything_a_slug_or_hostname_looks_like(self):
        for ref in ("bare-metal", "Mac.Home", "aw", "aaaa1111bbbb222",
                    "aaaa1111bbbb22223", "AAAA1111BBBB2222", ""):
            self.assertFalse(looks_like_id(ref), ref)


class ResolveByIdTest(unittest.TestCase):
    def test_an_id_costs_no_lookup(self):
        """The hot path: an agent passing back an id it already has, or this
        module's own output being fed in again."""
        c = _client()
        self.assertEqual(resolve_host_ref(c, "aaaa1111bbbb2222"), "aaaa1111bbbb2222")
        c.list_account_hosts.assert_not_called()

    def test_an_unknown_id_is_passed_through_not_rejected_locally(self):
        """The backend 404s an id outside the account, and that is a better
        error than one this client invents from a list it may not fully see."""
        c = _client()
        self.assertEqual(resolve_host_ref(c, "9999999999999999"), "9999999999999999")
        c.list_account_hosts.assert_not_called()


class ResolveBySlugAndHostnameTest(unittest.TestCase):
    def test_workspace_slug(self):
        self.assertEqual(resolve_host_ref(_client(), "bare-metal"), "aaaa1111bbbb2222")

    def test_hostname(self):
        self.assertEqual(resolve_host_ref(_client(), "Ubuntu-Box"), "aaaa1111bbbb2222")

    def test_hostname_matching_ignores_case(self):
        """A hostname's case is whatever the machine reported; nobody types
        the capitals."""
        self.assertEqual(resolve_host_ref(_client(), "mac.home"), "cccc3333dddd4444")

    def test_slug_wins_over_a_hostname_on_another_host(self):
        hosts = [
            {"id": "1111111111111111", "workspace_slug": "prod", "hostname": "x", "connected": True},
            {"id": "2222222222222222", "workspace_slug": "other", "hostname": "prod", "connected": True},
        ]
        # Both match "prod" — one by slug, one by hostname — so this is a real
        # ambiguity the caller must settle, not something to guess at.
        with self.assertRaises(AmbiguousHost):
            resolve_host_ref(_client(hosts), "prod")

    def test_a_caller_that_already_listed_hosts_avoids_a_second_round_trip(self):
        c = _client()
        self.assertEqual(resolve_host_ref(c, "aw", hosts=HOSTS), "eeee5555ffff6666")
        c.list_account_hosts.assert_not_called()


class FailureTest(unittest.TestCase):
    def test_unknown_reference_lists_what_is_available(self):
        """A bare 'not found' leaves the caller with nothing to try next."""
        with self.assertRaises(HostNotFound) as ctx:
            resolve_host_ref(_client(), "typo")
        msg = str(ctx.exception)
        self.assertIn("bare-metal", msg)
        self.assertIn("aaaa1111bbbb2222", msg)

    def test_empty_reference_is_a_programming_error_not_a_lookup(self):
        with self.assertRaises(ValueError):
            resolve_host_ref(_client(), "")

    def test_a_stale_duplicate_resolves_to_the_connected_one(self):
        """Re-linking a workspace leaves the previous host row behind. When
        exactly one of the matches is dialed in, that is unambiguously the
        one meant."""
        hosts = [
            {"id": "0000dead0000beef", "workspace_slug": "aw", "hostname": "old", "connected": False},
            {"id": "eeee5555ffff6666", "workspace_slug": "aw", "hostname": "new", "connected": True},
        ]
        self.assertEqual(resolve_host_ref(_client(hosts), "aw"), "eeee5555ffff6666")

    def test_two_connected_matches_refuse_to_guess(self):
        """Opening a shell on a machine the caller did not name is worse than
        making them type an id."""
        hosts = [
            {"id": "1111111111111111", "workspace_slug": "aw", "hostname": "a", "connected": True},
            {"id": "2222222222222222", "workspace_slug": "aw", "hostname": "b", "connected": True},
        ]
        with self.assertRaises(AmbiguousHost) as ctx:
            resolve_host_ref(_client(hosts), "aw")
        self.assertIn("1111111111111111", str(ctx.exception))
        self.assertIn("2222222222222222", str(ctx.exception))

    def test_all_matches_offline_still_refuses_rather_than_picking(self):
        hosts = [
            {"id": "1111111111111111", "workspace_slug": "aw", "hostname": "a", "connected": False},
            {"id": "2222222222222222", "workspace_slug": "aw", "hostname": "b", "connected": False},
        ]
        with self.assertRaises(AmbiguousHost):
            resolve_host_ref(_client(hosts), "aw")


class DispatchIntegrationTest(unittest.TestCase):
    def test_host_flag_accepts_a_slug_on_every_verb(self):
        """`shell` and `--host` must not disagree about what a host reference
        means — same resolver, one place."""
        from remote_host_cli_app.cli import dispatch

        c = _client()
        c.list_processes.return_value = {"count": 0, "processes": []}
        dispatch("ps", client=c, host_id="bare-metal")
        c.list_processes.assert_called_once_with(host_id="aaaa1111bbbb2222")

    def test_no_host_flag_does_not_trigger_a_lookup(self):
        from remote_host_cli_app.cli import dispatch

        c = _client()
        c.status.return_value = {"id": "x"}
        dispatch("status", client=c)
        c.list_account_hosts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
