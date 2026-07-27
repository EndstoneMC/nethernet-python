"""Public NetherNet transport façade.

``Transport`` wires the LAN discovery/signaling plane (:class:`LanTransport`) to the session
plane (:class:`SessionManager`): it advertises a host, discovers hosts, opens outgoing sessions,
and surfaces incoming ones. Application send/recv happens on the :class:`Session` objects.

asyncio-native. Configuration defaults follow SPEC.md s10.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from nethernet.constants import (
    DEFAULT_APPLICATION_ID,
    DEFAULT_BROADCAST_INTERVAL,
    DEFAULT_BROADCAST_PORT,
    DEFAULT_NEGOTIATION_TIMEOUT,
)
from nethernet.discovery.lan import Address, LanTransport
from nethernet.errors import SessionError
from nethernet.network_id import NetworkID, NetworkIDType
from nethernet.session import Session
from nethernet.session_manager import SessionManager


class Transport:
    """Top-level NetherNet endpoint for one local P2P peer."""

    def __init__(
        self,
        local_id: NetworkID,
        *,
        application_id: int = DEFAULT_APPLICATION_ID,
        port: int = DEFAULT_BROADCAST_PORT,
        bind_host: str = "",
        broadcast_host: str = "255.255.255.255",
        broadcast_port: int | None = None,
        broadcast_interval: float = DEFAULT_BROADCAST_INTERVAL,
        negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
        ice_servers: list | None = None,
        relay_only: bool = False,
        advertisement: bytes | None = None,
        on_session: Callable[[Session], None] | None = None,
        on_session_close: Callable[[Session, SessionError], None] | None = None,
        on_packet: Callable[[Session, bytes], None] | None = None,
        on_host_discovered: Callable[[NetworkID, bytes, Address], None] | None = None,
    ) -> None:
        if local_id.type is not NetworkIDType.P2P:
            raise ValueError("LAN transport requires a P2P NetworkID")
        self._local_id = local_id
        self._advertisement = advertisement
        self._on_host_discovered = on_host_discovered
        self._discovered: dict[NetworkID, bytes] = {}
        self._tasks: set[asyncio.Task] = set()

        self._manager = SessionManager(
            local_id=local_id,
            send_signal=self._send_signal,
            on_session_open=on_session,
            on_session_close=on_session_close,
            on_packet=on_packet,
            ice_servers=ice_servers,
            relay_only=relay_only,
            negotiation_timeout=negotiation_timeout,
        )
        self._lan = LanTransport(
            local_id=local_id.value,
            application_id=application_id,
            port=port,
            bind_host=bind_host,
            broadcast_host=broadcast_host,
            broadcast_port=broadcast_port,
            broadcast_interval=broadcast_interval,
            on_signal=self._on_lan_signal,
            on_host_discovered=self._handle_host_discovered,
            get_advertisement=self._get_advertisement,
        )

    # -- lifecycle ---------------------------------------------------------------------

    async def start(self) -> None:
        """Bind the LAN UDP socket."""
        await self._lan.start()

    async def aclose(self) -> None:
        await self._manager.aclose()
        await self._lan.close()

    @property
    def local_id(self) -> NetworkID:
        return self._local_id

    @property
    def bound_port(self) -> int | None:
        return self._lan.bound_port

    # -- discovery / advertisement -----------------------------------------------------

    def set_advertisement(self, advertisement: bytes | None) -> None:
        """Set (or clear) the host advertisement returned when answering Requests."""
        self._advertisement = advertisement

    def start_broadcasting(self) -> None:
        """Periodically broadcast discovery Requests (SPEC.md s7.1)."""
        self._lan.start_broadcasting()

    def remember_peer(self, network_id: NetworkID, addr: Address) -> None:
        """Cache a peer's address for unicast delivery (e.g. a peer learned out of band)."""
        self._lan.remember_peer(network_id, addr)

    async def discover(self, timeout: float = 2.0) -> dict[NetworkID, bytes]:
        """Broadcast a Request and collect host advertisements for ``timeout`` seconds."""
        self._discovered.clear()
        self._lan.broadcast_request()
        await asyncio.sleep(timeout)
        return dict(self._discovered)

    # -- sessions ----------------------------------------------------------------------

    async def connect(self, remote_id: NetworkID) -> Session:
        """Open an outgoing session to ``remote_id`` (SPEC.md s9.1)."""
        return await self._manager.connect(remote_id)

    # -- wiring ------------------------------------------------------------------------

    def _get_advertisement(self) -> bytes | None:
        return self._advertisement

    def _send_signal(self, remote_id: NetworkID, message: str) -> None:
        self._lan.send_signal(remote_id, message)

    def _on_lan_signal(self, sender_id: NetworkID, message: str) -> None:
        # LAN delivery is a sync callback; signaling dispatch is async, so schedule it.
        task = asyncio.create_task(self._manager.handle_signal(sender_id, message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _handle_host_discovered(self, network_id: NetworkID, data: bytes, addr: Address) -> None:
        self._discovered[network_id] = data
        if self._on_host_discovered is not None:
            self._on_host_discovered(network_id, data, addr)
