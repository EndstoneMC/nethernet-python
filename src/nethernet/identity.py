from __future__ import annotations

import base64
import binascii
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

from nethernet.errors import ConnectionFailed, NetherNetError, SessionError


class InvalidIdentity(NetherNetError):  # noqa: N818 - matches ConnectionFailed/ConnectionClosed
    """An ``a=identity`` assertion that is missing, malformed or fails verification.

    Attributes:
        error: Always ``SessionError.IDENTITY_NOT_ALLOWED``.
    """

    error = SessionError.IDENTITY_NOT_ALLOWED


PublicKey = ec.EllipticCurvePublicKey | rsa.RSAPublicKey
PrivateKey = ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey

IDENTITY_PREFIX = "a=identity:"
FINGERPRINT_PREFIX = "a=fingerprint:"
OPERATOR_DOMAIN = "self"
IDP_PROTOCOL = "default"
OPERATOR_ALG = "ES384"
OPERATOR_TOKEN_LIFETIME = 60
SELF_SIGNED_AUDIENCE = "api://auth-minecraft-services/multiplayer"
SELF_SIGNED_TOKEN_LIFETIME = 31536000
MAX_TOKEN_SIZE = 16384
MAX_JWS_SIZE = 16384

_HASHES: dict[str, type[hashes.HashAlgorithm]] = {
    "256": hashes.SHA256,
    "384": hashes.SHA384,
    "512": hashes.SHA512,
}
_EC_ALGORITHMS: dict[str, tuple[type[ec.EllipticCurve], int]] = {
    "ES256": (ec.SECP256R1, 32),
    "ES384": (ec.SECP384R1, 48),
    "ES512": (ec.SECP521R1, 66),
}
_RSA_ALGORITHMS = ("RS256", "RS384", "RS512")
_JWK_CURVES = {"P-256": "ES256", "P-384": "ES384", "P-521": "ES512"}
_FIELD_PRIMES = {
    "P-256": 2**256 - 2**224 + 2**192 + 2**96 - 1,
    "P-384": 2**384 - 2**128 - 2**96 + 2**32 - 1,
    "P-521": 2**521 - 1,
}
_RSA_MODULUS_BYTES = (128, 256, 512)
_BASE64URL_DIGITS = re.compile(r"[A-Za-z0-9+/_-]*")
_BASE64_DIGITS = re.compile(r"[A-Za-z0-9+/]*")
_TO_STANDARD_ALPHABET = str.maketrans("-_", "+/")


def generate_operator_key() -> ec.EllipticCurvePrivateKey:
    """Generate a long-lived P-384 operator key that signs as ES384."""
    return ec.generate_private_key(ec.SECP384R1())


def _alg_for_key(key: PrivateKey | PublicKey) -> str:
    if isinstance(key, rsa.RSAPrivateKey | rsa.RSAPublicKey):
        return "RS256"
    for alg, (curve_cls, _) in _EC_ALGORITHMS.items():
        if isinstance(key.curve, curve_cls):
            return alg
    raise InvalidIdentity(f"unsupported curve {key.curve.name!r}")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _decode_digits(digits: str) -> bytes:
    digits = digits.translate(_TO_STANDARD_ALPHABET)
    if len(digits) % 4 == 1:
        digits = digits[:-1]
    data = base64.b64decode(digits + "=" * (-len(digits) % 4))
    if not data:
        raise InvalidIdentity("empty base64 value")
    return data


def _unb64url(text: str) -> bytes:
    return _decode_digits(_BASE64URL_DIGITS.match(text).group())


def _strict_b64decode(text: str) -> bytes:
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidIdentity("invalid base64") from None
    if base64.b64encode(data).decode() != text:
        raise InvalidIdentity("invalid base64")
    return data


_NAMED_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _json_string(text: str) -> str:
    out = ['"']
    for char in text:
        escaped = _NAMED_ESCAPES.get(char)
        if escaped is not None:
            out.append(escaped)
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def canonical_json(value: object) -> str:
    if isinstance(value, dict):
        members = sorted(value.items(), key=lambda item: item[0])
        return "{" + ",".join(f"{_json_string(k)}:{canonical_json(v)}" for k, v in members) + "}"
    if isinstance(value, list | tuple):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, str):
        return _json_string(value)
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, int | float):
        return json.dumps(value)
    raise TypeError(f"cannot encode {type(value).__name__} as JSON")


