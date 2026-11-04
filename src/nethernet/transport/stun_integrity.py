"""Ignore STUN responses that fail MESSAGE-INTEGRITY, as libwebrtc's StunRequestManager does."""

from __future__ import annotations

from aioice import ice, stun

_original_request = ice.StunProtocol.request
_original_datagram_received = ice.StunProtocol.datagram_received


async def _request(self, request, addr, integrity_key=None, retransmissions=None):
    keys = self.__dict__.setdefault("integrity_keys", {})
    if integrity_key is not None:
        keys[request.transaction_id] = integrity_key
    try:
        return await _original_request(self, request, addr, integrity_key, retransmissions)
    finally:
        keys.pop(request.transaction_id, None)


def _datagram_received(self, data, addr):
    key = self.__dict__.get("integrity_keys", {}).get(bytes(data[8:20]))
    if key is not None and not _is_authentic(data, key):
        return
    _original_datagram_received(self, data, addr)


def _is_authentic(data, key) -> bool:
    try:
        message = stun.parse_message(data)
    except ValueError:
        return True
    if message.message_class not in (stun.Class.RESPONSE, stun.Class.ERROR):
        return True
    if "MESSAGE-INTEGRITY" not in message.attributes:
        return False
    try:
        stun.parse_message(data, integrity_key=key)
    except ValueError:
        return False
    return True


def install() -> None:
    if ice.StunProtocol.request is not _request:
        ice.StunProtocol.request = _request
        ice.StunProtocol.datagram_received = _datagram_received
