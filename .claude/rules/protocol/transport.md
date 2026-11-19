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
- The `a=max-message-size` line ends at `\n` with one trailing `\r` stripped. Its value is the first field after the colon, and any further colon-separated fields are ignored, so `a=max-message-size:5:9` reads 5 (1.26.60).
- The value must start with an ASCII digit or `-`, then hold only ASCII decimal digits to the end of the field, with no `+`, space, tab, extra `\r`, `0x` prefix, non-ASCII digit or 0x00. A bare `-` fails. Leading zeros are decimal, so `007` is 7. A field that fails this, or lies outside -2147483648 to 2147483647, fails the whole SDP with an invalid max message size error (1.26.60).
- 0 and negative values in range parse (1.26.60).
- Once both descriptions are set, the SCTP max message size is the local value when the remote value is 0, otherwise the smaller of the two, compared signed. A peer value of 0 therefore means any size, and a negative peer value is taken as is (1.26.60).
- As answerer BDS fragments at the smaller of 262144 and the peer's value minus 1, at 65535 when the peer sends no line, and at 262143 when the peer sends 0 (1.26.60).
- Ignore a STUN response that fails message integrity and keep waiting, as libwebrtc does.
- Closing a session closes its peer connection (1.26.60).

## Negotiation errors

- A failing negotiation step closes the session with its own code (`errors.md`) (1.26.60).

## Data channels

- The dialer creates `ReliableDataChannel`, then `UnreliableDataChannel` (`framing.md`), before it creates the offer. The listener creates none and takes them from the on-data-channel event (1.26.60).
- A fragment sent before its channel opens waits and goes out in order once it opens (1.26.60).
- The channel's own send queue waits, never the caller. A fragment that would push the channel's buffered amount past 16 MiB, libwebrtc's `MaxSendQueueSize()`, stays queued, with every later fragment, until the buffered amount drops. A full queue drops nothing (1.26.60).
- A fragment over 16 MiB would block the queue for good, which the fragment size cap (`framing.md`) rules out (1.26.60).
- A channel close ends the session with a reason from the channel's error status, whoever closed it (1.26.60).
- No error, as when the peer resets the channel's stream or BDS closes the channel itself after a malformed message, reports `None` (code 0) (1.26.60).
- An error, as on an SCTP ABORT from the peer or a transport failure, gives `SessionError.DATA_CHANNEL_CLOSED` (32) (1.26.60).
- A session reports one close reason, the first. A later channel close or ABORT on the same session reports nothing (1.26.60).
- When the application closes a session, as on a kick, the closing side reports `None` (code 0) at once, then closes the peer connection. That close sends the ABORT and close_notify below and reports nothing more (1.26.60). A server stop sends that ABORT on every session within 0.16 s (1.26.60).
- After a malformed message BDS closes the channel the message came on: a 0- or 1-byte message or a broken fragment countdown on the reliable channel, or a 0- or 1-byte message on the unreliable channel (`framing.md`). It starts a stream reset by itself only in these cases (1.26.60).
- Closing either channel ends the whole session, with reason 0 because the self-started reset carries no error (1.26.60).
- For the reliable channel BDS sends a stream reset request for that channel's stream only, answers our reset, then sends ABORT and close_notify, all within about 1 ms, and reports the player gone 14 ms after the message (1.26.60).
- BDS closes a session, whether for a game-layer disconnect or a server stop, by sending an SCTP ABORT and then a DTLS close_notify in a separate datagram. It sends no SHUTDOWN, no stream reset and no signaling message first (1.26.60).
- Otherwise BDS sends a stream reset only as the answer to ours: it answers with a success response and resets its own outgoing streams for each reset one, then sends ABORT and close_notify within a millisecond (1.26.60).
- BDS treats a stream reset from the peer and an ABORT from the peer the same way, as a full close, and reports the player gone about 15 ms later. Both kinds of reset end the session. A peer that vanishes with no close is noticed when ICE connectivity fails, about 17 s later on BDS, and the session closes with `SessionError.ICE` (5). NetherNet has no idle timer (1.26.60).
- This library notices a vanished peer when aioice's consent checks fail (RFC 7675, about 30 s), and then closes with reason 5.

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
- No port answers a bare STUN Binding request: unbound ports give ICMP unreachable and the live data socket stays silent. What a mapping shows in BDS's answer is in `config.md` "WebRTC UDP ports".
- A dialer still connects when the advertised address is unbound or unroutable, since BDS's checks teach it the peer-reflexive address (1.26.60).

## aiortc mapping

- The dialer calls `pc.createDataChannel("ReliableDataChannel")` and `pc.createDataChannel("UnreliableDataChannel", ordered=False, maxRetransmits=0)`. The listener takes them from `@pc.on("datachannel")` and reads `channel.label`.
- An offer or answer is `createOffer` or `createAnswer`, then `setLocalDescription`, then `pc.localDescription.sdp`. aiortc takes no offer or answer options and its defaults match.
- aiortc is non-trickle: `setLocalDescription` returns with gathering complete and every candidate in the SDP.
- aiortc falls back to `stun:stun.l.google.com:19302` when `iceServers` is `None`. Pass `RTCConfiguration(iceServers=[])` to clear the servers.
- aiortc writes `a=max-message-size:65536`. Rewrite it to the values BDS advertises (Peer connection).
- aiortc exposes no SCTP max message size. Parse the remote value as the grammar under Peer connection says, failing the remote description when it does not parse. Take the remote value from its SDP `a=max-message-size`, 65536 when the line is missing, then use the local advertised value when the remote is 0 and otherwise the signed smaller of the two. Fragments are that size minus 1, and a size of 1 or less closes with 34.
- `aiortc.sdp.candidate_from_sdp` and `candidate_to_sdp` take and return a candidate without the `candidate:` prefix. Strip it before parsing and add it back after serializing, and give a parsed candidate `sdpMLineIndex = 0`. aiortc matches an `sdpMid` of `""` to no m-line, so leave it `None`.
- aiortc's `addIceCandidate` needs the remote description set. Apply a queued `CANDIDATEADD` (`signaling.md`) with it after both descriptions are set.
- aiortc gives a channel close no error status. Count a close as no error when it comes from a stream reset, either side's `RTCDataChannel.close()`, while `pc.sctp.state` stays `connected`. Count it as an error when the SCTP transport has closed, by ABORT or otherwise, or `pc.sctp.transport.state` is `failed`.
- aiortc's `iceConnectionState` has no `connected` or `disconnected`. Its `completed` stands for Connected.
- aiortc has no relay-only transport policy, so `ConnectionFlags.RELAY_ONLY` only strips candidates other than `typ relay` from the local SDP.
- aiortc negotiates the DTLS role from the SDP itself.
- A peer-connection close is `await pc.close()`.
- aioice before 0.13 fails the candidate pair on a 401 without integrity. `stun_integrity` patches it there; aioice 0.13 and later ignore it themselves.
