---
paths:
  - "src/nethernet/api.py"
  - "src/nethernet/transport_api.py"
  - "src/nethernet/__init__.py"
---

# Public API

The facade is `api.py`, `transport_api.py` and `__init__.py`, a thin asyncio layer shaped like `websockets`.

## Layer, don't rewrite

- The engine (sessions, session routing, the peer connection, LAN discovery, HTTP signaling) stays internal. The facade builds on it and never re-implements it.
- No protocol logic in the facade: no codec, state machine, timer policy or wire constant. It turns the engine's callbacks into a `Future` per connect and an `asyncio.Queue` per connection, server and discovery.
- A wire fix goes into the engine, never into the facade.
- The public surface is exactly `__all__` in `__init__.py`. Everything else is internal, undocumented and free to change.
- Re-export `NetworkID`, `SessionError`, `SendType` and `ConnectionFlags` as they are.
- Names are websocket-style: `connect`, `serve`, `discover`, `send`, `recv`, `state`.
- Every public name keeps a short user-facing docstring (`comments.md`).

## Entry points

- Three entry points, never split by signaling channel.
- `serve(handler, ...)` is the only server entry point. One call serves HTTP signaling on `host`:`port` and LAN signaling on `lan_host`:`lan_port` together, answers discovery with `connection` 4 and does not broadcast. It signs each HTTP answer with `identity_signer`, an `IdentitySigner`, by default a fresh auto-generated P-384 one, and refuses an unsigned or invalid HTTP offer with `SessionError.IDENTITY_NOT_ALLOWED` (37) as BDS does. LAN offers are not checked. Its defaults come from `protocol/config.md`.
- `connect(target, ...)` is the only client entry point. The target picks the channel: a `NetworkID` or `DiscoveredHost` goes over LAN, a string, `host`, `host:port` or an `http(s)://` URL, over HTTP. An HTTP connect signs with a fresh self-signed `PlayerSigner` unless `identity_signer` is given.
- `discover(...)` is the LAN discovery call.

## Connection

- One `Connection` class serves both ends. NetherNet is symmetric, so there is no client and server split.
- `Connection` has `remote_id`, `local_id`, `connection_id` and `state`.
- `connect` resolves once the session is connected and raises `ConnectionFailed(error)` otherwise. A `timeout` that expires and a LAN host that is not found raise `TimeoutError`; error 14 stays with the negotiation timeout (`protocol/session.md`). The LAN host lookup is bounded by the `timeout` argument, and by `negotiation_timeout` when `timeout` is unset.
- `send(data, reliability=SendType.RELIABLE)` raises `ConnectionClosed` unless connected. The framing rules (`protocol/framing.md`) apply unchanged.
- `recv()` returns the next whole packet in FIFO order. After a close it hands out the buffered packets first, then raises `ConnectionClosed(error)`.
- `async for packet in connection` stops when this end closes and raises on an error close. A peer's ABORT reaches this end as `ConnectionClosed(DATA_CHANNEL_CLOSED)` and a peer's stream reset as `ConnectionClosed` with `.error` `None`. A peer that vanishes is noticed only later (`protocol/transport.md`).
- `close()`, which takes no error because the wire carries no reason on close, and `wait_closed() -> SessionError | None`. `Connection` is an async context manager that closes on exit.
- Success is `None`, never a `SessionError` member: `SessionError` holds only real errors, and every surface that reports an outcome is `SessionError | None`, with the wire code 0 mapped at the decode and encode boundary.

## Server and discovery

- `serve` awaits `handler(connection)` once per connection, each in its own task.
- `serve` raises from a `host` or `lan_host` that does not parse or cannot be bound, and from a `lan_port` that is in use. It never falls back to port 0 as BDS does (`protocol/lan-discovery.md`).
- `Server` has `serve_forever`, `aclose`, `set_advertisement`, `local_id`, `bound_port` (HTTP), `lan_port` and `connections`, the open connections. `Server` is an async context manager, and `serve` and `connect` return an awaitable that is one too. `serve_forever` returns on `aclose` or cancellation.
- `discover(timeout=...)` yields each responder once as a frozen `DiscoveredHost(network_id, advertisement, address)` and ends at the deadline. `DiscoveredHost` also reads the advertisement as `name`, `version`, `protocol`, `level`, `players`, `max_players`, `game_type`, `editor`, `hardcore`, `online_auth` and `self_signed_auth`, each `None` when the record does not parse. Break out of `discover` through `contextlib.aclosing`.
- `discover` takes `timeout` (default 2.0 s), `bind_host`, `port`, `broadcast_host`, `broadcast_port` and `application_id`; the 2.0 s deadline and the first four are library options, not BDS settings.
- `serve` advertises the stock BDS values by default: name `Dedicated Server`, level `Bedrock level`, 10 players, game type 0, protocol 2225, version `1.26.60`, `online_auth` and `self_signed_auth` both true. `connection` is a wire constant in `discovery/packets.py`.
- `connect(host)` reuses `host.address` for unicast signaling.
- No public coroutine blocks the loop, and no example polls for a state.

## Errors and identity

- Every public exception derives from `NetherNetError`: `ConnectionFailed`, `ConnectionClosed` (`.error` is `None` when the session closed without an error), and `InvalidIdentity`.
- Exception names carry no `Error` suffix, as in `websockets`.
- Where BDS fails silently at startup, as on a bind or listen error, the library raises instead: the error propagates out of `serve()` and its startup, as with `asyncio.start_server`. What peers see on the wire still matches BDS.
- The library does the identity envelope only (`protocol/identity.md`). Authorizing a player and pinning an operator key are the application's callbacks.
- `validate_offer(offer)` returns a bool: `False` refuses the offer with 37. Any exception from the accept path drops the offer with no reply, so an HTTP client gets the 504 after 10 s (`protocol/http-signaling.md`).
- `on_server_identity(identity)` runs on an HTTP `connect` for the answer's verified operator identity, a `ServerIdentity` with `domain`, `claims`, `public_key` and `key_pin`, sync or async. The identity of an HTTP answer is always verified, with or without the callback, and skipped only over LAN. A raise, or an answer whose identity is missing or fails verification, refuses the answer and the dialer closes with 37. The return value is ignored.

## Ask first

- Renaming a public name or changing a default.
- Splitting `Connection`, or exposing an internal class or hook.
- Adding a dependency, raising the Python floor, or adding a synchronous facade.
- Bounding a receive, incoming or discovery queue, or adding `send` backpressure.
