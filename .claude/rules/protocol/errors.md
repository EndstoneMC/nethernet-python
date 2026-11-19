---
paths:
  - "src/nethernet/errors.py"
---

# Errors

## SessionError

A session closes with one `SessionError`. `CONNECTERROR` carries it (`signaling.md`) and the application sees it on close. Every value and member below (1.26.60).

| Value | Member | Value | Member |
| ---: | --- | ---: | --- |
| 0 | success, reported as `None` | 21 | `SIGNALING_MESSAGE_DELIVERY_FAILED` |
| 1 | `DESTINATION_NOT_LOGGED_IN` | 22 | `SIGNALING_TURN_AUTH_FAILED` |
| 2 | `NEGOTIATION_TIMEOUT` | 23 | `SIGNALING_FALLBACK_TO_BEST_EFFORT_DELIVERY` |
| 3 | `WRONG_TRANSPORT_VERSION` | 24 | `NO_SIGNALING_CHANNEL` |
| 4 | `FAILED_TO_CREATE_PEER_CONNECTION` | 25 | `NOT_LOGGED_IN` |
| 5 | `ICE` | 26 | `SIGNALING_FAILED_TO_SEND` |
| 6 | `CONNECT_REQUEST` | 27 | `RELAY_SERVER_CONFIGURATION_RESULT_FAILURE` |
| 7 | `CONNECT_RESPONSE` | 28 | `RELAY_SERVER_CONFIG_PARSING_ERROR_NO_URLS` |
| 8 | `CANDIDATE_ADD` | 29 | `RELAY_SERVER_CONFIG_PARSING_ERROR_NO_CREDS` |
| 9 | `INACTIVITY_TIMEOUT` | 30 | `RELAY_SERVER_CONFIG_PARSING_ERROR_NO_SERVERS` |
| 10 | `FAILED_TO_CREATE_OFFER` | 31 | `RELAY_SERVER_CONFIG_PARSING_ERROR_NO_EXPIRATION` |
| 11 | `FAILED_TO_CREATE_ANSWER` | 32 | `DATA_CHANNEL_CLOSED` |
| 12 | `FAILED_TO_SET_LOCAL_DESCRIPTION` | 33 | `INTERNAL_ERROR_JSON_SERIALIZATION` |
| 13 | `FAILED_TO_SET_REMOTE_DESCRIPTION` | 34 | `INVALID_ARGUMENT` |
| 14 | `NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE` | 35 | `GENERIC_FAILURE` |
| 15 | `NEGOTIATION_TIMEOUT_WAITING_FOR_ACCEPT` | 36 | `FAILED_TO_CREATE_IDENTITY_ASSERTION` |
| 16 | `INCOMING_CONNECTION_IGNORED` | 37 | `IDENTITY_NOT_ALLOWED` |
| 17 | `SIGNALING_PARSING_FAILURE` | 38 | `NOT_AUTHENTICATED` |
| 18 | `SIGNALING_UNKNOWN_ERROR` | 39 | `NOT_ALLOWED` |
| 19 | `SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED` | 40 | `SERVER_FULL` |
| 20 | `SIGNALING_BROADCAST_DELIVERY_FAILED` | | |

- BDS renders every code as `NetherNet SessionError: <n>`, with no per-code message, unknown codes included (1.26.60).

## Where codes arise

