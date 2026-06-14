"""LAN discovery crypto envelope — SPEC.md s8.

Each discovery datagram is sealed as ``HMAC-SHA256(key, plaintext) || AES-256-ECB(key,
PKCS7(plaintext))``, where ``key = SHA256(app_id as 8 little-endian bytes)`` is used as both
the AES-256 key and the HMAC key. ECB is part of the scheme (a known weakness, retained for
interoperability per SPEC.md s8.2).

cryptography API verified against the official reference:
https://cryptography.io/en/latest/hazmat/primitives/symmetric-encryption/
https://cryptography.io/en/latest/hazmat/primitives/padding/
"""

from __future__ import annotations

import hashlib
import hmac

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

MAC_SIZE = 32  # HMAC-SHA256 output, prepended to the ciphertext
_BLOCK_BITS = 128  # AES block size for PKCS7 padding


def derive_key(app_id: int) -> bytes:
    """key = SHA256(app_id as 8 little-endian bytes) — SPEC.md s8.1 (32 bytes)."""
    return hashlib.sha256(app_id.to_bytes(8, "little")).digest()


class Envelope:
    """Seals/opens discovery datagrams under the key derived from an Application Id."""

    def __init__(self, app_id: int) -> None:
        self.key = derive_key(app_id)

    def seal(self, plaintext: bytes) -> bytes:
        """Return ``MAC || AES-256-ECB(PKCS7(plaintext))`` — SPEC.md s8.3."""
        mac = hmac.new(self.key, plaintext, hashlib.sha256).digest()
        padder = PKCS7(_BLOCK_BITS).padder()
        padded = padder.update(plaintext) + padder.finalize()
        encryptor = Cipher(algorithms.AES256(self.key), modes.ECB()).encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        return mac + ciphertext

    def open(self, envelope: bytes) -> bytes | None:
        """Verify + decrypt; return the plaintext or None if rejected — SPEC.md s8.4."""
        if len(envelope) < MAC_SIZE:
            return None
        received_mac, ciphertext = envelope[:MAC_SIZE], envelope[MAC_SIZE:]
        try:
            decryptor = Cipher(algorithms.AES256(self.key), modes.ECB()).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = PKCS7(_BLOCK_BITS).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
        except ValueError:
            # Ciphertext not a whole number of blocks, or invalid PKCS7 padding.
            return None
        computed_mac = hmac.new(self.key, plaintext, hashlib.sha256).digest()
        if not hmac.compare_digest(received_mac, computed_mac):
            return None
        return plaintext
