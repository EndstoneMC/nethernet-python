"""A Python implementation of the NetherNet transport, built on asyncio and aiortc."""

from importlib.metadata import version

from nethernet.api import (
    Connection,
    DiscoveredHost,
    IncomingOffer,
    Server,
    connect,
    discover,
    serve,
)
from nethernet.discovery.lan import Address
from nethernet.errors import (
    ConnectionClosed,
    ConnectionFailed,
    ConnectionFlags,
    NetherNetError,
    SendType,
    SessionError,
)
from nethernet.identity import (
    IdentityEnvelope,
    IdentitySigner,
    InvalidIdentity,
    PlayerSigner,
    ServerIdentity,
    generate_operator_key,
)
from nethernet.network_id import NetworkID, NetworkIDType, new_connection_id
from nethernet.session import SessionState

__version__ = version("nethernet-python")

__all__ = [
    "Address",
    "Connection",
    "ConnectionClosed",
    "ConnectionFailed",
    "ConnectionFlags",
    "DiscoveredHost",
    "IdentityEnvelope",
    "IdentitySigner",
    "IncomingOffer",
    "InvalidIdentity",
    "NetherNetError",
    "NetworkID",
    "NetworkIDType",
    "PlayerSigner",
    "SendType",
    "Server",
    "ServerIdentity",
    "SessionError",
    "SessionState",
    "__version__",
    "connect",
    "discover",
    "generate_operator_key",
    "new_connection_id",
    "serve",
]
