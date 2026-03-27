"""
Outbound SIP call via pyVoIP (VoIPPhone + VoIPCall), audio bridged to MediaBridge (PCMU ↔ 48 kHz PCM).

Requires provider account env vars. INVITE target is sip:{user}@{REVERSE_PYVOIP_SERVER} — the domain
in the pasted URI should match the registrar host unless your PBX routes otherwise.
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
import time

try:
    import audioop
except ImportError:
    import audioop_lts as audioop  # type: ignore

from pyVoIP.VoIP import CallState, VoIPCall, VoIPPhone

from MediaBridge import MediaBridge


def _pcm_silence_ulaw() -> bytes:
    return bytes([0xFF]) * 160


def _ulaw_to_write_format(ulaw160: bytes) -> bytes:
    """Bytes VoIPCall.write_audio expects (see RTP.encode_pcmu inverse of parse_pcmu)."""
    if len(ulaw160) != 160:
        ulaw160 = (ulaw160 + b"\xff" * 160)[:160]
    lin = audioop.ulaw2lin(ulaw160, 1)
    return audioop.bias(lin, 1, 128)


def _read_format_to_ulaw(lin160: bytes) -> bytes:
    """Convert read_audio bytes to PCMU payload for MediaBridge.inject_pyvoip_pcmu."""
    if len(lin160) != 160:
        lin160 = (lin160 + b"\x00" * 160)[:160]
    x = audioop.bias(lin160, 1, -128)
    return audioop.lin2ulaw(x, 1)


def parse_sip_user_and_host(uri: str) -> tuple[str, str]:
    """sip:user@host[:port] -> (user, host)."""
    u = (uri or "").strip()
    if u.lower().startswith("sip:"):
        u = u[4:]
    u = u.split(";")[0]
    if "@" not in u:
        raise ValueError(f"Invalid SIP URI: {uri!r}")
    user, hostpart = u.split("@", 1)
    host = hostpart.split(":", 1)[0]
    return user.strip(), host.strip()


class PyVoipAudioBridge:
    """Runs a thread: VoIPCall RTP ↔ MediaBridge.inject / tx queue."""

    def __init__(self, call: VoIPCall, mb: MediaBridge, tx_queue: queue.Queue) -> None:
        self.call = call
        self.mb = mb
        self.tx_queue = tx_queue
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="pyvoip-audio", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        silence_u = _pcm_silence_ulaw()
        while not self._stop.is_set():
            if self.call.state != CallState.ANSWERED:
                if self.call.state == CallState.ENDED:
                    break
                time.sleep(0.05)
                continue
            try:
                u = self.tx_queue.get(timeout=0.12)
            except queue.Empty:
                u = silence_u
            try:
                self.call.write_audio(_ulaw_to_write_format(u))
            except Exception:
                pass
            try:
                r = self.call.read_audio(160, blocking=False)
            except Exception:
                r = b""
            if r and len(r) >= 160:
                chunk = r[:160]
                if chunk != b"\x80" * 160:
                    try:
                        ul = _read_format_to_ulaw(chunk)
                        self.mb.inject_pyvoip_pcmu(ul)
                    except Exception:
                        pass
            time.sleep(0.018)


def build_voip_phone_from_env() -> VoIPPhone:
    server = (os.environ.get("REVERSE_PYVOIP_SERVER") or "").strip()
    user = (os.environ.get("REVERSE_PYVOIP_USER") or "").strip()
    password = (os.environ.get("REVERSE_PYVOIP_PASSWORD") or "").strip()
    if not server or not user:
        raise RuntimeError(
            "Set REVERSE_PYVOIP_SERVER and REVERSE_PYVOIP_USER (and REVERSE_PYVOIP_PASSWORD) in bot/.env"
        )
    port = int(os.environ.get("REVERSE_PYVOIP_PORT", "5060"))
    sip_bind = int(os.environ.get("REVERSE_PYVOIP_SIP_BIND_PORT", "5064"))
    my_ip = (os.environ.get("REVERSE_PYVOIP_MY_IP") or "").strip() or _detect_local_ip()
    rtp_lo = int(os.environ.get("REVERSE_PYVOIP_RTP_LOW", "30000"))
    rtp_hi = int(os.environ.get("REVERSE_PYVOIP_RTP_HIGH", "40000"))
    return VoIPPhone(
        server,
        port,
        user,
        password,
        myIP=my_ip,
        callCallback=None,
        sipPort=sip_bind,
        rtpPortLow=rtp_lo,
        rtpPortHigh=rtp_hi,
    )


def _detect_local_ip() -> str:
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "0.0.0.0"


_phone_singleton: VoIPPhone | None = None
_phone_lock = threading.Lock()


def shutdown_phone() -> None:
    global _phone_singleton
    with _phone_lock:
        if _phone_singleton is not None:
            try:
                _phone_singleton.stop()
            except Exception:
                pass
            _phone_singleton = None


def get_or_create_phone() -> VoIPPhone:
    global _phone_singleton
    with _phone_lock:
        if _phone_singleton is None:
            _phone_singleton = build_voip_phone_from_env()
            _phone_singleton.start()
            time.sleep(1.5)
        return _phone_singleton


async def dial_and_bridge_audio(
    mb: MediaBridge,
    destination_sip_uri: str,
    *,
    wait_answer_timeout: float = 60.0,
) -> tuple[VoIPCall, PyVoipAudioBridge, queue.Queue]:
    """
    Register (if new phone), INVITE sip:{user}@{server}, wait until answered, attach audio bridge.
    Caller must use same REVERSE_PYVOIP_SERVER as account; ``destination_sip_uri`` supplies ``user``
    (and host is checked with a warning if different).
    """
    phone = get_or_create_phone()
    user, host = parse_sip_user_and_host(destination_sip_uri)
    srv = (os.environ.get("REVERSE_PYVOIP_SERVER") or "").strip()
    if host.lower() != srv.lower():
        print(
            f"[pyvoip] Warning: URI host {host!r} != REVERSE_PYVOIP_SERVER {srv!r} — "
            f"INVITE will still use sip:{user}@{srv} (pyVoIP limitation).",
            flush=True,
        )

    tx_queue: queue.Queue = queue.Queue(maxsize=128)
    mb.attach_pyvoip_tx_queue(tx_queue)

    loop = asyncio.get_event_loop()

    def _dial() -> VoIPCall:
        return phone.call(user)

    call = await loop.run_in_executor(None, _dial)

    deadline = time.monotonic() + wait_answer_timeout
    while time.monotonic() < deadline:
        if call.state == CallState.ANSWERED:
            break
        if call.state == CallState.ENDED:
            raise RuntimeError("SIP call ended before answer")
        await asyncio.sleep(0.08)
    else:
        try:
            call.hangup()
        except Exception:
            pass
        raise RuntimeError("SIP call timed out waiting for answer (no 200 OK?)")

    bridge = PyVoipAudioBridge(call, mb, tx_queue)
    bridge.start()
    return call, bridge, tx_queue


def hangup_call(call: VoIPCall | None) -> None:
    if not call:
        return
    try:
        if call.state == CallState.ANSWERED:
            call.hangup()
    except Exception:
        try:
            call.bye()
        except Exception:
            pass
