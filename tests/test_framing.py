"""Application packet framing / fragmentation — SPEC.md s6.3-s6.4.

The PacketQueue is exercised without aiortc: outbound fragments are captured via injected send
callbacks, and inbound fragments are fed back through on_*_message().
"""

from nethernet.errors import ESendType
from nethernet.transport.framing import FRAGMENT_SIZE, MAX_PACKET_SIZE, PacketQueue


def make_queue():
    reliable_out: list[bytes] = []
    unreliable_out: list[bytes] = []
    q = PacketQueue(reliable_out.append, unreliable_out.append)
    return q, reliable_out, unreliable_out


# --- Single-fragment sends (N <= FRAGMENT_SIZE), SPEC.md s6.3 case 1 ---


def test_single_byte_packet_is_one_fragment_header_zero():
    q, rel, _ = make_queue()
    assert q.push(b"A", ESendType.RELIABLE) is True
    assert rel == [b"\x00A"]


def test_exactly_fragment_size_is_a_single_fragment():
    q, rel, _ = make_queue()
    payload = bytes(FRAGMENT_SIZE)
    assert q.push(payload, ESendType.RELIABLE) is True
    assert len(rel) == 1
    assert rel[0][0] == 0
    assert len(rel[0]) == FRAGMENT_SIZE + 1


def test_empty_packet_sends_header_only_and_delivers_empty():
    q, rel, _ = make_queue()
    assert q.push(b"", ESendType.RELIABLE) is True
    assert rel == [b"\x00"]
    q.on_reliable_message(rel[0])
    assert q.read() == b""


def test_unreliable_single_fragment_uses_unreliable_channel():
    q, rel, unrel = make_queue()
    assert q.push(b"hi", ESendType.UNRELIABLE) is True
    assert rel == []
    assert unrel == [b"\x00hi"]


# --- Multi-fragment reliable (SPEC.md s6.3 case 4): countdown headers ---


def test_fragment_size_plus_one_splits_into_two_with_countdown():
    q, rel, _ = make_queue()
    payload = bytes(i % 256 for i in range(FRAGMENT_SIZE + 1))  # 262144
    assert q.push(payload, ESendType.RELIABLE) is True
    assert len(rel) == 2
    assert rel[0][0] == 1 and len(rel[0]) == 1 + FRAGMENT_SIZE
    assert rel[1][0] == 0 and len(rel[1]) == 1 + 1
    for frag in rel:
        q.on_reliable_message(frag)
    assert q.read() == payload


def test_three_fragment_reassembly_headers_count_down_to_zero():
    q, rel, _ = make_queue()
    payload = bytes((i * 7) % 256 for i in range(FRAGMENT_SIZE * 2 + 5))
    n = len(payload)
    expected = (n - 1) // FRAGMENT_SIZE + 1
    assert q.push(payload, ESendType.RELIABLE) is True
    assert len(rel) == expected
    assert [f[0] for f in rel] == list(range(expected - 1, -1, -1))  # [2, 1, 0]
    for frag in rel:
        q.on_reliable_message(frag)
    assert q.read() == payload


def test_reassembly_delivers_nothing_until_final_fragment():
    q, rel, _ = make_queue()
    payload = bytes(FRAGMENT_SIZE + 1)
    q.push(payload, ESendType.RELIABLE)
    q.on_reliable_message(rel[0])
    assert q.peek() is None  # header != 0 -> still reassembling
    q.on_reliable_message(rel[1])
    assert q.read() == payload


# --- Drop rules (SPEC.md s6.3 cases 2 and 3) ---


def test_oversize_packet_is_dropped():
    q, rel, unrel = make_queue()
    assert q.push(bytes(MAX_PACKET_SIZE + 1), ESendType.RELIABLE) is False
    assert rel == [] and unrel == []


def test_unreliable_multifragment_is_dropped():
    q, rel, unrel = make_queue()
    assert q.push(bytes(FRAGMENT_SIZE + 1), ESendType.UNRELIABLE) is False
    assert rel == [] and unrel == []


# --- Receive: zero-length ignored, FIFO, partial read (SPEC.md s6.3-s6.4) ---


def test_zero_length_inbound_is_ignored():
    q, _, _ = make_queue()
    q.on_reliable_message(b"")
    q.on_unreliable_message(b"")
    assert q.peek() is None
    assert q.read() is None


def test_received_packets_are_fifo():
    q, _, _ = make_queue()
    q.on_unreliable_message(b"\x00first")
    q.on_unreliable_message(b"\x00second")
    assert q.read() == b"first"
    assert q.read() == b"second"
    assert q.read() is None


def test_partial_read_leaves_remainder_at_head():
    q, _, _ = make_queue()
    q.on_unreliable_message(b"\x00" + b"0123456789")  # one 10-byte packet
    assert q.peek() == 10
    assert q.read(4) == b"0123"
    assert q.peek() == 6  # remainder stays at head
    assert q.read(100) == b"456789"
    assert q.peek() is None
