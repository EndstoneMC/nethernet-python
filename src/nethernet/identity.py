"""SDP identity assertion mechanics — NetherNet Onboarding Guide s5.

Envelope crypto only: parse/strip/insert ``a=identity``, the canonical fingerprint payload,
the detached JWS over it, and the self-signed server JWT carrying ``cpk``. Policy — validating
a GameServerToken against the auth service, authorization decisions, TOFU pin storage — stays
with the application.

The ``cpk`` claim is serialized as base64 DER SubjectPublicKeyInfo (the guide leaves the format
implementation-defined; this matches Minecraft's public-key convention).

cryptography API verified against the official reference:
https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ec/
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

from nethernet.errors import NetherNetError


class InvalidIdentity(NetherNetError):  # noqa: N818 - matches ConnectionFailed/ConnectionClosed
    """An ``a=identity`` assertion is missing, malformed, or fails verification."""


# JOSE ECDSA algorithms: name -> (curve, hash, coordinate size in bytes).
_ALGORITHMS: dict[str, tuple[type[ec.EllipticCurve], type[hashes.HashAlgorithm], int]] = {
    "ES256": (ec.SECP256R1, hashes.SHA256, 32),
    "ES384": (ec.SECP384R1, hashes.SHA384, 48),
    "ES512": (ec.SECP521R1, hashes.SHA512, 66),
}


def generate_operator_key() -> ec.EllipticCurvePrivateKey:
    """Generate a long-lived operator keypair (P-384, signs as ES384)."""
    return ec.generate_private_key(ec.SECP384R1())


def _alg_for_key(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> str:
    for alg, (curve_cls, _, _) in _ALGORITHMS.items():
        if isinstance(key.curve, curve_cls):
            return alg
    raise InvalidIdentity(f"unsupported curve {key.curve.name!r}")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64url(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        raise InvalidIdentity("invalid base64url") from None


def _canonical_json(obj: object) -> bytes:
    """RFC 8785 subset per the guide: sorted keys, no whitespace, minimal escaping."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# -- fingerprint payload (Guide s5.1 step 4) -------------------------------------------


def fingerprint_payload(sdp: str) -> bytes:
    """Canonical JSON over every ``a=fingerprint`` line of an SDP, in order."""
    entries = []
    for line in sdp.splitlines():
        stripped = line.strip()
        if stripped.startswith("a=fingerprint:"):
            algorithm, _, digest = stripped[len("a=fingerprint:") :].partition(" ")
            entries.append({"algorithm": algorithm, "digest": digest})
    return _canonical_json({"fingerprint": entries})


# -- raw JOSE ECDSA signatures (r || s, fixed width) -----------------------------------


def _sign_raw(private_key: ec.EllipticCurvePrivateKey, alg: str, data: bytes) -> bytes:
    _, hash_cls, size = _ALGORITHMS[alg]
    r, s = decode_dss_signature(private_key.sign(data, ec.ECDSA(hash_cls())))
    return r.to_bytes(size, "big") + s.to_bytes(size, "big")


def _verify_raw(
    public_key: ec.EllipticCurvePublicKey, alg: str, data: bytes, signature: bytes
) -> bool:
    _, hash_cls, size = _ALGORITHMS[alg]
    if len(signature) != 2 * size:
        return False
    r = int.from_bytes(signature[:size], "big")
    s = int.from_bytes(signature[size:], "big")
    try:
        public_key.verify(encode_dss_signature(r, s), data, ec.ECDSA(hash_cls()))
    except InvalidSignature:
        return False
    return True


# -- detached fingerprints JWS (Guide s5.1 step 5 / s5.2) ------------------------------


def sign_fingerprints(sdp: str, private_key: ec.EllipticCurvePrivateKey) -> str:
    """Detached compact JWS (``header..signature``) over the canonical fingerprint payload."""
    alg = _alg_for_key(private_key)
    header = _b64url(_canonical_json({"alg": alg}))
    payload = _b64url(fingerprint_payload(sdp))
    signature = _sign_raw(private_key, alg, f"{header}.{payload}".encode())
    return f"{header}..{_b64url(signature)}"


