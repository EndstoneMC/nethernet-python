---
paths:
  - "src/**"
---

# Conventions

- Every integer on the wire is little-endian, except the ASCII tokens of a signaling message and opaque application payloads (unverified).
- Offset tables list fields in wire order. `u8`, `u16`, `u32` and `u64` are little-endian unsigned integers of that width.
- A peer is a `NetworkID` (`network-id.md`). A session, one connection attempt between two peers, is a `u64` connection id.
- The dialer is the WebRTC offerer and the listener the answerer. A peer can take either role per session (unverified).
- Three planes make a connection: signaling (`signaling.md`, `http-signaling.md`), the WebRTC transport (`transport.md`, `framing.md`) and LAN discovery (`lan-discovery.md`, `lan-crypto.md`). `session.md` joins them.
- An implementation conforms when it produces and accepts the bytes and message orders the rules define.
