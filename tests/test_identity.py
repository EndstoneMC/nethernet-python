import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from nethernet.errors import ConnectionFailed, SessionError
from nethernet.identity import (
    IdentitySigner,
    InvalidIdentity,
    PlayerSigner,
    _sign,
    build_envelope,
    canonical_json,
    decode_cpk,
    decode_jwk,
    encode_cpk,
    encode_jwk,
    extract_identity,
    fingerprint_payload,
    generate_operator_key,
    insert_identity,
    parse_envelope,
    sign_fingerprints,
    strip_identity,
    verify_fingerprints,
    verify_jwt_signature,
    verify_player_identity,
    verify_server_identity,
)

GUIDE_DIGEST = "4A:AD:B9:B1:3F:82:18:3B:54:02:12:DF:3E:5D:49:6B:19:E5:7C:AB"

SDP = (
    "v=0\r\n"
    "o=- 123456789 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    f"a=fingerprint:sha-256 {GUIDE_DIGEST}\r\n"
    "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
    "a=sctp-port:5000\r\n"
)

P256_GX = "axfR8uEsQkf4vOblY6RA8ncDfYEt6zOg9KE5RdiYwpY"
P256_GY = "T-NC4v4af5uO5-tKfA-eFivOM1drMV7Oy7ZAaDe_UfU"

RFC7515_A3_JWK = {
    "kty": "EC",
    "crv": "P-256",
    "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
    "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0",
}
RFC7515_A3_JWS = (
    "eyJhbGciOiJFUzI1NiJ9"
    ".eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6Ly9leGFt"
    "cGxlLmNvbS9pc19yb290Ijp0cnVlfQ"
    ".DtEhU3ljbEg8L38VWAfUAqOyKAM6-Xx-F4GawxaepmXFCgfTjDxw5djxLa8ISlSA"
    "pmWQxfKTUJqPP3-Kg6NU1Q"
)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_jwk(public_key):
    numbers = public_key.public_numbers()
    size = (public_key.curve.key_size + 7) // 8
    return {
        "kty": "EC",
        "crv": {"secp256r1": "P-256", "secp384r1": "P-384", "secp521r1": "P-521"}[
            public_key.curve.name
        ],
        **{axis: b64url(getattr(numbers, axis).to_bytes(size, "big")) for axis in ("x", "y")},
    }


def identity_value(sdp: str) -> str:
    (line,) = [line for line in sdp.split("\r\n") if line.startswith("a=identity:")]
    return line[len("a=identity:") :]


def player_token(cpk: object, iss: str = "auth.example") -> str:
    header = b64url(b'{"alg":"ES384"}')
    payload = b64url(json.dumps({"cpk": cpk, "iss": iss, "xuid": "1"}).encode())
    return f"{header}.{payload}.c2lnbmF0dXJl"


def envelope_sdp(sdp: str, domain: str, token: str, fingerprints: str) -> str:
    return insert_identity(sdp, build_envelope(domain, token, fingerprints))


def test_identity_line_is_appended_when_the_sdp_has_no_media_line():
    assert insert_identity("v=0\ns=-\n", "QUJD") == "v=0\r\ns=-\r\na=identity:QUJD\r\n"


def test_receiver_reads_the_first_identity_line_case_insensitively():
    first = build_envelope("first", "T1", "F1")
    second = build_envelope("second", "T2", "F2")
    sdp = f"v=0\r\nA=Identity:{first}\r\na=identity:{second}\r\nm=application 9 x\r\n"
    stripped, envelope = extract_identity(sdp)
    assert envelope.domain == "first"
    assert (envelope.token, envelope.fingerprints) == ("T1", "F1")
    assert stripped == "v=0\r\nm=application 9 x\r\n"


def test_envelope_ignores_extra_keys():
    value = base64.b64encode(
        b'{"assertion":"x","extra":1,"idp":{"domain":"d","extra":[],"protocol":"p"}}'
    ).decode()
    envelope = parse_envelope(value)
    assert (envelope.domain, envelope.protocol) == ("d", "p")
    assert (envelope.token, envelope.fingerprints) == ("", "")


@pytest.mark.parametrize(
    "outer",
    [
        b'{"idp":{"domain":"d","protocol":"p"}}',
        b'{"assertion":1,"idp":{"domain":"d","protocol":"p"}}',
        b'{"assertion":"x","idp":"d"}',
        b'{"assertion":"x","idp":{"protocol":"p"}}',
        b'{"assertion":"x","idp":{"domain":"d","protocol":0}}',
        b'["assertion"]',
        b"not json",
    ],
)
def test_envelope_without_the_required_members_is_rejected(outer):
    with pytest.raises(InvalidIdentity):
        parse_envelope(base64.b64encode(outer).decode())


