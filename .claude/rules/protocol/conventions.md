---
paths:
  - "src/**"
---

# Conventions

- The fixed-width integers NetherNet defines, the LAN base header and the Response and Message length and recipient fields, are little-endian, read and written in host order on x86-64 with no byte swap (1.26.60).
- The host advertisement carries only varints, zigzag varints and single bytes, the fragment header is one byte, and signaling messages carry numbers as decimal text, so none has a byte order (1.26.60).
- The standard protocols keep their own byte order: STUN, DTLS and SCTP in network order, and JWK `x`, `y`, `n` and `e` big-endian.
- Offset tables list fields in wire order. `u8`, `u16`, `u32` and `u64` are little-endian unsigned integers of that width.
- A peer is a `NetworkID` (`network-id.md`). A session, one connection attempt between two peers, is keyed by the pair (remote network id, connection id), the connection id a `u64`.
- The dialer is the WebRTC offerer and the listener the answerer. BDS and the client each carry both roles, and a session takes its role from which side opens it: the opener dials, the side that receives its `CONNECTREQUEST` listens (1.26.60).
- Three planes make a connection: signaling (`signaling.md`, `http-signaling.md`), the WebRTC transport (`transport.md`, `framing.md`) and LAN discovery (`lan-discovery.md`, `lan-crypto.md`). `session.md` joins them.
- An implementation conforms when it produces and accepts the bytes and message orders the rules define.
