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
import inspect
import ssl
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
from nethernet.identity import (
    IdentityEnvelope,
    IdentitySigner,
    ServerIdentity,
    extract_identity,
    strip_identity,
    verify_server_identity,
)
from nethernet.network_id import NetworkID, new_connection_id
from nethernet.session import Session, SessionState
from nethernet.signaling.http import HttpSignalingServer, request_answer
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
    """One connection to a peer (BDS ``WebRTCNetworkPeer``); a single class for both roles.

    ``endpoint`` is None for HTTP-signaled connections, which have no LAN transport to own.
    """

    def __init__(
        self, session: Session, endpoint: _Endpoint | None, *, owns_endpoint: bool = False
    ) -> None:
        self._session = session
        self._endpoint = endpoint
        self._owns_endpoint = owns_endpoint
        self._recv_queue: asyncio.Queue[bytes | _Closed] = asyncio.Queue()
        self._connected: asyncio.Future[None] = asyncio.get_event_loop().create_future()
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
            self._connected.exception()  # mark retrieved; awaiting the future still raises
        self._recv_queue.put_nowait(_CLOSED)
        self._closed.set()

    def _enqueue(self, data: bytes) -> None:
        self._recv_queue.put_nowait(data)

    async def _await_connected(self, timeout: float | None) -> None:
        # timeout=None blocks until the session opens (or raises ConnectionFailed if it closes
        # first); a timeout cancels the wait and reports the negotiation timeout.
        try:
            await asyncio.wait_for(self._connected, timeout)
        except TimeoutError:
            raise ConnectionFailed(
                ESessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE
            ) from None


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
        # Closing the transport closes every live session, which marks each Connection closed
        # through the on_session_close callback below — no separate sweep needed here.
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


# --- HTTP signaling (NetherNet Onboarding Guide s4) — connect_http() / serve_http() ---


@dataclass(frozen=True)
class IncomingOffer:
    """An offer received over HTTP signaling, before any answer is generated."""

    network_id_text: str  # raw URL path segment; treat as opaque (Guide s9)
    network_id: NetworkID  # best-effort parsed form (may be unset)
    sdp: str  # offer SDP with a=identity already stripped
    identity: IdentityEnvelope | None  # client assertion; validating it is the app's policy


def _make_http_session(
    *,
    local_id: NetworkID,
    remote_id: NetworkID,
    is_dialer: bool,
    on_open: Callable[[Connection], None],
    on_close: Callable[[Connection, ESessionError], None],
    ice_servers: list | None,
    relay_only: bool,
    negotiation_timeout: float,
) -> Connection:
    """A Session/Connection pair with no LAN transport (full ICE: nothing to trickle)."""
    slot: list[Connection] = []
    session = Session(
        connection_id=new_connection_id(),
        local_id=local_id,
        remote_id=remote_id,
        is_dialer=is_dialer,
        send_signal=lambda message: None,
        on_open=lambda session: on_open(slot[0]),
        on_close=lambda session, error: on_close(slot[0], error),
        on_packet=lambda session, data: slot[0]._enqueue(data),
        ice_servers=ice_servers,
        relay_only=relay_only,
        negotiation_timeout=negotiation_timeout,
    )
    conn = Connection(session, None)
    slot.append(conn)
    return conn


async def connect_http(
    server_url: str,
    *,
    local_id: NetworkID | None = None,
    ice_servers: list | None = None,
    relay_only: bool = False,
    negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
    capability_check: bool = True,
    on_server_identity: Callable[[ServerIdentity], Awaitable[None] | None] | None = None,
    timeout: float | None = None,
) -> Connection:
    """Dial a host through HTTP signaling (Onboarding Guide s4, s7).

    Full ICE: the complete offer goes in one ``POST {server_url}/v1/join/{local_id}`` and the
    answer comes back in the response — no trickle, no LAN discovery. Requires the ``http``
    extra (aiohttp).

    ``on_server_identity`` receives the structurally verified server assertion (Guide s5.2);
    trust policy — TLS vs TOFU pinning of ``key_digest`` — is the caller's, and raising from
    the callback aborts the connection. Without it, any ``a=identity`` is stripped unverified.
    """
    local = local_id or NetworkID.p2p(new_connection_id())
    conn = _make_http_session(
        local_id=local,
        remote_id=NetworkID.unset(),
        is_dialer=True,
        on_open=Connection._mark_connected,
        on_close=Connection._mark_closed,
        ice_servers=ice_servers,
        relay_only=relay_only,
        negotiation_timeout=negotiation_timeout,
    )
    session = conn._session
    try:
        offer_sdp = await session.offer()
        answer_sdp = await request_answer(
            server_url,
            str(local),
            offer_sdp,
            capability_check=capability_check,
            timeout=negotiation_timeout,
        )
        if on_server_identity is not None:
            answer_sdp, server_identity = verify_server_identity(answer_sdp)
            result = on_server_identity(server_identity)
            if inspect.isawaitable(result):
                await result
        else:
            answer_sdp = strip_identity(answer_sdp)
        await session.accept_answer(answer_sdp)
        await conn._await_connected(timeout)
        return conn
    except BaseException:
        await session.aclose(ESessionError.GENERIC_FAILURE)
        raise


