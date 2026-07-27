"""Signaling message codec + error enums — SPEC.md s4, s5.2-s5.3."""

from nethernet.errors import ConnectionFlags, SendType, SessionError
from nethernet.signaling.messages import (
    CandidateAdd,
    ConnectError,
    ConnectRequest,
    ConnectResponse,
    parse,
)

# A realistic SDP blob: contains spaces AND newlines (the data token must survive both).
SDP = "v=0\r\no=- 4611731400430051336 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0\r\n"
CANDIDATE = "candidate:1 1 udp 2130706431 10.0.0.5 54321 typ host generation 0"


# --- Enums (SPEC.md s4) ---


def test_send_type_values():
    assert SendType.UNRELIABLE == 0
    assert SendType.RELIABLE == 1


def test_session_error_values_are_stable():
    assert SessionError.NONE == 0
    assert SessionError.ICE == 5
    assert SessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED == 19
    assert SessionError.DATA_CHANNEL_CLOSED == 32
    assert SessionError.GENERIC_FAILURE == 35


def test_connection_flags_are_a_bitmask():
    assert ConnectionFlags.NONE == 0
    assert ConnectionFlags.RELAY_ONLY == 1
    assert ConnectionFlags.NO_RELAY_CANDIDATES == 32
    combo = ConnectionFlags.RELAY_ONLY | ConnectionFlags.NO_TCP_CANDIDATES
    assert combo == 3


# --- Serialization (SPEC.md s5.2): IDENTIFIER + " " + decimal(connId) + " " + data ---


def test_connect_request_serialize():
    assert ConnectRequest(123, SDP).serialize() == f"CONNECTREQUEST 123 {SDP}"


def test_connect_response_serialize():
    assert ConnectResponse(123, SDP).serialize() == f"CONNECTRESPONSE 123 {SDP}"


def test_candidate_add_serialize():
    assert CandidateAdd(7, CANDIDATE).serialize() == f"CANDIDATEADD 7 {CANDIDATE}"


def test_connect_error_serialize_uses_decimal_error():
    msg = ConnectError(7, SessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED)
    assert msg.serialize() == "CONNECTERROR 7 19"


# --- Parsing round-trips: data may contain spaces and newlines (first-two-spaces split) ---


def test_connect_request_roundtrip_preserves_multiline_sdp():
    msg = parse(ConnectRequest(999, SDP).serialize())
    assert msg == ConnectRequest(999, SDP)
    assert msg.sdp == SDP


def test_connect_response_roundtrip():
    assert parse(ConnectResponse(1, SDP).serialize()) == ConnectResponse(1, SDP)


def test_candidate_add_roundtrip_preserves_spaces():
    msg = parse(CandidateAdd(5, CANDIDATE).serialize())
    assert msg == CandidateAdd(5, CANDIDATE)
    assert msg.candidate == CANDIDATE


def test_connect_error_roundtrip_maps_to_enum():
    msg = parse("CONNECTERROR 7 19")
    assert msg == ConnectError(7, SessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED)
    assert msg.error == 19


def test_max_u64_connection_id_parses():
    cid = 0xFFFFFFFFFFFFFFFF
    assert parse(f"CONNECTREQUEST {cid} x").connection_id == cid


# --- Rejection rules (SPEC.md s5.2) ---


def test_unknown_identifier_is_rejected():
    assert parse("GREETINGS 1 hello") is None


def test_fewer_than_three_tokens_is_rejected():
    assert parse("CONNECTREQUEST 123") is None  # no second space
    assert parse("CONNECTREQUEST") is None
    assert parse("") is None


def test_non_u64_connection_id_is_rejected():
    assert parse("CONNECTREQUEST notanumber sdp") is None
    assert parse("CONNECTREQUEST -1 sdp") is None
    assert parse(f"CONNECTREQUEST {2**64} sdp") is None  # overflow


def test_connect_error_with_non_integer_data_is_rejected():
    assert parse("CONNECTERROR 7 notanumber") is None


def test_empty_data_token_is_allowed():
    # "CONNECTREQUEST 5 " has three tokens; the data token is just empty.
    assert parse("CONNECTREQUEST 5 ") == ConnectRequest(5, "")
