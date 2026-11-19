import asyncio

from session_doubles import (
    ANSWER,
    HOST_CANDIDATE,
    OFFER,
    OTHER,
    REMOTE,
    REMOTE_CANDIDATE,
    Channels,
    Recorder,
    VirtualClock,
    failing,
    make_manager,
    settle,
)

from nethernet.errors import ConnectionFlags, SessionError
from nethernet.session import Route, SessionState, SignalingChannels
from nethernet.signaling.messages import (
    CandidateAdd,
    ConnectError,
    ConnectRequest,
    ConnectResponse,
)

CID = 777


def sign(sdp):
    return sdp + "a=identity:operator\r\n"


async def offer(manager, sdp=OFFER, *, sender=REMOTE, via_lan=False, cid=CID):
    await manager.handle_signal(sender, ConnectRequest(cid, sdp).serialize(), via_lan=via_lan)
    await settle()


async def test_http_offer_creates_a_listener_that_answers_on_the_same_id_with_full_sdp():
    channels = Channels()
    manager = make_manager(channels, Recorder(), http=True, identity=sign)
    await offer(manager, OFFER + "a=identity:player\r\n")
    session = manager.get(REMOTE, CID)
    assert session is not None and not session.is_dialer
    assert session.remote_identity == "player"
    assert "a=identity" not in session.peer_connection.remote_sdp
    assert channels.http_messages() == [ConnectResponse(CID, sign(ANSWER))]
    assert channels.lan == []
    await manager.aclose()


async def test_lan_offer_gets_no_identity_and_one_full_answer_over_lan():
    channels = Channels()
    manager = make_manager(channels, Recorder(), http=True, identity=sign)
    await offer(manager, via_lan=True)
    session = manager.get(REMOTE, CID)
    assert channels.lan_messages() == [ConnectResponse(CID, ANSWER)]
    assert channels.http == []
    assert session.peer_connection.kwargs["trickle"] is False
    await manager.aclose()


async def test_listener_adds_buffered_candidates_before_it_sends_the_answer():
    channels = Channels()
    manager = make_manager(channels, Recorder())
    await manager.handle_signal(REMOTE, CandidateAdd(CID, REMOTE_CANDIDATE).serialize())
    await offer(manager)
    pc = manager.get(REMOTE, CID).peer_connection
    assert pc.rtc.events == ["remote", "local", f"candidate {REMOTE_CANDIDATE}"]
    assert pc.rtc.applied == [REMOTE_CANDIDATE]
    await manager.aclose()


async def test_trickle_listener_over_lan_strips_its_answer_and_sends_each_candidate():
    channels = Channels()
    manager = make_manager(channels, Recorder())
    await manager.handle_signal(REMOTE, CandidateAdd(CID, REMOTE_CANDIDATE).serialize())
    await offer(manager)
    messages = channels.lan_messages()
    assert isinstance(messages[0], ConnectResponse)
    assert "a=candidate" not in messages[0].sdp
    assert messages[1] == CandidateAdd(CID, HOST_CANDIDATE)
    await manager.aclose()


async def test_only_relay_only_flag_makes_the_listener_relay_only():
    flags = {REMOTE: ConnectionFlags.RELAY_ONLY, OTHER: ConnectionFlags.NO_TCP_CANDIDATES}
    manager = make_manager(Channels(), Recorder(), connection_flags=flags.__getitem__)
    await offer(manager)
    await offer(manager, sender=OTHER)
    assert manager.get(REMOTE, CID).peer_connection.kwargs["relay_only"] is True
    assert manager.get(OTHER, CID).peer_connection.kwargs["relay_only"] is False
    await manager.aclose()


async def test_listener_opens_once_when_ice_connects():
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    await offer(manager)
    session = manager.get(REMOTE, CID)
    session.peer_connection.drive_ice("completed")
    session.peer_connection.drive_ice("completed")
    assert recorder.opened == [session]
    await manager.aclose()


async def test_failed_answer_steps_close_with_their_errors():
    for step, error in (
        ("create", SessionError.FAILED_TO_CREATE_PEER_CONNECTION),
        ("answer", SessionError.FAILED_TO_CREATE_ANSWER),
    ):
        recorder = Recorder()
        factory = failing(**{step: error})
        manager = make_manager(Channels(), recorder, peer_connection_factory=factory)
        await offer(manager)
        assert recorder.errors() == [error]
        await manager.aclose()


async def test_two_remotes_may_use_the_same_connection_id():
    manager = make_manager(Channels(), Recorder())
    await offer(manager)
    await offer(manager, sender=OTHER)
    assert manager.get(REMOTE, CID) is not manager.get(OTHER, CID)
    assert len(manager.sessions) == 2
    await manager.aclose()


