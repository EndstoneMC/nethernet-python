"""Public async API — see docs/api-design.md.

Module-level ``connect()`` / ``serve()`` / ``discover()`` returning a single ``Connection``
(both roles; NetherNet is symmetric P2P) and a ``Server``. This mirrors how BDS wraps NetherNet
(a ``Connector`` producing per-connection ``NetworkPeer`` objects), expressed in idiomatic
asyncio (websockets-style): ``await send`` / ``await recv`` / ``async for`` / ``async with``.

It is a thin layer over the internal :class:`~nethernet.transport_api.Transport` engine: a
``Future`` resolves the connect/accept, and an ``asyncio.Queue`` per connection carries received
packets. The byte-exact protocol code underneath is untouched.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from nethernet.constants import (
    DEFAULT_APPLICATION_ID,
    DEFAULT_BROADCAST_PORT,
    DEFAULT_NEGOTIATION_TIMEOUT,
)
from nethernet.discovery.lan import Address
from nethernet.errors import (
    ConnectionClosed,
    ConnectionFailed,
    ESendType,
    ESessionError,
)
from nethernet.network_id import NetworkID, new_connection_id
from nethernet.session import Session, SessionState
from nethernet.transport_api import Transport

# Cap on packets buffered for an incoming connection before its Connection object exists (an app
# packet can in principle arrive between a channel opening and both channels being open).
_MAX_PENDING_PACKETS = 256


@dataclass(frozen=True)
class DiscoveredHost:
    """A host found via LAN discovery (docs/api-design.md s3.4)."""

    network_id: NetworkID
    advertisement: bytes  # opaque application data (SPEC.md s7.3 Response)
    address: Address  # source address; connect() reuses it for unicast signaling


class _Closed:
    """Sentinel queued on a connection's receive queue to wake a waiting ``recv()`` on close."""


_CLOSED = _Closed()


class Connection:
    """One connection to a peer (BDS ``WebRTCNetworkPeer``); a single class for both roles."""

    def __init__(
        self, session: Session, endpoint: _Endpoint, *, owns_endpoint: bool = False
    ) -> None:
        self._session = session
        self._endpoint = endpoint
        self._owns_endpoint = owns_endpoint
        self._recv_queue: asyncio.Queue[bytes | _Closed] = asyncio.Queue()
        self._connected: asyncio.Future[None] = asyncio.get_event_loop().create_future()
        self._connected.add_done_callback(self._consume_connected)
        self._closed = asyncio.Event()
        self._closed_error: ESessionError | None = None

    # -- identity (SPEC.md s3, s9) -----------------------------------------------------

    @property
    def remote_id(self) -> NetworkID:
        return self._session.remote_id

    @property
    def local_id(self) -> NetworkID:
        return self._session.local_id

    @property
    def connection_id(self) -> int:
        return self._session.connection_id

    @property
    def state(self) -> SessionState:
        return self._session.state

    # -- message I/O (SPEC.md s6.2-s6.3) -----------------------------------------------

    async def send(self, data: bytes, reliability: ESendType = ESendType.RELIABLE) -> None:
        """Send an application packet; raises :class:`ConnectionClosed` if not connected."""
        if self._session.state is not SessionState.CONNECTED:
            raise ConnectionClosed(self._closed_error or ESessionError.NONE)
        self._session.send(data, reliability)

    async def recv(self) -> bytes:
        """Return the next complete application packet (SPEC.md s6.4 FIFO).

        Raises :class:`ConnectionClosed` once the connection is closed and the queue is drained.
        """
        item = await self._recv_queue.get()
        if isinstance(item, _Closed):
            self._recv_queue.put_nowait(_CLOSED)  # sticky: subsequent recv() also raises
            raise ConnectionClosed(self._closed_error or ESessionError.NONE)
        return item

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self.recv()
        except ConnectionClosed as exc:
            if exc.error == ESessionError.NONE:
                raise StopAsyncIteration from None
            raise

    # -- lifecycle (SPEC.md s9.5) ------------------------------------------------------

    async def close(self, error: ESessionError = ESessionError.NONE) -> None:
        await self._session.aclose(error)
        if self._owns_endpoint:
            await self._endpoint.aclose()

    async def wait_closed(self) -> ESessionError:
        await self._closed.wait()
        return self._closed_error or ESessionError.NONE

    async def __aenter__(self) -> Connection:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # -- internal: driven by the endpoint's engine callbacks ----------------------------

    def _mark_connected(self) -> None:
        if not self._connected.done():
            self._connected.set_result(None)

    def _mark_closed(self, error: ESessionError) -> None:
        self._closed_error = error
        if not self._connected.done():
            self._connected.set_exception(ConnectionFailed(error))
        self._recv_queue.put_nowait(_CLOSED)
        self._closed.set()

    def _enqueue(self, data: bytes) -> None:
        self._recv_queue.put_nowait(data)

    async def _await_connected(self, timeout: float | None) -> None:
        if timeout is None:
            await self._connected
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._connected), timeout)
        except TimeoutError:
            raise ConnectionFailed(
                ESessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE
            ) from None

    @staticmethod
    def _consume_connected(fut: asyncio.Future[None]) -> None:
        # Retrieve any exception so a connect-timeout abandonment never logs "never retrieved".
        if not fut.cancelled():
            fut.exception()


