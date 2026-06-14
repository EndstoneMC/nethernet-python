"""Discovery packet codec — SPEC.md s7.2-s7.3.

Every discovery packet begins with a packed (1-byte aligned) 20-byte base header. All integers
are little-endian. The base header is::

    offset 0  u16 PacketLength   (total = header size + payload size)
    offset 2  u16 PacketType     (0=Request, 1=Response, 2=Message)
    offset 4  u64 SenderId       (unaligned -- the struct is packed)
    offset 12 8B  Pad            (reserved, zero on send, ignored on receive)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import TypeAlias


class DiscoveryPacketType(IntEnum):
    REQUEST = 0  # Request
    RESPONSE = 1  # Response
    MESSAGE = 2  # Message


BASE_HEADER_SIZE = 20
RESPONSE_HEADER_SIZE = 24
MESSAGE_HEADER_SIZE = 32
RESPONSE_PAYLOAD_MAX = 1148  # SPEC.md s7.3 (DiscoveryResponsePacketHeader)
MESSAGE_PAYLOAD_MAX = 1140  # SPEC.md s7.3 (DiscoveryMessagePacketHeader)

# PacketLength, PacketType, SenderId, then 8 pad bytes (written zero, skipped on read).
_BASE = struct.Struct("<HHQ8x")
_RESPONSE_EXTRA = struct.Struct("<I")  # ApplicationDataLength
_MESSAGE_EXTRA = struct.Struct("<QI")  # RecipientId, MessageDataLength


@dataclass(frozen=True)
class DiscoveryRequest:
    """Type 0, 20 bytes, no payload — broadcast to solicit responses (SPEC.md s7.3)."""

    sender_id: int

    def serialize(self) -> bytes:
        return _BASE.pack(BASE_HEADER_SIZE, DiscoveryPacketType.REQUEST, self.sender_id)


@dataclass(frozen=True)
class DiscoveryResponse:
    """Type 1 — opaque host advertisement, truncated to 1148 bytes (SPEC.md s7.3)."""

    sender_id: int
    application_data: bytes

    def serialize(self) -> bytes:
        payload = self.application_data[:RESPONSE_PAYLOAD_MAX]
        header = _BASE.pack(
            RESPONSE_HEADER_SIZE + len(payload), DiscoveryPacketType.RESPONSE, self.sender_id
        )
        return header + _RESPONSE_EXTRA.pack(len(payload)) + payload


@dataclass(frozen=True)
class DiscoveryMessage:
    """Type 2 — a signaling message string for ``recipient_id``, capped at 1140 (SPEC.md s7.3)."""

    sender_id: int
    recipient_id: int
    message_data: bytes

    def serialize(self) -> bytes:
        payload = self.message_data[:MESSAGE_PAYLOAD_MAX]
        header = _BASE.pack(
            MESSAGE_HEADER_SIZE + len(payload), DiscoveryPacketType.MESSAGE, self.sender_id
        )
        return header + _MESSAGE_EXTRA.pack(self.recipient_id, len(payload)) + payload


DiscoveryPacket: TypeAlias = DiscoveryRequest | DiscoveryResponse | DiscoveryMessage


def parse(data: bytes) -> DiscoveryPacket | None:
    """Parse a decrypted discovery packet, or None if malformed (SPEC.md s7.2-s7.3)."""
    if len(data) < BASE_HEADER_SIZE:
        return None
    _packet_length, packet_type, sender_id = _BASE.unpack_from(data, 0)

    if packet_type == DiscoveryPacketType.REQUEST:
        return DiscoveryRequest(sender_id)

    if packet_type == DiscoveryPacketType.RESPONSE:
        if len(data) < RESPONSE_HEADER_SIZE:
            return None
        (app_len,) = _RESPONSE_EXTRA.unpack_from(data, BASE_HEADER_SIZE)
        end = RESPONSE_HEADER_SIZE + app_len
        if len(data) < end:
            return None
        return DiscoveryResponse(sender_id, data[RESPONSE_HEADER_SIZE:end])

    if packet_type == DiscoveryPacketType.MESSAGE:
        if len(data) < MESSAGE_HEADER_SIZE:
            return None
        recipient_id, msg_len = _MESSAGE_EXTRA.unpack_from(data, BASE_HEADER_SIZE)
        end = MESSAGE_HEADER_SIZE + msg_len
        if len(data) < end:
            return None
        return DiscoveryMessage(sender_id, recipient_id, data[MESSAGE_HEADER_SIZE:end])

    return None
