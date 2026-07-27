"""Echo server on the LAN.

Advertises itself so clients can find it with discovery, then echoes every packet back.
Run this, then run echo_client.py on another machine on the same LAN.

To try both ends on one machine, pass --loopback here and to the client. Discovery normally
relies on UDP broadcast, which the host's own processes do not receive.
"""

import argparse
import asyncio
import secrets

import nethernet
from nethernet import ESendType, NetworkID


async def handle(connection: nethernet.Connection) -> None:
    print(f"connected: {connection.remote_id}")
    async for packet in connection:
        await connection.send(packet, ESendType.RELIABLE)
    print(f"disconnected: {connection.remote_id}")


async def main(port: int, application_id: int, loopback: bool) -> None:
    local_id = NetworkID.p2p(secrets.randbits(64))
    server = nethernet.serve(
        handle,
        local_id,
        advertisement=b"MCPE;NetherNet-Python;0;0.0.0;0;10;",
        port=port,
        application_id=application_id,
        bind_host="127.0.0.1" if loopback else "",
    )
    async with server:
        print(f"hosting as {local_id} on port {server.bound_port}")
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7551)
    parser.add_argument("--app-id", type=lambda v: int(v, 0), default=0xDEADBEEF)
    parser.add_argument("--loopback", action="store_true", help="bind 127.0.0.1 for a local demo")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.port, args.app_id, args.loopback))
    except KeyboardInterrupt:
        pass
