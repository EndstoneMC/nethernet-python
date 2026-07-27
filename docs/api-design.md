# NetherNet Public API Design

**Status:** Draft / approved-shape · **Scope:** the *public async API surface* only
**Relationship to `SPEC.md`:** `SPEC.md` defines the **wire protocol** (observable bytes &
message ordering) and is authoritative and untouched by anything here. This document defines
the **Python API** layered over that protocol. Where behavior is observable on the wire, this
doc defers to `SPEC.md` by section (`§`).

---

## 1. Objective

Give `nethernet` an idiomatic asyncio public API **modeled on how Bedrock Dedicated Server
(BDS) itself wraps NetherNet** — a per-connection object obtained from module-level
`connect()` / `serve()` functions — rather than exposing the raw transport engine. The public
surface should read like `websockets`: `await`-based, message-oriented, context-managed.

### 1.1 What BDS actually exposes (verified in IDA, `bedrock_server.exe` 1.26.20)

BDS does **not** hand its netcode a raw transport. It exposes two abstractions:

```
game netcode
   │ programs against
   ▼
NetworkPeer (abstract; vtbl = 9 vfuncs)         ← per-connection object
   ├── WebRTCNetworkPeer        (NetherNet impl)
   ├── RakNetConnector::RakNetNetworkPeer
   └── decorators: Encrypted / Compressed / Batched / Latency / PacketTrace …
   ▲ produced & owned by
Connector (abstract) + Connector::ConnectionCallbacks
   └── NetherNetConnector  (keyed by ⟨NetherNet::NetworkID, connectionId⟩)
        ├── ClientNetherNetConnector      ← client: dials out
        └── ServerNetherNetConnector      ← server: host(ConnectionDefinition)
        │ drives
        ▼
   NetherNet::INetherNetTransportInterface          ← the library == our SPEC
```

Observed method shapes (demangled):

- `NetworkPeer` (the per-connection object): `sendPacket(std::string, Reliability,
  Compressibility)`, `_receivePacket(std::string&, …) -> DataStatus`,
  `getNetworkStatus() -> NetworkStatus`, `update()`.
- `NetherNetConnector` (the engine): `sendPacket / isPacketAvailable / readPacket /
  getSessionState / closeSessionWithUser` (each keyed by `⟨NetworkID, connId⟩`),
  `runEvents()` draining `NewIncomingConnectionEvent / NewOutgoingConnectionEvent /
  DisconnectEvent`, `setBroadcastRequestCallback` (supply advertisement when answering a
  discovery Request) + `setBroadcastResponseCallback` (receive responses).
- `ServerNetherNetConnector::host(ConnectionDefinition)` — lightweight: enables trickle-ICE per
  the definition and starts the event queue; the actual accepting is the event pump + signaling.
- `NetherNetServerLocator` — the LAN "find servers" locator on the client side.

**Key insight.** BDS's API is *polled* (`runEvents()`, `isPacketAvailable`+`readPacket`,
`update()`) because it runs on the game tick. The faithful **asyncio** translation of that
exact shape is `await recv()` / `async for` / `await serve_forever()`. So mirroring BDS and
being idiomatic asyncio are the same design.

**Target users:** Python 3.11+ asyncio app authors (e.g. Endstone) integrating LAN NetherNet.
Roles: both dialer (client) and listener (server).

---

## 2. Design principle — layer, don't rewrite

The existing callback engine (`SessionManager`, `PeerConnection`, `LanTransport`, and the
current `Transport` façade) is kept **internal** and becomes the analog of BDS's
`NetherNetConnector`. The public `await`/`async for` surface is a thin skin over it using a
`Future` (connect/accept completion) and `asyncio.Queue` (per-connection receive, incoming
connections, discovery) — the same way `asyncio.streams` wraps its own callback core.

```
public:   connect() / serve() / discover()  ·  Connection  ·  Server      (this doc)
              │  Future + asyncio.Queue
internal:  Transport · SessionManager · Session · PeerConnection · LanTransport  (== NetherNetConnector)
              │  bytes
wire:      SPEC.md §5–§8                                                   (== INetherNetTransportInterface)
```

Naming note — the public per-connection class is a single **`Connection`**, *not* split into
`ClientConnection`/`ServerConnection`. `websockets` splits those only because HTTP is
client/server asymmetric; NetherNet is symmetric peer-to-peer (the same `Session` drives both
the dialer and listener roles, §9), so one class is the more honest model. It sits cleanly
above the internal WebRTC-level `PeerConnection`, so there is no name collision.

The byte-exact protocol code and its 114 passing tests are not disturbed.

---

## 3. Public surface

Exported from `nethernet`. `Transport` and the other engine classes are **not** part of the
public surface (importable for advanced use, but undocumented and unstable).

### 3.1 Module-level entry points

