"""SIP bridge configuration and constants."""

import os
import socket
from pathlib import Path

BUFFER_SIZE = 2048


def _read_rtpengine_interface() -> str | None:
    """Read interface= from config/rtpengine.conf so bridge advertises same IP as RTPEngine (Linphone must send RTP there)."""
    conf_path = os.environ.get("RTPENGINE_CONF", "").strip()
    if not conf_path:
        try:
            repo_root = Path(__file__).resolve().parent.parent
            conf_path = repo_root / "config" / "rtpengine.conf"
        except Exception:
            return None
    path = Path(conf_path)
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("interface="):
                    val = line.split("=", 1)[1].strip()
                    if val and val != "0.0.0.0":
                        return val
    except OSError:
        pass
    return None


def get_advertised_host_for_media() -> str:
    """IP where RTPEngine listens so Element/Linphone send media there. Must match RTPEngine's interface= in rtpengine.conf."""
    env = os.environ.get("MEDIABRIDGE_ADVERTISED_HOST", "").strip()
    if env:
        return env
    iface = _read_rtpengine_interface()
    if iface:
        return iface
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        addr = s.getsockname()[0]
        s.close()
        if addr and addr != "0.0.0.0":
            return addr
    except OSError:
        pass
    return "127.0.0.1"


def get_non_loopback_host() -> str | None:
    """Primary non-loopback IP (for c= in SIP SDP; many SIP UACs do not accept 127.0.0.1 in c=)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        addr = s.getsockname()[0]
        s.close()
        if addr and addr not in ("0.0.0.0", "127.0.0.1"):
            return addr
    except OSError:
        pass
    return None


SYNC_POLL_MS = 3000
ANSWER_TIMEOUT_SEC = 90
WAIT_JOIN_ROOM_SEC = 25
# After m.call.answer, poll sync for this many seconds for m.call.candidates. 0 = no extra wait (select_answer immediately).
WAIT_CANDIDATES_AFTER_ANSWER_SEC = 0.5
DEFAULT_RTP_PORT = int(os.environ.get("SIP_BRIDGE_RTP_PORT", "10000"))

# Minimal WebRTC-style SDP for m.call.invite when no real offer (Element sees WebRTC, Opus only).
MINIMAL_INVITE_SDP = (
    "v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\n"
    "a=group:BUNDLE 0\r\na=ice-ufrag:x\r\na=ice-pwd:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\r\n"
    "m=audio 9 RTP/SAVPF 111\r\nc=IN IP4 0.0.0.0\r\na=rtcp:9 IN IP4 0.0.0.0\r\n"
    "a=ice-options:trickle\r\na=fingerprint:sha-256 00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00\r\n"
    "a=setup:actpass\r\na=mid:0\r\na=sendrecv\r\na=rtpmap:111 opus/48000/2\r\n"
)
