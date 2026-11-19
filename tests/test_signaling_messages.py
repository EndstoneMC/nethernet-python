import pytest

from nethernet.errors import SessionError
from nethernet.signaling.messages import (
    CandidateAdd,
    ConnectError,
    ConnectRequest,
    ConnectResponse,
    parse,
)

SDP = "v=0\r\no=- 4611731400430051336 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0\r\n"
CANDIDATE = "candidate:1 1 udp 2130706431 10.0.0.5 54321 typ host generation 0"


@pytest.mark.conformance
def test_messages_serialize_as_identifier_connection_id_and_data_joined_by_spaces():
    assert ConnectRequest(123, SDP).serialize() == "CONNECTREQUEST 123 " + SDP
    assert ConnectResponse(123, SDP).serialize() == "CONNECTRESPONSE 123 " + SDP
    assert CandidateAdd(7, CANDIDATE).serialize() == "CANDIDATEADD 7 " + CANDIDATE
    assert ConnectError(7, SessionError.ICE).serialize() == "CONNECTERROR 7 5"
    assert ConnectError(7, None).serialize() == "CONNECTERROR 7 0"
    assert ConnectError(0, SessionError(-1)).serialize() == "CONNECTERROR 0 -1"


@pytest.mark.conformance
def test_max_u64_connection_id_serializes_unsigned():
    assert ConnectRequest(2**64 - 1, "x").serialize() == "CONNECTREQUEST 18446744073709551615 x"


@pytest.mark.conformance
def test_each_identifier_parses_to_its_message():
    assert parse("CONNECTREQUEST 999 " + SDP) == ConnectRequest(999, SDP)
    assert parse("CONNECTRESPONSE 1 " + SDP) == ConnectResponse(1, SDP)
    assert parse("CANDIDATEADD 5 " + CANDIDATE) == CandidateAdd(5, CANDIDATE)
    assert parse("CONNECTERROR 7 19") == ConnectError(
        7, SessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED
    )


def test_data_keeps_every_space_and_newline_after_the_second_space():
    assert parse("CANDIDATEADD 1  a b\nc ") == CandidateAdd(1, " a b\nc ")


def test_empty_data_is_allowed():
    assert parse("CONNECTREQUEST 5 ") == ConnectRequest(5, "")


@pytest.mark.parametrize("message", ["", "CONNECTREQUEST", "CONNECTREQUEST 123", "CONNECTREQUEST "])
def test_message_without_two_spaces_is_dropped(message):
    assert parse(message) is None


@pytest.mark.parametrize(
    "message", ["GREETINGS 1 x", "connectrequest 1 x", "ConnectRequest 1 x", " CONNECTREQUEST 1 x"]
)
def test_identifier_must_match_exactly(message):
    assert parse(message) is None


def test_empty_connection_id_token_is_dropped():
    assert parse("CONNECTREQUEST  1 x") is None


def test_connection_id_reads_its_digit_prefix():
    assert parse("CONNECTREQUEST 123abc sdp") == ConnectRequest(123, "sdp")
    assert parse("CONNECTREQUEST 18446744073709551615z x") == ConnectRequest(2**64 - 1, "x")


def test_connection_id_zero_and_leading_zeros_parse():
    assert parse("CONNECTREQUEST 0 x") == ConnectRequest(0, "x")
    assert parse("CONNECTREQUEST 007 x") == ConnectRequest(7, "x")


@pytest.mark.parametrize(
    "token", ["notanumber", "-1", "+1", "abc123", "18446744073709551616", "9" * 5000]
)
def test_connection_id_without_digits_or_above_u64_is_dropped(token):
    assert parse(f"CONNECTREQUEST {token} sdp") is None


def test_connect_error_reads_a_signed_value():
    assert parse("CONNECTERROR 7 0") == ConnectError(7, None)
    assert parse("CONNECTERROR 7 40") == ConnectError(7, SessionError.SERVER_FULL)
    assert parse("CONNECTERROR 7 -1") == ConnectError(7, SessionError(-1))
    assert parse("CONNECTERROR 7 2147483647") == ConnectError(7, SessionError(2147483647))
    assert parse("CONNECTERROR 7 -2147483648") == ConnectError(7, SessionError(-2147483648))


@pytest.mark.parametrize("data", ["", "-", "+5", " 5", "notanumber"])
def test_connect_error_without_digits_is_dropped(data):
    assert parse("CONNECTERROR 7 " + data) is None


def test_connect_error_with_an_unknown_code_keeps_the_code():
    msg = parse("CONNECTERROR 7 99")
    assert msg == ConnectError(7, SessionError(99))
    assert isinstance(msg.error, SessionError)
    assert msg.error == 99


def test_connect_error_ignores_characters_after_its_digits():
    assert parse("CONNECTERROR 7 5abc") == ConnectError(7, SessionError.ICE)


@pytest.mark.parametrize("data", ["2147483648", "-2147483649", "9" * 5000])
def test_connect_error_outside_the_32_bit_range_is_dropped(data):
    assert parse("CONNECTERROR 7 " + data) is None