async def test_repeated_offer_reuses_the_listener_session_without_applying_it_again():
    channels = Channels()
    manager = make_manager(channels, Recorder())
    await offer(manager)
    session = manager.get(REMOTE, CID)
    pc = session.peer_connection
    sent = len(channels.lan)
    await offer(manager)
    assert manager.get(REMOTE, CID) is session
    assert session.peer_connection is pc
    assert len(channels.lan) == sent
    await manager.aclose()


async def test_offer_matching_a_dialer_session_is_ignored_and_sends_nothing():
    channels = Channels()
    recorder = Recorder()
    manager = make_manager(channels, recorder)
    dialer = manager.dial(REMOTE, connection_id=CID)
    await settle()
    sent = len(channels.lan)
    await offer(manager)
    assert manager.get(REMOTE, CID) is dialer
    assert len(channels.lan) == sent
    assert recorder.closed == []
    await manager.aclose()


async def test_accepted_answer_does_nothing_on_a_listener():
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    await offer(manager)
    pc = manager.get(REMOTE, CID).peer_connection
    await manager.handle_signal(REMOTE, ConnectResponse(CID, ANSWER).serialize())
    assert pc.remote_sdp == OFFER
    assert recorder.closed == []
    await manager.aclose()


async def test_listener_ignores_every_connect_error():
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    await offer(manager)
    for code in (SessionError.ICE, SessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED):
        await manager.handle_signal(REMOTE, ConnectError(CID, code).serialize())
    assert recorder.closed == []
    assert manager.get(REMOTE, CID) is not None
    await manager.aclose()


async def test_connect_error_for_an_unknown_pair_is_dropped():
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    await manager.handle_signal(REMOTE, ConnectError(CID, SessionError.ICE).serialize())
    assert manager.sessions == []
    await manager.aclose()


async def test_early_candidate_creates_a_listener_session_the_offer_attaches_to():
    manager = make_manager(Channels(), Recorder())
    await manager.handle_signal(REMOTE, CandidateAdd(CID, REMOTE_CANDIDATE).serialize())
    early = manager.get(REMOTE, CID)
    assert early is not None and not early.is_dialer
    assert early.peer_connection is None
    await offer(manager)
    assert manager.get(REMOTE, CID) is early
    assert early.peer_connection.rtc.applied == [REMOTE_CANDIDATE]
    await manager.aclose()


async def test_early_session_trickle_follows_the_candidate_channel():
    manager = make_manager(Channels(), Recorder())
    await manager.handle_signal(REMOTE, CandidateAdd(1, REMOTE_CANDIDATE).serialize(), via_lan=True)
    await manager.handle_signal(REMOTE, CandidateAdd(2, REMOTE_CANDIDATE).serialize())
    assert manager.get(REMOTE, 1).trickle is False
    assert manager.get(REMOTE, 2).trickle is True
    await manager.aclose()


async def test_early_session_whose_offer_never_comes_survives_the_sweep():
    clock = VirtualClock()
    manager = make_manager(Channels(), Recorder())
    await manager.handle_signal(REMOTE, CandidateAdd(CID, REMOTE_CANDIDATE).serialize())
    await clock.advance(60)
    assert manager.get(REMOTE, CID) is not None
    await manager.aclose()


async def test_malformed_message_is_dropped_with_no_reply():
    channels = Channels()
    recorder = Recorder()
    manager = make_manager(channels, recorder, http=True)
    for message in ("garbage", "CONNECTREQUEST abc sdp", "connectrequest 1 sdp"):
        await manager.handle_signal(REMOTE, message)
    await settle()
    assert manager.sessions == []
    assert channels.lan == channels.http == []
    await manager.aclose()


async def test_declined_offer_gets_connect_error_37_on_its_channel_and_no_session():
    for via_lan in (True, False):
        channels = Channels()
        manager = make_manager(channels, Recorder(), http=True, accept=lambda request: False)
        await offer(manager, via_lan=via_lan)
        reply = [ConnectError(CID, SessionError.IDENTITY_NOT_ALLOWED)]
        assert (channels.lan_messages() if via_lan else channels.http_messages()) == reply
        assert (channels.http if via_lan else channels.lan) == []
        assert manager.sessions == []
        await manager.aclose()


async def test_offer_whose_accept_hook_raises_gets_no_session_and_no_reply():
    def accept(request):
        raise RuntimeError("hook")

    channels = Channels()
    manager = make_manager(channels, Recorder(), http=True, accept=accept)
    await offer(manager, via_lan=True)
    await offer(manager, via_lan=False)
    assert manager.sessions == []
    assert channels.lan == channels.http == []
    await manager.aclose()


