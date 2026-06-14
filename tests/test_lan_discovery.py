"""LAN discovery + signaling transport — SPEC.md s7-s8."""

import asyncio

from nethernet.constants import DEFAULT_APPLICATION_ID, DEFAULT_BROADCAST_PORT
from nethernet.discovery.crypto import Envelope
from nethernet.discovery.lan import LanTransport
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


class FakeTransport:
    def __init__(self):
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def close(self):
        pass


def make_transport(local_id, **kwargs):
    t = LanTransport(local_id=local_id, **kwargs)
    fake = FakeTransport()
    t.connection_made(fake)
    return t, fake


def opened(data):
    return parse_discovery(ENV.open(data))


# --- Discovery (SPEC.md s7.3-s7.4) ---


def test_broadcast_request_sends_sealed_request():
    t, fake = make_transport(111)
    t.broadcast_request()
    assert len(fake.sent) == 1
    data, addr = fake.sent[0]
    assert addr == ("255.255.255.255", DEFAULT_BROADCAST_PORT)
    packet = opened(data)
    assert isinstance(packet, DiscoveryRequest)
    assert packet.sender_id == 111


def test_request_is_answered_with_advertisement_unicast():
    t, fake = make_transport(111, get_advertisement=lambda: b"MCPE;Server;3;10")
    t.datagram_received(ENV.seal(DiscoveryRequest(222).serialize()), ("10.0.0.9", 7551))
    assert len(fake.sent) == 1
    data, addr = fake.sent[0]
    assert addr == ("10.0.0.9", 7551)  # unicast back to requester
    packet = opened(data)
    assert isinstance(packet, DiscoveryResponse)
    assert packet.sender_id == 111
    assert packet.application_data == b"MCPE;Server;3;10"


def test_request_is_ignored_when_not_advertising():
    t, fake = make_transport(111)  # no get_advertisement
    t.datagram_received(ENV.seal(DiscoveryRequest(222).serialize()), ("10.0.0.9", 7551))
    assert fake.sent == []


def test_response_delivers_discovered_host_with_source_address():
    seen = []
    t, _ = make_transport(
        111, on_host_discovered=lambda nid, data, addr: seen.append((nid, data, addr))
    )
    t.datagram_received(ENV.seal(DiscoveryResponse(222, b"adv").serialize()), ("10.0.0.9", 7551))
    assert seen == [(NetworkID.p2p(222), b"adv", ("10.0.0.9", 7551))]


def test_broadcast_request_targets_configured_broadcast_port():
    # Bind port and broadcast target port are decoupled so a client on an ephemeral port can
    # direct its Request at a known host:port (used for loopback / directed discovery).
    t, fake = make_transport(111, broadcast_host="127.0.0.1", broadcast_port=9999)
    t.broadcast_request()
    _data, addr = fake.sent[0]
    assert addr == ("127.0.0.1", 9999)


def test_own_packets_are_ignored():
    seen = []
    t, _ = make_transport(111, on_host_discovered=lambda *a: seen.append(a))
    t.datagram_received(ENV.seal(DiscoveryResponse(111, b"adv").serialize()), ("10.0.0.9", 7551))
    assert seen == []


# --- LAN signaling (SPEC.md s7.4) ---


def test_message_to_us_is_delivered_to_signaling():
    signals = []
    t, _ = make_transport(111, on_signal=lambda nid, msg: signals.append((nid, msg)))
    text = "CONNECTREQUEST 5 v=0 sdp..."
    t.datagram_received(
        ENV.seal(DiscoveryMessage(222, 111, text.encode()).serialize()), ("10.0.0.9", 7551)
    )
    assert signals == [(NetworkID.p2p(222), text)]


def test_message_to_other_recipient_is_ignored():
    signals = []
    t, _ = make_transport(111, on_signal=lambda *a: signals.append(a))
    t.datagram_received(
        ENV.seal(DiscoveryMessage(222, 999, b"CONNECTREQUEST 5 x").serialize()), ("a", 1)
    )
    assert signals == []


def test_empty_probe_message_is_ignored_and_sender_zero_not_recorded():
    # SPEC.md s12: first-discovery probe is a Message with SenderId=0 and empty data.
    signals = []
    t, _ = make_transport(111, on_signal=lambda *a: signals.append(a))
    t.datagram_received(ENV.seal(DiscoveryMessage(0, 111, b"").serialize()), ("10.0.0.9", 7551))
    assert signals == []
    assert 0 not in t._peers


def test_bad_mac_and_garbage_datagrams_are_dropped():
    signals = []
    t, _ = make_transport(111, on_signal=lambda *a: signals.append(a))
    wrong_packet = DiscoveryMessage(222, 111, b"CONNECTREQUEST 5 x").serialize()
    wrong_key = Envelope(0x12345678).seal(wrong_packet)  # sealed under a different app id
    t.datagram_received(wrong_key, ("a", 1))
    t.datagram_received(b"\x00" * 40, ("a", 1))
    assert signals == []


def test_send_signal_unicasts_to_learned_peer():
    t, fake = make_transport(111)
    # Learn peer 222's address from an inbound (non-advertising, so no auto-response).
    t.datagram_received(ENV.seal(DiscoveryRequest(222).serialize()), ("10.0.0.9", 7551))
    fake.sent.clear()
    t.send_signal(NetworkID.p2p(222), "CONNECTRESPONSE 5 v=0...")
    data, addr = fake.sent[0]
    assert addr == ("10.0.0.9", 7551)
    packet = opened(data)
    assert isinstance(packet, DiscoveryMessage)
    assert packet.recipient_id == 222
    assert packet.message_data == b"CONNECTRESPONSE 5 v=0..."


def test_send_signal_broadcasts_to_unknown_peer():
    t, fake = make_transport(111)
    t.send_signal(NetworkID.p2p(333), "CONNECTREQUEST 5 x")
    _data, addr = fake.sent[0]
    assert addr == ("255.255.255.255", DEFAULT_BROADCAST_PORT)


# --- Real-socket loopback (validates connection_made / datagram_received / seal over UDP) ---


async def test_two_transports_exchange_signaling_over_udp():
    received = []
    a = LanTransport(local_id=111, bind_host="127.0.0.1", port=0)
    b = LanTransport(
        local_id=222,
        bind_host="127.0.0.1",
        port=0,
        on_signal=lambda nid, msg: received.append((nid, msg)),
    )
    await a.start()
    await b.start()
    try:
        a.remember_peer(NetworkID.p2p(222), ("127.0.0.1", b.bound_port))
        a.send_signal(NetworkID.p2p(222), "CONNECTREQUEST 7 hello")

        deadline = asyncio.get_event_loop().time() + 5
        while not received and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.02)
        assert received == [(NetworkID.p2p(111), "CONNECTREQUEST 7 hello")]
    finally:
        await a.close()
        await b.close()
