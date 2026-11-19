import asyncio
import errno
import socket

import pytest

from nethernet.constants import DEFAULT_APPLICATION_ID, DEFAULT_BROADCAST_PORT
from nethernet.discovery import lan
from nethernet.discovery.crypto import Envelope
from nethernet.discovery.lan import LanTransport, broadcast_addresses
from nethernet.discovery.packets import (
    DiscoveryMessage,
    DiscoveryRequest,
    DiscoveryResponse,
)
from nethernet.discovery.packets import (
    parse as parse_discovery,
)
from nethernet.network_id import NetworkID

ENV = Envelope(DEFAULT_APPLICATION_ID)
SOURCE = ("10.0.0.9", 7551)
LOOPBACK = "127.0.0.1"


class FakeSocket:
    def __init__(self, family=socket.AF_INET6):
        self.family = family
        self.sent = []
        self.errors = {}
        self.closed = False
        self.on_send = None

    def sendto(self, data, addr):
        error = self.errors.get(addr[0].removeprefix("::ffff:"))
        if error is not None:
            raise error
        self.sent.append((data, addr))
        if self.on_send is not None:
            self.on_send()

    def getsockname(self):
        return ("::", DEFAULT_BROADCAST_PORT, 0, 0)

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_transport(local_id, family=socket.AF_INET6, **kwargs):
    t = LanTransport(local_id=local_id, **kwargs)
    sock = FakeSocket(family)
    t._sock = sock
    return t, sock


def opened(data):
    return parse_discovery(ENV.open(data))


def deliver(t, packet, addr=SOURCE):
    t.datagram_received(ENV.seal(packet.serialize()), addr)


def sent_packets(sock):
    return [(opened(data), addr) for data, addr in sock.sent]


def mapped(addr):
    return (f"::ffff:{addr[0]}", addr[1])


async def test_endpoint_binds_one_dual_stack_broadcast_socket_without_reuseaddr():
    t = LanTransport(local_id=111, port=0)
    await t.start()
    try:
        sock = t._sock
        assert sock.family == socket.AF_INET6
        assert sock.getsockname()[0] == "::"
        assert sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 0
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST) != 0
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF) >= lan.RECEIVE_BUFFER_SIZE
        assert sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_LOOP) == 0
    finally:
        await t.close()


async def test_port_in_use_retries_on_an_ephemeral_port_and_requests_still_go_to_the_port():
    blocker = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    blocker.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    blocker.bind(("::", 0))
    blocker.setblocking(False)
    port = blocker.getsockname()[1]
    t = LanTransport(local_id=111, port=port, broadcast_host=LOOPBACK)
    await t.start()
    try:
        assert t._sock.family == socket.AF_INET6
        assert t.bound_port not in (None, port)
        t.broadcast_request()
        loop = asyncio.get_running_loop()
        data, _ = await asyncio.wait_for(loop.sock_recvfrom(blocker, 4096), 5)
        assert opened(data) == DiscoveryRequest(111)
    finally:
        await t.close()
        blocker.close()


async def test_ipv6_failure_falls_back_to_an_ipv4_socket(monkeypatch):
    def fail(host, port):
        raise OSError(errno.EAFNOSUPPORT, "no IPv6")

    monkeypatch.setattr(lan, "_bind_ipv6", fail)
    t = LanTransport(local_id=111, port=0)
    await t.start()
    try:
        assert t._sock.family == socket.AF_INET
        assert t._sock.getsockname()[0] == "0.0.0.0"
        assert t._sock.getsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST) != 0
    finally:
        await t.close()


async def test_in_use_ipv6_bind_skips_the_ipv4_fallback_and_retries_once(monkeypatch):
    calls = []
    real_bind_ipv6 = lan._bind_ipv6

    def bind_ipv6(host, port):
        calls.append(("ipv6", port))
        if port != 0:
            raise OSError(errno.EADDRINUSE, "in use")
        return real_bind_ipv6(host, port)

    def bind_ipv4(host, port):
        calls.append(("ipv4", port))
        raise AssertionError("IPv4 fallback after an in-use IPv6 bind")

    monkeypatch.setattr(lan, "_bind_ipv6", bind_ipv6)
    monkeypatch.setattr(lan, "_bind_ipv4", bind_ipv4)
    t = LanTransport(local_id=111, port=7551)
    await t.start()
    try:
        assert calls == [("ipv6", 7551), ("ipv6", 0)]
    finally:
        await t.close()


