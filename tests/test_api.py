from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import socket
from pathlib import Path

import pytest

import nethernet
from nethernet import (
    ConnectionClosed,
    ConnectionFailed,
    DiscoveredHost,
    PlayerSigner,
    SendType,
    SessionError,
    connect,
    discover,
    generate_operator_key,
    serve,
)
from nethernet.discovery.packets import HostAdvertisement
from nethernet.identity import encode_cpk

LOOPBACK = "127.0.0.1"
EXAMPLES = ("serve_echo.py", "connect_echo.py", "discover_hosts.py")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def player() -> PlayerSigner:
    key = generate_operator_key()
    header = _b64url(json.dumps({"alg": "ES384"}).encode())
    claims = _b64url(json.dumps({"cpk": encode_cpk(key.public_key()), "iss": "self"}).encode())
    return PlayerSigner(f"{header}.{claims}.{_b64url(b'sig')}", key)


async def echo_once(conn):
    await conn.send(await conn.recv(), SendType.RELIABLE)
    await conn.wait_closed()


async def idle(conn):
    await conn.wait_closed()


def local_server(handler, **kwargs):
    return serve(handler, host=LOOPBACK, port=0, lan_host=LOOPBACK, lan_port=0, **kwargs)


def load_example(name: str):
    path = Path(__file__).resolve().parent.parent / "examples" / name
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", EXAMPLES)
def test_examples_import_and_expose_main(name):
    assert callable(load_example(name).main)


def test_public_surface_is_exact():
    assert sorted(nethernet.__all__) == sorted(
        [
            "Address",
            "Connection",
            "ConnectionClosed",
            "ConnectionFailed",
            "ConnectionFlags",
            "DiscoveredHost",
            "IdentityEnvelope",
            "IdentitySigner",
            "IncomingOffer",
            "InvalidIdentity",
            "NetherNetError",
            "NetworkID",
            "NetworkIDType",
            "PlayerSigner",
            "SendType",
            "Server",
            "ServerIdentity",
            "SessionError",
            "SessionState",
            "__version__",
            "connect",
            "discover",
            "generate_operator_key",
            "new_connection_id",
            "serve",
        ]
    )
    for name in nethernet.__all__:
        assert hasattr(nethernet, name)


async def test_http_round_trip_with_player_and_operator_identity():
    seen = []
    async with local_server(echo_once) as server:
        url = f"http://{LOOPBACK}:{server.bound_port}"
        async with connect(
            url, identity_signer=player(), on_server_identity=seen.append, timeout=20
        ) as conn:
            await conn.send(b"ping")
            assert await conn.recv() == b"ping"
    assert len(seen) == 1
    assert seen[0].domain == "self"


async def test_http_target_without_scheme_is_probed():
    async with local_server(echo_once) as server:
        async with connect(
            f"{LOOPBACK}:{server.bound_port}", identity_signer=player(), timeout=20
        ) as conn:
            await conn.send(b"ping")
            assert await conn.recv() == b"ping"


async def test_http_connect_without_a_signer_uses_a_self_signed_player():
    async with local_server(echo_once) as server:
        async with connect(f"http://{LOOPBACK}:{server.bound_port}", timeout=20) as conn:
            await conn.send(b"ping")
            assert await conn.recv() == b"ping"


async def test_validate_offer_returning_false_is_refused_with_identity_not_allowed():
    offers = []

    def refuse(offer):
        offers.append(offer)
        return False

    async with local_server(idle, validate_offer=refuse) as server:
        with pytest.raises(ConnectionFailed) as exc:
            await connect(
                f"http://{LOOPBACK}:{server.bound_port}", identity_signer=player(), timeout=20
            )
    assert exc.value.error is SessionError.IDENTITY_NOT_ALLOWED
    assert len(offers) == 1


async def test_validate_offer_raising_drops_the_offer_with_no_reply():
    served = []

    def explode(offer):
        raise ValueError("boom")

    async def handler(conn):
        served.append(conn)

    async with local_server(handler, validate_offer=explode) as server:
        with pytest.raises(ConnectionFailed) as exc:
            await connect(
                f"http://{LOOPBACK}:{server.bound_port}",
                identity_signer=player(),
                negotiation_timeout=1,
                timeout=20,
            )
    assert exc.value.error is SessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE
    assert served == []


async def test_serve_signs_its_answers_with_a_generated_operator_key_by_default():
    pins = []
    for _ in range(2):
        async with local_server(idle) as server:
            seen = []
            async with connect(
                f"http://{LOOPBACK}:{server.bound_port}", on_server_identity=seen.append, timeout=20
            ):
                pass
            assert seen[0].domain == "self"
            assert seen[0].claims["cpk"]["crv"] == "P-384"
            pins.append(seen[0].key_pin)
    assert pins[0] != pins[1]


