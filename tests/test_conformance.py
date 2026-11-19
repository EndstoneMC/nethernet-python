import asyncio
import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from nethernet.discovery.crypto import Envelope
from nethernet.discovery.packets import (
    DiscoveryMessage,
    DiscoveryRequest,
    DiscoveryResponse,
    HostAdvertisement,
)
from nethernet.discovery.packets import parse as parse_discovery
from nethernet.errors import SendType, SessionError
from nethernet.identity import (
    IdentitySigner,
    build_envelope,
    canonical_json,
    fingerprint_payload,
    insert_identity,
)
from nethernet.network_id import NetworkID
from nethernet.signaling.http import HttpSignalingServer
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

HTTP_TLS_ALERT = bytes.fromhex("15030100020228")
HTTP_PING_ANSWER = bytes.fromhex(
    "485454502f312e3120323030204f4b0d0a436f6e74656e742d547970653a206170706c6963617469"
    "6f6e2f6a736f6e0d0a436f6e74656e742d4c656e6774683a203132380d0a436f6e6e656374696f6e"
    "3a20636c6f73650d0a0d0a7b226e616d65223a2244656469636174656420536572766572222c2270"
    "726f746f636f6c223a323232352c2276657273696f6e223a22312e32362e3630222c226c6576656c"
    "223a22426564726f636b206c6576656c222c22706c6179657273223a302c226d6178506c61796572"
    "73223a31302c2267616d6554797065223a307d"
)
HTTP_EMPTY_PING_ANSWER = bytes.fromhex(
    "485454502f312e3120323030204f4b0d0a436f6e74656e742d547970653a206170706c6963617469"
    "6f6e2f6a736f6e0d0a436f6e74656e742d4c656e6774683a20300d0a436f6e6e656374696f6e3a20"
    "636c6f73650d0a0d0a"
)
HTTP_EXPECTED_ID_ANSWER = bytes.fromhex(
    "485454502f312e3120343030204578706563746564202f76312f6a6f696e2f7b6e6574776f726b49"
    "647d0d0a436f6e74656e742d547970653a20746578742f706c61696e0d0a436f6e74656e742d4c65"
    "6e6774683a2032390d0a436f6e6e656374696f6e3a20636c6f73650d0a0d0a457870656374656420"
    "2f76312f6a6f696e2f7b6e6574776f726b49647d"
)
HTTP_INVALID_ID_ANSWER = bytes.fromhex(
    "485454502f312e312034303020496e76616c6964206e6574776f726b49640d0a436f6e74656e742d"
    "547970653a20746578742f706c61696e0d0a436f6e74656e742d4c656e6774683a2031370d0a436f"
    "6e6e656374696f6e3a20636c6f73650d0a0d0a496e76616c6964206e6574776f726b4964"
)
HTTP_MISSING_OFFER_ANSWER = bytes.fromhex(
    "485454502f312e3120343030204d697373696e6720534450206f6666657220696e20726571756573"
    "7420626f64790d0a436f6e74656e742d547970653a20746578742f706c61696e0d0a436f6e74656e"
    "742d4c656e6774683a2033330d0a436f6e6e656374696f6e3a20636c6f73650d0a0d0a4d69737369"
    "6e6720534450206f6666657220696e207265717565737420626f6479"
)
HTTP_NOT_FOUND_ANSWER = bytes.fromhex(
    "485454502f312e3120343034204e6f7420466f756e640d0a436f6e74656e742d547970653a207465"
    "78742f706c61696e0d0a436f6e74656e742d4c656e6774683a20390d0a436f6e6e656374696f6e3a"
    "20636c6f73650d0a0d0a4e6f7420466f756e64"
)
HTTP_GATEWAY_TIMEOUT_ANSWER = bytes.fromhex(
    "485454502f312e312035303420476174657761792054696d656f75740d0a436f6e74656e742d5479"
    "70653a20746578742f706c61696e0d0a436f6e74656e742d4c656e6774683a2031350d0a436f6e6e"
    "656374696f6e3a20636c6f73650d0a0d0a476174657761792054696d656f7574"
)
HTTP_INTERNAL_ERROR_ANSWER = bytes.fromhex(
    "485454502f312e312035303020496e7465726e616c20536572766572204572726f720d0a436f6e74"
    "656e742d4c656e6774683a20300d0a436f6e6e656374696f6e3a20636c6f73650d0a0d0a"
)
HTTP_SDP_ANSWER = bytes.fromhex(
    "485454502f312e3120323030204f4b0d0a436f6e74656e742d547970653a206170706c6963617469"
    "6f6e2f7364700d0a436f6e74656e742d4c656e6774683a2031300d0a436f6e6e656374696f6e3a20"
    "636c6f73650d0a0d0a763d300d0a613d780d0a"
)
HTTP_REJECTION_ANSWER = bytes.fromhex(
    "485454502f312e3120323030204f4b0d0a436f6e74656e742d547970653a206170706c6963617469"
    "6f6e2f7364700d0a436f6e74656e742d4c656e6774683a20320d0a436f6e6e656374696f6e3a2063"
    "6c6f73650d0a0d0a3337"
)
IDENTITY_ENVELOPE_JSON = bytes.fromhex(
    "7b22617373657274696f6e223a227b5c2266696e6765727072696e74735c223a5c22465c222c5c22"
    "746f6b656e5c223a5c22545c227d222c22696470223a7b22646f6d61696e223a2273656c66222c22"
    "70726f746f636f6c223a2264656661756c74227d7d"
)
IDENTITY_FINGERPRINT_PAYLOAD = bytes.fromhex(
    "7b2266696e6765727072696e74223a5b7b22616c676f726974686d223a227368612d323536222c22"
    "646967657374223a2234413a41443a42393a42313a33463a38323a31383a33423a35343a30323a31"
    "323a44463a33453a35443a34393a36423a31393a45353a37433a4142227d5d7d"
)
IDENTITY_INSERTED_SDP = bytes.fromhex(
    "763d300d0a733d2d0d0a613d6964656e746974793a51554a440d0a6d3d6170706c69636174696f6e"
    "2039205544502f44544c532f53435450207765627274632d646174616368616e6e656c0d0a6d3d61"
    "7564696f20392052545020300d0a"
)
IDENTITY_JSON_ESCAPES = bytes.fromhex(
    "7b2261223a225c225c5c2f5c625c745c6e5c665c725c75303030315c75303031415c75303031467fc3a9227d"
)
IDENTITY_OPERATOR_TOKEN_HEADER = bytes.fromhex(
    "7b22616c67223a224553333834222c22783575223a224d485977454159484b6f5a497a6a30434151"
    "59464b3445454143494459674145716f664b4972364c4254654f7363636538794374644734644f32"
    "4b4c703575595766644234494a554b6a685641764a647631557062447055586a6879646771334e68"
    "66655370596d4c4739646e70692f6b704c634b666a304862306f6d68523836646f7845375877754d"
    "414b594c484f485836426e5870444858795136673566227d0a"
)
IDENTITY_OPERATOR_TOKEN_PAYLOAD = bytes.fromhex(
    "7b2263706b223a7b22637276223a22502d333834222c226b7479223a224543222c2278223a22716f"
    "664b4972364c4254654f7363636538794374644734644f324b4c703575595766644234494a554b6a"
    "685641764a647631557062447055586a687964677133222c2279223a224e6866655370596d4c4739"
    "646e70695f6b704c634b666a304862306f6d68523836646f7845375877754d414b594c484f485836"
    "426e5870444858795136673566227d2c22657870223a313036302c22696174223a313030307d0a"
)


