"""Echo client on the LAN.

Discovers echo_server.py, connects to the first host it finds, sends a few messages and
prints the replies. The server must use the same port and Application Id.

Pass --loopback (and start the server with it too) to run both ends on one machine.
"""

import argparse
import asyncio

import nethernet
from nethernet import SendType


def transport_options(port: int, application_id: int, loopback: bool) -> dict:
    """Bind and broadcast settings shared by discover() and connect()."""
    if not loopback:
        return {"port": port, "application_id": application_id}
    # Bind an ephemeral port on the loopback interface and aim requests straight at the host,
    # since a broadcast never reaches another process on this machine.
    return {
        "port": 0,
        "application_id": application_id,
        "bind_host": "127.0.0.1",
        "broadcast_host": "127.0.0.1",
        "broadcast_port": port,
    }


async def main(port: int, application_id: int, timeout: float, loopback: bool) -> None:
    options = transport_options(port, application_id, loopback)
    hosts = [host async for host in nethernet.discover(timeout=timeout, **options)]
    if not hosts:
        print("no hosts found")
        return

    host = hosts[0]
    print(f"found {host.network_id}: {host.advertisement!r}")
    connection = await nethernet.connect(host, **options)
    async with connection:
        print(f"connected to {connection.remote_id}")
        for line in ("hello", "nethernet", "goodbye"):
            await connection.send(line.encode(), SendType.RELIABLE)
            reply = await asyncio.wait_for(connection.recv(), timeout=5)
            print(f"echoed: {reply.decode()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7551)
    parser.add_argument("--app-id", type=lambda v: int(v, 0), default=0xDEADBEEF)
    parser.add_argument("--timeout", type=float, default=3.0, help="discovery timeout")
    parser.add_argument("--loopback", action="store_true", help="talk to a server on this machine")
    args = parser.parse_args()
    asyncio.run(main(args.port, args.app_id, args.timeout, args.loopback))
