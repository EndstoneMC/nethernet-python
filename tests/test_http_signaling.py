import asyncio
import socket
import ssl

import aiohttp
import pytest

from nethernet.errors import ConnectionFailed, SessionError
from nethernet.network_id import NetworkID
from nethernet.signaling import http
from nethernet.signaling.http import (
    HttpSignalingServer,
    parse_network_id,
    parse_target,
    probe,
    probe_batch,
    probe_urls,
    request_answer,
    resolve_base_url,
)
from nethernet.signaling.messages import ConnectResponse

PING = {
    "name": "Dedicated Server",
    "protocol": 2225,
    "version": "1.26.60",
    "level": "Bedrock level",
    "players": 0,
    "maxPlayers": 10,
    "gameType": 0,
}
PING_BODY = (
    b'{"name":"Dedicated Server","protocol":2225,"version":"1.26.60","level":"Bedrock level",'
    b'"players":0,"maxPlayers":10,"gameType":0}'
)
OFFER = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\n"
EXPECTED_ID = "Expected /v1/join/{networkId}"


class Host:
    def __init__(self) -> None:
        self.signals: list[tuple[NetworkID, str]] = []
        self.server: HttpSignalingServer | None = None
        self.reply: str | None = None
        self.raise_: BaseException | None = None

    async def on_signal(self, sender: NetworkID, message: str) -> None:
        self.signals.append((sender, message))
        if self.raise_ is not None:
            raise self.raise_
        if self.reply is not None:
            session_id = message.split(" ")[1]
            self.server.send(sender, self.reply.format(id=session_id))


async def start_host(
    app: Host | None = None, *, host: str = "127.0.0.1", **kwargs
) -> tuple[Host, HttpSignalingServer]:
    app = app or Host()
    server = HttpSignalingServer(app.on_signal, host=host, port=0, **kwargs)
    app.server = server
    await server.start()
    return app, server


async def exchange(port: int, data: bytes, address: str = "127.0.0.1") -> bytes:
    reader, writer = await asyncio.open_connection(address, port)
    writer.write(data)
    await writer.drain()
    try:
        answer = await asyncio.wait_for(reader.read(), 5)
    except ConnectionResetError:
        answer = b""
    writer.close()
    return answer


def response(status: str, content_type: str, body: bytes) -> bytes:
    return (
        f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    ).encode() + body


def error(code: int, body: str) -> bytes:
    return response(f"{code} {body}", "text/plain", body.encode())


def post(path: str, body: bytes, extra: str = "") -> bytes:
    return (
        f"POST {path} HTTP/1.1\r\nHost: h\r\nContent-Length: {len(body)}\r\n{extra}\r\n"
    ).encode() + body


class Recorder:
    def __init__(
        self, reply: bytes | None, *, delay: float = 0.0, hang_on_tls: bool = False
    ) -> None:
        self.reply = reply
        self.delay = delay
        self.hang_on_tls = hang_on_tls
        self.requests: list[bytes] = []
        self.server: asyncio.Server | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = await reader.read(65536)
        if data[:1] == b"\x16":
            self.requests.append(b"TLS")
            if self.hang_on_tls:
                await asyncio.sleep(3600)
            writer.close()
            return
        while b"\r\n\r\n" not in data:
            data += await reader.read(65536)
        head, _, body = data.partition(b"\r\n\r\n")
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                while len(body) < int(line.split(b":")[1]):
                    body += await reader.read(65536)
        self.requests.append(head + b"\r\n\r\n" + body)
        if self.reply is None:
            await asyncio.sleep(3600)
        await asyncio.sleep(self.delay)
        writer.write(self.reply)
        await writer.drain()
        writer.close()

    async def start(self) -> int:
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def aclose(self) -> None:
        self.server.close()


def test_probe_urls_for_an_explicit_port_are_https_then_http():
    assert probe_urls("example.com", 8080, {}) == [
        ["https://example.com:8080"],
        ["http://example.com:8080"],
    ]