async def test_failed_retry_leaves_no_socket_and_start_raises(monkeypatch):
    def in_use(host, port):
        raise OSError(errno.EADDRINUSE, "in use")

    monkeypatch.setattr(lan, "_bind_ipv6", in_use)
    t = LanTransport(local_id=111, port=7551)
    with pytest.raises(OSError):
        await t.start()
    assert t.bound_port is None
    assert t.send_signal(NetworkID.p2p(222), "CONNECTREQUEST 1 x") is False


def test_ipv4_destination_goes_out_ipv4_mapped_on_the_dual_stack_socket():
    t, sock = make_transport(111)
    t.add_host(NetworkID.p2p(222), "10.0.0.9", 7551)
    assert t.send_signal(NetworkID.p2p(222), "CONNECTREQUEST 1 x")
    assert sock.sent[0][1] == ("::ffff:10.0.0.9", 7551)


def test_ipv6_destination_fails_on_the_ipv4_socket():
    t, sock = make_transport(111, family=socket.AF_INET)
    t.add_host(NetworkID.p2p(222), "fe80::1", 7551)
    assert t.send_signal(NetworkID.p2p(222), "CONNECTREQUEST 1 x") is False
    assert sock.sent == []


async def test_broken_pipe_send_rebuilds_the_socket():
    t, sock = make_transport(111, bind_host=LOOPBACK, port=0)
    t.add_host(NetworkID.p2p(222), "10.0.0.9", 7551)
    sock.errors["10.0.0.9"] = BrokenPipeError(errno.EPIPE, "broken pipe")
    assert t.send_signal(NetworkID.p2p(222), "CONNECTREQUEST 1 x") is False
    assert sock.closed
    await t._rebuild
    try:
        assert t._sock is not None and t._sock is not sock
        assert t.bound_port is not None
    finally:
        await t.close()


async def test_failed_rebuild_leaves_no_socket(monkeypatch):
    t, sock = make_transport(111, bind_host=LOOPBACK, port=0)
    t.add_host(NetworkID.p2p(222), "10.0.0.9", 7551)
    sock.errors["10.0.0.9"] = BrokenPipeError(errno.EPIPE, "broken pipe")

    def fail(host, port):
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(lan, "_bind_ipv4", fail)
    t.send_signal(NetworkID.p2p(222), "CONNECTREQUEST 1 x")
    await t._rebuild
    assert t._sock is None
    assert t.send_signal(NetworkID.p2p(222), "CONNECTREQUEST 1 x") is False
    await t.close()


async def test_endpoint_is_an_async_context_manager():
    async with LanTransport(local_id=111, bind_host=LOOPBACK, port=0) as t:
        assert t._sock is not None
    assert t._sock is None


def test_request_is_answered_with_advertisement_unicast():
    t, sock = make_transport(111, get_advertisement=lambda: b"0761")
    deliver(t, DiscoveryRequest(222))
    assert sent_packets(sock) == [(DiscoveryResponse(111, b"0761"), mapped(SOURCE))]


def test_request_with_sender_zero_is_answered():
    t, sock = make_transport(111, get_advertisement=lambda: b"0761")
    deliver(t, DiscoveryRequest(0))
    assert sent_packets(sock) == [(DiscoveryResponse(111, b"0761"), mapped(SOURCE))]


def test_request_from_the_local_id_is_ignored():
    t, sock = make_transport(111, get_advertisement=lambda: b"0761")
    deliver(t, DiscoveryRequest(111))
    assert sock.sent == []


def test_advertisement_is_fetched_again_for_each_request():
    adverts = iter([b"aa", b"bb"])
    t, sock = make_transport(111, get_advertisement=lambda: next(adverts))
    deliver(t, DiscoveryRequest(222))
    deliver(t, DiscoveryRequest(222))
    assert [p.application_data for p, _ in sent_packets(sock)] == [b"aa", b"bb"]


def test_request_without_an_advertisement_is_answered_with_empty_data():
    t, sock = make_transport(111, get_advertisement=lambda: None)
    deliver(t, DiscoveryRequest(222))
    assert sent_packets(sock) == [(DiscoveryResponse(111, b""), mapped(SOURCE))]


def test_failed_reply_is_ignored():
    t, sock = make_transport(111, get_advertisement=lambda: b"0761")
    sock.errors[SOURCE[0]] = OSError(errno.ENETUNREACH, "unreachable")
    deliver(t, DiscoveryRequest(222))
    assert sock.sent == []


