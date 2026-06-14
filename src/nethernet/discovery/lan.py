"""LAN discovery + LAN signaling transport — SPEC.md s7-s8.

A UDP endpoint that broadcasts discovery Requests, answers them with the application's host
advertisement, surfaces discovered hosts, and carries signaling messages inside encrypted
discovery Message packets (the LAN binding of the abstract signaling channel, SPEC.md s5.1).
Every datagram is sealed/opened with the AES-256-ECB + HMAC envelope (Task 6).

IPv4 broadcast is implemented; IPv6 all-hosts link-local (SPEC.md s7.1) is a follow-up.

The protocol logic (build/seal/dispatch) is independent of the socket: tests drive
``datagram_received`` directly with a captured transport.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable
from typing import TypeAlias

from nethernet.constants import (
    DEFAULT_APPLICATION_ID,
    DEFAULT_BROADCAST_INTERVAL,
    DEFAULT_BROADCAST_PORT,
)
from nethernet.discovery.crypto import Envelope
from nethernet.discovery.packets import (
    DiscoveryMessage,
    DiscoveryRequest,
    DiscoveryResponse,
)
from nethernet.discovery.packets import (
    parse as parse_discovery,
)
from nethernet.network_id import NetworkID

Address: TypeAlias = tuple[str, int]


class LanTransport(asyncio.DatagramProtocol):
    """UDP discovery + LAN signaling for one local peer (SPEC.md s7.4)."""

    def __init__(
        self,
        *,
        local_id: int,
        application_id: int = DEFAULT_APPLICATION_ID,
        port: int = DEFAULT_BROADCAST_PORT,
        bind_host: str = "",
        broadcast_host: str = "255.255.255.255",
        broadcast_interval: float = DEFAULT_BROADCAST_INTERVAL,
        on_signal: Callable[[NetworkID, str], None] | None = None,
        on_host_discovered: Callable[[NetworkID, bytes], None] | None = None,
        get_advertisement: Callable[[], bytes | None] | None = None,
    ) -> None:
        self._local_id = local_id
        self._envelope = Envelope(application_id)
        self._port = port
        self._bind_host = bind_host
        self._broadcast_addr: Address = (broadcast_host, port)
        self._broadcast_interval = broadcast_interval
        self._on_signal = on_signal
        self._on_host_discovered = on_host_discovered
        self._get_advertisement = get_advertisement

        self._transport: asyncio.DatagramTransport | None = None
        self._peers: dict[int, Address] = {}
        self._broadcast_task: asyncio.Task | None = None
        self._bound_port: int | None = None

    # -- asyncio.DatagramProtocol ------------------------------------------------------

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Address) -> None:
        self._on_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        # Ignore transient errors (e.g. ICMP port-unreachable on broadcast).
        pass

    # -- lifecycle ---------------------------------------------------------------------

    async def start(self) -> None:
        """Bind the UDP socket (SO_REUSEADDR + SO_BROADCAST) and begin receiving."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((self._bind_host, self._port))
        self._bound_port = sock.getsockname()[1]
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda: self, sock=sock)

    @property
    def bound_port(self) -> int | None:
        return self._bound_port

    def start_broadcasting(self) -> None:
        """Begin periodically broadcasting discovery Requests (SPEC.md s7.1)."""
        if self._broadcast_task is None:
            self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    async def _broadcast_loop(self) -> None:
        while True:
            self.broadcast_request()
            await asyncio.sleep(self._broadcast_interval)

    async def close(self) -> None:
        if self._broadcast_task is not None:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    # -- sending -----------------------------------------------------------------------

    def broadcast_request(self) -> None:
        """Broadcast a discovery Request to solicit responses (SPEC.md s7.3)."""
        self._send_packet(DiscoveryRequest(self._local_id), self._broadcast_addr)

    def send_signal(self, remote_id: NetworkID, message: str) -> None:
        """Send a signaling string to ``remote_id`` inside a Message packet (SPEC.md s7.4)."""
        packet = DiscoveryMessage(self._local_id, remote_id.value, message.encode("utf-8"))
        self._send_packet(packet, self._peers.get(remote_id.value, self._broadcast_addr))

    def remember_peer(self, network_id: NetworkID, addr: Address) -> None:
        """Cache a peer's last-seen address for unicast delivery (SPEC.md s7.4)."""
        self._peers[network_id.value] = addr

    def _send_packet(
        self,
        packet: DiscoveryRequest | DiscoveryResponse | DiscoveryMessage,
        addr: Address,
    ) -> None:
        if self._transport is None:
            return
        self._transport.sendto(self._envelope.seal(packet.serialize()), addr)

    # -- receiving ---------------------------------------------------------------------

    def _on_datagram(self, data: bytes, addr: Address) -> None:
        plaintext = self._envelope.open(data)
        if plaintext is None:
            return  # bad MAC / decrypt failure -> silently dropped (SPEC.md s8.4)
        packet = parse_discovery(plaintext)
        if packet is None:
            return
        if packet.sender_id == self._local_id:
            return  # ignore our own packets (SPEC.md s7.4)
        if packet.sender_id != 0:
            # A zero SenderId (e.g. the first-discovery probe, SPEC.md s12) is not a new peer.
            self._peers[packet.sender_id] = addr

        if isinstance(packet, DiscoveryRequest):
            self._handle_request(addr)
        elif isinstance(packet, DiscoveryResponse):
            if self._on_host_discovered is not None:
                self._on_host_discovered(NetworkID.p2p(packet.sender_id), packet.application_data)
        elif isinstance(packet, DiscoveryMessage):
            self._handle_message(packet)

    def _handle_request(self, addr: Address) -> None:
        if self._get_advertisement is None:
            return  # not advertising a host
        advertisement = self._get_advertisement()
        if advertisement is None:
            return
        self._send_packet(DiscoveryResponse(self._local_id, advertisement), addr)

    def _handle_message(self, packet: DiscoveryMessage) -> None:
        if packet.recipient_id != self._local_id:
            return  # addressed to someone else (SPEC.md s7.4)
        if not packet.message_data:
            return  # empty payload (e.g. first-discovery probe) -> ignore (SPEC.md s12)
        try:
            message = packet.message_data.decode("utf-8")
        except UnicodeDecodeError:
            return
        if self._on_signal is not None:
            self._on_signal(NetworkID.p2p(packet.sender_id), message)
