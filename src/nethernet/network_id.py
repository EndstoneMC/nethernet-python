from __future__ import annotations

import math
import secrets
import uuid as _uuid
from dataclasses import dataclass
from enum import Enum

U64_MAX = 0xFFFFFFFFFFFFFFFF
_I64_MIN = -(2**63)
_I64_MAX = 2**63 - 1

_DIGITS = frozenset("0123456789")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_DASH_OFFSETS = (8, 13, 18, 23)


class NetworkIDType(Enum):
    """The form of a ``NetworkID``: unset, P2P or Realms."""

    UNSET = 0
    P2P = 1
    REALMS = 2


@dataclass(frozen=True)
class NetworkID:
    """A peer identity: a P2P ``u64``, a Realms UUID, or unset."""

    type: NetworkIDType
    value: int = 0
    uuid: _uuid.UUID | None = None

    @classmethod
    def p2p(cls, value: int) -> NetworkID:
        if not 0 <= value <= U64_MAX:
            raise ValueError(f"P2P NetworkID must be a u64, got {value}")
        return cls(NetworkIDType.P2P, value=value)

    @classmethod
    def realms(cls, u: _uuid.UUID) -> NetworkID:
        return cls(NetworkIDType.REALMS, uuid=u)

    @classmethod
    def unset(cls) -> NetworkID:
        return cls(NetworkIDType.UNSET)

    @classmethod
    def parse(cls, text: str) -> NetworkID:
        if not text:
            return cls.unset()
        if all(c in _DIGITS for c in text):
            digits = text.lstrip("0") or "0"
            if len(digits) <= 20 and int(digits) <= U64_MAX:
                return cls.p2p(int(digits))
        hex_text = _uuid_hex(text)
        if hex_text is not None:
            return cls.realms(_uuid.UUID(int=int(hex_text, 16)))
        return cls.unset()

    @classmethod
    def from_json(cls, value: object) -> NetworkID:
        if isinstance(value, int) and _I64_MIN <= value <= U64_MAX:
            return cls.p2p(value & U64_MAX)
        if isinstance(value, int | float):
            return cls.p2p(_real_low_32_bits(float(value)))
        if isinstance(value, str):
            return cls.parse(value)
        return cls.unset()

    def to_json(self) -> int | str | None:
        if self.type is NetworkIDType.P2P:
            return self.value
        if self.type is NetworkIDType.REALMS:
            return str(self.uuid)
        return None

    @property
    def is_valid(self) -> bool:
        if self.type is NetworkIDType.P2P:
            return self.value != 0
        if self.type is NetworkIDType.REALMS:
            return self.uuid is not None and self.uuid.int != 0
        return False

    def __str__(self) -> str:
        if self.type is NetworkIDType.P2P:
            return str(self.value)
        if self.type is NetworkIDType.REALMS:
            return str(self.uuid)
        return ""

    def correlation_id(self) -> str:
        if not self.is_valid:
            return ""
        if self.type is NetworkIDType.REALMS:
            return f"<nethernet>{self.uuid}"
        words = [(self.value >> shift) & 0xFFFF for shift in (48, 32, 16, 0)]
        return "<nethernet>" + "-".join(f"{w:04x}" for w in words)


def _real_low_32_bits(value: float) -> int:
    if not math.isfinite(value):
        return 0
    truncated = math.trunc(value)
    if not _I64_MIN <= truncated <= _I64_MAX:
        return 0
    return truncated & 0xFFFFFFFF


def _uuid_hex(text: str) -> str | None:
    if len(text) == 36:
        if any(text[i] != "-" for i in _DASH_OFFSETS):
            return None
        text = text.replace("-", "")
    if len(text) == 32 and all(c in _HEX_DIGITS for c in text):
        return text
    return None


def new_connection_id() -> int:
    """Draw a fresh random ``u64`` connection id."""
    return int.from_bytes(secrets.token_bytes(8), "little")