def test_signaling_serialization_is_byte_exact():
    sdp = "v=0\r\na=foo bar baz\r\n"
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


def test_discovery_request_golden_bytes():
    expected = bytes.fromhex("1400 0000 8877665544332211 0000000000000000")
    assert DiscoveryRequest(SENDER).serialize() == expected
    assert parse_discovery(expected) == DiscoveryRequest(SENDER)


def test_discovery_response_golden_bytes():
    expected = bytes.fromhex("1a00 0100 8877665544332211 0000000000000000 02000000 4142")
    assert DiscoveryResponse(SENDER, b"AB").serialize() == expected
    assert parse_discovery(expected) == DiscoveryResponse(SENDER, b"AB")


def test_discovery_message_golden_bytes():
    expected = bytes.fromhex(
        "2200 0200 8877665544332211 0000000000000000 00ffeeddccbbaa99 02000000 4142"
    )
    assert DiscoveryMessage(SENDER, RECIPIENT, b"AB").serialize() == expected
    assert parse_discovery(expected) == DiscoveryMessage(SENDER, RECIPIENT, b"AB")


def test_discovery_response_length_fields_clamp_at_1148_golden_bytes():
    payload = b"z" * 1200
    expected = bytes.fromhex("9404 0100 8877665544332211 0000000000000000 7c040000") + payload
    assert DiscoveryResponse(SENDER, payload).serialize() == expected
    assert parse_discovery(expected) == DiscoveryResponse(SENDER, payload)


