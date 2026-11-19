import asyncio
import json

from aioice.ice import CandidatePair
from session_doubles import (
    ANSWER,
    HOST_CANDIDATE,
    LOCAL,
    OFFER,
    REMOTE,
    REMOTE_CANDIDATE,
    SRFLX_CANDIDATE,
    Channels,
    Recorder,
    VirtualClock,
    failing,
    make_manager,
    settle,
)

from nethernet.errors import SendType, SessionError
from nethernet.session import SessionState, anonymize_ip
from nethernet.signaling.messages import (
    CandidateAdd,
    ConnectError,
    ConnectRequest,
    ConnectResponse,
)

CID = 4242


async def dial(manager, **kwargs):
    session = manager.dial(REMOTE, connection_id=CID, **kwargs)
    await settle()
    return session


async def answer(manager, sdp=ANSWER, *, via_lan=True):
    await manager.handle_signal(REMOTE, ConnectResponse(CID, sdp).serialize(), via_lan=via_lan)


async def test_dialer_sends_stripped_offer_then_each_candidate_over_lan_when_trickle():
    channels = Channels()
    manager = make_manager(channels, Recorder())
    await dial(manager)
    messages = channels.lan_messages()
    assert isinstance(messages[0], ConnectRequest)
    assert messages[0].connection_id == CID
    assert "a=candidate" not in messages[0].sdp
    assert "a=end-of-candidates" not in messages[0].sdp
    assert messages[1:] == [CandidateAdd(CID, HOST_CANDIDATE), CandidateAdd(CID, SRFLX_CANDIDATE)]
    await manager.aclose()


async def test_non_trickle_dialer_sends_one_full_sdp_and_no_candidate_add():
    channels = Channels()
    manager = make_manager(channels, Recorder(), trickle=False)
    session = await dial(manager)
    assert channels.lan_messages() == [ConnectRequest(CID, OFFER)]
    assert session.peer_connection.kwargs["trickle"] is False
    await manager.aclose()


async def test_dialer_over_http_sends_the_full_sdp_with_candidates_inline():
    channels = Channels()
    manager = make_manager(channels, Recorder(), lan=False, http=True)
    await dial(manager)
    await settle()
    assert channels.http_messages() == [ConnectRequest(CID, OFFER)]
    assert channels.lan == []
    await manager.aclose()


async def test_dialer_identity_generator_adds_the_identity_line_to_the_offer():
    channels = Channels()
    manager = make_manager(channels, Recorder(), lan=False, http=True)
    await dial(manager, identity=lambda sdp: sdp + "a=identity:abc\r\n")
    await settle()
    assert channels.http_messages()[0].sdp.endswith("a=identity:abc\r\n")
    await manager.aclose()


async def test_accepted_answer_is_set_then_buffered_candidates_are_added():
    channels = Channels()
    manager = make_manager(channels, Recorder())
    session = await dial(manager)
    await manager.handle_signal(REMOTE, CandidateAdd(CID, REMOTE_CANDIDATE).serialize())
    assert session.peer_connection.rtc.applied == []
    await answer(manager)
    pc = session.peer_connection
    assert pc.remote_sdp == ANSWER
    assert pc.rtc.events == ["local", "remote", f"candidate {REMOTE_CANDIDATE}"]
    assert session.deadline is None
    await manager.aclose()


async def test_answer_identity_is_kept_and_stripped_before_webrtc():
    channels = Channels()
    manager = make_manager(channels, Recorder())
    session = await dial(manager)
    await answer(manager, ANSWER + "a=identity:xyz\r\n")
    assert session.remote_identity == "xyz"
    assert "a=identity" not in session.peer_connection.remote_sdp
    await manager.aclose()


