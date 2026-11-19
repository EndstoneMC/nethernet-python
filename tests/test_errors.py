import pytest

from nethernet.errors import (
    ConnectionClosed,
    ConnectionFailed,
    ConnectionFlags,
    SendType,
    SessionError,
)

SESSION_ERRORS = [
    "NONE",
    "DESTINATION_NOT_LOGGED_IN",
    "NEGOTIATION_TIMEOUT",
    "WRONG_TRANSPORT_VERSION",
    "FAILED_TO_CREATE_PEER_CONNECTION",
    "ICE",
    "CONNECT_REQUEST",
    "CONNECT_RESPONSE",
    "CANDIDATE_ADD",
    "INACTIVITY_TIMEOUT",
    "FAILED_TO_CREATE_OFFER",
    "FAILED_TO_CREATE_ANSWER",
    "FAILED_TO_SET_LOCAL_DESCRIPTION",
    "FAILED_TO_SET_REMOTE_DESCRIPTION",
    "NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE",
    "NEGOTIATION_TIMEOUT_WAITING_FOR_ACCEPT",
    "INCOMING_CONNECTION_IGNORED",
    "SIGNALING_PARSING_FAILURE",
    "SIGNALING_UNKNOWN_ERROR",
    "SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED",
    "SIGNALING_BROADCAST_DELIVERY_FAILED",
    "SIGNALING_MESSAGE_DELIVERY_FAILED",
    "SIGNALING_TURN_AUTH_FAILED",
    "SIGNALING_FALLBACK_TO_BEST_EFFORT_DELIVERY",
    "NO_SIGNALING_CHANNEL",
    "NOT_LOGGED_IN",
    "SIGNALING_FAILED_TO_SEND",
    "RELAY_SERVER_CONFIGURATION_RESULT_FAILURE",
    "RELAY_SERVER_CONFIG_PARSING_ERROR_NO_URLS",
    "RELAY_SERVER_CONFIG_PARSING_ERROR_NO_CREDS",
    "RELAY_SERVER_CONFIG_PARSING_ERROR_NO_SERVERS",
    "RELAY_SERVER_CONFIG_PARSING_ERROR_NO_EXPIRATION",
    "DATA_CHANNEL_CLOSED",
    "INTERNAL_ERROR_JSON_SERIALIZATION",
    "INVALID_ARGUMENT",
    "GENERIC_FAILURE",
    "FAILED_TO_CREATE_IDENTITY_ASSERTION",
    "IDENTITY_NOT_ALLOWED",
    "NOT_AUTHENTICATED",
    "NOT_ALLOWED",
    "SERVER_FULL",
]


def test_session_error_has_every_value_from_0_to_40_and_no_other():
    assert [(e.value, e.name) for e in SessionError] == list(enumerate(SESSION_ERRORS))


def test_send_type_values():
    assert [(e.name, e.value) for e in SendType] == [("UNRELIABLE", 0), ("RELIABLE", 1)]


def test_connection_flags_values():
    assert [(name, e.value) for name, e in ConnectionFlags.__members__.items()] == [
        ("NONE", 0),
        ("RELAY_ONLY", 1),
        ("NO_TCP_CANDIDATES", 2),
        ("NO_LOCAL_CANDIDATES", 4),
        ("NO_SERVER_REFLEXIVE_CANDIDATES", 8),
        ("NO_PEER_REFLEXIVE_CANDIDATES", 16),
        ("NO_RELAY_CANDIDATES", 32),
    ]


def test_connection_flags_combine_as_a_bitmask():
    assert ConnectionFlags.RELAY_ONLY | ConnectionFlags.NO_TCP_CANDIDATES == 3


@pytest.mark.parametrize("code", [41, 99, -1, 2**31 - 1])
def test_an_unknown_code_is_still_a_session_error(code):
    error = SessionError(code)
    assert isinstance(error, SessionError)
    assert error == code
    assert int(error) == code


def test_a_known_code_is_its_member():
    assert SessionError(40) is SessionError.SERVER_FULL


def test_connection_failed_carries_an_unknown_code():
    failure = ConnectionFailed(SessionError(99))
    assert failure.error == 99
    assert "99" in str(failure)


def test_connection_closed_carries_an_unknown_code():
    closed = ConnectionClosed(SessionError(99))
    assert closed.error == 99
    assert "99" in str(closed)


@pytest.mark.parametrize("cls", [ConnectionFailed, ConnectionClosed])
def test_a_plain_int_code_is_coerced_to_a_session_error(cls):
    unknown = cls(99)
    assert isinstance(unknown.error, SessionError)
    assert unknown.error == 99
    assert "99" in str(unknown)
    assert cls(40).error is SessionError.SERVER_FULL


def test_connection_closed_defaults_to_none():
    assert ConnectionClosed().error is SessionError.NONE
