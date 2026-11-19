---
paths:
  - "src/nethernet/identity.py"
  - "tests/test_identity.py"
---

# Identity assertions

- Canonical JSON: SPEC §9.1
- Envelope: SPEC §9.2
- Fingerprints: SPEC §9.3
- Algorithms and keys: SPEC §9.4
- Base64url decoding: SPEC §9.5
- Player assertion: SPEC §9.6
- Operator assertion: SPEC §9.7
- Verification: SPEC §9.8

## JSON

- This library raises `TypeError` for a NaN or infinite number. Identity tokens never hold one.

## Operator assertion

- The client keeps at most 100 pins and drops the least recently pinned first. It saves them as `{"keys":[<JWK>,...]}` (1.26.60).
- This library's pin store is the application's `on_server_identity` callback (`api.md`).

## Verification

- This library deliberately deviates: it rejects an RSA exponent that cryptography refuses, below 3 or even.
- This library strips the `a=identity` line before aiortc.
