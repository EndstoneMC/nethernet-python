from __future__ import annotations

import re
import secrets
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import TypeAlias


class DiscoveryPacketType(IntEnum):
    REQUEST = 0
    RESPONSE = 1
    MESSAGE = 2


BASE_HEADER_SIZE = 20
RESPONSE_HEADER_SIZE = 24
MESSAGE_HEADER_SIZE = 32
RESPONSE_PAYLOAD_MAX = 1148
MESSAGE_PAYLOAD_MAX = 1140

_PREFIX = struct.Struct("<HH")
_BASE = struct.Struct("<HHQ8x")
_SENDER = struct.Struct("<Q")
_RESPONSE_EXTRA = struct.Struct("<I")
_MESSAGE_EXTRA = struct.Struct("<QI")
_HEADER_SIZES = {
    DiscoveryPacketType.REQUEST: BASE_HEADER_SIZE,
    DiscoveryPacketType.RESPONSE: RESPONSE_HEADER_SIZE,
    DiscoveryPacketType.MESSAGE: MESSAGE_HEADER_SIZE,
}


@dataclass(frozen=True)
class DiscoveryRequest:
    sender_id: int

    def serialize(self) -> bytes:
        return _BASE.pack(BASE_HEADER_SIZE, DiscoveryPacketType.REQUEST, self.sender_id)


@dataclass(frozen=True)
class DiscoveryResponse:
    sender_id: int
    application_data: bytes

    def serialize(self) -> bytes:
        length = min(len(self.application_data), RESPONSE_PAYLOAD_MAX)
        header = _BASE.pack(
            RESPONSE_HEADER_SIZE + length, DiscoveryPacketType.RESPONSE, self.sender_id
        )
        return header + _RESPONSE_EXTRA.pack(length) + self.application_data


@dataclass(frozen=True)
class DiscoveryMessage:
    sender_id: int
    recipient_id: int
    message_data: bytes

    def serialize(self) -> bytes:
        length = min(len(self.message_data), MESSAGE_PAYLOAD_MAX)
        header = _BASE.pack(
            MESSAGE_HEADER_SIZE + length, DiscoveryPacketType.MESSAGE, self.sender_id
        )
        return header + _MESSAGE_EXTRA.pack(self.recipient_id, length) + self.message_data


DiscoveryPacket: TypeAlias = DiscoveryRequest | DiscoveryResponse | DiscoveryMessage


def parse(data: bytes) -> DiscoveryPacket | None:
    if len(data) < _PREFIX.size:
        return None
    packet_length, packet_type = _PREFIX.unpack_from(data, 0)
    if len(data) < packet_length:
        return None
    header_size = _HEADER_SIZES.get(packet_type)
    if header_size is None or len(data) < header_size:
        return None
    (sender_id,) = _SENDER.unpack_from(data, 4)
    payload = bytes(data[header_size:])
    if packet_type == DiscoveryPacketType.REQUEST:
        return DiscoveryRequest(sender_id)
    if packet_type == DiscoveryPacketType.RESPONSE:
        return DiscoveryResponse(sender_id, payload)
    (recipient_id,) = _SENDER.unpack_from(data, BASE_HEADER_SIZE)
    return DiscoveryMessage(sender_id, recipient_id, payload)


ADVERTISEMENT_DATA_VERSION = 7
ADVERTISEMENT_PROTOCOL = 2225
ADVERTISEMENT_VERSION = "1.26.60"
NONCE_ROTATION_WINDOW = 300.0
LAN_SIGNALING_CONNECTION = 4
DEFAULT_SERVER_NAME = "Dedicated Server"
DEFAULT_LEVEL_NAME = "Bedrock level"
DEFAULT_MAX_PLAYERS = 10


@dataclass(frozen=True, kw_only=True)
class HostAdvertisement:
    data_version: int = ADVERTISEMENT_DATA_VERSION
    name: str = DEFAULT_SERVER_NAME
    protocol: int = ADVERTISEMENT_PROTOCOL
    version: str = ADVERTISEMENT_VERSION
    level: str = DEFAULT_LEVEL_NAME
    players: int = 0
    max_players: int = DEFAULT_MAX_PLAYERS
    game_type: int = 0
    editor: bool = False
    hardcore: bool = False
    online_auth: bool = True
    self_signed_auth: bool = True
    nonce: str
    connection: int = LAN_SIGNALING_CONNECTION

    def serialize(self) -> bytes:
        record = bytearray([self.data_version])
        _write_text(record, self.name)
        _write_varint(record, _zigzag(self.protocol))
        _write_text(record, self.version)
        _write_text(record, self.level)
        _write_varint(record, _zigzag(self.players))
        _write_varint(record, _zigzag(self.max_players))
        _write_varint(record, _zigzag(self.game_type))
        record += bytes([self.editor, self.hardcore, self.online_auth, self.self_signed_auth])
        _write_text(record, self.nonce)
        _write_varint(record, _zigzag(self.connection))
        return record.hex().encode("ascii")

    @classmethod
    def parse(cls, data: bytes) -> HostAdvertisement | None:
        try:
            text = data.decode("ascii")
            if re.fullmatch(r"[0-9A-Fa-f]*", text) is None:
                return None
            reader = _Reader(bytes.fromhex(text))
            return cls(
                data_version=reader.byte(),
                name=reader.text(),
                protocol=reader.zigzag(),
                version=reader.text(),
                level=reader.text(),
                players=reader.zigzag(),
                max_players=reader.zigzag(),
                game_type=reader.zigzag(),
                editor=bool(reader.byte()),
                hardcore=bool(reader.byte()),
                online_auth=bool(reader.byte()),
                self_signed_auth=bool(reader.byte()),
                nonce=reader.text(),
                connection=reader.zigzag(),
            )
        except (ValueError, IndexError):
            return None


class AdvertisementNonce:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._renew()

    @property
    def value(self) -> str:
        return self._value

    def update(self) -> str:
        if self._clock() - self._created > NONCE_ROTATION_WINDOW:
            self._renew()
        return self._value

    def _renew(self) -> None:
        self._value = format(secrets.randbits(64), "x")
        self._created = self._clock()


def _zigzag(value: int) -> int:
    return value << 1 if value >= 0 else (-value << 1) - 1


def _write_varint(out: bytearray, value: int) -> None:
    while value >= 0x80:
        out.append(value & 0x7F | 0x80)
        value >>= 7
    out.append(value)


def _write_text(out: bytearray, text: str) -> None:
    encoded = text.encode("utf-8")
    _write_varint(out, len(encoded))
    out += encoded


class _Reader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def byte(self) -> int:
        value = self._data[self._offset]
        self._offset += 1
        return value

    def varint(self) -> int:
        value = 0
        for shift in range(0, 35, 7):
            byte = self.byte()
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value & 0xFFFFFFFF
        raise ValueError("varint too long")

    def zigzag(self) -> int:
        value = self.varint()
        return (value >> 1) ^ -(value & 1)

    def text(self) -> str:
        length = self.varint()
        end = self._offset + length
        if end > len(self._data):
            raise ValueError("text past the end of the record")
        value = self._data[self._offset : end].decode("utf-8")
        self._offset = end
        return value
