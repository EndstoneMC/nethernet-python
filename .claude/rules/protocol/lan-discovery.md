---
paths:
  - "src/nethernet/discovery/__init__.py"
  - "src/nethernet/discovery/packets.py"
  - "src/nethernet/discovery/lan.py"
  - "tests/test_discovery_packets.py"
  - "tests/test_lan_discovery.py"
  - "tests/test_conformance.py"
---

# LAN discovery

LAN peers find each other and exchange signaling over UDP with no server. `lan-crypto.md` seals every datagram, so both peers need the same application id. `config.md` holds the discovery port, the broadcast interval and the application id.

## Socket

- One UDP socket per LAN endpoint, IPv6 and dual stack: `IPV6_V6ONLY` 0, `SO_BROADCAST` 1, `SO_RCVBUF` 32768, bound to `[::]` on the discovery port (1.26.60).
- After the bind it joins `ff02::1` on interface index 0 and sets `IPV6_MULTICAST_LOOP` 0. A failure of either option, or of `SO_RCVBUF`, is ignored (1.26.60).
- When the IPv6 socket cannot be created, configured or bound for any reason but the port being in use, fall back to an IPv4 socket: `SO_BROADCAST` 1, `SO_RCVBUF` 32768, bound to `0.0.0.0` on the same port, with no multicast group (1.26.60).
- When the port is in use, retry the whole sequence once on port 0, an ephemeral port. An in-use port on the IPv6 bind skips the IPv4 fallback (1.26.60).
- `SO_REUSEADDR` is not set, so a second endpoint on the same host lands on an ephemeral port (1.26.60).
- Any other failure, or a failed retry, leaves the endpoint with no socket. It sends and receives nothing until the socket is rebuilt (1.26.60).
- On the dual-stack socket an IPv4 destination goes out as its IPv4-mapped address `::ffff:a.b.c.d`. On the IPv4 socket an IPv6 destination fails with address family not supported (1.26.60).
- A send that fails with a broken pipe error drops the socket and builds a new one as above, trying the discovery port first (1.26.60).
- Peers learn an endpoint's real port from the UDP source address. Requests always go to the discovery port, so an endpoint on an ephemeral port gets Responses and Messages but no Requests (1.26.60).
- BDS binds `[::]` on the discovery port at startup, exclusively: another process cannot bind it, even with `SO_REUSEADDR` (1.26.60).
- BDS binds it whatever `enable-lan-visibility` says. With `enable-lan-visibility=false` it answers no Request but still processes Messages and Responses, the peer table and the probe included (1.26.60).
- An idle BDS sends no Requests: a listener on `0.0.0.0` on the discovery port saw no datagram in 30 s (1.26.60).
- This library hands one `socket.AF_INET6` socket, or the IPv4 fallback, to `create_datagram_endpoint(sock=...)`.

## Base header

Every packet starts with this packed header, 20 bytes, no padding, all integers little-endian (1.26.60):

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 2 | `PacketLength` (`u16`): header size plus payload size |
| 2 | 2 | `PacketType` (`u16`): 0 Request, 1 Response, 2 Message |
| 4 | 8 | `SenderId` (`u64`): the sender's P2P `NetworkID` |
| 12 | 8 | padding: zero on send, ignored on receive |

- `SenderId` sits at offset 4, unaligned. A naturally aligned layout is wrong on the wire.

## Request

- Type 0: the base header alone, `PacketLength = 20`, `SenderId` set to the id enabled for broadcast discovery (1.26.60).

## Response

Type 1, a 24-byte header (1.26.60):

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 20 | base header |
| 20 | 4 | `ApplicationDataLength` (`u32`) |
| 24 | ... | application data |

- With N bytes of application data, `ApplicationDataLength = min(N, 1148)` and `PacketLength = 24 + ApplicationDataLength`. The datagram still carries all N bytes (1.26.60).
- The application data is the application's host advertisement, fetched again for each Request (1.26.60). BDS sends the record in Host advertisement.

## Host advertisement

BDS's application data is the lowercase hex text of this binary record, so `ApplicationDataLength` is twice the record size (1.26.60):