async def test_session_opens_once_when_ice_connects_not_when_channels_open():
    channels = Channels()
    recorder = Recorder()
    manager = make_manager(channels, recorder)
    session = await dial(manager)
    await answer(manager)
    session.peer_connection.drive_ice("checking")
    assert recorder.opened == []
    session.peer_connection.drive_ice("completed")
    session.peer_connection.drive_ice("completed")
    assert recorder.opened == [session]
    await manager.aclose()


async def test_routing_keys_sessions_by_remote_then_connection_id():
    channels = Channels()
    manager = make_manager(channels, Recorder())
    first = await dial(manager)
    second = manager.dial(LOCAL, connection_id=CID)
    assert manager.get(REMOTE, CID) is first
    assert manager.get(LOCAL, CID) is second
    await manager.aclose()


async def test_connect_error_closes_the_dialer_with_the_carried_value_whatever_it_is():
    for code in (SessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED, SessionError(99)):
        channels = Channels()
        recorder = Recorder()
        manager = make_manager(channels, recorder)
        await dial(manager)
        await manager.handle_signal(REMOTE, ConnectError(CID, code).serialize())
        assert recorder.errors() == [code]
        assert manager.get(REMOTE, CID) is None
        await manager.aclose()


async def test_connect_response_for_an_unknown_pair_is_decided_then_dropped():
    decided = []
    manager = make_manager(Channels(), Recorder(), accept=lambda request: decided.append(request))
    await answer(manager)
    assert [request.connection_id for request in decided] == [CID]
    assert manager.sessions == []
    await manager.aclose()


async def test_answer_whose_accept_hook_raises_is_dropped():
    def accept(request):
        raise RuntimeError("hook")

    recorder = Recorder()
    manager = make_manager(Channels(), recorder, accept=accept)
    session = await dial(manager)
    await answer(manager)
    assert recorder.closed == []
    assert session.peer_connection.remote_sdp is None
    await manager.aclose()


async def test_accept_is_given_remote_id_identity_fingerprints_and_channel():
    decided = []

    async def accept(request):
        decided.append(request)
        return True

    manager = make_manager(Channels(), Recorder(), accept=accept)
    await dial(manager)
    await answer(manager, ANSWER + "a=identity:xyz\r\n", via_lan=False)
    request = decided[0]
    assert request.remote_id == REMOTE
    assert request.connection_id == CID
    assert request.is_offer is False
    assert request.identity == "xyz"
    assert request.fingerprints == [("sha-256", "AA:BB:CC")]
    assert request.via_lan is False
    await manager.aclose()


async def test_declined_answer_closes_the_dialer_with_37():
    recorder = Recorder()
    manager = make_manager(Channels(), recorder, accept=lambda request: False)
    session = await dial(manager)
    await answer(manager)
    assert recorder.errors() == [SessionError.IDENTITY_NOT_ALLOWED]
    assert session.peer_connection.remote_sdp is None
    await manager.aclose()


async def test_undecided_answer_leaves_the_dialer_to_its_own_negotiation_timeout():
    clock = VirtualClock()
    recorder = Recorder()
    pending = []

    def accept(request):
        future = asyncio.get_running_loop().create_future()
        pending.append(future)
        return future

    manager = make_manager(Channels(), recorder, accept=accept)
    await dial(manager)
    task = asyncio.ensure_future(answer(manager))
    await clock.advance(10)
    assert recorder.errors() == [SessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE]
    task.cancel()
    await manager.aclose()


async def test_failed_http_send_closes_the_dialer_with_the_reported_error_19_included():
    channels = Channels(http_errors=[SessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED])
    recorder = Recorder()
    manager = make_manager(channels, recorder, lan=False, http=True)
    await dial(manager)
    await settle()
    assert recorder.errors() == [SessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED]
    await manager.aclose()


async def test_no_usable_signaling_channel_closes_with_24():
    recorder = Recorder()
    manager = make_manager(Channels(), recorder, lan=False, http=False)
    await dial(manager)
    assert recorder.errors() == [SessionError.NO_SIGNALING_CHANNEL]
    await manager.aclose()