def padded_envelope() -> str:
    values = (build_envelope("d" * n, "T", "F") for n in range(1, 4))
    return next(value for value in values if value.endswith("="))


@pytest.mark.parametrize(
    "mangle",
    [
        lambda v: v.rstrip("="),
        lambda v: v + "\n",
        lambda v: " " + v,
        lambda v: v[:4] + "\r\n" + v[4:],
    ],
)
def test_envelope_must_be_strict_base64(mangle):
    value = padded_envelope()
    assert parse_envelope(value).token == "T"
    with pytest.raises(InvalidIdentity):
        parse_envelope(mangle(value))


def test_strip_identity_removes_every_identity_line():
    sdp = "v=0\r\na=identity:x\r\nA=IDENTITY:y\nm=application 9 x\r\n"
    assert strip_identity(sdp) == "v=0\r\nm=application 9 x\r\n"


def test_fingerprint_entries_sort_bytewise_keep_duplicates_and_skip_empty_parts():
    sdp = (
        "a=fingerprint:sha-256 BB\r\n"
        "A=FINGERPRINT:sha-256 AA \r\n"
        "a=fingerprint:SHA-1 ZZ\r\n"
        "a=fingerprint:sha-256 BB\r\n"
        "a=fingerprint:sha-512\r\n"
        "a=fingerprint: CC\r\n"
        " a=fingerprint:sha-1 DD\r\n"
        "a=fingerprint:sha-1 a b\n"
    )
    assert fingerprint_payload(sdp) == (
        b'{"fingerprint":[{"algorithm":"SHA-1","digest":"ZZ"},'
        b'{"algorithm":"sha-1","digest":"a b"},'
        b'{"algorithm":"sha-256","digest":"AA "},'
        b'{"algorithm":"sha-256","digest":"BB"},'
        b'{"algorithm":"sha-256","digest":"BB"}]}'
    )


def test_local_sdp_without_fingerprints_yields_no_assertion():
    with pytest.raises(ConnectionFailed) as info:
        IdentitySigner(generate_operator_key()).sign("v=0\r\nm=application 9 x\r\n")
    assert info.value.error == SessionError.FAILED_TO_CREATE_IDENTITY_ASSERTION


def test_receiver_rejects_an_sdp_without_fingerprints():
    key = generate_operator_key()
    jws = sign_fingerprints(SDP, key)
    with pytest.raises(InvalidIdentity):
        verify_fingerprints("v=0\r\nm=application 9 x\r\n", jws, key.public_key())


@pytest.mark.parametrize(
    ("curve", "alg", "size"),
    [
        (ec.SECP256R1(), "ES256", 64),
        (ec.SECP384R1(), "ES384", 96),
        (ec.SECP521R1(), "ES512", 132),
    ],
)
def test_fingerprints_are_a_detached_jws_with_raw_ecdsa_signatures(curve, alg, size):
    key = ec.generate_private_key(curve)
    jws = sign_fingerprints(SDP, key)
    header, payload, signature = jws.split(".")
    assert unb64url(header) == f'{{"alg":"{alg}"}}'.encode()
    assert payload == ""
    assert len(unb64url(signature)) == size
    verify_fingerprints(SDP, jws, key.public_key())


def test_fingerprints_verify_under_an_rsa_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jws = sign_fingerprints(SDP, key)
    assert unb64url(jws.split(".")[0]) == b'{"alg":"RS256"}'
    verify_fingerprints(SDP, jws, key.public_key())


@pytest.mark.parametrize("alg", ["none", "HS256", "es256", "ES256 ", "PS256", "EdDSA"])
def test_unlisted_alg_fails(alg):
    key = ec.generate_private_key(ec.SECP256R1())
    signature = sign_fingerprints(SDP, key).split(".")[2]
    header = b64url(json.dumps({"alg": alg}).encode())
    with pytest.raises(InvalidIdentity):
        verify_fingerprints(SDP, f"{header}..{signature}", key.public_key())


def test_verify_fingerprints_rejects_tampered_sdp():
    key = generate_operator_key()
    jws = sign_fingerprints(SDP, key)
    with pytest.raises(InvalidIdentity):
        verify_fingerprints(SDP.replace("4A:AD", "FF:FF"), jws, key.public_key())


def test_verify_fingerprints_rejects_wrong_key():
    jws = sign_fingerprints(SDP, generate_operator_key())
    with pytest.raises(InvalidIdentity):
        verify_fingerprints(SDP, jws, generate_operator_key().public_key())


