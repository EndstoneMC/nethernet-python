import asyncio
from types import SimpleNamespace
from unittest import mock

from aioice import Candidate
from aioice.ice import CandidatePair
from aiortc import RTCPeerConnection, RTCSessionDescription

from nethernet.errors import SessionError
from nethernet.network_id import NetworkID
from nethernet.session_manager import SessionManager
from nethernet.signaling.messages import parse
from nethernet.transport import peer_connection as peer_connection_module
from nethernet.transport.peer_connection import PeerConnection, candidate_to_message

LOCAL = NetworkID.p2p(1000)
REMOTE = NetworkID.p2p(2000)
OTHER = NetworkID.p2p(3000)

HOST_CANDIDATE = "candidate:1 1 udp 2130706431 192.168.1.20 50000 typ host"
SRFLX_CANDIDATE = (
    "candidate:2 1 udp 1694498815 203.0.113.7 50001 typ srflx raddr 192.168.1.20 rport 50000"
)
REMOTE_CANDIDATE = "candidate:9 1 udp 2130706431 10.0.0.5 54321 typ host"


def sdp_with_candidates(kind: str) -> str:
    return (
        "v=0\r\n"
        f"o=- {kind} 1 IN IP4 0.0.0.0\r\n"
        "s=-\r\n"
        "t=0 0\r\n"
        "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
        "c=IN IP4 0.0.0.0\r\n"
        "a=mid:0\r\n"
        "a=sctp-port:5000\r\n"
        "a=max-message-size:262144\r\n"
        f"a={HOST_CANDIDATE}\r\n"
        f"a={SRFLX_CANDIDATE}\r\n"
        "a=end-of-candidates\r\n"
        "a=ice-ufrag:abcd\r\n"
        "a=ice-pwd:abcdefghijklmnopqrstuvwx\r\n"
        "a=fingerprint:sha-256 AA:BB:CC\r\n"
        "a=setup:actpass\r\n"
    )


OFFER = sdp_with_candidates("offer")
ANSWER = sdp_with_candidates("answer")


class FakeRTCPeerConnection(RTCPeerConnection):
    fail_on: dict[str, SessionError] = {}

    def __init__(self, **kwargs):
        if "create" in self.fail_on:
            raise RuntimeError("create")
        super().__init__(**kwargs)
        self.ice_state = "new"
        self.local: RTCSessionDescription | None = None
        self.remote: RTCSessionDescription | None = None
        self.applied: list[str] = []
        self.events: list[str] = []
        self.closed = False

    async def createOffer(self):
        if "offer" in self.fail_on:
            raise RuntimeError("offer")
        return RTCSessionDescription(OFFER, "offer")

    async def createAnswer(self):
        if "answer" in self.fail_on:
            raise RuntimeError("answer")
        return RTCSessionDescription(ANSWER, "answer")

    async def setLocalDescription(self, description):
        self.local = description
        self.events.append("local")

    async def setRemoteDescription(self, description):
        if "remote" in self.fail_on:
            raise RuntimeError("remote")
        self.remote = description
        self.events.append("remote")

    async def addIceCandidate(self, candidate):
        text = candidate_to_message(candidate)
        self.applied.append(text)
        self.events.append(f"candidate {text}")

    @property
    def localDescription(self):
        return self.local

    @property
    def remoteDescription(self):
        return self.remote

    @property
    def iceConnectionState(self):
        return self.ice_state

    async def close(self):
        self.closed = True


class FakePeerConnection(PeerConnection):
    fail_on: dict[str, SessionError] = {}

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        rtc = type("Rtc", (FakeRTCPeerConnection,), {"fail_on": self.fail_on})
        with mock.patch.object(peer_connection_module, "RTCPeerConnection", rtc):
            super().__init__(**kwargs)

    @property
    def rtc(self) -> FakeRTCPeerConnection:
        return self._pc

    @property
    def remote_sdp(self):
        return self.rtc.remote.sdp if self.rtc.remote is not None else None

    def set_ice_state(self, state: str) -> None:
        self.rtc.ice_state = state

    def drive_ice(self, state: str) -> None:
        self.rtc.ice_state = state
        self.rtc.emit("iceconnectionstatechange")

    def nominate(self, local: str, remote: str, state: CandidatePair.State) -> None:
        pair = SimpleNamespace(
            local_candidate=Candidate.from_sdp(local.removeprefix("candidate:")),
            remote_candidate=Candidate.from_sdp(remote.removeprefix("candidate:")),
            state=state,
        )
        self.rtc.sctp.transport.transport._connection._nominated[1] = pair


def failing(**fail_on: SessionError) -> type[FakePeerConnection]:
    return type("FailingPeerConnection", (FakePeerConnection,), {"fail_on": fail_on})


class Channels:
    def __init__(self, *, http_errors=None):
        self.lan: list[tuple[NetworkID, str]] = []
        self.http: list[tuple[NetworkID, str]] = []
        self.http_errors = list(http_errors or [])

    def lan_send(self, remote, message):
        self.lan.append((remote, message))

    async def http_send(self, remote, message):
        self.http.append((remote, message))
        return self.http_errors.pop(0) if self.http_errors else SessionError.NONE

    def lan_messages(self):
        return [parse(message) for _, message in self.lan]

    def http_messages(self):
        return [parse(message) for _, message in self.http]


class Recorder:
    def __init__(self):
        self.opened = []
        self.closed = []
        self.packets = []

    def on_open(self, session):
        self.opened.append(session)

    def on_close(self, session, error, summary):
        self.closed.append((session, error, summary))

    def errors(self):
        return [error for _, error, _ in self.closed]


def make_manager(channels, recorder, *, lan=True, http=False, **kwargs):
    kwargs.setdefault("peer_connection_factory", FakePeerConnection)
    return SessionManager(
        local_id=LOCAL,
        lan_send=channels.lan_send if lan else None,
        http_send=channels.http_send if http else None,
        on_session_open=recorder.on_open,
        on_session_close=recorder.on_close,
        **kwargs,
    )


class VirtualClock:
    def __init__(self):
        self.now = 0.0
        asyncio.get_running_loop().time = self.time

    def time(self):
        return self.now

    async def advance(self, seconds: float, step: float = 0.25) -> None:
        target = self.now + seconds
        while self.now < target:
            self.now = min(target, self.now + step)
            await settle()


async def settle(rounds: int = 10) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)