def _json_object(data: bytes | str) -> dict:
    try:
        value = json.loads(data)
    except (ValueError, RecursionError):
        raise InvalidIdentity("malformed JSON") from None
    if not isinstance(value, dict):
        raise InvalidIdentity("JSON is not an object")
    return value


def _line_texts(sdp: str) -> list[str]:
    lines = sdp.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _split_keepends(sdp: str) -> list[str]:
    parts = sdp.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _has_prefix(line: str, prefix: str) -> bool:
    return line[: len(prefix)].lower() == prefix


def fingerprint_entries(sdp: str) -> list[tuple[str, str]]:
    entries = []
    for line in _line_texts(sdp):
        if not _has_prefix(line, FINGERPRINT_PREFIX):
            continue
        algorithm, _, digest = line[len(FINGERPRINT_PREFIX) :].partition(" ")
        if algorithm and digest:
            entries.append((algorithm, digest))
    entries.sort(key=lambda entry: (entry[0].encode(), entry[1].encode()))
    return entries


def fingerprint_payload(sdp: str) -> bytes:
    entries = [{"algorithm": a, "digest": d} for a, d in fingerprint_entries(sdp)]
    return canonical_json({"fingerprint": entries}).encode()


def _sign(private_key: PrivateKey, alg: str, data: bytes) -> bytes:
    hash_algorithm = _HASHES[alg[2:]]()
    if isinstance(private_key, rsa.RSAPrivateKey):
        return private_key.sign(data, padding.PKCS1v15(), hash_algorithm)
    _, size = _EC_ALGORITHMS[alg]
    r, s = decode_dss_signature(private_key.sign(data, ec.ECDSA(hash_algorithm)))
    return r.to_bytes(size, "big") + s.to_bytes(size, "big")


def _verify(public_key: PublicKey, alg: str, data: bytes, signature: bytes) -> bool:
    if alg in _EC_ALGORITHMS:
        curve_cls, _ = _EC_ALGORITHMS[alg]
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            return False
        half = len(signature) // 2
        if not isinstance(public_key.curve, curve_cls) or half == 0:
            return False
        r = int.from_bytes(signature[:half], "big")
        s = int.from_bytes(signature[half : 2 * half], "big")
        try:
            public_key.verify(encode_dss_signature(r, s), data, ec.ECDSA(_HASHES[alg[2:]]()))
        except InvalidSignature:
            return False
        return True
    if alg in _RSA_ALGORITHMS:
        if not isinstance(public_key, rsa.RSAPublicKey):
            return False
        try:
            public_key.verify(signature, data, padding.PKCS1v15(), _HASHES[alg[2:]]())
        except InvalidSignature:
            return False
        return True
    return False


def _header_alg(header_b64: str) -> str:
    alg = _json_object(_unb64url(header_b64)).get("alg")
    if not isinstance(alg, str) or (alg not in _EC_ALGORITHMS and alg not in _RSA_ALGORITHMS):
        raise InvalidIdentity(f"unsupported JWS alg {alg!r}")
    return alg


def sign_fingerprints(sdp: str, private_key: PrivateKey) -> str:
    if not fingerprint_entries(sdp):
        raise ConnectionFailed(SessionError.FAILED_TO_CREATE_IDENTITY_ASSERTION)
    alg = _alg_for_key(private_key)
    header = _b64url(canonical_json({"alg": alg}).encode())
    payload = _b64url(fingerprint_payload(sdp))
    signature = _sign(private_key, alg, f"{header}.{payload}".encode())
    return f"{header}..{_b64url(signature)}"


def verify_fingerprints(sdp: str, jws: str, public_key: PublicKey) -> None:
    if not jws or len(jws.encode()) > MAX_JWS_SIZE:
        raise InvalidIdentity("fingerprints JWS is empty or too large")
    if not fingerprint_entries(sdp):
        raise InvalidIdentity("SDP has no fingerprint")
    parts = jws.split(".")
    header_b64, signature_b64 = parts[0], parts[-1]
    alg = _header_alg(header_b64)
    signing_input = f"{header_b64}.{_b64url(fingerprint_payload(sdp))}".encode()
    if not _verify(public_key, alg, signing_input, _unb64url(signature_b64)):
        raise InvalidIdentity("fingerprint signature verification failed")


def encode_cpk(public_key: PublicKey) -> str:
    der = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(der).decode()