def test_rfc7515_es256_example_verifies_under_its_jwk():
    claims = verify_jwt_signature(RFC7515_A3_JWS, decode_jwk(RFC7515_A3_JWK))
    assert claims == {"iss": "joe", "exp": 1300819380, "http://example.com/is_root": True}


def test_rfc7515_es256_example_fails_with_a_flipped_signature():
    tampered = RFC7515_A3_JWS.replace(".DtEh", ".EtEh")
    with pytest.raises(InvalidIdentity):
        verify_jwt_signature(tampered, decode_jwk(RFC7515_A3_JWK))


def test_operator_token_is_es384_with_x5u_and_newline_terminated_json():
    key = generate_operator_key()
    token = IdentitySigner(key, clock=lambda: 1000.9).token()
    header, payload, signature = token.split(".")
    assert unb64url(header).endswith(b"}\n")
    assert unb64url(payload).endswith(b"}\n")
    assert json.loads(unb64url(header)) == {"alg": "ES384", "x5u": encode_cpk(key.public_key())}
    assert json.loads(unb64url(payload)) == {
        "cpk": encode_jwk(key.public_key()),
        "exp": 1060,
        "iat": 1000,
    }
    assert len(unb64url(signature)) == 96
    assert verify_jwt_signature(token, key.public_key())["exp"] == 1060


def test_operator_key_must_be_p384():
    with pytest.raises(InvalidIdentity):
        IdentitySigner(ec.generate_private_key(ec.SECP256R1()))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('"\\', '"\\"\\\\"'),
        ("\b\t\n\f\r", '"\\b\\t\\n\\f\\r"'),
        ("\x01\x1a\x1f", '"\\u0001\\u001A\\u001F"'),
        ("/\x7fé中", '"/\x7fé中"'),
    ],
)
def test_json_strings_escape_only_quote_backslash_and_control_bytes(value, expected):
    assert canonical_json(value) == expected


def test_json_is_compact_with_sorted_keys():
    value = {"b": [1, True, None], "a": {"z": False, "y": "é"}}
    assert canonical_json(value) == '{"a":{"y":"é","z":false},"b":[1,true,null]}'


def test_operator_jwk_is_sorted_and_has_no_kid():
    jwk = encode_jwk(ec.derive_private_key(1, ec.SECP256R1()).public_key())
    assert json.dumps(jwk, separators=(",", ":")) == (
        f'{{"crv":"P-256","kty":"EC","x":"{P256_GX}","y":"{P256_GY}"}}'
    )


def test_operator_assertion_is_self_signed_under_domain_self():
    key = generate_operator_key()
    signed = IdentitySigner(key).sign(SDP)
    outer = json.loads(base64.b64decode(identity_value(signed)))
    assert outer["idp"] == {"domain": "self", "protocol": "default"}
    assert list(json.loads(outer["assertion"])) == ["fingerprints", "token"]

    stripped, identity = verify_server_identity(signed)
    assert stripped == strip_identity(signed)
    assert identity.domain == "self"
    assert identity.claims["cpk"] == encode_jwk(key.public_key())
    assert identity.public_key.public_numbers() == key.public_key().public_numbers()
    assert (
        identity.key_pin
        == base64.b64encode(
            key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).decode()
    )


def test_operator_token_expiry_is_never_checked():
    signed = IdentitySigner(generate_operator_key(), clock=lambda: 0.0).sign(SDP)
    _, identity = verify_server_identity(signed)
    assert identity.claims["exp"] == 60


def test_operator_cpk_as_a_der_string_is_rejected():
    key = generate_operator_key()
    header = b64url(b'{"alg":"ES384"}')
    payload = b64url(json.dumps({"cpk": encode_cpk(key.public_key())}).encode())
    signature = b64url(_sign(key, "ES384", f"{header}.{payload}".encode()))
    sdp = envelope_sdp(SDP, "self", f"{header}.{payload}.{signature}", sign_fingerprints(SDP, key))
    with pytest.raises(InvalidIdentity, match="malformed cpk claim"):
        verify_server_identity(sdp)


def test_operator_token_alg_must_match_the_key():
    key = ec.derive_private_key(5, ec.SECP384R1())
    token = IdentitySigner(key).token()
    _, payload, signature = token.split(".")
    forged = ".".join([b64url(b'{"alg":"ES256"}'), payload, signature])
    sdp = envelope_sdp(SDP, "self", forged, sign_fingerprints(SDP, key))
    with pytest.raises(InvalidIdentity, match="does not match"):
        verify_server_identity(sdp)