async def test_dialer_prefers_http_when_both_channels_are_set():
    channels = Channels()
    manager = make_manager(channels, Recorder(), lan=True, http=True)
    await dial(manager)
    await settle()
    assert channels.lan == []
    assert channels.http_messages() == [ConnectRequest(CID, OFFER)]
    await manager.aclose()


async def test_lan_needs_lan_signaling_on_and_the_remote_on_the_lan():
    for kwargs in ({"lan_signaling": False}, {"is_lan_peer": lambda remote: False}):
        recorder = Recorder()
        manager = make_manager(Channels(), recorder, **kwargs)
        await dial(manager)
        assert recorder.errors() == [SessionError.NO_SIGNALING_CHANNEL]
        await manager.aclose()


async def test_negotiation_timeout_closes_the_dialer_with_14_on_the_next_sweep():
    clock = VirtualClock()
    recorder = Recorder()
    manager = make_manager(Channels(), recorder, negotiation_timeout=7)
    await dial(manager)
    await clock.advance(9.75)
    assert recorder.errors() == []
    await clock.advance(0.25)
    assert recorder.errors() == [SessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE]
    await manager.aclose()


async def test_negotiation_timeout_defaults_to_10_seconds():
    clock = VirtualClock()
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    await dial(manager)
    await clock.advance(9.75)
    assert recorder.errors() == []
    await clock.advance(0.25)
    assert recorder.errors() == [SessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE]
    await manager.aclose()


async def test_negotiation_timeout_stops_once_the_answer_is_set():
    clock = VirtualClock()
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    await dial(manager)
    await clock.advance(3)
    await answer(manager)
    await clock.advance(60)
    assert recorder.errors() == []
    await manager.aclose()


async def test_negative_negotiation_timeout_counts_as_zero():
    clock = VirtualClock()
    recorder = Recorder()
    manager = make_manager(Channels(), recorder, negotiation_timeout=-3)
    await dial(manager)
    await clock.advance(4.75)
    assert recorder.errors() == []
    await clock.advance(0.25)
    assert recorder.errors() == [SessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE]
    await manager.aclose()


async def test_sweep_closes_a_session_whose_ice_is_closed_with_5():
    clock = VirtualClock()
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    session = await dial(manager)
    await answer(manager)
    session.peer_connection.set_ice_state("closed")
    await clock.advance(5)
    assert recorder.errors() == [SessionError.ICE]
    await manager.aclose()


async def test_sweep_is_timed_from_the_end_of_the_previous_pass():
    clock = VirtualClock()
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    session = await dial(manager)
    await answer(manager)
    await clock.advance(5)
    session.peer_connection.set_ice_state("failed")
    await clock.advance(4.75)
    assert recorder.errors() == []
    await clock.advance(0.25)
    assert recorder.errors() == [SessionError.ICE]
    await manager.aclose()


async def test_disconnected_ice_never_closes_the_session():
    clock = VirtualClock()
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    session = await dial(manager)
    await answer(manager)
    session.peer_connection.drive_ice("completed")
    session.peer_connection.set_ice_state("disconnected")
    await clock.advance(30)
    assert recorder.errors() == []
    await manager.aclose()


async def test_close_reasons_for_failed_negotiation_steps():
    cases = [
        ({"create": SessionError.FAILED_TO_CREATE_PEER_CONNECTION}, False),
        ({"offer": SessionError.FAILED_TO_CREATE_OFFER}, False),
        ({"remote": SessionError.FAILED_TO_SET_REMOTE_DESCRIPTION}, True),
    ]
    for fail_on, answered in cases:
        recorder = Recorder()
        manager = make_manager(Channels(), recorder, peer_connection_factory=failing(**fail_on))
        await dial(manager)
        if answered:
            await answer(manager)
        assert recorder.errors() == list(fail_on.values())
        await manager.aclose()


