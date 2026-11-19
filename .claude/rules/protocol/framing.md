---
paths:
  - "src/nethernet/transport/framing.py"
  - "tests/test_framing.py"
  - "tests/test_conformance.py"
---

# Framing

- Fragment: SPEC §11.1
- Fragment size: SPEC §11.2
- Channels: SPEC §10.3
- Send: SPEC §11.3
- Receive on the reliable channel: SPEC §11.4
- Receive on the unreliable channel: SPEC §11.5
- Delivery: SPEC §11.6
- Payload: SPEC §11.7

## Send

- The send returns `true` once the fragments are queued, and `false` when it drops the packet or knows no such session. It never blocks and never waits for delivery (1.26.60).

## Receive

- A read shorter than the head packet returns its prefix and leaves the rest as the head packet (1.26.60).
- A packet-available check reports the size of the whole head packet (1.26.60).
