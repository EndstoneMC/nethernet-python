from __future__ import annotations

from collections import deque
from collections.abc import Callable

from nethernet.errors import SendType

FRAGMENT_SIZE = 0x3FFFF
MAX_FRAGMENTS = 255
MAX_PACKET_SIZE = MAX_FRAGMENTS * FRAGMENT_SIZE


class PacketQueue:
    def __init__(
        self,
        send_reliable: Callable[[bytes], None],
        send_unreliable: Callable[[bytes], None],
        *,
        fragment_size: int = FRAGMENT_SIZE,
    ) -> None:
        self._send_reliable = send_reliable
        self._send_unreliable = send_unreliable
        self._fragment_size = fragment_size
        self._partial = bytearray()
        self._expected_header: int | None = None
        self._received: deque[bytes] = deque()

    @property
    def fragment_size(self) -> int:
        return self._fragment_size

    @property
    def max_packet_size(self) -> int:
        return MAX_FRAGMENTS * self._fragment_size

    def update_fragment_size(self, max_message_size: int | None) -> bool:
        if max_message_size is None:
            return True
        if max_message_size <= 1:
            return False
        self._fragment_size = max_message_size - 1
        return True

    def push(self, data: bytes, send_type: SendType) -> bool:
        if send_type not in (SendType.RELIABLE, SendType.UNRELIABLE):
            return False
        size = len(data)
        if size == 0:
            return False
        fragment_size = self._fragment_size
        if size <= fragment_size:
            self._send_fragment(0, data, send_type)
            return True
        if size > MAX_FRAGMENTS * fragment_size:
            return False
        if send_type != SendType.RELIABLE:
            return False
        fragments = (size - 1) // fragment_size + 1
        offset = 0
        while fragments:
            fragments -= 1
            self._send_fragment(fragments, data[offset : offset + fragment_size], SendType.RELIABLE)
            offset += fragment_size
        return True

    def _send_fragment(self, header: int, payload: bytes, send_type: SendType) -> None:
        fragment = bytes((header,)) + bytes(payload)
        if send_type == SendType.RELIABLE:
            self._send_reliable(fragment)
        else:
            self._send_unreliable(fragment)

    def on_reliable_message(self, data: bytes) -> bool:
        if len(data) <= 1:
            self.discard_partial()
            return False
        header = data[0]
        if self._expected_header is not None and header != self._expected_header:
            self.discard_partial()
            return False
        self._partial.extend(data[1:])
        if header == 0:
            self._received.append(bytes(self._partial))
            self.discard_partial()
        else:
            self._expected_header = header - 1
        return True

    def on_unreliable_message(self, data: bytes) -> bool:
        if len(data) <= 1:
            return False
        self._received.append(bytes(data[1:]))
        return True

    def discard_partial(self) -> None:
        self._partial.clear()
        self._expected_header = None

    def peek(self) -> int | None:
        if not self._received:
            return None
        return len(self._received[0])

    def read(self, max_size: int | None = None) -> bytes | None:
        if not self._received:
            return None
        head = self._received[0]
        if max_size is None or max_size >= len(head):
            return self._received.popleft()
        self._received[0] = head[max_size:]
        return head[:max_size]
