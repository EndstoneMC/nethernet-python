import asyncio

import pytest
from aiortc import RTCIceServer
from aiortc.rtcsctptransport import RTCSctpTransport

from nethernet.errors import ConnectionFailed, SendType, SessionError
from nethernet.transport import peer_connection as peer_connection_module
from nethernet.transport.framing import FRAGMENT_SIZE
from nethernet.transport.peer_connection import (
    MAX_ICE_SERVERS,
    MAX_MESSAGE_SIZE,
    PeerConnection,
    answer_max_message_size,
    candidate_from_message,
    candidate_to_message,
    ice_configuration,
    keep_only_relay_candidates,
    remote_max_message_size,
    set_max_message_size,
)

WEBRTC_BINARY = 53
OFFER_WITHOUT_ICE_CREDENTIALS = (
    "v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\n"
    "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\nc=IN IP4 0.0.0.0\r\n"
    "a=mid:0\r\na=sctp-port:5000\r\n"
)
HOST_CANDIDATE = "candidate:1 1 udp 2130706431 10.0.0.5 54321 typ host generation 0"


async def next_packet(packets):
    return await asyncio.wait_for(packets.get(), timeout=15)


def open_channel(pc, reliable=True):
    channel = (pc._reliable if reliable else pc._unreliable).channel
    channel._setReadyState("open")
    return channel


def sent_payloads(channel):
    queue = channel.transport._data_channel_queue
    return [item[2] for item in queue if item[0] is channel and item[1] == WEBRTC_BINARY]


def test_set_max_message_size_replaces_existing_value():
    sdp = "m=application 9 ...\r\na=sctp-port:5000\r\na=max-message-size:65536\r\n"
    out = set_max_message_size(sdp)
    assert "a=max-message-size:262144" in out
    assert "65536" not in out


def test_set_max_message_size_inserts_when_absent():
    sdp = "m=application 9 ...\r\na=sctp-port:5000\r\n"
    out = set_max_message_size(sdp)
    assert "a=max-message-size:262144\r\n" in out


def test_remote_max_message_size_is_read_from_the_sdp():
    assert remote_max_message_size("a=sctp-port:5000\r\na=max-message-size:65536\r\n") == 65536
    assert remote_max_message_size("a=sctp-port:5000\r\n") == 65536


def test_max_message_size_of_65536_omits_the_line():
    sdp = "a=sctp-port:5000\r\na=max-message-size:262144\r\n"
    assert set_max_message_size(sdp, 65536) == "a=sctp-port:5000\r\n"


def test_answer_advertises_the_smaller_of_the_offer_value_and_262144():
    assert answer_max_message_size(65536) == 65536
    assert answer_max_message_size(100000) == 100000
    assert answer_max_message_size(262144) == 262144
    assert answer_max_message_size(1000000) == 262144
    assert answer_max_message_size(0) == 262144
    assert answer_max_message_size(-1) == 262144


async def test_listener_answer_takes_its_max_message_size_from_the_offer():
    dialer = PeerConnection(is_dialer=True)
    try:
        offer = await dialer.create_offer()
        for offered, answered in ((100000, 100000), (1000000, 262144), (0, 262144)):
            listener = PeerConnection(is_dialer=False)
            try:
                answer = await listener.create_answer(set_max_message_size(offer, offered))
                assert remote_max_message_size(answer) == answered
            finally:
                await listener.close()
        listener = PeerConnection(is_dialer=False)
        try:
            answer = await listener.create_answer(set_max_message_size(offer, 65536))
            assert "a=max-message-size:" not in answer
        finally:
            await listener.close()
    finally:
        await dialer.close()


def test_no_ice_servers_passes_an_explicit_empty_list():
    assert ice_configuration(None).iceServers == []
    assert ice_configuration([]).iceServers == []


