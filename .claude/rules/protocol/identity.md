---
paths:
  - "src/nethernet/identity.py"
  - "tests/test_identity.py"
---

# Identity assertions

The envelope follows Mojang's public [NetherNet onboarding guide](https://github.com/Mojang/bedrock-protocol-docs/blob/main/NetherNetOnboardingGuide.md). Both directions of HTTP signaling can carry one: the offer asserts a player, the answer asserts the operator's key.

## JSON

- Every JSON text here is compact, with no whitespace and object keys in sorted order (1.26.60).
- Strings escape only `"`, `\` and control bytes: `\b`, `\t`, `\n`, `\f` and `\r` by name, other bytes 0x01 to 0x1F as `\u00XX` in uppercase hex. Bytes from 0x80 up and `/` are written raw (1.26.60).
- The operator token's header and payload JSON each end in `
` before base64url. The fingerprints JWS header does not (1.26.60).

## Envelope

- The assertion is one `a=identity:<value>` line, inserted directly before the first `m=` line, or appended when the SDP has none (1.26.60).
- Inserting it rewrites every line of the SDP to end in CRLF (1.26.60).
- `<value>` is standard base64 with padding of `{"assertion":<string>,"idp":{"domain":<domain>,"protocol":<protocol>}}` (1.26.60).
- The `assertion` string is the JSON `{"fingerprints":<jws>,"token":<token>}` (1.26.60).
- `protocol` is `default` on both sides (1.26.60).
- A receiver reads the first line that starts, ASCII case-insensitively, with `a=identity:` and ignores later ones (1.26.60).
- It rejects the envelope unless the value is strict base64 of a JSON object with a string `assertion` and an object `idp` holding a string `domain` and a string `protocol`. Extra keys are ignored (1.26.60).
- The client hands a received SDP to WebRTC with `a=identity` intact, and libwebrtc ignores the attribute (1.26.60). This library strips it before aiortc.

## Fingerprints

- The fingerprint payload is `{"fingerprint":[{"algorithm":<a>,"digest":<d>},...]}`, one entry per `a=fingerprint:<a> <d>` line (1.26.60).
- A fingerprint line matches its `a=fingerprint:` prefix ASCII case-insensitively. `<a>` runs to the first space and `<d>` is the rest of the line verbatim. A line with either part empty is skipped (1.26.60).
- Entries are sorted ascending by algorithm, then by digest, bytewise. Duplicates stay (1.26.60).
- A local SDP with no fingerprint line yields no assertion, and the negotiation fails with `SessionError.FAILED_TO_CREATE_IDENTITY_ASSERTION` (36) (1.26.60).
- A receiver recomputes the payload from the SDP it received and rejects the assertion when that SDP has no fingerprint line (1.26.60).
- `fingerprints` is a detached compact JWS, `header..signature`, over that payload (1.26.60).
- JWS parts are base64url (1.26.60).
- `alg` is matched exactly and case-sensitively against `ES256`, `ES384`, `ES512`, `RS256`, `RS384` and `RS512`, with SHA-256, SHA-384 and SHA-512 by suffix. Anything else, `none` and `HS256` included, fails (1.26.60).
- ECDSA signatures are JOSE raw `r || s` of 64, 96 and 132 bytes for `ES256`, `ES384` and `ES512` (1.26.60). `ES256` is on P-256, `ES384` on P-384 and `ES512` on P-521 (1.26.60).
- The key must match the `alg`: an `ES*` alg needs exactly its curve, an `RS*` alg an RSA key (1.26.60).
- The fingerprints JWS header is `{"alg":<alg>}` alone (1.26.60).

## Player assertion

- The client's `token` is the player's `GameServerToken`, unchanged. It signs `fingerprints` with the player's key (1.26.60).
- The client makes no assertion without a token and a key, and the negotiation fails with `SessionError.FAILED_TO_CREATE_IDENTITY_ASSERTION` (36) (1.26.60).
- The client's `idp.domain` is the `iss` claim of the player's `GameServerToken`, or empty when that token does not parse (1.26.60). BDS does not check the offer's `idp.domain` (1.26.60).
- `cpk` in the `GameServerToken` is a string public key. Base64 DER SubjectPublicKeyInfo is the form BDS accepts (1.26.60).
- BDS accepts a self-signed player token with no Xbox chain, even with `online-mode=true` (1.26.60).

## Operator assertion

- The host's assertion is self-signed with the operator's key, `idp.domain` is `self` (1.26.60).
- The operator's key is a P-384 key and signs with `ES384`. Every session of one BDS process uses the same key, and BDS makes a new one on every restart (1.26.60).
- Its `token` is a JWT signed by the operator's key. The header is `{"alg":"ES384","x5u":<key>}`, `<key>` the operator's DER SubjectPublicKeyInfo in standard base64 with `+` and `/`, with no `typ` and no `kid` (1.26.60).
- The payload is `{"cpk":{"crv":"P-384","kty":"EC","x":<x>,"y":<y>},"exp":<exp>,"iat":<iat>}`, `<x>` and `<y>` the 48-byte coordinates in base64url (1.26.60).
- `iat` is the signing time in Unix seconds and `exp` is `iat + 60` (1.26.60).
- `fingerprints` is signed by the same key (1.26.60).
- A client checks the operator's key against a trust-on-first-use store and asks the player about an unknown key (1.26.60).
- The client's pin is the operator's key itself, its DER SubjectPublicKeyInfo in standard base64, with no digest. It matches on the key alone, not per host (1.26.60).
- The client keeps at most 100 pins and drops the least recently pinned first. It saves them as `{"keys":[<JWK>,...]}` (1.26.60).
- This library's pin store is the application's `on_server_identity` callback (`api.md`).

## Verification

- A token is at most 16384 bytes and has exactly three dot-separated segments whose header and payload decode as JSON objects (1.26.60).
- A detached JWS is at most 16384 bytes (1.26.60).
- Both sides need a non-empty `fingerprints`, a non-empty `token` and a `cpk` claim that parses as a key, then verify `fingerprints` under `cpk` over the received SDP's payload (1.26.60).
- The host verifying a player reads `cpk` as a string key and does not check the token's own signature (1.26.60).
- The client verifying an operator reads `cpk` as a JWK and checks the token's signature under it. The token's `alg` must match the key's type (1.26.60).
- No side reads `iat`, `exp` or `nbf`. Expiry and clock skew are never checked (1.26.60).
- Verification yields only pass or fail, with no error code per failure (1.26.60).
- `http-signaling.md` says how BDS answers an offer without `a=identity`.
