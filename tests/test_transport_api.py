import asyncio

from nethernet import NetworkID, SendType, SessionError
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


async def test_a_host_signal_to_an_unmatched_http_dialer_is_dropped_as_success():
    transport = Transport(
        NetworkID.p2p(5), lan_host="127.0.0.1", lan_port=0, http_host="127.0.0.1", http_port=0
    )
    await transport.start()
    try:
        assert await transport._http_send(NetworkID.p2p(9), "CONNECTRESPONSE 3 v=0") is None
    finally:
        await transport.aclose()


async def test_a_host_signal_without_an_http_server_reports_delivery_failure():
    transport = Transport(NetworkID.p2p(5), lan_host="127.0.0.1", lan_port=0)
    await transport.start()
    try:
        error = await transport._http_send(NetworkID.p2p(9), "CONNECTRESPONSE 3 v=0")
        assert error is SessionError.SIGNALING_UNICAST_MESSAGE_DELIVERY_FAILED
    finally:
        await transport.aclose()