def test_request_never_records_a_peer():
    t, sock = make_transport(111, get_advertisement=lambda: b"0761")
    deliver(t, DiscoveryRequest(222))
    sock.sent.clear()
    assert not t.is_on_lan(NetworkID.p2p(222))
    assert t.send_signal(NetworkID.p2p(222), "CONNECTRESPONSE 5 x") is False
    assert sock.sent == []


def test_invisible_endpoint_answers_no_request_but_still_processes_the_rest():
    seen, signals = [], []
    t, sock = make_transport(
        111,
        visible=False,
        get_advertisement=lambda: b"0761",
        on_host_discovered=lambda *a: seen.append(a),
        on_signal=lambda *a: signals.append(a),
    )
    deliver(t, DiscoveryRequest(222))
    assert sock.sent == []
    deliver(t, DiscoveryResponse(333, b"adv"))
    deliver(t, DiscoveryMessage(444, 111, b"CONNECTREQUEST 5 x"))
    assert seen == [(NetworkID.p2p(333), b"adv", SOURCE)]
    assert signals == [(NetworkID.p2p(444), "CONNECTREQUEST 5 x")]
    assert [p.recipient_id for p, _ in sent_packets(sock)] == [333, 444]


def test_response_delivers_discovered_host_with_source_address():
    seen = []
    t, _ = make_transport(111, on_host_discovered=lambda *a: seen.append(a))
    deliver(t, DiscoveryResponse(222, b"adv"), ("::ffff:10.0.0.9", 7551, 0, 0))
    assert seen == [(NetworkID.p2p(222), b"adv", ("10.0.0.9", 7551))]


def test_response_from_the_local_id_is_recorded_but_not_delivered():
    seen = []
    t, sock = make_transport(111, on_host_discovered=lambda *a: seen.append(a))
    deliver(t, DiscoveryResponse(111, b"adv"))
    assert seen == []
    assert t.is_on_lan(NetworkID.p2p(111))
    assert sent_packets(sock) == [(DiscoveryMessage(0, 111, b""), mapped(SOURCE))]


def test_first_response_from_a_peer_gets_the_probe():
    t, sock = make_transport(111)
    deliver(t, DiscoveryResponse(222, b"adv"))
    assert sent_packets(sock) == [(DiscoveryMessage(0, 222, b""), mapped(SOURCE))]


def test_refresh_sends_no_probe_and_replaces_the_address():
    t, sock = make_transport(111)
    deliver(t, DiscoveryResponse(222, b"adv"))
    deliver(t, DiscoveryMessage(222, 111, b"CANDIDATEADD 5 x"), ("10.0.0.10", 50000))
    assert len(sock.sent) == 1
    t.send_signal(NetworkID.p2p(222), "CANDIDATEADD 5 y")
    assert sock.sent[1][1] == ("::ffff:10.0.0.10", 50000)


def test_message_to_us_is_delivered_to_signaling():
    signals = []
    t, _ = make_transport(111, on_signal=lambda nid, msg: signals.append((nid, msg)))
    text = "CONNECTREQUEST 5 v=0 sdp..."
    deliver(t, DiscoveryMessage(222, 111, text.encode()))
    assert signals == [(NetworkID.p2p(222), text)]


def test_message_to_another_recipient_is_dropped_after_its_sender_is_recorded():
    signals = []
    t, sock = make_transport(111, on_signal=lambda *a: signals.append(a))
    deliver(t, DiscoveryMessage(222, 999, b"CONNECTREQUEST 5 x"))
    assert signals == []
    assert t.is_on_lan(NetworkID.p2p(222))
    assert sent_packets(sock) == [(DiscoveryMessage(0, 222, b""), mapped(SOURCE))]


def test_message_from_the_local_id_is_recorded_probed_and_delivered():
    signals = []
    t, sock = make_transport(111, on_signal=lambda *a: signals.append(a))
    deliver(t, DiscoveryMessage(111, 111, b"CONNECTREQUEST 5 x"))
    assert sent_packets(sock) == [(DiscoveryMessage(0, 111, b""), mapped(SOURCE))]
    assert signals == [(NetworkID.p2p(111), "CONNECTREQUEST 5 x")]