@pytest.mark.parametrize("port", [0, 19132])
def test_probe_urls_for_the_default_port_try_19132_and_no_port(port):
    assert probe_urls("example.com", port, {}) == [
        ["https://example.com:19132", "https://example.com"],
        ["http://example.com:19132", "http://example.com"],
    ]


def test_probe_urls_bracket_an_ipv6_host():
    assert probe_urls("::1", 25565, {}) == [["https://[::1]:25565"], ["http://[::1]:25565"]]


def test_probe_urls_use_a_cached_url_alone():
    cache = {("example.com", 0): "http://example.com:19132"}
    assert probe_urls("example.com", 0, cache) == [["http://example.com:19132"]]


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("example.com", ("example.com", 0)),
        ("example.com:19133", ("example.com", 19133)),
        ("[::1]:19132", ("::1", 19132)),
        ("[::1]", ("::1", 0)),
        ("::1", ("::1", 0)),
    ],
)
def test_parse_target_reads_host_and_port(target, expected):
    assert parse_target(target) == expected


@pytest.mark.parametrize("target", ["", "h:x", "h:70000", "[::1", "[::1]x", ":19132"])
def test_parse_target_rejects_a_malformed_target(target):
    with pytest.raises(ValueError):
        parse_target(target)


async def test_direct_join_probe_tries_https_then_a_plain_get_and_ignores_the_body():
    recorder = Recorder(response("202 Accepted", "text/plain", b"not json"))
    port = await recorder.start()
    try:
        base = await probe("127.0.0.1", port, cache={})
    finally:
        await recorder.aclose()
    assert base == f"http://127.0.0.1:{port}"
    assert recorder.requests == [
        b"TLS",
        f"GET /v1/join HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode(),
    ]


async def test_a_hanging_https_request_times_out_after_3_seconds_and_http_answers():
    recorder = Recorder(response("200 OK", "text/plain", b""), hang_on_tls=True)
    port = await recorder.start()
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        base = await probe("127.0.0.1", port, cache={})
    finally:
        await recorder.aclose()
    assert base == f"http://127.0.0.1:{port}"
    assert 2.5 < loop.time() - started < 5.5
    assert recorder.requests[0] == b"TLS"


async def test_every_url_of_a_batch_starts_at_once_and_the_first_success_wins():
    hanging = Recorder(None)
    slow = Recorder(response("200 OK", "text/plain", b""), delay=1.0)
    fast = Recorder(response("200 OK", "text/plain", b""))
    urls = [f"http://127.0.0.1:{await recorder.start()}" for recorder in (hanging, slow, fast)]
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        async with aiohttp.ClientSession() as session:
            winner = await probe_batch(session, urls)
    finally:
        for recorder in (hanging, slow, fast):
            await recorder.aclose()
    assert winner == urls[2]
    assert loop.time() - started < 0.9
    assert all(len(recorder.requests) == 1 for recorder in (hanging, slow, fast))


async def test_server_list_probe_sends_the_game_version():
    recorder = Recorder(response("200 OK", "application/json", PING_BODY))
    port = await recorder.start()
    try:
        await probe("127.0.0.1", port, version="1.26.60", cache={})
    finally:
        await recorder.aclose()
    assert recorder.requests == [
        b"TLS",
        f"GET /v1/join?version=1.26.60 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode(),
    ]


async def test_direct_join_probe_passes_a_host_with_an_empty_ping_body():
    _, server = await start_host()
    cache: dict = {}
    try:
        base = await probe("127.0.0.1", server.bound_port, cache=cache)
    finally:
        await server.aclose()
    assert base == f"http://127.0.0.1:{server.bound_port}"
    assert cache == {("127.0.0.1", server.bound_port): base}


@pytest.mark.parametrize(
    "reply",
    [
        response("200 OK", "application/json", b""),
        response("200 OK", "application/json", b"not json"),
        response("200 OK", "application/json", b"[1]"),
    ],
)
async def test_server_list_probe_needs_a_ping_json_body(reply):
    recorder = Recorder(reply)
    port = await recorder.start()
    try:
        with pytest.raises(ConnectionFailed) as info:
            await probe("127.0.0.1", port, version="1.26.60", cache={})
    finally:
        await recorder.aclose()
    assert info.value.error == SessionError.NO_SIGNALING_CHANNEL