| # | Field | Encoding | BDS value |
| ---: | --- | --- | --- |
| 1 | `dataVersion` | `u8` | 7 |
| 2 | `name` | varint length, UTF-8 | the server name |
| 3 | `protocol` | zigzag varint | 2225 |
| 4 | `version` | varint length, text | `1.26.60` |
| 5 | `level` | varint length, text | the level name |
| 6 | `players` | zigzag varint | players online |
| 7 | `maxPlayers` | zigzag varint | the player limit |
| 8 | `gameType` | zigzag varint | the level's game type |
| 9 | `editor` | bool, 1 byte | editor world |
| 10 | `hardcore` | bool, 1 byte | hardcore world |
| 11 | `onlineAuth` | bool, 1 byte | online authentication on |
| 12 | `selfSignedAuth` | bool, 1 byte | self-signed authentication on |
| 13 | `nonce` | varint length, text | see below |
| 14 | `connection` | zigzag varint | 4 or 7 |

- The nonce is a random `u64` in lowercase hex with no zero padding. It is new at every restart (1.26.60).
- The nonce is checked only when the advertisement updates: at startup, when a player joins or leaves, and when the player limit changes. An update more than 300 s of monotonic time after the nonce was drawn draws a new one. An idle host keeps its nonce (1.26.60).
- `connection` is a connection-type value, not the transport: -1 undefined, 0 local, 1 IPv4, 2 IPv6, 3 plain WebSocket signaling, 4 LAN WebRTC signaling, 5 NAT, 6 UPnP, 7 JSON-RPC WebSocket signaling, 8 unknown IP (1.26.60).
- BDS sends 7 only while its link to the online signaling service is up, and 4 otherwise. A host without that link, such as a LAN or HTTP-only host, sends 4 (1.26.60).
- The client never checks `dataVersion`. It parses every value with the same 14 fields and drops only bytes that fail to parse (1.26.60).
- This library has no online signaling link, so it advertises 4.
- A stock BDS record is 67 bytes, 134 hex characters (1.26.60).

## Message

Type 2, a 32-byte header (1.26.60):

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 20 | base header |
| 20 | 8 | `RecipientId` (`u64`): the addressee's P2P `NetworkID` |
| 28 | 4 | `MessageDataLength` (`u32`) |
| 32 | ... | message data |

- With N bytes of message data, `MessageDataLength = min(N, 1140)` and `PacketLength = 32 + MessageDataLength`. The datagram still carries all N bytes (1.26.60).
- 1140 caps only the length fields, not the data. A message over 1140 bytes, such as a full SDP, goes out whole in one datagram, and a receiver sizes it from the datagram. BDS accepts an 1870-byte offer whose header says 1140 (1.26.60).
- The message data is one signaling message string (`signaling.md`).

## Receive

- Open every datagram (`lan-crypto.md`) before parsing.
- Drop a datagram under 4 bytes, then one shorter than its `PacketLength`, then one with an unknown `PacketType`, then one shorter than its type's header: 20, 24 or 32 bytes (1.26.60).
- A datagram longer than its `PacketLength` is accepted (1.26.60).
- The payload runs from the end of the header to the end of the datagram. `ApplicationDataLength` and `MessageDataLength` are ignored, and an empty payload is valid (1.26.60).
- Ignore a Request whose `SenderId` is the local id, and deliver no Response whose `SenderId` is the local id. A Request with `SenderId = 0` is answered (1.26.60).
- A Message is never filtered on `SenderId`. One from the local id updates the peer table and gets the probe like any other (1.26.60).
- Request: reply with a Response with `SenderId` set to the local id and the application's advertisement, unicast to the Request's source address and port. A Request is answered whether or not the endpoint broadcasts Requests itself, and a failed reply is ignored (1.26.60).
- BDS answers a Request sent to loopback, `255.255.255.255`, a directed broadcast, its unicast address or `[::1]` with one unicast Response from the discovery port. A stock BDS Response is 158 bytes (1.26.60).
- Response: deliver `(SenderId, application data)` to the application as a discovered host (1.26.60).
- A Response or Message with a non-zero `SenderId` updates the peer table and sends any probe first. A Request never does (1.26.60).
- Then a Message with `RecipientId` equal to the local id feeds its data to signaling as a LAN message from `SenderId`. One addressed to another id is dropped, after its sender was recorded (1.26.60).
- BDS answers a LAN offer with one Message holding `CONNECTRESPONSE`: candidates inline in the SDP, no `CANDIDATEADD`, no `a=identity`, `a=setup:active` (`session.md`) (1.26.60).

