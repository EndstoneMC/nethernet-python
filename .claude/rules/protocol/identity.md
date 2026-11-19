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
- The operator token's and the client's self-signed token's header and payload JSON each end in `\n` before base64url. The fingerprints JWS header does not (1.26.60).

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
- A verifier does not check the ECDSA signature length against 64, 96 or 132. It takes the first half of the bytes as `r` and the second half as `s`, drops the last byte of an odd length and fails under 2 bytes. A short or long signature verifies when both halves hold the right values with leading zeros allowed, and fails otherwise (1.26.60).
- The key must match the `alg`: an `ES*` alg needs exactly its curve, an `RS*` alg an RSA key (1.26.60).
- The fingerprints JWS header is `{"alg":<alg>}` alone (1.26.60).

## Player assertion

- The client's `token` is the player's `GameServerToken`, unchanged. It signs `fingerprints` with the player's key (1.26.60).
- The client makes no assertion without a token and a key, and the negotiation fails with `SessionError.FAILED_TO_CREATE_IDENTITY_ASSERTION` (36) (1.26.60).
- The client's `idp.domain` is the `iss` claim of the player's `GameServerToken`, or empty when that token does not parse or has no `iss`, as a self-signed token has none. The operator's `idp.domain` is the fixed string `self`. BDS does not check the offer's `idp.domain` (1.26.60).
- `cpk` in the `GameServerToken` is a string public key. Base64 DER SubjectPublicKeyInfo is the form BDS accepts (1.26.60).
- That key is P-256, P-384 or P-521, or RSA with a modulus of exactly 1024, 2048 or 4096 bits. A `cpk` that is not a JSON string reads as empty and fails (1.26.60).
- The string `cpk` is standard base64 decoded as `Verification` describes, but only `+` and `/` are digits 62 and 63. A `-` or `_` ends it (1.26.60).
- BDS accepts a self-signed player token with no Xbox chain, even with `online-mode=true` (1.26.60).
- The client signs a self-signed token with its own P-384 key and `ES384`. The header is `{"alg":"ES384","x5u":<key>}`, or `{"alg":"ES384","kid":"host","x5u":<key>}` when the client signs as host, `<key>` its DER SubjectPublicKeyInfo in standard base64 with padding, with no `typ` (1.26.60).
- Its payload holds `xid`, `mid`, `nid`, `pid`, `xname`, `nname` and `pname` strings, `exp`, `cpk` (the same key as `x5u`), `aud`, `leguuid`, `ap` (an integer), and `"ishost":true` only for a host (1.26.60).
- `aud` is `api://auth-minecraft-services/multiplayer`, and `leguuid` is a lowercase hyphenated UUID (1.26.60).
- It has no `iss`, `iat` or `nbf`. `exp` is the signing time in Unix seconds plus 31536000, one year (1.26.60).

## Operator assertion

- The host's assertion is self-signed with the operator's key. The operator token has no `iss` (1.26.60).
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

- A token is at most 16384 bytes, 16384 included, and has exactly three dot-separated segments whose header and payload decode as JSON objects (1.26.60).
- A detached JWS is at most 16384 bytes, 16384 included (1.26.60).
- Every base64url value BDS reads, the JWS and JWT segments and the JWK `x`, `y`, `n` and `e`, takes both alphabets mixed: `-` and `+` are digit 62, `_` and `/` digit 63 (1.26.60).
- Decoding stops without error at the first `=` or the first byte outside `A-Z`, `a-z`, `0-9` and the digits 62 and 63, and the rest of the value is ignored. Padding passes and whitespace ends the value (1.26.60).
- A last group of 2 or 3 digits yields 1 or 2 bytes with its spare bits dropped, set or not. A last single digit yields nothing. A value that decodes to no bytes fails (1.26.60).
- A token's signature covers its header and payload segments as received. The `fingerprints` signature covers the received header segment, `.`, and the unpadded base64url of the recomputed payload (1.26.60).
- A JWK `cpk` is read for `kty`, then `crv`, `x` and `y`, or `n` and `e`. Every other member, a private `d` included, is ignored (1.26.60).
- `kty` is exactly `EC` or `RSA`, and `crv` exactly `P-256`, `P-384` or `P-521` (1.26.60).
- EC `x` and `y` are big-endian integers of any non-empty length, so shorter values and leading zero bytes pass. The point must lie on the curve with both coordinates below the field prime (1.26.60).
- An RSA JWK `n` decodes to exactly 128, 256 or 512 bytes, leading zero bytes counted. `e` is any non-empty big-endian value, leading zeros ignored (1.26.60).
- An RSA key pairs with any of `RS256`, `RS384` and `RS512` (1.26.60).
- This library deliberately deviates: it rejects an RSA exponent that cryptography refuses, below 3 or even.
- Both sides need a non-empty `fingerprints`, a non-empty `token` and a `cpk` claim that parses as a key, then verify `fingerprints` under `cpk` over the received SDP's payload (1.26.60).
- The host verifying a player reads `cpk` as a string key and does not check the token's own signature (1.26.60).
- `cpk` is the only member of a player's token the host reads. It reads no header member, so `alg`, `kid` and `x5u` are free (1.26.60).
- `xid`, `mid`, `nid`, `pid`, `xname`, `nname`, `pname`, `exp`, `aud`, `leguuid`, `ap` and `ishost` may each be missing or hold any type or value. `aud` is not compared with `api://auth-minecraft-services/multiplayer` (1.26.60).
- Every failure of the host's check on a player, a missing or bad `cpk` included, ends in `CONNECTERROR` with `SessionError.IDENTITY_NOT_ALLOWED` (37) (1.26.60).
- The host checks a player only on a `CONNECTREQUEST` that came over the signaling channel, HTTP for BDS. One that came over LAN is accepted without the check, with or without an `a=identity` line and whatever that line holds. Only the channel decides this (1.26.60).
- The client verifying an operator reads `cpk` as a JWK and checks the token's signature under it. The token's `alg` must match the key's type (1.26.60).
- No side reads `iat`, `exp` or `nbf`. Expiry and clock skew are never checked (1.26.60).
- Verification yields only pass or fail, with no error code per failure (1.26.60).
- `http-signaling.md` says how BDS answers an offer without `a=identity`.
