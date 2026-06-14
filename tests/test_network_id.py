"""NetworkID + ConnectionId — SPEC.md s3."""

import uuid

import pytest

from nethernet.network_id import U64_MAX, NetworkID, NetworkIDType, new_connection_id

# --- Parsing (SPEC.md s3.2): decimal u64 -> P2P, else UUID -> Realms, else unset ---


def test_decimal_string_parses_as_p2p():
    nid = NetworkID.parse("1234567890")
    assert nid.type is NetworkIDType.P2P
    assert nid.value == 1234567890
    assert str(nid) == "1234567890"


def test_max_u64_parses_as_p2p():
    nid = NetworkID.parse(str(U64_MAX))
    assert nid.type is NetworkIDType.P2P
    assert nid.value == U64_MAX


def test_value_above_u64_is_not_p2p_and_falls_through_to_unset():
    # Too large for a u64 -> not a decimal NetworkID, and not a UUID -> unset.
    nid = NetworkID.parse(str(U64_MAX + 1))
    assert nid.type is NetworkIDType.UNSET
    assert not nid.is_valid


def test_surrounding_whitespace_is_not_a_p2p_id():
    # SPEC.md s3.2: "unsigned decimal integer with no surrounding whitespace".
    assert NetworkID.parse(" 123").type is NetworkIDType.UNSET
    assert NetworkID.parse("123 ").type is NetworkIDType.UNSET


def test_signed_value_is_not_a_p2p_id():
    assert NetworkID.parse("-1").type is NetworkIDType.UNSET


def test_uuid_string_parses_as_realms():
    u = uuid.uuid4()
    nid = NetworkID.parse(str(u))
    assert nid.type is NetworkIDType.REALMS
    assert nid.uuid == u
    assert str(nid) == str(u)


def test_garbage_string_is_unset():
    nid = NetworkID.parse("not-a-network-id")
    assert nid.type is NetworkIDType.UNSET
    assert not nid.is_valid
    assert str(nid) == ""


# --- Round-trips ---


def test_p2p_str_parse_roundtrip():
    nid = NetworkID.p2p(9876543210)
    assert NetworkID.parse(str(nid)) == nid


def test_realms_str_parse_roundtrip():
    nid = NetworkID.realms(uuid.uuid4())
    assert NetworkID.parse(str(nid)) == nid


def test_p2p_rejects_out_of_range_value():
    with pytest.raises(ValueError):
        NetworkID.p2p(U64_MAX + 1)
    with pytest.raises(ValueError):
        NetworkID.p2p(-1)


# --- Correlation id (SPEC.md s3.4): MSW-first, four 4-hex-digit words ---


def test_correlation_id_format():
    nid = NetworkID.p2p(0x1234_5678_9ABC_DEF0)
    assert nid.correlation_id() == "<nethernet>1234-5678-9abc-def0"


def test_correlation_id_zero_pads_each_word():
    # 1234567890 == 0x0000_0000_4996_02D2
    nid = NetworkID.p2p(1234567890)
    assert nid.correlation_id() == "<nethernet>0000-0000-4996-02d2"


# --- ConnectionId / RAWNETWORKID (SPEC.md s3.3) ---


def test_new_connection_id_is_within_u64_range():
    for _ in range(1000):
        cid = new_connection_id()
        assert 0 <= cid <= U64_MAX


def test_new_connection_id_is_not_constant():
    assert len({new_connection_id() for _ in range(100)}) > 1
