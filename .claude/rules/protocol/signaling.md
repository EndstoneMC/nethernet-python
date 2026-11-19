---
paths:
  - "src/nethernet/signaling/__init__.py"
  - "src/nethernet/signaling/messages.py"
  - "tests/test_signaling_messages.py"
  - "tests/test_conformance.py"
---

# Signaling

## Channel

- A signaling channel sends a message string from a local `NetworkID` to a remote one, and surfaces each inbound `(sender, message)` pair.
- The LAN channel carries the whole string as the data of a LAN discovery Message packet (`lan-discovery.md`) (1.26.60).
- The HTTP channel carries only the SDP and rebuilds the string at each end (`http-signaling.md`) (1.26.60).
- An inbound message from a non-LAN channel is not filtered by its addressee (1.26.60).

## Message format

- A message is `IDENTIFIER connectionId data`: the identifier, the connection id, and `data`, joined by single ASCII spaces, with no trailing newline (1.26.60).
- `connectionId` is unsigned decimal with no sign or padding (1.26.60).
- `data` is the SDP or candidate text verbatim, with no escaping, or for `CONNECTERROR` the error as signed decimal (1.26.60).

## Parsing

A message parses in these steps, and any failure drops it (1.26.60):

1. Find the first space, then the next space after it. Either missing fails, so a message needs three tokens.
2. The identifier is the text before the first space. It must equal one of the four identifiers exactly, case included.
3. The connection id token is the text between the two spaces. It must be non-empty.
4. Read the connection id as base-10 digits up to the first non-digit. No digit read, or a value above the `u64` range, fails. Trailing characters are ignored, so `CONNECTREQUEST 123abc ...` carries id `123`. A leading sign or space fails.
5. `data` is everything after the second space, spaces and newlines included, since SDP has both. It may be empty.
6. For `CONNECTERROR`, read `data` as an optional `-` and decimal digits up to the first non-digit, into a signed 32-bit value. Trailing characters are ignored. No digit read, a leading `+` or space, or a value outside the signed 32-bit range fails.

## Messages

| Identifier | Direction | `data` |
| --- | --- | --- |
| `CONNECTREQUEST` | dialer to listener | the SDP offer |
| `CONNECTRESPONSE` | listener to dialer | the SDP answer |
| `CANDIDATEADD` | both | one ICE candidate |
| `CONNECTERROR` | either | a `SessionError` value in decimal |

- The table holds every identifier (1.26.60).
- The receiver of `CONNECTREQUEST` sets `data` as the remote offer, of `CONNECTRESPONSE` as the remote answer (1.26.60).
- `CANDIDATEADD` carries one candidate in WebRTC's standard form with its prefix: `candidate:<foundation> <component> <protocol> <priority> <ip> <port> typ <type> ...`. It is the SDP candidate attribute without its `a=` prefix and line end, extensions such as `generation`, `ufrag`, `network-id` and `network-cost` included. It is no NetherNet format, so a standard WebRTC peer parses it (1.26.60).
- `transport.md` says how the receiver applies a `CANDIDATEADD` candidate and when it queues one.

## Receiving

A parsed message goes to the session of its sender `NetworkID` and connection id (`network-id.md`). Both sides behave alike except where a side is named (1.26.60).

- A message that fails to parse is dropped silently. Nothing is sent back and no `SessionError` is reported (1.26.60).
- `CONNECTREQUEST` first goes to the listener's application, with the identity assertion from the SDP if any (`identity.md`). With no application hook the offer is accepted (1.26.60).
- An accepted `CONNECTREQUEST` with a new connection id opens an incoming session. One that matches an existing incoming session is ignored, and the session carries on. One that matches an outgoing session of the same remote is dropped silently (1.26.60).
- A declined `CONNECTREQUEST` is answered with `CONNECTERROR` carrying the same connection id and `SessionError.IDENTITY_NOT_ALLOWED` (37) (1.26.60).
- `CONNECTRESPONSE` first goes to the dialer's application, with the identity assertion from the SDP. Accepted, the answer is applied. Declined, the dialer closes the session with `SessionError.IDENTITY_NOT_ALLOWED` (37) (1.26.60).
- The listener ignores `CONNECTRESPONSE` and `CONNECTERROR` for its incoming sessions (1.26.60).
- `CONNECTERROR` closes the dialer's outgoing session with the error it carries (1.26.60).
- `CANDIDATEADD` adds the candidate to the session on either side (1.26.60).
- `CONNECTRESPONSE` and `CONNECTERROR` for a session that does not exist are dropped silently (1.26.60).
- `CANDIDATEADD` for a session that does not exist opens one with no direction yet and queues the candidate. A later `CONNECTREQUEST` with that connection id makes it an incoming session (1.26.60).
- A session opened from a LAN message gets no identity assertion on its answer (1.26.60).

## Sending

- A message goes over LAN when LAN signaling is on, the remote is known from LAN discovery or was added as a LAN host, and either no other signaling channel is set or the session prefers LAN. Otherwise it goes over the other signaling channel (1.26.60).
- An incoming session prefers the channel its `CONNECTREQUEST` came on, so one that came over LAN answers over LAN, its declining `CONNECTERROR` included. An outgoing session has no preference and uses the other channel when one is set (1.26.60).
- With no route the send fails with `SessionError.NO_SIGNALING_CHANNEL` (24). A send for a session that no longer exists fails with `SessionError.SIGNALING_FAILED_TO_SEND` (26) (1.26.60).
- A delivery error the signaling channel reports later closes the session with that error, except `SessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED` (19), which closes only an outgoing session (1.26.60).
