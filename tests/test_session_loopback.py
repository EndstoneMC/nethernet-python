"""SessionManager routing + full in-process handshake — SPEC.md s9 (Checkpoint D).

Two managers are wired through an in-memory signaling router; a dialer connects to a listener,
both data channels open, and application packets flow both ways (unreliable, reliable, and a
reliable multi-fragment packet).
"""

import asyncio

from nethernet.errors import SendType
from nethernet.network_id import NetworkID
from nethernet.session import SessionState
from nethernet.session_manager import SessionManager
from nethernet.signaling.messages import ConnectError
from nethernet.transport.framing import FRAGMENT_SIZE


async def wait_for(predicate, timeout=25.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise TimeoutError("condition not met in time")


class Loopback:
    """Routes signaling strings between managers by NetworkID (async, like a real channel)."""

    def __init__(self):
        self._peers: dict[str, SessionManager] = {}
        self._tasks: set[asyncio.Task] = set()

    def register(self, network_id: NetworkID, manager: SessionManager) -> None:
        self._peers[str(network_id)] = manager

    def send(self, sender_id: NetworkID, remote_id: NetworkID, message: str) -> None:
        target = self._peers.get(str(remote_id))
        if target is None:
            return
        task = asyncio.create_task(target.handle_signal(sender_id, message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def make_manager(loop_router, local_id, *, opened, received):
    return SessionManager(
        local_id=local_id,
        send_signal=lambda remote, message: loop_router.send(local_id, remote, message),
        on_session_open=opened.append,
        on_packet=lambda session, data: received.append(data),
    )


async def test_unknown_session_non_request_is_ignored():
    router = Loopback()
    mgr = make_manager(router, NetworkID.p2p(5), opened=[], received=[])
    # A CONNECTERROR for a session that doesn't exist must be ignored, not crash.
    await mgr.handle_signal(NetworkID.p2p(9), ConnectError(123456, 5).serialize())
    await mgr.handle_signal(NetworkID.p2p(9), "this is not a valid signaling message at all")
    await mgr.aclose()


async def test_full_handshake_and_bidirectional_traffic():
    router = Loopback()
    dialer_id = NetworkID.p2p(111)
    listener_id = NetworkID.p2p(222)
    dialer_opened: list = []
    listener_opened: list = []
    dialer_recv: list[bytes] = []
    listener_recv: list[bytes] = []

    dialer_mgr = make_manager(router, dialer_id, opened=dialer_opened, received=dialer_recv)
    listener_mgr = make_manager(router, listener_id, opened=listener_opened, received=listener_recv)
    router.register(dialer_id, dialer_mgr)
    router.register(listener_id, listener_mgr)

    try:
        session = await dialer_mgr.connect(listener_id)

        # Both sides reach CONNECTED (full offer/answer + ICE/DTLS/SCTP over loopback).
        await wait_for(
            lambda: session.state is SessionState.CONNECTED and len(listener_opened) == 1
        )
        listener_session = listener_opened[0]

        # Reliable single-fragment, dialer -> listener.
        session.send(b"hello", SendType.RELIABLE)
        await wait_for(lambda: listener_recv == [b"hello"])

        # Unreliable single-fragment, listener -> dialer.
        listener_session.send(b"pong", SendType.UNRELIABLE)
        await wait_for(lambda: dialer_recv == [b"pong"])

        # Reliable multi-fragment (262144 bytes), dialer -> listener.
        big = bytes(i % 256 for i in range(FRAGMENT_SIZE + 1))
        session.send(big, SendType.RELIABLE)
        await wait_for(lambda: len(listener_recv) == 2)
        assert listener_recv[1] == big
    finally:
        await dialer_mgr.aclose()
        await listener_mgr.aclose()
