# Live interop results (Task 15 — SPEC.md s11.3)

Status: **PENDING — needs an external reference peer.** This task can't be completed
autonomously; it requires a live NetherNet peer on the LAN (real Minecraft Bedrock, or a custom
C++ driver linked against the `../NetherNet` static library). See "How to run" below.

## What is already verified (autonomous)

- **Python ↔ Python over real UDP** — `tests/test_transport_api.py` completes a full session
  (LAN discovery binding → signaling → WebRTC ICE/DTLS/SCTP → framed app traffic, both
  reliabilities incl. a 262144-byte multi-fragment packet).
- **Byte-exact conformance (s11.1/.2/.4)** — `tests/test_conformance.py`: signaling strings,
  discovery Request/Response/Message golden hex, fragmentation fixtures.
- **Envelope cross-check (s8)** — `seal()` reproduces an independent **OpenSSL** vector
  byte-for-byte, so a sealed datagram decrypts/verifies under another conformant implementation.

## What still needs a live run (s11.3)

1. **LAN discovery exchange** (Request → Response) with a reference peer.
2. **Full P2P session**: offer/answer/candidates → open data channels → application traffic.

## Why it's blocked

- The `../NetherNet` C++ project builds a **static library** (`build/Release/NetherNet.lib`),
  not a runnable peer — there is no standalone reference executable to talk to.
- The authoritative reference is **Minecraft Bedrock** itself, which must be run on the LAN.

## How to run (manual)

Both peers must share the **Application Id** (default `0xDEADBEEF`) and **port** (default
`7551`), and be on the same LAN segment (UDP broadcast).

- **Python advertises, reference joins:** `uv run python examples/host.py`, then have the
  reference peer discover/join the LAN game. Expect a discovery Response from us, then a
  CONNECTREQUEST we answer.
- **Python joins a reference host:** start the reference host, then
  `uv run python examples/join.py` — expect `discover()` to list the host, then a successful
  `connect()` and echo.

Capture the exchange (e.g. Wireshark on UDP `7551`) and record below any deltas to fix
(SDP quirks, candidate formatting, DTLS/ICE timing, max-message-size).

## Observations

_(to be filled in during a live run)_

| Date | Reference peer | Discovery | Session | Notes / fixes |
|------|----------------|-----------|---------|---------------|
|      |                |           |         |               |
