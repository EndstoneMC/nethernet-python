"""Dialer session state machine — SPEC.md s9.1, s9.4-s9.5.

These cover the dialer's signaling behavior (offer emission, error/timeout close, candidate
buffering) without completing ICE; the full dialer<->listener connect is in test_session_loopback.
"""

import asyncio

from nethernet.errors import ESendType, ESessionError
from nethernet.network_id import NetworkID
from nethernet.session import Session, SessionState
from nethernet.signaling.messages import CandidateAdd, ConnectError, ConnectRequest, parse


def parsed_messages(sent):
    return [parse(s) for s in sent]


def make_dialer(sent, *, on_open=None, on_close=None, timeout=10.0):
    return Session(
        connection_id=4242,
        local_id=NetworkID.p2p(1),
        remote_id=NetworkID.p2p(2),
        is_dialer=True,
        send_signal=sent.append,
        on_open=on_open,
        on_close=on_close,
        negotiation_timeout=timeout,
    )


async def test_start_sends_connect_request_with_offer():
    sent: list[str] = []
    s = make_dialer(sent)
    try:
        await s.start()
        messages = parsed_messages(sent)
        # First message is the offer; any following messages are trickled candidates.
        assert isinstance(messages[0], ConnectRequest)
        assert messages[0].connection_id == 4242
        assert "v=0" in messages[0].sdp  # a real SDP offer
        assert "a=candidate" not in messages[0].sdp  # candidates are trickled separately
        assert all(isinstance(m, CandidateAdd) for m in messages[1:])
        assert s.state == SessionState.OFFER_SENT
    finally:
        await s.aclose()


async def test_connect_error_closes_with_that_error():
    sent: list[str] = []
    closed: list[ESessionError] = []
    s = make_dialer(sent, on_close=lambda _s, err: closed.append(err))
    await s.start()
    await s.handle_signal(ConnectError(4242, ESessionError.NEGOTIATION_TIMEOUT))
    assert closed == [ESessionError.NEGOTIATION_TIMEOUT]
    assert s.state == SessionState.CLOSED
    await s.aclose()


async def test_dialer_acts_on_unicast_delivery_failed_error():
    # SPEC.md s5.5: error 19 closes outgoing (dialer) sessions (only incoming sessions ignore it).
    sent: list[str] = []
    closed: list[ESessionError] = []
    s = make_dialer(sent, on_close=lambda _s, err: closed.append(err))
    await s.start()
    await s.handle_signal(
        ConnectError(4242, ESessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED)
    )
    assert closed == [ESessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED]
    await s.aclose()


async def test_timeout_waiting_for_response_closes_with_code_14():
    sent: list[str] = []
    closed: list[ESessionError] = []
    s = make_dialer(sent, on_close=lambda _s, err: closed.append(err), timeout=0.1)
    await s.start()
    await asyncio.sleep(0.25)
    assert closed == [ESessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE]
    assert s.state == SessionState.CLOSED
    await s.aclose()


async def test_candidate_before_remote_description_is_buffered():
    sent: list[str] = []
    s = make_dialer(sent)
    try:
        await s.start()
        await s.handle_signal(
            CandidateAdd(4242, "candidate:1 1 udp 2130706431 10.0.0.5 5000 typ host")
        )
        assert s.state == SessionState.OFFER_SENT  # still waiting, no error
    finally:
        await s.aclose()


async def test_send_is_rejected_before_connected():
    sent: list[str] = []
    s = make_dialer(sent)
    try:
        await s.start()
        assert s.send(b"data", ESendType.RELIABLE) is False  # not CONNECTED yet
    finally:
        await s.aclose()
