---
paths:
  - "src/nethernet/session.py"
  - "src/nethernet/session_manager.py"
  - "tests/test_session_dialer.py"
  - "tests/test_session_listener.py"
  - "tests/test_session_loopback.py"
---

# Session

## Dialer

1. Create the peer connection and both data channels (`transport.md`), create the offer and set it as the local description (1.26.60).
2. With an `identity_signer` from the application (`PlayerSigner` or `IdentitySigner`), add the `a=identity` line to the offer (`identity.md`) (1.26.60).
3. Send `CONNECTREQUEST` with the connection id and the offer. The negotiation timeout runs from this send (1.26.60).
4. Send each local candidate as `CANDIDATEADD` when trickle, and keep each received `CANDIDATEADD` as Candidates says (1.26.60).
5. On an accepted `CONNECTRESPONSE` (Accept), set the answer as the remote description, then add the buffered candidates. The negotiation timeout stops here (1.26.60).
6. ICE reaches connected: the session opens to the application, once (1.26.60).

## Listener

1. On an accepted `CONNECTREQUEST` for a new remote and connection id (Routing), ask the application for the remote's connection flags (`errors.md`) and create the peer connection. Only `ConnectionFlags.RELAY_ONLY` acts here, making ICE relay-only. BDS sets only `NO_TCP_CANDIDATES`, so its listener sessions are not relay-only (1.26.60).
2. Keep the offer's `a=identity` assertion as the remote identity (`identity.md`), set the offer as the remote description, create the answer and set it as the local description (1.26.60).
3. Add the candidates buffered so far. Add the `a=identity` line when the application gave an `identity_signer` and the offer did not come over LAN. Send `CONNECTRESPONSE` with the same connection id on the channel the offer came on (1.26.60).
4. Send each local candidate as `CANDIDATEADD` when trickle, and add each received one (1.26.60).
5. The data channels arrive from the on-data-channel event. ICE reaches connected: the session opens to the application, once (1.26.60).

## Routing

- Sessions are keyed by remote `NetworkID`, then by connection id, so two remotes may use the same connection id (1.26.60).
- `CONNECTREQUEST`: the accept decision (Accept), then find or create the session for the sender and connection id. A new one becomes a listener session. An existing listener session is reused and the offer is not applied again. An existing dialer session with that id ignores it: nothing is sent, and `SessionError.INCOMING_CONNECTION_IGNORED` (16) is never produced (1.26.60).
- `CONNECTRESPONSE`: the accept decision for every one, an unknown pair included, then the matching session. Accepted, a dialer applies it and a listener does nothing. One for an unknown pair is then dropped (1.26.60).
- `CONNECTERROR`: a dialer closes with the error it carries, whatever its value. A listener ignores every `CONNECTERROR`. One for an unknown pair is dropped (1.26.60).
- `CANDIDATEADD`: find or create the session for the pair. For an unknown pair it creates a listener session early and defers the candidate, and the accepted offer later attaches to that session. The early session's trickle mode follows the candidate's channel. One whose offer never comes stays, since the sweep does not remove it (1.26.60). BDS keeps an early candidate over LAN and starts its checks toward it as soon as it answers. Without one BDS sends no checks until the dialer starts its own, and ICE still connects (1.26.60).
- A message that does not parse (`signaling.md`) is dropped with no reply and no error (1.26.60).

## Accept

- The application accepts or declines each `CONNECTREQUEST` and `CONNECTRESPONSE`, asynchronously, given the remote `NetworkID`, the connection id, the `a=identity` assertion and the fingerprints from the SDP, and whether the message came over LAN. With no handler, every one is accepted (1.26.60).
- A declined offer gets `CONNECTERROR` with `SessionError.IDENTITY_NOT_ALLOWED` (37) and the same connection id, on the channel it came on, and creates no session (1.26.60).
- A declined answer closes the matching session with `SessionError.IDENTITY_NOT_ALLOWED` (37), dialer or listener (1.26.60).
- The accept wait has no timer. An offer the application never decides gets no session and no reply, and the remote dialer closes with its own 14 (1.26.60).
- A duplicate `CONNECTREQUEST` is decided again. Declined, it gets the 37 reply and the existing session stays (1.26.60).

## Candidates