def decode_cpk(text: str) -> PublicKey:
    if not isinstance(text, str):
        raise InvalidIdentity("malformed cpk claim")
    try:
        der = _decode_digits(_BASE64_DIGITS.match(text).group())
        key = serialization.load_der_public_key(der)
    except (InvalidIdentity, UnsupportedAlgorithm, ValueError, TypeError):
        raise InvalidIdentity("malformed cpk claim") from None
    if isinstance(key, rsa.RSAPublicKey):
        if key.key_size not in (1024, 2048, 4096):
            raise InvalidIdentity("malformed cpk claim")
    elif not isinstance(key, ec.EllipticCurvePublicKey) or not any(
        isinstance(key.curve, curve_cls) for curve_cls, _ in _EC_ALGORITHMS.values()
    ):
        raise InvalidIdentity("malformed cpk claim")
    return key


def encode_jwk(public_key: ec.EllipticCurvePublicKey) -> dict[str, str]:
    alg = _alg_for_key(public_key)
    _, size = _EC_ALGORITHMS[alg]
    crv = next(name for name, value in _JWK_CURVES.items() if value == alg)
    numbers = public_key.public_numbers()
    return {
        "crv": crv,
        "kty": "EC",
        "x": _b64url(numbers.x.to_bytes(size, "big")),
        "y": _b64url(numbers.y.to_bytes(size, "big")),
    }


def _jwk_bytes(jwk: dict, member: str) -> bytes:
    value = jwk[member]
    if not isinstance(value, str):
        raise ValueError
    return _unb64url(value)


def _decode_ec_jwk(jwk: dict) -> ec.EllipticCurvePublicKey:
    crv = jwk["crv"]
    if not isinstance(crv, str) or crv not in _JWK_CURVES:
        raise ValueError
    curve_cls, _ = _EC_ALGORITHMS[_JWK_CURVES[crv]]
    x = int.from_bytes(_jwk_bytes(jwk, "x"), "big")
    y = int.from_bytes(_jwk_bytes(jwk, "y"), "big")
    if x >= _FIELD_PRIMES[crv] or y >= _FIELD_PRIMES[crv]:
        raise ValueError
    return ec.EllipticCurvePublicNumbers(x, y, curve_cls()).public_key()


def _decode_rsa_jwk(jwk: dict) -> rsa.RSAPublicKey:
    n_bytes = _jwk_bytes(jwk, "n")
    e_bytes = _jwk_bytes(jwk, "e")
    if len(n_bytes) not in _RSA_MODULUS_BYTES:
        raise ValueError
    n = int.from_bytes(n_bytes, "big")
    e = int.from_bytes(e_bytes, "big")
    return rsa.RSAPublicNumbers(e, n).public_key()


def decode_jwk(jwk: object) -> PublicKey:
    if not isinstance(jwk, dict):
        raise InvalidIdentity("malformed cpk claim")
    try:
        kty = jwk["kty"]
        if kty == "RSA":
            return _decode_rsa_jwk(jwk)
        if kty == "EC":
            return _decode_ec_jwk(jwk)
        raise ValueError
    except (InvalidIdentity, KeyError, TypeError, ValueError):
        raise InvalidIdentity("malformed cpk claim") from None


def key_pin(public_key: PublicKey) -> str:
    return encode_cpk(public_key)


@dataclass(frozen=True)
class _Token:
    header: dict
    claims: dict
    signing_input: bytes
    signature_b64: str


def _decode_jwt(token: str) -> _Token:
    if not token or len(token.encode()) > MAX_TOKEN_SIZE:
        raise InvalidIdentity("token is empty or too large")
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidIdentity("malformed JWT")
    header_b64, payload_b64, signature_b64 = parts
    header = _json_object(_unb64url(header_b64))
    claims = _json_object(_unb64url(payload_b64))
    return _Token(header, claims, f"{header_b64}.{payload_b64}".encode(), signature_b64)


def verify_jwt_signature(token: str, public_key: PublicKey) -> dict:
    decoded = _decode_jwt(token)
    alg = decoded.header.get("alg")
    if not isinstance(alg, str) or alg != _key_alg(public_key, alg):
        raise InvalidIdentity(f"JWT alg {alg!r} does not match the key")
    signature = _unb64url(decoded.signature_b64)
    if not _verify(public_key, alg, decoded.signing_input, signature):
        raise InvalidIdentity("JWT self-signature verification failed")
    return decoded.claims


def _key_alg(public_key: PublicKey, alg: str) -> str | None:
    if isinstance(public_key, rsa.RSAPublicKey):
        return alg if alg in _RSA_ALGORITHMS else None
    return _alg_for_key(public_key)


