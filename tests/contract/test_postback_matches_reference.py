"""Contract test: production postback signer/verifier matches the reference oracle (7.2.3)."""
from __future__ import annotations

from opsresolve.inbound import postback as prod
from opsresolve_ref import postback as ref

KEY = b"demo-hmac-key-rotate-before-use"


def test_sign_verify_roundtrip_parity() -> None:
    signed = prod.sign(KEY, "SR-2026-000184", "BOOK_SLOT", "SLT-1", now=1_792_497_600, nonce="n8f2k1")
    ref_signed = ref.sign(KEY, "SR-2026-000184", "BOOK_SLOT", "SLT-1", now=1_792_497_600, nonce="n8f2k1")
    assert signed == ref_signed
    assert prod.verify(KEY, signed, now=1_792_497_660) == ref.verify(KEY, ref_signed, now=1_792_497_660)
