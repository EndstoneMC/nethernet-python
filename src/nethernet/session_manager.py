"""Session manager — routes signaling messages to per-connection sessions (SPEC.md s9).

Owns the ``connection_id -> Session`` map. Outbound signaling goes through an injected
``send_signal(remote_id, message)`` callback; inbound parsed signals are dispatched to the
matching session, creating a listener session for an unknown CONNECTREQUEST.
"""

from __future__ import annotations

from collections.abc import Callable

from nethernet.errors import ESessionError
from nethernet.network_id import NetworkID, new_connection_id
from nethernet.session import DEFAULT_NEGOTIATION_TIMEOUT, Session
from nethernet.signaling.messages import ConnectRequest, parse


class SessionManager:
    """Creates and routes sessions for one local peer."""

    def __init__(
        self,
        *,
        local_id: NetworkID,
        send_signal: Callable[[NetworkID, str], None],
        on_session_open: Callable[[Session], None] | None = None,
        on_session_close: Callable[[Session, ESessionError], None] | None = None,
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
            if not isinstance(parsed, ConnectRequest):
                return  # no such session and not a new request -> ignore
            session = self._create_session(parsed.connection_id, sender_id, is_dialer=False)
        await session.handle_signal(parsed)

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

    def _handle_session_close(self, session: Session, error: ESessionError) -> None:
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
