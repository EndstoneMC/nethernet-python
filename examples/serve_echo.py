import argparse
import asyncio

import nethernet


async def handle(connection: nethernet.Connection) -> None:
    print(f"connected: {connection.remote_id}")
    try:
        async for packet in connection:
            await connection.send(packet)
    except nethernet.ConnectionClosed:
        pass
    print(f"disconnected: {connection.remote_id}")


async def main(host: str, port: int, lan_port: int) -> None:
    async with nethernet.serve(handle, host=host, port=port, lan_port=lan_port) as server:
        print(f"serving on tcp {server.bound_port} and udp {server.lan_port}", flush=True)
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="")
    parser.add_argument("--port", type=int, default=19132)
    parser.add_argument("--lan-port", type=int, default=7551)
    args = parser.parse_args()
    try:
        asyncio.run(main(args.host, args.port, args.lan_port))
    except KeyboardInterrupt:
        pass
