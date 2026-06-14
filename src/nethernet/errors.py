"""Stable enums shared across the protocol — SPEC.md s4.

Member names mirror the spec's enum names verbatim (``None`` is spelled ``NONE`` since it is a
Python keyword) so the wire-critical numeric values stay easy to audit against SPEC.md s4.
"""

from __future__ import annotations

from enum import IntEnum, IntFlag


class ESessionError(IntEnum):
    """Session error carried in CONNECTERROR and surfaced on session close (SPEC.md s4)."""

    NONE = 0
    DestinationNotLoggedIn = 1
    NegotiationTimeout = 2
    WrongTransportVersion = 3
    FailedToCreatePeerConnection = 4
    ICE = 5
    ConnectRequest = 6
    ConnectResponse = 7
    CandidateAdd = 8
    InactivityTimeout = 9
    FailedToCreateOffer = 10
    FailedToCreateAnswer = 11
    FailedToSetLocalDescription = 12
    FailedToSetRemoteDescription = 13
    NegotiationTimeoutWaitingForResponse = 14
    NegotiationTimeoutWaitingForAccept = 15
    IncomingConnectionIgnored = 16
    SignalingParsingFailure = 17
    SignalingUnknownError = 18
    SignalingUnicastMessageDeliveryFailed = 19
    SignalingBroadcastDeliveryFailed = 20
    SignalingMessageDeliveryFailed = 21
    SignalingTurnAuthFailed = 22
    SignalingFallbackToBestEffortDelivery = 23
    NoSignalingChannel = 24
    NotLoggedIn = 25
    SignalingFailedToSend = 26
    RelayServerConfigurationResultFailure = 27
    RelayServerConfigurationResultParsingErrorNoUrls = 28
    RelayServerConfigurationResultParsingErrorNoCreds = 29
    RelayServerConfigurationResultParsingErrorNoServers = 30
    RelayServerConfigurationResultParsingErrorNoExpiration = 31
    DataChannelClosed = 32
    InternalErrorJsonSerialization = 33
    InvalidArgument = 34
    GenericFailure = 35


class ESendType(IntEnum):
    """Application send reliability (SPEC.md s4 / s6.2)."""

    Unreliable = 0
    Reliable = 1


class EConnectionFlags(IntFlag):
    """ICE candidate-filtering flags (SPEC.md s4). ``RelayOnly`` forces relay-only ICE."""

    NONE = 0
    RelayOnly = 1
    NoTCPCandidates = 2
    NoLocalCandidates = 4
    NoServerReflexiveCandidates = 8
    NoPeerReflexiveCandidates = 16
    NoRelayCandidates = 32