async def test_ice_failure_closes_with_5_at_once():
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    session = await dial(manager)
    session.peer_connection.drive_ice("failed")
    assert recorder.errors() == [SessionError.ICE]
    await manager.aclose()


async def test_close_is_reported_once_with_a_json_summary_and_closes_the_peer_connection():
    recorder = Recorder()
    channels = Channels()
    manager = make_manager(channels, recorder)
    session = await dial(manager)
    sent = len(channels.lan)
    assert manager.close(REMOTE, CID) is True
    await settle()
    assert manager.close(REMOTE, CID) is False
    assert recorder.errors() == [None]
    assert isinstance(json.loads(recorder.closed[0][2]), dict)
    assert session.peer_connection.rtc.closed
    assert len(channels.lan) == sent
    await manager.aclose()


async def test_close_of_an_unknown_session_returns_false_with_no_report():
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    assert manager.close(REMOTE, 1) is False
    assert recorder.closed == []
    await manager.aclose()


async def test_closing_sends_no_connect_error():
    channels = Channels()
    manager = make_manager(channels, Recorder())
    await dial(manager)
    await manager.handle_signal(REMOTE, ConnectError(CID, SessionError.ICE).serialize())
    manager.dial(REMOTE, connection_id=7)
    manager.close(REMOTE, 7)
    await settle()
    assert not any(isinstance(message, ConnectError) for message in channels.lan_messages())
    await manager.aclose()


async def test_send_while_connecting_is_queued_and_returns_true():
    manager = make_manager(Channels(), Recorder())
    await dial(manager)
    assert manager.send(REMOTE, CID, b"hello", SendType.RELIABLE) is True
    await manager.aclose()


async def test_send_to_an_unknown_or_removed_session_returns_false():
    manager = make_manager(Channels(), Recorder())
    await dial(manager)
    assert manager.send(REMOTE, CID + 1, b"x") is False
    manager.close(REMOTE, CID)
    assert manager.send(REMOTE, CID, b"x") is False
    assert manager.read(REMOTE, CID) is None
    assert manager.peek(REMOTE, CID) is None
    assert manager.state(REMOTE, CID) is None
    await manager.aclose()


async def test_dropped_packet_returns_false():
    manager = make_manager(Channels(), Recorder())
    await dial(manager)
    assert manager.send(REMOTE, CID, b"", SendType.RELIABLE) is False
    assert manager.send(REMOTE, CID, b"x" * 300000, SendType.UNRELIABLE) is False
    await manager.aclose()


async def test_state_query_for_the_local_network_id_returns_false():
    manager = make_manager(Channels(), Recorder())
    manager.dial(LOCAL, connection_id=CID)
    assert manager.state(LOCAL, CID) is None
    await manager.aclose()


async def test_state_follows_the_ice_state():
    manager = make_manager(Channels(), Recorder())
    session = await dial(manager)
    pc = session.peer_connection
    expected = {
        "new": SessionState.CONNECTING,
        "checking": SessionState.CONNECTING,
        "connected": SessionState.ACTIVE,
        "completed": SessionState.ACTIVE,
        "disconnected": SessionState.ACTIVE,
        "failed": SessionState.ERRORED,
        "closed": SessionState.ERRORED,
    }
    for ice_state, state in expected.items():
        pc.set_ice_state(ice_state)
        status = manager.state(REMOTE, CID)
        assert (status.state, status.ice_state) == (state, ice_state)
    await manager.aclose()


