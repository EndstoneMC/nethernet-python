"""Example NetherNet server: advertise on the LAN, accept connections, and echo packets.

Run on one machine::

    uv run python examples/server.py

then run ``client.py`` on another machine on the same LAN (same Application Id and port).
"""

from __future__ import annotations

import asyncio
import secrets

import nethernet
from nethernet import ESendType, NetworkID


async def handler(conn: nethernet.Connection) -> None:
    """Echo every packet back until the peer disconnects."""
    print(f"connection from {conn.remote_id}")
    async with conn:
        async for packet in conn:
            print(f"recv {len(packet)} bytes; echoing")
            await conn.send(packet, ESendType.RELIABLE)


async def main() -> None:
    local_id = NetworkID.p2p(secrets.randbits(64))
    async with nethernet.serve(
        handler, local_id, advertisement=b"MCPE;NetherNet-Python;0;0.0.0;0;10;"
    ) as server:
        print(f"hosting as {local_id} ({local_id.correlation_id()})")
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