```python
async def connect(
    remote: NetworkID | DiscoveredHost,
    *,
    local_id: NetworkID | None = None,         # default: a fresh random P2P id
    application_id: int = DEFAULT_APPLICATION_ID,
    port: int = DEFAULT_BROADCAST_PORT,
    ice_servers: list | None = None,
    relay_only: bool = False,
    negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
) -> Connection: ...
#  Dial a remote peer (== ClientNetherNetConnector). Resolves once CONNECTED; raises
#  ConnectionFailed(error) on timeout/ICE failure. The result is an async context manager.

def serve(
    handler: Callable[[Connection], Awaitable[None]],
    local_id: NetworkID,
    *,
    advertisement: bytes | None = None,        # host advertisement (§7.3 Response payload)
    application_id: int = DEFAULT_APPLICATION_ID,
    port: int = DEFAULT_BROADCAST_PORT,
    broadcast: bool = True,                     # periodically advertise (§7.1)
    ice_servers: list | None = None,
    relay_only: bool = False,
    negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
) -> Server: ...
#  Accept incoming connections (== ServerNetherNetConnector::host). `handler(connection)` is
#  awaited once per connection in its own task. Returns a Server (await it, or `async with`).

def discover(
    *,
    local_id: NetworkID | None = None,
    application_id: int = DEFAULT_APPLICATION_ID,
    port: int = DEFAULT_BROADCAST_PORT,
    timeout: float = 2.0,
) -> AsyncIterator[DiscoveredHost]: ...
#  Broadcast a Request and async-iterate host advertisements (== NetherNetServerLocator).
```

### 3.2 `Connection` — one connection to a peer (BDS `NetworkPeer` / `WebRTCNetworkPeer`)

A single class for both roles (dialer and listener); NetherNet is symmetric P2P.

```python
class Connection:
    @property remote_id: NetworkID
    @property local_id: NetworkID
    @property connection_id: int
    @property state: SessionState

    async def __aenter__(self) -> "Connection"
    async def __aexit__(self, *exc) -> None                       # close()

    async def send(self, data: bytes, reliability: SendType = SendType.RELIABLE) -> None
    async def recv(self) -> bytes                                 # next complete packet (§6.4)
    def __aiter__(self) -> AsyncIterator[bytes]                   # async for packet in connection
    async def __anext__(self) -> bytes

    async def close(self, error: SessionError = SessionError.NONE) -> None
    async def wait_closed(self) -> SessionError
```

Semantics:
- `send()` (== `NetworkPeer::sendPacket`) raises `ConnectionClosed` if not `CONNECTED`.
  Coroutine for idiom + future SCTP backpressure; §6.3 oversize/unreliable-multi rules unchanged.
- `recv()` (== `_receivePacket`) returns the next FIFO packet (§6.4); after close **and** the
  queue is drained it raises `ConnectionClosed(error)`. Buffered packets are delivered first.
- `async for packet in connection` stops cleanly on a clean close; propagates on an error close.

### 3.3 `Server` (BDS `ServerNetherNetConnector`)

```python
class Server:
    async def __aenter__(self) -> "Server"
    async def __aexit__(self, *exc) -> None                       # aclose()
    async def serve_forever(self) -> None                         # run until cancelled/closed
    async def aclose(self) -> None                                # stop accepting, close connections
    def set_advertisement(self, advertisement: bytes | None) -> None
    @property local_id: NetworkID
    @property bound_port: int | None
    @property connections: list[Connection]                       # currently-open connections
```

### 3.4 `DiscoveredHost`

```python
@dataclass(frozen=True)
class DiscoveredHost:
    network_id: NetworkID
    advertisement: bytes      # opaque application data (§7.3 Response)
    address: Address          # source addr; connect() reuses it for unicast signaling
```

### 3.5 Exceptions

```python
class NetherNetError(Exception): ...
class ConnectionFailed(NetherNetError): error: SessionError   # connect() never reached CONNECTED
class ConnectionClosed(NetherNetError): error: SessionError   # send()/recv() on a closed connection
```

`error` carries the `SessionError` (§4): timeout 14/15, ICE 5, DataChannelClosed 32, ….
(`ConnectionClosed` mirrors the `websockets` exception of the same name; a clean close carries
`SessionError.NONE`.)

### 3.6 Re-exported as-is

`NetworkID`, `NetworkIDType`, `SessionState`, `SendType`, `SessionError`,
`ConnectionFlags`, `new_connection_id`, `Address`, `__version__`.

---

## 4. Canonical usage — `examples/echo_server.py` and `examples/echo_client.py`

```python
# examples/echo_server.py
import asyncio, secrets, nethernet
from nethernet import NetworkID, SendType

async def handler(conn):                         # called once per incoming connection
    async with conn:
        async for packet in conn:                # echo
            await conn.send(packet, SendType.RELIABLE)

async def main():
    local_id = NetworkID.p2p(secrets.randbits(64))
    async with nethernet.serve(handler, local_id,
                               advertisement=b"MCPE;NetherNet-Python;...") as server:
        await server.serve_forever()

asyncio.run(main())
```

```python
# examples/echo_client.py
import asyncio, nethernet
from nethernet import SendType

async def main():
    async for host in nethernet.discover(timeout=3):
        print("found", host.network_id, host.advertisement)
        async with await nethernet.connect(host) as conn:
            await conn.send(b"hello", SendType.RELIABLE)
            print("reply:", await conn.recv())
        break

asyncio.run(main())
```

