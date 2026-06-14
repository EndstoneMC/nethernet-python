"""Public Transport façade — wiring + full end-to-end over real LAN UDP (Checkpoint E)."""

import asyncio

import pytest

from nethernet import (
    ESendType,
    NetworkID,
    SessionState,
    Transport,
)
from nethernet.constants import (
    DEFAULT_APPLICATION_ID,
    DEFAULT_BROADCAST_INTERVAL,
    DEFAULT_BROADCAST_PORT,
    DEFAULT_NEGOTIATION_TIMEOUT,
)


async def wait_for(predicate, timeout=30.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise TimeoutError("condition not met in time")


def test_spec_defaults():
    assert DEFAULT_BROADCAST_PORT == 7551
    assert DEFAULT_APPLICATION_ID == 0xDEADBEEF
    assert DEFAULT_BROADCAST_INTERVAL == 2.0
    assert DEFAULT_NEGOTIATION_TIMEOUT == 10.0


def test_transport_requires_p2p_network_id():
    import uuid

    with pytest.raises(ValueError):
        Transport(NetworkID.realms(uuid.uuid4()))


async def test_end_to_end_session_over_lan_udp():
    host_id = NetworkID.p2p(222)
    client_id = NetworkID.p2p(111)
    host_sessions: list = []
    host_recv: list[bytes] = []
    client_recv: list[bytes] = []

    host = Transport(
        host_id,
        bind_host="127.0.0.1",
        port=0,
        advertisement=b"MCPE;NetherNet-Python;0;0.0.0;0;10;",
        on_session=host_sessions.append,
        on_packet=lambda session, data: host_recv.append(data),
    )
    client = Transport(
        client_id,
        bind_host="127.0.0.1",
        port=0,
        on_packet=lambda session, data: client_recv.append(data),
    )
    await host.start()
    await client.start()
    try:
        # Simulate "peer learned via discovery": seed the host's address (loopback can't broadcast).
        client.remember_peer(host_id, ("127.0.0.1", host.bound_port))

        session = await client.connect(host_id)
        await wait_for(lambda: session.state is SessionState.CONNECTED and len(host_sessions) == 1)

        session.send(b"hi host", ESendType.RELIABLE)
        await wait_for(lambda: host_recv == [b"hi host"])

        host_sessions[0].send(b"hi client", ESendType.UNRELIABLE)
        await wait_for(lambda: client_recv == [b"hi client"])
    finally:
        await client.aclose()
        await host.aclose()
