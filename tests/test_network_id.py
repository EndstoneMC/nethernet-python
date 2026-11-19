import uuid

import pytest

from nethernet.network_id import U64_MAX, NetworkID, NetworkIDType, new_connection_id

DASHED = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
UNDASHED = "6ba7b8109dad11d180b400c04fd430c8"
NIL = "00000000-0000-0000-0000-000000000000"


def test_empty_string_is_unset():
    nid = NetworkID.parse("")
    assert nid.type is NetworkIDType.UNSET
    assert str(nid) == ""


def test_decimal_string_parses_as_p2p():
    nid = NetworkID.parse("1234567890")
    assert nid.type is NetworkIDType.P2P
    assert nid.value == 1234567890
    assert str(nid) == "1234567890"


def test_p2p_zero_prints_as_zero():
    assert str(NetworkID.parse("0")) == "0"
    assert str(NetworkID.p2p(0)) == "0"


def test_p2p_accepts_leading_zeros_and_prints_without_them():
    nid = NetworkID.parse("000042")
    assert nid == NetworkID.p2p(42)
    assert str(nid) == "42"


def test_max_u64_parses_as_p2p():
    nid = NetworkID.parse("18446744073709551615")
    assert nid.type is NetworkIDType.P2P
    assert nid.value == U64_MAX


def test_decimal_above_u64_that_is_not_32_digits_is_unset():
    assert NetworkID.parse("18446744073709551616").type is NetworkIDType.UNSET


@pytest.mark.parametrize("text", ["123abc", " 123", "123 ", "+1", "-1", "１２３"])
def test_text_that_is_not_all_ascii_digits_is_not_p2p(text):
    assert NetworkID.parse(text).type is NetworkIDType.UNSET


def test_thirty_two_digit_decimal_overflows_and_parses_as_realms():
    nid = NetworkID.parse("1" * 32)
    assert nid.type is NetworkIDType.REALMS
    assert str(nid) == "11111111-1111-1111-1111-111111111111"


def test_very_long_digit_string_is_unset_without_raising():
    assert NetworkID.parse("9" * 5000).type is NetworkIDType.UNSET


def test_very_long_zero_padded_digit_string_is_p2p():
    assert NetworkID.parse("0" * 5000 + "7") == NetworkID.p2p(7)


def test_dashed_uuid_parses_as_realms():
    nid = NetworkID.parse(DASHED)
    assert nid.type is NetworkIDType.REALMS
    assert nid.uuid == uuid.UUID(int=0x6BA7B8109DAD11D180B400C04FD430C8)
    assert str(nid) == DASHED


def test_upper_case_uuid_parses_and_prints_lower_case():
    assert str(NetworkID.parse(DASHED.upper())) == DASHED


def test_thirty_two_hex_digits_parse_to_the_same_uuid_as_the_dashed_form():
    nid = NetworkID.parse(UNDASHED)
    assert nid.type is NetworkIDType.REALMS
    assert nid == NetworkID.parse(DASHED)
    assert str(nid) == DASHED


@pytest.mark.parametrize(
    "text",
    [
        "{" + DASHED + "}",
        "urn:uuid:" + DASHED,
        "6ba7b810-9dad11d1-80b4-00c04fd430c8-",
        "6ba7b810-9dad-11d1-80b4-00c04fd430cg",
        "6ba7b8109dad11d180b400c04fd430c",
        "6ba7b8109dad11d180b400c04fd430c8a",
        "6ba7b810-9dad-11d1-80b4-00c04fd430c",
        "not-a-network-id",
    ],
)
def test_other_uuid_forms_and_garbage_are_unset(text):
    nid = NetworkID.parse(text)
    assert nid.type is NetworkIDType.UNSET
    assert not nid.is_valid


def test_unset_is_invalid():
    assert not NetworkID.unset().is_valid


def test_p2p_zero_parses_but_is_invalid():
    nid = NetworkID.parse("0")
    assert nid.type is NetworkIDType.P2P
    assert not nid.is_valid


