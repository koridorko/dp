"""
MediaBridge: bidirectional audio relay between SIP (RTP/Opus or PCMU) and Matrix/Element (WebRTC/Opus).

Requires Linphone (and our 200 OK SDP) to use Opus 48 kHz. Single sample rate = 48 kHz, no resampling.

Why we decode and encode even though both clients use Opus:
- Linphone sends us RTP packets (UDP) whose payload is Opus. We have no "Opus pipe" to Element:
  our side of the call is WebRTC via aiortc. The WebRTC API is MediaStreamTrack: we must feed
  PCM (raw samples) to our send track; aiortc then encodes PCM→Opus and sends to Element.
  So we must decode Opus (from Linphone RTP) → PCM to feed the track.
- Element sends us Opus over WebRTC; aiortc decodes it and gives us PCM in track.recv().
  We must encode that PCM → Opus and put it in RTP packets to send to Linphone.
So: RTP(Opus) ↔ PCM ↔ WebRTC(Opus). The bridge always works in PCM internally; decode/encode
are only at the RTP boundaries.

Architecture: one event loop + one decoder thread. Socket is non-blocking, add_reader in loop.
- RTP from Linphone: add_reader callback parses → put in raw_rtp_queue → decoder thread decodes → call_soon_threadsafe appends to jitter_buffer_sip.
- SIP→Element: playout task every 20 ms takes from jitter_buffer_sip (or silence), puts in asyncio queue; track.recv() awaits that queue → steady 20 ms to Element.
- Element→SIP: _consume_remote_track puts PCM in jitter_buffer_element; send playout task every 20 ms takes from it (or silence), encodes in executor, sendto.
Same socket: read in loop (add_reader), write in loop (sendto from task).

Public API (unchanged for HTTP bridge):
  set_peer_sip_rtp_addr(host, port), get_sip_rtp_bind_addr(), create_offer() -> SDP,
  get_local_ice_candidates(), set_remote_answer(sdp), add_remote_candidate(...), start(), stop().

Matrix→SIP reverse bridge may call sip_bridge.aioice_hangup_patch.install_aioice_stun_hangup_patch()
once at startup to reduce aioice STUN retry noise after WebRTC teardown (Asterisk bridge does not).
"""

import asyncio
import collections
import concurrent.futures
import fractions
import os
import queue
import socket
import struct
import threading
import time

# Opus 48 kHz, 20 ms frames
SAMPLE_RATE = 48000
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 960
PCM_FRAME_BYTES = SAMPLES_PER_FRAME * 2  # 1920
RTP_HEADER_SIZE = 12
# Dynamic payload type for Opus (we advertise this in SDP)
RTP_PAYLOAD_TYPE_OPUS = 96
# RTP timestamp increment per 20 ms at 48 kHz (Opus) / 8 kHz (ulaw)
RTP_TIMESTAMP_INCREMENT = 960
RTP_TIMESTAMP_INCREMENT_ULAW = 160
JITTER_BUFFER_FRAMES = 12  # 12 * 20 ms = 240 ms target; reduces underrun crackling
PLAYOUT_INTERVAL = FRAME_MS / 1000.0
SIP_TO_WEBRTC_QUEUE_MAX = 64
# Min frames in SIP jitter buffer before we start playout (reduces initial crackling)
SIP_PREBUFFER_FRAMES = 3

# Accept these RTP payload types as Opus (Linphone may use 96, 97, 111, etc.)
OPUS_PAYLOAD_TYPES = {96, 97, 111}
# PCMU (ulaw) for Asterisk External Media
RTP_PAYLOAD_TYPE_PCMU = 0


def rtp_parse(data: bytes) -> tuple[bytes, int, int, int, int] | None:
    """Return (payload, payload_type, seq, ts, ssrc) or None."""
    if len(data) < RTP_HEADER_SIZE:
        return None
    if (data[0] >> 6) != 2:
        return None
    pt = data[1] & 0x7F
    seq = struct.unpack(">H", data[2:4])[0]
    ts = struct.unpack(">I", data[4:8])[0]
    ssrc = struct.unpack(">I", data[8:12])[0]
    return (data[RTP_HEADER_SIZE:], pt, seq, ts, ssrc)