@dataclass(frozen=True)
class IdentityEnvelope:
    """A parsed ``a=identity`` envelope.

    Attributes:
        domain: The ``idp.domain`` string.
        protocol: The ``idp.protocol`` string.
        token: The assertion's token, empty when the assertion has none.
        fingerprints: The assertion's detached JWS, empty when the assertion has none.
    """

    domain: str
    protocol: str
    token: str
    fingerprints: str


def build_envelope(domain: str, token: str, fingerprints: str) -> str:
    assertion = canonical_json({"fingerprints": fingerprints, "token": token})
    outer = canonical_json(
        {"assertion": assertion, "idp": {"domain": domain, "protocol": IDP_PROTOCOL}}
    )
    return base64.b64encode(outer.encode()).decode()


def parse_envelope(value: str) -> IdentityEnvelope:
    try:
        outer = _json_object(_strict_b64decode(value))
    except InvalidIdentity:
        raise InvalidIdentity("malformed a=identity envelope") from None
    assertion = outer.get("assertion")
    idp = outer.get("idp")
    if not isinstance(assertion, str) or not isinstance(idp, dict):
        raise InvalidIdentity("malformed a=identity envelope")
    domain = idp.get("domain")
    protocol = idp.get("protocol")
    if not isinstance(domain, str) or not isinstance(protocol, str):
        raise InvalidIdentity("malformed a=identity envelope")
    try:
        inner = json.loads(assertion)
    except (ValueError, RecursionError):
        inner = None
    if not isinstance(inner, dict):
        inner = {}
    token = inner.get("token")
    fingerprints = inner.get("fingerprints")
    return IdentityEnvelope(
        domain=domain,
        protocol=protocol,
        token=token if isinstance(token, str) else "",
        fingerprints=fingerprints if isinstance(fingerprints, str) else "",
    )


def strip_identity(sdp: str) -> str:
    return "".join(
        line
        for line in _split_keepends(sdp)
        if not _has_prefix(line.rstrip("\n").rstrip("\r"), IDENTITY_PREFIX)
    )


def extract_identity(sdp: str) -> tuple[str, IdentityEnvelope | None]:
    for line in _line_texts(sdp):
        if _has_prefix(line, IDENTITY_PREFIX):
            return strip_identity(sdp), parse_envelope(line[len(IDENTITY_PREFIX) :])
    return sdp, None


def insert_identity(sdp: str, envelope: str) -> str:
    lines = _line_texts(sdp)
    identity = f"{IDENTITY_PREFIX}{envelope}"
    index = next((i for i, line in enumerate(lines) if line.startswith("m=")), len(lines))
    lines.insert(index, identity)
    return "".join(f"{line}\r\n" for line in lines)