- A trickle peer sends its offer or answer once the local description is set, then each candidate as its own `CANDIDATEADD` as it gathers. Trickle and LAN signaling are on by default (`config.md`) (1.26.60).
- A non-trickle peer uses no STUN or TURN servers, waits for gathering to complete, sends one SDP with every candidate and no `CANDIDATEADD`, and still accepts inbound `CANDIDATEADD` (1.26.60).
- Trickle is a per-endpoint setting that LAN sessions override (LAN sessions), and a trickle peer interoperates with a non-trickle one (1.26.60).
- Add a received candidate once both the local and the remote description are set, and buffer it before that (1.26.60).
- A LAN Message carries the whole SDP (`lan-discovery.md`). On LAN, an offer may carry its candidates or send each as its own `CANDIDATEADD`, and BDS adds either (1.26.60).
- Over HTTP, send the full-ICE SDP (`http-signaling.md`). BDS's HTTP answer carries every candidate inline and still has `a=ice-options:trickle`. It accepts an HTTP offer with no candidates. It never sends `CANDIDATEADD` over LAN or HTTP (1.26.60).
- A `CANDIDATEADD` sent over LAN for an HTTP-created session, with the same connection id, is applied at once (1.26.60).
- A release client trickles over HTTP: its offer is one POST and each `CANDIDATEADD` is its own POST (1.26.60).

## Signaling channel

- A session's channel choice and the handling of a send failure are in `signaling.md` Sending. A failed send closes the session with the error it reports.

## Timeouts

- The negotiation timeout runs on the dialer only, from sending `CONNECTREQUEST` until the answer is set as the remote description. The listener has none (1.26.60). `config.md` holds the value.
- A sweep runs every 5 s, timed from the end of the previous pass (1.26.60). It closes a session whose ICE state is failed or closed with `SessionError.ICE` (5), and a dialer past its negotiation timeout with `SessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE` (14). A timed-out dialer therefore closes up to 5 s after the timeout expires (1.26.60).
- BDS has no idle timer and never closes a session with `SessionError.INACTIVITY_TIMEOUT` (9). A silent session over HTTP or LAN stayed open for 300 s while BDS kept sending ICE checks about every 2.6 s. A 9 reaches the application only as a `CONNECTERROR` value or a signaling-channel error (1.26.60).
- BDS never produces `SessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_ACCEPT` (15) (1.26.60).

## Close

The close reasons (1.26.60):

| `SessionError` | Value | Side | When |
| --- | ---: | --- | --- |
| success, reported as `None` | 0 | both | the application closes the session, or a data channel closes with no error, whoever closed it (`transport.md` maps aiortc) |
| `ICE` | 5 | both | ICE fails, or the sweep finds ICE failed or closed |
| `NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE` | 14 | dialer | the sweep finds the negotiation timeout expired |
| `NO_SIGNALING_CHANNEL` | 24 | both | no signaling channel is usable |
| `DATA_CHANNEL_CLOSED` | 32 | both | a data channel closes with an error, an SCTP abort from the remote included, or is missing at a state change (`transport.md` maps aiortc) |
| `INVALID_ARGUMENT` | 34 | both | on connect, the SCTP max message size is 1 or less |
| `IDENTITY_NOT_ALLOWED` | 37 | both | the application declines a `CONNECTRESPONSE` |
| the carried value | any | dialer | a `CONNECTERROR` arrives |
| the reported value | any | both | an HTTP send fails (Signaling channel) |

- A failed negotiation step closes the session with its own code (`errors.md`) (1.26.60).
- A session reports its close to the application once, with its `SessionError` and a JSON statistics summary, and closes its peer connection (1.26.60).
- An application close removes the session and reports 0. It returns false with no report when no session matches, so a second close of one session returns false (1.26.60).
- The library reports that 0 as `None` (`errors.md`).
- BDS sends `CONNECTERROR` only to decline an offer (Accept), never on close. It sent none over LAN on idle, a silently cut socket, the peer's close, a game-layer disconnect or `stop`, and `stop` closed every session within 0.16 s (1.26.60).

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
- aiortc and aioice record no STUN round-trip time or response count, so this library reports `rtt`, `avgrtt` and `responses_received` as `None`.

## LAN sessions

- A session created from a LAN message is non-trickle whatever the endpoint setting. BDS answers a LAN offer with one `CONNECTRESPONSE` carrying its candidates inline and sends no `CANDIDATEADD` (1.26.60).
- It gets no `identity_signer`, so its answer carries no `a=identity` (1.26.60).
- It replies over LAN (Signaling channel), and the accept decision is told it came over LAN (1.26.60).
- A dialer the application opens toward a LAN host keeps the endpoint's trickle setting (1.26.60).
