# nethernet

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A Python implementation of the NetherNet transport, built on asyncio with WebRTC provided by
[aiortc](https://github.com/aiortc/aiortc).

```python
import nethernet

async for host in nethernet.discover(timeout=3):
    async with await nethernet.connect(host) as conn:
        await conn.send(b"\x06\xc1...")
        print(await conn.recv())
    break
```

## Installation

```shell
pip install nethernet-python
```

or, with uv:

```shell
uv add nethernet-python
```

The import name is `nethernet`. Requires Python 3.11 or newer.

## Entry points

| Entry points | Purpose |
| --- | --- |
| `serve` / `connect` / `discover` | accept, dial and find NetherNet hosts |
| `serve_http` / `connect_http` | accept and dial NetherNet hosts by address |

Both hand back the same `Connection`, a message pipe with `send` / `recv`, and one class
serves both ends of a connection. The address-based entry points follow Mojang's
[NetherNet onboarding guide](https://github.com/Mojang/bedrock-protocol-docs/blob/main/NetherNetOnboardingGuide.md).

## Quick start

### Server

```python
import asyncio, secrets, nethernet
from nethernet import SendType, NetworkID

async def handle(connection):
    async for packet in connection:
        await connection.send(packet, SendType.RELIABLE)

async def main():
    local_id = NetworkID.p2p(secrets.randbits(64))
    async with nethernet.serve(handle, local_id, advertisement=b"MCPE;...") as server:
        await server.serve_forever()

asyncio.run(main())
```

### Client

```python
async for host in nethernet.discover(timeout=3):
    print(host.network_id, host.advertisement)
    async with await nethernet.connect(host) as conn:
        await conn.send(b"hello", SendType.RELIABLE)
        reply = await conn.recv()
    break
```

### Serving and dialing by address

`serve_http` exposes the endpoints a Bedrock client posts its offer to. Every answer must carry
an operator identity assertion or the client refuses the connection:

```python
from nethernet import IdentitySigner, generate_operator_key

key = generate_operator_key()
async with nethernet.serve_http(
    handle, port=8080,
    identity_signer=IdentitySigner(key, domain="partner.example"),
) as server:
    await server.serve_forever()
```

```python
conn = await nethernet.connect_http("https://partner.example")
```

More runnable programs are in [examples/](examples).

## Messages

`send` and `recv` move whole application packets; there is no added framing to parse. On the
wire each packet carries a one-byte fragment header, which the library adds and strips for you.
Reliability is per-call:

```python
await conn.send(data, SendType.RELIABLE)
await conn.send(data, SendType.UNRELIABLE)
```

Reliable packets are fragmented as needed, up to about 64 MiB. Unreliable ones are never
fragmented, so anything over 262143 bytes is dropped rather than sent.

Carrying Bedrock itself is one packet batch per message: length-prefixed game packets, without
the `0xFE` byte RakNet prefixes a batch with. A batch sent with that byte is silently ignored.

## Identity assertions

Both signaling directions can carry an `a=identity` assertion in the SDP: the client asserts an
authenticated player, the server asserts a long-lived operator key. This library implements the
envelope mechanics — canonical JSON, detached JWS over the DTLS fingerprints, the self-signed
`cpk` token — and leaves policy to you.

| Concern | Where it lives |
| --- | --- |
| signing an answer, verifying structure | `IdentitySigner`, `verify_server_identity` |
| authorizing a player from their token | your `validate_offer` callback |
| pinning an operator key across connections | your `on_server_identity` callback |

Validating a `GameServerToken` against Minecraft's auth service, reading XUIDs, and storing
trust-on-first-use pins are all application decisions, so they stay outside the transport.

## Errors

Failures raise exceptions rather than returning sentinels. All inherit from `NetherNetError`.

| Exception | Raised when |
| --- | --- |
| `ConnectionFailed` | a dial never reached the connected state (`.error` carries the cause) |
| `ConnectionClosed` | `send` / `recv` on a closed connection; `.error` is `NONE` for a clean close |
| `InvalidIdentity` | an `a=identity` assertion is missing, malformed, or fails verification |
| `SignalingRejected` | raise it from `validate_offer` to refuse an offer |

`.error` is a `SessionError`, the same code Bedrock reports: negotiation timeouts, ICE
failure, data-channel close.

## Development

This project uses [`uv`](https://docs.astral.sh/uv/).

```shell
uv sync --extra dev
uv run pytest
uv run ruff check
```

## License

MIT. See [LICENSE](LICENSE).