async def test_serve_with_a_lan_port_in_use_raises():
    async with local_server(idle) as first:
        with pytest.raises(OSError):
            await serve(idle, host=LOOPBACK, port=0, lan_host=LOOPBACK, lan_port=first.lan_port)


async def test_peer_stream_reset_closes_the_other_end_with_no_error():
    closed = asyncio.Event()
    seen = []

    async def watch(conn):
        await conn.send(await conn.recv())
        with pytest.raises(ConnectionClosed) as exc:
            await conn.recv()
        seen.append(exc.value.error)
        closed.set()

    async with local_server(watch) as server:
        conn = await connect(f"{LOOPBACK}:{server.bound_port}", timeout=20)
        await conn.send(b"hello")
        assert await conn.recv() == b"hello"
        conn._session.peer_connection._reliable.channel.close()
        await asyncio.wait_for(closed.wait(), 5)
        await conn.close()
    assert seen == [None]


async def test_on_server_identity_raising_closes_the_connection():
    def refuse(identity):
        raise ValueError("untrusted")

    async with local_server(idle) as server:
        with pytest.raises(ConnectionFailed) as exc:
            await connect(
                f"http://{LOOPBACK}:{server.bound_port}",
                identity_signer=player(),
                on_server_identity=refuse,
                timeout=20,
            )
    assert exc.value.error is SessionError.IDENTITY_NOT_ALLOWED


async def test_lan_round_trip_from_a_discovered_host():
    async with local_server(echo_once) as server:
        host = DiscoveredHost(server.local_id, b"", (LOOPBACK, server.lan_port))
        async with connect(host, bind_host=LOOPBACK, port=0, timeout=20) as conn:
            assert conn.remote_id == server.local_id
            await conn.send(b"ping")
            assert await conn.recv() == b"ping"


async def test_lan_connect_by_network_id_finds_the_host_first():
    async with local_server(echo_once) as server:
        async with connect(
            server.local_id,
            bind_host=LOOPBACK,
            port=0,
            broadcast_host=LOOPBACK,
            broadcast_port=server.lan_port,
            timeout=20,
        ) as conn:
            await conn.send(b"ping")
            assert await conn.recv() == b"ping"


async def test_connect_without_a_signaling_channel_raises_connection_failed():
    with pytest.raises(ConnectionFailed) as exc:
        await connect(f"{LOOPBACK}:1")
    assert exc.value.error is SessionError.NO_SIGNALING_CHANNEL


async def test_recv_after_a_clean_close_raises_connection_closed():
    async with local_server(idle) as server:
        host = DiscoveredHost(server.local_id, b"", (LOOPBACK, server.lan_port))
        conn = await connect(host, bind_host=LOOPBACK, port=0, timeout=20)
        await conn.close()
        with pytest.raises(ConnectionClosed):
            await conn.recv()
        with pytest.raises(ConnectionClosed):
            await conn.send(b"late")


async def test_serve_with_an_unparseable_host_raises():
    with pytest.raises(OSError):
        await serve(idle, host="not an address", port=0, lan_port=0)


async def test_serve_with_a_port_in_use_raises():
    with socket.socket() as busy:
        busy.bind((LOOPBACK, 0))
        busy.listen()
        with pytest.raises(OSError):
            await serve(idle, host=LOOPBACK, port=busy.getsockname()[1], lan_port=0)


async def test_discover_yields_the_host_with_a_lan_advertisement():
    async with local_server(idle, name="Echo", level="Hub") as server:
        hosts = [
            h
            async for h in discover(
                bind_host=LOOPBACK,
                port=0,
                broadcast_host=LOOPBACK,
                broadcast_port=server.lan_port,
                timeout=1,
            )
        ]
    assert [h.network_id for h in hosts] == [server.local_id]
    assert hosts[0].address == (LOOPBACK, server.lan_port)
    assert hosts[0].name == "Echo"
    assert hosts[0].level == "Hub"
    assert hosts[0].players == 0
    assert hosts[0].max_players == 10
    assert HostAdvertisement.parse(hosts[0].advertisement).connection == 4


async def test_invisible_server_does_not_answer_discovery():
    async with local_server(idle, visible=False) as server:
        hosts = [
            h
            async for h in discover(
                bind_host=LOOPBACK,
                port=0,
                broadcast_host=LOOPBACK,
                broadcast_port=server.lan_port,
                timeout=0.5,
            )
        ]
    assert hosts == []


