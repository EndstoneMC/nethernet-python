"""aiortc PeerConnection wrapper — SPEC.md s6.1-s6.2, s4.

The connect-and-exchange test runs a real loopback ICE/DTLS/SCTP handshake (a *medium* test).
"""

import asyncio

from nethernet.errors import SendType
from nethernet.transport.framing import FRAGMENT_SIZE
from nethernet.transport.peer_connection import (
    MAX_MESSAGE_SIZE,
    PeerConnection,
    candidate_from_message,
    candidate_to_message,
    keep_only_relay_candidates,
    set_max_message_size,
)


async def wait_for(predicate, timeout=15.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise TimeoutError("condition not met in time")


# --- SDP munging (SPEC.md s6.1) ---


def test_set_max_message_size_replaces_existing_value():
    sdp = "m=application 9 ...\r\na=sctp-port:5000\r\na=max-message-size:65536\r\n"
    out = set_max_message_size(sdp)
    assert "a=max-message-size:262144" in out
    assert "65536" not in out


def test_set_max_message_size_inserts_when_absent():
    sdp = "m=application 9 ...\r\na=sctp-port:5000\r\n"
    out = set_max_message_size(sdp)
    assert "a=max-message-size:262144\r\n" in out


def test_max_message_size_constant_matches_spec():
    assert MAX_MESSAGE_SIZE == 262144


# --- Candidate translation (SPEC.md s5.4 / s12) ---


def test_candidate_message_roundtrip_handles_prefix():
    text = "candidate:1 1 udp 2130706431 10.0.0.5 54321 typ host generation 0"
    candidate = candidate_from_message(text)
    # candidate_from_sdp must see no prefix -> foundation is "1", not "candidate:1".
    assert candidate.foundation == "1"
    assert candidate.sdpMLineIndex == 0
    again = candidate_to_message(candidate)
    assert again.startswith("candidate:")
    assert "typ host" in again


def test_keep_only_relay_candidates_drops_host():
    sdp = (
        "a=candidate:1 1 udp 2130706431 10.0.0.5 5000 typ host\r\n"
        "a=candidate:2 1 udp 1686052607 1.2.3.4 5000 typ relay\r\n"
        "a=sctp-port:5000\r\n"
    )
    out = keep_only_relay_candidates(sdp)
    assert "typ host" not in out
    assert "typ relay" in out
    assert "a=sctp-port:5000" in out


async def test_relay_only_offer_advertises_no_host_candidates():
    pc = PeerConnection(is_dialer=True, relay_only=True)
    try:
        offer = await pc.create_offer()
        assert "typ host" not in offer
    finally:
        await pc.close()


# --- End-to-end in-process connect + exchange (SPEC.md s6) ---


async def test_in_process_connect_and_exchange():
    dialer_open = asyncio.Event()
    listener_open = asyncio.Event()
    dialer_recv: list[bytes] = []
    listener_recv: list[bytes] = []

    dialer = PeerConnection(
        is_dialer=True, on_packet=dialer_recv.append, on_channels_open=dialer_open.set
    )
    listener = PeerConnection(
        is_dialer=False, on_packet=listener_recv.append, on_channels_open=listener_open.set
    )
    try:
        offer = await dialer.create_offer()
        assert "a=max-message-size:262144" in offer
        answer = await listener.create_answer(offer)
        await dialer.set_remote_answer(answer)

        await asyncio.wait_for(
            asyncio.gather(dialer_open.wait(), listener_open.wait()), timeout=20
        )

        # Reliable single-fragment, dialer -> listener.
        dialer.send(b"hello", SendType.RELIABLE)
        await wait_for(lambda: listener_recv == [b"hello"])

        # Reliable multi-fragment (262144 bytes) must reassemble intact (the reliable/ordered path).
        big = bytes(i % 256 for i in range(FRAGMENT_SIZE + 1))
        dialer.send(big, SendType.RELIABLE)
        await wait_for(lambda: len(listener_recv) == 2)
        assert listener_recv[1] == big

        # Unreliable single-fragment, listener -> dialer (selects the unreliable channel by label).
        listener.send(b"pong", SendType.UNRELIABLE)
        await wait_for(lambda: dialer_recv == [b"pong"])
    finally:
        await dialer.close()
        await listener.close()
