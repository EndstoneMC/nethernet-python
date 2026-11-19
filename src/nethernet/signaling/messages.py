from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from nethernet.errors import SessionError, decode_error, encode_error
from nethernet.network_id import U64_MAX


@dataclass(frozen=True)
class ConnectRequest:
    IDENTIFIER = "CONNECTREQUEST"
    connection_id: int
    sdp: str

    def serialize(self) -> str:
        return f"{self.IDENTIFIER} {self.connection_id} {self.sdp}"


@dataclass(frozen=True)
class ConnectResponse:
    IDENTIFIER = "CONNECTRESPONSE"
    connection_id: int
    sdp: str

    def serialize(self) -> str:
        return f"{self.IDENTIFIER} {self.connection_id} {self.sdp}"


@dataclass(frozen=True)
class CandidateAdd:
    IDENTIFIER = "CANDIDATEADD"
    connection_id: int
    candidate: str

    def serialize(self) -> str:
        return f"{self.IDENTIFIER} {self.connection_id} {self.candidate}"


@dataclass(frozen=True)
class ConnectError:
    IDENTIFIER = "CONNECTERROR"
    connection_id: int
    error: SessionError | None

    def serialize(self) -> str:
        return f"{self.IDENTIFIER} {self.connection_id} {encode_error(self.error)}"


SignalingMessage: TypeAlias = ConnectRequest | ConnectResponse | CandidateAdd | ConnectError

_BY_IDENTIFIER = {
    ConnectRequest.IDENTIFIER: ConnectRequest,
    ConnectResponse.IDENTIFIER: ConnectResponse,
    CandidateAdd.IDENTIFIER: CandidateAdd,
    ConnectError.IDENTIFIER: ConnectError,
}


_I32_MIN = -(2**31)
_I32_MAX = 2**31 - 1

_DIGITS = frozenset("0123456789")


def _digit_prefix(text: str) -> str | None:
    end = 0
    while end < len(text) and text[end] in _DIGITS:
        end += 1
    if end == 0:
        return None
    return text[:end].lstrip("0") or "0"


def _parse_connection_id(token: str) -> int | None:
    digits = _digit_prefix(token)
    if digits is None or len(digits) > 20:
        return None
    value = int(digits)
    return value if value <= U64_MAX else None


def _parse_error_code(data: str) -> int | None:
    negative = data[:1] == "-"
    digits = _digit_prefix(data[1:] if negative else data)
    if digits is None or len(digits) > 10:
        return None
    value = -int(digits) if negative else int(digits)
    return value if _I32_MIN <= value <= _I32_MAX else None


def _tokenize(message: str) -> tuple[str, str, str] | None:
    first = message.find(" ")
    if first == -1:
        return None
    second = message.find(" ", first + 1)
    if second == -1:
        return None
    return message[:first], message[first + 1 : second], message[second + 1 :]


def parse(message: str) -> SignalingMessage | None:
    tokens = _tokenize(message)
    if tokens is None:
        return None
    identifier, connection_token, data = tokens

    cls = _BY_IDENTIFIER.get(identifier)
    if cls is None:
        return None

    connection_id = _parse_connection_id(connection_token)
    if connection_id is None:
        return None

    if cls is ConnectError:
        code = _parse_error_code(data)
        if code is None:
            return None
        return ConnectError(connection_id, decode_error(code))

    return cls(connection_id, data)