async def test_set_advertisement_changes_what_discovery_reports_and_rejects_unknown_fields():
    async with local_server(idle) as server:
        server.set_advertisement(name="Renamed", max_players=3)
        with pytest.raises(TypeError):
            server.set_advertisement(bogus=1)
        hosts = [
            h
            async for h in discover(
                bind_host=LOOPBACK,
                port=0,
                broadcast_host=LOOPBACK,
                broadcast_port=server.lan_port,
                timeout=0.5,
            )
        ]
    assert hosts[0].name == "Renamed"
    assert hosts[0].max_players == 3


async def test_host_sees_the_client_close_as_a_data_channel_close():
    closed = asyncio.Event()
    seen = []

    async def watch(conn):
        await conn.send(await conn.recv())
        seen.append(await conn.wait_closed())
        closed.set()

    async with local_server(watch) as server:
        async with connect(f"{LOOPBACK}:{server.bound_port}", timeout=20) as conn:
            await conn.send(b"hello")
            assert await conn.recv() == b"hello"
        assert await conn.wait_closed() is None
        await asyncio.wait_for(closed.wait(), 5)
    assert seen == [SessionError.DATA_CHANNEL_CLOSED]


async def test_serve_does_not_broadcast_unless_asked():
    loop = asyncio.get_running_loop()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind((LOOPBACK, 0))
        listener.setblocking(False)
        target = listener.getsockname()[1]

        async with local_server(
            idle, broadcast_host=LOOPBACK, broadcast_port=target, broadcast=False
        ):
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(loop.sock_recv(listener, 2048), 0.5)

        async with local_server(
            idle, broadcast_host=LOOPBACK, broadcast_port=target, broadcast=True
        ):
            assert await asyncio.wait_for(loop.sock_recv(listener, 2048), 5)


async def test_serve_with_an_unparseable_lan_host_raises():
    with pytest.raises(OSError):
        await serve(idle, host=LOOPBACK, port=0, lan_host="bogus", lan_port=0)


async def test_connect_lan_lookup_gives_up_after_the_connect_timeout():
    loop = asyncio.get_running_loop()
    start = loop.time()
    with pytest.raises(TimeoutError):
        await connect(
            nethernet.NetworkID.p2p(1),
            bind_host=LOOPBACK,
            port=0,
            broadcast_host=LOOPBACK,
            broadcast_port=9,
            timeout=0.5,
            negotiation_timeout=30,
        )
    assert loop.time() - start < 5


async def test_connect_lan_lookup_falls_back_to_the_negotiation_timeout():
    loop = asyncio.get_running_loop()
    start = loop.time()
    with pytest.raises(TimeoutError):
        await connect(
            nethernet.NetworkID.p2p(1),
            bind_host=LOOPBACK,
            port=0,
            broadcast_host=LOOPBACK,
            broadcast_port=9,
            negotiation_timeout=0.5,
        )
    assert loop.time() - start < 5


class GarbageSigner:
    def sign(self, sdp):
        return sdp.replace("m=application", "a=identity:garbage\r\nm=application", 1)


async def test_garbage_identity_is_accepted_over_lan_and_refused_over_http():
    async with local_server(echo_once) as server:
        host = DiscoveredHost(server.local_id, b"", (LOOPBACK, server.lan_port))
        async with connect(
            host, bind_host=LOOPBACK, port=0, identity_signer=GarbageSigner(), timeout=20
        ) as conn:
            await conn.send(b"ping")
            assert await conn.recv() == b"ping"
        with pytest.raises(ConnectionFailed) as exc:
            await connect(
                f"http://{LOOPBACK}:{server.bound_port}",
                identity_signer=GarbageSigner(),
                timeout=20,
            )
    assert exc.value.error is SessionError.IDENTITY_NOT_ALLOWED


class UnsignedSigner:
    def sign(self, sdp):
        return sdp


async def test_unsigned_http_answer_is_refused_even_without_a_callback():
    async with local_server(idle, identity_signer=UnsignedSigner()) as server:
        with pytest.raises(ConnectionFailed) as exc:
            await connect(f"http://{LOOPBACK}:{server.bound_port}", timeout=20)
    assert exc.value.error is SessionError.IDENTITY_NOT_ALLOWED


async def test_signed_http_answer_is_accepted_without_a_callback():
    async with local_server(echo_once) as server:
        async with connect(f"http://{LOOPBACK}:{server.bound_port}", timeout=20) as conn:
            await conn.send(b"ping")
            assert await conn.recv() == b"ping"


async def test_unsigned_answer_over_lan_is_accepted():
    async with local_server(echo_once, identity_signer=UnsignedSigner()) as server:
        host = DiscoveredHost(server.local_id, b"", (LOOPBACK, server.lan_port))
        async with connect(host, bind_host=LOOPBACK, port=0, timeout=20) as conn:
            await conn.send(b"ping")
            assert await conn.recv() == b"ping"