def test_discovery_message_length_fields_clamp_at_1140_golden_bytes():
    payload = b"v" * 1870
    expected = (
        bytes.fromhex("9404 0200 8877665544332211 0000000000000000 00ffeeddccbbaa99 74040000")
        + payload
    )
    assert DiscoveryMessage(SENDER, RECIPIENT, payload).serialize() == expected
    assert parse_discovery(expected) == DiscoveryMessage(SENDER, RECIPIENT, payload)


def test_discovery_first_discovery_probe_golden_bytes():
    expected = bytes.fromhex(
        "2000 0200 0000000000000000 0000000000000000 00ffeeddccbbaa99 00000000"
    )
    assert DiscoveryMessage(0, RECIPIENT, b"").serialize() == expected


def test_discovery_datagram_longer_than_its_packet_length_is_sized_from_the_datagram():
    datagram = bytes.fromhex("1a00 0100 8877665544332211 0000000000000000 02000000 41424344")
    assert parse_discovery(datagram) == DiscoveryResponse(SENDER, b"ABCD")


def test_discovery_datagram_shorter_than_its_packet_length_is_dropped():
    datagram = bytes.fromhex("1b00 0100 8877665544332211 0000000000000000 03000000 4142")
    assert parse_discovery(datagram) is None


STOCK_ADVERTISEMENT = (
    b"07"
    b"1044656469636174656420536572766572"
    b"e222"
    b"07312e32362e3630"
    b"0d426564726f636b206c6576656c"
    b"00"
    b"14"
    b"00"
    b"00000100"
    b"1031613262336334643565366637303831"
    b"08"
)


def stock_advertisement(**overrides):
    fields = dict(
        name="Dedicated Server",
        level="Bedrock level",
        players=0,
        max_players=10,
        game_type=0,
        editor=False,
        hardcore=False,
        online_auth=True,
        self_signed_auth=False,
        nonce="1a2b3c4d5e6f7081",
        connection=4,
    )
    fields.update(overrides)
    return HostAdvertisement(**fields)


def test_host_advertisement_golden_record():
    advertisement = stock_advertisement()
    assert advertisement.serialize() == STOCK_ADVERTISEMENT
    assert len(STOCK_ADVERTISEMENT) == 134
    assert HostAdvertisement.parse(STOCK_ADVERTISEMENT) == advertisement
    assert len(DiscoveryResponse(SENDER, STOCK_ADVERTISEMENT).serialize()) == 158


def test_host_advertisement_zigzag_fields_golden_bytes():
    data = stock_advertisement(players=-1, max_players=200, game_type=1, connection=7).serialize()
    record = bytes.fromhex(data.decode())
    assert record[42:46] == bytes.fromhex("01 9003 02")
    assert record[-1:] == bytes.fromhex("0e")