def test_ice_servers_are_capped_at_sixteen():
    servers = [RTCIceServer(f"turn:relay{i}.example:3478", "u", "p") for i in range(20)]
    assert MAX_ICE_SERVERS == 16
    assert ice_configuration(servers).iceServers == servers[:16]


def test_no_trickle_peer_clears_every_ice_server():
    servers = [RTCIceServer("turn:relay.example:3478", "u", "p")]
    assert ice_configuration(servers, trickle=False).iceServers == []


def test_candidate_message_roundtrip_handles_prefix():
    candidate = candidate_from_message(HOST_CANDIDATE)
    assert candidate.foundation == "1"
    assert candidate.sdpMid is None
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


async def test_offer_is_non_trickle_and_advertises_the_full_max_message_size():
    pc = PeerConnection(is_dialer=True)
    try:
        offer = await pc.create_offer()
        assert pc._pc.iceGatheringState == "complete"
        assert "a=candidate:" in offer
        assert remote_max_message_size(pc._pc.localDescription.sdp) < MAX_MESSAGE_SIZE
        assert remote_max_message_size(offer) == MAX_MESSAGE_SIZE
    finally:
        await pc.close()


async def test_remote_candidate_waits_until_both_descriptions_are_set():
    dialer = PeerConnection(is_dialer=True)
    listener = PeerConnection(is_dialer=False)
    try:
        await dialer.add_remote_candidate(HOST_CANDIDATE)
        assert len(dialer._pending_candidates) == 1
        offer = await dialer.create_offer()
        assert len(dialer._pending_candidates) == 1

        await listener.add_remote_candidate(HOST_CANDIDATE)
        answer = await listener.create_answer(offer)
        assert len(listener._pending_candidates) == 0

        await dialer.set_remote_answer(answer)
        assert len(dialer._pending_candidates) == 0
    finally:
        await dialer.close()
        await listener.close()


async def test_malformed_remote_candidate_is_dropped():
    pc = PeerConnection(is_dialer=True)
    try:
        await pc.add_remote_candidate("candidate:garbage")
        assert len(pc._pending_candidates) == 0
    finally:
        await pc.close()


async def test_offer_without_ice_credentials_fails_to_set_the_remote_description():
    pc = PeerConnection(is_dialer=False)
    try:
        with pytest.raises(ConnectionFailed) as info:
            await pc.create_answer(OFFER_WITHOUT_ICE_CREDENTIALS)
        assert info.value.error == SessionError.FAILED_TO_SET_REMOTE_DESCRIPTION
    finally:
        await pc.close()


async def test_answer_without_an_offer_fails_to_set_the_remote_description():
    dialer = PeerConnection(is_dialer=True)
    try:
        with pytest.raises(ConnectionFailed) as info:
            await dialer.set_remote_answer("not an sdp")
        assert info.value.error == SessionError.FAILED_TO_SET_REMOTE_DESCRIPTION
    finally:
        await dialer.close()


async def test_identity_hook_extends_the_offer():
    pc = PeerConnection(is_dialer=True)
    try:
        offer = await pc.create_offer(identity=lambda sdp: sdp + "a=identity:x\r\n")
        assert offer.endswith("a=identity:x\r\n")
    finally:
        await pc.close()


async def test_identity_failure_fails_to_create_the_identity_assertion():
    def fail(_sdp):
        raise ValueError("no key")

    pc = PeerConnection(is_dialer=True)
    try:
        with pytest.raises(ConnectionFailed) as info:
            await pc.create_offer(identity=fail)
        assert info.value.error == SessionError.FAILED_TO_CREATE_IDENTITY_ASSERTION
    finally:
        await pc.close()


async def test_fragment_sent_before_its_channel_opens_goes_out_in_order_once_it_opens():
    pc = PeerConnection(is_dialer=True)
    try:
        assert pc.send(b"one", SendType.RELIABLE) is True
        assert pc.send(b"two", SendType.RELIABLE) is True
        channel = pc._reliable.channel
        assert sent_payloads(channel) == []
        open_channel(pc)
        assert sent_payloads(channel) == [b"\x00one", b"\x00two"]
    finally:
        await pc.close()