class IdentitySigner:
    """An operator identity signer for SDP answers, holding a P-384 key.

    Args:
        private_key: The operator's P-384 key.
        clock: The source of the signing time in Unix seconds.
    """

    def __init__(
        self,
        private_key: ec.EllipticCurvePrivateKey,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(
            private_key.curve, ec.SECP384R1
        ):
            raise InvalidIdentity("the operator key must be a P-384 key")
        self._private_key = private_key
        self._clock = clock

    def token(self) -> str:
        """Build a fresh operator token signed by the operator's key.

        Returns:
            A compact JWT whose ``cpk`` is the operator's key, valid for 60 seconds.
        """
        now = int(self._clock())
        public_key = self._private_key.public_key()
        header = {"alg": OPERATOR_ALG, "x5u": encode_cpk(public_key)}
        claims = {"cpk": encode_jwk(public_key), "exp": now + OPERATOR_TOKEN_LIFETIME, "iat": now}
        header_b64 = _b64url(f"{canonical_json(header)}\n".encode())
        payload_b64 = _b64url(f"{canonical_json(claims)}\n".encode())
        signing_input = f"{header_b64}.{payload_b64}".encode()
        signature = _sign(self._private_key, OPERATOR_ALG, signing_input)
        return f"{header_b64}.{payload_b64}.{_b64url(signature)}"

    def sign(self, sdp: str) -> str:
        """Insert the operator's identity assertion into an SDP answer.

        Args:
            sdp: The local SDP answer.

        Returns:
            The SDP with an ``a=identity`` line before its first media line.

        Raises:
            ConnectionFailed: The SDP has no fingerprint line.
        """
        fingerprints = sign_fingerprints(sdp, self._private_key)
        return insert_identity(sdp, build_envelope(OPERATOR_DOMAIN, self.token(), fingerprints))


def _token_issuer(token: str) -> str:
    try:
        issuer = _decode_jwt(token).claims.get("iss")
    except InvalidIdentity:
        return ""
    return issuer if isinstance(issuer, str) else ""


class PlayerSigner:
    """A signer of SDP offers with a player's identity assertion.

    Args:
        token: The player's ``GameServerToken``, carried unchanged.
        private_key: The key whose public half is the token's ``cpk``.
    """

    def __init__(self, token: str, private_key: PrivateKey | None) -> None:
        self._token = token
        self._private_key = private_key
        self._domain = _token_issuer(token)

    @classmethod
    def self_signed(
        cls,
        private_key: ec.EllipticCurvePrivateKey | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> PlayerSigner:
        """Create a signer whose token is signed by its own P-384 key, with no Xbox chain.

        Args:
            private_key: The player's P-384 key. A new one is generated when omitted.
            clock: The source of the signing time in Unix seconds.
        """
        key = generate_operator_key() if private_key is None else private_key
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP384R1
        ):
            raise ValueError("a self-signed player key must be a P-384 key")
        public_key = encode_cpk(key.public_key())
        header = {"alg": OPERATOR_ALG, "x5u": public_key}
        claims = {
            "aud": SELF_SIGNED_AUDIENCE,
            "cpk": public_key,
            "exp": int(clock()) + SELF_SIGNED_TOKEN_LIFETIME,
            "leguuid": str(uuid.uuid4()),
        }
        header_b64 = _b64url(f"{canonical_json(header)}\n".encode())
        payload_b64 = _b64url(f"{canonical_json(claims)}\n".encode())
        signature = _sign(key, OPERATOR_ALG, f"{header_b64}.{payload_b64}".encode())
        return cls(f"{header_b64}.{payload_b64}.{_b64url(signature)}", key)

    def sign(self, sdp: str) -> str:
        """Insert the player's identity assertion into an SDP offer.

        Args:
            sdp: The local SDP offer.

        Returns:
            The SDP with an ``a=identity`` line before its first media line.

        Raises:
            ConnectionFailed: There is no token or key, or the SDP has no fingerprint line.
        """
        if not self._token or self._private_key is None:
            raise ConnectionFailed(SessionError.FAILED_TO_CREATE_IDENTITY_ASSERTION)
        fingerprints = sign_fingerprints(sdp, self._private_key)
        return insert_identity(sdp, build_envelope(self._domain, self._token, fingerprints))


@dataclass(frozen=True)
class ServerIdentity:
    """A verified operator assertion.

    Attributes:
        domain: The ``idp.domain`` the operator sent.
        claims: The operator token's claims.
        public_key: The operator's public key.
        key_pin: The key as base64 DER SubjectPublicKeyInfo, to pin on first use.
    """

    domain: str
    claims: dict
    public_key: PublicKey
    key_pin: str


@dataclass(frozen=True)
class PlayerIdentity:
    domain: str
    token: str
    claims: dict
    public_key: PublicKey


def _require_envelope(sdp: str) -> tuple[str, IdentityEnvelope]:
    stripped, envelope = extract_identity(sdp)
    if envelope is None:
        raise InvalidIdentity("SDP has no a=identity attribute")
    if not envelope.fingerprints or not envelope.token:
        raise InvalidIdentity("assertion lacks fingerprints or token")
    return stripped, envelope


def verify_server_identity(sdp: str) -> tuple[str, ServerIdentity]:
    stripped, envelope = _require_envelope(sdp)
    public_key = decode_jwk(_decode_jwt(envelope.token).claims.get("cpk"))
    claims = verify_jwt_signature(envelope.token, public_key)
    verify_fingerprints(sdp, envelope.fingerprints, public_key)
    return stripped, ServerIdentity(
        domain=envelope.domain,
        claims=claims,
        public_key=public_key,
        key_pin=key_pin(public_key),
    )


def verify_player_identity(sdp: str) -> tuple[str, PlayerIdentity]:
    stripped, envelope = _require_envelope(sdp)
    claims = _decode_jwt(envelope.token).claims
    public_key = decode_cpk(claims.get("cpk"))
    verify_fingerprints(sdp, envelope.fingerprints, public_key)
    return stripped, PlayerIdentity(
        domain=envelope.domain,
        token=envelope.token,
        claims=claims,
        public_key=public_key,
    )