def test_host_advertisement_parses_any_data_version():
    record = b"08" + STOCK_ADVERTISEMENT[2:]
    assert HostAdvertisement.parse(record) == stock_advertisement(data_version=8)


def test_host_advertisement_rejects_a_truncated_record():
    assert HostAdvertisement.parse(STOCK_ADVERTISEMENT[:-2]) is None
    assert HostAdvertisement.parse(b"not hex") is None


def test_sealed_envelope_matches_openssl_vector_and_opens():
    app_id = 0xDEADBEEF
    golden = bytes.fromhex(
        "365cc9aeaff3358d65f08e11c57a060efb1d1cda080840ad6a4a41e27d3d7990"
        "a37c27b89c42919b52d8a12cc996a384"
    )
    envelope = Envelope(app_id)
    assert envelope.seal(b"NetherNet") == golden
    assert envelope.open(golden) == b"NetherNet"


def test_sealed_discovery_packet_round_trips_through_envelope():
    envelope = Envelope(0xDEADBEEF)
    packet = DiscoveryMessage(SENDER, RECIPIENT, b"CONNECTREQUEST 5 v=0...")
    assert parse_discovery(envelope.open(envelope.seal(packet.serialize()))) == packet


@pytest.mark.parametrize(
    ("size", "headers"),
    [
        (1, [0]),
        (FRAGMENT_SIZE, [0]),
        (FRAGMENT_SIZE + 1, [1, 0]),
        (FRAGMENT_SIZE * 4 + 100, [4, 3, 2, 1, 0]),
    ],
)
def test_fragmentation_fixture_reassembles_with_countdown_headers(size, headers):
    out: list[bytes] = []
    q = PacketQueue(out.append, lambda _data: None)
    payload = bytes(i % 256 for i in range(size))

    assert q.push(payload, SendType.RELIABLE) is True

    assert [fragment[0] for fragment in out] == headers

    for fragment in out:
        assert q.on_reliable_message(fragment) is True
    assert q.read() == payload


def test_fragment_goldens_at_the_default_fragment_size():
    out: list[bytes] = []
    q = PacketQueue(out.append, out.append)
    assert q.push(bytes.fromhex("ab"), SendType.RELIABLE) is True
    assert q.push(bytes.fromhex("cdef"), SendType.UNRELIABLE) is True
    assert out == [bytes.fromhex("00ab"), bytes.fromhex("00cdef")]


def test_fragment_goldens_at_fragment_size_plus_one():
    out: list[bytes] = []
    q = PacketQueue(out.append, lambda _data: None)
    payload = bytes(FRAGMENT_SIZE) + bytes.fromhex("5a")
    assert q.push(payload, SendType.RELIABLE) is True
    assert out == [bytes.fromhex("01") + bytes(FRAGMENT_SIZE), bytes.fromhex("005a")]


def test_fragment_goldens_for_a_four_fragment_packet():
    out: list[bytes] = []
    q = PacketQueue(out.append, lambda _data: None, fragment_size=2)
    assert q.push(bytes.fromhex("11223344556677"), SendType.RELIABLE) is True
    assert out == [
        bytes.fromhex("031122"),
        bytes.fromhex("023344"),
        bytes.fromhex("015566"),
        bytes.fromhex("0077"),
    ]


async def http_exchange(data: bytes, **kwargs) -> bytes:
    reply = kwargs.pop("reply", None)
    server: HttpSignalingServer

    def on_signal(sender: NetworkID, message: str) -> None:
        if reply is not None:
            server.send(sender, reply.format(id=message.split(" ")[1]))

    server = HttpSignalingServer(on_signal, host="127.0.0.1", port=0, **kwargs)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
        writer.write(data)
        await writer.drain()
        answer = await asyncio.wait_for(reader.read(), 5)
        writer.close()
    finally:
        await server.aclose()
    return answer


