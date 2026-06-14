"""Stable enums shared across the protocol — SPEC.md s4.

Member names use PEP8 ``UPPER_SNAKE_CASE``; the trailing comment on each is the spec's name so
the wire-critical numeric values stay easy to audit against SPEC.md s4.
"""

from __future__ import annotations

from enum import IntEnum, IntFlag


class ESessionError(IntEnum):
    """Session error carried in CONNECTERROR and surfaced on session close (SPEC.md s4)."""

    NONE = 0  # None
    DESTINATION_NOT_LOGGED_IN = 1  # DestinationNotLoggedIn
    NEGOTIATION_TIMEOUT = 2  # NegotiationTimeout
    WRONG_TRANSPORT_VERSION = 3  # WrongTransportVersion
    FAILED_TO_CREATE_PEER_CONNECTION = 4  # FailedToCreatePeerConnection
    ICE = 5  # ICE
    CONNECT_REQUEST = 6  # ConnectRequest
    CONNECT_RESPONSE = 7  # ConnectResponse
    CANDIDATE_ADD = 8  # CandidateAdd
    INACTIVITY_TIMEOUT = 9  # InactivityTimeout
    FAILED_TO_CREATE_OFFER = 10  # FailedToCreateOffer
    FAILED_TO_CREATE_ANSWER = 11  # FailedToCreateAnswer
    FAILED_TO_SET_LOCAL_DESCRIPTION = 12  # FailedToSetLocalDescription
    FAILED_TO_SET_REMOTE_DESCRIPTION = 13  # FailedToSetRemoteDescription
    NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE = 14  # NegotiationTimeoutWaitingForResponse
    NEGOTIATION_TIMEOUT_WAITING_FOR_ACCEPT = 15  # NegotiationTimeoutWaitingForAccept
    INCOMING_CONNECTION_IGNORED = 16  # IncomingConnectionIgnored
    SIGNALING_PARSING_FAILURE = 17  # SignalingParsingFailure
    SIGNALING_UNKNOWN_ERROR = 18  # SignalingUnknownError
    SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED = 19  # SignalingUnicastMessageDeliveryFailed
    SIGNALING_BROADCAST_DELIVERY_FAILED = 20  # SignalingBroadcastDeliveryFailed
    SIGNALING_MESSAGE_DELIVERY_FAILED = 21  # SignalingMessageDeliveryFailed
    SIGNALING_TURN_AUTH_FAILED = 22  # SignalingTurnAuthFailed
    SIGNALING_FALLBACK_TO_BEST_EFFORT_DELIVERY = 23  # SignalingFallbackToBestEffortDelivery
    NO_SIGNALING_CHANNEL = 24  # NoSignalingChannel
    NOT_LOGGED_IN = 25  # NotLoggedIn
    SIGNALING_FAILED_TO_SEND = 26  # SignalingFailedToSend
    RELAY_SERVER_CONFIGURATION_RESULT_FAILURE = 27  # RelayServerConfigurationResultFailure
    RELAY_SERVER_CONFIG_PARSING_ERROR_NO_URLS = 28  # ...ResultParsingErrorNoUrls
    RELAY_SERVER_CONFIG_PARSING_ERROR_NO_CREDS = 29  # ...ResultParsingErrorNoCreds
    RELAY_SERVER_CONFIG_PARSING_ERROR_NO_SERVERS = 30  # ...ResultParsingErrorNoServers
    RELAY_SERVER_CONFIG_PARSING_ERROR_NO_EXPIRATION = 31  # ...ResultParsingErrorNoExpiration
    DATA_CHANNEL_CLOSED = 32  # DataChannelClosed
    INTERNAL_ERROR_JSON_SERIALIZATION = 33  # InternalErrorJsonSerialization
    INVALID_ARGUMENT = 34  # InvalidArgument
    GENERIC_FAILURE = 35  # GenericFailure


class ESendType(IntEnum):
    """Application send reliability (SPEC.md s4 / s6.2)."""

    UNRELIABLE = 0  # Unreliable
    RELIABLE = 1  # Reliable


class EConnectionFlags(IntFlag):
    """ICE candidate-filtering flags (SPEC.md s4). ``RELAY_ONLY`` forces relay-only ICE."""

    NONE = 0  # None
    RELAY_ONLY = 1  # RelayOnly
    NO_TCP_CANDIDATES = 2  # NoTCPCandidates
    NO_LOCAL_CANDIDATES = 4  # NoLocalCandidates
    NO_SERVER_REFLEXIVE_CANDIDATES = 8  # NoServerReflexiveCandidates
    NO_PEER_REFLEXIVE_CANDIDATES = 16  # NoPeerReflexiveCandidates
    NO_RELAY_CANDIDATES = 32  # NoRelayCandidates


class NetherNetError(Exception):
    """Base class for all public NetherNet exceptions (docs/api-design.md s3.5)."""


class ConnectionFailed(NetherNetError):  # noqa: N818 - websockets-style name (no Error suffix)
    """``connect()`` never reached the CONNECTED state (negotiation timeout / ICE failure)."""

    def __init__(self, error: ESessionError) -> None:
        self.error = error
        super().__init__(f"connection failed: {error.name} ({int(error)})")


class ConnectionClosed(NetherNetError):  # noqa: N818 - websockets-style name (no Error suffix)
    """``send()``/``recv()`` on a closed connection; ``error == NONE`` for a clean close."""

    def __init__(self, error: ESessionError = ESessionError.NONE) -> None:
        self.error = error
        super().__init__(f"connection closed: {error.name} ({int(error)})")