@pytest.mark.parametrize("version", [None, "1.26.60"])
async def test_probe_fails_on_a_non_2xx_answer(version):
    recorder = Recorder(response("404 Not Found", "application/json", PING_BODY))
    port = await recorder.start()
    try:
        with pytest.raises(ConnectionFailed) as info:
            await probe("127.0.0.1", port, version=version, cache={})
    finally:
        await recorder.aclose()
    assert info.value.error == SessionError.NO_SIGNALING_CHANNEL


async def test_probe_fails_with_no_signaling_channel_on_timeout():
    recorder = Recorder(None)
    port = await recorder.start()
    try:
        with pytest.raises(ConnectionFailed) as info:
            await probe("127.0.0.1", port, cache={}, timeout=0.5)
    finally:
        await recorder.aclose()
    assert info.value.error == SessionError.NO_SIGNALING_CHANNEL


async def test_explicit_url_skips_the_probe():
    assert await resolve_base_url("http://h:1/", cache={}) == "http://h:1"
    assert await resolve_base_url("https://h", cache={}) == "https://h"


async def test_https_fails_the_handshake():
    _, server = await start_host()
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with pytest.raises(ssl.SSLError):
            await asyncio.open_connection("127.0.0.1", server.bound_port, ssl=context)
    finally:
        await server.aclose()


@pytest.mark.parametrize("path", ["/v1/join", "/v1/join?version=1.26.60", "/v1/join?x"])
async def test_get_v1_join_ignores_a_query_string(path):
    _, server = await start_host(ping=lambda: PING)
    try:
        answer = await exchange(server.bound_port, f"GET {path} HTTP/1.1\r\n\r\n".encode())
    finally:
        await server.aclose()
    assert answer == response("200 OK", "application/json", PING_BODY)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5", 5),
        ("0x10", 16),
        ("0X1f", 31),
        ("010", 8),
        ("+5", 5),
        ("\t5", 5),
        ("\v\f\r\n 5", 5),
        ("+0x10", 16),
        ("0x0", 0),
        ("00", 0),
        ("0", 0),
        ("18446744073709551615", 2**64 - 1),
        ("", None),
        ("0x", None),
        ("08", None),
        ("-5", None),
        ("+", None),
        ("+-5", None),
        ("18446744073709551616", None),
        ("5x", None),
        ("5\t", None),
        ("%35", None),
        ("%20%205", None),
        ("٥", None),
        ("9" * 5000, None),
        ("0x" + "f" * 5000, None),
        ("0" * 5000 + "5", 5),
        ("0x" + "0" * 5000 + "ff", 255),
        ("01" + "7" * 21, 2**64 - 1),
        ("02" + "0" * 21, None),
        ("0xffffffffffffffff", 2**64 - 1),
        ("0x10000000000000000", None),
    ],
)
def test_network_id_is_read_as_strtoull_base_0(text, expected):
    assert parse_network_id(text) == expected