class _Endpoint:
    """Internal: wraps one :class:`Transport`, routing engine callbacks to per-connection objects.

    BDS analog: ``NetherNetConnector`` (owns the ``connId -> peer`` map and the event queue).
    """

    def __init__(self, local_id: NetworkID, **transport_cfg: object) -> None:
        self._conns: dict[int, Connection] = {}
        self._pending_packets: dict[int, list[bytes]] = {}
        self._incoming: asyncio.Queue[Connection] = asyncio.Queue()
        self._discovered: asyncio.Queue[DiscoveredHost] = asyncio.Queue()
        self._transport = Transport(
            local_id,
            on_session=self._on_session_open,
            on_session_close=self._on_session_close,
            on_packet=self._on_packet,
            on_host_discovered=self._on_host_discovered,
            **transport_cfg,  # type: ignore[arg-type]
        )

    # -- lifecycle ---------------------------------------------------------------------

    async def start(self) -> None:
        await self._transport.start()

    async def aclose(self) -> None:
        for conn in list(self._conns.values()):
            conn._mark_closed(conn._closed_error or ESessionError.NONE)
        await self._transport.aclose()

    @property
    def bound_port(self) -> int | None:
        return self._transport.bound_port

    @property
    def local_id(self) -> NetworkID:
        return self._transport.local_id

    def start_broadcasting(self) -> None:
        self._transport.start_broadcasting()

    def broadcast_request(self) -> None:
        self._transport._lan.broadcast_request()

    def set_advertisement(self, advertisement: bytes | None) -> None:
        self._transport.set_advertisement(advertisement)

    async def connect(self, remote_id: NetworkID, *, timeout: float | None) -> Connection:
        session = await self._transport.connect(remote_id)
        conn = self._register(session, owns_endpoint=True)
        await conn._await_connected(timeout)
        return conn

    def remember_peer(self, network_id: NetworkID, addr: Address) -> None:
        self._transport.remember_peer(network_id, addr)

    # -- engine callbacks --------------------------------------------------------------

    def _register(self, session: Session, *, owns_endpoint: bool) -> Connection:
        conn = Connection(session, self, owns_endpoint=owns_endpoint)
        self._conns[session.connection_id] = conn
        for data in self._pending_packets.pop(session.connection_id, []):
            conn._enqueue(data)
        return conn

    def _on_session_open(self, session: Session) -> None:
        conn = self._conns.get(session.connection_id)
        if conn is None:  # incoming connection
            conn = self._register(session, owns_endpoint=False)
            self._incoming.put_nowait(conn)
        conn._mark_connected()

    def _on_session_close(self, session: Session, error: ESessionError) -> None:
        conn = self._conns.pop(session.connection_id, None)
        if conn is not None:
            conn._mark_closed(error)

    def _on_packet(self, session: Session, data: bytes) -> None:
        conn = self._conns.get(session.connection_id)
        if conn is not None:
            conn._enqueue(data)
            return
        buffer = self._pending_packets.setdefault(session.connection_id, [])
        if len(buffer) < _MAX_PENDING_PACKETS:
            buffer.append(data)

    def _on_host_discovered(self, network_id: NetworkID, data: bytes, addr: Address) -> None:
        self._discovered.put_nowait(DiscoveredHost(network_id, data, addr))


