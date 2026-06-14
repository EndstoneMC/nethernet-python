"""NetworkID and ConnectionId identifiers — SPEC.md s3.

A ``NetworkID`` identifies a peer and is one of: **P2P** (a ``u64`` rendered as decimal),
**Realms** (a 128-bit UUID), or *unset*. LAN signaling and discovery always use the P2P form.

A ``ConnectionId`` / ``RAWNETWORKID`` is a separate ``u64`` used as the per-session id in
signaling messages (SPEC.md s3.3); it is just an ``int`` here, minted by ``new_connection_id``.
"""

from __future__ import annotations

import secrets
import uuid as _uuid
from dataclasses import dataclass
from enum import Enum

U64_MAX = 0xFFFFFFFFFFFFFFFF


class NetworkIDType(Enum):
    UNSET = 0
    P2P = 1
    REALMS = 2


@dataclass(frozen=True)
class NetworkID:
    """A peer identity: P2P (u64), Realms (UUID), or unset."""

    type: NetworkIDType
    value: int = 0
    uuid: _uuid.UUID | None = None

    # -- constructors ------------------------------------------------------------------

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
        """Parse a string per SPEC.md s3.2: decimal u64 -> P2P, else UUID -> Realms, else unset."""
        # 1. Unsigned decimal integer with no surrounding whitespace, in u64 range -> P2P.
        if text and all(c in "0123456789" for c in text):
            value = int(text)
            if value <= U64_MAX:
                return cls.p2p(value)
        # 2. Parseable UUID -> Realms.
        try:
            return cls.realms(_uuid.UUID(text))
        except (ValueError, AttributeError):
            pass
        # 3. Otherwise unset / invalid.
        return cls.unset()

    # -- queries -----------------------------------------------------------------------

    @property
    def is_valid(self) -> bool:
        return self.type is not NetworkIDType.UNSET

    def __str__(self) -> str:
        if self.type is NetworkIDType.P2P:
            return str(self.value)
        if self.type is NetworkIDType.REALMS:
            return str(self.uuid)
        return ""

    def correlation_id(self) -> str:
        """Diagnostic id ``<nethernet>WWWW-WWWW-WWWW-WWWW`` (MSW first) — SPEC.md s3.4.

        Never appears on the wire. Defined for the P2P form.
        """
        if self.type is not NetworkIDType.P2P:
            raise ValueError("correlation_id is only defined for P2P NetworkIDs")
        words = [(self.value >> shift) & 0xFFFF for shift in (48, 32, 16, 0)]
        return "<nethernet>" + "-".join(f"{w:04x}" for w in words)


def new_connection_id() -> int:
    """Mint a fresh, unpredictable u64 ConnectionId / RAWNETWORKID — SPEC.md s3.3."""
    return secrets.randbits(64)