@pytest.mark.parametrize(
    ("request_bytes", "expected"),
    [
        (post("/v1/join/", b"x"), error(400, EXPECTED_ID)),
        (post("/v1/join/" + "9" * 5000, b"x"), error(400, EXPECTED_ID)),
        (post("/v1/join/12a", b"x"), error(400, EXPECTED_ID)),
        (post("/v1/join/-1", b"x"), error(400, EXPECTED_ID)),
        (post("/v1/join/0x", b"x"), error(400, EXPECTED_ID)),
        (post("/v1/join/08", b"x"), error(400, EXPECTED_ID)),
        (post("/v1/join/%35", b"x"), error(400, EXPECTED_ID)),
        (post("/v1/join/%20%205", b"x"), error(400, EXPECTED_ID)),
        (post("/v1/join/ 5", b"x"), error(400, EXPECTED_ID)),
        (post("/v1/join/18446744073709551616", b"x"), error(400, EXPECTED_ID)),
        (post("/v1/join/0", b"x"), error(400, "Invalid networkId")),
        (post("/v1/join/00", b"x"), error(400, "Invalid networkId")),
        (post("/v1/join/0x0?x=1", b"x"), error(400, "Invalid networkId")),
        (post("/v1/join/x", b""), error(400, EXPECTED_ID)),
        (post("/v1/join/5", b""), error(400, "Missing SDP offer in request body")),
        (b"POST /v1/join/5 HTTP/1.1\r\n\r\n", error(400, "Missing SDP offer in request body")),
        (
            b"POST /v1/join/5 HTTP/1.1\r\nContent-Length : 1\r\n\r\nx",
            error(400, "Missing SDP offer in request body"),
        ),
        (
            b"POST /v1/join/5 HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx\r\n0\r\n\r\n",
            error(400, "Missing SDP offer in request body"),
        ),
        (post("/v1/join", b"x"), error(404, "Not Found")),
        (post("/", b"x"), error(404, "Not Found")),
        (b"GET / HTTP/1.1\r\n\r\n", error(404, "Not Found")),
        (b"GET /v1/join/5 HTTP/1.1\r\n\r\n", error(404, "Not Found")),
        (b"PUT /v1/join/5 HTTP/1.1\r\nContent-Length: 1\r\n\r\nx", error(404, "Not Found")),
        (b"PATCH /v1/join/5 HTTP/1.1\r\nContent-Length: 1\r\n\r\nx", error(404, "Not Found")),
        (b"HEAD /v1/join HTTP/1.1\r\n\r\n", error(404, "Not Found")),
        (b"DELETE /v1/join HTTP/1.1\r\n\r\n", error(404, "Not Found")),
    ],
)
async def test_error_answers_carry_their_body_as_the_reason(request_bytes, expected):
    host, server = await start_host()
    try:
        answer = await exchange(server.bound_port, request_bytes)
    finally:
        await server.aclose()
    assert answer == expected
    assert host.signals == []


async def test_no_answer_within_the_join_timeout_is_a_504():
    _, server = await start_host(join_timeout=0.2)
    try:
        answer = await exchange(server.bound_port, post("/v1/join/5", OFFER.encode()))
    finally:
        await server.aclose()
    assert answer == error(504, "Gateway Timeout")


@pytest.mark.parametrize("raised", [RuntimeError("boom"), ValueError()])
async def test_an_exception_from_the_host_drops_the_offer_until_the_504(raised):
    host = Host()
    host.raise_ = raised
    _, server = await start_host(host, join_timeout=0.2)
    try:
        answer = await exchange(server.bound_port, post("/v1/join/5", OFFER.encode()))
    finally:
        await server.aclose()
    assert answer == error(504, "Gateway Timeout")
    assert host.signals == [(NetworkID.p2p(5), f"CONNECTREQUEST 1 {OFFER}")]


async def test_a_failing_request_handler_is_an_empty_500_without_a_content_type():
    def failing_ping() -> None:
        raise RuntimeError("ping failed")

    _, server = await start_host(ping=failing_ping)
    try:
        answer = await exchange(server.bound_port, b"GET /v1/join HTTP/1.1\r\n\r\n")
    finally:
        await server.aclose()
    assert answer == (
        b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    )


async def test_accepted_post_becomes_a_connectrequest_answered_by_the_first_signal():
    host = Host()
    host.reply = "CONNECTRESPONSE {id} v=0\r\na=answer x y\r\n"
    _, server = await start_host(host)
    try:
        first = await exchange(server.bound_port, post("/v1/join/123", OFFER.encode()))
        await exchange(server.bound_port, post("/v1/join/0", OFFER.encode()))
        second = await exchange(server.bound_port, post("/v1/join/0x7b?v=1", OFFER.encode()))
    finally:
        await server.aclose()
    assert host.signals == [
        (NetworkID.p2p(123), f"CONNECTREQUEST 1 {OFFER}"),
        (NetworkID.p2p(123), f"CONNECTREQUEST 2 {OFFER}"),
    ]
    assert first == second == response("200 OK", "application/sdp", b"v=0\r\na=answer x y\r\n")


