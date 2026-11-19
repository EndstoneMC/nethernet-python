from __future__ import annotations

import asyncio
import inspect
import json
import re
import socket
from collections.abc import Awaitable, Callable

import aiohttp

from nethernet.constants import DEFAULT_NEGOTIATION_TIMEOUT
from nethernet.errors import ConnectionFailed, SessionError, decode_error
from nethernet.network_id import U64_MAX, NetworkID
from nethernet.signaling.messages import ConnectResponse

SDP_CONTENT_TYPE = "application/sdp"
JSON_CONTENT_TYPE = "application/json"
TEXT_CONTENT_TYPE = "text/plain"

DEFAULT_SERVER_PORT = 19132
PROBE_TIMEOUT = 6.0
PROBE_REQUEST_TIMEOUT = 3.0
JOIN_TIMEOUT = 10.0
RECEIVE_TIMEOUT = 30.0
MAX_CONNECTIONS = 64
LISTEN_BACKLOG = 64
MAX_REQUEST_SIZE = 65536

JOIN_PATH = "/v1/join"
JOIN_PREFIX = "/v1/join/"

TLS_HANDSHAKE_FAILURE_ALERT = b"\x15\x03\x01\x00\x02\x02\x28"

_METHOD = re.compile(rb"[A-Z]{1,16}")
_DECIMAL = re.compile(r"[0-9]+")
_LEADING_SIGNED = re.compile(r"-?[0-9]+")
_C_SPACE = " \t\n\v\f\r"
_DIGITS = {8: "01234567", 10: "0123456789", 16: "0123456789abcdefABCDEF"}
_U64_SIGNIFICANT_DIGITS = {8: 22, 10: 20, 16: 16}
_I32_MIN = -(2**31)
_I32_MAX = 2**31 - 1

_STATUS_ERRORS = {
    401: SessionError.NOT_AUTHENTICATED,
    403: SessionError.NOT_ALLOWED,
    503: SessionError.SERVER_FULL,
}
_PROBE_SKIPPED_HEADERS = ("Accept", "Accept-Encoding", "User-Agent")

OnSignal = Callable[[NetworkID, str], Awaitable[None] | None]
PingBody = Callable[[], dict[str, object] | None]
EndpointCache = dict[tuple[str, int], str]

_endpoint_cache: EndpointCache = {}


def _bounded_u64(digits: str, base: int, limit: int) -> int | None:
    significant = digits.lstrip("0") or "0"
    if len(significant) > limit:
        return None
    value = int(significant, base)
    return value if value <= U64_MAX else None


def _format_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def parse_target(target: str) -> tuple[str, int]:
    host, port_text = target, ""
    if target.startswith("["):
        end = target.find("]")
        if end == -1:
            raise ValueError(f"malformed target {target!r}")
        host, rest = target[1:end], target[end + 1 :]
        if rest:
            if not rest.startswith(":"):
                raise ValueError(f"malformed target {target!r}")
            port_text = rest[1:]
    elif target.count(":") == 1:
        host, port_text = target.split(":")
    if not host:
        raise ValueError(f"malformed target {target!r}")
    if not port_text:
        return host, 0
    if not _DECIMAL.fullmatch(port_text) or len(port_text) > 5 or int(port_text) > 0xFFFF:
        raise ValueError(f"malformed target {target!r}")
    return host, int(port_text)


def probe_urls(host: str, port: int, cache: EndpointCache | None = None) -> list[list[str]]:
    cache = _endpoint_cache if cache is None else cache
    cached = cache.get((host, port))
    if cached is not None:
        return [[cached]]
    name = _format_host(host)
    if port not in (0, DEFAULT_SERVER_PORT):
        return [[f"https://{name}:{port}"], [f"http://{name}:{port}"]]
    return [
        [f"https://{name}:{DEFAULT_SERVER_PORT}", f"https://{name}"],
        [f"http://{name}:{DEFAULT_SERVER_PORT}", f"http://{name}"],
    ]


