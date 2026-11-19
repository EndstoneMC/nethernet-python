from __future__ import annotations

from enum import IntEnum, IntFlag


class SessionError(IntEnum):
    """The error a session closes with, carried in ``CONNECTERROR`` and seen on close."""

    NONE = 0
    DESTINATION_NOT_LOGGED_IN = 1
    NEGOTIATION_TIMEOUT = 2
    WRONG_TRANSPORT_VERSION = 3
    FAILED_TO_CREATE_PEER_CONNECTION = 4
    ICE = 5
    CONNECT_REQUEST = 6
    CONNECT_RESPONSE = 7
    CANDIDATE_ADD = 8
    INACTIVITY_TIMEOUT = 9
    FAILED_TO_CREATE_OFFER = 10
    FAILED_TO_CREATE_ANSWER = 11
    FAILED_TO_SET_LOCAL_DESCRIPTION = 12
    FAILED_TO_SET_REMOTE_DESCRIPTION = 13
    NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE = 14
    NEGOTIATION_TIMEOUT_WAITING_FOR_ACCEPT = 15
    INCOMING_CONNECTION_IGNORED = 16
    SIGNALING_PARSING_FAILURE = 17
    SIGNALING_UNKNOWN_ERROR = 18
    SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED = 19
    SIGNALING_BROADCAST_DELIVERY_FAILED = 20
    SIGNALING_MESSAGE_DELIVERY_FAILED = 21
    SIGNALING_TURN_AUTH_FAILED = 22
    SIGNALING_FALLBACK_TO_BEST_EFFORT_DELIVERY = 23
    NO_SIGNALING_CHANNEL = 24
    NOT_LOGGED_IN = 25
    SIGNALING_FAILED_TO_SEND = 26
    RELAY_SERVER_CONFIGURATION_RESULT_FAILURE = 27
    RELAY_SERVER_CONFIG_PARSING_ERROR_NO_URLS = 28
    RELAY_SERVER_CONFIG_PARSING_ERROR_NO_CREDS = 29
    RELAY_SERVER_CONFIG_PARSING_ERROR_NO_SERVERS = 30
    RELAY_SERVER_CONFIG_PARSING_ERROR_NO_EXPIRATION = 31
    DATA_CHANNEL_CLOSED = 32
    INTERNAL_ERROR_JSON_SERIALIZATION = 33
    INVALID_ARGUMENT = 34
    GENERIC_FAILURE = 35
    FAILED_TO_CREATE_IDENTITY_ASSERTION = 36
    IDENTITY_NOT_ALLOWED = 37
    NOT_AUTHENTICATED = 38
    NOT_ALLOWED = 39
    SERVER_FULL = 40

    @classmethod
    def _missing_(cls, value: object) -> SessionError | None:
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        member = int.__new__(cls, value)
        member._name_ = "UNKNOWN"
        member._value_ = value
        return member


class SendType(IntEnum):
    """How ``send`` delivers a packet: reliable and ordered, or unreliable."""

    UNRELIABLE = 0
    RELIABLE = 1


class ConnectionFlags(IntFlag):
    """Flags that filter which ICE candidates a connection uses."""

    NONE = 0
    RELAY_ONLY = 1
    NO_TCP_CANDIDATES = 2
    NO_LOCAL_CANDIDATES = 4
    NO_SERVER_REFLEXIVE_CANDIDATES = 8
    NO_PEER_REFLEXIVE_CANDIDATES = 16
    NO_RELAY_CANDIDATES = 32


class NetherNetError(Exception):
    """Base class for every public NetherNet exception."""


class ConnectionFailed(NetherNetError):  # noqa: N818 - websockets-style name
    """``connect()`` never reached the connected state."""

    def __init__(self, error: SessionError | int) -> None:
        error = SessionError(error)
        self.error = error
        super().__init__(f"connection failed: {error.name} ({int(error)})")


class ConnectionClosed(NetherNetError):  # noqa: N818 - websockets-style name
    """The connection is closed. ``error`` is ``SessionError.NONE`` for a clean close."""

    def __init__(self, error: SessionError | int = SessionError.NONE) -> None:
        error = SessionError(error)
        self.error = error
        super().__init__(f"connection closed: {error.name} ({int(error)})")