async def test_a_connecterror_answers_200_application_sdp_with_the_bare_code():
    host = Host()
    host.reply = "CONNECTERROR {id} 37"
    _, server = await start_host(host)
    try:
        answer = await exchange(server.bound_port, post("/v1/join/123", b"junk"))
    finally:
        await server.aclose()
    assert answer == response("200 OK", "application/sdp", b"37")


async def test_a_signal_for_no_pending_request_is_not_delivered():
    _, server = await start_host()
    try:
        assert not server.send(NetworkID.p2p(1), "CONNECTRESPONSE 1 v=0")
        assert not server.send(NetworkID.p2p(1), "CONNECTRESPONSE")
        assert not server.send(NetworkID.p2p(1), "CONNECTRESPONSE " + "9" * 5000 + " v=0")
    finally:
        await server.aclose()


async def test_aiohttp_client_posts_an_offer_and_reads_the_answer():
    host = Host()
    host.reply = "CONNECTRESPONSE {id} v=0\r\n"
    _, server = await start_host(host)
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"http://127.0.0.1:{server.bound_port}/v1/join/42",
                data=OFFER.encode(),
                headers={"Content-Type": "application/sdp"},
            ) as answer,
        ):
            assert answer.status == 200
            assert answer.headers["Content-Type"] == "application/sdp"
            assert answer.headers["Connection"] == "close"
            assert await answer.text() == "v=0\r\n"
    finally:
        await server.aclose()
    assert host.signals == [(NetworkID.p2p(42), f"CONNECTREQUEST 1 {OFFER}")]


async def test_a_pipelined_second_request_is_dropped_with_the_connection():
    host = Host()
    host.reply = "CONNECTRESPONSE {id} ok"
    _, server = await start_host(host)
    try:
        answer = await exchange(
            server.bound_port, post("/v1/join/9", b"v=0") + b"GET /v1/join HTTP/1.1\r\n\r\n"
        )
    finally:
        await server.aclose()
    assert answer == response("200 OK", "application/sdp", b"ok")


async def test_a_half_closed_client_gets_no_answer():
    host, server = await start_host(join_timeout=5)
    reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
    try:
        writer.write(post("/v1/join/9", b"v=0"))
        await writer.drain()
        writer.write_eof()
        assert await asyncio.wait_for(reader.read(), 5) == b""
    finally:
        writer.close()
        await server.aclose()
    assert host.signals == [(NetworkID.p2p(9), "CONNECTREQUEST 1 v=0")]


async def test_connection_65_is_closed_without_a_response():
    _, server = await start_host()
    held = []
    try:
        for _ in range(64):
            held.append(await asyncio.open_connection("127.0.0.1", server.bound_port))
        await asyncio.sleep(0.1)
        assert await exchange(server.bound_port, b"GET /v1/join HTTP/1.1\r\n\r\n") == b""
        held.pop()[1].close()
        await asyncio.sleep(0.1)
        answer = await exchange(server.bound_port, b"GET /v1/join HTTP/1.1\r\n\r\n")
        assert answer == response("200 OK", "application/json", b"")
    finally:
        for _, writer in held:
            writer.close()
        await server.aclose()


def sized_post(total: int) -> bytes:
    for length in range(total, 0, -1):
        candidate = post("/v1/join/x", b"b" * length)
        if len(candidate) == total:
            return candidate
    raise AssertionError(total)


@pytest.mark.parametrize(("total", "answered"), [(65536, True), (65537, False)])
async def test_a_request_over_65536_bytes_is_closed_without_a_response(total, answered):
    _, server = await start_host()
    try:
        answer = await exchange(server.bound_port, sized_post(total))
    finally:
        await server.aclose()
    assert answer == (error(400, EXPECTED_ID) if answered else b"")


async def test_headers_over_65536_bytes_are_closed_without_a_response():
    _, server = await start_host()
    try:
        answer = await exchange(server.bound_port, b"GET /v1/join HTTP/1.1\r\nX: " + b"a" * 70000)
    finally:
        await server.aclose()
    assert answer == b""