def rtp_build(
    payload: bytes,
    seq: int,
    ts: int,
    ssrc: int = 0x12345678,
    payload_type: int = RTP_PAYLOAD_TYPE_OPUS,
) -> bytes:
    header = struct.pack(
        ">BBHII",
        0x80,
        payload_type & 0x7F,
        seq & 0xFFFF,
        ts & 0xFFFFFFFF,
        ssrc,
    )
    return header + payload


def _ensure_rtcp_mux_in_sdp(sdp: str) -> str:
    """Add a=rtcp-mux to each m=audio/m=video section if missing (aiortc requires it)."""
    lines = sdp.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("m=audio") or line.startswith("m=video"):
            out.append(line)
            j = i + 1
            while j < len(lines) and not lines[j].startswith("m="):
                j += 1
            section = lines[i:j]
            if not any(l.strip() == "a=rtcp-mux" for l in section):
                out.append("a=rtcp-mux")
            for k in range(i + 1, j):
                out.append(lines[k])
            i = j
            continue
        out.append(line)
        i += 1
    return "\r\n".join(out)


def _make_opus_decoder():
    """Create and open one Opus decoder (reuse for all RTP packets)."""
    import av

    dec = av.CodecContext.create("libopus", "r")
    dec.open()
    return dec


def _opus_decode_payload(decoder, payload: bytes) -> list[bytes]:
    """Decode Opus RTP payload to 16-bit PCM (48 kHz mono). Uses shared decoder. Returns list of raw PCM chunks."""
    import av

    if not payload or not decoder:
        return []
    try:
        pkt = av.Packet(payload)
        frames = decoder.decode(pkt)
    except Exception:
        return []
    out = []
    resampler = None
    for f in frames:
        if not hasattr(f, "planes") or not f.planes:
            continue
        if getattr(f, "format", None) and str(f.format) != "s16":
            if resampler is None:
                resampler = av.AudioResampler(
                    format="s16", layout="mono", rate=SAMPLE_RATE
                )
            resampled = resampler.resample(f)
            if resampled:
                for r in resampled:
                    out.append(bytes(r.planes[0]))
        else:
            out.append(bytes(f.planes[0]))
    return out


def _make_opus_encoder():
    """Create and open one Opus encoder (reuse for all 20 ms frames)."""
    import av

    enc = av.CodecContext.create("libopus", "w")
    enc.format = "s16"
    enc.layout = "mono"
    enc.sample_rate = SAMPLE_RATE
    enc.open()
    return enc


def _opus_encode_pcm(encoder, pcm: bytes) -> bytes | None:
    """Encode 20 ms PCM (960 samples, 1920 bytes) to Opus. Uses shared encoder. Returns single packet payload or None."""
    import av

    if not encoder or len(pcm) < PCM_FRAME_BYTES:
        return None
    frame = av.AudioFrame(format="s16", layout="mono", samples=SAMPLES_PER_FRAME)
    frame.sample_rate = SAMPLE_RATE
    frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
    frame.planes[0].update(pcm[:PCM_FRAME_BYTES])
    try:
        packets = list(encoder.encode(frame))
        if packets and len(packets[0]) > 0:
            return bytes(packets[0])
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# MediaBridge
# ---------------------------------------------------------------------------


