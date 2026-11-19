from __future__ import annotations

import asyncio
import errno
import ipaddress
import socket
import struct
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeAlias

import ifaddr

from nethernet.constants import (
    DEFAULT_APPLICATION_ID,
    DEFAULT_BROADCAST_INTERVAL,
    DEFAULT_BROADCAST_PORT,
)
from nethernet.discovery.crypto import Envelope
from nethernet.discovery.packets import (
    DiscoveryMessage,
    DiscoveryPacket,
    DiscoveryRequest,
    DiscoveryResponse,
)
from nethernet.discovery.packets import (
    parse as parse_discovery,
)
from nethernet.network_id import NetworkID

Address: TypeAlias = tuple[str, int]
"""A peer's host and UDP port."""

LIMITED_BROADCAST = "255.255.255.255"
ALL_NODES = "ff02::1"
RECEIVE_BUFFER_SIZE = 32768
PEER_EXPIRY = 60.0


def _errnos(*names: str) -> frozenset[int]:
    return frozenset(getattr(errno, name) for name in names if hasattr(errno, name))


_IN_USE_ERRORS = _errnos("EADDRINUSE", "WSAEADDRINUSE") | (
    _errnos("WSAEACCES") if sys.platform == "win32" else frozenset()
)
_REMOVING_ERRORS = _errnos(
    "EHOSTUNREACH",
    "ENETUNREACH",
    "EAFNOSUPPORT",
    "EFAULT",
    "WSAEHOSTUNREACH",
    "WSAENETUNREACH",
    "WSAEAFNOSUPPORT",
    "WSAEFAULT",
)


def _in_use(exc: OSError) -> bool:
    return exc.errno in _IN_USE_ERRORS or getattr(exc, "winerror", None) in _IN_USE_ERRORS


def _removes_address(exc: OSError) -> bool:
    return exc.errno in _REMOVING_ERRORS or getattr(exc, "winerror", None) in _REMOVING_ERRORS


def interface_addresses() -> list[tuple[str, int]]:
    addresses = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            address = ip.ip[0] if isinstance(ip.ip, tuple) else ip.ip
            addresses.append((address, ip.network_prefix))
    return addresses


def broadcast_addresses(addresses: Iterable[tuple[str, int]], *, ipv6: bool) -> list[str]:
    targets: list[str] = []
    for text, prefix in addresses:
        try:
            address = ipaddress.ip_address(text.split("%", 1)[0])
        except ValueError:
            continue
        if address.is_loopback:
            continue
        if address.version == 4:
            if address.is_link_local:
                continue
            if prefix in (0, 32):
                target = str(address)
            else:
                try:
                    network = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
                except ValueError:
                    continue
                target = str(network.broadcast_address)
        elif ipv6:
            target = ALL_NODES
        else:
            continue
        if target not in targets:
            targets.append(target)
    return targets


def _ignore_failure(sock: socket.socket, level: int, option: int, value: int | bytes) -> None:
    try:
        sock.setsockopt(level, option, value)
    except OSError:
        pass