async def test_send_on_an_early_session_is_queued_until_its_peer_connection_exists():
    manager = make_manager(Channels(), Recorder())
    await manager.handle_signal(REMOTE, CandidateAdd(CID, REMOTE_CANDIDATE).serialize())
    assert manager.send(REMOTE, CID, b"early") is True
    await offer(manager)
    pc = manager.get(REMOTE, CID).peer_connection
    assert [bytes(f) for f in pc._reliable.pending] == [bytes([0]) + b"early"]
    await manager.aclose()


async def test_offer_decision_is_told_it_came_over_lan():
    decided = []
    manager = make_manager(Channels(), Recorder(), accept=lambda r: decided.append(r) or True)
    await offer(manager, OFFER + "a=identity:player\r\n", via_lan=True)
    assert decided[0].is_offer is True
    assert decided[0].via_lan is True
    assert decided[0].identity == "player"
    assert decided[0].fingerprints == [("sha-256", "AA:BB:CC")]
    await manager.aclose()


async def test_undecided_offer_gets_no_session_and_no_reply_however_long():
    clock = VirtualClock()
    channels = Channels()
    never = asyncio.get_running_loop().create_future()
    manager = make_manager(channels, Recorder(), accept=lambda request: never)
    task = asyncio.ensure_future(offer(manager))
    await clock.advance(120)
    assert manager.sessions == []
    assert channels.lan == []
    task.cancel()
    await manager.aclose()


async def test_duplicate_offer_is_decided_again_and_declined_keeps_the_session():
    answers = [True, False]
    channels = Channels()
    recorder = Recorder()
    manager = make_manager(channels, recorder, accept=lambda request: answers.pop(0))
    await offer(manager, via_lan=True)
    session = manager.get(REMOTE, CID)
    await offer(manager, via_lan=True)
    assert channels.lan_messages()[-1] == ConnectError(CID, SessionError.IDENTITY_NOT_ALLOWED)
    assert manager.get(REMOTE, CID) is session
    assert recorder.closed == []
    await manager.aclose()


async def test_declined_answer_closes_a_listener_with_37():
    decisions = [True, False]
    recorder = Recorder()
    manager = make_manager(Channels(), recorder, accept=lambda request: decisions.pop(0))
    await offer(manager)
    await manager.handle_signal(REMOTE, ConnectResponse(CID, ANSWER).serialize())
    assert recorder.errors() == [SessionError.IDENTITY_NOT_ALLOWED]
    await manager.aclose()


async def test_listener_has_no_negotiation_timeout():
    clock = VirtualClock()
    recorder = Recorder()
    manager = make_manager(Channels(), recorder, negotiation_timeout=1)
    await offer(manager)
    await clock.advance(60)
    assert recorder.closed == []
    await manager.aclose()


async def test_sweep_closes_a_listener_whose_ice_failed_with_5():
    clock = VirtualClock()
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    await offer(manager)
    manager.get(REMOTE, CID).peer_connection.set_ice_state("failed")
    await clock.advance(5)
    assert recorder.errors() == [SessionError.ICE]
    await manager.aclose()


async def test_unicast_delivery_failure_is_ignored_on_a_listener():
    channels = Channels(http_errors=[SessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED])
    recorder = Recorder()
    manager = make_manager(channels, recorder, lan=False, http=True)
    await offer(manager)
    await settle()
    assert recorder.closed == []
    await manager.aclose()


async def test_other_http_send_failure_closes_a_listener_with_that_error():
    channels = Channels(http_errors=[SessionError.SIGNALING_FAILED_TO_SEND])
    recorder = Recorder()
    manager = make_manager(channels, recorder, lan=False, http=True)
    await offer(manager)
    await settle()
    assert recorder.errors() == [SessionError.SIGNALING_FAILED_TO_SEND]
    await manager.aclose()


async def test_listener_prefers_the_channel_its_offer_came_on():
    channels = Channels()
    manager = make_manager(channels, Recorder(), http=True)
    await offer(manager, via_lan=False, cid=1)
    await offer(manager, via_lan=True, cid=2)
    assert [m.connection_id for m in channels.http_messages()] == [1]
    assert [m.connection_id for m in channels.lan_messages()] == [2]
    await manager.aclose()


async def test_data_channel_close_code_comes_from_the_peer_connection():
    for code in (None, SessionError.DATA_CHANNEL_CLOSED):
        recorder = Recorder()
        manager = make_manager(Channels(), recorder)
        await offer(manager)
        manager.get(REMOTE, CID).peer_connection.kwargs["on_close"](code)
        assert recorder.errors() == [code]
        await manager.aclose()