class MediaBridge:
    """
    Relay: SIP RTP (Opus 48 kHz) ↔ WebRTC (Opus 48 kHz). One sample rate, no resampling.
    """

    def __init__(
        self,
        sip_rtp_port: int,
        listen_host: str = "0.0.0.0",
        advertised_host: str | None = None,
        sip_codec: str = "opus",
    ) -> None:
        self.sip_rtp_port = sip_rtp_port
        self.listen_host = listen_host
        self.advertised_host = advertised_host
        self.sip_codec = (sip_codec or "opus").lower()
        self._pyvoip_external = False
        self._pyvoip_tx_queue: queue.Queue | None = None
        self._peer_sip: tuple[str, int] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._pc = None
        self._stop = threading.Event()
        self._webrtc_pts_ref: list[int] = [0]
        self._rtp_seq = 0
        self._rtp_ts = 0
        self._rtp_ssrc = 0x12345678
        self._peer_rtp_ssrc_set = False
        self._send_track = None
        self._local_ice_candidates: list[dict] = []
        self._sip_to_webrtc_queue: asyncio.Queue | None = None
        self._jitter_buffer_sip = collections.deque(maxlen=JITTER_BUFFER_FRAMES * 2)
        self._jitter_buffer_element = collections.deque(maxlen=JITTER_BUFFER_FRAMES * 2)
        self._raw_rtp_queue = queue.Queue(maxsize=256)
        self._decoder_thread = None
        self._opus_encoder = None
        self._element_pcm_buffer = bytearray()

    def attach_pyvoip_tx_queue(self, q: queue.Queue) -> None:
        """Matrix→SIP via pyVoIP: PCMU payloads (160 B) for VoIPCall.write_audio (after ulaw→lin8)."""
        self._pyvoip_external = True
        self._pyvoip_tx_queue = q

    def detach_pyvoip(self) -> None:
        self._pyvoip_external = False
        self._pyvoip_tx_queue = None

    def inject_pyvoip_pcmu(self, ulaw_payload: bytes) -> None:
        """SIP→Matrix: one RTP PCMU payload (160 B); fed into Element path like RTP decode."""
        if len(ulaw_payload) != 160 or not self._loop or self._stop.is_set():
            return
        chunks = self._ulaw_decode_to_48k(ulaw_payload)
        if chunks:
            self._loop.call_soon_threadsafe(self._append_sip_chunks, chunks)

    def set_peer_sip_rtp_addr(self, host: str, port: int) -> None:
        self._peer_sip = (host, port)
        print(f"[MediaBridge] Element→SIP RTP will be sent to {host}:{port}")

    def get_sip_rtp_bind_addr(self) -> tuple[str, int]:
        if self.advertised_host:
            return (self.advertised_host, self.sip_rtp_port)
        host = "127.0.0.1" if self.listen_host == "0.0.0.0" else self.listen_host
        return (host, self.sip_rtp_port)

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.listen_host, self.sip_rtp_port))
        self._sock.setblocking(False)
        self._stop.clear()
        try:
            self._opus_encoder = _make_opus_encoder()
        except Exception as e:
            print(
                f"[MediaBridge] Opus encoder init failed at start (Element→Linphone send will be silent): {e}",
                flush=True,
            )
        self._decoder_thread = threading.Thread(
            target=self._decoder_worker, daemon=True
        )
        self._decoder_thread.start()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        for _ in range(30):
            if self._loop is not None and self._sip_to_webrtc_queue is not None:
                break
            time.sleep(0.05)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_main())

    def _append_sip_chunks(self, pcm_list: list) -> None:
        """Called from decoder thread via call_soon_threadsafe."""
        for pcm in pcm_list:
            for i in range(0, len(pcm), PCM_FRAME_BYTES):
                chunk = pcm[i : i + PCM_FRAME_BYTES]
                if len(chunk) == PCM_FRAME_BYTES:
                    self._jitter_buffer_sip.append(chunk)

    def _decoder_worker(self) -> None:
        """Decode RTP payloads in background thread; push PCM to jitter buffer via call_soon_threadsafe."""
        dec = None
        if self.sip_codec == "opus":
            try:
                dec = _make_opus_decoder()
            except Exception:
                return
        while not self._stop.is_set():
            if self._pyvoip_external:
                time.sleep(0.3)
                continue
            try:
                payload, ssrc = self._raw_rtp_queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if self.sip_codec == "ulaw":
                chunks = self._ulaw_decode_to_48k(payload)
            else:
                chunks = _opus_decode_payload(dec, payload) if dec else []
            if chunks and self._loop and not self._stop.is_set():
                self._loop.call_soon_threadsafe(self._append_sip_chunks, chunks)

    def _on_rtp_readable(self) -> None:
        """Socket readable (loop): parse RTP, put payload in queue for decoder thread."""
        if self._pyvoip_external:
            return
        try:
            data, from_addr = self._sock.recvfrom(2048)
        except (BlockingIOError, OSError):
            return
        if not getattr(self, "_logged_first_rtp_from_sip", False):
            self._logged_first_rtp_from_sip = True
            print("[MediaBridge] First RTP from SIP peer.", flush=True)
        parsed = rtp_parse(data)
        if not parsed:
            return
        payload, pt, _, _, ssrc = parsed
        if self.sip_codec == "ulaw":
            if not self._peer_sip:
                self._peer_sip = from_addr
                print(
                    f"[MediaBridge] Asterisk RTP peer set from first packet: {from_addr}",
                    flush=True,
                )
            if not payload or pt != RTP_PAYLOAD_TYPE_PCMU:
                return
        else:
            if not payload or pt not in OPUS_PAYLOAD_TYPES:
                return
        if not self._peer_rtp_ssrc_set:
            self._rtp_ssrc = ssrc
            self._peer_rtp_ssrc_set = True
        try:
            self._raw_rtp_queue.put_nowait((payload, ssrc))
        except queue.Full:
            pass

    def _ulaw_decode_to_48k(self, payload: bytes) -> list[bytes]:
        """Decode ulaw payload to 48 kHz PCM (960 samples per 20 ms). Uses 8k->48k resample."""
        import av

        try:
            import audioop
        except ImportError:
            try:
                import audioop_lts as audioop
            except ImportError:
                return []
        if not payload:
            return []
        # ulaw -> 16-bit linear 8 kHz (2 bytes per sample)
        linear_8k = audioop.ulaw2lin(payload, 2)
        n_8k = len(linear_8k) // 2
        if n_8k == 0:
            return []
        # Resample 8k -> 48k (6x): 160 samples @ 8k = 20 ms -> 960 samples @ 48k
        resampler = getattr(self, "_ulaw_resampler", None)
        if resampler is None:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            try:
                self._ulaw_resampler = resampler
            except Exception:
                pass

        frames_8k = []
        for i in range(0, n_8k, 160):
            chunk = linear_8k[i * 2 : (i + 160) * 2]
            if len(chunk) < 320:
                break
            frame_8k = av.AudioFrame(format="s16", layout="mono", samples=160)
            frame_8k.sample_rate = 8000
            frame_8k.time_base = fractions.Fraction(1, 8000)
            frame_8k.planes[0].update(chunk)
            resampled = resampler.resample(frame_8k)
            if resampled:
                for r in resampled:
                    frames_8k.append(bytes(r.planes[0]))
        return frames_8k

    def _ulaw_encode_from_48k(self, pcm_48k: bytes) -> bytes | None:
        """Encode 20 ms 48k PCM (960 samples) to ulaw (160 bytes)."""
        try:
            import audioop
        except ImportError:
            try:
                import audioop_lts as audioop
            except ImportError:
                return None
        import av

        if len(pcm_48k) < PCM_FRAME_BYTES:
            return None
        resampler = getattr(self, "_ulaw_resampler_48to8", None)
        if resampler is None:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=8000)
            try:
                self._ulaw_resampler_48to8 = resampler
            except Exception:
                pass
        frame_48k = av.AudioFrame(
            format="s16", layout="mono", samples=SAMPLES_PER_FRAME
        )
        frame_48k.sample_rate = SAMPLE_RATE
        frame_48k.time_base = fractions.Fraction(1, SAMPLE_RATE)
        frame_48k.planes[0].update(pcm_48k[:PCM_FRAME_BYTES])
        resampled = resampler.resample(frame_48k)
        if not resampled:
            return None
        raw_8k = b"".join(bytes(r.planes[0]) for r in resampled)
        if len(raw_8k) < 320:
            return None
        return audioop.lin2ulaw(raw_8k[:320], 2)

    async def _sip_to_webrtc_playout(self) -> None:
        """Every 20 ms: take one frame from jitter buffer (or silence), put in queue for track.recv()."""
        import av

        silent = b"\x00" * PCM_FRAME_BYTES
        started = getattr(self, "_sip_playout_started", False)
        while not self._stop.is_set():
            try:
                if not started and len(self._jitter_buffer_sip) >= SIP_PREBUFFER_FRAMES:
                    started = True
                    self._sip_playout_started = True
                if started and self._jitter_buffer_sip:
                    pcm = self._jitter_buffer_sip.popleft()
                else:
                    pcm = silent
                if self._sip_to_webrtc_queue:
                    f = av.AudioFrame(
                        format="s16", layout="mono", samples=SAMPLES_PER_FRAME
                    )
                    f.sample_rate = SAMPLE_RATE
                    f.time_base = fractions.Fraction(1, SAMPLE_RATE)
                    f.pts = self._webrtc_pts_ref[0]
                    self._webrtc_pts_ref[0] += SAMPLES_PER_FRAME
                    f.planes[0].update(pcm)
                    try:
                        self._sip_to_webrtc_queue.put_nowait(f)
                    except asyncio.QueueFull:
                        pass
            except Exception:
                pass
            await asyncio.sleep(PLAYOUT_INTERVAL)

    async def _sip_send_playout(self) -> None:
        """Every 20 ms: take one frame from jitter_buffer_element (or silence), encode, sendto Linphone."""
        silent = b"\x00" * PCM_FRAME_BYTES
        while not self._stop.is_set():
            try:
                if self._pyvoip_external and self._pyvoip_tx_queue is not None:
                    if self._jitter_buffer_element:
                        pcm = self._jitter_buffer_element.popleft()
                    else:
                        pcm = silent
                    if self.sip_codec == "ulaw":
                        ulaw = self._ulaw_encode_from_48k(pcm)
                        if ulaw:
                            try:
                                self._pyvoip_tx_queue.put_nowait(ulaw)
                            except queue.Full:
                                pass
                    await asyncio.sleep(PLAYOUT_INTERVAL)
                    continue
                if self._peer_sip and self._peer_rtp_ssrc_set:
                    if self._opus_encoder is None:
                        try:
                            self._opus_encoder = _make_opus_encoder()
                        except Exception as e:
                            if not getattr(self, "_logged_encoder_fail", False):
                                self._logged_encoder_fail = True
                                print(
                                    f"[MediaBridge] Opus encoder init failed (Element→SIP send disabled): {e}",
                                    flush=True,
                                )
                    if self._jitter_buffer_element:
                        pcm = self._jitter_buffer_element.popleft()
                    else:
                        pcm = silent
                    if self.sip_codec == "ulaw":
                        opus_payload = self._ulaw_encode_from_48k(pcm)
                        pt = RTP_PAYLOAD_TYPE_PCMU
                    else:
                        enc = self._opus_encoder
                        if enc:
                            opus_payload = await self._loop.run_in_executor(
                                None, lambda e=enc, p=pcm: _opus_encode_pcm(e, p)
                            )
                        else:
                            opus_payload = None
                        pt = RTP_PAYLOAD_TYPE_OPUS
                    if opus_payload and self._sock:
                        if not getattr(self, "_logged_first_send_to_sip", False):
                            self._logged_first_send_to_sip = True
                            print(
                                f"[MediaBridge] First RTP sent to SIP peer {self._peer_sip} (Element→SIP path).",
                                flush=True,
                            )
                        self._rtp_seq = (self._rtp_seq + 1) & 0xFFFF
                        inc = (
                            RTP_TIMESTAMP_INCREMENT_ULAW
                            if self.sip_codec == "ulaw"
                            else RTP_TIMESTAMP_INCREMENT
                        )
                        self._rtp_ts = (self._rtp_ts + inc) & 0xFFFFFFFF
                        packet = rtp_build(
                            opus_payload,
                            self._rtp_seq,
                            self._rtp_ts,
                            self._rtp_ssrc,
                            payload_type=pt,
                        )
                        self._sock.sendto(packet, self._peer_sip)
            except (OSError, Exception):
                pass
            await asyncio.sleep(PLAYOUT_INTERVAL)

    async def _async_main(self) -> None:
        self._sip_to_webrtc_queue = asyncio.Queue(maxsize=SIP_TO_WEBRTC_QUEUE_MAX)
        self._loop.add_reader(self._sock.fileno(), self._on_rtp_readable)
        asyncio.create_task(self._sip_to_webrtc_playout())
        asyncio.create_task(self._sip_send_playout())
        await asyncio.Future()

    def create_offer(self) -> str:
        if self._loop is None or self._sip_to_webrtc_queue is None:
            raise RuntimeError(
                "MediaBridge not ready (start() may have returned too early)"
            )
        fut = asyncio.run_coroutine_threadsafe(self._create_offer(), self._loop)
        try:
            return fut.result(timeout=15)
        except concurrent.futures.TimeoutError:
            raise RuntimeError("create_offer timed out (15s)") from None

    async def _create_offer(self) -> str:
        import av
        from aiortc import (MediaStreamTrack, RTCConfiguration, RTCIceServer,
                            RTCPeerConnection)

        aq = self._sip_to_webrtc_queue
        pts_ref = self._webrtc_pts_ref
        silent = b"\x00" * PCM_FRAME_BYTES

        class SipToWebRtcTrack(MediaStreamTrack):
            kind = "audio"
            _recv_logged = False

            def __init__(self):
                super().__init__()

            async def recv(self):
                try:
                    frame = await asyncio.wait_for(aq.get(), timeout=PLAYOUT_INTERVAL)
                    if not SipToWebRtcTrack._recv_logged:
                        SipToWebRtcTrack._recv_logged = True
                        print(
                            "[MediaBridge] First frame to Element (queue → WebRTC).",
                            flush=True,
                        )
                    return frame
                except asyncio.TimeoutError:
                    pass
                f = av.AudioFrame(
                    format="s16", layout="mono", samples=SAMPLES_PER_FRAME
                )
                f.sample_rate = SAMPLE_RATE
                f.time_base = fractions.Fraction(1, SAMPLE_RATE)
                f.pts = pts_ref[0]
                pts_ref[0] += SAMPLES_PER_FRAME
                for plane in f.planes:
                    plane.update(b"\x00" * PCM_FRAME_BYTES)
                return f

        if self._pc:
            try:
                await self._pc.close()
            except Exception:
                pass
            self._pc = None
        # Reset per-call state so next call gets fresh prebuffer and logging
        self._sip_playout_started = False
        self._logged_first_rtp_from_sip = False
        self._logged_first_element_frame = False
        stun = (
            os.environ.get("MEDIABRIDGE_STUN", "stun:stun.l.google.com:19302") or ""
        ).strip()
        ice = [RTCIceServer(urls=stun)] if stun else []
        self._pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ice))
        self._local_ice_candidates = []
        self._pc.on("track", self._on_remote_track)
        self._send_track = SipToWebRtcTrack()
        self._pc.addTrack(self._send_track)
        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        for _ in range(3):
            if self._pc.iceGatheringState == "complete":
                break
            await asyncio.sleep(0.1)
        for tr in self._pc.getTransceivers():
            ice_transport = tr.receiver.transport.transport
            mid = tr.mid or "0"
            mline = tr._get_mline_index() if hasattr(tr, "_get_mline_index") else 0
            for c in ice_transport.iceGatherer.getLocalCandidates():
                cand_str = (
                    f"candidate:{getattr(c, 'foundation', '0')} {c.component} {c.protocol} "
                    f"{c.priority} {c.ip} {c.port} typ {c.type} generation 0"
                )
                self._local_ice_candidates.append(
                    {
                        "candidate": cand_str,
                        "sdpMid": mid,
                        "sdpMLineIndex": mline,
                    }
                )
        return (self._pc.localDescription or offer).sdp

    def create_answer_from_remote_offer(
        self,
        offer_sdp: str,
        remote_ice_candidates: list[tuple[str, str | None, int | None]] | None = None,
    ) -> str:
        """WebRTC answer when the remote (Element) sent the offer (reverse / Matrix-originated call)."""
        if self._loop is None or self._sip_to_webrtc_queue is None:
            raise RuntimeError(
                "MediaBridge not ready (start() may have returned too early)"
            )
        fut = asyncio.run_coroutine_threadsafe(
            self._create_answer_from_remote_offer(offer_sdp, remote_ice_candidates),
            self._loop,
        )
        try:
            return fut.result(timeout=20)
        except concurrent.futures.TimeoutError:
            raise RuntimeError(
                "create_answer_from_remote_offer timed out (20s)"
            ) from None

    async def _create_answer_from_remote_offer(
        self,
        offer_sdp: str,
        remote_ice_candidates: list[tuple[str, str | None, int | None]] | None = None,
    ) -> str:
        import av
        from aiortc import (MediaStreamTrack, RTCConfiguration, RTCIceServer,
                            RTCPeerConnection, RTCSessionDescription)

        aq = self._sip_to_webrtc_queue
        pts_ref = self._webrtc_pts_ref
        silent = b"\x00" * PCM_FRAME_BYTES

        class SipToWebRtcTrack(MediaStreamTrack):
            kind = "audio"
            _recv_logged = False

            def __init__(self):
                super().__init__()

            async def recv(self):
                try:
                    frame = await asyncio.wait_for(aq.get(), timeout=PLAYOUT_INTERVAL)
                    if not SipToWebRtcTrack._recv_logged:
                        SipToWebRtcTrack._recv_logged = True
                        print(
                            "[MediaBridge] First frame to Element (queue → WebRTC).",
                            flush=True,
                        )
                    return frame
                except asyncio.TimeoutError:
                    pass
                f = av.AudioFrame(
                    format="s16", layout="mono", samples=SAMPLES_PER_FRAME
                )
                f.sample_rate = SAMPLE_RATE
                f.time_base = fractions.Fraction(1, SAMPLE_RATE)
                f.pts = pts_ref[0]
                pts_ref[0] += SAMPLES_PER_FRAME
                for plane in f.planes:
                    plane.update(b"\x00" * PCM_FRAME_BYTES)
                return f

        if self._pc:
            try:
                await self._pc.close()
            except Exception:
                pass
            self._pc = None
        self._sip_playout_started = False
        self._logged_first_rtp_from_sip = False
        self._logged_first_element_frame = False
        self._element_pcm_buffer.clear()
        self._jitter_buffer_sip.clear()
        self._jitter_buffer_element.clear()
        self._webrtc_pts_ref[0] = 0
        self._peer_sip = None
        self._peer_rtp_ssrc_set = False
        self._rtp_seq = 0
        self._rtp_ts = 0

        stun = (
            os.environ.get("MEDIABRIDGE_STUN", "stun:stun.l.google.com:19302") or ""
        ).strip()
        ice = [RTCIceServer(urls=stun)] if stun else []
        self._pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ice))
        self._local_ice_candidates = []
        self._pc.on("track", self._on_remote_track)
        self._send_track = SipToWebRtcTrack()
        self._pc.addTrack(self._send_track)

        sdp = _ensure_rtcp_mux_in_sdp(offer_sdp)
        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=sdp, type="offer")
        )
        for cand, mid, idx in remote_ice_candidates or []:
            if not cand or str(cand).strip() in ("", "end-of-candidates"):
                continue
            try:
                from aioice import Candidate as AioiceCandidate
                from aiortc.rtcicetransport import candidate_from_aioice

                aio = AioiceCandidate.from_sdp(str(cand).strip())
                ic = candidate_from_aioice(aio)
                ic.sdpMid = mid or "0"
                ic.sdpMLineIndex = idx if idx is not None else 0
                await self._pc.addIceCandidate(ic)
            except Exception:
                pass
        answer = await self._pc.createAnswer()
        await self._pc.setLocalDescription(answer)
        for _ in range(50):
            if self._pc.iceGatheringState == "complete":
                break
            await asyncio.sleep(0.05)
        for tr in self._pc.getTransceivers():
            ice_transport = tr.receiver.transport.transport
            mid = tr.mid or "0"
            mline = tr._get_mline_index() if hasattr(tr, "_get_mline_index") else 0
            for c in ice_transport.iceGatherer.getLocalCandidates():
                cand_str = (
                    f"candidate:{getattr(c, 'foundation', '0')} {c.component} {c.protocol} "
                    f"{c.priority} {c.ip} {c.port} typ {c.type} generation 0"
                )
                self._local_ice_candidates.append(
                    {
                        "candidate": cand_str,
                        "sdpMid": mid,
                        "sdpMLineIndex": mline,
                    }
                )
        await self._ensure_sender_started()
        return (self._pc.localDescription or answer).sdp

    def get_local_ice_candidates(self) -> list[dict]:
        return list(getattr(self, "_local_ice_candidates", []))

    def set_remote_answer(self, sdp: str) -> None:
        from aiortc import RTCSessionDescription

        # Element may send answer without a=rtcp-mux; aiortc requires it
        sdp = _ensure_rtcp_mux_in_sdp(sdp)
        fut = asyncio.run_coroutine_threadsafe(
            self._pc.setRemoteDescription(
                RTCSessionDescription(sdp=sdp, type="answer")
            ),
            self._loop,
        )
        fut.result(timeout=10)
        asyncio.run_coroutine_threadsafe(self._ensure_sender_started(), self._loop)

    async def _ensure_sender_started(self) -> None:
        await asyncio.sleep(0.2)
        if self._stop.is_set() or not self._pc:
            return
        our_track = getattr(self, "_send_track", None)
        if not our_track:
            return
        for tr in self._pc.getTransceivers():
            if tr.kind != "audio":
                continue
            direction = getattr(tr, "currentDirection", None) or getattr(
                tr, "direction", None
            )
            if direction not in ("sendonly", "sendrecv"):
                continue
            tr.sender.replaceTrack(our_track)
            print("[MediaBridge] SIP->Matrix track bound to sender.", flush=True)
            return

    def add_remote_candidate(
        self,
        candidate: str,
        sdp_mid: str | None,
        sdp_mline_index: int | None,
    ) -> None:
        if not (candidate and candidate.strip()):
            return
        try:
            from aioice import Candidate as AioiceCandidate
            from aiortc.rtcicetransport import candidate_from_aioice

            aio = AioiceCandidate.from_sdp(candidate.strip())
            c = candidate_from_aioice(aio)
            c.sdpMid = sdp_mid or "0"
            c.sdpMLineIndex = sdp_mline_index if sdp_mline_index is not None else 0
            fut = asyncio.run_coroutine_threadsafe(
                self._pc.addIceCandidate(c), self._loop
            )
            fut.result(timeout=5)
        except Exception:
            pass

    def _on_remote_track(self, track) -> None:
        if track.kind != "audio":
            return
        asyncio.run_coroutine_threadsafe(self._consume_remote_track(track), self._loop)

    async def _consume_remote_track(self, track) -> None:
        """Element → SIP: read remote track (PCM from aiortc), convert to s16 if needed, accumulate to 20 ms, push to send buffer."""
        import av

        buf = self._element_pcm_buffer
        resampler = None  # for format conversion (e.g. float → s16)
        logged_first = getattr(self, "_logged_first_element_frame", False)
        while not self._stop.is_set() and self._pc:
            try:
                frame = await asyncio.wait_for(track.recv(), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                continue
            if frame is None:
                break
            # aiortc remote track may give float (fltp) or s16; we need s16 for our encoder
            fmt = getattr(frame, "format", None)
            planes = getattr(frame, "planes", None) or []
            if not planes:
                continue
            if fmt and str(fmt) != "s16":
                if resampler is None:
                    resampler = av.AudioResampler(
                        format="s16", layout="mono", rate=SAMPLE_RATE
                    )
                try:
                    converted = resampler.resample(frame)
                    raw = b"".join(bytes(r.planes[0]) for r in (converted or []))
                except Exception:
                    continue
            else:
                # Mono: planes[0]. Stereo: take left channel only (planes[0]).
                raw = bytes(planes[0])
            if not raw:
                continue
            if not logged_first:
                logged_first = True
                self._logged_first_element_frame = True
                print(
                    "[MediaBridge] First frame from Element (Element→SIP path); feeding send buffer.",
                    flush=True,
                )
            if len(buf) > PCM_FRAME_BYTES * 10:
                del buf[:PCM_FRAME_BYTES]
            buf.extend(raw)
            while len(buf) >= PCM_FRAME_BYTES:
                chunk = bytes(buf[:PCM_FRAME_BYTES])
                del buf[:PCM_FRAME_BYTES]
                self._jitter_buffer_element.append(chunk)

    def stop(self) -> None:
        self._stop.set()
        if self._decoder_thread and self._decoder_thread.is_alive():
            self._decoder_thread.join(timeout=1.0)
        if self._pc and self._loop:
            fut = asyncio.run_coroutine_threadsafe(self._pc.close(), self._loop)
            try:
                fut.result(timeout=2)
            except Exception:
                pass
        sock = self._sock
        self._sock = None
        if sock and self._loop:
            try:
                self._loop.call_soon_threadsafe(self._loop.remove_reader, sock.fileno())
            except Exception:
                pass
            time.sleep(0.05)
        if sock:
            try:
                sock.close()
            except OSError:
                pass
