---
paths:
  - "src/nethernet/transport/__init__.py"
  - "src/nethernet/transport/peer_connection.py"
  - "src/nethernet/transport/stun_integrity.py"
  - "tests/test_peer_connection.py"
  - "tests/test_stun_integrity.py"
---

# Transport

## Peer connection

- After signaling, the peers run standard WebRTC: ICE, DTLS with the role from the SDP `setup` attribute, and SCTP carrying the data channels (1.26.60). BDS's answer takes `a=setup:active` (1.26.60).
- ICE servers come as a relay configuration of `uri`, `username` and `password`, at most 16 servers (1.26.60). A dedicated server sets none (`config.md`).
- A no-trickle peer clears every ICE server before it creates the peer connection, so it gathers host candidates only (1.26.60).
- Offers and answers use libwebrtc's default `RTCOfferAnswerOptions`, with ICE restart off (1.26.60).
- libwebrtc writes and parses the SDP unedited. The only addition is the optional `a=identity` line (`identity.md`) (1.26.60).
- BDS's offer advertises `a=max-message-size:262144`. Its answer advertises the smaller of the offer's value and 262144, or 262144 when the offer's value is 0 or less (1.26.60).
- The SDP omits `a=max-message-size` when the value is 65536, and a missing line means 65536 (1.26.60).
- Ignore a STUN response that fails message integrity and keep waiting, as libwebrtc does.
- Closing a session closes its peer connection (1.26.60).

## Negotiation errors

| Step that fails | `SessionError` |
| --- | --- |
| create the peer connection or a data channel | `FAILED_TO_CREATE_PEER_CONNECTION` (4) |
| create the offer | `FAILED_TO_CREATE_OFFER` (10) |
| create the answer | `FAILED_TO_CREATE_ANSWER` (11) |
| set or serialize the local description | `FAILED_TO_SET_LOCAL_DESCRIPTION` (12) |
| set the remote description | `FAILED_TO_SET_REMOTE_DESCRIPTION` (13) |
| create the identity assertion | `FAILED_TO_CREATE_IDENTITY_ASSERTION` (36) |

- Each row closes the session with its error (1.26.60).

## Data channels

- The dialer creates `ReliableDataChannel`, then `UnreliableDataChannel` (`framing.md`), before it creates the offer. The listener creates none and takes them from the on-data-channel event (1.26.60).
- A fragment sent before its channel opens waits and goes out in order once it opens (1.26.60).
- The channel's own send queue waits, never the caller. A fragment that would push the channel's buffered amount past 16 MiB, libwebrtc's `MaxSendQueueSize()`, stays queued, with every later fragment, until the buffered amount drops. A full queue drops nothing (1.26.60).
- A fragment over 16 MiB would block the queue for good, which the fragment size cap (`framing.md`) rules out (1.26.60).
- A channel close ends the session with a reason from the channel's error status, whoever closed it (1.26.60).
- No error, as after a stream reset from either side or BDS's own close of a malformed message, gives `SessionError.NONE` (0) (1.26.60).
- An error, as on an SCTP ABORT or a transport failure, gives `SessionError.DATA_CHANNEL_CLOSED` (32) (1.26.60).
- BDS closes a session, whether for a game-layer disconnect or a server stop, by sending an SCTP ABORT and then a DTLS close_notify in a separate datagram. It sends no SHUTDOWN, no stream reset and no signaling message first (1.26.60).
- BDS sends a stream reset only as the answer to ours: it answers with a success response and resets its own outgoing streams for each reset one, then sends ABORT and close_notify within a millisecond (1.26.60).
- BDS treats a stream reset from the peer and an ABORT from the peer the same way, as a full close, and reports the player gone about 15 ms later. A peer that vanishes with no close is reported gone about 17 s later (1.26.60).

## Candidates

- A remote candidate gets sdp-mid `""` and mline index 0 (1.26.60).
- Apply a remote candidate at once only when both the current local and the current remote description are set. Otherwise queue it (1.26.60).
- The dialer applies its queue once the answer is set as the remote description (1.26.60).
- The listener applies its queue once the answer is set as the local description, before it sends `CONNECTRESPONSE` (1.26.60).
- The result of applying a remote candidate is ignored (1.26.60).

## ICE states