async def _probe_one(http: aiohttp.ClientSession, url: str, version: str | None) -> str | None:
    params = None if version is None else {"version": version}
    timeout = aiohttp.ClientTimeout(total=PROBE_REQUEST_TIMEOUT)
    try:
        async with http.get(
            f"{url}{JOIN_PATH}",
            params=params,
            timeout=timeout,
            skip_auto_headers=_PROBE_SKIPPED_HEADERS,
        ) as response:
            body = await response.read()
            status = response.status
    except (aiohttp.ClientError, OSError, TimeoutError):
        return None
    if status // 100 != 2:
        return None
    if version is None:
        return url
    if not body:
        return None
    try:
        ping = json.loads(body)
    except (ValueError, RecursionError):
        return None
    return url if isinstance(ping, dict) else None


async def probe_batch(
    http: aiohttp.ClientSession, urls: list[str], version: str | None = None
) -> str | None:
    tasks = [asyncio.create_task(_probe_one(http, url, version)) for url in urls]
    try:
        for next_done in asyncio.as_completed(tasks):
            url = await next_done
            if url is not None:
                return url
        return None
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def probe(
    host: str,
    port: int,
    *,
    version: str | None = None,
    cache: EndpointCache | None = None,
    timeout: float = PROBE_TIMEOUT,
) -> str:
    cache = _endpoint_cache if cache is None else cache
    batches = probe_urls(host, port, cache)
    try:
        async with asyncio.timeout(timeout), aiohttp.ClientSession() as http:
            for urls in batches:
                url = await probe_batch(http, urls, version)
                if url is not None:
                    cache[(host, port)] = url
                    return url
    except TimeoutError:
        pass
    raise ConnectionFailed(SessionError.NO_SIGNALING_CHANNEL)


async def resolve_base_url(
    target: str,
    *,
    cache: EndpointCache | None = None,
    timeout: float = PROBE_TIMEOUT,
) -> str:
    if target.startswith(("http://", "https://")):
        return target.rstrip("/")
    host, port = parse_target(target)
    return await probe(host, port, cache=cache, timeout=timeout)


def signal_session_id(token: str) -> int | None:
    match = _DECIMAL.match(token)
    if match is None:
        return None
    return _bounded_u64(match.group(), 10, _U64_SIGNIFICANT_DIGITS[10])


def _split_signal(signal: str) -> tuple[int, str] | None:
    first = signal.find(" ")
    second = signal.find(" ", first + 1) if first != -1 else -1
    if second == -1:
        return None
    session_id = signal_session_id(signal[first + 1 : second])
    if session_id is None:
        return None
    return session_id, signal[second + 1 :]


def _signed_code(text: str) -> int | None:
    match = _LEADING_SIGNED.match(text)
    if match is None:
        return None
    text = match.group()
    significant = text.lstrip("-").lstrip("0") or "0"
    if len(significant) > 10:
        return None
    value = -int(significant) if text.startswith("-") else int(significant)
    return value if _I32_MIN <= value <= _I32_MAX else None


def _answer_from_body(body: str) -> str:
    if body.startswith("v="):
        return body
    code = _signed_code(body)
    if code is None:
        raise ConnectionFailed(SessionError.SIGNALING_PARSING_FAILURE)
    raise ConnectionFailed(decode_error(code))


async def request_answer(
    base_url: str,
    own_id: NetworkID | int,
    signal: str,
    *,
    timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
) -> ConnectResponse | None:
    split = _split_signal(signal)
    if split is None:
        raise ConnectionFailed(SessionError.SIGNALING_FAILED_TO_SEND)
    session_id, data = split
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    try:
        async with (
            aiohttp.ClientSession(timeout=client_timeout) as http,
            http.post(
                f"{base_url.rstrip('/')}{JOIN_PREFIX}{own_id}",
                data=data.encode(),
                headers={"Content-Type": SDP_CONTENT_TYPE},
            ) as response,
        ):
            status = response.status
            body = await response.read()
    except (aiohttp.ClientError, OSError, TimeoutError):
        raise ConnectionFailed(SessionError.SIGNALING_FAILED_TO_SEND) from None
    if status // 100 != 2:
        raise ConnectionFailed(_STATUS_ERRORS.get(status, SessionError.SIGNALING_FAILED_TO_SEND))
    if not body:
        return None
    return ConnectResponse(session_id, _answer_from_body(body.decode("utf-8", "replace")))


