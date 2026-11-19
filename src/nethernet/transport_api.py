"""The internal endpoint that feeds LAN and HTTP signaling into one session manager."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from nethernet.constants import (
    DEFAULT_APPLICATION_ID,
    DEFAULT_BROADCAST_INTERVAL,
    DEFAULT_BROADCAST_PORT,
    DEFAULT_NEGOTIATION_TIMEOUT,
)
from nethernet.discovery.lan import LIMITED_BROADCAST, Address, LanTransport
from nethernet.errors import ConnectionFailed, ConnectionFlags, SessionError
from nethernet.network_id import NetworkID, new_connection_id
from nethernet.session import Session
from nethernet.session_manager import Accept, SessionManager
from nethernet.signaling.http import (
    DEFAULT_SERVER_PORT,
    HttpSignalingServer,
    request_answer,
)


class Transport:
    """One local endpoint: LAN discovery and signaling, optional HTTP signaling, and sessions.

    Args:
        local_id: The endpoint's own network id.
        application_id: The LAN discovery application id.
        lan_host: The LAN socket bind address, empty for dual stack.
        lan_port: The LAN discovery and signaling UDP port.
        broadcast_host: The limited broadcast address for discovery requests.
        broadcast_port: The port discovery requests go to, ``lan_port`` when None.
        broadcast_interval: Seconds between broadcast discovery requests.
        lan: Whether the LAN socket is bound at all.
        visible: Whether the endpoint answers LAN discovery requests.
        http_host: The HTTP signaling bind address, empty for dual stack, None for no server.
        http_client: Whether sessions may be dialed over HTTP signaling.
        http_port: The HTTP signaling TCP port.
        ping: Builds the body of ``GET /v1/join``.
        get_advertisement: Returns the host advertisement for each discovery request.
        ice_servers: ICE servers for every session.
        negotiation_timeout: Seconds a dialer waits for an answer.
        accept: Decides each incoming offer or answer.
        connection_flags: Returns the connection flags for a remote.
        identity: Signs each answer sent over HTTP.
        on_session_open: Called when a session connects.
        on_session_close: Called when a session closes.
        on_packet: Called for each received packet.
        on_host_discovered: Called for each discovery response.
    """

    def __init__(
        self,
        local_id: NetworkID,
        *,
        application_id: int = DEFAULT_APPLICATION_ID,
        lan: bool = True,
        lan_host: str = "",
        lan_port: int = DEFAULT_BROADCAST_PORT,
        broadcast_host: str = LIMITED_BROADCAST,
        broadcast_port: int | None = None,
        broadcast_interval: float = DEFAULT_BROADCAST_INTERVAL,
        visible: bool = True,
        http_host: str | None = None,
        http_client: bool = False,
        http_port: int = DEFAULT_SERVER_PORT,
        ping: Callable[[], dict[str, object] | None] | None = None,
        get_advertisement: Callable[[], bytes | None] | None = None,
        ice_servers: list | None = None,
        negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
        accept: Accept | None = None,
        connection_flags: Callable[[NetworkID], ConnectionFlags] | None = None,
        identity: Callable[[str], str] | None = None,
        on_session_open: Callable[[Session], None] | None = None,
        on_session_close: Callable[[Session, SessionError, str], None] | None = None,
        on_packet: Callable[[Session, bytes], None] | None = None,
        on_host_discovered: Callable[[NetworkID, bytes, Address], None] | None = None,
    ) -> None:
        self._lan_enabled = lan
        self._on_host_discovered = on_host_discovered
        self._negotiation_timeout = negotiation_timeout
        self._bases: dict[NetworkID, str] = {}
        self._tasks: set[asyncio.Task] = set()
        self._closing = False
        self._lan = LanTransport(
            local_id=local_id.value,
            application_id=application_id,
            port=lan_port,
            bind_host=lan_host,
            broadcast_host=broadcast_host,
            broadcast_port=broadcast_port,
            broadcast_interval=broadcast_interval,
            visible=visible,
            on_signal=self._on_lan_signal,
            on_host_discovered=on_host_discovered,
            get_advertisement=get_advertisement,
        )
        self._http = (
            None
            if http_host is None
            else HttpSignalingServer(
                self._on_http_signal, host=http_host, port=http_port, ping=ping
            )
        )
        self._manager = SessionManager(
            local_id=local_id,
            lan_send=self._lan.send_signal,
            http_send=self._http_send if http_client or http_host is not None else None,
            is_lan_peer=self._lan.is_on_lan,
            ice_servers=ice_servers,
            negotiation_timeout=negotiation_timeout,
            accept=accept,
            connection_flags=connection_flags,
            identity=identity,
            on_session_open=on_session_open,
            on_session_close=on_session_close,
            on_packet=on_packet,
        )

    @property
    def local_id(self) -> NetworkID:
        """The endpoint's own network id."""
        return self._manager.local_id

    @property
    def lan_port(self) -> int | None:
        """The bound LAN UDP port."""
        return self._lan.bound_port

    @property
    def http_port(self) -> int | None:
        """The bound HTTP signaling TCP port, None without a server."""
        return None if self._http is None else self._http.bound_port

    async def start(self) -> None:
        """Bind the sockets, raising the bind error after releasing what was bound."""
        try:
            if self._http is not None:
                await self._http.start()
            if self._lan_enabled:
                await self._lan.start()
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        """Close every session and socket."""
        if self._closing:
            return
        self._closing = True
        await self._manager.aclose()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._http is not None:
            await self._http.aclose()
        await self._lan.close()

    def add_host(self, network_id: NetworkID, address: Address) -> None:
        """Pin the LAN address of a host."""
        self._lan.add_host(network_id, *address)

    def enable_broadcast(self) -> None:
        """Broadcast discovery requests under the local id."""
        self._lan.start_broadcasting()

    def broadcast_request(self) -> None:
        """Send one discovery request."""
        self._lan.broadcast_request()

    def dial_lan(
        self, remote_id: NetworkID, *, identity: Callable[[str], str] | None = None
    ) -> Session:
        """Open a session to a LAN host."""
        return self._manager.dial(remote_id, identity=identity)

    def dial_http(self, base_url: str, *, identity: Callable[[str], str] | None = None) -> Session:
        """Open a session to the host behind an HTTP signaling base URL."""
        remote_id = NetworkID.p2p(new_connection_id())
        self._bases[remote_id] = base_url
        return self._manager.dial(remote_id, identity=identity)

    def _on_lan_signal(self, sender: NetworkID, message: str) -> None:
        self._spawn(self._manager.handle_signal(sender, message, via_lan=True))

    async def _on_http_signal(self, sender: NetworkID, message: str) -> None:
        await self._manager.handle_signal(sender, message, via_lan=False)

    async def _http_send(self, remote: NetworkID, message: str) -> SessionError:
        base = self._bases.get(remote)
        if base is None:
            if self._http is not None and self._http.send(remote, message):
                return SessionError.NONE
            return SessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED
        try:
            response = await request_answer(
                base, self.local_id, message, timeout=self._negotiation_timeout
            )
        except ConnectionFailed as exc:
            return exc.error
        if response is not None:
            await self._manager.handle_signal(remote, response.serialize(), via_lan=False)
        return SessionError.NONE

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
