from nethernet.errors import SendType
from nethernet.transport.framing import FRAGMENT_SIZE, MAX_PACKET_SIZE, PacketQueue


def make_queue(fragment_size=FRAGMENT_SIZE):
    reliable_out: list[bytes] = []
    unreliable_out: list[bytes] = []
    q = PacketQueue(reliable_out.append, unreliable_out.append, fragment_size=fragment_size)
    return q, reliable_out, unreliable_out


def test_default_fragment_size_and_packet_cap_match_the_rule():
    assert FRAGMENT_SIZE == 0x3FFFF
    assert MAX_PACKET_SIZE == 0x3FBFF01
    q, _, _ = make_queue()
    assert q.fragment_size == FRAGMENT_SIZE
    assert q.max_packet_size == MAX_PACKET_SIZE


def test_single_byte_packet_is_one_fragment_header_zero():
    q, rel, _ = make_queue()
    assert q.push(b"A", SendType.RELIABLE) is True
    assert rel == [b"\x00A"]


def test_exactly_fragment_size_is_a_single_fragment():
    q, rel, _ = make_queue()
    assert q.push(bytes(FRAGMENT_SIZE), SendType.RELIABLE) is True
    assert len(rel) == 1
    assert rel[0][0] == 0
    assert len(rel[0]) == FRAGMENT_SIZE + 1


def test_empty_packet_is_not_sent():
    q, rel, unrel = make_queue()
    assert q.push(b"", SendType.RELIABLE) is False
    assert q.push(b"", SendType.UNRELIABLE) is False
    assert rel == [] and unrel == []


def test_unknown_send_type_sends_nothing():
    q, rel, unrel = make_queue()
    assert q.push(b"hi", 2) is False
    assert rel == [] and unrel == []


def test_unreliable_single_fragment_uses_unreliable_channel():
    q, rel, unrel = make_queue()
    assert q.push(b"hi", SendType.UNRELIABLE) is True
    assert rel == []
    assert unrel == [b"\x00hi"]


def test_small_fragment_size_splits_into_hand_derived_fragments():
    q, rel, _ = make_queue(fragment_size=3)
    assert q.push(b"ABCDEFG", SendType.RELIABLE) is True
    assert rel == [bytes.fromhex("02414243"), bytes.fromhex("01444546"), bytes.fromhex("0047")]


def test_packet_of_exactly_two_fragments_ends_with_a_full_last_fragment():
    q, rel, _ = make_queue(fragment_size=2)
    assert q.push(b"ABCD", SendType.RELIABLE) is True
    assert rel == [bytes.fromhex("014142"), bytes.fromhex("004344")]


def test_fragment_size_plus_one_splits_into_two_with_countdown():
    q, rel, _ = make_queue()
    payload = bytes(i % 256 for i in range(FRAGMENT_SIZE + 1))
    assert q.push(payload, SendType.RELIABLE) is True
    assert len(rel) == 2
    assert rel[0][0] == 1 and len(rel[0]) == 1 + FRAGMENT_SIZE
    assert rel[1] == b"\x00" + payload[-1:]
    for fragment in rel:
        assert q.on_reliable_message(fragment) is True
    assert q.read() == payload


def test_packet_of_255_fragments_is_sent_and_one_more_byte_is_dropped():
    q, rel, _ = make_queue(fragment_size=3)
    assert q.max_packet_size == 765
    assert q.push(bytes(765), SendType.RELIABLE) is True
    assert len(rel) == 255
    assert rel[0][0] == 0xFE and rel[-1][0] == 0
    rel.clear()
    assert q.push(bytes(766), SendType.RELIABLE) is False
    assert rel == []


def test_oversize_packet_is_dropped():
    q, rel, unrel = make_queue()
    assert q.push(bytes(MAX_PACKET_SIZE + 1), SendType.RELIABLE) is False
    assert rel == [] and unrel == []


