---
paths:
  - "src/nethernet/signaling/http.py"
  - "src/nethernet/api.py"
  - "tests/test_http_signaling.py"
---

# HTTP signaling

- Channel choice and probing: SPEC §6.3.1
- Request syntax and routes: SPEC §6.3.2
- Connection handling: SPEC §6.3.3
- Responses: SPEC §6.3.4
- Bridging a POST to signaling: SPEC §6.3.5
- Client: SPEC §6.3.6
- Bind address and listen backlog: SPEC §14.1

## Probing

- When every URL fails, the client falls back to RakNet only when a remote flag allows it, off by default. Otherwise it shows an error (1.26.60). This library has no RakNet: the dial fails with `SessionError.NO_SIGNALING_CHANNEL`.
- `connect(target)` probes an HTTP signaling address, as `host` or `host:port`, as SPEC §6.3.1 says. An explicit `http://` or `https://` URL skips the probe and is the base URL.

## Server

- This library raises the `OSError` or `socket.gaierror` from an unparseable or unbindable bind address when the server starts, instead of starting with no listener (`api.md`).
- This library strips and ignores any query string on every route, `GET /v1/join` and `POST /v1/join/{networkId}`, before routing and id parsing, so an unknown query parameter never causes an error. Vanilla clients send no query on a POST, so they see no difference (`api.md`).
- This library answers an offer the application refuses, `validate_offer` returning `False`, with the same `37` as SPEC §6.3.5. An exception from the accept path drops the offer with no signal back, so it ends in the 504 after 10 s (`api.md`).

## Client

- This library's dialer sends one full-ICE offer POST with every candidate inline and no `CANDIDATEADD`, since aiortc gathers before the offer.
- This library raises a 2xx body that does not start with `v=` as the `SessionError` it holds, and as `SessionError.SIGNALING_PARSING_FAILURE` (17) when it is neither.