| Code | Situation |
| ---: | --- |
| 0 | The application closes a session itself, or a data channel closes with no error, a clean close by the remote included. A signaling send that succeeds, over HTTP included, reports 0 (1.26.60). |
| `FAILED_TO_CREATE_PEER_CONNECTION` (4) | The dialer or the listener cannot set up its peer connection (1.26.60). |
| `ICE` (5) | The ICE connection state of either side turns `failed`. The sweep also closes a session in state `failed` or `closed` (`session.md`). `disconnected` does not close it (1.26.60). |
| `FAILED_TO_CREATE_OFFER` (10) | The dialer cannot create its offer (1.26.60). |
| `FAILED_TO_CREATE_ANSWER` (11) | The listener cannot create its answer (1.26.60). |
| `FAILED_TO_SET_LOCAL_DESCRIPTION` (12) | Either side cannot set or serialize its local description (1.26.60). |
| `FAILED_TO_SET_REMOTE_DESCRIPTION` (13) | Either side cannot set the remote description (1.26.60). |
| `NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE` (14) | A dialer has still not set the answer as its remote description when the negotiation timeout has passed since it sent `CONNECTREQUEST`, checked on the sweep (`session.md`). An incoming session never times out this way (1.26.60). |
| `SIGNALING_PARSING_FAILURE` (17), `SIGNALING_UNKNOWN_ERROR` (18), `NOT_LOGGED_IN` (25) | The online signaling client fails to send (1.26.60). |
| `SIGNALING_MESSAGE_DELIVERY_FAILED` (21) | The online signaling service answers a tracked message with a JSON-RPC error (1.26.60). |
| `SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED` (19) | The signaling channel reports it for a message it could not deliver (`signaling.md`) (1.26.60). |
| `NO_SIGNALING_CHANNEL` (24) | A message has no route: LAN does not apply and no other signaling channel is set (1.26.60). |
| `SIGNALING_FAILED_TO_SEND` (26) | A message is sent for a session that no longer exists, or the online signaling client fails to send. The HTTP signaling client gets a non-2xx status other than 401, 403 and 503, or no response (1.26.60). |
| `RELAY_SERVER_CONFIGURATION_RESULT_FAILURE` (27) | The online signaling client's relay config request fails (1.26.60). |
| 28 to 31 | The relay config reply lacks its URLs (28), credentials (29), servers (30) or expiration (31) (1.26.60). |
| `DATA_CHANNEL_CLOSED` (32) | A data channel is missing or closes with an error (1.26.60). |
| `INTERNAL_ERROR_JSON_SERIALIZATION` (33) | The online signaling client cannot serialize a signal before sending it (1.26.60). |
| `INVALID_ARGUMENT` (34) | A session connects with an SCTP max message size of 1 or less (1.26.60). |
| `GENERIC_FAILURE` (35) | The online signaling client gets a send error from outside `SessionError` (1.26.60). |
| `FAILED_TO_CREATE_IDENTITY_ASSERTION` (36) | A side with an `identity_signer` cannot create the identity assertion for its local description (`identity.md`) (1.26.60). |
| `IDENTITY_NOT_ALLOWED` (37) | The listener's application declines an offer: the listener answers `CONNECTERROR` 37. The dialer's application declines an answer: the dialer closes its session with 37 (1.26.60). |
| `NOT_AUTHENTICATED` (38) | The HTTP signaling client gets status 401 (1.26.60). |
| `NOT_ALLOWED` (39) | The HTTP signaling client gets status 403 (1.26.60). |
| `SERVER_FULL` (40) | The HTTP signaling client gets status 503 (1.26.60). |

- Codes 17, 18, 21, 25, 27 to 31, 33 and 35 come only from online signaling, which this library does not implement. Codes 38 to 40 come only from the HTTP signaling client, which BDS as a server never runs (1.26.60).

- A dialer that receives `CONNECTERROR` closes with the code it carries unchanged, a `SessionError` or not (1.26.60).

- The negotiation timeout reports 14 only. `NEGOTIATION_TIMEOUT` (2) and `NEGOTIATION_TIMEOUT_WAITING_FOR_ACCEPT` (15) never come from it (1.26.60).
- BDS never sends or reports `SIGNALING_PARSING_FAILURE` (17) for a malformed message: it drops the message silently (1.26.60).
- No producer of 1 to 3, 6 to 9, 15, 16, 20, 22 or 23 exists (1.26.60).
- The library reports wire code 0 as `None`.
- The library reports an unknown wire code as a `SessionError` pseudo-member that keeps the int.
- The library's HTTP client produces 17 for a 2xx body that is neither an SDP nor a `SessionError` value (`http-signaling.md`).

## SendType

- `SendType.UNRELIABLE = 0`, `SendType.RELIABLE = 1` (1.26.60). `transport.md` maps each to its data channel.

## ConnectionFlags

- A bitmask that filters ICE candidates: `NONE = 0`, `RELAY_ONLY = 1`, `NO_TCP_CANDIDATES = 2`, `NO_LOCAL_CANDIDATES = 4`, `NO_SERVER_REFLEXIVE_CANDIDATES = 8`, `NO_PEER_REFLEXIVE_CANDIDATES = 16`, `NO_RELAY_CANDIDATES = 32` (1.26.60).
- The application supplies the flags per remote `NetworkID` when the peer connection is configured. BDS sets `NO_TCP_CANDIDATES` for every remote (1.26.60).
- `RELAY_ONLY` restricts ICE to relay candidates, libwebrtc's `IceTransportsType::kRelay` (1.26.60). `transport.md` says how aiortc does it.
- Nothing reads bits 2 to 32, so they have no effect, `NO_TCP_CANDIDATES` included (1.26.60).
- A black-box join over HTTP saw a single UDP host candidate in BDS's answer (1.26.60).