async def test_state_reports_zeros_before_the_first_snapshot_then_refreshes_after_2_seconds():
    clock = VirtualClock()
    manager = make_manager(Channels(), Recorder())
    session = await dial(manager)
    await clock.advance(1)
    manager.send(REMOTE, CID, b"x" * 100)
    first = manager.state(REMOTE, CID)
    assert (first.bytes_sent, first.packets_sent, first.rtt_ms) == (0, 0, None)
    second = manager.state(REMOTE, CID)
    assert (second.bytes_sent, second.packets_sent) == (100, 1)
    manager.send(REMOTE, CID, b"x" * 50)
    await clock.advance(2)
    assert manager.state(REMOTE, CID).bytes_sent == 100
    await clock.advance(0.25)
    manager.state(REMOTE, CID)
    assert manager.state(REMOTE, CID).bytes_sent == 150
    assert session.status().send_rate == 50 // 2
    await manager.aclose()


async def test_rates_divide_by_whole_seconds_and_keep_mean_and_maximum():
    clock = VirtualClock()
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    session = await dial(manager)
    manager.send(REMOTE, CID, b"x" * 300)
    await clock.advance(1.75)
    session.status()
    assert session.status().send_rate == 300
    manager.send(REMOTE, CID, b"x" * 101)
    await clock.advance(2.75)
    session.status()
    assert session.status().send_rate == 50
    manager.close(REMOTE, CID)
    rate = json.loads(recorder.closed[0][2])["sendRate"]
    assert rate == {"current": 50, "average": 175.0, "maximum": 300}
    await manager.aclose()


async def test_snapshot_is_taken_at_most_once_a_second():
    clock = VirtualClock()
    manager = make_manager(Channels(), Recorder())
    session = await dial(manager)
    await clock.advance(1)
    session.status()
    manager.send(REMOTE, CID, b"x" * 10)
    await clock.advance(0.5)
    session.close()
    assert session.status().bytes_sent == 0
    await manager.aclose()


async def test_candidates_are_recorded_on_first_sighting_with_anonymized_ips():
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    await dial(manager)
    await manager.handle_signal(REMOTE, CandidateAdd(CID, REMOTE_CANDIDATE).serialize())
    await manager.handle_signal(
        REMOTE, CandidateAdd(CID, REMOTE_CANDIDATE + " generation 1").serialize()
    )
    manager.close(REMOTE, CID)
    summary = json.loads(recorder.closed[0][2])
    assert [c["ip"] for c in summary["localCandidates"]] == ["192.168.1.0", "203.0.113.0"]
    assert [c["type"] for c in summary["localCandidates"]] == ["host", "srflx"]
    assert summary["remoteCandidates"] == [{"type": "host", "ip": "10.0.0.0", "url": ""}]
    await manager.aclose()


def test_ipv6_keeps_at_most_its_first_three_groups():
    assert anonymize_ip("2001:db8:85a3:1:2:8a2e:370:7334") == "2001:db8:85a3::"
    assert anonymize_ip("2001:db8::1") == "2001:db8::"
    assert anonymize_ip("fe80::1") == "fe80::"
    assert anonymize_ip("10.1.2.3") == "10.1.2.0"


async def test_pair_states_grow_on_change_and_rtt_is_none_without_stun_timing():
    clock = VirtualClock()
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    session = await dial(manager)
    pc = session.peer_connection
    relay = "candidate:5 1 udp 100 198.51.100.4 3478 typ relay"
    states = (
        CandidatePair.State.IN_PROGRESS,
        CandidatePair.State.IN_PROGRESS,
        CandidatePair.State.SUCCEEDED,
    )
    for state in states:
        pc.nominate(HOST_CANDIDATE, relay, state)
        await clock.advance(2.25)
        session.status()
    status = session.status()
    assert status.relay is True
    assert status.rtt_ms is None
    manager.close(REMOTE, CID)
    summary = json.loads(recorder.closed[0][2])
    assert summary["candidatePair"]["states"] == ["in_progress", "succeeded"]
    assert (summary["rtt"], summary["avgrtt"], summary["responsesReceived"]) == (None, None, None)
    await manager.aclose()


async def test_session_manager_is_an_async_context_manager():
    recorder = Recorder()
    async with make_manager(Channels(), recorder) as manager:
        await dial(manager)
    assert recorder.errors() == [None]