def format_response(status: int, reason: str, content_type: str | None, body: bytes) -> bytes:
    head = f"HTTP/1.1 {status} {reason}\r\n"
    if content_type is not None:
        head += f"Content-Type: {content_type}\r\n"
    head += f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    return head.encode("latin-1") + body


def _error(status: int, body: str) -> bytes:
    return format_response(status, body, TEXT_CONTENT_TYPE, body.encode())


INTERNAL_SERVER_ERROR = format_response(500, "Internal Server Error", None, b"")


def parse_network_id(text: str) -> int | None:
    index = 0
    while index < len(text) and text[index] in _C_SPACE:
        index += 1
    if text[index : index + 1] == "-":
        return None
    if text[index : index + 1] == "+":
        index += 1
    rest = text[index:]
    if rest[:2] in ("0x", "0X") and rest[2:3] and rest[2] in _DIGITS[16]:
        digits, base = rest[2:], 16
    elif rest[:1] == "0":
        digits, base = rest, 8
    else:
        digits, base = rest, 10
    if not digits or any(char not in _DIGITS[base] for char in digits):
        return None
    return _bounded_u64(digits, base, _U64_SIGNIFICANT_DIGITS[base])


class _DroppedRequestError(Exception):
    pass


def _parse_content_length(header_lines: list[bytes]) -> int:
    length: int | None = None
    for line in header_lines:
        name, colon, value = line.partition(b":")
        if not colon or name.lower() != b"content-length":
            continue
        text = value.strip(b" \t").decode("latin-1")
        if not _DECIMAL.fullmatch(text):
            raise _DroppedRequestError
        value_int = _bounded_u64(text, 10, _U64_SIGNIFICANT_DIGITS[10])
        if value_int is None or (length is not None and value_int != length):
            raise _DroppedRequestError
        length = value_int
    return 0 if length is None else length


async def _read_request(reader: asyncio.StreamReader) -> tuple[bytes, bytes, bytes] | bytes:
    buffer = bytearray()
    while True:
        if buffer[:2] == b"\x16\x03":
            return TLS_HANDSHAKE_FAILURE_ALERT
        end = buffer.find(b"\r\n\r\n")
        if end != -1:
            break
        if len(buffer) > MAX_REQUEST_SIZE:
            raise _DroppedRequestError
        chunk = await reader.read(MAX_REQUEST_SIZE + 1)
        if not chunk:
            raise _DroppedRequestError
        buffer += chunk
    head_size = end + 4
    lines = bytes(buffer[:end]).split(b"\r\n")
    method, space, rest = lines[0].partition(b" ")
    path, space_after_path, _ = rest.partition(b" ")
    if not space or not space_after_path:
        raise _DroppedRequestError
    if not _METHOD.fullmatch(method) or not path.startswith(b"/"):
        raise _DroppedRequestError
    length = _parse_content_length(lines[1:])
    if head_size + length > MAX_REQUEST_SIZE:
        raise _DroppedRequestError
    while len(buffer) < head_size + length:
        chunk = await reader.read(head_size + length - len(buffer))
        if not chunk:
            raise _DroppedRequestError
        buffer += chunk
    return method, path, bytes(buffer[head_size : head_size + length])


async def _until_eof(reader: asyncio.StreamReader) -> None:
    while await reader.read(MAX_REQUEST_SIZE):
        pass