def test_received_probe_records_nothing_and_passes_its_empty_data_to_signaling():
    signals = []
    t, sock = make_transport(111, on_signal=lambda *a: signals.append(a))
    deliver(t, DiscoveryMessage(0, 111, b""))
    assert signals == [(NetworkID.p2p(0), "")]
    assert sock.sent == []
    assert not t.is_on_lan(NetworkID.p2p(0))


def test_long_message_is_sized_from_the_datagram():
    signals = []
    t, _ = make_transport(111, on_signal=lambda *a: signals.append(a))
    text = "CONNECTREQUEST 5 " + "a" * 1853
    deliver(t, DiscoveryMessage(222, 111, text.encode()))
    assert signals == [(NetworkID.p2p(222), text)]


def test_bad_mac_and_garbage_datagrams_are_dropped():
    signals = []
    t, sock = make_transport(111, on_signal=lambda *a: signals.append(a))
    wrong_key = Envelope(0x12345678).seal(DiscoveryMessage(222, 111, b"X 5 x").serialize())
    t.datagram_received(wrong_key, SOURCE)
    t.datagram_received(b"\x00" * 40, SOURCE)
    assert signals == []
    assert sock.sent == []


def test_peer_expires_60_seconds_after_its_last_datagram():
    clock = Clock()
    t, sock = make_transport(111, clock=clock)
    deliver(t, DiscoveryResponse(222, b"adv"))
    clock.now += 59.9
    assert t.is_on_lan(NetworkID.p2p(222))
    clock.now += 0.1
    assert not t.is_on_lan(NetworkID.p2p(222))
    sock.sent.clear()
    assert t.send_signal(NetworkID.p2p(222), "CANDIDATEADD 5 x") is False
    assert sock.sent == []


def test_expired_peer_is_new_again_and_gets_another_probe():
    clock = Clock()
    t, sock = make_transport(111, clock=clock)
    deliver(t, DiscoveryResponse(222, b"adv"))
    clock.now += 60
    deliver(t, DiscoveryResponse(222, b"adv"))
    assert [p for p, _ in sent_packets(sock)] == [DiscoveryMessage(0, 222, b"")] * 2


def test_manual_host_never_expires_and_gets_no_probe():
    clock = Clock()
    t, sock = make_transport(111, clock=clock)
    t.add_host(NetworkID.p2p(222), "10.0.0.9", 7551)
    clock.now += 10_000
    assert t.is_on_lan(NetworkID.p2p(222))
    assert sock.sent == []


def test_manual_host_expires_60_seconds_after_a_later_datagram():
    clock = Clock()
    t, sock = make_transport(111, clock=clock)
    t.add_host(NetworkID.p2p(222), "10.0.0.9", 7551)
    deliver(t, DiscoveryResponse(222, b"adv"))
    assert sock.sent == []
    clock.now += 60
    assert not t.is_on_lan(NetworkID.p2p(222))


def test_adding_an_existing_peer_replaces_its_address_and_makes_it_permanent():
    clock = Clock()
    t, sock = make_transport(111, clock=clock)
    deliver(t, DiscoveryResponse(222, b"adv"))
    t.add_host(NetworkID.p2p(222), "10.0.0.20", 7551)
    clock.now += 10_000
    assert t.send_signal(NetworkID.p2p(222), "CANDIDATEADD 5 x")
    assert sock.sent[-1][1] == ("::ffff:10.0.0.20", 7551)


def test_removing_a_host_keeps_it_on_the_lan_for_60_more_seconds():
    clock = Clock()
    t, _ = make_transport(111, clock=clock)
    t.add_host(NetworkID.p2p(222), "10.0.0.9", 7551)
    clock.now += 1000
    t.remove_host(NetworkID.p2p(222))
    clock.now += 59.9
    assert t.is_on_lan(NetworkID.p2p(222))
    clock.now += 0.1
    assert not t.is_on_lan(NetworkID.p2p(222))
    t.remove_host(NetworkID.p2p(333))
    assert not t.is_on_lan(NetworkID.p2p(333))


def test_send_signal_unicasts_to_the_recorded_peer():
    t, sock = make_transport(111)
    deliver(t, DiscoveryResponse(222, b"adv"))
    sock.sent.clear()
    assert t.send_signal(NetworkID.p2p(222), "CONNECTRESPONSE 5 v=0...")
    assert sent_packets(sock) == [
        (DiscoveryMessage(111, 222, b"CONNECTRESPONSE 5 v=0..."), mapped(SOURCE))
    ]


