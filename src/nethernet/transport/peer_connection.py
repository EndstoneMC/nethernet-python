from __future__ import annotations

import asyncio
import re
from collections import deque
from collections.abc import Callable

from aiortc import (
    RTCConfiguration,
    RTCDataChannel,
    RTCIceCandidate,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.sdp import candidate_from_sdp, candidate_to_sdp

from nethernet.errors import ConnectionFailed, SendType, SessionError
from nethernet.transport import stun_integrity
from nethernet.transport.framing import PacketQueue

RELIABLE_LABEL = "ReliableDataChannel"
UNRELIABLE_LABEL = "UnreliableDataChannel"
MAX_MESSAGE_SIZE = 262144
DEFAULT_MAX_MESSAGE_SIZE = 65536
MAX_ICE_SERVERS = 16
MAX_SEND_QUEUE_SIZE = 16 * 1024 * 1024
RESET_TIMEOUT = 1.0

_MAX_MESSAGE_SIZE_LINE = re.compile(r"^a=max-message-size:(\d+)\s*$", re.MULTILINE)

stun_integrity.install()


def set_max_message_size(sdp: str, value: int = MAX_MESSAGE_SIZE) -> str:
    out = []
    for line in sdp.splitlines(keepends=True):
        if line.startswith("a=max-message-size:"):
            continue
        out.append(line)
        if line.startswith("a=sctp-port:") and value != DEFAULT_MAX_MESSAGE_SIZE:
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            out.append(f"a=max-message-size:{value}{newline}")
    return "".join(out)


def remote_max_message_size(sdp: str) -> int:
    match = _MAX_MESSAGE_SIZE_LINE.search(sdp)
    return int(match.group(1)) if match else DEFAULT_MAX_MESSAGE_SIZE


def answer_max_message_size(offer_value: int) -> int:
    return MAX_MESSAGE_SIZE if offer_value <= 0 else min(offer_value, MAX_MESSAGE_SIZE)


def ice_configuration(
    ice_servers: list[RTCIceServer] | None, *, trickle: bool = True
) -> RTCConfiguration:
    servers = list(ice_servers or [])[:MAX_ICE_SERVERS] if trickle else []
    return RTCConfiguration(iceServers=servers)


def strip_candidates(sdp: str) -> tuple[str, list[str]]:
    kept: list[str] = []
    candidates: list[str] = []
    for line in sdp.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("a=candidate:"):
            candidates.append(stripped[len("a=") :])
        elif stripped == "a=end-of-candidates":
            continue
        else:
            kept.append(line)
    return "".join(kept), candidates


def keep_only_relay_candidates(sdp: str) -> str:
    out = [
        line
        for line in sdp.splitlines(keepends=True)
        if not (line.startswith("a=candidate:") and " typ relay" not in line)
    ]
    return "".join(out)


def candidate_to_message(candidate: RTCIceCandidate) -> str:
    return "candidate:" + candidate_to_sdp(candidate)


def candidate_from_message(text: str) -> RTCIceCandidate:
    value = text[len("candidate:") :] if text.startswith("candidate:") else text
    candidate = candidate_from_sdp(value)
    candidate.sdpMid = None
    candidate.sdpMLineIndex = 0
    return candidate


class _Outbound:
    def __init__(self) -> None:
        self.channel: RTCDataChannel | None = None
        self.pending: deque[bytes] = deque()
        self.closing = False

    def send(self, fragment: bytes) -> None:
        if self.closing:
            return
        self.pending.append(fragment)
        self.flush()

    def flush(self) -> None:
        channel = self.channel
        if channel is None or channel.readyState != "open":
            return
        while self.pending:
            size = len(self.pending[0])
            if channel.bufferedAmount + size > MAX_SEND_QUEUE_SIZE:
                channel.bufferedAmountLowThreshold = max(0, MAX_SEND_QUEUE_SIZE - size)
                return
            channel.send(self.pending.popleft())


class PeerConnection:
    def __init__(
        self,
        *,
        is_dialer: bool,
        ice_servers: list[RTCIceServer] | None = None,
        trickle: bool = True,
        relay_only: bool = False,
        on_packet: Callable[[bytes], None] | None = None,
        on_channels_open: Callable[[], None] | None = None,
        on_connection_state_change: Callable[[str], None] | None = None,
        on_ice_state_change: Callable[[str], None] | None = None,
        on_ice_connected: Callable[[], None] | None = None,
        on_ice_failed: Callable[[], None] | None = None,
        on_fragment_size_change: Callable[[int], None] | None = None,
        on_close: Callable[[SessionError | None], None] | None = None,
    ) -> None:
        self._relay_only = relay_only
        self._on_packet = on_packet
        self._on_channels_open = on_channels_open
        self._on_connection_state_change = on_connection_state_change
        self._on_ice_state_change = on_ice_state_change
        self._on_ice_connected = on_ice_connected
        self._on_ice_failed = on_ice_failed
        self._on_fragment_size_change = on_fragment_size_change
        self._on_close = on_close

        self._reliable = _Outbound()
        self._unreliable = _Outbound()
        self._packets = PacketQueue(self._reliable.send, self._unreliable.send)
        self._channels_open_fired = False
        self._ice_connected = False
        self._close_reported = False
        self._close_task: asyncio.Future[None] | None = None
        self._closed = False

        self._remote_sdp: str | None = None
        self._pending_candidates: deque[RTCIceCandidate] = deque()

        try:
            self._pc = RTCPeerConnection(
                configuration=ice_configuration(ice_servers, trickle=trickle)
            )
            if is_dialer:
                self._register_channel(self._pc.createDataChannel(RELIABLE_LABEL))
                self._register_channel(
                    self._pc.createDataChannel(UNRELIABLE_LABEL, ordered=False, maxRetransmits=0)
                )
        except Exception as exc:
            raise ConnectionFailed(SessionError.FAILED_TO_CREATE_PEER_CONNECTION) from exc

        @self._pc.on("connectionstatechange")
        def _on_connectionstatechange() -> None:
            if not self._closed and self._on_connection_state_change is not None:
                self._on_connection_state_change(self._pc.connectionState)

        @self._pc.on("iceconnectionstatechange")
        def _on_iceconnectionstatechange() -> None:
            self._handle_ice_state(self._pc.iceConnectionState)

        if not is_dialer:

            @self._pc.on("datachannel")
            def _on_datachannel(channel: RTCDataChannel) -> None:
                self._register_channel(channel)

    async def create_offer(self, *, identity: Callable[[str], str] | None = None) -> str:
        try:
            offer = await self._pc.createOffer()
        except Exception as exc:
            raise ConnectionFailed(SessionError.FAILED_TO_CREATE_OFFER) from exc
        await self._set_local_description(offer)
        return self._local_sdp(MAX_MESSAGE_SIZE, identity)

    async def create_answer(
        self, remote_offer_sdp: str, *, identity: Callable[[str], str] | None = None
    ) -> str:
        await self._set_remote_description(remote_offer_sdp, "offer")
        try:
            answer = await self._pc.createAnswer()
        except Exception as exc:
            raise ConnectionFailed(SessionError.FAILED_TO_CREATE_ANSWER) from exc
        await self._set_local_description(answer)
        offer_value = remote_max_message_size(remote_offer_sdp)
        sdp = self._local_sdp(answer_max_message_size(offer_value), identity)
        await self.apply_pending_candidates()
        return sdp

    async def set_remote_answer(self, remote_answer_sdp: str) -> None:
        await self._set_remote_description(remote_answer_sdp, "answer")
        await self.apply_pending_candidates()

    async def add_remote_candidate(self, candidate_text: str) -> None:
        try:
            candidate = candidate_from_message(candidate_text)
        except Exception:
            return
        if self._descriptions_set():
            await self._apply_candidate(candidate)
        else:
            self._pending_candidates.append(candidate)

    async def apply_pending_candidates(self) -> None:
        if not self._descriptions_set():
            return
        while self._pending_candidates:
            await self._apply_candidate(self._pending_candidates.popleft())

    def _descriptions_set(self) -> bool:
        return self._pc.localDescription is not None and self._pc.remoteDescription is not None

    async def _apply_candidate(self, candidate: RTCIceCandidate) -> None:
        try:
            await self._pc.addIceCandidate(candidate)
        except Exception:
            pass

    async def _set_local_description(self, description: RTCSessionDescription) -> None:
        try:
            await self._pc.setLocalDescription(description)
        except Exception as exc:
            raise ConnectionFailed(SessionError.FAILED_TO_SET_LOCAL_DESCRIPTION) from exc

    async def _set_remote_description(self, sdp: str, kind: str) -> None:
        try:
            await self._pc.setRemoteDescription(RTCSessionDescription(sdp, kind))
        except Exception as exc:
            raise ConnectionFailed(SessionError.FAILED_TO_SET_REMOTE_DESCRIPTION) from exc
        self._remote_sdp = sdp

    def _local_sdp(self, max_message_size: int, identity: Callable[[str], str] | None) -> str:
        try:
            sdp = set_max_message_size(self._pc.localDescription.sdp, max_message_size)
            if self._relay_only:
                sdp = keep_only_relay_candidates(sdp)
        except Exception as exc:
            raise ConnectionFailed(SessionError.FAILED_TO_SET_LOCAL_DESCRIPTION) from exc
        if identity is None:
            return sdp
        try:
            return identity(sdp)
        except Exception as exc:
            raise ConnectionFailed(SessionError.FAILED_TO_CREATE_IDENTITY_ASSERTION) from exc

    def _handle_ice_state(self, state: str) -> None:
        if self._closed:
            return
        if self._on_ice_state_change is not None:
            self._on_ice_state_change(state)
        if state == "completed":
            self._handle_ice_connected()
        elif state == "failed":
            self._report_close(SessionError.ICE)
            if self._on_ice_failed is not None:
                self._on_ice_failed()

    def _handle_ice_connected(self) -> None:
        if self._ice_connected:
            return
        self._ice_connected = True
        previous = self._packets.fragment_size
        size = remote_max_message_size(self._remote_sdp) if self._remote_sdp else None
        if not self._packets.update_fragment_size(size):
            self._report_close(SessionError.INVALID_ARGUMENT)
            return
        fragment_size = self._packets.fragment_size
        if fragment_size != previous and self._on_fragment_size_change is not None:
            self._on_fragment_size_change(fragment_size)
        if self._on_ice_connected is not None:
            self._on_ice_connected()

    def _report_close(self, error: SessionError | None) -> None:
        if self._close_reported:
            return
        self._close_reported = True
        if self._on_close is not None:
            self._on_close(error)

    def send(self, data: bytes, send_type: SendType) -> bool:
        if self._closed:
            return False
        return self._packets.push(data, send_type)

    def read(self, max_size: int | None = None) -> bytes | None:
        return self._packets.read(max_size)

    def peek(self) -> int | None:
        return self._packets.peek()

    def _register_channel(self, channel: RTCDataChannel) -> None:
        outbound = self._reliable if channel.label == RELIABLE_LABEL else self._unreliable
        outbound.channel = channel

        @channel.on("open")
        def _on_open() -> None:
            self._handle_channel_open(outbound)

        @channel.on("message")
        def _on_message(message: bytes | str) -> None:
            self._handle_channel_message(outbound, message)

        @channel.on("bufferedamountlow")
        def _on_bufferedamountlow() -> None:
            outbound.flush()

        @channel.on("close")
        def _on_close() -> None:
            self._handle_channel_close(outbound)

        if channel.readyState == "open":
            self._handle_channel_open(outbound)

    def _handle_channel_open(self, outbound: _Outbound) -> None:
        outbound.flush()
        if (
            self._channels_open_fired
            or not self._channel_open(self._reliable)
            or not self._channel_open(self._unreliable)
        ):
            return
        self._channels_open_fired = True
        if not self._closed and self._on_channels_open is not None:
            self._on_channels_open()

    @staticmethod
    def _channel_open(outbound: _Outbound) -> bool:
        return outbound.channel is not None and outbound.channel.readyState == "open"

    def _handle_channel_message(self, outbound: _Outbound, message: bytes | str) -> None:
        if outbound.closing or self._closed or self._close_task is not None:
            return
        data = bytes(message) if isinstance(message, (bytes, bytearray)) else message.encode()
        if outbound is self._reliable:
            accepted = self._packets.on_reliable_message(data)
        else:
            accepted = self._packets.on_unreliable_message(data)
        if not accepted:
            if self._close_task is not None:
                return
            self._report_close(None)
            reset = self._reset_channel(outbound)
            self._close_task = asyncio.ensure_future(self._close_after(reset))
            return
        self._deliver()

    def _reset_channel(self, outbound: _Outbound) -> asyncio.Future[None] | None:
        outbound.closing = True
        outbound.pending.clear()
        channel = outbound.channel
        if channel is None or channel.readyState in ("closing", "closed"):
            return None
        done = asyncio.get_running_loop().create_future()
        channel.on("close", lambda: done.done() or done.set_result(None))
        channel.close()
        return done

    async def _close_after(self, reset: asyncio.Future[None] | None) -> None:
        try:
            if reset is not None:
                try:
                    await asyncio.wait_for(reset, RESET_TIMEOUT)
                except TimeoutError:
                    pass
        finally:
            await self.close()

    def _handle_channel_close(self, outbound: _Outbound) -> None:
        outbound.pending.clear()
        if outbound is self._reliable:
            self._packets.discard_partial()
        self._report_close(SessionError.DATA_CHANNEL_CLOSED if self._transport_failed() else None)

    def _transport_failed(self) -> bool:
        sctp = self._pc.sctp
        return sctp is not None and (sctp.state == "closed" or sctp.transport.state == "failed")

    def _deliver(self) -> None:
        if self._on_packet is None:
            return
        while (packet := self._packets.read()) is not None:
            self._on_packet(packet)

    @property
    def connection_state(self) -> str:
        return self._pc.connectionState

    @property
    def ice_connection_state(self) -> str:
        return self._pc.iceConnectionState

    def candidate_pair(self) -> dict | None:
        sctp = self._pc.sctp
        if sctp is None:
            return None
        pair = sctp.transport.transport._connection._nominated.get(1)
        if pair is None:
            return None
        return {
            "local": "candidate:" + pair.local_candidate.to_sdp(),
            "remote": "candidate:" + pair.remote_candidate.to_sdp(),
            "state": pair.state.name.lower(),
            "rtt": None,
            "responses_received": None,
        }

    @property
    def fragment_size(self) -> int:
        return self._packets.fragment_size

    @property
    def max_packet_size(self) -> int:
        return self._packets.max_packet_size

    async def close(self) -> None:
        self._closed = True
        try:
            self._report_close(None)
        finally:
            await asyncio.shield(self._pc.close())
