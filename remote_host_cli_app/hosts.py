"""Resolve a human-typed host reference to the host id the API wants.

`aw-workspace-cli remote-hosts shell 739ff3c351dcdd02` is unusable from memory.
People know their hosts by workspace slug ("bare-metal") or by hostname, both
of which `hosts` already reports — so accept any of the three and do the
lookup here, once, for every caller.

Kept out of ``client.py`` (which is transport: request building and auth) and
out of ``cli.py`` (which is argument parsing and dispatch), because ``shell``
and every ``--host``-taking verb need the identical resolution and must not
drift: one command whose subcommands disagree about what a host reference
means is precisely the trap ``--target`` was designed to avoid.
"""

from __future__ import annotations

import re

# What ``host_tokens.mint_host_credential`` produces: ``secrets.token_hex(8)``.
# Matching this lets an already-resolved id skip the lookup entirely, so the
# hot path (an agent passing --host from a previous `hosts` call, or this
# module's own output being fed back in) stays exactly one HTTP call.
_ID_RE = re.compile(r"^[0-9a-f]{16}$")


class HostNotFound(RuntimeError):
    """No host in the account matches the reference. The message lists what
    IS available — a bare "not found" leaves the caller with nothing to try."""


class AmbiguousHost(RuntimeError):
    """More than one host matches and no single one is obviously intended.
    Never guessed: picking silently would open a shell on a machine the
    caller did not name."""


def looks_like_id(ref: str) -> bool:
    return bool(_ID_RE.match(ref or ""))


def _describe(host: dict) -> str:
    state = "connected" if host.get("connected") else "offline"
    return (f"{host.get('id')}  {host.get('hostname') or '?'}"
            f"  (workspace {host.get('workspace_slug') or '?'}, {state})")


def resolve_host_ref(client, ref: str, *, hosts: list[dict] | None = None) -> str:
    """Turn ``ref`` — a host id, a workspace slug, or a hostname — into a
    host id.

    ``hosts`` lets a caller that already listed them avoid a second round
    trip; otherwise this fetches them.

    Matching order is id, then workspace slug, then hostname. Ids are exact
    and case-sensitive (they are generated hex); slug and hostname compare
    case-insensitively, because a hostname's case is whatever the machine
    happened to report and nobody types ``Mac.Home`` with the capitals.
    """
    ref = (ref or "").strip()
    if not ref:
        raise ValueError("resolve_host_ref needs a non-empty reference")
    if looks_like_id(ref):
        # Straight through: the backend 404s an id outside the account anyway,
        # and that is a better error than one this client invents.
        return ref

    if hosts is None:
        hosts = (client.list_account_hosts() or {}).get("hosts") or []

    needle = ref.casefold()
    matches = [h for h in hosts
               if (h.get("workspace_slug") or "").casefold() == needle
               or (h.get("hostname") or "").casefold() == needle]

    if not matches:
        known = "\n  ".join(_describe(h) for h in hosts) or "(none)"
        raise HostNotFound(
            f"no host matching {ref!r} in this account. Known hosts:\n  {known}"
        )

    if len(matches) > 1:
        # A workspace can have more than one host row — a re-link leaves the
        # previous one behind. When exactly one of them is actually dialed in,
        # that is unambiguously the one meant; anything else is a real choice
        # the caller has to make.
        connected = [h for h in matches if h.get("connected")]
        if len(connected) != 1:
            listed = "\n  ".join(_describe(h) for h in matches)
            raise AmbiguousHost(
                f"{ref!r} matches {len(matches)} hosts — name one by id:\n  {listed}"
            )
        matches = connected

    return matches[0]["id"]
