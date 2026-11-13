"""Identity assertion mechanics — Onboarding Guide s5 (envelope, canonical JSON, JWS, JWT)."""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from nethernet.identity import (
    IdentitySigner,
    InvalidIdentity,
    build_envelope,
    cpk_digest,
    decode_cpk,
    encode_cpk,
    extract_identity,
    fingerprint_payload,
    generate_operator_key,
    insert_identity,
    sign_fingerprints,
    strip_identity,
    verify_fingerprints,
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


def public_jwk(public_key):
    numbers = public_key.public_numbers()
    size = (public_key.curve.key_size + 7) // 8
    return {
        "kty": "EC",
        "crv": {"secp256r1": "P-256", "secp384r1": "P-384", "secp521r1": "P-521"}[
            public_key.curve.name
        ],
        **{
            axis: base64.urlsafe_b64encode(getattr(numbers, axis).to_bytes(size, "big"))
            .decode()
            .rstrip("=")
            for axis in ("x", "y")
        },
    }


def test_fingerprint_payload_matches_guide_example():
    # Byte-exact canonical JSON from Guide s5.1 step 4.
    expected = (
        '{"fingerprint":[{"algorithm":"sha-256","digest":"' + GUIDE_DIGEST + '"}]}'
    ).encode()
    assert fingerprint_payload(SDP) == expected


def test_fingerprint_payload_collects_media_level_lines_in_order():
    sdp = (
        "v=0\r\n"
        "a=fingerprint:sha-256 AA:BB\r\n"
        "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
        "a=fingerprint:sha-384 CC:DD\r\n"
    )
    expected = (
        b'{"fingerprint":[{"algorithm":"sha-256","digest":"AA:BB"},'
        b'{"algorithm":"sha-384","digest":"CC:DD"}]}'
    )
    assert fingerprint_payload(sdp) == expected


@pytest.mark.parametrize("curve", [ec.SECP256R1(), ec.SECP384R1(), ec.SECP521R1()])
def test_sign_and_verify_fingerprints_roundtrip(curve):
    key = ec.generate_private_key(curve)
    jws = sign_fingerprints(SDP, key)
    header, payload, signature = jws.split(".")
    assert payload == ""  # detached: payload omitted (RFC 7515 Appendix F)
    assert header and signature
    verify_fingerprints(SDP, jws, key.public_key())


def test_verify_fingerprints_rejects_tampered_sdp():
    key = generate_operator_key()
    jws = sign_fingerprints(SDP, key)
    tampered = SDP.replace("4A:AD", "FF:FF")
    with pytest.raises(InvalidIdentity):
        verify_fingerprints(tampered, jws, key.public_key())


def test_verify_fingerprints_rejects_wrong_key():
    jws = sign_fingerprints(SDP, generate_operator_key())
    with pytest.raises(InvalidIdentity):
        verify_fingerprints(SDP, jws, generate_operator_key().public_key())


def test_cpk_roundtrip():
    key = generate_operator_key()
    decoded = decode_cpk(encode_cpk(key.public_key()))
    assert decoded.public_numbers() == key.public_key().public_numbers()
    with pytest.raises(InvalidIdentity):
        decode_cpk("not base64!")


@pytest.mark.parametrize(
    ("curve", "scalar"), [(ec.SECP256R1(), 43), (ec.SECP384R1(), 176), (ec.SECP521R1(), 1)]
)
def test_decode_cpk_jwk_roundtrip(curve, scalar):
    # These keys cover leading-zero coordinates on each supported curve.
    public_key = ec.derive_private_key(scalar, curve).public_key()
    decoded = decode_cpk(public_jwk(public_key))
    assert decoded.public_numbers() == public_key.public_numbers()
    assert cpk_digest(decoded) == cpk_digest(decode_cpk(encode_cpk(public_key)))


def test_decode_cpk_rejects_missing_jwk_member():
    jwk = public_jwk(generate_operator_key().public_key())
    del jwk["x"]
    with pytest.raises(InvalidIdentity, match="^malformed cpk claim$"):
        decode_cpk(jwk)


@pytest.mark.parametrize(
    "changes",
    [
        {"kty": "RSA"},
        {"crv": "P-224"},
        {"crv": []},
        {"x": 1},
        {"d": "AQ"},
        {"x": "A" * 43, "y": "A" * 43},  # (0, 0) is not on P-256.
    ],
)
def test_decode_cpk_rejects_malformed_jwk(changes):
    jwk = {**public_jwk(ec.derive_private_key(43, ec.SECP256R1()).public_key()), **changes}
    with pytest.raises(InvalidIdentity, match="^malformed cpk claim$"):
        decode_cpk(jwk)


@pytest.mark.parametrize("width", [31, 33])
def test_decode_cpk_rejects_wrong_jwk_coordinate_width(width):
    # Scalar 43 has a leading-zero y; both widths preserve the same valid point.
    public_key = ec.derive_private_key(43, ec.SECP256R1()).public_key()
    jwk = public_jwk(public_key)
    value = public_key.public_numbers().y.to_bytes(width, "big")
    jwk["y"] = base64.urlsafe_b64encode(value).decode().rstrip("=")
    with pytest.raises(InvalidIdentity, match="^malformed cpk claim$"):
        decode_cpk(jwk)


@pytest.mark.parametrize("encoding", ["pad-bits", "padding"])
def test_decode_cpk_rejects_noncanonical_jwk_coordinate(encoding):
    jwk = public_jwk(ec.derive_private_key(43, ec.SECP256R1()).public_key())
    value = jwk["y"]
    if encoding == "pad-bits":
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        jwk["y"] = value[:-1] + alphabet[alphabet.index(value[-1]) + 1]
    else:
        jwk["y"] = value + "="
    assert base64.urlsafe_b64decode(jwk["y"] + "==") == base64.urlsafe_b64decode(value + "==")
    with pytest.raises(InvalidIdentity, match="^malformed cpk claim$"):
        decode_cpk(jwk)


def test_insert_identity_goes_before_first_media_line():
    out = insert_identity(SDP, "AAAA")
    lines = out.split("\r\n")
    identity_index = lines.index("a=identity:AAAA")
    media_index = next(i for i, line in enumerate(lines) if line.startswith("m="))
    assert identity_index == media_index - 1


def test_extract_identity_absent_and_strip():
    stripped, envelope = extract_identity(SDP)
    assert stripped == SDP
    assert envelope is None
    assert strip_identity(insert_identity(SDP, "garbage")) == SDP


def test_extract_identity_rejects_malformed_envelope():
    with pytest.raises(InvalidIdentity):
        extract_identity(insert_identity(SDP, "bm90IGpzb24="))  # base64("not json")


@pytest.mark.parametrize("form", ["der", "jwk"])
def test_signer_and_verify_server_identity_roundtrip(form):
    key = generate_operator_key()
    cpk = encode_cpk(key.public_key())
    claims = {"iss": "unit-test"}
    if form == "jwk":
        cpk = public_jwk(key.public_key())
        claims["cpk"] = cpk
    signer = IdentitySigner(key, domain="partner.example", claims=claims)
    signed = signer.sign(SDP)
    assert "a=identity:" in signed

    stripped, identity = verify_server_identity(signed)
    assert stripped == SDP
    assert identity.domain == "partner.example"
    assert identity.claims["iss"] == "unit-test"
    assert identity.claims["cpk"] == cpk
    assert identity.public_key.public_numbers() == key.public_key().public_numbers()
    assert identity.key_digest == cpk_digest(key.public_key())

    # The envelope structure round-trips through extract.
    _, envelope = extract_identity(signed)
    assert envelope.protocol == "default"
    assert envelope.fingerprints.count(".") == 2


def test_verify_server_identity_requires_assertion():
    with pytest.raises(InvalidIdentity):
        verify_server_identity(SDP)


def test_verify_server_identity_rejects_expired_token():
    signer = IdentitySigner(generate_operator_key(), domain="x", token_lifetime=-10)
    with pytest.raises(InvalidIdentity, match="server identity token expired"):
        verify_server_identity(signer.sign(SDP))


@pytest.mark.parametrize("form", ["der", "jwk"])
def test_verify_server_identity_rejects_resigned_fingerprints(form):
    # An attacker swapping the fingerprints for ones signed by a different key must fail:
    # the JWS no longer verifies under the token's cpk.
    key = generate_operator_key()
    claims = {"cpk": public_jwk(key.public_key())} if form == "jwk" else None
    operator = IdentitySigner(key, domain="x", claims=claims)
    signed = operator.sign(SDP)
    _, envelope = extract_identity(signed)
    attacker_jws = sign_fingerprints(SDP.replace("4A:AD", "FF:FF"), generate_operator_key())
    forged = insert_identity(
        SDP.replace("4A:AD", "FF:FF"),
        build_envelope(envelope.domain, envelope.token, attacker_jws),
    )
    with pytest.raises(InvalidIdentity, match="fingerprint signature verification failed"):
        verify_server_identity(forged)


def test_verify_server_identity_rejects_wrong_claimed_jwk():
    signer = IdentitySigner(
        generate_operator_key(),
        domain="x",
        claims={"cpk": public_jwk(generate_operator_key().public_key())},
    )
    with pytest.raises(InvalidIdentity, match="JWT self-signature verification failed"):
        verify_server_identity(signer.sign(SDP))