def test_unreliable_packet_larger_than_a_fragment_is_dropped():
    q, rel, unrel = make_queue()
    assert q.push(bytes(FRAGMENT_SIZE + 1), SendType.UNRELIABLE) is False
    assert rel == [] and unrel == []


def test_max_message_size_sets_fragment_size_to_one_less():
    q, rel, _ = make_queue()
    assert q.update_fragment_size(65536) is True
    assert q.fragment_size == 65535
    assert q.max_packet_size == 255 * 65535
    q.push(bytes(65536), SendType.RELIABLE)
    assert [len(fragment) for fragment in rel] == [65536, 2]


def test_unknown_max_message_size_keeps_the_fragment_size():
    q, _, _ = make_queue(fragment_size=100)
    assert q.update_fragment_size(None) is True
    assert q.fragment_size == 100


def test_max_message_size_of_one_or_less_is_rejected():
    q, _, _ = make_queue(fragment_size=100)
    assert q.update_fragment_size(1) is False
    assert q.update_fragment_size(0) is False
    assert q.fragment_size == 100
    assert q.update_fragment_size(2) is True
    assert q.fragment_size == 1


def test_reassembly_delivers_nothing_until_final_fragment():
    q, _, _ = make_queue()
    assert q.on_reliable_message(b"\x01AB") is True
    assert q.peek() is None
    assert q.on_reliable_message(b"\x00CD") is True
    assert q.read() == b"ABCD"


def test_first_fragment_header_sets_the_countdown():
    q, _, _ = make_queue()
    for fragment in (b"\x05a", b"\x04b", b"\x03c", b"\x02d", b"\x01e", b"\x00f"):
        assert q.on_reliable_message(fragment) is True
    assert q.read() == b"abcdef"


def test_fragment_that_breaks_the_countdown_discards_the_partial_packet():
    q, _, _ = make_queue()
    assert q.on_reliable_message(b"\x02AB") is True
    assert q.on_reliable_message(b"\x00CD") is False
    assert q.peek() is None
    assert q.on_reliable_message(b"\x00EF") is True
    assert q.read() == b"EF"


def test_repeated_header_breaks_the_countdown():
    q, _, _ = make_queue()
    assert q.on_reliable_message(b"\x01AB") is True
    assert q.on_reliable_message(b"\x01CD") is False
    assert q.peek() is None


def test_empty_or_one_byte_reliable_message_discards_the_partial_packet():
    for message in (b"", b"\x00"):
        q, _, _ = make_queue()
        assert q.on_reliable_message(b"\x01AB") is True
        assert q.on_reliable_message(message) is False
        assert q.on_reliable_message(b"\x00CD") is True
        assert q.read() == b"CD"


def test_empty_or_one_byte_unreliable_message_is_rejected():
    q, _, _ = make_queue()
    assert q.on_unreliable_message(b"") is False
    assert q.on_unreliable_message(b"\x00") is False
    assert q.peek() is None


def test_unreliable_header_byte_is_stripped_unchecked():
    q, _, _ = make_queue()
    assert q.on_unreliable_message(b"\x07hi") is True
    assert q.read() == b"hi"


def test_channel_close_discards_the_partial_packet():
    q, _, _ = make_queue()
    q.on_reliable_message(b"\x01AB")
    q.discard_partial()
    assert q.on_reliable_message(b"\x00CD") is True
    assert q.read() == b"CD"


def test_received_packets_are_fifo():
    q, _, _ = make_queue()
    q.on_unreliable_message(b"\x00first")
    q.on_reliable_message(b"\x00second")
    assert q.read() == b"first"
    assert q.read() == b"second"
    assert q.read() is None


def test_short_read_leaves_the_rest_as_the_head_packet():
    q, _, _ = make_queue()
    q.on_unreliable_message(b"\x00" + b"0123456789")
    q.on_unreliable_message(b"\x00next")
    assert q.peek() == 10
    assert q.read(4) == b"0123"
    assert q.peek() == 6
    assert q.read(100) == b"456789"
    assert q.read() == b"next"
    assert q.peek() is None