async def test_full_send_queue_holds_fragments_until_the_buffered_amount_drops(monkeypatch):
    monkeypatch.setattr(peer_connection_module, "MAX_SEND_QUEUE_SIZE", 10)
    pc = PeerConnection(is_dialer=True)
    try:
        channel = open_channel(pc)
        assert pc.send(b"abcd", SendType.RELIABLE) is True
        assert pc.send(b"efgh", SendType.RELIABLE) is True
        assert pc.send(b"ij", SendType.RELIABLE) is True
        assert sent_payloads(channel) == [b"\x00abcd", b"\x00efgh"]
        assert channel.bufferedAmount == 10
        channel._addBufferedAmount(-5)
        assert sent_payloads(channel) == [b"\x00abcd", b"\x00efgh", b"\x00ij"]
    finally:
        await pc.close()


async def test_malformed_reliable_message_resets_the_reliable_stream_then_closes_the_transport():
    closes: list[SessionError] = []
    pc = PeerConnection(is_dialer=True, on_close=closes.append)
    reliable = open_channel(pc)
    unreliable = open_channel(pc, reliable=False)
    reliable.emit("message", b"AB")
    reliable.emit("message", b"\x00")
    assert reliable.readyState in ("closing", "closed")
    assert unreliable.readyState == "open"
    await pc._close_task
    assert closes == [SessionError.NONE]
    assert pc._pc.connectionState == "closed"
    assert pc.peek() is None


async def test_malformed_unreliable_message_resets_only_the_reliable_stream():
    closes: list[SessionError] = []
    pc = PeerConnection(is_dialer=True, on_close=closes.append)
    reliable = open_channel(pc)
    unreliable = open_channel(pc, reliable=False)
    unreliable.emit("message", b"")
    assert reliable.readyState in ("closing", "closed")
    assert unreliable.readyState == "open"
    await pc._close_task
    assert closes == [SessionError.NONE]
    assert pc._pc.connectionState == "closed"


async def test_channel_close_after_the_sctp_transport_closed_reports_data_channel_closed_once():
    closes: list[SessionError] = []
    pc = PeerConnection(is_dialer=True, on_close=closes.append)
    try:
        reliable = open_channel(pc)
        unreliable = open_channel(pc, reliable=False)
        reliable.emit("message", b"\x01AB")
        pc._pc.sctp._set_state(RTCSctpTransport.State.CLOSED)
        reliable._setReadyState("closed")
        unreliable._setReadyState("closed")
        assert closes == [SessionError.DATA_CHANNEL_CLOSED]
        assert pc.peek() is None
    finally:
        await pc.close()


async def test_closing_the_peer_connection_reports_none_once_and_refuses_sends():
    closes: list[SessionError] = []
    pc = PeerConnection(is_dialer=True, on_close=closes.append)
    open_channel(pc)
    await pc.close()
    assert closes == [SessionError.NONE]
    assert pc.send(b"late", SendType.RELIABLE) is False


async def connect_pair(rewrite_answer=None, **dialer_callbacks):
    dialer_open = asyncio.Event()
    listener_open = asyncio.Event()
    dialer_recv: asyncio.Queue[bytes] = asyncio.Queue()
    listener_recv: asyncio.Queue[bytes] = asyncio.Queue()
    dialer = PeerConnection(
        is_dialer=True,
        on_packet=dialer_recv.put_nowait,
        on_channels_open=dialer_open.set,
        **dialer_callbacks,
    )
    listener = PeerConnection(
        is_dialer=False, on_packet=listener_recv.put_nowait, on_channels_open=listener_open.set
    )
    offer = await dialer.create_offer()
    assert "a=max-message-size:262144" in offer
    answer = await listener.create_answer(offer)
    if rewrite_answer is not None:
        answer = rewrite_answer(answer)
    await dialer.set_remote_answer(answer)
    return dialer, listener, dialer_open, listener_open, dialer_recv, listener_recv


