"""nethernet — a cleanroom Python implementation of Minecraft Bedrock's NetherNet transport.

The wire protocol (LAN discovery, LAN signaling, WebRTC session negotiation, and data-channel
framing) is implemented from ``SPEC.md``, with WebRTC provided by aiortc.

Public API (see ``docs/api-design.md``): module-level :func:`connect` / :func:`serve` /
:func:`discover` returning a :class:`Connection` and :class:`Server`. The ``Transport`` engine
is internal (import from ``nethernet.transport_api`` for advanced use).

The partner HTTP signaling flow (NetherNet Onboarding Guide) is available as
:func:`connect_http` / :func:`serve_http` with the ``http`` extra; identity assertion
mechanics live in :mod:`nethernet.identity`.
"""

from nethernet.api import (
    Connection,
    DiscoveredHost,
    HttpServer,
    IncomingOffer,
    Server,
    connect,
    connect_http,
    discover,
    serve,
    serve_http,
)
from nethernet.discovery.lan import Address
from nethernet.errors import (
    ConnectionClosed,
    ConnectionFailed,
    EConnectionFlags,
    ESendType,
    ESessionError,
    NetherNetError,
)
from nethernet.identity import (
    IdentityEnvelope,
    IdentitySigner,
    InvalidIdentity,
    ServerIdentity,
    generate_operator_key,
)
from nethernet.network_id import NetworkID, NetworkIDType, new_connection_id
from nethernet.session import SessionState
from nethernet.signaling.http import SignalingRejected

__version__ = "0.0.1"

__all__ = [
    # entry points
    "connect",
    "serve",
    "discover",
    "connect_http",
    "serve_http",
    # connection objects
    "Connection",
    "Server",
    "HttpServer",
    "DiscoveredHost",
    "IncomingOffer",
    # identity assertions (Onboarding Guide s5)
    "IdentityEnvelope",
    "IdentitySigner",
    "ServerIdentity",
    "InvalidIdentity",
    "SignalingRejected",
    "generate_operator_key",
    # identity
    "NetworkID",
    "NetworkIDType",
    "new_connection_id",
    "Address",
    # state / enums
    "SessionState",
    "ESendType",
    "ESessionError",
    "EConnectionFlags",
    # exceptions
    "NetherNetError",
    "ConnectionFailed",
    "ConnectionClosed",
    "__version__",
]
