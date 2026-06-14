"""Listener session state machine — SPEC.md s9.2, s5.5."""

import asyncio

from nethernet.errors import ESessionError
from nethernet.network_id import NetworkID
from nethernet.session import Session, SessionState
from nethernet.signaling.messages import (
    CandidateAdd,
    ConnectError,
    ConnectRequest,
    ConnectResponse,
    parse,
)
from nethernet.transport.peer_connection import PeerConnection


async def make_offer() -> str:
    pc = PeerConnection(is_dialer=True)
    offer = await pc.create_offer()
    await pc.close()
    return offer


def make_listener(sent, *, on_open=None, on_close=None, timeout=10.0):
    return Session(
        connection_id=777,
        local_id=NetworkID.p2p(2),
        remote_id=NetworkID.p2p(1),
        is_dialer=False,
        send_signal=sent.append,
        on_open=on_open,
        on_close=on_close,
        negotiation_timeout=timeout,
    )


async def test_request_produces_connect_response_answer():
    sent: list[str] = []
    offer = await make_offer()
    s = make_listener(sent)
    try:
        await s.handle_signal(ConnectRequest(777, offer))
        assert len(sent) == 1
        msg = parse(sent[0])
        assert isinstance(msg, ConnectResponse)
        assert msg.connection_id == 777
        assert "v=0" in msg.sdp  # a real SDP answer
        assert s.state == SessionState.ANSWER_SENT
    finally:
        await s.aclose()


async def test_listener_ignores_unicast_delivery_failed_error():
    # SPEC.md s5.5: error 19 MUST NOT close an incoming (listener) session.
    sent: list[str] = []
    closed: list[ESessionError] = []
    s = make_listener(sent, on_close=lambda _s, err: closed.append(err))
    await s.handle_signal(
        ConnectError(777, ESessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED)
    )
    assert closed == []
    assert s.state is not SessionState.CLOSED
    await s.aclose()


async def test_listener_closes_on_other_error():
    sent: list[str] = []
    closed: list[ESessionError] = []
    s = make_listener(sent, on_close=lambda _s, err: closed.append(err))
    await s.handle_signal(ConnectError(777, ESessionError.ICE))
    assert closed == [ESessionError.ICE]
    assert s.state is SessionState.CLOSED
    await s.aclose()


async def test_listener_timeout_waiting_for_accept_closes_with_code_15():
    sent: list[str] = []
    closed: list[ESessionError] = []
    offer = await make_offer()
    s = make_listener(sent, on_close=lambda _s, err: closed.append(err), timeout=0.1)
    await s.handle_signal(ConnectRequest(777, offer))
    await asyncio.sleep(0.25)
    assert closed == [ESessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_ACCEPT]
    await s.aclose()


async def test_candidate_before_request_is_buffered():
    sent: list[str] = []
    s = make_listener(sent)
    try:
        await s.handle_signal(
            CandidateAdd(777, "candidate:1 1 udp 2130706431 10.0.0.5 5000 typ host")
        )
        assert s.state is SessionState.IDLE  # buffered, no error
    finally:
        await s.aclose()