## Broadcast discovery

- The application enables broadcast discovery per local id and disables it per id. Enabling an id twice does nothing (1.26.60).
- Enabling the first id runs a tick at once. Each tick re-arms itself one broadcast interval after it ends, with no backoff or jitter (1.26.60).
- Disabling every id and enabling one again before the pending tick runs starts a second tick chain, so Requests go out twice per interval (1.26.60).

Each tick, in order (1.26.60):

1. With no id enabled, clear the broadcast addresses and stop. The tick is not re-armed.
2. With no broadcast addresses and no interface scan running, start an interface scan. It completes later, so the first tick usually sends only to `255.255.255.255`.
3. For each enabled id, send one Request to each broadcast address, then one to `255.255.255.255`. Every Request goes to the discovery port, never to the endpoint's ephemeral port.
4. Re-arm.

## Broadcast addresses

- An interface scan builds the broadcast addresses from every interface address (1.26.60):
  - Skip loopback, `127.0.0.0/8` and `::1`.
  - Skip IPv4 link-local, `169.254.0.0/16`.
  - An IPv4 address adds its directed broadcast, the address with every host bit set. A prefix of 0 or 32 adds the address itself.
  - Any IPv6 address, link-local included, adds `ff02::1` once, on the dual-stack socket only. On the IPv4 socket an IPv6 address is skipped.
- A finished scan replaces the set. A scan that finds nothing leaves it empty (1.26.60).
- No timer refreshes the set. A new scan starts only on a tick that finds the set empty, so an offline host scans every tick (1.26.60).
- A Request that fails with host unreachable, network unreachable, address family not supported or bad address removes that broadcast address. Other errors keep it, and `255.255.255.255` is never removed (1.26.60).

## Peer table

- A record maps a `SenderId` to an address and a last-active time (1.26.60).
- A Response or Message with a non-zero `SenderId` adds its sender, or refreshes the record's time and replaces its address with the datagram's source address and port (1.26.60).
- Never record `SenderId = 0` (1.26.60).
- A record expires once 60 s pass since its last-active time. Expiry is checked on every lookup and update, with no timer (1.26.60).
- A peer is on the LAN while its record lives, manual hosts included (1.26.60).

### Manual hosts

- Adding a LAN host by id, IP text and port stores a record that never expires. On an existing record it replaces the address and makes the record permanent. It sends no probe (1.26.60).
- A later Response or Message from that id refreshes it like any peer, so it then expires 60 s after its last datagram (1.26.60).
- Removing a LAN host does not erase it. It resets the record's time to now, so the peer stays on the LAN and reachable for 60 s more. Removing an id with no record does nothing (1.26.60).

## First-discovery probe

- When a Response or Message adds a new record, send that peer a Message with `SenderId = 0`, `RecipientId` set to the peer, empty message data and `PacketLength = 32`, to its recorded address (1.26.60).
- A refresh sends no probe, even when the address changed (1.26.60).
- A zero `SenderId` is never recorded, so no probe echoes back. Signaling drops data with no space, the empty probe data included (`signaling.md`) (1.26.60).
- Sending the probe is optional. Receiving one has no side effect.

## Send

- A signaling message goes out as a Message with `SenderId` set to the local id and `RecipientId` to the target, unicast to the target's recorded address. It is never broadcast (1.26.60).
- A target with no live record gets nothing: no datagram and no log. The LAN send result is an unknown-peer error that no session reports (`session.md`) (1.26.60).

## Suspend and resume

- While LAN is suspended the endpoint sends, receives and ticks nothing. Its socket stays open (1.26.60).
- On resume it drops the socket and builds a new one as in Socket, then runs any overdue tick at once (1.26.60).
