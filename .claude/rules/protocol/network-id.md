---
paths:
  - "src/nethernet/network_id.py"
  - "tests/test_network_id.py"
---

# NetworkID

## Forms

| Form | Value | String form |
| --- | --- | --- |
| P2P | `u64` | its decimal digits, as `"1234567890"` |
| Realms | 128-bit UUID | the canonical UUID string |
| unset | none | empty |

- A P2P id prints as unsigned decimal with no sign, padding or leading zeros, so `0` prints `"0"` (1.26.60).
- A Realms id prints as lowercase hex in `8-4-4-4-12` groups, the 128-bit value most significant digit first (1.26.60).
- LAN discovery and LAN signaling carry only the P2P form, as a raw `u64` (1.26.60).
- A Realms id reaches BDS from the network only through online signaling, as a JSON string. The id in an HTTP signaling path is always P2P (1.26.60).

## JSON

- Written, a P2P id is a JSON number, a Realms id its string form and an unset id `null` (1.26.60).

Read, the JSON type picks the form (1.26.60):

- An integer is P2P. A negative integer `-n` is P2P `2^64 - n`, so `-1` is `18446744073709551615`.
- A bool is a number: `true` is P2P 1, `false` is P2P 0.
- A real truncates toward zero to a signed 64-bit integer and keeps only its low 32 bits. `1.5` and `1.0` give P2P 1, `5e9` gives P2P 705032704, `-1.0` gives P2P 4294967295, and an integer literal beyond the 64-bit range, which reads as a real, gives P2P 0. A real from 2^63 up, one below -2^63, NaN and an infinity also give P2P 0. `-0.5` gives P2P 0, `4294967296.0` gives 0 and `4294967297.9` gives 1.
- A string parses as below.
- `null`, an array or an object is unset.

## Parsing

Pick the form in this order (1.26.60):

1. An empty string is unset.
2. Every character is an ASCII decimal digit and the value fits a `u64`: P2P, leading zeros allowed. `"123abc"`, `" 123"`, `"+1"` and `"-1"` are not P2P.
3. Else exactly 32 hex digits, or 36 characters with `-` at offsets 8, 13, 18 and 23 and hex digits elsewhere, in either case: Realms. A 32-digit decimal string overflows a `u64` and so parses as Realms. Braced, `urn:uuid:` and every other UUID form are not accepted.
4. Else unset. Parsing never fails.

- The 32-digit form parses to the same UUID as its dashed form. The first 16 hex digits are the most significant half (1.26.60).
- An unset id is invalid. A P2P id of `0` and the nil UUID parse but are invalid (1.26.60).
- Two ids are equal only in the same form with the same value. Unset equals unset (1.26.60).

## Connection id

- The dialer draws a fresh random `u64` connection id for each outgoing session and puts it in every signaling message of that session. The listener answers with the same id (1.26.60).
- The id may be `0`: nothing rejects or redraws it (1.26.60).
- A session is the pair of the remote `NetworkID` and the connection id. Two remotes may use the same connection id at once (1.26.60).
- BDS draws no random `NetworkID` of either form. Its only random id is the connection id (1.26.60).
- Draw it from 8 bytes of `secrets`.

## Correlation id

- A P2P id renders for logs as `<nethernet>WWWW-WWWW-WWWW-WWWW`: the four 16-bit words of the `u64`, most significant first, each as 4 lowercase hex digits (1.26.60).
- A Realms id renders as `<nethernet>` followed by its UUID string (1.26.60).
- An invalid id renders as the empty string: unset, P2P `0` and the nil UUID (1.26.60).
- The StartGame packet's multiplayer correlation id carries the correlation id of the server's own `NetworkID` (1.26.60).
- The correlation id never appears in signaling or LAN discovery (1.26.60).
