import asyncio

from nethernet import NetworkID, SendType
from nethernet.transport_api import Transport


async def test_two_transports_open_a_session_over_lan_udp():
    opened = asyncio.Queue()
    received = asyncio.Queue()
    host = Transport(
        NetworkID.p2p(222),
        lan_host="127.0.0.1",
        lan_port=0,
        on_session_open=opened.put_nowait,
        on_packet=lambda session, data: received.put_nowait(data),
    )
    client = Transport(NetworkID.p2p(111), lan_host="127.0.0.1", lan_port=0, visible=False)
    await host.start()
    await client.start()
    try:
        client.add_host(host.local_id, ("127.0.0.1", host.lan_port))
        session = client.dial_lan(host.local_id)
        await asyncio.wait_for(opened.get(), 30)
        session.send(b"hi host", SendType.RELIABLE)
        assert await asyncio.wait_for(received.get(), 30) == b"hi host"
    finally:
        await client.aclose()
        await host.aclose()


async def test_http_port_is_none_without_a_server():
    transport = Transport(NetworkID.p2p(5), lan_host="127.0.0.1", lan_port=0)
    await transport.start()
    try:
        assert transport.http_port is None
        assert transport.lan_port
    finally:
        await transport.aclose()