def test_operator_token_signed_by_another_key_is_rejected():
    key = generate_operator_key()
    other = generate_operator_key()
    other_token = IdentitySigner(other).token()
    _, payload, signature = other_token.split(".")
    claims = json.loads(unb64url(payload))
    claims["cpk"] = encode_jwk(key.public_key())
    swapped = f"{other_token.split('.')[0]}.{b64url(json.dumps(claims).encode())}.{signature}"
    sdp = envelope_sdp(SDP, "self", swapped, sign_fingerprints(SDP, key))
    with pytest.raises(InvalidIdentity, match="self-signature"):
        verify_server_identity(sdp)


def test_operator_fingerprints_resigned_by_another_key_are_rejected():
    key = generate_operator_key()
    token = IdentitySigner(key).token()
    attacker = sign_fingerprints(SDP, generate_operator_key())
    with pytest.raises(InvalidIdentity, match="fingerprint signature verification failed"):
        verify_server_identity(envelope_sdp(SDP, "self", token, attacker))


def test_verify_server_identity_requires_an_assertion():
    with pytest.raises(InvalidIdentity):
        verify_server_identity(SDP)


@pytest.mark.parametrize("missing", ["token", "fingerprints"])
def test_assertion_needs_a_non_empty_token_and_fingerprints(missing):
    key = generate_operator_key()
    parts = {"token": IdentitySigner(key).token(), "fingerprints": sign_fingerprints(SDP, key)}
    parts[missing] = ""
    with pytest.raises(InvalidIdentity):
        verify_server_identity(envelope_sdp(SDP, "self", parts["token"], parts["fingerprints"]))


@pytest.mark.parametrize(
    "token",
    ["a.b", "a.b.c.d", f"{b64url(b'[]')}.{b64url(b'{}')}.c2ln", f"{b64url(b'{}')}.bm90.c2ln"],
)
def test_token_needs_three_segments_with_json_object_header_and_payload(token):
    key = generate_operator_key()
    with pytest.raises(InvalidIdentity):
        verify_player_identity(envelope_sdp(SDP, "d", token, sign_fingerprints(SDP, key)))


def test_player_assertion_carries_the_token_unchanged_and_verifies_without_its_signature():
    key = ec.generate_private_key(ec.SECP384R1())
    token = player_token(encode_cpk(key.public_key()), iss="auth.example")
    signed = PlayerSigner(token, key).sign(SDP)
    outer = json.loads(base64.b64decode(identity_value(signed)))
    assert json.loads(outer["assertion"])["token"] == token
    assert outer["idp"]["domain"] == "auth.example"

    stripped, identity = verify_player_identity(signed)
    assert stripped == strip_identity(signed)
    assert identity.domain == "auth.example"
    assert identity.token == token
    assert identity.claims["xuid"] == "1"


@pytest.mark.parametrize(
    "token",
    [
        "not-a-token",
        "a.b.c",
        ".".join([b64url(b"{}"), b64url(b'{"iss":7}'), "c2ln"]),
        ".".join([b64url(b"{}"), b64url(b"{}"), "c2ln"]),
    ],
)
def test_player_domain_is_empty_without_a_string_iss(token):
    signed = PlayerSigner(token, generate_operator_key()).sign(SDP)
    assert json.loads(base64.b64decode(identity_value(signed)))["idp"]["domain"] == ""


def test_player_cpk_must_be_a_string_key():
    key = generate_operator_key()
    signed = PlayerSigner(player_token(encode_jwk(key.public_key())), key).sign(SDP)
    with pytest.raises(InvalidIdentity, match="malformed cpk claim"):
        verify_player_identity(signed)


@pytest.mark.parametrize(("token", "has_key"), [("", True), ("t.o.k", False)])
def test_player_without_token_or_key_makes_no_assertion(token, has_key):
    key = generate_operator_key() if has_key else None
    with pytest.raises(ConnectionFailed) as info:
        PlayerSigner(token, key).sign(SDP)
    assert info.value.error == SessionError.FAILED_TO_CREATE_IDENTITY_ASSERTION


def test_cpk_der_roundtrip():
    key = generate_operator_key()
    decoded = decode_cpk(encode_cpk(key.public_key()))
    assert decoded.public_numbers() == key.public_key().public_numbers()
    with pytest.raises(InvalidIdentity):
        decode_cpk("not base64!")


@pytest.mark.parametrize(
    ("curve", "scalar"), [(ec.SECP256R1(), 43), (ec.SECP384R1(), 176), (ec.SECP521R1(), 1)]
)
def test_decode_jwk_roundtrip(curve, scalar):
    public_key = ec.derive_private_key(scalar, curve).public_key()
    decoded = decode_jwk(public_jwk(public_key))
    assert decoded.public_numbers() == public_key.public_numbers()
    assert encode_cpk(decoded) == encode_cpk(public_key)
    assert encode_jwk(public_key) == public_jwk(public_key)


