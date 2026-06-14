"""Session negotiation state machine — SPEC.md s9.

A ``Session`` is one connection attempt between two peers, identified by a 64-bit
``connection_id``. It drives a :class:`~nethernet.transport.peer_connection.PeerConnection`,
exchanges signaling messages through an injected ``send_signal`` callback, and enforces the
negotiation timeout (SPEC.md s9.4).

This module implements the **dialer** (offerer) role (SPEC.md s9.1); the **listener** role
(s9.2) is added on top of the same class.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from enum import Enum, auto

from nethernet.constants import DEFAULT_NEGOTIATION_TIMEOUT
from nethernet.errors import ESendType, ESessionError
from nethernet.network_id import NetworkID
from nethernet.signaling.messages import (
    CandidateAdd,
    ConnectError,
    ConnectRequest,
    ConnectResponse,
    SignalingMessage,
)
from nethernet.transport.peer_connection import PeerConnection


class SessionState(Enum):
    IDLE = auto()
    OFFER_SENT = auto()  # dialer: offer sent, awaiting response
    ANSWER_SENT = auto()  # listener: answer sent, awaiting ICE
    NEGOTIATING = auto()  # dialer: answer received, awaiting ICE
    CONNECTED = auto()
    CLOSED = auto()


class Session:
    """One negotiation/connection attempt between two peers (SPEC.md s9)."""

    def __init__(
        self,
        *,
        connection_id: int,
        local_id: NetworkID,
        remote_id: NetworkID,
        is_dialer: bool,
        send_signal: Callable[[str], None],
        on_open: Callable[[Session], None] | None = None,
        on_close: Callable[[Session, ESessionError], None] | None = None,
        on_packet: Callable[[Session, bytes], None] | None = None,
        ice_servers: list | None = None,
        relay_only: bool = False,
        negotiation_timeout: float = DEFAULT_NEGOTIATION_TIMEOUT,
    ) -> None:
        self.connection_id = connection_id
        self.local_id = local_id
        self.remote_id = remote_id
        self.is_dialer = is_dialer
        self.state = SessionState.IDLE

        self._send_signal = send_signal
        self._on_open = on_open
        self._on_close = on_close
        self._on_packet = on_packet
        self._negotiation_timeout = negotiation_timeout

        self._timeout_handle: asyncio.TimerHandle | None = None
        self._close_task: asyncio.Task | None = None

        self._pc = PeerConnection(
            is_dialer=is_dialer,
            ice_servers=ice_servers,
            relay_only=relay_only,
            on_packet=self._handle_packet,
            on_channels_open=self._handle_channels_open,
            on_connection_state_change=self._handle_connection_state,
        )

    # -- dialer entry point ------------------------------------------------------------

    async def start(self) -> None:
        """Dialer: create the offer and send CONNECTREQUEST (SPEC.md s9.1)."""
        offer = await self._pc.create_offer()
        self.state = SessionState.OFFER_SENT
        self._send_signal(ConnectRequest(self.connection_id, offer).serialize())
        self._arm_timeout(ESessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE)

    # -- inbound signaling -------------------------------------------------------------

    async def handle_signal(self, message: SignalingMessage) -> None:
        """Dispatch an inbound signaling message for this session."""
        if self.state == SessionState.CLOSED:
            return
        if isinstance(message, ConnectError):
            self._handle_connect_error(message)
        elif isinstance(message, CandidateAdd):
            await self._pc.add_remote_candidate(message.candidate)
        elif self.is_dialer and isinstance(message, ConnectResponse):
            await self._handle_response(message)
        elif not self.is_dialer and isinstance(message, ConnectRequest):
            await self._handle_request(message)
        # other messages (e.g. ConnectRequest to a dialer) are ignored

    async def _handle_response(self, message: ConnectResponse) -> None:
        if self.state != SessionState.OFFER_SENT:
            return
        self.state = SessionState.NEGOTIATING
        self._arm_timeout(ESessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_ACCEPT)
        await self._pc.set_remote_answer(message.sdp)

    async def _handle_request(self, message: ConnectRequest) -> None:
        """Listener: apply the offer, answer, and send CONNECTRESPONSE (SPEC.md s9.2)."""
        if self.state != SessionState.IDLE:
            return
        answer = await self._pc.create_answer(message.sdp)
        self.state = SessionState.ANSWER_SENT
        self._send_signal(ConnectResponse(self.connection_id, answer).serialize())
        self._arm_timeout(ESessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_ACCEPT)

    def _handle_connect_error(self, message: ConnectError) -> None:
        # SPEC.md s5.5: a SignalingUnicastMessageDeliveryFailed (19) MUST NOT close an incoming
        # (listener) session; only outgoing (dialer) sessions act on it. All other errors close.
        if (
            not self.is_dialer
            and message.error == ESessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED
        ):
            return
        self.close(message.error)

    # -- transport callbacks -----------------------------------------------------------

    def _handle_channels_open(self) -> None:
        if self.state in (SessionState.CONNECTED, SessionState.CLOSED):
            return
        self.state = SessionState.CONNECTED
        self._cancel_timeout()
        if self._on_open is not None:
            self._on_open(self)

    def _handle_connection_state(self, state: str) -> None:
        if self.state == SessionState.CLOSED:
            return
        if state == "failed":
            self.close(
                ESessionError.DATA_CHANNEL_CLOSED
                if self.state == SessionState.CONNECTED
                else ESessionError.ICE
            )
        elif state == "closed" and self.state == SessionState.CONNECTED:
            self.close(ESessionError.DATA_CHANNEL_CLOSED)

    def _handle_packet(self, data: bytes) -> None:
        if self._on_packet is not None:
            self._on_packet(self, data)

    # -- application I/O ----------------------------------------------------------------

    def send(self, data: bytes, send_type: ESendType) -> bool:
        """Send an application packet; only valid once CONNECTED (SPEC.md s6.3)."""
        if self.state != SessionState.CONNECTED:
            return False
        return self._pc.send(data, send_type)

    def read(self, max_size: int | None = None) -> bytes | None:
        return self._pc.read(max_size)

    def peek(self) -> int | None:
        return self._pc.peek()

    # -- timeouts & close --------------------------------------------------------------

    def _arm_timeout(self, error: ESessionError) -> None:
        self._cancel_timeout()
        loop = asyncio.get_running_loop()
        self._timeout_handle = loop.call_later(self._negotiation_timeout, self.close, error)

    def _cancel_timeout(self) -> None:
        if self._timeout_handle is not None:
            self._timeout_handle.cancel()
            self._timeout_handle = None

    def close(self, error: ESessionError = ESessionError.NONE, notify_remote: bool = False) -> None:
        """Tear down the session and notify the application (SPEC.md s9.5)."""
        if self.state == SessionState.CLOSED:
            return
        self.state = SessionState.CLOSED
        self._cancel_timeout()
        if notify_remote:
            self._send_signal(ConnectError(self.connection_id, error).serialize())
        self._close_task = asyncio.create_task(self._pc.close())
        if self._on_close is not None:
            self._on_close(self, error)

    async def aclose(self, error: ESessionError = ESessionError.NONE) -> None:
        """Close and await transport teardown (for tests / graceful shutdown)."""
        if self.state != SessionState.CLOSED:
            self.close(error)
        if self._close_task is not None:
            await self._close_task