class HttpSignalingServer:
    def __init__(
        self,
        on_signal: OnSignal,
        *,
        host: str = "",
        port: int = DEFAULT_SERVER_PORT,
        ping: PingBody | None = None,
        join_timeout: float = JOIN_TIMEOUT,
        receive_timeout: float = RECEIVE_TIMEOUT,
    ) -> None:
        self._on_signal = on_signal
        self._host = host
        self._port = port
        self._ping = ping
        self._join_timeout = join_timeout
        self._receive_timeout = receive_timeout
        self._server: asyncio.Server | None = None
        self._connections: dict[asyncio.StreamWriter, asyncio.Task | None] = {}
        self._pending: dict[tuple[int, int], asyncio.Future[str]] = {}
        self._next_session_id = 1
        self.bound_port: int | None = None

    async def start(self) -> None:
        if self._host:
            socket.getaddrinfo(
                self._host, self._port, type=socket.SOCK_STREAM, flags=socket.AI_NUMERICHOST
            )
            self._server = await asyncio.start_server(
                self._on_connection, self._host, self._port, backlog=LISTEN_BACKLOG
            )
        else:
            self._server = await self._start_dual_stack()
        self.bound_port = self._server.sockets[0].getsockname()[1]

    async def _start_dual_stack(self) -> asyncio.Server:
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            sock.bind(("::", self._port))
        except OSError:
            if sock is not None:
                sock.close()
            return await asyncio.start_server(
                self._on_connection, "0.0.0.0", self._port, backlog=LISTEN_BACKLOG
            )
        return await asyncio.start_server(self._on_connection, sock=sock, backlog=LISTEN_BACKLOG)

    async def aclose(self) -> None:
        if self._server is None:
            return
        self._server.close()
        for writer, task in list(self._connections.items()):
            writer.transport.abort()
            if task is not None:
                task.cancel()
        tasks = [task for task in self._connections.values() if task is not None]
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._server.wait_closed()
        self._server = None

    def send(self, remote: NetworkID, message: str) -> bool:
        split = _split_signal(message)
        if split is None:
            return False
        session_id, data = split
        future = self._pending.get((remote.value, session_id))
        if future is None or future.done():
            return False
        future.set_result(data)
        return True

    async def _on_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if len(self._connections) >= MAX_CONNECTIONS:
            writer.close()
            return
        self._connections[writer] = asyncio.current_task()
        try:
            try:
                async with asyncio.timeout(self._receive_timeout):
                    request = await _read_request(reader)
            except (_DroppedRequestError, TimeoutError):
                return
            if isinstance(request, bytes):
                response = request
            elif reader.at_eof():
                return
            else:
                try:
                    response = await self._respond(reader, *request)
                except Exception:
                    response = INTERNAL_SERVER_ERROR
            if response is None:
                return
            writer.write(response)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            self._connections.pop(writer, None)
            writer.close()

    async def _respond(
        self, reader: asyncio.StreamReader, method: bytes, target: bytes, body: bytes
    ) -> bytes | None:
        path = target.decode("latin-1").partition("?")[0]
        if method == b"GET" and path == JOIN_PATH:
            ping = self._ping() if self._ping is not None else None
            content = b"" if ping is None else json.dumps(ping, separators=(",", ":")).encode()
            return format_response(200, "OK", JSON_CONTENT_TYPE, content)
        if method != b"POST" or not path.startswith(JOIN_PREFIX):
            return _error(404, "Not Found")
        network_id = parse_network_id(path[len(JOIN_PREFIX) :])
        if network_id is None:
            return _error(400, "Expected /v1/join/{networkId}")
        if network_id == 0:
            return _error(400, "Invalid networkId")
        if not body:
            return _error(400, "Missing SDP offer in request body")
        offer = body.decode("utf-8", "replace")
        join = asyncio.create_task(self._join(NetworkID.p2p(network_id), offer))
        eof = asyncio.create_task(_until_eof(reader))
        try:
            await asyncio.wait({join, eof}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            eof.cancel()
            if not join.done():
                join.cancel()
            await asyncio.gather(join, eof, return_exceptions=True)
        if join.cancelled():
            return None
        return join.result()

    async def _deliver(self, sender: NetworkID, message: str) -> None:
        try:
            result = self._on_signal(sender, message)
            if inspect.isawaitable(result):
                await result
        except Exception:
            return

    async def _join(self, sender: NetworkID, offer: str) -> bytes:
        session_id = self._next_session_id
        self._next_session_id += 1
        key = (sender.value, session_id)
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[key] = future

        async def wait_answer() -> str:
            await self._deliver(sender, f"CONNECTREQUEST {session_id} {offer}")
            return await future

        try:
            answer = await asyncio.wait_for(wait_answer(), self._join_timeout)
        except TimeoutError:
            return _error(504, "Gateway Timeout")
        finally:
            self._pending.pop(key, None)
            future.cancel()
        return format_response(200, "OK", SDP_CONTENT_TYPE, answer.encode())
