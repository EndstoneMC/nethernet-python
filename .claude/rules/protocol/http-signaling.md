---
paths:
  - "src/nethernet/signaling/http.py"
  - "src/nethernet/api.py"
  - "tests/test_http_signaling.py"
---

# HTTP signaling

The routes follow Mojang's public [NetherNet onboarding guide](https://github.com/Mojang/bedrock-protocol-docs/blob/main/NetherNetOnboardingGuide.md).

## Channel choice

- A host picked from LAN discovery is signaled over LAN only (1.26.60).
- An IP target is probed first and signaled over HTTP once a probe URL answers (1.26.60).
- Friend, invite, Realms and featured-server joins signal through the online service, which this library does not implement (1.26.60).

## Probing

- The candidate URLs for host `H` and port `P` (1.26.60):
  - a URL that answered an earlier probe of `H:P`, alone, when the client has one cached;
  - otherwise, with `P` neither 0 nor 19132: `https://H:P`, `http://H:P`;
  - otherwise: `https://H:19132`, `https://H`, `http://H:19132`, `http://H`.
- The `https://` URLs form the first batch and the `http://` URLs the fallback batch, run once the first batch has no success (1.26.60).
- Every URL of a batch starts at once. The first to succeed wins, and the list order sets only the start order (1.26.60).
- The whole probe times out after 6 s (1.26.60).
- The direct-join probe is `GET <url>/v1/join` with no query and no extra headers, and a 3 s timeout per request. Any 2xx answer without a transport error succeeds, and the body is ignored (1.26.60).
- So a host whose `GET /v1/join` body is empty, as BDS's with `enable-lan-visibility=false`, still passes the direct-join probe (1.26.60).
- The server list's probe is `GET <url>/v1/join?version=<game version>`, and it needs a 2xx answer whose body is non-empty and parses as the ping JSON (1.26.60).
- The first URL that succeeds is cached for `H:P` and becomes the signaling base URL (1.26.60).
- When every URL fails, the client falls back to RakNet only when a remote flag allows it, off by default. Otherwise it shows an error (1.26.60). This library has no RakNet: the dial fails with `SessionError.NO_SIGNALING_CHANNEL`.
- `connect(target)` probes an HTTP signaling address, as `host` or `host:port`, this way. An explicit `http://` or `https://` URL skips the probe and is the base URL.

## Server

- The dedicated server listens on plain TCP on `server-port`, default 19132. `server-udp-ports` does not move it (1.26.60).
- `server-ip` is the bind address. An IPv4 or IPv6 address, `127.0.0.1`, `::1` or an interface address, listens on that address alone, single stack (1.26.60).
- With an empty bind address, the shipped default, the server listens on dual-stack `[::]` and falls back to `0.0.0.0` only when that fails. A bind address that does not parse or is not on the host leaves nothing listening, logs no error, and BDS still starts. The listen backlog is 64 (1.26.60).
- This library raises the `OSError` or `socket.gaierror` from an unparseable or unbindable bind address when the server starts (`api.md`).
- The HTTP signaling server speaks plain HTTP/1.1. When a connection's first two bytes are `16 03`, it sends the 7 bytes `15 03 01 00 02 02 28`, a fatal TLS `handshake_failure` alert, and closes, so `https://` fails the handshake (1.26.60).
- `GET /v1/join` answers `200 application/json` with the ping JSON, for example `{"name":"Dedicated Server","protocol":2225,"version":"1.26.60","level":"Bedrock level","players":0,"maxPlayers":10,"gameType":0}` (1.26.60).
- BDS fills that body only with `enable-lan-visibility=true`. Otherwise the body is empty (1.26.60).
- The GET route ignores a query string, so `/v1/join?version=...` gets the same answer (1.26.60).
- `POST /v1/join/{networkId}` carries the offer SDP as its body, `{networkId}` the dialer's own id (1.26.60).
- `{networkId}` is read as an unsigned `u64` in C `strtoull` base 0: `0x` hex, a leading `0` for octal, a leading `+` and leading whitespace are accepted. A `-`, no digits, a value over `u64` or any byte after the number is rejected. The POST route keeps the query string, so `?` is such a trailing byte (1.26.60).
- This library strips and ignores any query string on every route, `GET /v1/join` and `POST /v1/join/{networkId}`, before routing and id parsing, so an unknown query parameter never causes an error. Vanilla clients send no query on a POST, so they see no difference (`api.md`).
- So `0x10`, `010`, `+5` and a leading tab or vertical tab are accepted, `0x0` and `00` are the id 0, and `0x`, `08`, `-5` and 2^64 are rejected (1.26.60).
- The path is not percent-decoded, and a literal space ends it. `%35`, `%20%205` and `/v1/join/ 5` are rejected (1.26.60).
- The id is checked before the body (1.26.60).