def test_decode_jwk_rejects_missing_member():
    jwk = public_jwk(generate_operator_key().public_key())
    del jwk["x"]
    with pytest.raises(InvalidIdentity, match="^malformed cpk claim$"):
        decode_jwk(jwk)


@pytest.mark.parametrize(
    "changes",
    [
        {"kty": "RSA"},
        {"kty": "OKP"},
        {"kty": "ec"},
        {"kty": None},
        {"crv": "P-224"},
        {"crv": "p-256"},
        {"crv": []},
        {"x": 1},
        {"x": ""},
        {"x": "="},
        {"x": "A"},
        {"x": "A" * 43, "y": "A" * 43},
    ],
)
def test_decode_jwk_rejects_malformed_jwk(changes):
    jwk = {**public_jwk(ec.derive_private_key(43, ec.SECP256R1()).public_key()), **changes}
    with pytest.raises(InvalidIdentity, match="^malformed cpk claim$"):
        decode_jwk(jwk)


def test_decode_jwk_reads_an_rsa_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    numbers = key.public_numbers()
    jwk = {
        "kty": "RSA",
        "n": b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": "AQAB",
    }
    assert decode_jwk(jwk).public_numbers() == numbers


def detached_jws(sdp: str, key, alg: str) -> str:
    header = b64url(canonical_json({"alg": alg}).encode())
    payload = b64url(fingerprint_payload(sdp))
    return f"{header}..{b64url(_sign(key, alg, f'{header}.{payload}'.encode()))}"


def padded_to(value: str, size: int) -> str:
    return value + "=" + "A" * (size - len(value) - 1)


@pytest.mark.parametrize(("size", "accepted"), [(16384, True), (16385, False)])
def test_fingerprints_jws_may_be_16384_bytes(size, accepted):
    key = generate_operator_key()
    jws = padded_to(sign_fingerprints(SDP, key), size)
    assert len(jws) == size
    if accepted:
        verify_fingerprints(SDP, jws, key.public_key())
    else:
        with pytest.raises(InvalidIdentity, match="too large"):
            verify_fingerprints(SDP, jws, key.public_key())


@pytest.mark.parametrize(("size", "accepted"), [(16384, True), (16385, False)])
def test_token_may_be_16384_bytes(size, accepted):
    key = generate_operator_key()
    token = padded_to(IdentitySigner(key).token(), size)
    assert len(token) == size
    sdp = envelope_sdp(SDP, "self", token, sign_fingerprints(SDP, key))
    if accepted:
        verify_server_identity(sdp)
    else:
        with pytest.raises(InvalidIdentity, match="too large"):
            verify_server_identity(sdp)


def to_standard_alphabet(value: str) -> str:
    return value.replace("-", "+").replace("_", "/")


@pytest.mark.parametrize(
    "mangle",
    [
        lambda v: v + "==",
        lambda v: v + "=garbage",
        lambda v: v + " trailing",
        lambda v: v + "\n",
        lambda v: v + "!x",
        to_standard_alphabet,
    ],
)
def test_base64url_values_decode_leniently(mangle):
    key = generate_operator_key()
    header, payload, signature = IdentitySigner(key).token().split(".")
    jwk = encode_jwk(key.public_key())
    jwk = {**jwk, "x": mangle(jwk["x"]), "y": mangle(jwk["y"])}
    assert decode_jwk(jwk).public_numbers() == key.public_key().public_numbers()
    token = f"{header}.{payload}.{mangle(signature)}"
    assert verify_jwt_signature(token, key.public_key())["cpk"] == encode_jwk(key.public_key())
    fp_header, _, fp_signature = sign_fingerprints(SDP, key).split(".")
    verify_fingerprints(SDP, f"{fp_header}..{mangle(fp_signature)}", key.public_key())


def test_a_token_header_and_payload_decode_leniently_under_a_signature_over_them():
    key = generate_operator_key()
    header = b64url(b'{"alg":"ES384"}') + "=="
    claims = {"cpk": encode_jwk(key.public_key()), "iat": 1}
    payload = to_standard_alphabet(b64url(canonical_json(claims).encode() + b"\xff\xfe")) + "!"
    signature = b64url(_sign(key, "ES384", f"{header}.{payload}".encode()))
    sdp = envelope_sdp(SDP, "self", f"{header}.{payload}.{signature}", sign_fingerprints(SDP, key))
    with pytest.raises(InvalidIdentity, match="malformed JSON"):
        verify_server_identity(sdp)
    payload = to_standard_alphabet(b64url(canonical_json(claims).encode())) + "=!"
    signature = b64url(_sign(key, "ES384", f"{header}.{payload}".encode()))
    sdp = envelope_sdp(SDP, "self", f"{header}.{payload}.{signature}", sign_fingerprints(SDP, key))
    assert verify_server_identity(sdp)[1].claims == claims


