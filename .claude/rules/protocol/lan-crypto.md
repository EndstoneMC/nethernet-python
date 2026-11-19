---
paths:
  - "src/nethernet/discovery/crypto.py"
  - "tests/test_crypto_envelope.py"
  - "tests/test_conformance.py"
---

# LAN crypto

Every LAN discovery datagram is an encrypted, authenticated envelope (1.26.60).

## Key

- `key = SHA256(application id as 8 little-endian bytes)`, 32 bytes (1.26.60). The application id is a `u64` with its default in `config.md`.
- The same `key` is the AES-256 key and the HMAC-SHA256 key (1.26.60).
- Peers with different application ids cannot open each other's datagrams, so they never see each other (1.26.60).

## Envelope

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 32 | `HMAC-SHA256(key, plaintext)` |
| 32 | ... | `AES-256-ECB(key, PKCS7(plaintext))` |

- The plaintext is the whole discovery packet, header and payload, and the envelope is the whole UDP datagram body (1.26.60).
- The MAC is over the plaintext, not the ciphertext (1.26.60).
- There is no IV, nonce, length prefix or version byte (1.26.60).

## Seal

Seal every packet sent, the first-discovery probe included (1.26.60):

1. Build the packet plaintext `P` (`lan-discovery.md`).
2. `MAC = HMAC-SHA256(key, P)`.
3. `C = AES-256-ECB-encrypt(key, PKCS7(P))`.
4. Send `MAC || C`.

## Open

Open every datagram received (1.26.60):

1. Drop a datagram under 32 bytes.
2. Split it into the received MAC, its first 32 bytes, and `C`, the rest.
3. `P = unpad(AES-256-ECB-decrypt(key, C))`. Drop it when `C` is not a whole number of blocks or decryption or unpadding fails.
4. Compute `HMAC-SHA256(key, P)` and compare it with the received MAC in constant time, with `hmac.compare_digest`. Drop it when they differ.
5. Parse `P` as a discovery packet.

- Drop a rejected datagram silently (1.26.60).
