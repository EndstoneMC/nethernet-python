"""HTTP signaling end-to-end — Onboarding Guide s4 (full ICE, one POST round-trip)."""

import pytest
from aiohttp import web

import nethernet
from nethernet import (
    ConnectionFailed,
    IdentitySigner,
    NetworkID,
    SendType,
    SessionError,
    SignalingRejected,
    generate_operator_key,
)
from nethernet.identity import cpk_digest
from nethernet.transport.framing import FRAGMENT_SIZE


async def echo_handler(conn):
    async with conn:
        async for packet in conn:
            await conn.send(packet, SendType.RELIABLE)


async def test_http_loopback_echo():
    async with nethernet.serve_http(echo_handler, host="127.0.0.1") as server:
        url = f"http://127.0.0.1:{server.bound_port}"
        conn = await nethernet.connect_http(url, timeout=30)
        async with conn:
            await conn.send(b"hello", SendType.RELIABLE)
            assert await conn.recv() == b"hello"
            # A multi-fragment reliable packet reassembles across the HTTP-signaled session.
            big = bytes(i % 256 for i in range(FRAGMENT_SIZE + 1))
            await conn.send(big, SendType.RELIABLE)
            assert await conn.recv() == big


async def test_http_identity_end_to_end():
    key = generate_operator_key()
    signer = IdentitySigner(key, domain="partner.example", claims={"iss": "loopback"})
    seen = []

    async with nethernet.serve_http(
        echo_handler, host="127.0.0.1", identity_signer=signer
    ) as server:
        url = f"http://127.0.0.1:{server.bound_port}"
        conn = await nethernet.connect_http(url, on_server_identity=seen.append, timeout=30)
        async with conn:
            await conn.send(b"ping", SendType.RELIABLE)
            assert await conn.recv() == b"ping"

    (identity,) = seen
    assert identity.domain == "partner.example"
    assert identity.claims["iss"] == "loopback"
    assert identity.key_digest == cpk_digest(key.public_key())


async def test_validate_offer_sees_network_id_and_stripped_sdp():
    offers = []

    async with nethernet.serve_http(
        echo_handler, host="127.0.0.1", validate_offer=offers.append
    ) as server:
        url = f"http://127.0.0.1:{server.bound_port}"
        local = NetworkID.p2p(1234567890)
        conn = await nethernet.connect_http(url, local_id=local, timeout=30)
        async with conn:
            await conn.send(b"ping", SendType.RELIABLE)
            assert await conn.recv() == b"ping"

    (offer,) = offers
    assert offer.network_id_text == "1234567890"
    assert offer.network_id == local
    assert offer.identity is None
    assert "a=identity" not in offer.sdp
    assert "a=candidate:" in offer.sdp  # full ICE: candidates embedded in the offer


async def test_validate_offer_rejection_fails_the_dial():
    def reject(offer):
        raise SignalingRejected("not on the allowlist")

    async def handler(conn):  # pragma: no cover - never reached
        raise AssertionError("handler must not run for a rejected offer")

    async with nethernet.serve_http(handler, host="127.0.0.1", validate_offer=reject) as server:
        url = f"http://127.0.0.1:{server.bound_port}"
        with pytest.raises(ConnectionFailed) as info:
            await nethernet.connect_http(url, timeout=30)
        assert info.value.error == SessionError.SIGNALING_FAILED_TO_SEND


async def test_capability_check_fails_on_non_nethernet_server():
    # A plain HTTP server without /v1/join: the client must not attempt a connection (Guide s4).
    runner = web.AppRunner(web.Application())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        with pytest.raises(ConnectionFailed) as info:
            await nethernet.connect_http(f"http://127.0.0.1:{port}")
        assert info.value.error == SessionError.NO_SIGNALING_CHANNEL
    finally:
        await runner.cleanup()
