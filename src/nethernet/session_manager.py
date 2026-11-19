from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nethernet.constants import DEFAULT_NEGOTIATION_TIMEOUT
from nethernet.errors import ConnectionFlags, SendType, SessionError
from nethernet.identity import fingerprint_entries
from nethernet.network_id import NetworkID, new_connection_id
from nethernet.session import (
    HttpSend,
    LanSend,
    Route,
    Session,
    SessionStatus,
    SignalingChannels,
    identity_value,
)
from nethernet.signaling.messages import (
    CandidateAdd,
    ConnectError,
    ConnectRequest,
    ConnectResponse,
    parse,
)
from nethernet.transport.peer_connection import PeerConnection

SWEEP_INTERVAL = 5.0


@dataclass(frozen=True)
class AcceptRequest:
    remote_id: NetworkID
    connection_id: int
    is_offer: bool
    identity: str | None
    fingerprints: list[tuple[str, str]]
    via_lan: bool
    sdp: str


Accept = Callable[[AcceptRequest], Awaitable[bool] | bool]


class SessionManager:
    def __init__(
        self,
        *,
        local_id: NetworkID,
        lan_send: LanSend | None = None,
        http_send: HttpSend | None = None,
        is_lan_peer: Callable[[NetworkID], bool] | None = None,
        lan_signaling: bool = True,
        trickle: bool = True,
        ice_servers: list | None = None,
        negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
        accept: Accept | None = None,
        connection_flags: Callable[[NetworkID], ConnectionFlags] | None = None,
        identity: Callable[[str], str] | None = None,
        on_session_open: Callable[[Session], None] | None = None,
        on_session_close: Callable[[Session, SessionError | None, str], None] | None = None,
        on_packet: Callable[[Session, bytes], None] | None = None,
        peer_connection_factory: Callable[..., PeerConnection] = PeerConnection,
    ) -> None:
        self._local_id = local_id
        self._channels = SignalingChannels(
            lan_send=lan_send,
            http_send=http_send,
            is_lan_peer=is_lan_peer,
            lan_signaling=lan_signaling,
        )
        self._trickle = trickle
        self._ice_servers = ice_servers
        self._negotiation_timeout = negotiation_timeout
        self._accept = accept
        self._connection_flags = connection_flags
        self._identity = identity
        self._on_session_open = on_session_open
        self._on_session_close = on_session_close
        self._on_packet = on_packet
        self._factory = peer_connection_factory

        self._sessions: dict[NetworkID, dict[int, Session]] = {}
        self._tasks: set[asyncio.Task] = set()
        self._closing: set[asyncio.Task] = set()
        self._sweep_handle: asyncio.TimerHandle | None = None
        self._closed = False

    @property
    def local_id(self) -> NetworkID:
        return self._local_id

    @property
    def sessions(self) -> list[Session]:
        return [session for by_id in self._sessions.values() for session in by_id.values()]

    def get(self, remote_id: NetworkID, connection_id: int) -> Session | None:
        return self._sessions.get(remote_id, {}).get(connection_id)

    def dial(
        self,
        remote_id: NetworkID,
        *,
        identity: Callable[[str], str] | None = None,
        connection_id: int | None = None,
    ) -> Session:
        if connection_id is None:
            connection_id = new_connection_id()
        session = self._new_session(remote_id, connection_id, is_dialer=True, trickle=self._trickle)
        error = session.create_peer_connection(relay_only=self._relay_only(remote_id))
        if error is not None:
            asyncio.get_running_loop().call_soon(session.close, error)
        else:
            self._spawn(session.start(identity=identity))
        return session

    async def handle_signal(
        self, sender: NetworkID, message: str, *, via_lan: bool = False
    ) -> None:
        parsed = parse(message)
        if parsed is None or self._closed:
            return
        if isinstance(parsed, ConnectRequest):
            await self._handle_request(sender, parsed, via_lan)
        elif isinstance(parsed, ConnectResponse):
            await self._handle_response(sender, parsed, via_lan)
        elif isinstance(parsed, ConnectError):
            session = self.get(sender, parsed.connection_id)
            if session is not None and session.is_dialer:
                session.close(parsed.error)
        elif isinstance(parsed, CandidateAdd):
            session = self.get(sender, parsed.connection_id)
            if session is None:
                session = self._new_session(
                    sender,
                    parsed.connection_id,
                    is_dialer=False,
                    trickle=self._trickle and not via_lan,
                )
            await session.add_candidate(parsed.candidate)

    async def _handle_request(
        self, sender: NetworkID, request: ConnectRequest, via_lan: bool
    ) -> None:
        accepted = await self._decide(sender, request.connection_id, request.sdp, True, via_lan)
        if accepted is None:
            return
        if not accepted:
            reply = ConnectError(request.connection_id, SessionError.IDENTITY_NOT_ALLOWED)
            self._reply(sender, reply.serialize(), via_lan)
            return
        try:
            relay_only = self._relay_only(sender)
        except Exception:
            return
        session = self.get(sender, request.connection_id)
        if session is None:
            session = self._new_session(
                sender,
                request.connection_id,
                is_dialer=False,
                trickle=self._trickle and not via_lan,
            )
        elif session.is_dialer or session.offer_received:
            return
        await session.apply_offer(
            request.sdp,
            via_lan=via_lan,
            relay_only=relay_only,
            identity=self._identity,
        )

    async def _handle_response(
        self, sender: NetworkID, response: ConnectResponse, via_lan: bool
    ) -> None:
        accepted = await self._decide(sender, response.connection_id, response.sdp, False, via_lan)
        session = self.get(sender, response.connection_id)
        if session is None or accepted is None:
            return
        if not accepted:
            session.close(SessionError.IDENTITY_NOT_ALLOWED)
        elif session.is_dialer:
            await session.apply_answer(response.sdp)

    async def _decide(
        self, sender: NetworkID, connection_id: int, sdp: str, is_offer: bool, via_lan: bool
    ) -> bool | None:
        if self._accept is None:
            return True
        request = AcceptRequest(
            remote_id=sender,
            connection_id=connection_id,
            is_offer=is_offer,
            identity=identity_value(sdp),
            fingerprints=fingerprint_entries(sdp),
            via_lan=via_lan,
            sdp=sdp,
        )
        try:
            result = self._accept(request)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            return None
        return bool(result)

    def _reply(self, remote_id: NetworkID, message: str, via_lan: bool) -> None:
        route = self._channels.route(remote_id, prefers_lan=via_lan)
        if route is Route.LAN:
            self._channels.lan_send(remote_id, message)
        elif route is Route.HTTP:
            self._spawn(self._channels.send(route, remote_id, message))

    def _relay_only(self, remote_id: NetworkID) -> bool:
        if self._connection_flags is None:
            return False
        return bool(self._connection_flags(remote_id) & ConnectionFlags.RELAY_ONLY)

    def _new_session(
        self, remote_id: NetworkID, connection_id: int, *, is_dialer: bool, trickle: bool
    ) -> Session:
        session = Session(
            connection_id=connection_id,
            remote_id=remote_id,
            is_dialer=is_dialer,
            channels=self._channels,
            trickle=trickle,
            ice_servers=self._ice_servers,
            negotiation_timeout=self._negotiation_timeout,
            peer_connection_factory=self._factory,
            on_open=self._handle_open,
            on_close=self._handle_close,
            on_packet=self._on_packet,
        )
        self._sessions.setdefault(remote_id, {})[connection_id] = session
        self._schedule_sweep()
        return session

    def _spawn(self, coroutine: Awaitable[object]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _schedule_sweep(self) -> None:
        if self._sweep_handle is None and not self._closed:
            loop = asyncio.get_running_loop()
            self._sweep_handle = loop.call_later(SWEEP_INTERVAL, self._sweep)

    def _sweep(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        for session in self.sessions:
            session.sweep(now)
        self._sweep_handle = loop.call_later(SWEEP_INTERVAL, self._sweep)

    def _handle_open(self, session: Session) -> None:
        if self._on_session_open is not None:
            self._on_session_open(session)

    def _handle_close(self, session: Session, error: SessionError | None, summary: str) -> None:
        by_id = self._sessions.get(session.remote_id)
        if by_id is not None and by_id.get(session.connection_id) is session:
            del by_id[session.connection_id]
            if not by_id:
                del self._sessions[session.remote_id]
        if session.close_task is not None:
            self._closing.add(session.close_task)
            session.close_task.add_done_callback(self._closing.discard)
        if self._on_session_close is not None:
            self._on_session_close(session, error, summary)

    def send(
        self,
        remote_id: NetworkID,
        connection_id: int,
        data: bytes,
        send_type: SendType = SendType.RELIABLE,
    ) -> bool:
        session = self.get(remote_id, connection_id)
        return session is not None and session.send(data, send_type)

    def read(
        self, remote_id: NetworkID, connection_id: int, max_size: int | None = None
    ) -> bytes | None:
        session = self.get(remote_id, connection_id)
        return session.read(max_size) if session is not None else None

    def peek(self, remote_id: NetworkID, connection_id: int) -> int | None:
        session = self.get(remote_id, connection_id)
        return session.peek() if session is not None else None

    def state(self, remote_id: NetworkID, connection_id: int) -> SessionStatus | None:
        if remote_id == self._local_id:
            return None
        session = self.get(remote_id, connection_id)
        return session.status() if session is not None else None

    def close(self, remote_id: NetworkID, connection_id: int) -> bool:
        session = self.get(remote_id, connection_id)
        return session is not None and session.close()

    async def __aenter__(self) -> SessionManager:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._closed = True
        if self._sweep_handle is not None:
            self._sweep_handle.cancel()
            self._sweep_handle = None
        for session in self.sessions:
            session.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._closing:
            await asyncio.shield(asyncio.gather(*self._closing, return_exceptions=True))
