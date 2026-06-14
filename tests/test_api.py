"""Public async API surface — see docs/api-design.md.

connect() / serve() / discover() + Connection / Server / DiscoveredHost, layered over the
internal Transport engine. These exercise the public contract only.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from nethernet.api import DiscoveredHost, connect, discover, serve
from nethernet.errors import (
    ConnectionClosed,
    ConnectionFailed,
    ESendType,
    ESessionError,
    NetherNetError,
)
from nethernet.network_id import NetworkID

LOOPBACK = "127.0.0.1"

# --- Examples (docs/api-design.md s4) ---


def _load_example(name: str):
    path = Path(__file__).resolve().parent.parent / "examples" / name
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_examples_import_and_expose_main():
    server = _load_example("server.py")
    client = _load_example("client.py")
    assert callable(server.main)
    assert callable(client.main)


# --- Public surface (docs/api-design.md s3.6) ---


def test_public_surface_is_exported_and_transport_is_internal():
    import nethernet

    for name in (
        "connect",
        "serve",
        "discover",
        "Connection",
        "Server",
        "DiscoveredHost",
        "NetworkID",
        "NetworkIDType",
        "new_connection_id",
        "SessionState",
        "ESendType",
        "ESessionError",
        "EConnectionFlags",
        "NetherNetError",
        "ConnectionFailed",
        "ConnectionClosed",
        "Address",
    ):
        assert hasattr(nethernet, name), f"missing public export: {name}"

    # Transport is the internal engine — not part of the public top-level surface.
    assert not hasattr(nethernet, "Transport")


# --- Exceptions (docs/api-design.md s3.5) ---


def test_exception_hierarchy_and_error_payload():
    assert issubclass(NetherNetError, Exception)
    assert issubclass(ConnectionFailed, NetherNetError)
    assert issubclass(ConnectionClosed, NetherNetError)

    failed = ConnectionFailed(ESessionError.ICE)
    assert failed.error is ESessionError.ICE
    assert "ICE" in str(failed)

    closed = ConnectionClosed(ESessionError.NONE)
    assert closed.error is ESessionError.NONE


# --- connect() / serve() / Connection round trip (docs/api-design.md s3.1-s3.3) ---


async def test_serve_and_connect_round_trip():
    server_id = NetworkID.p2p(222)
    received: list[bytes] = []

    async def handler(conn):
        data = await conn.recv()
        received.append(data)
        await conn.send(b"pong", ESendType.RELIABLE)
        await conn.wait_closed()

    async with serve(
        handler, server_id, bind_host=LOOPBACK, port=0, advertisement=b"adv", broadcast=False
    ) as server:
        host = DiscoveredHost(server_id, b"adv", (LOOPBACK, server.bound_port))
        async with await connect(host, bind_host=LOOPBACK, port=0) as conn:
            assert conn.remote_id == server_id
            await conn.send(b"ping", ESendType.RELIABLE)
            assert await conn.recv() == b"pong"
        assert received == [b"ping"]


async def test_connect_failure_raises_connection_failed():
    # No server: the dialer's CONNECTREQUEST goes unanswered, so negotiation times out (s9.4).
    with pytest.raises(ConnectionFailed) as exc:
        await connect(
            NetworkID.p2p(999),
            bind_host=LOOPBACK,
            port=0,
            negotiation_timeout=1.0,
        )
    assert exc.value.error in (
        ESessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE,
        ESessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_ACCEPT,
    )


async def test_connect_explicit_timeout_raises_connection_failed():
    # The connect()-level `timeout` bounds the wait independently of negotiation_timeout.
    with pytest.raises(ConnectionFailed) as exc:
        await connect(NetworkID.p2p(998), bind_host=LOOPBACK, port=0, timeout=0.5)
    assert exc.value.error is ESessionError.NEGOTIATION_TIMEOUT_WAITING_FOR_RESPONSE


async def test_recv_after_clean_close_raises_connection_closed():
    server_id = NetworkID.p2p(222)

    async def handler(conn):
        await conn.wait_closed()

    async with serve(handler, server_id, bind_host=LOOPBACK, port=0, broadcast=False) as server:
        host = DiscoveredHost(server_id, b"", (LOOPBACK, server.bound_port))
        conn = await connect(host, bind_host=LOOPBACK, port=0)
        await conn.close()
        with pytest.raises(ConnectionClosed):
            await conn.recv()


# --- discover() (docs/api-design.md s3.1) ---


async def test_discover_yields_hosts_with_address():
    server_id = NetworkID.p2p(222)

    async def handler(conn):
        await conn.wait_closed()

    async with serve(
        handler,
        server_id,
        bind_host=LOOPBACK,
        port=0,
        advertisement=b"MCPE;disc;1",
        broadcast=False,
    ) as server:
        hosts = [
            h
            async for h in discover(
                bind_host=LOOPBACK,
                port=0,
                broadcast_host=LOOPBACK,
                broadcast_port=server.bound_port,
                timeout=2.0,
            )
        ]

    found = [h for h in hosts if h.network_id == server_id]
    assert len(found) == 1
    assert found[0].advertisement == b"MCPE;disc;1"
    assert found[0].address == (LOOPBACK, server.bound_port)