def test_send_signal_to_an_unknown_peer_sends_nothing():
    t, sock = make_transport(111)
    assert t.send_signal(NetworkID.p2p(333), "CONNECTREQUEST 5 x") is False
    assert sock.sent == []


def test_broadcast_addresses_from_the_interface_scan():
    addresses = [
        ("127.0.0.1", 8),
        ("::1", 128),
        ("169.254.10.20", 16),
        ("192.168.1.23", 24),
        ("10.5.0.2", 16),
        ("10.5.7.7", 16),
        ("172.16.0.9", 32),
        ("172.17.0.9", 0),
        ("fe80::1", 64),
        ("2001:db8::5", 64),
    ]
    assert broadcast_addresses(addresses, ipv6=True) == [
        "192.168.1.255",
        "10.5.255.255",
        "172.16.0.9",
        "172.17.0.9",
        "ff02::1",
    ]
    assert broadcast_addresses(addresses, ipv6=False) == [
        "192.168.1.255",
        "10.5.255.255",
        "172.16.0.9",
        "172.17.0.9",
    ]
    assert broadcast_addresses([("127.0.0.1", 8)], ipv6=True) == []


def broadcaster(local_id=111, family=socket.AF_INET6, scan=None, interval=0.01):
    return make_transport(
        local_id,
        family=family,
        port=7551,
        broadcast_interval=interval,
        scan_interfaces=scan or (lambda: [("192.168.1.23", 24), ("fe80::1", 64)]),
    )


async def wait_for_sends(sock, count):
    done = asyncio.Event()

    def check():
        if len(sock.sent) >= count:
            done.set()

    sock.on_send = check
    check()
    await asyncio.wait_for(done.wait(), 5)


async def test_first_enable_ticks_at_once_and_the_first_tick_only_reaches_the_limited_broadcast():
    t, sock = broadcaster()
    try:
        t.enable_broadcast(111)
        assert sent_packets(sock) == [(DiscoveryRequest(111), ("::ffff:255.255.255.255", 7551))]
    finally:
        await t.close()


async def test_each_tick_sends_each_enabled_id_to_each_broadcast_address_then_the_limited_one():
    t, sock = broadcaster()
    try:
        t.enable_broadcast(111)
        t.enable_broadcast(222)
        await wait_for_sends(sock, 7)
        assert sent_packets(sock)[1:7] == [
            (DiscoveryRequest(111), ("::ffff:192.168.1.255", 7551)),
            (DiscoveryRequest(111), ("ff02::1", 7551)),
            (DiscoveryRequest(111), ("::ffff:255.255.255.255", 7551)),
            (DiscoveryRequest(222), ("::ffff:192.168.1.255", 7551)),
            (DiscoveryRequest(222), ("ff02::1", 7551)),
            (DiscoveryRequest(222), ("::ffff:255.255.255.255", 7551)),
        ]
    finally:
        await t.close()


async def test_every_request_goes_to_the_discovery_port():
    t, sock = broadcaster(family=socket.AF_INET)
    try:
        t.enable_broadcast(111)
        await wait_for_sends(sock, 3)
        assert {addr for _, addr in sock.sent} == {
            ("255.255.255.255", 7551),
            ("192.168.1.255", 7551),
        }
    finally:
        await t.close()


async def test_enabling_an_id_twice_does_nothing():
    t, sock = broadcaster(interval=60)
    try:
        t.enable_broadcast(111)
        t.enable_broadcast(111)
        assert len(sock.sent) == 1
        assert len(t._timers) == 1
    finally:
        await t.close()


async def test_disabling_every_id_stops_the_tick_and_clears_the_broadcast_addresses():
    t, sock = broadcaster()
    try:
        t.enable_broadcast(111)
        await wait_for_sends(sock, 3)
        t.disable_broadcast(111)
        sent = len(sock.sent)
        timers = set(t._timers)
        t._tick()
        assert t._broadcast_targets == []
        assert len(sock.sent) == sent
        assert t._timers == timers
    finally:
        await t.close()


async def test_disabling_every_id_then_enabling_one_before_the_pending_tick_starts_a_second_chain():
    t, sock = broadcaster(interval=60)
    try:
        t.enable_broadcast(111)
        t.disable_broadcast(111)
        t.enable_broadcast(111)
        assert len(sock.sent) == 2
        assert len(t._timers) == 2
    finally:
        await t.close()


