import asyncio

from nethernet.errors import SendType, SessionError
from nethernet.network_id import NetworkID
from nethernet.session import SessionState
from nethernet.session_manager import SessionManager
from nethernet.transport.framing import FRAGMENT_SIZE

DIALER = NetworkID.p2p(111)
LISTENER = NetworkID.p2p(222)


class Peer:
    def __init__(self, local_id, network):
        self.opened = asyncio.Queue()
        self.packets = asyncio.Queue()
        self.closed = []
        self.manager = SessionManager(
            local_id=local_id,
            lan_send=lambda remote, message: network.deliver(local_id, remote, message),
            on_session_open=self.opened.put_nowait,
            on_session_close=lambda session, error, summary: self.closed.append(error),
            on_packet=lambda session, data: self.packets.put_nowait(data),
        )


class LanNetwork:
    def __init__(self):
        self.peers = {}
        self.tasks = set()

    def deliver(self, sender, remote, message):
        peer = self.peers.get(remote)
        if peer is None:
            return
        task = asyncio.ensure_future(peer.manager.handle_signal(sender, message, via_lan=True))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)


async def next_item(queue):
    return await asyncio.wait_for(queue.get(), timeout=25)


async def test_dialer_joins_a_lan_listener_and_packets_flow_both_ways():
    network = LanNetwork()
    dialer = Peer(DIALER, network)
    listener = Peer(LISTENER, network)
    network.peers = {DIALER: dialer, LISTENER: listener}
    try:
        session = dialer.manager.dial(LISTENER)
        assert dialer.manager.send(LISTENER, session.connection_id, b"early") is True
        assert await next_item(dialer.opened) is session
        remote = await next_item(listener.opened)
        assert remote.connection_id == session.connection_id
        assert remote.trickle is False
        assert dialer.manager.state(LISTENER, session.connection_id).state is SessionState.ACTIVE

        assert await next_item(listener.packets) == b"early"
        remote.send(b"pong", SendType.UNRELIABLE)
        assert await next_item(dialer.packets) == b"pong"
        big = bytes(i % 256 for i in range(FRAGMENT_SIZE + 1))
        session.send(big, SendType.RELIABLE)
        assert await next_item(listener.packets) == big

        assert dialer.manager.close(LISTENER, session.connection_id) is True
        assert dialer.closed == [SessionError.NONE]
    finally:
        await dialer.manager.aclose()
        await listener.manager.aclose()
