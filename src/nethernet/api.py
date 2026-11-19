"""The public asyncio API: ``serve``, ``connect`` and ``discover``."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Generator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from nethernet.constants import (
    DEFAULT_APPLICATION_ID,
    DEFAULT_BROADCAST_PORT,
    DEFAULT_NEGOTIATION_TIMEOUT,
)
from nethernet.discovery.lan import LIMITED_BROADCAST, Address
from nethernet.discovery.packets import (
    LAN_SIGNALING_CONNECTION,
    AdvertisementNonce,
    HostAdvertisement,
)
from nethernet.errors import (
    ConnectionClosed,
    ConnectionFailed,
    ConnectionFlags,
    SendType,
    SessionError,
)
from nethernet.identity import (
    IdentityEnvelope,
    IdentitySigner,
    PlayerSigner,
    ServerIdentity,
    extract_identity,
    generate_operator_key,
    verify_player_identity,
    verify_server_identity,
)
from nethernet.network_id import NetworkID, new_connection_id
from nethernet.session import Session, SessionState
from nethernet.session_manager import AcceptRequest
from nethernet.signaling.http import DEFAULT_SERVER_PORT, resolve_base_url
from nethernet.transport_api import Transport

MAX_PENDING_PACKETS = 256

T = TypeVar("T")


@dataclass(frozen=True)
class DiscoveredHost:
    """A host found by LAN discovery.

    Attributes:
        network_id: The host's network id.
        advertisement: The raw advertisement bytes the host answered with.
        address: The address the answer came from, reused for signaling.
    """

    network_id: NetworkID
    advertisement: bytes
    address: Address

    def _info(self, field: str) -> Any:
        info = HostAdvertisement.parse(self.advertisement)
        return None if info is None else getattr(info, field)

    @property
    def name(self) -> str | None:
        """The server name. None when the advertisement does not parse."""
        return self._info("name")

    @property
    def version(self) -> str | None:
        """The game version. None when the advertisement does not parse."""
        return self._info("version")

    @property
    def protocol(self) -> int | None:
        """The protocol version. None when the advertisement does not parse."""
        return self._info("protocol")

    @property
    def level(self) -> str | None:
        """The level name. None when the advertisement does not parse."""
        return self._info("level")

    @property
    def players(self) -> int | None:
        """The number of players online. None when the advertisement does not parse."""
        return self._info("players")

    @property
    def max_players(self) -> int | None:
        """The player limit. None when the advertisement does not parse."""
        return self._info("max_players")

    @property
    def game_type(self) -> int | None:
        """The level's game type. None when the advertisement does not parse."""
        return self._info("game_type")

    @property
    def editor(self) -> bool | None:
        """Whether the world is an editor world. None when the advertisement does not parse."""
        return self._info("editor")

    @property
    def hardcore(self) -> bool | None:
        """Whether the world is hardcore. None when the advertisement does not parse."""
        return self._info("hardcore")

    @property
    def online_auth(self) -> bool | None:
        """Whether online authentication is on. None when the advertisement does not parse."""
        return self._info("online_auth")

    @property
    def self_signed_auth(self) -> bool | None:
        """Whether self-signed authentication is on. None when the advertisement does not parse."""
        return self._info("self_signed_auth")


@dataclass(frozen=True)
class IncomingOffer:
    """An offer received over HTTP signaling, before any answer is made.

    Attributes:
        network_id_text: The dialer's network id in decimal.
        network_id: The dialer's network id.
        sdp: The offer SDP with ``a=identity`` stripped.
        identity: The dialer's identity envelope, None when the offer has none.
    """

    network_id_text: str
    network_id: NetworkID
    sdp: str
    identity: IdentityEnvelope | None


class _Opening(Generic[T]):
    def __init__(self, coroutine: Awaitable[T]) -> None:
        self._coroutine = coroutine
        self._value: Any = None

    def __await__(self) -> Generator[Any, None, T]:
        return self._coroutine.__await__()

    async def __aenter__(self) -> T:
        self._value = await self._coroutine
        return self._value

    async def __aexit__(self, *exc: object) -> None:
        await self._value.__aexit__(*exc)


class _Closed:
    pass


_CLOSED = _Closed()