def http_post(path: str, body: bytes) -> bytes:
    return f"POST {path} HTTP/1.1\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body


async def test_http_tls_client_hello_gets_the_handshake_failure_alert_golden_bytes():
    hello = bytes.fromhex("16030100c8010000c40303") + bytes(32)
    assert await http_exchange(hello) == HTTP_TLS_ALERT


async def test_http_ping_answer_golden_bytes():
    ping = {
        "name": "Dedicated Server",
        "protocol": 2225,
        "version": "1.26.60",
        "level": "Bedrock level",
        "players": 0,
        "maxPlayers": 10,
        "gameType": 0,
    }
    request = b"GET /v1/join HTTP/1.1\r\n\r\n"
    assert await http_exchange(request, ping=lambda: ping) == HTTP_PING_ANSWER
    assert await http_exchange(request) == HTTP_EMPTY_PING_ANSWER


@pytest.mark.parametrize(
    ("request_bytes", "expected"),
    [
        (http_post("/v1/join/x", b"o"), HTTP_EXPECTED_ID_ANSWER),
        (http_post("/v1/join/0", b"o"), HTTP_INVALID_ID_ANSWER),
        (http_post("/v1/join/5", b""), HTTP_MISSING_OFFER_ANSWER),
        (b"GET / HTTP/1.1\r\n\r\n", HTTP_NOT_FOUND_ANSWER),
    ],
)
async def test_http_error_answer_golden_bytes(request_bytes, expected):
    assert await http_exchange(request_bytes) == expected


async def test_http_gateway_timeout_answer_golden_bytes():
    answer = await http_exchange(http_post("/v1/join/5", b"v=0\r\n"), join_timeout=0.2)
    assert answer == HTTP_GATEWAY_TIMEOUT_ANSWER


async def test_http_internal_server_error_answer_golden_bytes():
    def failing_ping() -> None:
        raise RuntimeError("ping failed")

    answer = await http_exchange(b"GET /v1/join HTTP/1.1\r\n\r\n", ping=failing_ping)
    assert answer == HTTP_INTERNAL_ERROR_ANSWER


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("CONNECTRESPONSE {id} v=0\r\na=x\r\n", HTTP_SDP_ANSWER),
        ("CONNECTERROR {id} 37", HTTP_REJECTION_ANSWER),
    ],
)
async def test_http_first_signal_answer_golden_bytes(reply, expected):
    answer = await http_exchange(http_post("/v1/join/123", b"v=0\r\n"), reply=reply)
    assert answer == expected


def test_identity_envelope_golden_bytes():
    assert base64.b64decode(build_envelope("self", "T", "F")) == IDENTITY_ENVELOPE_JSON


def test_identity_fingerprint_payload_golden_bytes():
    sdp = (
        "v=0\r\n"
        "a=fingerprint:sha-256 4A:AD:B9:B1:3F:82:18:3B:54:02:12:DF:3E:5D:49:6B:19:E5:7C:AB\r\n"
        "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
    )
    assert fingerprint_payload(sdp) == IDENTITY_FINGERPRINT_PAYLOAD


def test_identity_insertion_golden_bytes():
    sdp = "v=0\ns=-\r\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\nm=audio 9 RTP 0"
    assert insert_identity(sdp, "QUJD").encode() == IDENTITY_INSERTED_SDP


def test_identity_json_escape_golden_bytes():
    value = {"a": '"\\/\b\t\n\f\r\x01\x1a\x1f\x7fé'}
    assert canonical_json(value).encode() == IDENTITY_JSON_ESCAPES


def test_identity_operator_token_golden_bytes():
    key = ec.derive_private_key(1, ec.SECP384R1())
    header, payload, _ = IdentitySigner(key, clock=lambda: 1000.9).token().split(".")
    assert base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)) == (
        IDENTITY_OPERATOR_TOKEN_HEADER
    )
    assert base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)) == (
        IDENTITY_OPERATOR_TOKEN_PAYLOAD
    )
