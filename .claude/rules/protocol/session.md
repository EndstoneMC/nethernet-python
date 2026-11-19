---
paths:
  - "src/nethernet/session.py"
  - "src/nethernet/session_manager.py"
  - "tests/test_session_dialer.py"
  - "tests/test_session_listener.py"
  - "tests/test_session_loopback.py"
---

# Session

- Dialer: SPEC §12.3
- Listener: SPEC §12.4
- Routing: SPEC §12.1
- Accept decision: SPEC §12.2
- Candidates: SPEC §12.5
- Timeouts and the sweep: SPEC §12.7
- Close reasons: SPEC §12.8
- LAN sessions: SPEC §12.6

## Close

- A session reports its close to the application once, with its `SessionError` and a JSON statistics summary (Statistics), and closes its peer connection (1.26.60).
- An application close removes the session and reports 0. It returns false with no report when no session matches, so a second close of one session returns false (1.26.60).
- The library reports that 0 as `None` (`errors.md`).
- This library signs with the application's `identity_signer`, a `PlayerSigner` for a player or an `IdentitySigner` for an operator.

## Application I/O

- Send, peek, read and state queries find any session not yet removed, connecting or dead but not yet swept included. An unknown or removed session returns false (1.26.60).
- A send returns true once the packet is queued, with no state check. A packet sent while connecting waits until its data channel opens. `framing.md` holds the size limits (1.26.60).
- A state query for the local `NetworkID` returns false (1.26.60).
- The session state reports connecting while ICE is new or checking, active while ICE is connected, completed or disconnected, and errored while ICE is failed or closed, with the ICE state itself (1.26.60).
- It also reports the RTT in milliseconds, bytes sent, the send rate in bytes per second, bytes received, packets sent and whether the selected pair's remote candidate is a relay, from the last statistics snapshot. A query refreshes a snapshot older than 2 s for the next one, and reports zeros before the first (1.26.60).

## Statistics

- A statistics snapshot is taken at most once a second. A rate is the counter's change divided by the whole seconds elapsed, an average rate is the running mean over all snapshots, and a maximum rate is the largest seen (1.26.60).
- A candidate's type, network type, IP and URL are recorded when it is first seen and never updated. The IP is anonymized: IPv4 ends in `.0`, IPv6 keeps at most its first three groups followed by `::` (1.26.60).
- A candidate pair's state list grows only when the state changes. `avgrtt` is the current RTT divided by the responses received, or the current RTT when none (1.26.60).
- The summary is one JSON object. `startTime` and `endTime` are always present, strings as `YYYY-MM-DDTHH:MM:SS.mmmZ` in UTC. `errors` is always present, an array that may be empty. Every other member is omitted when empty or zero, never null (1.26.60).
- `active` is a string, the selected pair's id. `local` and `remote` are objects keyed by candidate id, each holding `type`, `net`, `ip` and `url` strings, each omitted when empty. `pairs` is an object keyed by pair id (1.26.60).
- An `errors` entry holds `time` (string), and, when set, `address` (string), `port` (int), `url` (string), `code` (int) and `message` (string) (1.26.60).
- A pair holds `local`, `remote` (strings), `states` (array of strings), `bytesSent`, `packetsSent`, `bytesReceived`, `packetsReceived`, and for each of those four counters a rate, an average rate and a maximum rate (`bytesSentRate`, `avgBytesSentRate`, `maxBytesSentRate`, `packetsSentRate`, `bytesReceivedRate`, `packetsReceivedRate` and so on), the average a float and the others integers, and `rtt` and `avgrtt` as floats. Each is omitted when zero (1.26.60).
- aiortc and aioice record no STUN round-trip time or response count, so this library reports `rtt`, `avgrtt` and `responses_received` as `None`.
- Floats in the summary are written as `identity.md` formats a non-integer number.
- This library keys candidates `L<n>` and `R<n>` in order of first sighting, and leaves `rtt` and `avgrtt` out of the JSON.
