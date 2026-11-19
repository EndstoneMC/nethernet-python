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

- Three entry points, never split by signaling channel. There is no `serve_http` or `connect_http`, and they need no deprecation before 0.1.0.
- `serve(handler, ...)` is the only server entry point. One call serves HTTP signaling on `host`:`port` and LAN signaling on `lan_host`:`lan_port` together, answers discovery with `connection` 4 and does not broadcast. Its defaults come from `protocol/config.md`.
- `connect(target, ...)` is the only client entry point. The target picks the channel: a `NetworkID` or `DiscoveredHost` goes over LAN, a string, `host`, `host:port` or an `http(s)://` URL, over HTTP. An HTTP connect signs with a fresh self-signed `PlayerSigner` unless `identity_signer` is given.
- `discover(...)` is the LAN discovery call.

## Connection

- One `Connection` class serves both ends. NetherNet is symmetric, so there is no client and server split.
- `Connection` has `remote_id`, `local_id`, `connection_id` and `state`.
- `connect` resolves once the session is connected and raises `ConnectionFailed(error)` otherwise. A `timeout` that expires and a LAN host that is not found raise `TimeoutError`; error 14 stays with the negotiation timeout (`protocol/session.md`).
- `send(data, reliability=SendType.RELIABLE)` raises `ConnectionClosed` unless connected. The framing rules (`protocol/framing.md`) apply unchanged.
- `recv()` returns the next whole packet in FIFO order. After a close it hands out the buffered packets first, then raises `ConnectionClosed(error)`.
- `async for packet in connection` stops on a clean close and raises on an error close.
- `close(error=SessionError.NONE)` and `wait_closed() -> SessionError`. `Connection` is an async context manager that closes on exit.

## Server and discovery

- `serve` awaits `handler(connection)` once per connection, each in its own task.
- `serve` raises from a `host` or `lan_host` that does not parse or cannot be bound.
- `Server` has `serve_forever`, `aclose`, `set_advertisement`, `local_id`, `bound_port` (HTTP), `lan_port` and `connections`, the open connections. `Server` is an async context manager, and `serve` and `connect` return an awaitable that is one too. `serve_forever` returns on `aclose` or cancellation.
- `discover(timeout=...)` yields each responder once as a frozen `DiscoveredHost(network_id, advertisement, address)` and ends at the deadline. `DiscoveredHost` also reads the advertisement as `name`, `version`, `protocol`, `level`, `players`, `max_players`, `game_type`, `editor`, `hardcore`, `online_auth` and `self_signed_auth`, each `None` when the record does not parse. Break out of `discover` through `contextlib.aclosing`.
- `discover` takes `timeout` (default 2.0 s), `bind_host`, `port`, `broadcast_host`, `broadcast_port` and `application_id`; the 2.0 s deadline and the first four are library options, not BDS settings.
- `serve` advertises the stock BDS values by default: name `Dedicated Server`, level `Bedrock level`, 10 players, game type 0. `connection` is a wire constant in `discovery/packets.py`.
- `connect(host)` reuses `host.address` for unicast signaling.
- No public coroutine blocks the loop, and no example polls for a state.

## Errors and identity

- Every public exception derives from `NetherNetError`: `ConnectionFailed`, `ConnectionClosed` (`.error` is `NONE` on a clean close), and `InvalidIdentity`.
- Exception names carry no `Error` suffix, as in `websockets`.
- Where BDS fails silently at startup, as on a bind or listen error, the library raises instead: the error propagates out of `serve()` and its startup, as with `asyncio.start_server`. What peers see on the wire still matches BDS.
- The library accepts unknown query parameters for forward compatibility.
- The library does the identity envelope only (`protocol/identity.md`). Authorizing a player (`validate_offer`, a raise refuses the offer) and pinning an operator key (`on_server_identity`) are the application's callbacks.

## Ask first

- Renaming a public name or changing a default.
- Splitting `Connection`, or exposing an internal class or hook.
- Adding a dependency, raising the Python floor, or adding a synchronous facade.
- Bounding a receive, incoming or discovery queue, or adding `send` backpressure.