class Connection:
    """One connection to a peer, the same class on both ends.

    Use it as an async context manager to close it on exit, and iterate it to receive packets
    until a clean close.

    Attributes:
        local_id: This end's network id.
    """

    def __init__(
        self, session: Session, local_id: NetworkID, owner: Transport | None = None
    ) -> None:
        self.local_id = local_id
        self._session = session
        self._owner = owner
        self._queue: asyncio.Queue[bytes | _Closed] = asyncio.Queue()
        self._connected: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._closed = asyncio.Event()
        self._error: SessionError | None = None
        self._cleanup: asyncio.Task | None = None

    @property
    def remote_id(self) -> NetworkID:
        """The peer's network id."""
        return self._session.remote_id

    @property
    def connection_id(self) -> int:
        """The connection id both ends agreed on."""
        return self._session.connection_id

    @property
    def state(self) -> SessionState:
        """The session's connecting, active or errored state."""
        return self._session.state

    async def send(self, data: bytes, reliability: SendType = SendType.RELIABLE) -> None:
        """Send one packet.

        Args:
            data: The whole packet.
            reliability: Whether delivery is reliable and ordered.

        Raises:
            ConnectionClosed: The connection is not open.
        """
        opened = self._connected.done() and not self._connected.cancelled()
        if not opened or self._closed.is_set() or not self._session.send(data, reliability):
            raise ConnectionClosed(self._error or SessionError.NONE)

    async def recv(self) -> bytes:
        """Return the next whole packet in arrival order.

        Raises:
            ConnectionClosed: The connection closed and every buffered packet was returned.
        """
        item = await self._queue.get()
        if isinstance(item, _Closed):
            self._queue.put_nowait(_CLOSED)
            raise ConnectionClosed(self._error or SessionError.NONE)
        return item

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self.recv()
        except ConnectionClosed as exc:
            if exc.error == SessionError.NONE:
                raise StopAsyncIteration from None
            raise

    async def close(self, error: SessionError = SessionError.NONE) -> None:
        """Close the connection.

        Args:
            error: The reason reported to this end only, the peer is not told.
        """
        self._session.close(error)
        await self._session.wait_closed()
        if self._owner is not None:
            await self._owner.aclose()

    async def wait_closed(self) -> SessionError:
        """Wait for the close and return its error, ``NONE`` for a clean close."""
        await self._closed.wait()
        return self._error or SessionError.NONE

    async def __aenter__(self) -> Connection:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _mark_connected(self) -> None:
        if not self._connected.done():
            self._connected.set_result(None)

    def _mark_closed(self, error: SessionError) -> None:
        self._error = error
        if not self._connected.done():
            self._connected.set_exception(ConnectionFailed(error))
            self._connected.exception()
        self._queue.put_nowait(_CLOSED)
        self._closed.set()
        if self._owner is not None and self._cleanup is None:
            self._cleanup = asyncio.ensure_future(self._owner.aclose())

    async def _await_connected(self, timeout: float | None) -> None:
        await asyncio.wait_for(self._connected, timeout)


class _Endpoint:
    def __init__(
        self,
        local_id: NetworkID,
        *,
        on_change: Callable[[], None] | None = None,
        **config: Any,
    ) -> None:
        self.conns: dict[Session, Connection] = {}
        self.incoming: asyncio.Queue[Connection] = asyncio.Queue()
        self.discovered: asyncio.Queue[DiscoveredHost] = asyncio.Queue()
        self._pending: dict[Session, list[bytes]] = {}
        self._on_change = on_change
        self.transport = Transport(
            local_id,
            on_session_open=self._on_open,
            on_session_close=self._on_close,
            on_packet=self._on_packet,
            on_host_discovered=self._on_host_discovered,
            **config,
        )

    def register(self, session: Session, owner: Transport | None = None) -> Connection:
        conn = Connection(session, self.transport.local_id, owner)
        self.conns[session] = conn
        for data in self._pending.pop(session, []):
            conn._queue.put_nowait(data)
        return conn

    def _on_open(self, session: Session) -> None:
        conn = self.conns.get(session)
        if conn is None:
            conn = self.register(session)
            self.incoming.put_nowait(conn)
        conn._mark_connected()
        if self._on_change is not None:
            self._on_change()

    def _on_close(self, session: Session, error: SessionError, summary: str) -> None:
        self._pending.pop(session, None)
        conn = self.conns.pop(session, None)
        if conn is not None:
            conn._mark_closed(error)
            if self._on_change is not None:
                self._on_change()

    def _on_packet(self, session: Session, data: bytes) -> None:
        conn = self.conns.get(session)
        if conn is not None:
            conn._queue.put_nowait(data)
            return
        buffer = self._pending.setdefault(session, [])
        if len(buffer) < MAX_PENDING_PACKETS:
            buffer.append(data)

    def _on_host_discovered(self, network_id: NetworkID, data: bytes, address: Address) -> None:
        self.discovered.put_nowait(DiscoveredHost(network_id, data, address))


