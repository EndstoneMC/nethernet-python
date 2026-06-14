"""LAN discovery crypto envelope — SPEC.md s8.

The golden vector below was produced independently with the OpenSSL 3.2.3 CLI (a different
implementation) for app_id 0xDEADBEEF and plaintext b"NetherNet":

    key = SHA256(LE u64 app_id)
    mac = HMAC-SHA256(key, plaintext)
    ct  = AES-256-ECB(key, PKCS7(plaintext))
    envelope = mac || ct

Because ECB + HMAC are deterministic, seal() must reproduce these exact bytes (SPEC.md s11.1).
"""

import hashlib
import hmac

from nethernet.discovery.crypto import MAC_SIZE, Envelope, derive_key

APP_ID = 0xDEADBEEF
GOLD_KEY = bytes.fromhex("eed4c37861936c6ebb91ab9d195c1b489de46362d3bb8e30d8aab08bf217e6f3")
GOLD_ENVELOPE = bytes.fromhex(
    "365cc9aeaff3358d65f08e11c57a060efb1d1cda080840ad6a4a41e27d3d7990"  # mac
    "a37c27b89c42919b52d8a12cc996a384"  # ct
)


# --- Independent (OpenSSL) vector ---


def test_derive_key_matches_openssl_vector():
    assert derive_key(APP_ID) == GOLD_KEY


def test_seal_matches_openssl_vector():
    assert Envelope(APP_ID).seal(b"NetherNet") == GOLD_ENVELOPE


# --- Round-trip ---


def test_seal_open_roundtrip_across_sizes():
    env = Envelope(APP_ID)
    for plaintext in [b"", b"x", b"a" * 16, b"hello world", bytes(range(256))]:
        assert env.open(env.seal(plaintext)) == plaintext


def test_mac_is_computed_over_plaintext():
    # Recompute the MAC independently with stdlib and match the envelope's 32-byte prefix.
    pt = b"verify mac placement"
    sealed = Envelope(APP_ID).seal(pt)
    assert sealed[:MAC_SIZE] == hmac.new(GOLD_KEY, pt, hashlib.sha256).digest()


# --- Rejection (SPEC.md s8.4) ---


def test_open_rejects_short_datagram():
    assert Envelope(APP_ID).open(b"\x00" * 31) is None


def test_open_rejects_tampered_mac():
    env = Envelope(APP_ID)
    sealed = bytearray(env.seal(b"payload"))
    sealed[0] ^= 0xFF
    assert env.open(bytes(sealed)) is None


def test_open_rejects_tampered_ciphertext():
    env = Envelope(APP_ID)
    sealed = bytearray(env.seal(b"payload"))
    sealed[-1] ^= 0xFF
    assert env.open(bytes(sealed)) is None


def test_open_rejects_non_block_multiple_ciphertext():
    env = Envelope(APP_ID)
    sealed = env.seal(b"payload")  # MAC(32) + one 16-byte block
    assert env.open(sealed[:-1]) is None  # 15-byte ciphertext is not a whole AES block


def test_open_rejects_wrong_app_id():
    sealed = Envelope(APP_ID).seal(b"secret")
    assert Envelope(0x12345678).open(sealed) is None