def test_base64url_drops_a_lone_last_digit_and_the_spare_bits_of_the_last_group():
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    key = generate_operator_key()
    jwk = encode_jwk(key.public_key())
    assert len(jwk["y"]) % 4 == 0
    jwk["y"] += "A"
    assert decode_jwk(jwk).public_numbers() == key.public_key().public_numbers()
    short = ec.derive_private_key(43, ec.SECP256R1()).public_key()
    jwk = public_jwk(short)
    assert len(jwk["y"]) % 4 == 3
    jwk["y"] = jwk["y"][:-1] + alphabet[alphabet.index(jwk["y"][-1]) | 0b11]
    assert decode_jwk(jwk).public_numbers() == short.public_numbers()


@pytest.mark.parametrize("value", ["", "=", "A", "=AAAA", "!AAAA"])
def test_a_base64url_value_with_no_bytes_fails(value):
    jwk = {**public_jwk(ec.derive_private_key(43, ec.SECP256R1()).public_key()), "y": value}
    with pytest.raises(InvalidIdentity, match="^malformed cpk claim$"):
        decode_jwk(jwk)


def test_decode_jwk_ignores_d_and_unlisted_members():
    key = generate_operator_key()
    jwk = {**encode_jwk(key.public_key()), "d": "AQ", "kid": 7, "alg": "none", "use": "enc"}
    assert decode_jwk(jwk).public_numbers() == key.public_key().public_numbers()


def test_decode_jwk_takes_short_and_zero_padded_coordinates():
    public_key = ec.derive_private_key(379, ec.SECP256R1()).public_key()
    numbers = public_key.public_numbers()
    assert numbers.x.bit_length() <= 248
    for width in (31, 40):
        jwk = {**public_jwk(public_key), "x": b64url(numbers.x.to_bytes(width, "big"))}
        assert decode_jwk(jwk).public_numbers() == numbers


def test_decode_jwk_rejects_a_coordinate_at_or_above_the_field_prime():
    prime = 2**256 - 2**224 + 2**192 + 2**96 - 1
    public_key = ec.derive_private_key(43, ec.SECP256R1()).public_key()
    numbers = public_key.public_numbers()
    jwk = {**public_jwk(public_key), "x": b64url((numbers.x + prime).to_bytes(33, "big"))}
    with pytest.raises(InvalidIdentity, match="^malformed cpk claim$"):
        decode_jwk(jwk)


def rsa_jwk(public_key, n_width=None, e="AQAB"):
    n = public_key.public_numbers().n
    width = n_width or (n.bit_length() + 7) // 8
    return {"kty": "RSA", "n": b64url(n.to_bytes(width, "big")), "e": e}


RSA_1024 = rsa.generate_private_key(public_exponent=65537, key_size=1024)
RSA_2048 = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.mark.parametrize("e", ["AQAB", "AAEAAQ", "AAAAAQAB"])
def test_decode_jwk_takes_rsa_e_with_leading_zeros(e):
    public_key = RSA_2048.public_key()
    assert decode_jwk(rsa_jwk(public_key, e=e)).public_numbers() == public_key.public_numbers()


@pytest.mark.parametrize(
    "jwk",
    [
        rsa_jwk(RSA_2048.public_key(), n_width=257),
        rsa_jwk(RSA_2048.public_key(), e=""),
        {"kty": "RSA", "n": b64url(bytes([0xC1]) + bytes(190) + b"\x01"), "e": "AQAB"},
        {"kty": "RSA", "e": "AQAB"},
        {"kty": "RSA", "n": 5, "e": "AQAB"},
    ],
)
def test_decode_jwk_rejects_an_rsa_modulus_outside_128_256_or_512_bytes(jwk):
    with pytest.raises(InvalidIdentity, match="^malformed cpk claim$"):
        decode_jwk(jwk)