class Server:
    """Accepts incoming connections, one ``handler`` task each (BDS ServerNetherNetConnector)."""

    def __init__(
        self,
        endpoint: _Endpoint,
        handler: Callable[[Connection], Awaitable[None]],
        *,
        broadcast: bool,
    ) -> None:
        self._endpoint = endpoint
        self._handler = handler
        self._broadcast = broadcast
        self._accept_task: asyncio.Task | None = None
        self._handler_tasks: set[asyncio.Task] = set()
        self._closed = asyncio.Event()

    async def start(self) -> Server:
        await self._endpoint.start()
        if self._broadcast:
            self._endpoint.start_broadcasting()
        self._accept_task = asyncio.create_task(self._accept_loop())
        return self

    async def _accept_loop(self) -> None:
        while True:
            conn = await self._endpoint._incoming.get()
            task = asyncio.create_task(self._run_handler(conn))
            self._handler_tasks.add(task)
            task.add_done_callback(self._handler_tasks.discard)

    async def _run_handler(self, conn: Connection) -> None:
        try:
            await self._handler(conn)
        except ConnectionClosed:
            pass  # remote disconnect is a normal end of a handler
        finally:
            await conn.close()

    async def serve_forever(self) -> None:
        await self._closed.wait()

    async def aclose(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if self._accept_task is not None:
            self._accept_task.cancel()
        for task in list(self._handler_tasks):
            task.cancel()
        await self._endpoint.aclose()

    def set_advertisement(self, advertisement: bytes | None) -> None:
        self._endpoint.set_advertisement(advertisement)

    @property
    def local_id(self) -> NetworkID:
        return self._endpoint.local_id

    @property
    def bound_port(self) -> int | None:
        return self._endpoint.bound_port

    @property
    def connections(self) -> list[Connection]:
        return list(self._endpoint._conns.values())

    async def __aenter__(self) -> Server:
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


# --- module-level entry points (docs/api-design.md s3.1) ---


async def connect(
    remote: NetworkID | DiscoveredHost,
    *,
    local_id: NetworkID | None = None,
    application_id: int = DEFAULT_APPLICATION_ID,
    port: int = DEFAULT_BROADCAST_PORT,
    bind_host: str = "",
    broadcast_host: str = "255.255.255.255",
    broadcast_port: int | None = None,
    ice_servers: list | None = None,
    relay_only: bool = False,
    negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
    timeout: float | None = None,
) -> Connection:
    """Dial a remote peer and return a connected :class:`Connection`.

    BDS analog: ``ClientNetherNetConnector``. Resolves once the session is CONNECTED; raises
    :class:`ConnectionFailed` on negotiation timeout / ICE failure.
    """
    endpoint = _Endpoint(
        local_id or NetworkID.p2p(new_connection_id()),
        application_id=application_id,
        port=port,
        bind_host=bind_host,
        broadcast_host=broadcast_host,
        broadcast_port=broadcast_port,
        ice_servers=ice_servers,
        relay_only=relay_only,
        negotiation_timeout=negotiation_timeout,
    )
    await endpoint.start()
    try:
        if isinstance(remote, DiscoveredHost):
            endpoint.remember_peer(remote.network_id, remote.address)
            remote_id = remote.network_id
        else:
            remote_id = remote
        return await endpoint.connect(remote_id, timeout=timeout)
    except BaseException:
        await endpoint.aclose()
        raise


def serve(
    handler: Callable[[Connection], Awaitable[None]],
    local_id: NetworkID,
    *,
    advertisement: bytes | None = None,
    application_id: int = DEFAULT_APPLICATION_ID,
    port: int = DEFAULT_BROADCAST_PORT,
    bind_host: str = "",
    broadcast_host: str = "255.255.255.255",
    broadcast_port: int | None = None,
    broadcast: bool = True,
    ice_servers: list | None = None,
    relay_only: bool = False,
    negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
) -> Server:
    """Accept incoming connections (BDS ``ServerNetherNetConnector::host``).

    ``handler(connection)`` is awaited once per connection in its own task. Returns a
    :class:`Server` async context manager; ``await server.serve_forever()`` runs until closed.
    """
    endpoint = _Endpoint(
        local_id,
        advertisement=advertisement,
        application_id=application_id,
        port=port,
        bind_host=bind_host,
        broadcast_host=broadcast_host,
        broadcast_port=broadcast_port,
        ice_servers=ice_servers,
        relay_only=relay_only,
        negotiation_timeout=negotiation_timeout,
    )
    return Server(endpoint, handler, broadcast=broadcast)


async def discover(
    *,
    local_id: NetworkID | None = None,
    application_id: int = DEFAULT_APPLICATION_ID,
    port: int = DEFAULT_BROADCAST_PORT,
    bind_host: str = "",
    broadcast_host: str = "255.255.255.255",
    broadcast_port: int | None = None,
    timeout: float = 2.0,
) -> AsyncIterator[DiscoveredHost]:
    """Broadcast a Request and async-iterate discovered hosts (BDS ``NetherNetServerLocator``).

    Yields each distinct responder as its Response arrives, for up to ``timeout`` seconds.
    """
    endpoint = _Endpoint(
        local_id or NetworkID.p2p(new_connection_id()),
        application_id=application_id,
        port=port,
        bind_host=bind_host,
        broadcast_host=broadcast_host,
        broadcast_port=broadcast_port,
    )
    await endpoint.start()
    seen: set[NetworkID] = set()
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    try:
        endpoint.broadcast_request()
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            try:
                host = await asyncio.wait_for(endpoint._discovered.get(), timeout=remaining)
            except TimeoutError:
                return
            if host.network_id in seen:
                continue
            seen.add(host.network_id)
            yield host
    finally:
        await endpoint.aclose()