def verify_fingerprints(sdp: str, jws: str, public_key: ec.EllipticCurvePublicKey) -> None:
    """Verify a detached fingerprints JWS against an SDP; raises :class:`InvalidIdentity`."""
    parts = jws.split(".")
    if len(parts) != 3 or parts[1]:
        raise InvalidIdentity("fingerprints JWS must be a detached compact serialization")
    header_b64, _, signature_b64 = parts
    try:
        header = json.loads(_unb64url(header_b64))
    except ValueError:
        raise InvalidIdentity("malformed JWS header") from None
    alg = header.get("alg") if isinstance(header, dict) else None
    if alg not in _ALGORITHMS:
        raise InvalidIdentity(f"unsupported JWS alg {alg!r}")
    payload = _b64url(fingerprint_payload(sdp))
    signing_input = f"{header_b64}.{payload}".encode()
    if not _verify_raw(public_key, alg, signing_input, _unb64url(signature_b64)):
        raise InvalidIdentity("fingerprint signature verification failed")


# -- cpk claim -------------------------------------------------------------------------


def encode_cpk(public_key: ec.EllipticCurvePublicKey) -> str:
    """Base64 DER SubjectPublicKeyInfo — the ``cpk`` claim value."""
    der = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(der).decode()


def decode_cpk(text: str) -> ec.EllipticCurvePublicKey:
    try:
        key = serialization.load_der_public_key(base64.b64decode(text, validate=True))
    except (ValueError, TypeError):
        raise InvalidIdentity("malformed cpk claim") from None
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise InvalidIdentity("cpk is not an EC public key")
    return key


def cpk_digest(public_key: ec.EllipticCurvePublicKey) -> str:
    """sha-256 hex digest of the DER SPKI — the TOFU pinning unit (Guide s5.2)."""
    der = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()


# -- JWT (self-signed server token, Guide s5.2) ----------------------------------------


def _sign_jwt(claims: dict, private_key: ec.EllipticCurvePrivateKey) -> str:
    alg = _alg_for_key(private_key)
    header = _b64url(_canonical_json({"alg": alg, "typ": "JWT"}))
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signature = _sign_raw(private_key, alg, f"{header}.{payload}".encode())
    return f"{header}.{payload}.{_b64url(signature)}"


def _decode_jwt(token: str) -> tuple[dict, dict, bytes, bytes]:
    """Split a compact JWT into (header, claims, signing_input, signature); no verification."""
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidIdentity("malformed JWT")
    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_unb64url(header_b64))
        claims = json.loads(_unb64url(payload_b64))
    except ValueError:
        raise InvalidIdentity("malformed JWT header/payload") from None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise InvalidIdentity("malformed JWT header/payload")
    return header, claims, f"{header_b64}.{payload_b64}".encode(), _unb64url(signature_b64)


# -- envelope (Guide s5: base64 JSON in a session-level a=identity) --------------------


@dataclass(frozen=True)
class IdentityEnvelope:
    """A parsed ``a=identity`` envelope: the ``idp`` block plus the opaque assertion parts."""

    domain: str
    protocol: str
    token: str  # a JWT: GameServerToken (offer) or self-signed server token (answer)
    fingerprints: str  # detached JWS over the SDP's a=fingerprint lines


def build_envelope(domain: str, token: str, fingerprints: str) -> str:
    """Base64 envelope value for ``a=identity`` (``idp.protocol`` is always ``default``)."""
    assertion = json.dumps({"token": token, "fingerprints": fingerprints}, separators=(",", ":"))
    outer = json.dumps(
        {"idp": {"domain": domain, "protocol": "default"}, "assertion": assertion},
        separators=(",", ":"),
    )
    return base64.b64encode(outer.encode()).decode()


def _parse_envelope(value: str) -> IdentityEnvelope:
    try:
        outer = json.loads(base64.b64decode(value, validate=True))
        idp = outer["idp"]
        inner = json.loads(outer["assertion"])
        return IdentityEnvelope(
            domain=str(idp["domain"]),
            protocol=str(idp["protocol"]),
            token=str(inner["token"]),
            fingerprints=str(inner["fingerprints"]),
        )
    except (ValueError, KeyError, TypeError):
        raise InvalidIdentity("malformed a=identity envelope") from None


def extract_identity(sdp: str) -> tuple[str, IdentityEnvelope | None]:
    """Strip ``a=identity`` from an SDP and parse it; ``(stripped_sdp, envelope or None)``.

    Raises :class:`InvalidIdentity` if the attribute is present but malformed. Always strip
    before handing an SDP to WebRTC (Guide s5.1 step 7).
    """
    kept: list[str] = []
    value: str | None = None
    for line in sdp.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("a=identity:"):
            value = stripped[len("a=identity:") :]
        else:
            kept.append(line)
    if value is None:
        return sdp, None
    return "".join(kept), _parse_envelope(value)


