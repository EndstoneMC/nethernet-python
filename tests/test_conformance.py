"""Conformance / differential fixtures — SPEC.md s11.

These assert byte-exact output against hand-derived golden vectors (independent of the
implementation): signaling strings (s5.2), discovery packets (s7), the sealed envelope (s8,
matched against an OpenSSL vector), and the fragmentation fixtures (s11.4). Run with::

    uv run pytest -m conformance
"""

import pytest

from nethernet.discovery.crypto import Envelope
from nethernet.discovery.packets import DiscoveryMessage, DiscoveryRequest, DiscoveryResponse
from nethernet.discovery.packets import parse as parse_discovery
from nethernet.errors import SendType, SessionError
from nethernet.signaling.messages import (
    CandidateAdd,
    ConnectError,
    ConnectRequest,
    ConnectResponse,
    parse,
)
from nethernet.transport.framing import FRAGMENT_SIZE, PacketQueue

pytestmark = pytest.mark.conformance

SENDER = 0x1122334455667788
RECIPIENT = 0x99AABBCCDDEEFF00


# --- s11.1/s5.2: signaling string round-trips (byte-exact) ---


def test_signaling_serialization_is_byte_exact():
    sdp = "v=0\r\na=foo bar baz\r\n"  # contains spaces and newlines
    assert ConnectRequest(4242, sdp).serialize() == f"CONNECTREQUEST 4242 {sdp}"
    assert ConnectResponse(1, sdp).serialize() == f"CONNECTRESPONSE 1 {sdp}"
    assert CandidateAdd(7, "candidate:x").serialize() == "CANDIDATEADD 7 candidate:x"
    assert ConnectError(7, SessionError.ICE).serialize() == "CONNECTERROR 7 5"


def test_signaling_roundtrips_reproduce_input():
    sdp = "v=0\r\no=- 1 2 IN IP4 0.0.0.0\r\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
    for message in (
        ConnectRequest(4242, sdp),
        ConnectResponse(4242, sdp),
        CandidateAdd(4242, "candidate:1 1 udp 2130706431 10.0.0.5 5000 typ host"),
        ConnectError(4242, SessionError.NEGOTIATION_TIMEOUT),
    ):
        text = message.serialize()
        assert parse(text).serialize() == text


# --- s11.1/s11.2/s7: discovery packets (golden hex derived from the spec layout) ---


def test_discovery_request_golden_bytes():
    expected = bytes.fromhex("14000000" "8877665544332211" "0000000000000000")
    assert DiscoveryRequest(SENDER).serialize() == expected
    assert parse_discovery(expected) == DiscoveryRequest(SENDER)


def test_discovery_response_golden_bytes():
    # PacketLength=26, type=1, sender, pad, ApplicationDataLength=2, data="AB".
    expected = bytes.fromhex(
        "1a000100" "8877665544332211" "0000000000000000" "02000000" "4142"
    )
    assert DiscoveryResponse(SENDER, b"AB").serialize() == expected
    assert parse_discovery(expected) == DiscoveryResponse(SENDER, b"AB")


def test_discovery_message_golden_bytes():
    # PacketLength=34, type=2, sender, pad, recipient, MessageDataLength=2, data="AB".
    expected = bytes.fromhex(
        "22000200" "8877665544332211" "0000000000000000" "00ffeeddccbbaa99" "02000000" "4142"
    )
    assert DiscoveryMessage(SENDER, RECIPIENT, b"AB").serialize() == expected
    assert parse_discovery(expected) == DiscoveryMessage(SENDER, RECIPIENT, b"AB")


# --- s11.1/s8: sealed envelope matches an independent OpenSSL vector and round-trips ---


def test_sealed_envelope_matches_openssl_vector_and_opens():
    app_id = 0xDEADBEEF
    golden = bytes.fromhex(
        "365cc9aeaff3358d65f08e11c57a060efb1d1cda080840ad6a4a41e27d3d7990"
        "a37c27b89c42919b52d8a12cc996a384"
    )
    envelope = Envelope(app_id)
    assert envelope.seal(b"NetherNet") == golden  # byte-identical to OpenSSL
    assert envelope.open(golden) == b"NetherNet"  # so it verifies under our impl too


def test_sealed_discovery_packet_round_trips_through_envelope():
    envelope = Envelope(0xDEADBEEF)
    packet = DiscoveryMessage(SENDER, RECIPIENT, b"CONNECTREQUEST 5 v=0...")
    assert parse_discovery(envelope.open(envelope.seal(packet.serialize()))) == packet


# --- s11.4: fragmentation fixtures ---


@pytest.mark.parametrize("size", [1, FRAGMENT_SIZE, FRAGMENT_SIZE + 1, FRAGMENT_SIZE * 4 + 100])
def test_fragmentation_fixture_reassembles_with_countdown_headers(size):
    out: list[bytes] = []
    q = PacketQueue(out.append, lambda _data: None)
    payload = bytes(i % 256 for i in range(size))

    assert q.push(payload, SendType.RELIABLE) is True

    expected_fragments = (size - 1) // FRAGMENT_SIZE + 1
    assert len(out) == expected_fragments
    # Headers count down to 0 (the final fragment is always 0).
    assert [fragment[0] for fragment in out] == list(range(expected_fragments - 1, -1, -1))

    for fragment in out:
        q.on_reliable_message(fragment)
    assert q.read() == payload
