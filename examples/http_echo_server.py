"""Echo server using HTTP signaling.

Serves the /v1/join endpoints a Minecraft client posts its SDP offer to, and signs every
answer with an operator identity assertion. Run this, then run http_echo_client.py.

The keypair is generated per run, so the client sees a new key each time. A real deployment
loads one long-lived key from a secrets manager and shares it across the fleet, because
clients pin the key rather than the address.
"""

import argparse
import asyncio

import nethernet
from nethernet import IdentitySigner, SendType, generate_operator_key
from nethernet.identity import cpk_digest


async def handle(connection: nethernet.Connection) -> None:
    print(f"connected: {connection.remote_id}")
    async for packet in connection:
        await connection.send(packet, SendType.RELIABLE)
    print(f"disconnected: {connection.remote_id}")


def log_offer(offer: nethernet.IncomingOffer) -> None:
    """Authorization hook. Raise SignalingRejected here to refuse the connection."""
    client = "an identity assertion" if offer.identity else "no identity assertion"
    print(f"offer from {offer.network_id_text} with {client}")


async def main(host: str, port: int, domain: str) -> None:
    key = generate_operator_key()
    print(f"operator key fingerprint: {cpk_digest(key.public_key())}")

    server = nethernet.serve_http(
        handle,
        host=host,
        port=port,
        identity_signer=IdentitySigner(key, domain=domain),
        validate_offer=log_offer,
    )
    async with server:
        print(f"signaling on http://{host}:{server.bound_port}/v1/join")
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--domain", default="partner.example")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.host, args.port, args.domain))
    except KeyboardInterrupt:
        pass