class Server:
    """A running host that accepts connections over HTTP and LAN signaling.

    Use it as an async context manager to close it on exit.
    """

    def __init__(
        self,
        handler: Callable[[Connection], Awaitable[None]],
        local_id: NetworkID,
        *,
        fields: dict[str, Any],
        broadcast: bool,
        visible: bool,
        **config: Any,
    ) -> None:
        self._handler = handler
        self._fields = fields
        self._broadcast = broadcast
        self._visible = visible
        self._nonce = AdvertisementNonce()
        self._advertisement = b""
        self._ping: dict[str, object] | None = None
        self._accept_task: asyncio.Task | None = None
        self._handlers: set[asyncio.Task] = set()
        self._closed = asyncio.Event()
        self._endpoint = _Endpoint(
            local_id,
            on_change=self._refresh,
            visible=visible,
            get_advertisement=lambda: self._advertisement,
            ping=lambda: self._ping,
            **config,
        )

    @property
    def local_id(self) -> NetworkID:
        """The host's network id."""
        return self._endpoint.transport.local_id

    @property
    def bound_port(self) -> int | None:
        """The bound HTTP signaling TCP port."""
        return self._endpoint.transport.http_port

    @property
    def lan_port(self) -> int | None:
        """The bound LAN UDP port."""
        return self._endpoint.transport.lan_port

    @property
    def connections(self) -> list[Connection]:
        """The open connections."""
        return list(self._endpoint.conns.values())

    def set_advertisement(self, **changes: Any) -> None:
        """Change the advertised fields, such as ``name``, ``level`` or ``max_players``.

        Raises:
            TypeError: A field is not an advertisement field.
        """
        unknown = set(changes) - set(self._fields)
        if unknown:
            raise TypeError(f"unknown advertisement fields: {sorted(unknown)}")
        self._fields.update(changes)
        self._refresh()

    async def serve_forever(self) -> None:
        """Wait until the server closes."""
        await self._closed.wait()

    async def aclose(self) -> None:
        """Close the server and every connection."""
        if self._closed.is_set():
            return
        self._closed.set()
        if self._accept_task is not None:
            self._accept_task.cancel()
        for task in list(self._handlers):
            task.cancel()
        await self._endpoint.transport.aclose()
        await asyncio.gather(*self._handlers, return_exceptions=True)

    async def __aenter__(self) -> Server:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _start(self) -> Server:
        self._refresh()
        await self._endpoint.transport.start()
        if self._broadcast:
            self._endpoint.transport.enable_broadcast()
        self._accept_task = asyncio.create_task(self._accept_loop())
        return self

    def _refresh(self) -> None:
        advertisement = HostAdvertisement(
            **self._fields,
            players=len(self._endpoint.conns),
            nonce=self._nonce.update(),
            connection=LAN_SIGNALING_CONNECTION,
        )
        self._advertisement = advertisement.serialize()
        self._ping = None
        if self._visible:
            self._ping = {
                "name": advertisement.name,
                "protocol": advertisement.protocol,
                "version": advertisement.version,
                "level": advertisement.level,
                "players": advertisement.players,
                "maxPlayers": advertisement.max_players,
                "gameType": advertisement.game_type,
            }

    async def _accept_loop(self) -> None:
        while True:
            conn = await self._endpoint.incoming.get()
            task = asyncio.create_task(self._run_handler(conn))
            self._handlers.add(task)
            task.add_done_callback(self._handlers.discard)

    async def _run_handler(self, conn: Connection) -> None:
        try:
            await self._handler(conn)
        except ConnectionClosed:
            pass
        finally:
            await conn.close()


