import pytest
from opsresolve_ref.postback import sign, verify, PostbackError
KEY = b"test-key-not-secret"

def test_roundtrip():  # UT-PB-01
    v = sign(KEY, "SR-2026-000184", "BOOK_SLOT", "SLT-1", now=1000, nonce="n8f2k1")
    assert verify(KEY, v, now=1500)["optionId"] == "SLT-1"

def test_tampered_option_rejected():  # UT-PB-02 / TC-08
    v = sign(KEY, "SR-2026-000184", "BOOK_SLOT", "SLT-1", now=1000, nonce="a1")
    with pytest.raises(PostbackError, match="BAD_SIGNATURE"):
        verify(KEY, v.replace("SLT-1", "SLT-3"), now=1500)

def test_expired():  # UT-PB-03
    v = sign(KEY, "SR-1", "RECOVERY_ACCEPT", "ALT_TECH", ttl_s=1800, now=0, nonce="b2")
    with pytest.raises(PostbackError, match="EXPIRED"):
        verify(KEY, v, now=1801)

def test_replay_blocked():  # UT-PB-04 / TC-07
    v = sign(KEY, "SR-1", "BOOK_SLOT", "SLT-1", now=0, nonce="c3")
    used = set()
    verify(KEY, v, now=10, consumed=used)
    with pytest.raises(PostbackError, match="REPLAYED"):
        verify(KEY, v, now=11, consumed=used)

def test_wrong_key():  # UT-PB-05
    v = sign(KEY, "SR-1", "BOOK_SLOT", "SLT-1", now=0, nonce="d4")
    with pytest.raises(PostbackError, match="BAD_SIGNATURE"):
        verify(b"other", v, now=1)

def test_malformed():  # UT-PB-06
    with pytest.raises(PostbackError, match="MALFORMED"):
        verify(KEY, "Book 14:00", now=1)

def test_pipe_injection_rejected():  # UT-PB-07
    with pytest.raises(PostbackError):
        sign(KEY, "SR-1|x", "BOOK_SLOT", "SLT-1")