@pytest.mark.parametrize(
    "request_bytes",
    [
        b"get /v1/join HTTP/1.1\r\n\r\n",
        b"ABCDEFGHIJKLMNOPQ /v1/join HTTP/1.1\r\n\r\n",
        b" /v1/join HTTP/1.1\r\n\r\n",
        b"GET v1/join HTTP/1.1\r\n\r\n",
        b"GET /v1/join\r\n\r\n",
        b"GET  /v1/join HTTP/1.1\r\n\r\n",
        b"G3T /v1/join HTTP/1.1\r\n\r\n",
        b"POST /v1/join/5 HTTP/1.1\r\nContent-Length: abc\r\n\r\nx",
        b"POST /v1/join/5 HTTP/1.1\r\nContent-Length: 1 x\r\n\r\nx",
        b"POST /v1/join/5 HTTP/1.1\r\nContent-Length: -1\r\n\r\nx",
        b"POST /v1/join/5 HTTP/1.1\r\nContent-Length: +1\r\n\r\nx",
        b"POST /v1/join/5 HTTP/1.1\r\nContent-Length: 18446744073709551616\r\n\r\nx",
        b"POST /v1/join/5 HTTP/1.1\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\nxx",
        b"POST /v1/join/5 HTTP/1.1\r\nContent-Length: " + b"9" * 5000 + b"\r\n\r\nx",
    ],
)
async def test_a_malformed_request_is_closed_without_a_response(request_bytes):
    host, server = await start_host()
    try:
        answer = await exchange(server.bound_port, request_bytes)
    finally:
        await server.aclose()
    assert answer == b""
    assert host.signals == []


@pytest.mark.parametrize(
    "request_line",
    [b"ABCDEFGHIJKLMNOP / HTTP/1.1", b"GET / anything goes here", b"GET / "],
)
async def test_the_version_token_is_not_checked(request_line):
    _, server = await start_host()
    try:
        answer = await exchange(server.bound_port, request_line + b"\r\n\r\n")
    finally:
        await server.aclose()
    assert answer == error(404, "Not Found")


@pytest.mark.parametrize(
    "header",
    [
        "content-LENGTH:\t 4 \t",
        "Content-Length: 4\r\nContent-Length: 4",
        "Content-Length: 0004",
        "Content-Length: " + "0" * 5000 + "4",
    ],
)
async def test_content_length_is_found_case_insensitively_and_trimmed(header):
    host = Host()
    host.reply = "CONNECTRESPONSE {id} ok"
    _, server = await start_host(host)
    request_bytes = f"POST /v1/join/9 HTTP/1.1\r\n{header}\r\n\r\nv=0\n".encode()
    try:
        answer = await exchange(server.bound_port, request_bytes)
    finally:
        await server.aclose()
    assert answer == response("200 OK", "application/sdp", b"ok")
    assert host.signals == [(NetworkID.p2p(9), "CONNECTREQUEST 1 v=0\n")]


async def test_server_waits_for_the_whole_body():
    host = Host()
    host.reply = "CONNECTRESPONSE {id} ok"
    _, server = await start_host(host)
    reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
    try:
        writer.write(b"POST /v1/join/9 HTTP/1.1\r\nContent-Length: 7\r\n\r\nv=")
        await writer.drain()
        await asyncio.sleep(0.1)
        assert host.signals == []
        writer.write(b"0\r\nxy")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 5) == response(
            "200 OK", "application/sdp", b"ok"
        )
    finally:
        writer.close()
        await server.aclose()
    assert host.signals == [(NetworkID.p2p(9), "CONNECTREQUEST 1 v=0\r\nxy")]


@pytest.mark.parametrize("partial", [b"GET /v1/join HTTP/1.1\r\n", b"GET /v1/join HTTP/1.1\n\n"])
async def test_an_incomplete_request_is_closed_after_the_receive_timeout(partial):
    _, server = await start_host(receive_timeout=0.2)
    try:
        answer = await exchange(server.bound_port, partial)
    finally:
        await server.aclose()
    assert answer == b""