def strip_identity(sdp: str) -> str:
    """Remove any ``a=identity`` line without parsing it."""
    return "".join(
        line
        for line in sdp.splitlines(keepends=True)
        if not line.strip().startswith("a=identity:")
    )


def insert_identity(sdp: str, envelope: str) -> str:
    """Insert a session-level ``a=identity`` immediately before the first ``m=`` line."""
    newline = "\r\n" if "\r\n" in sdp else "\n"
    out: list[str] = []
    inserted = False
    for line in sdp.splitlines(keepends=True):
        if not inserted and line.startswith("m="):
            out.append(f"a=identity:{envelope}{newline}")
            inserted = True
        out.append(line)
    if not inserted:
        out.append(f"a=identity:{envelope}{newline}")
    return "".join(out)


# -- server assertion: produce & verify (Guide s5.2) -----------------------------------


class IdentitySigner:
    """Produces the server ``a=identity`` for SDP answers.

    Holds the operator's long-lived EC private key. :meth:`sign` self-signs a JWT carrying the
    ``cpk`` claim, signs the answer's fingerprints, and inserts the envelope. Clients pin
    ``cpk`` — reuse one keypair across a fleet and treat rotation as a deliberate event.
    """

    def __init__(
        self,
        private_key: ec.EllipticCurvePrivateKey,
        *,
        domain: str,
        claims: dict | None = None,
        token_lifetime: float | None = 30 * 86400,
    ) -> None:
        _alg_for_key(private_key)  # fail fast on unsupported curves
        self._private_key = private_key
        self._domain = domain
        self._claims = dict(claims or {})
        self._token_lifetime = token_lifetime

    def _token(self) -> str:
        now = int(time.time())
        claims = {"cpk": encode_cpk(self._private_key.public_key()), "iat": now, **self._claims}
        if self._token_lifetime is not None:
            claims["exp"] = now + int(self._token_lifetime)
        return _sign_jwt(claims, self._private_key)

    def sign(self, sdp: str) -> str:
        """Return ``sdp`` with the server assertion inserted."""
        envelope = build_envelope(
            self._domain, self._token(), sign_fingerprints(sdp, self._private_key)
        )
        return insert_identity(sdp, envelope)


@dataclass(frozen=True)
class ServerIdentity:
    """A structurally verified server assertion; pin :attr:`key_digest` for TOFU."""

    domain: str  # untrusted display text from the idp block
    claims: dict  # JWT claims (cpk, iat/exp, any operator extras)
    public_key: ec.EllipticCurvePublicKey
    key_digest: str  # sha-256 hex of the cpk SPKI — the unit of trust


def verify_server_identity(sdp: str) -> tuple[str, ServerIdentity]:
    """Verify the server assertion in an answer SDP; ``(stripped_sdp, ServerIdentity)``.

    Structural verification only (self-signed JWT via the embedded ``cpk``, fingerprints JWS,
    ``exp``). Trust anchoring — TLS or TOFU pinning of ``key_digest`` — is the caller's policy.
    """
    stripped, envelope = extract_identity(sdp)
    if envelope is None:
        raise InvalidIdentity("answer has no a=identity attribute")
    header, claims, signing_input, signature = _decode_jwt(envelope.token)
    alg = header.get("alg")
    if alg not in _ALGORITHMS:
        raise InvalidIdentity(f"unsupported JWT alg {alg!r}")
    cpk = claims.get("cpk")
    if not isinstance(cpk, str):
        raise InvalidIdentity("JWT has no cpk claim")
    public_key = decode_cpk(cpk)
    if not _verify_raw(public_key, alg, signing_input, signature):
        raise InvalidIdentity("JWT self-signature verification failed")
    exp = claims.get("exp")
    if isinstance(exp, int | float) and exp < time.time():
        raise InvalidIdentity("server identity token expired")
    verify_fingerprints(stripped, envelope.fingerprints, public_key)
    return stripped, ServerIdentity(
        domain=envelope.domain,
        claims=claims,
        public_key=public_key,
        key_digest=cpk_digest(public_key),
    )