def test_nil_uuid_parses_but_is_invalid():
    nid = NetworkID.parse(NIL)
    assert nid.type is NetworkIDType.REALMS
    assert not nid.is_valid


def test_nonzero_p2p_and_realms_are_valid():
    assert NetworkID.parse("1").is_valid
    assert NetworkID.parse(DASHED).is_valid


def test_ids_of_different_forms_with_the_same_value_differ():
    assert NetworkID.p2p(1) != NetworkID.realms(uuid.UUID(int=1))


def test_unset_equals_unset():
    assert NetworkID.parse("") == NetworkID.unset()
    assert NetworkID.parse("garbage") == NetworkID.unset()


def test_p2p_rejects_out_of_range_value():
    with pytest.raises(ValueError):
        NetworkID.p2p(U64_MAX + 1)
    with pytest.raises(ValueError):
        NetworkID.p2p(-1)


def test_p2p_correlation_id_is_four_hex_words_most_significant_first():
    nid = NetworkID.p2p(0x1234_5678_9ABC_DEF0)
    assert nid.correlation_id() == "<nethernet>1234-5678-9abc-def0"


def test_p2p_correlation_id_zero_pads_each_word():
    assert NetworkID.p2p(1234567890).correlation_id() == "<nethernet>0000-0000-4996-02d2"


def test_realms_correlation_id_is_the_uuid_string():
    assert NetworkID.parse(DASHED).correlation_id() == "<nethernet>" + DASHED


@pytest.mark.parametrize("nid", [NetworkID.unset(), NetworkID.p2p(0), NetworkID.parse(NIL)])
def test_invalid_id_correlation_id_is_empty(nid):
    assert nid.correlation_id() == ""


def test_p2p_json_is_a_number():
    assert NetworkID.p2p(1234567890).to_json() == 1234567890


def test_realms_json_is_its_string_form():
    assert NetworkID.parse(DASHED.upper()).to_json() == DASHED


def test_unset_json_is_null():
    assert NetworkID.unset().to_json() is None


def test_json_number_reads_as_p2p():
    assert NetworkID.from_json(42) == NetworkID.p2p(42)
    assert NetworkID.from_json(0) == NetworkID.p2p(0)
    assert NetworkID.from_json(18446744073709551615) == NetworkID.p2p(U64_MAX)


def test_json_string_parses_as_a_string_form():
    assert NetworkID.from_json("42") == NetworkID.p2p(42)
    assert NetworkID.from_json(DASHED) == NetworkID.realms(
        uuid.UUID(int=0x6BA7B8109DAD11D180B400C04FD430C8)
    )
    assert NetworkID.from_json("") == NetworkID.unset()
    assert NetworkID.from_json("garbage") == NetworkID.unset()


def test_json_negative_integer_wraps_to_u64():
    assert NetworkID.from_json(-1) == NetworkID.p2p(18446744073709551615)
    assert NetworkID.from_json(-2) == NetworkID.p2p(18446744073709551614)


def test_json_bool_reads_as_a_number():
    assert NetworkID.from_json(True) == NetworkID.p2p(1)
    assert NetworkID.from_json(False) == NetworkID.p2p(0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1.5, 1), (1.0, 1), (5e9, 705032704), (-1.0, 4294967295), (-1.5, 4294967295)],
)
def test_json_real_truncates_and_keeps_its_low_32_bits(value, expected):
    assert NetworkID.from_json(value) == NetworkID.p2p(expected)


@pytest.mark.parametrize("value", [2**64, -(2**63) - 1, 1e30])
def test_json_number_beyond_the_64_bit_range_reads_as_p2p_zero(value):
    assert NetworkID.from_json(value) == NetworkID.p2p(0)


@pytest.mark.parametrize("value", [None, [1], {"id": 1}])
def test_json_null_array_or_object_reads_as_unset(value):
    assert NetworkID.from_json(value) == NetworkID.unset()


def test_new_connection_id_is_within_u64_range():
    for _ in range(1000):
        assert 0 <= new_connection_id() <= U64_MAX


def test_new_connection_id_is_not_constant():
    assert len({new_connection_id() for _ in range(100)}) > 1
