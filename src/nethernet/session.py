from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum

from nethernet.constants import DEFAULT_NEGOTIATION_TIMEOUT
from nethernet.errors import ConnectionFailed, SendType, SessionError
from nethernet.identity import strip_identity
from nethernet.network_id import NetworkID
from nethernet.signaling.messages import CandidateAdd, ConnectRequest, ConnectResponse
from nethernet.transport.peer_connection import PeerConnection, strip_candidates

LanSend = Callable[[NetworkID, str], None]
HttpSend = Callable[[NetworkID, str], Awaitable[SessionError | None]]

_IDENTITY_PREFIX = "a=identity:"
_ACTIVE_ICE_STATES = frozenset({"connected", "completed", "disconnected"})
_ERRORED_ICE_STATES = frozenset({"failed", "closed"})
_SNAPSHOT_INTERVAL = 1
_SNAPSHOT_MAX_AGE = 2.0


class SessionState(Enum):
    """Whether a session is connecting, active or errored, as its ICE state says."""

    CONNECTING = "connecting"
    ACTIVE = "active"
    ERRORED = "errored"


@dataclass(frozen=True)
class SessionStatus:
    state: SessionState
    ice_state: str
    rtt_ms: int | None = None
    bytes_sent: int = 0
    send_rate: int = 0
    bytes_received: int = 0
    packets_sent: int = 0
    relay: bool = False


class Route(Enum):
    LAN = "lan"
    HTTP = "http"


@dataclass
class SignalingChannels:
    lan_send: LanSend | None = None
    http_send: HttpSend | None = None
    is_lan_peer: Callable[[NetworkID], bool] | None = None
    lan_signaling: bool = True

    def lan_usable(self, remote_id: NetworkID) -> bool:
        if not self.lan_signaling or self.lan_send is None:
            return False
        return self.is_lan_peer is None or self.is_lan_peer(remote_id)

    def route(self, remote_id: NetworkID, *, prefers_lan: bool) -> Route | None:
        if self.lan_usable(remote_id) and (self.http_send is None or prefers_lan):
            return Route.LAN
        if self.http_send is not None:
            return Route.HTTP
        return None

    async def send(self, route: Route, remote_id: NetworkID, message: str) -> SessionError | None:
        if route is Route.LAN:
            if self.lan_send is not None:
                self.lan_send(remote_id, message)
            return None
        if self.http_send is None:
            return SessionError.NO_SIGNALING_CHANNEL
        error = await self.http_send(remote_id, message)
        return None if error is None else SessionError(error)


def identity_value(sdp: str) -> str | None:
    for line in sdp.splitlines():
        if line[: len(_IDENTITY_PREFIX)].lower() == _IDENTITY_PREFIX:
            return line[len(_IDENTITY_PREFIX) :]
    return None


def anonymize_ip(ip: str) -> str:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if address.version == 4:
        return ".".join(str(address).split(".")[:3] + ["0"])
    head = address.compressed.split("::")[0]
    groups = [group for group in head.split(":") if group][:3]
    return ":".join(groups) + "::"


def candidate_info(text: str) -> tuple[tuple[str, str, str, str], dict[str, str]] | None:
    value = text[len("a=") :] if text.startswith("a=") else text
    if value.startswith("candidate:"):
        value = value[len("candidate:") :]
    fields = value.split()
    if len(fields) < 8 or fields[6] != "typ":
        return None
    protocol, ip, port, kind = fields[2].lower(), fields[4], fields[5], fields[7]
    return (protocol, ip, port, kind), {"type": kind, "ip": anonymize_ip(ip)}


def _milliseconds(seconds: float | None) -> int | None:
    return None if seconds is None else int(seconds * 1000)


def _timestamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _now() -> datetime:
    return datetime.now(UTC)


_COUNTERS = ("bytesSent", "packetsSent", "bytesReceived", "packetsReceived")


class _Rate:
    def __init__(self) -> None:
        self.current = 0
        self.average = 0.0
        self.maximum = 0
        self._samples = 0

    def update(self, value: int) -> None:
        self.current = value
        self._samples += 1
        self.average += (value - self.average) / self._samples
        self.maximum = max(self.maximum, value)


