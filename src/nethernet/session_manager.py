"""Session manager — routes signaling messages to per-connection sessions (SPEC.md s9).

Owns the ``connection_id -> Session`` map. Outbound signaling goes through an injected
``send_signal(remote_id, message)`` callback; inbound parsed signals are dispatched to the
matching session, creating a listener session for an unknown CONNECTREQUEST.
"""

from __future__ import annotations

from collections.abc import Callable

from nethernet.errors import SessionError
from nethernet.network_id import NetworkID, new_connection_id
from nethernet.session import DEFAULT_NEGOTIATION_TIMEOUT, Session
from nethernet.signaling.messages import CandidateAdd, ConnectRequest, parse

# Cap on candidates buffered for a not-yet-created session, to bound memory from stray messages.
_MAX_PENDING_CANDIDATES = 64


class SessionManager:
    """Creates and routes sessions for one local peer."""

    def __init__(
        self,
        *,
        local_id: NetworkID,
        send_signal: Callable[[NetworkID, str], None],
        on_session_open: Callable[[Session], None] | None = None,
        on_session_close: Callable[[Session, SessionError], None] | None = None,
        on_packet: Callable[[Session, bytes], None] | None = None,
        ice_servers: list | None = None,
        relay_only: bool = False,
        negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
    ) -> None:
        self._local_id = local_id
        self._send_signal = send_signal
        self._on_session_open = on_session_open
        self._on_session_close = on_session_close
        self._on_packet = on_packet
        self._ice_servers = ice_servers
        self._relay_only = relay_only
        self._negotiation_timeout = negotiation_timeout
        self._sessions: dict[int, Session] = {}
        # Candidates may arrive (UDP reordering) before the CONNECTREQUEST that creates the
        # listener session; buffer them per connection id and flush once it exists.
        self._pending_candidates: dict[int, list[CandidateAdd]] = {}

    @property
    def local_id(self) -> NetworkID:
        return self._local_id

    async def connect(self, remote_id: NetworkID) -> Session:
        """Open a new outgoing (dialer) session to ``remote_id`` (SPEC.md s9.1)."""
        session = self._create_session(new_connection_id(), remote_id, is_dialer=True)
        await session.start()
        return session

    async def handle_signal(self, sender_id: NetworkID, message: str) -> None:
        """Dispatch an inbound signaling string to the matching session (SPEC.md s5, s9)."""
        parsed = parse(message)
        if parsed is None:
            return  # unrecognized / malformed -> ignore (SPEC.md s5.2)
        session = self._sessions.get(parsed.connection_id)
        if session is None:
            if isinstance(parsed, ConnectRequest):
                session = self._create_session(parsed.connection_id, sender_id, is_dialer=False)
                await session.handle_signal(parsed)
                await self._flush_pending_candidates(session)
                return
            if isinstance(parsed, CandidateAdd):
                self._buffer_pending_candidate(parsed)  # session not created yet
                return
            return  # no such session and not a new request -> ignore
        await session.handle_signal(parsed)

    def _buffer_pending_candidate(self, candidate: CandidateAdd) -> None:
        buffer = self._pending_candidates.setdefault(candidate.connection_id, [])
        if len(buffer) < _MAX_PENDING_CANDIDATES:
            buffer.append(candidate)

    async def _flush_pending_candidates(self, session: Session) -> None:
        for candidate in self._pending_candidates.pop(session.connection_id, []):
            await session.handle_signal(candidate)

    def _create_session(
        self, connection_id: int, remote_id: NetworkID, *, is_dialer: bool
    ) -> Session:
        session = Session(
            connection_id=connection_id,
            local_id=self._local_id,
            remote_id=remote_id,
            is_dialer=is_dialer,
            send_signal=lambda message: self._send_signal(remote_id, message),
            on_open=self._handle_session_open,
            on_close=self._handle_session_close,
            on_packet=self._handle_packet,
            ice_servers=self._ice_servers,
            relay_only=self._relay_only,
            negotiation_timeout=self._negotiation_timeout,
        )
        self._sessions[connection_id] = session
        return session

    def _handle_session_open(self, session: Session) -> None:
        if self._on_session_open is not None:
            self._on_session_open(session)

    def _handle_session_close(self, session: Session, error: SessionError) -> None:
        self._sessions.pop(session.connection_id, None)
        if self._on_session_close is not None:
            self._on_session_close(session, error)

    def _handle_packet(self, session: Session, data: bytes) -> None:
        if self._on_packet is not None:
            self._on_packet(session, data)

    async def aclose(self) -> None:
        for session in list(self._sessions.values()):
            await session.aclose()
        self._sessions.clear()