Compare `websockets` (`serve(handler, …)` + `await server.serve_forever()`;
`async with connect(uri) as ws: await ws.send(...); await ws.recv()`): the shapes are
intentionally identical; NetherNet adds the `SendType` reliability arg and LAN `discover()`,
and uses one `Connection` class for both ends.

---

## 5. Commands

| Purpose | Command |
|--------|---------|
| Run tests | `uv run pytest -q` |
| Conformance subset | `uv run pytest -m conformance` |
| Lint | `uv run ruff check` |
| Server example | `uv run python examples/echo_server.py` |
| Client example | `uv run python examples/echo_client.py` |

---

## 6. Project structure (delta only)

```
src/nethernet/
  api.py (new)        # connect() / serve() / discover(); Connection; Server; DiscoveredHost
  transport_api.py    # Transport — demoted to internal engine; api.py builds over it
  session.py          # feed _on_packet into a per-connection asyncio.Queue (Connection.recv source)
  errors.py           # add NetherNetError, ConnectionFailed, ConnectionClosed
  __init__.py         # export connect/serve/discover, Connection, Server, DiscoveredHost, enums;
                      #   stop exporting Transport from the public surface
examples/
  echo_server.py (new)  # replaces host.py — serve() + handler
  echo_client.py (new)  # replaces join.py — discover() + connect()
docs/api-design.md    # this document
tests/test_api.py     # public-surface behavior tests (see §8)
```

Engine modules (`session_manager.py`, `transport/`, `discovery/`, codecs) are not restructured.
`examples/host.py` / `examples/join.py` are removed in favor of `server.py` / `client.py`.

---

## 7. Code style

- asyncio-native; **Python 3.11+**; no sync wrapper this round (out of scope per project decisions).
- `from __future__ import annotations`; full type hints; keyword-only configuration args.
- PEP8 naming, `UPPER_SNAKE_CASE` enum members; **ruff clean** (enforced). (Note: we deliberately
  do *not* mirror BDS/aiortc camelCase — `sendPacket` → `send`, `getSessionState` → `state`.)
- Enum classes drop the C++ `E` prefix `SPEC.md` carries: `ESessionError` → `SessionError`,
  `ESendType` → `SendType`, `EConnectionFlags` → `ConnectionFlags`. `SPEC.md` keeps the original
  names, since it documents the C++ protocol.
- Resource-closing coroutines named `close`/`aclose`; objects provide `__aenter__`/`__aexit__`.
- Docstrings cite the governing `SPEC.md` section for wire-observable behavior, and name the
  BDS analog where it clarifies intent (e.g. "== WebRTCNetworkPeer::sendPacket").
- Public surface is exactly §3; everything else is internal and may change.

---

## 8. Testing strategy

Keep the existing **114 tests green** (engine unchanged). Add public-surface tests
(in-process / real-UDP loopback, no external peer):

1. `connect()` resolves exactly when CONNECTED; raises `ConnectionFailed` (error 14/15 timeout, 5 ICE).
2. `serve(handler, …)` invokes `handler(connection)` once per incoming connection; concurrent
   incoming connections each get their own handler task; `connections` reflects live ones.
3. `Connection.recv()` returns complete packets FIFO (§6.4); a large multi-fragment reliable
   packet (§6.3) reassembles into one `recv()`.
4. `Connection.recv()` drains buffered packets, then raises `ConnectionClosed` (clean `NONE` vs
   error); `async for packet in connection` stops cleanly / propagates on error.
5. `discover(timeout=t)` async-iterates `DiscoveredHost`s, de-dupes responders, ends at deadline.
6. `connect(DiscoveredHost)` reuses the discovered address for signaling.
7. `async with nethernet.serve(...)` / `async with connection` close cleanly on exception;
   `server.serve_forever()` returns on `aclose()`/cancel.
8. `examples/echo_server.py` + `examples/echo_client.py` import-and-smoke; no poll loops remain.

Verification base: `uv run pytest -q` green + `uv run ruff check` clean.

---

## 9. Boundaries

**Always**
- Treat `SPEC.md` as authoritative; never change wire format/ordering/semantics for API
  convenience. Cite `§` for wire-observable behavior.
- Preserve the callback engine and its byte-exact codecs/tests; build the public API over them.
- Keep `connect()`/`serve()`/`discover()`/`recv()` non-blocking on the event loop.
- ruff-clean, PEP8, type-hinted; snake_case (no camelCase mirroring of BDS/aiortc).

**Ask first**
- Renaming a public name in §3 (`Connection`, `Server`, `connect`/`serve`/`discover`) or changing a default.
- Splitting `Connection` into client/server classes, or exposing `Transport` / any engine hook.
- Adding a dependency, bumping minimum Python, or adding a sync facade.
- Bounding the receive/incoming/discovery queues (drop policy) or adding `send` backpressure.

**Never**
- Re-introduce poll loops (`while state != CONNECTED: sleep`) into examples or the public API.
- Block the event loop with synchronous network/crypto in a public coroutine.
- Break `uv run pytest -m conformance` or the §11 conformance guarantees.
- Overwrite or fork `SPEC.md` from this document.
```