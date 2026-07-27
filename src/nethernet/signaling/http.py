"""HTTP signaling channel — NetherNet Onboarding Guide s4.

The partner flow: the dialer POSTs a full-ICE SDP offer to ``/v1/join/{networkId}`` and
receives the full-ICE answer in the response body; ``GET /v1/join`` is the capability check.
Requires the ``http`` extra (aiohttp).

aiohttp API verified against the official reference:
https://docs.aiohttp.org/en/stable/web_reference.html
https://docs.aiohttp.org/en/stable/client_reference.html
"""

from __future__ import annotations

import ssl
from collections.abc import Awaitable, Callable

try:
    import aiohttp
    from aiohttp import web
except ImportError:  # pragma: no cover - exercised only without the http extra
    aiohttp = None
    web = None

from nethernet.errors import ConnectionFailed, ESessionError, NetherNetError

SDP_CONTENT_TYPE = "application/sdp"

# answer_offer(network_id, offer_sdp) -> answer_sdp; network_id is the raw URL path segment.
AnswerOffer = Callable[[str, str], Awaitable[str]]


class SignalingRejected(NetherNetError):  # noqa: N818 - matches ConnectionFailed et al.
    """Reject an offer from an ``answer_offer`` / ``validate_offer`` callback (HTTP 403)."""

    def __init__(self, reason: str = "rejected", status: int = 403) -> None:
        self.status = status
        super().__init__(reason)


def _require_aiohttp() -> None:
    if aiohttp is None:
        raise NetherNetError("HTTP signaling requires aiohttp — install nethernet[http]")


async def request_answer(
    server_url: str,
    network_id: str,
    offer_sdp: str,
    *,
    capability_check: bool = True,
    timeout: float = 10.0,
) -> str:
    """POST the offer to ``{server_url}/v1/join/{network_id}`` and return the answer SDP.

    Exactly one POST per attempt, no retries (Guide s4). Raises :class:`ConnectionFailed` with
    ``NO_SIGNALING_CHANNEL`` if the capability check fails and ``SIGNALING_FAILED_TO_SEND`` on
    a non-2xx or failed POST.
    """
    _require_aiohttp()
    base = server_url.rstrip("/")
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as http:
        if capability_check:
            try:
                async with http.get(f"{base}/v1/join") as response:
                    supported = response.status // 100 == 2
            except (aiohttp.ClientError, OSError):
                supported = False
            if not supported:
                raise ConnectionFailed(ESessionError.NO_SIGNALING_CHANNEL)
        try:
            async with http.post(
                f"{base}/v1/join/{network_id}",
                data=offer_sdp.encode(),
                headers={"Content-Type": SDP_CONTENT_TYPE},
            ) as response:
                if response.status // 100 != 2:
                    raise ConnectionFailed(ESessionError.SIGNALING_FAILED_TO_SEND)
                return await response.text()
        except (aiohttp.ClientError, OSError):
            raise ConnectionFailed(ESessionError.SIGNALING_FAILED_TO_SEND) from None


class HttpSignalingServer:
    """Serves the two signaling endpoints; answers come from an ``answer_offer`` callback.

    ``GET /v1/join`` returns 2xx (capability check). ``POST /v1/join/{network_id}`` passes the
    body to the callback and returns its answer as ``application/sdp``; a raised
    :class:`SignalingRejected` maps to its HTTP status, any other :class:`NetherNetError`
    (e.g. a malformed identity envelope) to 400.
    """

    def __init__(
        self,
        answer_offer: AnswerOffer,
        *,
        host: str = "0.0.0.0",
        port: int = 0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        _require_aiohttp()
        self._answer_offer = answer_offer
        self._host = host
        self._port = port
        self._ssl_context = ssl_context
        self._runner: web.AppRunner | None = None
        self.bound_port: int | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/v1/join", self._handle_capability)
        app.router.add_post("/v1/join/{network_id}", self._handle_join)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port, ssl_context=self._ssl_context)
        await site.start()
        addresses = self._runner.addresses
        self.bound_port = addresses[0][1] if addresses else None

    async def aclose(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _handle_capability(self, request: web.Request) -> web.Response:
        return web.Response()

    async def _handle_join(self, request: web.Request) -> web.Response:
        offer_sdp = await request.text()
        try:
            answer = await self._answer_offer(request.match_info["network_id"], offer_sdp)
        except SignalingRejected as exc:
            return web.Response(status=exc.status, text=str(exc))
        except NetherNetError as exc:
            return web.Response(status=400, text=str(exc))
        return web.Response(text=answer, content_type=SDP_CONTENT_TYPE)