class _Pair:
    def __init__(self, local: str, remote: str) -> None:
        self.local = local
        self.remote = remote
        self.states: list[str] = []
        self.counters = [0, 0, 0, 0]
        self.rates = [_Rate() for _ in _COUNTERS]
        self.rtt: float | None = None
        self.avgrtt: float | None = None

    def add(self, deltas: list[int], elapsed: int) -> None:
        for index, delta in enumerate(deltas):
            self.counters[index] += delta
            self.rates[index].update(delta // elapsed)

    def summary(self) -> dict[str, object]:
        out: dict[str, object] = {"local": self.local, "remote": self.remote}
        if self.states:
            out["states"] = list(self.states)
        for name, count, rate in zip(_COUNTERS, self.counters, self.rates, strict=True):
            title = name[0].upper() + name[1:]
            values = (
                (name, count),
                (f"{name}Rate", rate.current),
                (f"avg{title}Rate", rate.average),
                (f"max{title}Rate", rate.maximum),
            )
            out.update((key, value) for key, value in values if value)
        if self.rtt:
            out["rtt"] = float(self.rtt)
        if self.avgrtt:
            out["avgrtt"] = float(self.avgrtt)
        return out


class Statistics:
    def __init__(self, now: float) -> None:
        self.bytes_sent = 0
        self.bytes_received = 0
        self.packets_sent = 0
        self.packets_received = 0
        self.snapshot: SessionStatus | None = None
        self.taken_at: float | None = None
        self.start_time = _now()
        self._base_time = now
        self._base = [0, 0, 0, 0]
        self._send_rate = 0
        self._local: dict[tuple[str, str, str, str], tuple[str, dict[str, str]]] = {}
        self._remote: dict[tuple[str, str, str, str], tuple[str, dict[str, str]]] = {}
        self._pairs: dict[str, _Pair] = {}
        self._active: str | None = None
        self._rtt: float | None = None
        self._relay = False

    def see_local_candidate(self, text: str) -> str | None:
        return self._see(self._local, "L", text)

    def see_remote_candidate(self, text: str) -> str | None:
        return self._see(self._remote, "R", text)

    @staticmethod
    def _see(table: dict, prefix: str, text: str) -> str | None:
        parsed = candidate_info(text)
        if parsed is None:
            return None
        key, info = parsed
        if key not in table:
            table[key] = (f"{prefix}{len(table) + 1}", info)
        return table[key][0]

    def _totals(self) -> list[int]:
        return [self.bytes_sent, self.packets_sent, self.bytes_received, self.packets_received]

    def take(self, now: float, state: SessionState, ice_state: str, pair: dict | None) -> bool:
        elapsed = int(now - (self.taken_at if self.taken_at is not None else self._base_time))
        if elapsed < _SNAPSHOT_INTERVAL:
            return False
        totals = self._totals()
        deltas = [total - base for total, base in zip(totals, self._base, strict=True)]
        self._base = totals
        self.taken_at = now
        self._send_rate = deltas[0] // elapsed
        if pair is not None:
            self._take_pair(pair, deltas, elapsed)
        self.snapshot = SessionStatus(
            state=state,
            ice_state=ice_state,
            rtt_ms=_milliseconds(self._rtt),
            bytes_sent=self.bytes_sent,
            send_rate=self._send_rate,
            bytes_received=self.bytes_received,
            packets_sent=self.packets_sent,
            relay=self._relay,
        )
        return True

    def _take_pair(self, pair: dict, deltas: list[int], elapsed: int) -> None:
        local = self.see_local_candidate(str(pair.get("local", "")))
        remote = self.see_remote_candidate(str(pair.get("remote", "")))
        if local is None or remote is None:
            return
        pair_id = f"{local}-{remote}"
        entry = self._pairs.setdefault(pair_id, _Pair(local, remote))
        state = str(pair.get("state", ""))
        if state and (not entry.states or entry.states[-1] != state):
            entry.states.append(state)
        entry.add(deltas, elapsed)
        rtt = pair.get("rtt")
        responses = pair.get("responses_received")
        entry.rtt = rtt
        entry.avgrtt = None if rtt is None else (rtt / responses if responses else rtt)
        self._rtt = rtt
        self._active = pair_id
        remote_info = candidate_info(str(pair.get("remote", "")))
        self._relay = remote_info is not None and remote_info[0][3] == "relay"

    def summary(self) -> str:
        out: dict[str, object] = {
            "startTime": _timestamp(self.start_time),
            "endTime": _timestamp(_now()),
            "errors": [],
        }
        if self._active:
            out["active"] = self._active
        for name, table in (("local", self._local), ("remote", self._remote)):
            if table:
                out[name] = {
                    candidate_id: {key: value for key, value in info.items() if value}
                    for candidate_id, info in table.values()
                }
        if self._pairs:
            out["pairs"] = {pair_id: pair.summary() for pair_id, pair in self._pairs.items()}
        return json.dumps(out, separators=(",", ":"))


class Session:
    def __init__(
        self,
        *,
        connection_id: int,
        remote_id: NetworkID,
        is_dialer: bool,
        channels: SignalingChannels,
        trickle: bool = True,
        ice_servers: list | None = None,
        negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
        peer_connection_factory: Callable[..., PeerConnection] = PeerConnection,
        on_open: Callable[[Session], None] | None = None,
        on_close: Callable[[Session, SessionError | None, str], None] | None = None,
        on_packet: Callable[[Session, bytes], None] | None = None,
    ) -> None:
        self.connection_id = connection_id
        self.remote_id = remote_id
        self.is_dialer = is_dialer
        self.trickle = trickle
        self.remote_identity: str | None = None
        self.offer_received = False
        self.prefers_lan = False
        self.close_task: asyncio.Task | None = None

        self._channels = channels
        self._ice_servers = ice_servers
        self._negotiation_timeout = max(0.0, negotiation_timeout)
        self._factory = peer_connection_factory
        self._on_open = on_open
        self._on_close = on_close
        self._on_packet = on_packet

        self._loop = asyncio.get_running_loop()
        self._pc: PeerConnection | None = None
        self._deferred: list[str] = []
        self._outbox: list[tuple[bytes, SendType]] = []
        self._answer_applied = False
        self._deadline: float | None = None
        self._opened = False
        self._closed = False
        self._tasks: set[asyncio.Task] = set()
        self._stats = Statistics(self._loop.time())

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def opened(self) -> bool:
        return self._opened

    @property
    def peer_connection(self) -> PeerConnection | None:
        return self._pc

    @property
    def deadline(self) -> float | None:
        return self._deadline

    def create_peer_connection(self, *, relay_only: bool = False) -> SessionError | None:
        try:
            self._pc = self._factory(
                is_dialer=self.is_dialer,
                ice_servers=self._ice_servers,
                trickle=self.trickle,
                relay_only=relay_only,
                on_packet=self._handle_packet if self._on_packet is not None else None,
                on_ice_connected=self._handle_ice_connected,
                on_ice_failed=self._handle_ice_failed,
                on_close=self.close,
            )
        except ConnectionFailed as exc:
            return exc.error
        outbox, self._outbox = self._outbox, []
        for data, send_type in outbox:
            self._pc.send(data, send_type)
        return None

    async def start(self, *, identity: Callable[[str], str] | None = None) -> None:
        if self._pc is None or self._closed:
            return
        try:
            offer = await self._pc.create_offer(identity=identity)
        except ConnectionFailed as exc:
            self.close(exc.error)
            return
        if self._closed:
            return
        if self._send_description(ConnectRequest, offer):
            self._deadline = self._loop.time() + self._negotiation_timeout

    async def apply_offer(
        self,
        sdp: str,
        *,
        via_lan: bool,
        relay_only: bool = False,
        identity: Callable[[str], str] | None = None,
    ) -> None:
        if self._closed or self.offer_received:
            return
        self.offer_received = True
        self.prefers_lan = via_lan
        error = self.create_peer_connection(relay_only=relay_only)
        if error is not None:
            self.close(error)
            return
        self.remote_identity = identity_value(sdp)
        for candidate in strip_candidates(sdp)[1]:
            self._stats.see_remote_candidate(candidate)
        pc = self._pc
        deferred, self._deferred = self._deferred, []
        try:
            for candidate in deferred:
                await pc.add_remote_candidate(candidate)
            answer = await pc.create_answer(
                strip_identity(sdp), identity=None if via_lan else identity
            )
        except ConnectionFailed as exc:
            self.close(exc.error)
            return
        if not self._closed:
            self._send_description(ConnectResponse, answer)

    async def apply_answer(self, sdp: str) -> None:
        if not self.is_dialer or self._answer_applied or self._closed or self._pc is None:
            return
        self._answer_applied = True
        self.remote_identity = identity_value(sdp)
        for candidate in strip_candidates(sdp)[1]:
            self._stats.see_remote_candidate(candidate)
        try:
            await self._pc.set_remote_answer(strip_identity(sdp))
        except ConnectionFailed as exc:
            self.close(exc.error)
            return
        self._deadline = None

    async def add_candidate(self, candidate: str) -> None:
        if self._closed:
            return
        self._stats.see_remote_candidate(candidate)
        if self._pc is None:
            self._deferred.append(candidate)
            return
        await self._pc.add_remote_candidate(candidate)

    def _send_description(self, message: type[ConnectRequest | ConnectResponse], sdp: str) -> bool:
        route = self._channels.route(self.remote_id, prefers_lan=self.prefers_lan)
        if route is None:
            self.close(SessionError.NO_SIGNALING_CHANNEL)
            return False
        stripped, candidates = strip_candidates(sdp)
        for candidate in candidates:
            self._stats.see_local_candidate(candidate)
        if not (self.trickle and route is Route.LAN):
            self._signal(route, message(self.connection_id, sdp).serialize())
            return True
        self._signal(route, message(self.connection_id, stripped).serialize())
        for candidate in candidates:
            self._signal(route, CandidateAdd(self.connection_id, candidate).serialize())
        return True

    def _signal(self, route: Route, message: str) -> None:
        if route is Route.LAN:
            self._channels.lan_send(self.remote_id, message)
            return
        task = self._loop.create_task(self._send_http(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send_http(self, message: str) -> None:
        error = await self._channels.send(Route.HTTP, self.remote_id, message)
        if error is None or self._closed:
            return
        if error == SessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED and not self.is_dialer:
            return
        self.close(error)

    def _handle_ice_connected(self) -> None:
        if self._opened or self._closed:
            return
        self._opened = True
        if self._on_open is not None:
            self._on_open(self)

    def _handle_ice_failed(self) -> None:
        self.close(SessionError.ICE)

    def _handle_packet(self, data: bytes) -> None:
        self._stats.bytes_received += len(data)
        self._stats.packets_received += 1
        if self._on_packet is not None:
            self._on_packet(self, data)

    @property
    def ice_state(self) -> str:
        return self._pc.ice_connection_state if self._pc is not None else "new"

    @property
    def state(self) -> SessionState:
        ice_state = self.ice_state
        if ice_state in _ERRORED_ICE_STATES:
            return SessionState.ERRORED
        if ice_state in _ACTIVE_ICE_STATES:
            return SessionState.ACTIVE
        return SessionState.CONNECTING

    def send(self, data: bytes, send_type: SendType = SendType.RELIABLE) -> bool:
        if self._pc is None:
            self._outbox.append((data, send_type))
        elif not self._pc.send(data, send_type):
            return False
        self._stats.bytes_sent += len(data)
        self._stats.packets_sent += 1
        return True

    def read(self, max_size: int | None = None) -> bytes | None:
        if self._pc is None:
            return None
        data = self._pc.read(max_size)
        if data is not None:
            self._stats.bytes_received += len(data)
            self._stats.packets_received += 1
        return data

    def peek(self) -> int | None:
        return self._pc.peek() if self._pc is not None else None

    def status(self) -> SessionStatus:
        state, ice_state = self.state, self.ice_state
        snapshot = self._stats.snapshot
        now = self._loop.time()
        if self._stats.taken_at is None or now - self._stats.taken_at > _SNAPSHOT_MAX_AGE:
            self._take_snapshot(now)
        if snapshot is None:
            return SessionStatus(state=state, ice_state=ice_state)
        return replace(snapshot, state=state, ice_state=ice_state)

    def _take_snapshot(self, now: float) -> None:
        pair = self._pc.candidate_pair() if self._pc is not None else None
        self._stats.take(now, self.state, self.ice_state, pair)

    def sweep(self, now: float) -> None:
        if self._closed:
            return
        if self._pc is not None and self._pc.ice_connection_state in _ERRORED_ICE_STATES:
            self.close(SessionError.ICE)
        elif self.is_dialer and self._deadline is not None and now >= self._deadline:
            self.close(SessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE)

    def close(self, error: SessionError | None = None) -> bool:
        if self._closed:
            return False
        self._closed = True
        self._deadline = None
        self._take_snapshot(self._loop.time())
        for task in self._tasks:
            task.cancel()
        if self._pc is not None:
            self.close_task = self._loop.create_task(self._pc.close())
        if self._on_close is not None:
            self._on_close(
                self, None if error is None else SessionError(error), self._stats.summary()
            )
        return True

    async def wait_closed(self) -> None:
        if self.close_task is not None:
            await asyncio.shield(self.close_task)