| ICE connection state | Outcome | Session state |
| --- | --- | --- |
| New, Checking | none | connecting |
| Connected | update the fragment size (`framing.md`), then open the session | active |
| Completed, Disconnected | none | active |
| Failed | close with `SessionError.ICE` (5) | error |
| Closed | none | error |

- The states are libwebrtc's `IceConnectionState`. BDS reacts to Connected and Failed only (1.26.60).
- Disconnected never closes the session (1.26.60).

## UDP ports

- Every WebRTC socket binds in one port range, built as `config.md` "WebRTC UDP ports" says (1.26.60).
- A new socket takes the first free port counting up from the port after the last one bound, skips every port a live socket holds, and wraps once to the range start (1.26.60).
- With no free port it shares the live socket that has the fewest users. It fails only when no live socket is left (1.26.60).
- With no minimum port the OS picks the port. A minimum above the maximum binds nothing. A minimum with no maximum leaves the range open above (1.26.60).
- Sessions that share a socket share its port, so one session's connectivity checks reach another session, which answers 401 without `MESSAGE-INTEGRITY` (1.26.60).

## STUN reflector

- Each port mapping in `config.md` "WebRTC UDP ports" adds a reflector entry: a source port range, an offset and a reply IP (1.26.60).
- An entry maps an address whose port is in its range to the reply IP, or to the source IP when the reply IP is any, and the source port plus the offset (1.26.60).
- It gives no mapping when the port is outside its range or the mapped port would exceed 65535 (1.26.60).
- It answers only when its filter IP is any or equals the source IP. BDS always leaves the filter at any (1.26.60).
- No port answers a bare STUN Binding request: unbound ports give ICMP unreachable and the live data socket stays silent. A mapping shows only as the `srflx` candidate in BDS's answer, `typ srflx <reply IP>:A raddr <interface IP> rport B`, with no host candidate. Only B is bound (1.26.60).
- With `server-port-mappings` next to the shipped `server-udp-ports=19132`, the answer carries host 19132 only and the mapping has no visible effect (1.26.60).
- A dialer still connects when the advertised address is unbound or unroutable, since BDS's checks teach it the peer-reflexive address (1.26.60).

## aiortc mapping

- The dialer calls `pc.createDataChannel("ReliableDataChannel")` and `pc.createDataChannel("UnreliableDataChannel", ordered=False, maxRetransmits=0)`. The listener takes them from `@pc.on("datachannel")` and reads `channel.label`.
- An offer or answer is `createOffer` or `createAnswer`, then `setLocalDescription`, then `pc.localDescription.sdp`. aiortc takes no offer or answer options and its defaults match.
- aiortc is non-trickle: `setLocalDescription` returns with gathering complete and every candidate in the SDP.
- aiortc falls back to `stun:stun.l.google.com:19302` when `iceServers` is `None`. Pass `RTCConfiguration(iceServers=[])` to clear the servers.
- aiortc writes `a=max-message-size:65536`. Rewrite it to 262144 in an offer, and to the smaller of the offer's value and 262144 in an answer, as BDS does.
- aiortc exposes no SCTP max message size. Take it from the remote SDP's `a=max-message-size`, 65536 when the line is missing.
- `aiortc.sdp.candidate_from_sdp` and `candidate_to_sdp` take and return a candidate without the `candidate:` prefix. Strip it before parsing and add it back after serializing, and give a parsed candidate `sdpMLineIndex = 0`. aiortc matches an `sdpMid` of `""` to no m-line, so leave it `None`.
- aiortc's `addIceCandidate` needs the remote description set. Apply a queued `CANDIDATEADD` (`signaling.md`) with it after both descriptions are set.
- aiortc gives a channel close no error status. Count a close as no error when it comes from a stream reset, either side's `RTCDataChannel.close()`, while `pc.sctp.state` stays `connected`. Count it as an error when the SCTP transport has closed, by ABORT or otherwise, or `pc.sctp.transport.state` is `failed`.
- aiortc's `iceConnectionState` has no `connected` or `disconnected`. Its `completed` stands for Connected.
- aiortc has no relay-only transport policy, so `ConnectionFlags.RELAY_ONLY` only strips candidates other than `typ relay` from the local SDP.
- aiortc negotiates the DTLS role from the SDP itself.
- A peer-connection close is `await pc.close()`.
- aioice before 0.13 fails the candidate pair on a 401 without integrity. `stun_integrity` patches it there; aioice 0.13 and later ignore it themselves.
