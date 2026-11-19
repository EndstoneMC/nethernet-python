import re

from nethernet.discovery.packets import (
    MESSAGE_PAYLOAD_MAX,
    NONCE_ROTATION_WINDOW,
    RESPONSE_PAYLOAD_MAX,
    AdvertisementNonce,
    DiscoveryMessage,
    DiscoveryPacketType,
    DiscoveryRequest,
    DiscoveryResponse,
    HostAdvertisement,
    parse,
)

SENDER = 0x1122334455667788
RECIPIENT = 0x99AABBCCDDEEFF00


def u16(v):
    return v.to_bytes(2, "little")


def u32(v):
    return v.to_bytes(4, "little")


def u64(v):
    return v.to_bytes(8, "little")


def test_request_byte_layout():
    b = DiscoveryRequest(SENDER).serialize()
    assert len(b) == 20
    assert b[0:2] == u16(20)
    assert b[2:4] == u16(DiscoveryPacketType.REQUEST)
    assert b[4:12] == u64(SENDER)
    assert b[12:20] == b"\x00" * 8


def test_request_roundtrip():
    assert parse(DiscoveryRequest(SENDER).serialize()) == DiscoveryRequest(SENDER)


def test_response_byte_layout():
    data = b"0761"
    b = DiscoveryResponse(SENDER, data).serialize()
    assert b[0:2] == u16(24 + len(data))
    assert b[2:4] == u16(DiscoveryPacketType.RESPONSE)
    assert b[4:12] == u64(SENDER)
    assert b[20:24] == u32(len(data))
    assert b[24:] == data


def test_response_roundtrip():
    data = b"host advertisement bytes"
    assert parse(DiscoveryResponse(SENDER, data).serialize()) == DiscoveryResponse(SENDER, data)


def test_response_length_fields_clamp_at_1148_but_the_whole_payload_is_sent():
    b = DiscoveryResponse(SENDER, b"x" * 2000).serialize()
    assert b[0:2] == u16(24 + RESPONSE_PAYLOAD_MAX)
    assert b[20:24] == u32(RESPONSE_PAYLOAD_MAX)
    assert len(b) == 24 + 2000
    assert parse(b).application_data == b"x" * 2000


def test_message_byte_layout():
    msg = b"CONNECTREQUEST 5 v=0..."
    b = DiscoveryMessage(SENDER, RECIPIENT, msg).serialize()
    assert b[0:2] == u16(32 + len(msg))
    assert b[2:4] == u16(DiscoveryPacketType.MESSAGE)
    assert b[4:12] == u64(SENDER)
    assert b[20:28] == u64(RECIPIENT)
    assert b[28:32] == u32(len(msg))
    assert b[32:] == msg


def test_message_roundtrip():
    msg = b"CANDIDATEADD 7 candidate:1 1 udp 2130706431 10.0.0.5 5000 typ host"
    got = parse(DiscoveryMessage(SENDER, RECIPIENT, msg).serialize())
    assert got == DiscoveryMessage(SENDER, RECIPIENT, msg)


def test_message_length_fields_clamp_at_1140_but_the_whole_payload_is_sent():
    b = DiscoveryMessage(SENDER, RECIPIENT, b"y" * 1870).serialize()
    assert b[0:2] == u16(32 + MESSAGE_PAYLOAD_MAX)
    assert b[28:32] == u32(MESSAGE_PAYLOAD_MAX)
    assert len(b) == 32 + 1870
    assert parse(b).message_data == b"y" * 1870


def test_empty_payloads_roundtrip():
    assert parse(DiscoveryResponse(SENDER, b"").serialize()) == DiscoveryResponse(SENDER, b"")
    assert parse(DiscoveryMessage(SENDER, RECIPIENT, b"").serialize()) == DiscoveryMessage(
        SENDER, RECIPIENT, b""
    )


def test_datagram_under_four_bytes_is_dropped():
    assert parse(b"\x14\x00\x00") is None


def test_datagram_shorter_than_its_packet_length_is_dropped():
    full = DiscoveryResponse(SENDER, b"hello").serialize()
    assert parse(full[:26]) is None


def test_unknown_packet_type_is_dropped():
    bad = bytearray(DiscoveryRequest(SENDER).serialize())
    bad[2:4] = u16(9)
    assert parse(bytes(bad)) is None


def test_datagram_shorter_than_its_type_header_is_dropped():
    assert parse(u16(4) + u16(0) + b"\x00" * 15) is None
    assert parse(u16(20) + u16(1) + u64(SENDER) + b"\x00" * 10) is None
    assert parse(u16(24) + u16(2) + u64(SENDER) + b"\x00" * 18) is None


def test_payload_runs_to_the_end_of_the_datagram_whatever_the_length_fields_say():
    datagram = u16(24) + u16(1) + u64(SENDER) + b"\x00" * 8 + u32(0) + b"extra"
    assert parse(datagram) == DiscoveryResponse(SENDER, b"extra")
    datagram = u16(32) + u16(2) + u64(SENDER) + b"\x00" * 8 + u64(RECIPIENT) + u32(99) + b"ab"
    assert parse(datagram) == DiscoveryMessage(SENDER, RECIPIENT, b"ab")


def test_request_padding_is_ignored_on_receive():
    datagram = u16(20) + u16(0) + u64(SENDER) + b"\xff" * 8
    assert parse(datagram) == DiscoveryRequest(SENDER)


def test_host_advertisement_defaults_are_the_values_bds_sends():
    advertisement = HostAdvertisement(
        name="Dedicated Server",
        level="Bedrock level",
        players=0,
        max_players=10,
        game_type=0,
        editor=False,
        hardcore=False,
        online_auth=True,
        self_signed_auth=False,
        nonce="1a2b3c4d5e6f7081",
        connection=4,
    )
    assert advertisement.data_version == 7
    assert advertisement.protocol == 2225
    assert advertisement.version == "1.26.60"


def test_nonce_is_lowercase_hex_without_zero_padding():
    nonce = AdvertisementNonce().value
    assert re.fullmatch(r"0|[1-9a-f][0-9a-f]{0,15}", nonce)


def test_nonce_is_kept_inside_its_window_and_replaced_on_the_next_update_after_it():
    now = [1000.0]
    nonce = AdvertisementNonce(clock=lambda: now[0])
    first = nonce.value
    now[0] += NONCE_ROTATION_WINDOW
    assert nonce.update() == first
    now[0] += 1
    assert nonce.value == first
    second = nonce.update()
    assert second != first
    assert nonce.value == second
    now[0] += NONCE_ROTATION_WINDOW
    assert nonce.update() == second


def test_nonce_rotation_window_is_300_seconds():
    assert NONCE_ROTATION_WINDOW == 300.0


def test_each_nonce_source_starts_with_a_new_nonce():
    assert AdvertisementNonce().value != AdvertisementNonce().value
