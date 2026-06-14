"""Discovery packet codec — SPEC.md s7.2-s7.3. All integers little-endian, headers packed."""

from nethernet.discovery.packets import (
    MESSAGE_PAYLOAD_MAX,
    RESPONSE_PAYLOAD_MAX,
    DiscoveryMessage,
    DiscoveryPacketType,
    DiscoveryRequest,
    DiscoveryResponse,
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


# --- Request: base header only, 20 bytes, type 0 (SPEC.md s7.3) ---


def test_request_byte_layout():
    b = DiscoveryRequest(SENDER).serialize()
    assert len(b) == 20
    assert b[0:2] == u16(20)  # PacketLength
    assert b[2:4] == u16(DiscoveryPacketType.REQUEST)  # 0
    assert b[4:12] == u64(SENDER)  # SenderId at offset 4 (unaligned, packed)
    assert b[12:20] == b"\x00" * 8  # Pad zero on send


def test_request_roundtrip():
    assert parse(DiscoveryRequest(SENDER).serialize()) == DiscoveryRequest(SENDER)


# --- Response: 24-byte header + application data (cap 1148), type 1 (SPEC.md s7.3) ---


def test_response_byte_layout():
    data = b"MCPE;Dedicated Server;...;3;10;"
    b = DiscoveryResponse(SENDER, data).serialize()
    assert b[0:2] == u16(24 + len(data))  # PacketLength = 24 + ApplicationDataLength
    assert b[2:4] == u16(DiscoveryPacketType.RESPONSE)  # 1
    assert b[4:12] == u64(SENDER)
    assert b[20:24] == u32(len(data))  # ApplicationDataLength
    assert b[24:] == data


def test_response_roundtrip():
    data = b"host advertisement bytes"
    assert parse(DiscoveryResponse(SENDER, data).serialize()) == DiscoveryResponse(SENDER, data)


def test_response_truncates_to_cap():
    b = DiscoveryResponse(SENDER, b"x" * 2000).serialize()
    assert b[20:24] == u32(RESPONSE_PAYLOAD_MAX)  # 1148
    assert parse(b).application_data == b"x" * RESPONSE_PAYLOAD_MAX


# --- Message: 32-byte header + message data (cap 1140), type 2 (SPEC.md s7.3) ---


def test_message_byte_layout():
    msg = b"CONNECTREQUEST 5 v=0..."
    b = DiscoveryMessage(SENDER, RECIPIENT, msg).serialize()
    assert b[0:2] == u16(32 + len(msg))  # PacketLength = 32 + MessageDataLength
    assert b[2:4] == u16(DiscoveryPacketType.MESSAGE)  # 2
    assert b[4:12] == u64(SENDER)
    assert b[20:28] == u64(RECIPIENT)  # RecipientId at offset 20
    assert b[28:32] == u32(len(msg))  # MessageDataLength
    assert b[32:] == msg


def test_message_roundtrip():
    msg = b"CANDIDATEADD 7 candidate:1 1 udp 2130706431 10.0.0.5 5000 typ host"
    got = parse(DiscoveryMessage(SENDER, RECIPIENT, msg).serialize())
    assert got == DiscoveryMessage(SENDER, RECIPIENT, msg)


def test_message_truncates_to_cap():
    b = DiscoveryMessage(SENDER, RECIPIENT, b"y" * 2000).serialize()
    assert b[28:32] == u32(MESSAGE_PAYLOAD_MAX)  # 1140
    assert parse(b).message_data == b"y" * MESSAGE_PAYLOAD_MAX


def test_empty_payloads_roundtrip():
    assert parse(DiscoveryResponse(SENDER, b"").serialize()) == DiscoveryResponse(SENDER, b"")
    assert parse(DiscoveryMessage(SENDER, RECIPIENT, b"").serialize()) == DiscoveryMessage(
        SENDER, RECIPIENT, b""
    )


# --- Rejection ---


def test_too_short_is_rejected():
    assert parse(b"\x00" * 19) is None


def test_unknown_packet_type_is_rejected():
    bad = bytearray(DiscoveryRequest(SENDER).serialize())
    bad[2:4] = u16(9)
    assert parse(bytes(bad)) is None


def test_truncated_payload_is_rejected():
    # Declares 5 application bytes but only 2 are present.
    full = DiscoveryResponse(SENDER, b"hello").serialize()
    assert parse(full[:26]) is None
