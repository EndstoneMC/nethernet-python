---
paths:
  - "src/nethernet/transport/framing.py"
  - "tests/test_framing.py"
  - "tests/test_conformance.py"
---

# Framing

## Fragment

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | header (`u8`): the count of fragments that follow this one |
| 1 | 1 to the fragment size | payload |

- Every data-channel message is one fragment, so the last or only fragment of a packet has header 0 (1.26.60).
- An empty packet cannot be sent: a one-byte message is malformed (1.26.60).

## Fragment size

- The fragment size defaults to 262143 (`0x3FFFF`), the default SCTP max message size 262144 minus 1 (1.26.60).
- When ICE reaches Connected (`transport.md`), the fragment size becomes the SCTP transport's max message size minus 1. It keeps its previous value when no max message size is known (1.26.60).
- A max message size of 1 or less closes the session with `SessionError.INVALID_ARGUMENT` (34) (1.26.60).
- The fragment size is per session (1.26.60).
- The max message size is the one `transport.md` negotiates. A peer value of 0 gives the local value, and a negative one is 1 or less (1.26.60).
- Against BDS the max message size is at most 262144 (`transport.md`), so the fragment size never exceeds 262143 and one fragment never passes the send-queue limit (`transport.md`) (1.26.60).

## Channels

| Label | Ordering | Init | Carries |
| --- | --- | --- | --- |
| `ReliableDataChannel` | reliable, ordered | `ordered = true`, no retransmit limit | `SendType.RELIABLE` |
| `UnreliableDataChannel` | unreliable, unordered | `ordered = false`, `maxRetransmits = 0` | `SendType.UNRELIABLE` |

- Both channels are in-band: not pre-negotiated, no fixed stream id, empty protocol (1.26.60).
- Route a received channel by exact label: `ReliableDataChannel` is the ordered channel, any other label the unordered one (1.26.60).
- A send with any other `SendType` value sends nothing (1.26.60).

## Send

Send a packet of `N` bytes with a `SendType`, with `F` the fragment size (1.26.60):

1. `N <= F`: one fragment with header 0 and the whole packet, on the channel the `SendType` picks.
2. Else `N > 255 * F`: drop it.
3. Else unreliable: drop it. An unreliable packet is never fragmented.
4. Else reliable: `fragments = (N - 1) // F + 1`, from 2 to 255. Send consecutive chunks of up to `F` bytes in order on `ReliableDataChannel`, the header counting down from `fragments - 1` to 0.

- 255 fragments bound a reliable packet at `255 * F` bytes, `0x3FBFF01` at the default fragment size (1.26.60).
- The send returns `true` once the fragments are queued, and `false` when it drops the packet or knows no such session. It never blocks and never waits for delivery (1.26.60).

## Receive

- Reliable channel: a message of 0 or 1 bytes discards the partial packet and closes `ReliableDataChannel` (1.26.60).
- Reliable channel: the first fragment of a packet sets the expected header to its own header. Each later fragment must carry the expected header, which drops by one after every fragment with a non-zero header. A mismatch discards the partial packet and closes `ReliableDataChannel` (1.26.60).
- Reliable channel: append each fragment's payload to the session's partial packet. A fragment with header 0 completes it: deliver it and start a new one. SCTP ordering is the only sequencing (1.26.60).
- Unreliable channel: a message of 0 or 1 bytes closes `UnreliableDataChannel`, which ends the whole session with reason 0 (`transport.md`). Size is the only check on this channel (1.26.60).
- Unreliable channel: each message is a whole packet. Strip the header byte unchecked and deliver the rest (1.26.60).
- A channel that closes discards the partial packet (1.26.60). `transport.md` says how the close ends the session.
- Deliver packets to the application in FIFO order (1.26.60).
- A read shorter than the head packet returns its prefix and leaves the rest as the head packet (1.26.60).
- A packet-available check reports the size of the whole head packet (1.26.60).
- The receive queue and the partial packet have no size cap (1.26.60).

## Payload

- A Bedrock packet batch carries no RakNet `0xFE` prefix. BDS hands each packet's bytes to the batch decoder unchanged (1.26.60).
- BDS silently ignores a batch that has the prefix: a `00 fe 06 c1 01 00 00 03 cf` message gets no reply and the session stays open, and the same batch without `fe` gets NetworkSettings (1.26.60).