async def test_an_empty_bind_address_listens_dual_stack():
    _, server = await start_host(host="")
    try:
        answer = await exchange(server.bound_port, b"GET /v1/join HTTP/1.1\r\n\r\n")
        assert answer == response("200 OK", "application/json", b"")
        if socket.has_ipv6:
            answer = await exchange(server.bound_port, b"GET /v1/join HTTP/1.1\r\n\r\n", "::1")
            assert answer == response("200 OK", "application/json", b"")
    finally:
        await server.aclose()


async def test_a_given_bind_address_listens_on_that_address_alone():
    _, server = await start_host(host="127.0.0.1")
    try:
        assert all(sock.family == socket.AF_INET for sock in server._server.sockets)
    finally:
        await server.aclose()


async def test_an_empty_bind_address_falls_back_to_ipv4_without_ipv6(monkeypatch):
    class NoIpv6:
        def __getattr__(self, name):
            return getattr(socket, name)

        @staticmethod
        def socket(family=socket.AF_INET, *args, **kwargs):
            if family == socket.AF_INET6:
                raise OSError("no IPv6")
            return socket.socket(family, *args, **kwargs)

    monkeypatch.setattr(http, "socket", NoIpv6())
    _, server = await start_host(host="")
    try:
        assert [sock.family for sock in server._server.sockets] == [socket.AF_INET]
        answer = await exchange(server.bound_port, b"GET /v1/join HTTP/1.1\r\n\r\n")
        assert answer == response("200 OK", "application/json", b"")
    finally:
        await server.aclose()


@pytest.mark.parametrize("address", ["", "127.0.0.1"])
async def test_the_listen_backlog_is_64(monkeypatch, address):
    backlogs = []
    real_start_server = asyncio.start_server

    async def recording_start_server(*args, **kwargs):
        backlogs.append(kwargs.get("backlog"))
        return await real_start_server(*args, **kwargs)

    monkeypatch.setattr(http.asyncio, "start_server", recording_start_server)
    _, server = await start_host(host=address)
    await server.aclose()
    assert backlogs == [64]


@pytest.mark.parametrize("address", ["not an address", "localhost", "999.1.1.1"])
async def test_an_unparseable_bind_address_raises_on_start(address):
    with pytest.raises(socket.gaierror):
        await start_host(host=address)


async def test_an_unbindable_bind_address_raises_on_start():
    with pytest.raises(OSError):
        await start_host(host="192.0.2.1")


async def test_client_posts_the_signal_data_to_its_own_id_as_application_sdp():
    recorder = Recorder(response("200 OK", "application/sdp", b"v=0\r\n"))
    port = await recorder.start()
    try:
        await request_answer(
            f"http://127.0.0.1:{port}", NetworkID.p2p(123), f"CONNECTREQUEST 7 {OFFER}"
        )
    finally:
        await recorder.aclose()
    (request,) = recorder.requests
    head, _, body = request.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    assert lines[0] == b"POST /v1/join/123 HTTP/1.1"
    assert b"Content-Type: application/sdp" in lines[1:]
    assert body == OFFER.encode()


async def test_client_delivers_the_answer_as_connectresponse_with_the_offer_session_id():
    recorder = Recorder(response("200 OK", "application/sdp", b"v=0\r\na=x\r\n"))
    port = await recorder.start()
    try:
        answer = await request_answer(f"http://127.0.0.1:{port}", 123, f"CONNECTREQUEST 7 {OFFER}")
    finally:
        await recorder.aclose()
    assert answer == ConnectResponse(7, "v=0\r\na=x\r\n")


@pytest.mark.parametrize(
    ("signal", "session_id", "body"),
    [
        ("CANDIDATEADD 7 candidate:1 1 udp", 7, b"candidate:1 1 udp"),
        ("ANYTHING 7abc data", 7, b"data"),
        ("x 18446744073709551615 a b", 2**64 - 1, b"a b"),
    ],
)
async def test_client_posts_any_signal_type_and_reads_the_id_up_to_a_non_digit(
    signal, session_id, body
):
    recorder = Recorder(response("200 OK", "application/sdp", b"v=0\r\n"))
    port = await recorder.start()
    try:
        answer = await request_answer(f"http://127.0.0.1:{port}", 123, signal)
    finally:
        await recorder.aclose()
    assert answer == ConnectResponse(session_id, "v=0\r\n")
    assert recorder.requests[0].partition(b"\r\n\r\n")[2] == body


