---
paths:
  - "src/nethernet/transport/__init__.py"
  - "src/nethernet/transport/peer_connection.py"
  - "src/nethernet/transport/stun_integrity.py"
  - "tests/test_peer_connection.py"
  - "tests/test_stun_integrity.py"
---

# Transport

- Peer connection, SDP and max message size: SPEC §10.1
- Connection flags: SPEC §10.2
- Data channels: SPEC §10.3
- Candidates: SPEC §10.4
- ICE states: SPEC §10.5
- Data-channel close: SPEC §10.6
- UDP ports and the STUN reflector: SPEC §14.2

## Data channels

- A fragment over 16 MiB would block the queue for good, which the fragment size cap (`framing.md`) rules out (1.26.60).
- For the reliable channel BDS sends a stream reset request for that channel's stream only, answers our reset, then sends ABORT and close_notify, all within about 1 ms, and reports the player gone 14 ms after the message (1.26.60).
- BDS treats a stream reset from the peer and an ABORT from the peer the same way, as a full close, and reports the player gone about 15 ms later. Both kinds of reset end the session. A peer that vanishes with no close is noticed when ICE connectivity fails, about 17 s later on BDS, and the session closes with `SessionError.ICE` (5). NetherNet has no idle timer (1.26.60).
- This library notices a vanished peer when aioice's consent checks fail (RFC 7675, about 30 s), and then closes with reason 5.

## aiortc mapping

- The dialer calls `pc.createDataChannel("ReliableDataChannel")` and `pc.createDataChannel("UnreliableDataChannel", ordered=False, maxRetransmits=0)`. The listener takes them from `@pc.on("datachannel")` and reads `channel.label`.
- An offer or answer is `createOffer` or `createAnswer`, then `setLocalDescription`, then `pc.localDescription.sdp`. aiortc takes no offer or answer options and its defaults match.
- aiortc is non-trickle: `setLocalDescription` returns with gathering complete and every candidate in the SDP.
- aiortc falls back to `stun:stun.l.google.com:19302` when `iceServers` is `None`. Pass `RTCConfiguration(iceServers=[])` to clear the servers.
- aiortc writes `a=max-message-size:65536`. Rewrite it to the values BDS advertises (SPEC §10.1).
- aiortc exposes no SCTP max message size. Parse the remote value as the grammar SPEC §10.1 gives, failing the remote description when it does not parse. Take the remote value from its SDP `a=max-message-size`, 65536 when the line is missing, then use the local advertised value when the remote is 0 and otherwise the signed smaller of the two. Fragments are that size minus 1, and a size of 1 or less closes with 34.
- `aiortc.sdp.candidate_from_sdp` and `candidate_to_sdp` take and return a candidate without the `candidate:` prefix. Strip it before parsing and add it back after serializing, and give a parsed candidate `sdpMLineIndex = 0`. aiortc matches an `sdpMid` of `""` to no m-line, so leave it `None`.
- aiortc's `addIceCandidate` needs the remote description set. Apply a queued `CANDIDATEADD` with it after both descriptions are set, as SPEC §10.4 says.
- aiortc gives a channel close no error status. Count a close as no error when it comes from a stream reset, either side's `RTCDataChannel.close()`, while `pc.sctp.state` stays `connected`. Count it as an error when the SCTP transport has closed, by ABORT or otherwise, or `pc.sctp.transport.state` is `failed`.
- aiortc's `iceConnectionState` has no `connected` or `disconnected`. Its `completed` stands for Connected.
- aiortc has no relay-only transport policy, so `ConnectionFlags.RELAY_ONLY` only strips candidates other than `typ relay` from the local SDP.
- aiortc negotiates the DTLS role from the SDP itself.
- A peer-connection close is `await pc.close()`.
- aioice before 0.13 fails the candidate pair on a 401 without integrity. `stun_integrity` patches it there; aioice 0.13 and later ignore it themselves.