async def test_in_process_connect_and_exchange():
    connected = asyncio.Event()
    sizes: list[int] = []
    dialer, listener, dialer_open, listener_open, dialer_recv, listener_recv = await connect_pair(
        on_ice_connected=connected.set, on_fragment_size_change=sizes.append
    )
    try:
        await asyncio.wait_for(
            asyncio.gather(connected.wait(), dialer_open.wait(), listener_open.wait()), timeout=20
        )
        assert dialer.ice_connection_state == "completed"
        for pc in (dialer, listener):
            assert pc._reliable.channel.label == "ReliableDataChannel"
            assert pc._reliable.channel.ordered is True
            assert pc._unreliable.channel.label == "UnreliableDataChannel"
            assert pc._unreliable.channel.ordered is False
            assert pc._unreliable.channel.maxRetransmits == 0
        assert dialer.fragment_size == FRAGMENT_SIZE
        assert sizes == []

        dialer.send(b"hello", SendType.RELIABLE)
        assert await next_packet(listener_recv) == b"hello"

        big = bytes(i % 256 for i in range(FRAGMENT_SIZE + 1))
        dialer.send(big, SendType.RELIABLE)
        assert await next_packet(listener_recv) == big

        listener.send(b"pong", SendType.UNRELIABLE)
        assert await next_packet(dialer_recv) == b"pong"
    finally:
        await dialer.close()
        await listener.close()


async def test_ice_connected_takes_the_fragment_size_from_the_remote_max_message_size():
    connected = asyncio.Event()
    sizes: list[int] = []
    dialer, listener, *_ = await connect_pair(
        lambda sdp: set_max_message_size(sdp, 65536),
        on_ice_connected=connected.set,
        on_fragment_size_change=sizes.append,
    )
    try:
        await asyncio.wait_for(connected.wait(), timeout=20)
        assert sizes == [65535]
        assert dialer.fragment_size == 65535
        assert dialer.max_packet_size == 255 * 65535
    finally:
        await dialer.close()
        await listener.close()


async def test_ice_connected_with_a_max_message_size_of_one_closes_with_invalid_argument():
    closed = asyncio.Event()
    closes: list[SessionError] = []
    connected: list[bool] = []

    def on_close(error):
        closes.append(error)
        closed.set()

    dialer, listener, *_ = await connect_pair(
        lambda sdp: set_max_message_size(sdp, 1),
        on_ice_connected=lambda: connected.append(True),
        on_close=on_close,
    )
    try:
        await asyncio.wait_for(closed.wait(), timeout=20)
        assert closes == [SessionError.INVALID_ARGUMENT]
        assert connected == []
        assert dialer.fragment_size == FRAGMENT_SIZE
    finally:
        await dialer.close()
        await listener.close()


async def test_candidate_pair_is_none_until_a_pair_is_selected():
    dialer, listener, dialer_open, listener_open, *_ = await connect_pair()
    try:
        assert dialer.candidate_pair() is None
        await asyncio.wait_for(asyncio.gather(dialer_open.wait(), listener_open.wait()), timeout=20)
        pair = dialer.candidate_pair()
        assert pair is not None
        assert pair["local"].startswith("candidate:") and " typ " in pair["local"]
        assert pair["remote"].startswith("candidate:") and " typ " in pair["remote"]
        assert pair["state"] == "succeeded"
        assert set(pair) == {"local", "remote", "state", "rtt", "responses_received"}
    finally:
        await dialer.close()
        await listener.close()


async def connect_and_record_close():
    closed = asyncio.Event()
    closes: list[SessionError] = []

    def on_close(error):
        closes.append(error)
        closed.set()

    dialer, listener, dialer_open, listener_open, *_ = await connect_pair(on_close=on_close)
    await asyncio.wait_for(asyncio.gather(dialer_open.wait(), listener_open.wait()), timeout=20)
    return dialer, listener, closed, closes