def serve(
    handler: Callable[[Connection], Awaitable[None]],
    local_id: NetworkID | None = None,
    *,
    host: str = "",
    port: int = DEFAULT_SERVER_PORT,
    lan_host: str = "",
    lan_port: int = DEFAULT_BROADCAST_PORT,
    application_id: int = DEFAULT_APPLICATION_ID,
    name: str = "Dedicated Server",
    level: str = "Bedrock level",
    max_players: int = 10,
    game_type: int = 0,
    editor: bool = False,
    hardcore: bool = False,
    online_auth: bool = False,
    self_signed_auth: bool = False,
    visible: bool = True,
    broadcast: bool = False,
    broadcast_host: str = LIMITED_BROADCAST,
    broadcast_port: int | None = None,
    ice_servers: list | None = None,
    negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
    flags: ConnectionFlags = ConnectionFlags.NONE,
    identity_signer: IdentitySigner | None = None,
    validate_offer: Callable[[IncomingOffer], Awaitable[None] | None] | None = None,
) -> _Opening[Server]:
    """Start a host that serves HTTP signaling and LAN signaling together.

    Await it, or use it as an async context manager, to get the running ``Server``. A bind
    address that does not parse or cannot be bound raises here.

    Args:
        handler: Awaited once per connection, each in its own task.
        local_id: The host's network id, random when None.
        host: The HTTP signaling bind address, empty for dual stack.
        port: The HTTP signaling TCP port.
        lan_host: The LAN socket bind address, empty for dual stack.
        lan_port: The LAN discovery and signaling UDP port.
        application_id: The LAN discovery application id.
        name: The advertised server name.
        level: The advertised level name.
        max_players: The advertised player limit.
        game_type: The advertised game type.
        editor: Whether the advertised world is an editor world.
        hardcore: Whether the advertised world is hardcore.
        online_auth: Whether the advertisement says online authentication is on.
        self_signed_auth: Whether the advertisement says self-signed authentication is on.
        visible: Whether the host answers LAN discovery requests and ``GET /v1/join``.
        broadcast: Whether the host broadcasts discovery requests itself.
        broadcast_host: The limited broadcast address for those requests.
        broadcast_port: The port those requests go to, ``lan_port`` when None.
        ice_servers: ICE servers for every connection.
        negotiation_timeout: Seconds a session waits for its peer.
        flags: Connection flags applied to every remote.
        identity_signer: Signs each HTTP answer, a fresh operator key when None.
        validate_offer: Called for each HTTP offer once its identity verifies, raise to refuse it.

    Returns:
        An awaitable that resolves to the running ``Server``.

    Raises:
        OSError: The bind address cannot be bound.
        socket.gaierror: The bind address does not parse.
    """
    signer = identity_signer or IdentitySigner(generate_operator_key())

    async def accept(request: AcceptRequest) -> bool:
        if not request.is_offer or request.via_lan:
            return True
        try:
            verify_player_identity(request.sdp)
            if validate_offer is not None:
                stripped, envelope = extract_identity(request.sdp)
                sender = request.remote_id
                result = validate_offer(IncomingOffer(str(sender), sender, stripped, envelope))
                if inspect.isawaitable(result):
                    await result
        except Exception:
            return False
        return True

    server = Server(
        handler,
        local_id or NetworkID.p2p(new_connection_id()),
        fields={
            "name": name,
            "level": level,
            "max_players": max_players,
            "game_type": game_type,
            "editor": editor,
            "hardcore": hardcore,
            "online_auth": online_auth,
            "self_signed_auth": self_signed_auth,
        },
        broadcast=broadcast,
        visible=visible,
        application_id=application_id,
        lan_host=lan_host,
        lan_port=lan_port,
        broadcast_host=broadcast_host,
        broadcast_port=broadcast_port,
        http_host=host,
        http_port=port,
        ice_servers=ice_servers,
        negotiation_timeout=negotiation_timeout,
        connection_flags=lambda remote: flags,
        accept=accept,
        identity=signer.sign,
    )
    return _Opening(server._start())


async def _connect(
    target: str | NetworkID | DiscoveredHost,
    *,
    local_id: NetworkID | None,
    application_id: int,
    port: int,
    bind_host: str,
    broadcast_host: str,
    broadcast_port: int | None,
    ice_servers: list | None,
    negotiation_timeout: float,
    flags: ConnectionFlags,
    timeout: float | None,
    identity_signer: PlayerSigner | None,
    on_server_identity: Callable[[ServerIdentity], Awaitable[None] | None] | None,
) -> Connection:
    over_http = isinstance(target, str)
    base_url = await resolve_base_url(target) if isinstance(target, str) else None

    async def accept(request: AcceptRequest) -> bool:
        if request.is_offer or request.via_lan or on_server_identity is None:
            return True
        try:
            _, identity = verify_server_identity(request.sdp)
            result = on_server_identity(identity)
            if inspect.isawaitable(result):
                await result
        except Exception:
            return False
        return True

    endpoint = _Endpoint(
        local_id or NetworkID.p2p(new_connection_id()),
        lan=not over_http,
        http_client=over_http,
        visible=False,
        application_id=application_id,
        lan_host=bind_host,
        lan_port=port,
        broadcast_host=broadcast_host,
        broadcast_port=broadcast_port,
        ice_servers=ice_servers,
        negotiation_timeout=negotiation_timeout,
        connection_flags=lambda remote: flags,
        accept=accept,
    )
    transport = endpoint.transport
    await transport.start()
    try:
        signer = identity_signer or (PlayerSigner.self_signed() if over_http else None)
        sign = signer.sign if signer is not None else None
        if base_url is not None:
            session = transport.dial_http(base_url, identity=sign)
        else:
            remote_id = await _locate(endpoint, target, timeout or negotiation_timeout)
            session = transport.dial_lan(remote_id, identity=sign)
        conn = endpoint.register(session, transport)
        await conn._await_connected(timeout)
        return conn
    except BaseException:
        await transport.aclose()
        raise


