"""Signaling message codec — SPEC.md s5.2-s5.3.

Every signaling message is an ASCII string of exactly three space-separated tokens::

    <IDENTIFIER> <connectionId> <data>

The ``data`` token may itself contain spaces and newlines (SDP blobs do), so parsing splits on
the **first two spaces only** and treats the remainder as ``data``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from nethernet.errors import ESessionError
from nethernet.network_id import U64_MAX


@dataclass(frozen=True)
class ConnectRequest:
    """Dialer -> listener: the offerer's full SDP offer (SPEC.md s5.3)."""

    IDENTIFIER = "CONNECTREQUEST"
    connection_id: int
    sdp: str

    def serialize(self) -> str:
        return f"{self.IDENTIFIER} {self.connection_id} {self.sdp}"


@dataclass(frozen=True)
class ConnectResponse:
    """Listener -> dialer: the answerer's full SDP answer (SPEC.md s5.3)."""

    IDENTIFIER = "CONNECTRESPONSE"
    connection_id: int
    sdp: str

    def serialize(self) -> str:
        return f"{self.IDENTIFIER} {self.connection_id} {self.sdp}"


@dataclass(frozen=True)
class CandidateAdd:
    """Both directions: one trickle ICE candidate (the ``candidate:`` value) (SPEC.md s5.3-s5.4)."""

    IDENTIFIER = "CANDIDATEADD"
    connection_id: int
    candidate: str

    def serialize(self) -> str:
        return f"{self.IDENTIFIER} {self.connection_id} {self.candidate}"


@dataclass(frozen=True)
class ConnectError:
    """Either direction: an ``ESessionError`` as decimal ASCII (SPEC.md s5.3)."""

    IDENTIFIER = "CONNECTERROR"
    connection_id: int
    error: int  # an ESessionError when known; kept as int to tolerate unknown future codes

    def serialize(self) -> str:
        return f"{self.IDENTIFIER} {self.connection_id} {int(self.error)}"


SignalingMessage = Union[ConnectRequest, ConnectResponse, CandidateAdd, ConnectError]

_BY_IDENTIFIER = {
    ConnectRequest.IDENTIFIER: ConnectRequest,
    ConnectResponse.IDENTIFIER: ConnectResponse,
    CandidateAdd.IDENTIFIER: CandidateAdd,
    ConnectError.IDENTIFIER: ConnectError,
}


def _parse_u64(token: str) -> int | None:
    """A bare unsigned decimal in u64 range (no sign, no whitespace), else None."""
    if not token or not token.isascii() or not all(c in "0123456789" for c in token):
        return None
    value = int(token)
    return value if value <= U64_MAX else None


def _tokenize(message: str) -> tuple[str, str, str] | None:
    """Split on the first two spaces; the remainder is ``data``. None if <3 tokens."""
    first = message.find(" ")
    if first == -1:
        return None
    second = message.find(" ", first + 1)
    if second == -1:
        return None
    return message[:first], message[first + 1 : second], message[second + 1 :]


def parse(message: str) -> SignalingMessage | None:
    """Parse a signaling string into a message, or None if it must be rejected (SPEC.md s5.2)."""
    tokens = _tokenize(message)
    if tokens is None:
        return None
    identifier, connection_token, data = tokens

    cls = _BY_IDENTIFIER.get(identifier)
    if cls is None:
        return None

    connection_id = _parse_u64(connection_token)
    if connection_id is None:
        return None

    if cls is ConnectError:
        error = _parse_int(data)
        if error is None:
            return None
        try:
            error = ESessionError(error)
        except ValueError:
            pass  # tolerate codes outside the known enum
        return ConnectError(connection_id, error)

    return cls(connection_id, data)


def _parse_int(token: str) -> int | None:
    """A signed decimal integer (the CONNECTERROR code), else None."""
    body = token[1:] if token[:1] == "-" else token
    if not body or not all(c in "0123456789" for c in body):
        return None
    return int(token)