class HttpServer:
    """Accepts connections via HTTP signaling; one ``handler`` task per connection.

    The partner-flow analog of :class:`Server`: no LAN discovery or broadcast — clients learn
    the signaling URL out of band (Guide s3).
    """

    def __init__(
        self,
        handler: Callable[[Connection], Awaitable[None]],
        local_id: NetworkID,
        *,
        host: str,
        port: int,
        ssl_context: ssl.SSLContext | None,
        identity_signer: IdentitySigner | None,
        validate_offer: Callable[[IncomingOffer], Awaitable[None] | None] | None,
        ice_servers: list | None,
        relay_only: bool,
        negotiation_timeout: float,
    ) -> None:
        self._handler = handler
        self._local_id = local_id
        self._identity_signer = identity_signer
        self._validate_offer = validate_offer
        self._ice_servers = ice_servers
        self._relay_only = relay_only
        self._negotiation_timeout = negotiation_timeout
        self._signaling = HttpSignalingServer(
            self._answer_offer, host=host, port=port, ssl_context=ssl_context
        )
        self._conns: dict[int, Connection] = {}
        self._handler_tasks: set[asyncio.Task] = set()
        self._closed = asyncio.Event()

    async def start(self) -> HttpServer:
        await self._signaling.start()
        return self

    # -- signaling callback ------------------------------------------------------------

    async def _answer_offer(self, network_id_text: str, offer_sdp: str) -> str:
        offer_sdp, envelope = extract_identity(offer_sdp)  # malformed -> InvalidIdentity -> 400
        remote_id = NetworkID.parse(network_id_text)
        if self._validate_offer is not None:
            result = self._validate_offer(
                IncomingOffer(network_id_text, remote_id, offer_sdp, envelope)
            )
            if inspect.isawaitable(result):
                await result
        conn = _make_http_session(
            local_id=self._local_id,
            remote_id=remote_id,
            is_dialer=False,
            on_open=self._handle_open,
            on_close=self._handle_close,
            ice_servers=self._ice_servers,
            relay_only=self._relay_only,
            negotiation_timeout=self._negotiation_timeout,
        )
        self._conns[conn.connection_id] = conn
        try:
            answer_sdp = await conn._session.answer(offer_sdp)
        except BaseException:
            self._conns.pop(conn.connection_id, None)
            await conn._session.aclose(ESessionError.FAILED_TO_CREATE_ANSWER)
            raise
        if self._identity_signer is not None:
            answer_sdp = self._identity_signer.sign(answer_sdp)
        return answer_sdp

    def _handle_open(self, conn: Connection) -> None:
        conn._mark_connected()
        task = asyncio.create_task(self._run_handler(conn))
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_tasks.discard)

    def _handle_close(self, conn: Connection, error: ESessionError) -> None:
        self._conns.pop(conn.connection_id, None)
        conn._mark_closed(error)

    async def _run_handler(self, conn: Connection) -> None:
        try:
            await self._handler(conn)
        except ConnectionClosed:
            pass  # remote disconnect is a normal end of a handler
        finally:
            await conn.close()

    # -- lifecycle ---------------------------------------------------------------------

    async def serve_forever(self) -> None:
        await self._closed.wait()

    async def aclose(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        await self._signaling.aclose()
        # Close sessions first so handlers end via ConnectionClosed; then reap stragglers.
        for conn in list(self._conns.values()):
            await conn._session.aclose()
        self._conns.clear()
        for task in list(self._handler_tasks):
            task.cancel()
        await asyncio.gather(*self._handler_tasks, return_exceptions=True)

    @property
    def local_id(self) -> NetworkID:
        return self._local_id

    @property
    def bound_port(self) -> int | None:
        return self._signaling.bound_port

    @property
    def connections(self) -> list[Connection]:
        return list(self._conns.values())

    async def __aenter__(self) -> HttpServer:
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


def serve_http(
    handler: Callable[[Connection], Awaitable[None]],
    local_id: NetworkID | None = None,
    *,
    host: str = "0.0.0.0",
    port: int = 0,
    ssl_context: ssl.SSLContext | None = None,
    identity_signer: IdentitySigner | None = None,
    validate_offer: Callable[[IncomingOffer], Awaitable[None] | None] | None = None,
    ice_servers: list | None = None,
    relay_only: bool = False,
    negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
) -> HttpServer:
    """Accept connections via HTTP signaling (Onboarding Guide s4). Requires ``nethernet[http]``.

    ``handler(connection)`` is awaited once per connection in its own task. ``validate_offer``
    is the authorization hook — it sees the raw network id, the stripped offer SDP, and the
    client's identity envelope (validating the token is the application's policy); raise
    :class:`~nethernet.signaling.http.SignalingRejected` to refuse. ``identity_signer`` adds
    the server assertion that real Minecraft clients require on every answer (Guide s5.2).
    """
    return HttpServer(
        handler,
        local_id or NetworkID.p2p(new_connection_id()),
        host=host,
        port=port,
        ssl_context=ssl_context,
        identity_signer=identity_signer,
        validate_offer=validate_offer,
        ice_servers=ice_servers,
        relay_only=relay_only,
        negotiation_timeout=negotiation_timeout,
    )