async def _locate(endpoint: _Endpoint, target: NetworkID | DiscoveredHost, timeout: float):
    transport = endpoint.transport
    if isinstance(target, DiscoveredHost):
        transport.add_host(target.network_id, target.address)
        return target.network_id
    transport.broadcast_request()
    async with asyncio.timeout(timeout):
        while True:
            host = await endpoint.discovered.get()
            if host.network_id == target:
                transport.add_host(host.network_id, host.address)
                return host.network_id


def connect(
    target: str | NetworkID | DiscoveredHost,
    *,
    local_id: NetworkID | None = None,
    application_id: int = DEFAULT_APPLICATION_ID,
    port: int = DEFAULT_BROADCAST_PORT,
    bind_host: str = "",
    broadcast_host: str = LIMITED_BROADCAST,
    broadcast_port: int | None = None,
    ice_servers: list | None = None,
    negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
    flags: ConnectionFlags = ConnectionFlags.NONE,
    timeout: float | None = None,
    identity_signer: PlayerSigner | None = None,
    on_server_identity: Callable[[ServerIdentity], Awaitable[None] | None] | None = None,
) -> _Opening[Connection]:
    """Connect to a host and resolve once the connection is open.

    A string target is an HTTP signaling address, ``host``, ``host:port`` or an ``http://`` or
    ``https://`` URL. A ``NetworkID`` or ``DiscoveredHost`` is reached over LAN signaling.

    Args:
        target: The host to connect to.
        local_id: This end's network id, random when None.
        application_id: The LAN discovery application id.
        port: The LAN UDP port to bind.
        bind_host: The LAN socket bind address, empty for dual stack.
        broadcast_host: The limited broadcast address for discovery requests.
        broadcast_port: The port discovery requests go to, ``port`` when None.
        ice_servers: ICE servers for the connection.
        negotiation_timeout: Seconds to wait for the host's answer.
        flags: Connection flags for the connection.
        timeout: Seconds to wait for the connection to open, unbounded when None.
        identity_signer: Signs the offer over HTTP, a fresh self-signed player when None.
        on_server_identity: Called with the host's verified operator identity, raise to refuse.

    Returns:
        An awaitable that resolves to the open ``Connection``, also an async context manager.

    Raises:
        ConnectionFailed: No signaling channel answered or the connection did not open.
        TimeoutError: The host was not found or the connection did not open within ``timeout``.
    """
    return _Opening(
        _connect(
            target,
            local_id=local_id,
            application_id=application_id,
            port=port,
            bind_host=bind_host,
            broadcast_host=broadcast_host,
            broadcast_port=broadcast_port,
            ice_servers=ice_servers,
            negotiation_timeout=negotiation_timeout,
            flags=flags,
            timeout=timeout,
            identity_signer=identity_signer,
            on_server_identity=on_server_identity,
        )
    )


async def discover(
    *,
    application_id: int = DEFAULT_APPLICATION_ID,
    port: int = DEFAULT_BROADCAST_PORT,
    bind_host: str = "",
    broadcast_host: str = LIMITED_BROADCAST,
    broadcast_port: int | None = None,
    timeout: float = 2.0,
) -> AsyncIterator[DiscoveredHost]:
    """Find hosts on the LAN, yielding each one once until the deadline.

    Args:
        application_id: The LAN discovery application id.
        port: The LAN UDP port to bind.
        bind_host: The LAN socket bind address, empty for dual stack.
        broadcast_host: The limited broadcast address for the request.
        broadcast_port: The port the request goes to, ``port`` when None.
        timeout: Seconds to listen for answers.

    Yields:
        Each host that answered.
    """
    endpoint = _Endpoint(
        NetworkID.p2p(new_connection_id()),
        visible=False,
        application_id=application_id,
        lan_host=bind_host,
        lan_port=port,
        broadcast_host=broadcast_host,
        broadcast_port=broadcast_port,
    )
    await endpoint.transport.start()
    seen: set[NetworkID] = set()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        endpoint.transport.broadcast_request()
        while (remaining := deadline - loop.time()) > 0:
            try:
                host = await asyncio.wait_for(endpoint.discovered.get(), remaining)
            except TimeoutError:
                return
            if host.network_id not in seen:
                seen.add(host.network_id)
                yield host
    finally:
        await endpoint.transport.aclose()