async def test_offer_whose_connection_flags_hook_raises_gets_no_session_and_no_reply():
    def flags(remote):
        raise RuntimeError("flags")

    channels = Channels()
    manager = make_manager(channels, Recorder(), http=True, connection_flags=flags)
    await offer(manager, via_lan=True)
    await offer(manager, via_lan=False)
    assert manager.sessions == []
    assert channels.lan == channels.http == []
    await manager.aclose()


async def test_lan_candidate_for_an_http_created_session_is_applied_at_once():
    manager = make_manager(Channels(), Recorder(), http=True)
    await offer(manager, via_lan=False)
    pc = manager.get(REMOTE, CID).peer_connection
    await manager.handle_signal(
        REMOTE, CandidateAdd(CID, REMOTE_CANDIDATE).serialize(), via_lan=True
    )
    assert pc.rtc.applied == [REMOTE_CANDIDATE]
    await manager.aclose()


async def test_silent_active_session_is_never_closed():
    clock = VirtualClock()
    recorder = Recorder()
    manager = make_manager(Channels(), recorder)
    await offer(manager)
    manager.get(REMOTE, CID).peer_connection.drive_ice("completed")
    await clock.advance(300)
    assert recorder.closed == []
    assert manager.state(REMOTE, CID).state is SessionState.ACTIVE
    await manager.aclose()


async def test_declined_answer_for_an_unknown_pair_is_ignored_with_no_37():
    channels = Channels()
    recorder = Recorder()
    manager = make_manager(channels, recorder, http=True, accept=lambda request: False)
    await manager.handle_signal(REMOTE, ConnectResponse(CID, ANSWER).serialize(), via_lan=True)
    await manager.handle_signal(REMOTE, ConnectResponse(CID, ANSWER).serialize())
    await settle()
    assert channels.lan == channels.http == []
    assert manager.sessions == []
    assert recorder.closed == []
    await manager.aclose()


async def test_first_offer_attaches_to_an_early_session_and_a_later_one_is_dropped():
    channels = Channels()
    manager = make_manager(channels, Recorder())
    await manager.handle_signal(REMOTE, CandidateAdd(CID, REMOTE_CANDIDATE).serialize())
    early = manager.get(REMOTE, CID)
    await offer(manager)
    pc = early.peer_connection
    answers = len(channels.lan)
    await offer(manager)
    assert manager.get(REMOTE, CID) is early
    assert early.peer_connection is pc
    assert pc.rtc.events.count("remote") == 1
    assert len(channels.lan) == answers
    await manager.aclose()


async def test_channel_choice_follows_the_endpoint_setting_then_lan_reachability_and_preference():
    async def http(remote, message):
        return None

    cases = [
        (False, True, True, True, Route.HTTP),
        (False, False, True, True, None),
        (True, True, True, True, Route.LAN),
        (True, True, True, False, Route.HTTP),
        (True, True, False, True, Route.HTTP),
        (True, False, True, False, Route.LAN),
        (True, False, False, True, None),
    ]
    for lan_signaling, other, on_lan, prefers_lan, expected in cases:
        channels = SignalingChannels(
            lan_send=lambda remote, message: None,
            http_send=http if other else None,
            is_lan_peer=lambda remote, on_lan=on_lan: on_lan,
            lan_signaling=lan_signaling,
        )
        assert channels.route(REMOTE, prefers_lan=prefers_lan) is expected


async def test_other_channel_setting_is_the_same_for_every_remote():
    channels = Channels()
    manager = make_manager(
        channels, Recorder(), http=True, is_lan_peer=lambda remote: remote == REMOTE
    )
    await offer(manager, sender=REMOTE, via_lan=True)
    await offer(manager, sender=OTHER, via_lan=True)
    assert [remote for remote, _ in channels.lan] == [REMOTE]
    assert [remote for remote, _ in channels.http] == [OTHER]
    await manager.aclose()


async def test_declined_lan_offer_with_lan_signaling_off_is_refused_over_the_other_channel():
    channels = Channels()
    manager = make_manager(
        channels, Recorder(), http=True, lan_signaling=False, accept=lambda request: False
    )
    await offer(manager, via_lan=True)
    assert channels.lan == []
    assert channels.http_messages() == [ConnectError(CID, SessionError.IDENTITY_NOT_ALLOWED)]
    await manager.aclose()


async def test_declined_offer_with_no_usable_channel_gets_no_reply():
    channels = Channels()
    manager = make_manager(channels, Recorder(), lan_signaling=False, accept=lambda request: False)
    await offer(manager, via_lan=True)
    assert channels.lan == channels.http == []
    await manager.aclose()
