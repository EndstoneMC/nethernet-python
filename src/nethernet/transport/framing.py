"""Application packet framing and fragmentation — SPEC.md s6.3-s6.4.

Each data-channel message is a fragment ``[header: u8][payload]`` where ``header`` is the count
of fragments that follow this one (the final fragment of a packet always has ``header == 0``).
Unreliable packets are never fragmented. A reassembled packet is queued FIFO for the reader.

This layer is transport-agnostic: outbound fragments go to injected send callbacks and inbound
fragments arrive via ``on_reliable_message`` / ``on_unreliable_message``. Reliable reassembly
relies on the underlying channel being ordered and reliable (SCTP), so no sequence numbers are
carried beyond the countdown header.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from nethernet.errors import SendType

FRAGMENT_SIZE = 0x3FFFF  # 262143 (SPEC.md s6.3)
MAX_PACKET_SIZE = 0x3FBFF01  # max single reliable packet, i.e. <= 256 fragments


class PacketQueue:
    """Fragments outbound packets and reassembles inbound ones into a FIFO receive queue."""

    def __init__(
        self,
        send_reliable: Callable[[bytes], None],
        send_unreliable: Callable[[bytes], None],
    ) -> None:
        self._send_reliable = send_reliable
        self._send_unreliable = send_unreliable
        self._reassembly = bytearray()
        self._received: deque[bytes] = deque()

    # -- sending -----------------------------------------------------------------------

    def push(self, data: bytes, send_type: SendType) -> bool:
        """Fragment and send a packet. Returns False if it was dropped (SPEC.md s6.3)."""
        size = len(data)
        if size < FRAGMENT_SIZE + 1:
            self._send_fragment(0, data, send_type)
            return True
        if size > MAX_PACKET_SIZE:
            return False  # too large
        if send_type != SendType.RELIABLE:
            return False  # multi-fragment packets must not go on the unreliable channel
        fragments = (size - 1) // FRAGMENT_SIZE + 1
        offset = 0
        while fragments:
            fragments -= 1  # header counts down: fragments-1 ... 0
            chunk = data[offset : offset + FRAGMENT_SIZE]
            self._send_fragment(fragments, chunk, SendType.RELIABLE)
            offset += FRAGMENT_SIZE
        return True

    def _send_fragment(self, header: int, payload: bytes, send_type: SendType) -> None:
        fragment = bytes((header,)) + bytes(payload)
        if send_type == SendType.RELIABLE:
            self._send_reliable(fragment)
        else:
            self._send_unreliable(fragment)

    # -- receiving ---------------------------------------------------------------------

    def on_reliable_message(self, data: bytes) -> None:
        """Reassemble an ordered fragment; deliver the packet when header == 0."""
        if len(data) == 0:
            return
        header = data[0]
        self._reassembly.extend(data[1:])
        if header == 0:
            self._received.append(bytes(self._reassembly))
            self._reassembly.clear()

    def on_unreliable_message(self, data: bytes) -> None:
        """An unordered message is a whole packet; strip the header and deliver."""
        if len(data) == 0:
            return
        self._received.append(bytes(data[1:]))

    def peek(self) -> int | None:
        """Size of the head packet, or None if the queue is empty (SPEC.md s6.4)."""
        if not self._received:
            return None
        return len(self._received[0])

    def read(self, max_size: int | None = None) -> bytes | None:
        """Read the head packet (SPEC.md s6.4).

        With ``max_size`` smaller than the head packet, return that prefix and leave the
        remainder at the head; otherwise dequeue and return the whole packet. None if empty.
        """
        if not self._received:
            return None
        head = self._received[0]
        if max_size is None or max_size >= len(head):
            return self._received.popleft()
        self._received[0] = head[max_size:]
        return head[:max_size]