async def test_a_scan_that_finds_nothing_rescans_every_tick():
    scans = []
    t, _ = broadcaster(scan=lambda: scans.append(1) or [], interval=60)
    try:
        t.enable_broadcast(111)
        await asyncio.sleep(0)
        assert t._broadcast_targets == []
        t._tick()
        await asyncio.sleep(0)
        assert len(scans) == 2
    finally:
        await t.close()


async def test_listed_send_errors_remove_a_broadcast_address_and_others_keep_it():
    t, sock = broadcaster(
        family=socket.AF_INET,
        scan=lambda: [("192.168.1.23", 24), ("10.5.0.2", 16)],
        interval=60,
    )
    sock.errors["192.168.1.255"] = OSError(errno.EHOSTUNREACH, "host unreachable")
    sock.errors["10.5.255.255"] = OSError(errno.EACCES, "denied")
    sock.errors["255.255.255.255"] = OSError(errno.ENETUNREACH, "network unreachable")
    try:
        t.enable_broadcast(111)
        await asyncio.sleep(0)
        assert t._broadcast_targets == ["192.168.1.255", "10.5.255.255"]
        t._tick()
        assert t._broadcast_targets == ["10.5.255.255"]
        t._tick()
        assert t._broadcast_targets == ["10.5.255.255"]
    finally:
        await t.close()


async def test_suspended_endpoint_sends_receives_and_ticks_nothing_until_resumed():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind((LOOPBACK, 0))
    receiver.setblocking(False)
    signals = []
    t = LanTransport(
        local_id=111,
        bind_host=LOOPBACK,
        port=0,
        broadcast_host=LOOPBACK,
        broadcast_port=receiver.getsockname()[1],
        broadcast_interval=0.01,
        scan_interfaces=lambda: [],
        on_signal=lambda *a: signals.append(a),
    )
    await t.start()
    loop = asyncio.get_running_loop()
    try:
        t.add_host(NetworkID.p2p(222), *receiver.getsockname())
        old_sock = t._sock
        t.suspend()
        t.enable_broadcast(111)
        assert t.send_signal(NetworkID.p2p(222), "CONNECTREQUEST 1 x") is False
        t.datagram_received(ENV.seal(DiscoveryMessage(222, 111, b"X 1 y").serialize()), SOURCE)
        assert signals == []
        assert t._tick_overdue
        await t.resume()
        assert t._sock is not old_sock
        data, _ = await asyncio.wait_for(loop.sock_recvfrom(receiver, 4096), 5)
        assert opened(data) == DiscoveryRequest(111)
    finally:
        await t.close()
        receiver.close()


async def test_two_transports_exchange_signaling_over_udp():
    received = asyncio.Queue()
    a = LanTransport(local_id=111, bind_host=LOOPBACK, port=0)
    b = LanTransport(
        local_id=222,
        bind_host=LOOPBACK,
        port=0,
        on_signal=lambda nid, msg: received.put_nowait((nid, msg)),
    )
    await a.start()
    await b.start()
    try:
        a.add_host(NetworkID.p2p(222), LOOPBACK, b.bound_port)
        assert a.send_signal(NetworkID.p2p(222), "CONNECTREQUEST 7 hello")
        got = await asyncio.wait_for(received.get(), 5)
        assert got == (NetworkID.p2p(111), "CONNECTREQUEST 7 hello")
        assert b.is_on_lan(NetworkID.p2p(111))
        assert b.send_signal(NetworkID.p2p(111), "CONNECTRESPONSE 7 hi")
    finally:
        await a.close()
        await b.close()


async def test_request_and_response_round_trip_over_udp():
    found = asyncio.Queue()
    host = LanTransport(local_id=222, bind_host=LOOPBACK, port=0, get_advertisement=lambda: b"ad")
    await host.start()
    client = LanTransport(
        local_id=111,
        bind_host=LOOPBACK,
        port=0,
        broadcast_host=LOOPBACK,
        broadcast_port=host.bound_port,
        on_host_discovered=lambda *a: found.put_nowait(a),
    )
    await client.start()
    try:
        client.broadcast_request()
        network_id, data, address = await asyncio.wait_for(found.get(), 5)
        assert (network_id, data, address) == (
            NetworkID.p2p(222),
            b"ad",
            (LOOPBACK, host.bound_port),
        )
        assert client.is_on_lan(NetworkID.p2p(222))
        assert not host.is_on_lan(NetworkID.p2p(111))
    finally:
        await client.close()
        await host.close()