| Case | Status and reason | Body |
| --- | --- | --- |
| `{networkId}` rejected as above | `400 Expected /v1/join/{networkId}` | `Expected /v1/join/{networkId}` |
| `{networkId}` is 0 | `400 Invalid networkId` | `Invalid networkId` |
| Empty body | `400 Missing SDP offer in request body` | `Missing SDP offer in request body` |
| Any other method or path, `/` and `POST /v1/join` included | `404 Not Found` | `Not Found` |
| No answer within 10 s | `504 Gateway Timeout` | `Gateway Timeout` |
| The request handler failed | `500 Internal Server Error` | empty |

- The 400, 404 and 504 answers are `text/plain`. The 500 answer has no `Content-Type`, `Content-Length: 0` and an empty body (1.26.60).
- `PATCH` and `HEAD` get the 404 like any other method, and a `HEAD` answer still carries its 9-byte body (1.26.60).
- The server never answers 405 or 503 (1.26.60).
- An accepted POST becomes `CONNECTREQUEST <sessionId> <body>` from `{networkId}`, and the request stays open until the host signals back (1.26.60).
- `<sessionId>` counts up in decimal from 1, with no randomness. Only an accepted POST takes one (1.26.60).
- The first signal the host sends to `{networkId}` with that session id completes the request as `200 application/sdp`, its body everything after the second space. The signal's type is not checked (1.26.60).
- So a rejecting host's `CONNECTERROR` answers `200 application/sdp` whose body is the bare decimal `SessionError`, not SDP (1.26.60).
- Over HTTP BDS answers an offer whose `a=identity` is missing or fails verification this way with `SessionError.IDENTITY_NOT_ALLOWED`, body `37`. The check runs whatever `online-mode` says, so a client dialing BDS signs its offer (1.26.60).
- An offer that passes the identity check but fails in WebRTC gets no signal back, so it ends in the 504 after 10 s (1.26.60).
- This library answers a rejected offer with the HTTP status of its `SignalingRejected`, 403 by default, and a malformed identity envelope with 400.

## Connections

- A response is `HTTP/1.1 <status> <reason>`, then `Content-Type` when there is one, `Content-Length` and `Connection: close`. The server closes the connection after writing it. There is no keep-alive (1.26.60).
- A second request on the same connection, pipelined or sent after the answer, is dropped with the connection (1.26.60).
- A client that half-closes its side gets no answer (1.26.60).
- The server holds at most 64 open connections. The 65th and later are accepted and closed at once, without a response (1.26.60).
- A connection that has not sent a whole request within 30 s is closed without a response (1.26.60).
- Lines end in CRLF. A request with LF-only line endings never completes and meets the 30 s close (1.26.60).
- A request of up to 65536 bytes, headers and body together, is answered. One byte more closes the connection without a response (1.26.60).
- The request line is `<method> <path> <version>`. The method is 1 to 16 bytes of `A` to `Z` and the path starts with `/`. The version token is not checked. Anything else closes the connection without a response (1.26.60).
- The `Content-Length` header name is exactly `content-length` in any case, with no space before the `:`. Its value, trimmed of spaces and tabs, is an unsigned decimal with nothing after it and no sign. A repeat with the same value is accepted, a different one is malformed (1.26.60).
- No `Content-Length` means an empty body. `Transfer-Encoding: chunked` is ignored (1.26.60).
- A malformed `Content-Length` closes the connection without a response. Otherwise the server waits for the whole body (1.26.60).

## Client

- Each signal goes to `<base>/v1/join/<own networkId>`, the dialer's own id in decimal (1.26.60).
- The client splits the signal `<type> <sessionId> <data>` at its first two spaces and posts only `<data>` as the body. It never checks the type (1.26.60).
- The client gives the HTTP host a random `u64` id of its own, and answers arrive from that id (1.26.60).
- The request carries `Content-Type: application/sdp` and is a `POST` (1.26.60).
- BDS's answer is a full-ICE SDP with every candidate inline, and no `CANDIDATEADD` follows, though it still says `a=ice-options:trickle` (1.26.60).
- The client trickles by default. Each `CANDIDATEADD` goes as its own POST whose body is the candidate. An attempt is one offer POST plus one POST per candidate, and none is retried (1.26.60).
- This library's dialer sends one full-ICE offer POST with every candidate inline and no `CANDIDATEADD`, since aiortc gathers before the offer.
- A 2xx answer without a transport error succeeds. A non-empty body is delivered as `CONNECTRESPONSE <sessionId> <body>` from the remote, `<sessionId>` that of the offer. An empty body delivers nothing (1.26.60).
- The client does not inspect the body (1.26.60). This library raises a 2xx body that does not start with `v=` as the `SessionError` it holds, and as `SessionError.SIGNALING_PARSING_FAILURE` when it is neither.
- Any other answer fails the dial (1.26.60):

| Answer | `SessionError` |
| --- | --- |
| 401 | `NOT_AUTHENTICATED` (38) |
| 403 | `NOT_ALLOWED` (39) |
| 503 | `SERVER_FULL` (40) |
| any other status, or a failed request | `SIGNALING_FAILED_TO_SEND` (26) |
