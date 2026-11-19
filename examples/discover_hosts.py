import argparse
import asyncio
import contextlib

import nethernet


async def main(timeout: float, port: int, broadcast_host: str, broadcast_port: int | None) -> None:
    hosts = nethernet.discover(
        timeout=timeout, port=port, broadcast_host=broadcast_host, broadcast_port=broadcast_port
    )
    async with contextlib.aclosing(hosts):
        async for host in hosts:
            print(f"{host.network_id} {host.name} at {host.address[0]}:{host.address[1]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--port", type=int, default=7551)
    parser.add_argument("--broadcast-host", default="255.255.255.255")
    parser.add_argument("--broadcast-port", type=int)
    args = parser.parse_args()
    asyncio.run(main(args.timeout, args.port, args.broadcast_host, args.broadcast_port))