def test_decode_jwk_counts_leading_zero_bytes_of_n():
    public_key = rsa.RSAPublicNumbers(65537, RSA_1024.public_key().public_numbers().n >> 8)
    padded = {"kty": "RSA", "n": b64url(public_key.n.to_bytes(128, "big")), "e": "AQAB"}
    assert decode_jwk(padded).public_numbers() == public_key
    unpadded = {"kty": "RSA", "n": b64url(public_key.n.to_bytes(127, "big")), "e": "AQAB"}
    with pytest.raises(InvalidIdentity, match="^malformed cpk claim$"):
        decode_jwk(unpadded)


def rsa_operator_sdp(key, alg: str, token_alg: str | None = None) -> str:
    header = b64url(canonical_json({"alg": token_alg or alg}).encode())
    payload = b64url(canonical_json({"cpk": rsa_jwk(key.public_key())}).encode())
    signature = b64url(_sign(key, alg, f"{header}.{payload}".encode()))
    token = f"{header}.{payload}.{signature}"
    return envelope_sdp(SDP, "self", token, detached_jws(SDP, key, alg))


@pytest.mark.parametrize("alg", ["RS256", "RS384", "RS512"])
def test_an_rsa_operator_cpk_verifies_with_every_rs_alg(alg):
    _, identity = verify_server_identity(rsa_operator_sdp(RSA_2048, alg))
    assert identity.public_key.public_numbers() == RSA_2048.public_key().public_numbers()


@pytest.mark.parametrize("token_alg", ["ES256", "ES384", "PS256", "none"])
def test_an_rsa_operator_token_rejects_a_non_rs_alg(token_alg):
    with pytest.raises(InvalidIdentity, match="does not match"):
        verify_server_identity(rsa_operator_sdp(RSA_2048, "RS256", token_alg))


@pytest.mark.parametrize("alg", ["RS256", "RS384", "RS512"])
@pytest.mark.parametrize("key", [RSA_1024, RSA_2048], ids=["1024", "2048"])
def test_an_rsa_player_cpk_verifies_with_every_rs_alg(key, alg):
    token = player_token(encode_cpk(key.public_key()))
    sdp = envelope_sdp(SDP, "auth.example", token, detached_jws(SDP, key, alg))
    _, identity = verify_player_identity(sdp)
    assert identity.public_key.public_numbers() == key.public_key().public_numbers()


@pytest.mark.parametrize("alg", ["ES256", "ES384"])
def test_rsa_fingerprints_reject_an_es_alg(alg):
    signature = sign_fingerprints(SDP, RSA_2048).split(".")[2]
    header = b64url(canonical_json({"alg": alg}).encode())
    with pytest.raises(InvalidIdentity):
        verify_fingerprints(SDP, f"{header}..{signature}", RSA_2048.public_key())


def test_player_cpk_rejects_an_rsa_modulus_outside_1024_2048_or_4096_bits():
    key = rsa.generate_private_key(public_exponent=65537, key_size=1536)
    with pytest.raises(InvalidIdentity, match="malformed cpk claim"):
        decode_cpk(encode_cpk(key.public_key()))


def test_player_cpk_rejects_a_curve_outside_p256_p384_and_p521():
    key = ec.generate_private_key(ec.SECP256K1())
    with pytest.raises(InvalidIdentity, match="malformed cpk claim"):
        decode_cpk(encode_cpk(key.public_key()))


@pytest.mark.parametrize("suffix", ["", "-tail", "_tail", " tail", "\n", "=tail"])
def test_player_cpk_is_standard_base64_ended_by_any_other_byte(suffix):
    public_key = ec.derive_private_key(1, ec.SECP384R1()).public_key()
    cpk = encode_cpk(public_key)
    assert decode_cpk(cpk.rstrip("=") + suffix).public_numbers() == public_key.public_numbers()


def test_player_cpk_reads_an_underscore_as_the_end():
    cpk = encode_cpk(ec.derive_private_key(1, ec.SECP384R1()).public_key())
    assert "/" in cpk
    with pytest.raises(InvalidIdentity, match="malformed cpk claim"):
        decode_cpk(cpk.replace("/", "_"))


def test_a_deeply_nested_envelope_is_invalid():
    with pytest.raises(InvalidIdentity):
        parse_envelope(base64.b64encode(b"[" * 100000).decode())


def test_a_deeply_nested_assertion_reads_as_empty():
    outer = canonical_json(
        {"assertion": "[" * 100000, "idp": {"domain": "d", "protocol": "default"}}
    )
    envelope = parse_envelope(base64.b64encode(outer.encode()).decode())
    assert (envelope.token, envelope.fingerprints) == ("", "")


def test_a_deeply_nested_token_header_is_invalid():
    key = generate_operator_key()
    token = f"{b64url(b'[' * 10000)}.{b64url(b'{}')}.c2ln"
    with pytest.raises(InvalidIdentity):
        verify_player_identity(envelope_sdp(SDP, "d", token, sign_fingerprints(SDP, key)))


