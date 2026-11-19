from __future__ import annotations

import hashlib
import hmac

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

MAC_SIZE = 32
_BLOCK_BITS = 128


def derive_key(app_id: int) -> bytes:
    return hashlib.sha256(app_id.to_bytes(8, "little")).digest()


class Envelope:
    def __init__(self, app_id: int) -> None:
        self.key = derive_key(app_id)

    def seal(self, plaintext: bytes) -> bytes:
        mac = hmac.new(self.key, plaintext, hashlib.sha256).digest()
        padder = PKCS7(_BLOCK_BITS).padder()
        padded = padder.update(plaintext) + padder.finalize()
        encryptor = Cipher(algorithms.AES256(self.key), modes.ECB()).encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        return mac + ciphertext

    def open(self, envelope: bytes) -> bytes | None:
        if len(envelope) < MAC_SIZE:
            return None
        received_mac, ciphertext = envelope[:MAC_SIZE], envelope[MAC_SIZE:]
        try:
            decryptor = Cipher(algorithms.AES256(self.key), modes.ECB()).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = PKCS7(_BLOCK_BITS).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
        except ValueError:
            return None
        computed_mac = hmac.new(self.key, plaintext, hashlib.sha256).digest()
        if not hmac.compare_digest(received_mac, computed_mac):
            return None
        return plaintext