@pytest.mark.parametrize(
    "signal",
    [
        "CONNECTREQUEST 7",
        "CONNECTREQUEST",
        "CONNECTREQUEST  7 x",
        "CONNECTREQUEST +7 x",
        "CONNECTREQUEST -7 x",
        "CONNECTREQUEST abc x",
        "CONNECTREQUEST 18446744073709551616 x",
        "CONNECTREQUEST " + "9" * 5000 + " x",
    ],
)
async def test_client_refuses_a_signal_without_a_readable_session_id(signal):
    with pytest.raises(ConnectionFailed) as info:
        await request_answer("http://127.0.0.1:1", 123, signal, timeout=1)
    assert info.value.error == SessionError.SIGNALING_FAILED_TO_SEND


async def test_client_delivers_nothing_for_an_empty_2xx_body():
    recorder = Recorder(response("204 No Content", "application/sdp", b""))
    port = await recorder.start()
    try:
        assert await request_answer(f"http://127.0.0.1:{port}", 123, "CONNECTREQUEST 7 x") is None
    finally:
        await recorder.aclose()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"37", SessionError.IDENTITY_NOT_ALLOWED),
        (b"36", SessionError.FAILED_TO_CREATE_IDENTITY_ASSERTION),
        (b"9999", 9999),
        (b"-1", -1),
        (b"37abc", SessionError.IDENTITY_NOT_ALLOWED),
        (b"2147483647", 2**31 - 1),
        (b"-2147483648", -(2**31)),
        (b"2147483648", SessionError.SIGNALING_PARSING_FAILURE),
        (b"9" * 5000, SessionError.SIGNALING_PARSING_FAILURE),
        (b"+5", SessionError.SIGNALING_PARSING_FAILURE),
        (b" 37", SessionError.SIGNALING_PARSING_FAILURE),
        (b"not an sdp", SessionError.SIGNALING_PARSING_FAILURE),
    ],
)
async def test_a_2xx_body_that_is_not_sdp_raises_the_session_error_it_holds(body, expected):
    recorder = Recorder(response("200 OK", "application/sdp", body))
    port = await recorder.start()
    try:
        with pytest.raises(ConnectionFailed) as info:
            await request_answer(f"http://127.0.0.1:{port}", 123, f"CONNECTREQUEST 7 {OFFER}")
    finally:
        await recorder.aclose()
    assert info.value.error == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("401 Unauthorized", SessionError.NOT_AUTHENTICATED),
        ("403 Forbidden", SessionError.NOT_ALLOWED),
        ("503 Service Unavailable", SessionError.SERVER_FULL),
        ("400 Bad Request", SessionError.SIGNALING_FAILED_TO_SEND),
        ("404 Not Found", SessionError.SIGNALING_FAILED_TO_SEND),
        ("500 Internal Server Error", SessionError.SIGNALING_FAILED_TO_SEND),
        ("504 Gateway Timeout", SessionError.SIGNALING_FAILED_TO_SEND),
    ],
)
async def test_a_non_2xx_answer_fails_the_dial(status, expected):
    recorder = Recorder(response(status, "text/plain", b"v=0\r\n"))
    port = await recorder.start()
    try:
        with pytest.raises(ConnectionFailed) as info:
            await request_answer(f"http://127.0.0.1:{port}", 123, f"CONNECTREQUEST 7 {OFFER}")
    finally:
        await recorder.aclose()
    assert info.value.error == expected


async def test_a_failed_request_fails_the_dial_with_signaling_failed_to_send():
    recorder = Recorder(None)
    port = await recorder.start()
    await recorder.aclose()
    await recorder.server.wait_closed()
    with pytest.raises(ConnectionFailed) as info:
        await request_answer(
            f"http://127.0.0.1:{port}", 123, f"CONNECTREQUEST 7 {OFFER}", timeout=2
        )
    assert info.value.error == SessionError.SIGNALING_FAILED_TO_SEND