def _bind_ipv6(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        _ignore_failure(sock, socket.SOL_SOCKET, socket.SO_RCVBUF, RECEIVE_BUFFER_SIZE)
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    group = socket.inet_pton(socket.AF_INET6, ALL_NODES) + struct.pack("@I", 0)
    _ignore_failure(sock, socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, group)
    _ignore_failure(sock, socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_LOOP, 0)
    return sock


def _bind_ipv4(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        _ignore_failure(sock, socket.SOL_SOCKET, socket.SO_RCVBUF, RECEIVE_BUFFER_SIZE)
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    return sock


def _ip_version(host: str) -> int | None:
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).version
    except ValueError:
        return None


@dataclass
class _Peer:
    address: Any
    last_active: float
    permanent: bool


class LanTransport(asyncio.DatagramProtocol):
    def __init__(
        self,
        *,
        local_id: int,
        application_id: int = DEFAULT_APPLICATION_ID,
        port: int = DEFAULT_BROADCAST_PORT,
        bind_host: str = "",
        broadcast_host: str = LIMITED_BROADCAST,
        broadcast_port: int | None = None,
        broadcast_interval: float = DEFAULT_BROADCAST_INTERVAL,
        visible: bool = True,
        on_signal: Callable[[NetworkID, str], None] | None = None,
        on_host_discovered: Callable[[NetworkID, bytes, Address], None] | None = None,
        get_advertisement: Callable[[], bytes | None] | None = None,
        scan_interfaces: Callable[[], Iterable[tuple[str, int]]] = interface_addresses,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._local_id = local_id
        self._envelope = Envelope(application_id)
        self._port = port
        self._bind_host = bind_host
        self._limited_broadcast = broadcast_host
        self._request_port = port if broadcast_port is None else broadcast_port
        self._broadcast_interval = broadcast_interval
        self.visible = visible
        self._on_signal = on_signal
        self._on_host_discovered = on_host_discovered
        self._get_advertisement = get_advertisement
        self._scan_interfaces = scan_interfaces
        self._clock = clock

        self._sock: socket.socket | None = None
        self._bound_port: int | None = None
        self._transport: asyncio.BaseTransport | None = None
        self._peers: dict[int, _Peer] = {}
        self._broadcast_ids: dict[int, None] = {}
        self._broadcast_targets: list[str] = []
        self._scanning = False
        self._timers: set[asyncio.TimerHandle] = set()
        self._suspended = False
        self._tick_overdue = False
        self._rebuild: asyncio.Task | None = None
        self._closed = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport

    def datagram_received(self, data: bytes, addr: Any) -> None:
        if self._suspended:
            return
        self._on_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        pass

    async def start(self) -> None:
        self._closed = False
        await self._open()

    async def __aenter__(self) -> LanTransport:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def bound_port(self) -> int | None:
        return self._bound_port

    @property
    def suspended(self) -> bool:
        return self._suspended

    async def close(self) -> None:
        self._closed = True
        for timer in self._timers:
            timer.cancel()
        self._timers.clear()
        if self._rebuild is not None:
            self._rebuild.cancel()
            try:
                await self._rebuild
            except asyncio.CancelledError:
                pass
            self._rebuild = None
        self._drop_socket()

    def suspend(self) -> None:
        self._suspended = True

    async def resume(self) -> None:
        if not self._suspended:
            return
        self._suspended = False
        self._drop_socket()
        try:
            await self._open()
        except OSError:
            pass
        if self._tick_overdue:
            self._tick_overdue = False
            self._tick()

    def enable_broadcast(self, network_id: int) -> None:
        if network_id in self._broadcast_ids:
            return
        first = not self._broadcast_ids
        self._broadcast_ids[network_id] = None
        if first:
            self._tick()

    def disable_broadcast(self, network_id: int) -> None:
        self._broadcast_ids.pop(network_id, None)

    def start_broadcasting(self) -> None:
        self.enable_broadcast(self._local_id)

    def broadcast_request(self) -> None:
        if self._suspended:
            return
        self._send_requests(self._local_id)

    def send_signal(self, remote_id: NetworkID, message: str) -> bool:
        if self._suspended:
            return False
        peer = self._lookup(remote_id.value, self._clock())
        if peer is None:
            return False
        data = message.encode("utf-8", "surrogateescape")
        packet = DiscoveryMessage(self._local_id, remote_id.value, data)
        return self._send(packet, peer.address) is None

    def is_on_lan(self, network_id: NetworkID) -> bool:
        return self._lookup(network_id.value, self._clock()) is not None

    def add_host(self, network_id: NetworkID, host: str, port: int) -> None:
        peer = self._lookup(network_id.value, self._clock())
        if peer is None:
            self._peers[network_id.value] = _Peer((host, port), self._clock(), True)
        else:
            peer.address = (host, port)
            peer.permanent = True

    def remove_host(self, network_id: NetworkID) -> None:
        peer = self._lookup(network_id.value, self._clock())
        if peer is not None:
            peer.last_active = self._clock()
            peer.permanent = False

    def remember_peer(self, network_id: NetworkID, addr: Address) -> None:
        self.add_host(network_id, addr[0], addr[1])

    async def _open(self) -> None:
        sock = self._build_socket()
        loop = asyncio.get_running_loop()
        try:
            await loop.create_datagram_endpoint(lambda: self, sock=sock)
        except BaseException:
            sock.close()
            raise
        self._sock = sock
        self._bound_port = sock.getsockname()[1]

    def _build_socket(self) -> socket.socket:
        try:
            return self._bind(self._port)
        except OSError as exc:
            if not _in_use(exc):
                raise
        return self._bind(0)

    def _bind(self, port: int) -> socket.socket:
        version = _ip_version(self._bind_host)
        if self._bind_host and version is None:
            socket.getaddrinfo(
                self._bind_host, port, type=socket.SOCK_DGRAM, flags=socket.AI_NUMERICHOST
            )
            raise socket.gaierror(socket.EAI_NONAME, "bind address is not an IP address")
        if version == 4:
            return _bind_ipv4(self._bind_host, port)
        if version == 6 and self._bind_host != "::":
            return _bind_ipv6(self._bind_host, port)
        try:
            return _bind_ipv6("::", port)
        except OSError as exc:
            if _in_use(exc):
                raise
        return _bind_ipv4("0.0.0.0", port)

    def _drop_socket(self) -> None:
        if self._transport is not None:
            self._transport.close()
        elif self._sock is not None:
            self._sock.close()
        self._transport = None
        self._sock = None

    def _schedule_rebuild(self) -> None:
        self._drop_socket()
        if self._rebuild is None and not self._closed:
            self._rebuild = asyncio.get_running_loop().create_task(self._rebuild_socket())

    async def _rebuild_socket(self) -> None:
        try:
            await self._open()
        except OSError:
            pass
        finally:
            self._rebuild = None

    def _destination(self, address: Any) -> Any:
        host = address[0]
        version = _ip_version(host)
        if self._sock is not None and self._sock.family == socket.AF_INET6:
            if version == 4:
                return (f"::ffff:{host}", address[1])
            return address
        if version == 6:
            return None
        return address

    def _send(self, packet: DiscoveryPacket, address: Any) -> OSError | None:
        if self._sock is None:
            return OSError(errno.ENOTCONN, "no LAN socket")
        destination = self._destination(address)
        if destination is None:
            return OSError(errno.EAFNOSUPPORT, "address family not supported")
        try:
            self._sock.sendto(self._envelope.seal(packet.serialize()), destination)
        except BrokenPipeError as exc:
            self._schedule_rebuild()
            return exc
        except OSError as exc:
            return exc
        return None

    def _send_requests(self, sender_id: int) -> None:
        request = DiscoveryRequest(sender_id)
        for target in list(self._broadcast_targets):
            error = self._send(request, (target, self._request_port))
            if error is not None and _removes_address(error) and target in self._broadcast_targets:
                self._broadcast_targets.remove(target)
        self._send(request, (self._limited_broadcast, self._request_port))

    def _tick(self) -> None:
        if self._closed:
            return
        if self._suspended:
            self._tick_overdue = True
            return
        if not self._broadcast_ids:
            self._broadcast_targets.clear()
            return
        if not self._broadcast_targets and not self._scanning:
            self._scanning = True
            asyncio.get_running_loop().call_soon(self._finish_scan)
        for network_id in list(self._broadcast_ids):
            self._send_requests(network_id)
        self._arm()

    def _arm(self) -> None:
        handle: asyncio.TimerHandle

        def fire() -> None:
            self._timers.discard(handle)
            self._tick()

        handle = asyncio.get_running_loop().call_later(self._broadcast_interval, fire)
        self._timers.add(handle)

    def _finish_scan(self) -> None:
        self._scanning = False
        if self._closed:
            return
        ipv6 = self._sock is not None and self._sock.family == socket.AF_INET6
        try:
            addresses = list(self._scan_interfaces())
        except OSError:
            addresses = []
        self._broadcast_targets = broadcast_addresses(addresses, ipv6=ipv6)

    def _lookup(self, network_id: int, now: float) -> _Peer | None:
        peer = self._peers.get(network_id)
        if peer is not None and not peer.permanent and now - peer.last_active >= PEER_EXPIRY:
            del self._peers[network_id]
            return None
        return peer

    def _record(self, network_id: int, address: Any) -> bool:
        now = self._clock()
        for expired in [
            key
            for key, peer in self._peers.items()
            if not peer.permanent and now - peer.last_active >= PEER_EXPIRY
        ]:
            del self._peers[expired]
        peer = self._peers.get(network_id)
        if peer is None:
            self._peers[network_id] = _Peer(address, now, False)
            return True
        peer.address = address
        peer.last_active = now
        peer.permanent = False
        return False

    def _on_datagram(self, data: bytes, addr: Any) -> None:
        plaintext = self._envelope.open(data)
        if plaintext is None:
            return
        packet = parse_discovery(plaintext)
        if packet is None:
            return
        if isinstance(packet, DiscoveryRequest):
            self._handle_request(packet, addr)
            return
        if packet.sender_id != 0 and self._record(packet.sender_id, addr):
            self._send(DiscoveryMessage(0, packet.sender_id, b""), addr)
        if isinstance(packet, DiscoveryResponse):
            self._handle_response(packet, addr)
        else:
            self._handle_message(packet)

    def _handle_request(self, packet: DiscoveryRequest, addr: Any) -> None:
        if packet.sender_id == self._local_id or not self.visible:
            return
        advertisement = None if self._get_advertisement is None else self._get_advertisement()
        self._send(DiscoveryResponse(self._local_id, advertisement or b""), addr)

    def _handle_response(self, packet: DiscoveryResponse, addr: Any) -> None:
        if packet.sender_id == self._local_id or self._on_host_discovered is None:
            return
        self._on_host_discovered(
            NetworkID.p2p(packet.sender_id), packet.application_data, _public_address(addr)
        )

    def _handle_message(self, packet: DiscoveryMessage) -> None:
        if packet.recipient_id != self._local_id or self._on_signal is None:
            return
        message = packet.message_data.decode("utf-8", "surrogateescape")
        self._on_signal(NetworkID.p2p(packet.sender_id), message)


def _public_address(address: Any) -> Address:
    host, port = address[0], address[1]
    if host.startswith("::ffff:") and _ip_version(host[7:]) == 4:
        host = host[7:]
    elif len(address) == 4 and address[3]:
        host = f"{host}%{address[3]}"
    return (host, port)
