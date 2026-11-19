---
paths:
  - "src/nethernet/discovery/crypto.py"
  - "tests/test_crypto_envelope.py"
  - "tests/test_conformance.py"
---

# LAN crypto

- Key: SPEC §8.1
- Envelope: SPEC §8.2
- Seal and open: SPEC §8.3

## Open

- This library compares the MACs with `hmac.compare_digest`.
