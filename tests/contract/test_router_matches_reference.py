"""Contract test: the production Channel Router matches the reference oracle.

The reference implementation (``opsresolve_ref.router``) is the authoritative spec for
channel selection (Section 7.6). The production router (``opsresolve.channels.router``)
must return identical results for the same inputs.
"""
from __future__ import annotations

import itertools

from opsresolve.channels import router as prod
from opsresolve_ref import router as ref

INTENTS = list(prod.CAPABLE.keys())
CHANNELS = ["RCS", "WHATSAPP", "SMS", "EMAIL"]


def _consent(*opted_in: str) -> dict:
    return {c: ("OPTED_IN" if c in opted_in else "OPTED_OUT") for c in CHANNELS}


def test_router_parity_across_common_cases() -> None:
    consents = [
        _consent(*combo)
        for r in range(len(CHANNELS) + 1)
        for combo in itertools.combinations(CHANNELS, r)
    ]
    for intent in INTENTS:
        for consent in consents:
            for preferred in CHANNELS:
                for priority in ("P1", "P3"):
                    args = dict(preferred=preferred, priority=priority)
                    assert prod.select_channel(intent, consent, **args) == ref.select_channel(
                        intent, consent, **args
                    )