async def test_remote_stream_reset_closes_with_no_error():
    dialer, listener, closed, closes = await connect_and_record_close()
    try:
        listener._reliable.channel.close()
        await asyncio.wait_for(closed.wait(), timeout=15)
        assert closes == [SessionError.NONE]
        assert dialer._pc.sctp.state == "connected"
    finally:
        await dialer.close()
        await listener.close()


async def test_remote_sctp_abort_closes_with_data_channel_closed():
    dialer, listener, closed, closes = await connect_and_record_close()
    try:
        await listener._pc.sctp.stop()
        await asyncio.wait_for(closed.wait(), timeout=15)
        assert closes == [SessionError.DATA_CHANNEL_CLOSED]
    finally:
        await dialer.close()


async def test_clean_close_reports_none_locally_and_data_channel_closed_remotely():
    local: list[SessionError] = []
    dialer, listener, closed, closes = await connect_and_record_close()
    listener._on_close = local.append
    try:
        dialer.send(b"hello", SendType.RELIABLE)
        await asyncio.sleep(0.2)
        await listener.close()
        assert local == [SessionError.NONE]
        await asyncio.wait_for(closed.wait(), timeout=5)
        assert closes == [SessionError.DATA_CHANNEL_CLOSED]
    finally:
        await dialer.close()


async def test_malformed_message_resets_the_stream_and_the_remote_sees_no_error():
    dialer, listener, closed, closes = await connect_and_record_close()
    try:
        listener._reliable.channel.emit("message", b"\x00")
        assert listener._reliable.channel.readyState in ("closing", "closed")
        await asyncio.wait_for(closed.wait(), timeout=5)
        assert closes == [SessionError.NONE]
    finally:
        await dialer.close()
        await listener.close()


async def test_first_close_reason_wins():
    closes: list[SessionError] = []
    pc = PeerConnection(is_dialer=True, on_close=closes.append)
    open_channel(pc)
    pc._pc.sctp._set_state(RTCSctpTransport.State.CLOSED)
    pc._handle_channel_close(pc._reliable)
    await pc.close()
    assert closes == [SessionError.DATA_CHANNEL_CLOSED]


async def test_channels_opening_after_close_fire_no_open_callback():
    opened: list[bool] = []
    pc = PeerConnection(is_dialer=True, on_channels_open=lambda: opened.append(True))
    await pc.close()
    open_channel(pc)
    open_channel(pc, reliable=False)
    assert opened == []


async def test_close_closes_the_peer_connection_when_the_close_callback_raises():
    def on_close(error):
        raise RuntimeError

    pc = PeerConnection(is_dialer=True, on_close=on_close)
    with pytest.raises(RuntimeError):
        await pc.close()
    assert pc._closed is True
    assert pc.connection_state == "closed"


async def test_second_close_returns_after_the_first_was_cancelled():
    dialer, listener, *_ = await connect_pair()
    try:
        task = asyncio.ensure_future(listener.close())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(listener.close(), timeout=5)
        assert listener.connection_state == "closed"
    finally:
        await dialer.close()


async def test_second_malformed_message_during_the_reset_wait_starts_no_second_close():
    pc = PeerConnection(is_dialer=True)
    reliable = open_channel(pc)
    reliable.emit("message", b"\x00")
    task = pc._close_task
    reliable.emit("message", b"\x00")
    open_channel(pc, reliable=False).emit("message", b"")
    assert pc._close_task is task
    await task


async def test_peer_abort_during_the_reset_wait_keeps_none():
    closes: list[SessionError] = []
    pc = PeerConnection(is_dialer=True, on_close=closes.append)
    open_channel(pc).emit("message", b"\x00")
    pc._pc.sctp._set_state(RTCSctpTransport.State.CLOSED)
    pc._handle_channel_close(pc._reliable)
    await pc._close_task
    assert closes == [SessionError.NONE]
