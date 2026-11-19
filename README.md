# nethernet

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A Python implementation of the NetherNet transport, built on asyncio with WebRTC provided by
[aiortc](https://github.com/aiortc/aiortc).

```python
import contextlib, nethernet

hosts = nethernet.discover(timeout=3)
async with contextlib.aclosing(hosts):
    async for host in hosts:
        async with nethernet.connect(host) as conn:
            await conn.send(b"�...")
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

| Entry point | Purpose |
| --- | --- |
| `serve` | host connections, with HTTP and LAN signaling together |
| `connect` | dial a host by address, `NetworkID` or discovered host |
| `discover` | find hosts on the LAN |

`connect` and `serve` hand back the same `Connection`, a message pipe with `send` / `recv`. A
string target is an address, `host`, `host:port` or a URL, and signals over HTTP as in Mojang's
[NetherNet onboarding guide](https://github.com/Mojang/bedrock-protocol-docs/blob/main/NetherNetOnboardingGuide.md).
A `NetworkID` or a host from `discover` signals over the LAN.

## Quick start

### Server

```python
import asyncio, nethernet

async def handle(connection):
    try:
        async for packet in connection:
            await connection.send(packet)
    except nethernet.ConnectionClosed:
        pass

async def main():
    async with nethernet.serve(handle, name="Echo") as server:
        await server.serve_forever()

asyncio.run(main())
```

`serve` listens for HTTP signaling on `[::]:19132` and for LAN discovery and signaling on UDP
7551, and answers discovery requests without broadcasting its own.

### Client

```python
async with nethernet.connect("192.168.1.20") as conn:
    await conn.send(b"hello")
    reply = await conn.recv()
```

Over HTTP `connect` signs the offer with a fresh self-signed player, or with the `PlayerSigner`
you pass. To pin the host's operator key, pass `on_server_identity`.

### Discovery

```python
hosts = nethernet.discover(timeout=3)
async with contextlib.aclosing(hosts):
    async for host in hosts:
        print(host.network_id, host.name)
```

Runnable programs are in [examples/](examples): [serve_echo.py](examples/serve_echo.py),
[connect_echo.py](examples/connect_echo.py) and [discover_hosts.py](examples/discover_hosts.py).

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
| signing an answer or an offer | `IdentitySigner`, `PlayerSigner` |
| authorizing a player from their token | your `validate_offer` callback |
| pinning an operator key across connections | your `on_server_identity` callback |

Validating a `GameServerToken` against Minecraft's auth service, reading XUIDs, and storing
trust-on-first-use pins are all application decisions, so they stay outside the transport.

## Errors

Failures raise exceptions rather than returning sentinels. All inherit from `NetherNetError`.

| Exception | Raised when |
| --- | --- |
| `ConnectionFailed` | a dial never reached the connected state (`.error` carries the cause) |
| `ConnectionClosed` | `send` / `recv` on a closed connection; `.error` is `NONE` when this end closed; a peer's close arrives as `DATA_CHANNEL_CLOSED` |
| `InvalidIdentity` | an `a=identity` assertion is missing, malformed, or fails verification |

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