@pytest.mark.parametrize(
    "key", [None, ec.generate_private_key(ec.SECP256R1()), RSA_2048], ids=["new", "p256", "rsa"]
)
def test_a_self_signed_player_verifies_without_an_xbox_chain(key):
    signer = PlayerSigner.self_signed(key)
    _, identity = verify_player_identity(signer.sign(SDP))
    assert identity.domain == "self"
    assert identity.claims == {"cpk": encode_cpk(identity.public_key), "iss": "self"}
    if key is not None:
        assert identity.public_key.public_numbers() == key.public_key().public_numbers()
    assert verify_jwt_signature(identity.token, identity.public_key) == identity.claims


def jws_with_signature(header: str, signature: bytes) -> str:
    return f"{header}..{b64url(signature)}"


def raw_signature_halves(key) -> tuple[str, int, int]:
    header, _, signature = sign_fingerprints(SDP, key).split(".")
    raw = unb64url(signature)
    return header, int.from_bytes(raw[:48], "big"), int.from_bytes(raw[48:], "big")


@pytest.mark.parametrize("width", [48, 49, 60, 200])
@pytest.mark.parametrize("trailing", [b"", b"\x07"], ids=["even", "odd"])
def test_an_ecdsa_signature_of_any_length_verifies_when_its_halves_hold_r_and_s(width, trailing):
    key = generate_operator_key()
    header, r, s = raw_signature_halves(key)
    signature = r.to_bytes(width, "big") + s.to_bytes(width, "big") + trailing
    verify_fingerprints(SDP, jws_with_signature(header, signature), key.public_key())


@pytest.mark.parametrize("signature", [b"\x01", b"\x00\x00\x00"])
def test_an_ecdsa_signature_under_2_bytes_or_with_zero_halves_fails(signature):
    key = generate_operator_key()
    header, _, _ = raw_signature_halves(key)
    with pytest.raises(InvalidIdentity):
        verify_fingerprints(SDP, jws_with_signature(header, signature), key.public_key())


@pytest.mark.parametrize("width", [48, 60])
def test_an_ecdsa_signature_with_wrong_half_values_fails(width):
    key = generate_operator_key()
    header, r, s = raw_signature_halves(key)
    signature = s.to_bytes(width, "big") + r.to_bytes(width, "big")
    with pytest.raises(InvalidIdentity):
        verify_fingerprints(SDP, jws_with_signature(header, signature), key.public_key())


def test_an_ecdsa_signature_split_off_centre_fails():
    key = generate_operator_key()
    header, r, s = raw_signature_halves(key)
    signature = r.to_bytes(49, "big") + s.to_bytes(48, "big") + b"\x00"
    with pytest.raises(InvalidIdentity):
        verify_fingerprints(SDP, jws_with_signature(header, signature), key.public_key())


def test_a_token_signature_padded_with_leading_zeros_verifies():
    key = generate_operator_key()
    header, payload, signature = IdentitySigner(key).token().split(".")
    raw = unb64url(signature)
    r, s = int.from_bytes(raw[:48], "big"), int.from_bytes(raw[48:], "big")
    padded = b64url(r.to_bytes(64, "big") + s.to_bytes(64, "big") + b"\x01")
    claims = verify_jwt_signature(f"{header}.{payload}.{padded}", key.public_key())
    assert claims["cpk"] == encode_jwk(key.public_key())


@pytest.mark.parametrize("middle", [".payload.", ".", ".a.b."])
def test_fingerprints_jws_reads_its_first_and_last_segments(middle):
    key = generate_operator_key()
    header, _, signature = sign_fingerprints(SDP, key).split(".")
    verify_fingerprints(SDP, f"{header}{middle}{signature}", key.public_key())


@pytest.mark.parametrize("e", ["AA", "AQ", "Ag", "BA"])
def test_decode_jwk_rejects_an_rsa_exponent_cryptography_refuses(e):
    with pytest.raises(InvalidIdentity, match="^malformed cpk claim$"):
        decode_jwk(rsa_jwk(RSA_2048.public_key(), e=e))


def test_decode_jwk_takes_an_rsa_exponent_of_3():
    numbers = decode_jwk(rsa_jwk(RSA_2048.public_key(), e="Aw")).public_numbers()
    assert numbers.e == 3


def test_invalid_identity_carries_identity_not_allowed():
    with pytest.raises(InvalidIdentity) as info:
        verify_server_identity(SDP)
    assert info.value.error == SessionError.IDENTITY_NOT_ALLOWED
