import asyncio

import pytest
from aioice import ice, stun

from nethernet.transport import peer_connection  # noqa: F401

KEY = b"remote-password"
ADDR = ("127.0.0.1", 3478)


class Harness(ice.StunProtocol):
    def __init__(self):
        super().__init__(None)
        self.sent = []

    def send_stun(self, message, addr):
        self.sent.append(message)


def reply(request, message_class, key=None, error=None):
    response = stun.Message(stun.Method.BINDING, message_class, request.transaction_id)
    if error:
        response.attributes["ERROR-CODE"] = error
    if key is not None:
        response.add_message_integrity(key)
    return bytes(response)


async def start(protocol):
    request = stun.Message(stun.Method.BINDING, stun.Class.REQUEST)
    task = asyncio.ensure_future(protocol.request(request, ADDR, integrity_key=KEY))
    await asyncio.sleep(0)
    return request, task


async def test_unauthenticated_error_is_ignored_then_success_resolves():
    protocol = Harness()
    request, task = await start(protocol)
    protocol.datagram_received(reply(request, stun.Class.ERROR, error=(401, "Unauthorized")), ADDR)
    await asyncio.sleep(0)
    assert not task.done()
    protocol.datagram_received(reply(request, stun.Class.RESPONSE, key=KEY), ADDR)
    response, _ = await asyncio.wait_for(task, 1)
    assert response.message_class == stun.Class.RESPONSE


async def test_wrong_key_response_is_ignored():
    protocol = Harness()
    request, task = await start(protocol)
    protocol.datagram_received(reply(request, stun.Class.RESPONSE, key=b"other"), ADDR)
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()


async def test_authenticated_error_fails_transaction():
    protocol = Harness()
    request, task = await start(protocol)
    protocol.datagram_received(
        reply(request, stun.Class.ERROR, key=KEY, error=(487, "Role Conflict")), ADDR
    )
    with pytest.raises(stun.TransactionFailed):
        await asyncio.wait_for(task, 1)


async def test_request_without_integrity_key_accepts_plain_response():
    protocol = Harness()
    request = stun.Message(stun.Method.BINDING, stun.Class.REQUEST)
    task = asyncio.ensure_future(protocol.request(request, ADDR))
    await asyncio.sleep(0)
    protocol.datagram_received(reply(request, stun.Class.RESPONSE), ADDR)
    await asyncio.wait_for(task, 1)
