"""SIP bridge configuration and constants (Asterisk + Matrix)."""

import os

BUFFER_SIZE = 2048

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
