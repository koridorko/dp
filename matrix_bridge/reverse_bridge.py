"""
Matrix → SIP reverse bridge.

Requires env MY_MATRIX_USERNAME (@you:server). The bot waits until it shares a 1:1 room
with only that user (accepts invites), then listens for sip:… and native voice calls.

WebRTC uses MediaBridge; SIP leg uses pyVoIP (VoIPPhone outbound INVITE, PCMU RTP).
See docs/ARCHITECTURE.md and env REVERSE_PYVOIP_* below.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import sys

try:
    from dotenv import load_dotenv
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(_root, "bot", ".env"))
except ImportError:
    pass

from Matrix import MatrixPayloadCreator, make_party_id
from MediaBridge import MediaBridge
from bot.MatrixBot import MatrixBot

from sip_bridge.aioice_hangup_patch import install_aioice_stun_hangup_patch
from sip_bridge.config import DEFAULT_RTP_PORT
from sip_bridge.matrix_sync import (
    events_from_room,
    get_event_type_and_content,
    nio_sync,
    patch_nio_call_candidates_schema,
    trickle_ice_tuples_from_m_call_event,
)
from sip_bridge.matrix_trickle import finish_matrix_webrtc_setup

from .pairing import load_bridge_user_id, wait_for_paired_room
from .pyvoip_bridge import (
    dial_and_bridge_audio,
    hangup_call,
    shutdown_phone,
)

_SIP_IN_TEXT = re.compile(r"sip:[a-zA-Z0-9._~%+!$&'()*;,=\-:]+@[a-zA-Z0-9.\-]+(?::\d+)?", re.I)
_READY = "SIP target saved. Call the bot to bridge audio to that URI."


def _guess_outbound_ipv4() -> str | None:
    """Best-effort local IPv4 for SDP when we must not advertise 127.0.0.1 to a public SIP peer."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def _is_unusable_rtp_advertise_host(h: str) -> bool:
    """Empty, loopback, unspecified — treat like unset (bot/.env often has 127.0.0.1 for local Asterisk)."""
    if not (h or "").strip():
        return True
    try:
        a = ipaddress.ip_address(h.split("%", 1)[0].strip())
        return bool(a.is_loopback or a.is_unspecified)
    except ValueError:
        return h.strip().lower() == "localhost"


def _reverse_advertised_rtp_host() -> tuple[str, str]:
    """
    Host we put in WebRTC SDP (pyVoIP handles SIP/RTP separately).

    Returns (host, reason) where reason is 'env:MEDIABRIDGE_ADVERTISED_HOST', 'env:BRIDGE_RTP_HOST',
    'auto:route', or 'fallback:127.0.0.1'.
    """
    explicit = os.environ.get("MEDIABRIDGE_ADVERTISED_HOST", "").strip()
    if explicit and not _is_unusable_rtp_advertise_host(explicit):
        return explicit, "env:MEDIABRIDGE_ADVERTISED_HOST"
    bridge = os.environ.get("BRIDGE_RTP_HOST", "127.0.0.1").strip()
    if bridge and not _is_unusable_rtp_advertise_host(bridge):
        return bridge, "env:BRIDGE_RTP_HOST"
    guessed = _guess_outbound_ipv4()
    if guessed:
        return guessed, "auto:route"
    return "127.0.0.1", "fallback:127.0.0.1"


def _is_loopback_host(h: str) -> bool:
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return h in ("localhost",)


def _is_private_host(h: str) -> bool:
    try:
        return ipaddress.ip_address(h).is_private
    except ValueError:
        return False


def _sender(event) -> str | None:
    if hasattr(event, "sender"):
        return getattr(event, "sender", None)
    raw = getattr(event, "source", event) if not isinstance(event, dict) else event
    return raw.get("sender") if isinstance(raw, dict) else None


def _sip_in_message(body: str) -> str | None:
    m = _SIP_IN_TEXT.search(body or "")
    return m.group(0) if m else None


class ReverseBridge:
    def __init__(self) -> None:
        self.bot = MatrixBot()
        self.mb: MediaBridge | None = None
        self._pyvoip_bridge = None
        self._pyvoip_call = None
        self.room_sip: dict[str, str] = {}
        self._seen: set[tuple[str, str]] = set()
        self._bridge_user_id: str = ""
        self._paired_room_id: str | None = None

    async def run(self) -> None:
        os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for v in ("MATRIX_BOT_USERNAME", "MATRIX_BOT_PASSWORD", "MATRIX_BOT_HOMESERVER"):
            if not os.environ.get(v, "").strip():
                print(f"[reverse] Missing {v}", file=sys.stderr)
                sys.exit(1)
        if not (os.environ.get("REVERSE_PYVOIP_SERVER") or "").strip() or not (
            os.environ.get("REVERSE_PYVOIP_USER") or ""
        ).strip():
            print(
                "[reverse] Set REVERSE_PYVOIP_SERVER, REVERSE_PYVOIP_USER, REVERSE_PYVOIP_PASSWORD "
                "in bot/.env (pyVoIP registration).",
                file=sys.stderr,
            )
            sys.exit(1)

        await self.bot.connect_to_server()
        patch_nio_call_candidates_schema()
        install_aioice_stun_hangup_patch()

        rtp_port = int(os.environ.get("REVERSE_BRIDGE_RTP_PORT", str(DEFAULT_RTP_PORT + 2)))
        advertised, adv_reason = _reverse_advertised_rtp_host()
        if adv_reason == "auto:route":
            print(
                f"[reverse] MEDIABRIDGE_ADVERTISED_HOST unset; using detected IPv4 {advertised!r} for WebRTC ICE.",
                flush=True,
            )
        elif _is_loopback_host(advertised):
            print(
                "[reverse] WARNING: WebRTC advertised as loopback — set MEDIABRIDGE_ADVERTISED_HOST.",
                file=sys.stderr,
                flush=True,
            )
        elif _is_private_host(advertised):
            print(
                f"[reverse] Note: WebRTC advertised {advertised!r} is private (NAT/port-forward as needed).",
                flush=True,
            )

        self.mb = MediaBridge(
            sip_rtp_port=rtp_port,
            listen_host="0.0.0.0",
            advertised_host=advertised or None,
            sip_codec="ulaw",
        )
        self.mb.start()

        client = self.bot.client
        my_id = self.bot.user_id
        self._bridge_user_id = load_bridge_user_id()
        print(
            f"[reverse] {my_id} — pairing with {self._bridge_user_id} (1:1 room only).",
            flush=True,
        )
        self._paired_room_id = await wait_for_paired_room(self.bot, self._bridge_user_id)
        print(
            f"[reverse] Paired in room …{self._paired_room_id[-8:]} — paste sip: URI here, then call.",
            flush=True,
        )
        print(
            "[reverse] SIP leg: pyVoIP (REGISTER + INVITE sip:user@REVERSE_PYVOIP_SERVER).",
            flush=True,
        )
        try:
            while True:
                sync = await nio_sync(client)
                if sync:
                    await self._rooms(sync, client, my_id)
                else:
                    await asyncio.sleep(0.5)
        finally:
            if self._pyvoip_bridge:
                try:
                    self._pyvoip_bridge.stop()
                except Exception:
                    pass
                self._pyvoip_bridge = None
            hangup_call(self._pyvoip_call)
            self._pyvoip_call = None
            shutdown_phone()
            if self.mb:
                self.mb.stop()

    async def _rooms(self, sync, client, my_id: str) -> None:
        join = getattr(sync.rooms, "join", None) if getattr(sync, "rooms", None) else None
        if not join:
            return
        for room_id, room in join.items():
            if self._paired_room_id and room_id != self._paired_room_id:
                continue
            evs = events_from_room(room)
            i = 0
            while i < len(evs):
                ev = evs[i]
                i += 1
                sender = _sender(ev)
                et, c = get_event_type_and_content(ev)
                if not et:
                    continue
                if et == "m.room.message" and c.get("msgtype") == "m.text" and sender != my_id:
                    if self._bridge_user_id and sender != self._bridge_user_id:
                        continue
                    uri = _sip_in_message(c.get("body") or "")
                    if uri:
                        self.room_sip[room_id] = uri
                        print(f"[reverse] {room_id[-8:]} → {uri}", flush=True)
                        from nio import RoomSendError
                        r = await client.room_send(room_id, "m.room.message", {"msgtype": "m.text", "body": _READY})
                        if isinstance(r, RoomSendError):
                            print(f"[reverse] send: {r}", file=sys.stderr)
                    else:
                        from nio import RoomSendError
                        r = await client.room_send(
                            room_id,
                            "m.room.message",
                            {
                                "msgtype": "m.text",
                                "body": "Paste the SIP URI to dial (sip:user@host), then call the bot.",
                            },
                        )
                        if isinstance(r, RoomSendError):
                            print(f"[reverse] send: {r}", file=sys.stderr)
                    continue
                if et != "m.call.invite" or (c.get("invitee") not in (None, my_id)):
                    continue
                if self._bridge_user_id and sender and sender != self._bridge_user_id:
                    continue
                call_id = c.get("call_id")
                if not call_id or (room_id, call_id) in self._seen:
                    continue
                offer = (c.get("offer") or {}).get("sdp")
                if not offer:
                    continue
                self._seen.add((room_id, call_id))
                ver = str(c.get("version") or "1")
                peer_party = c.get("party_id") or ""
                print(
                    f"[reverse] m.call.invite call_id={call_id!r} "
                    f"element_party={'(empty)' if not peer_party else f'len={len(peer_party)}'}",
                    flush=True,
                )
                pre: list[tuple[str, str | None, int | None]] = []
                while i < len(evs):
                    t2, c2 = get_event_type_and_content(evs[i])
                    if t2 not in ("m.call.candidates", "m.call.candidate") or c2.get("call_id") != call_id:
                        break
                    pre.extend(trickle_ice_tuples_from_m_call_event(t2, c2))
                    i += 1
                await self._call(
                    client, room_id, call_id, ver, offer, peer_party, pre
                )

    async def _call(
        self,
        client,
        room_id: str,
        call_id: str,
        version: str,
        offer_sdp: str,
        element_party: str,
        pre_ice: list[tuple[str, str | None, int | None]],
    ) -> None:
        uri = self.room_sip.get(room_id)
        bot_party = make_party_id()
        mb = self.mb
        if not uri or not mb:
            from nio import RoomSendError
            await client.room_send(
                room_id, "m.room.message",
                {"msgtype": "m.text", "body": "Send a sip:user@host line first."},
            )
            p = MatrixPayloadCreator.create_hangup_payload(
                call_id, version, bot_party, "no_user_response"
            )["content"]
            r = await client.room_send(room_id, "m.call.hangup", p)
            if isinstance(r, RoomSendError):
                print(r, file=sys.stderr)
            return

        if self._pyvoip_bridge:
            try:
                self._pyvoip_bridge.stop()
            except Exception:
                pass
            self._pyvoip_bridge = None
        hangup_call(self._pyvoip_call)
        self._pyvoip_call = None
        mb.detach_pyvoip()

        try:
            answer = mb.create_answer_from_remote_offer(offer_sdp, remote_ice_candidates=pre_ice or None)
            loc = mb.get_local_ice_candidates()
            host, port = mb.get_sip_rtp_bind_addr()
            print(
                f"[reverse] WebRTC answer ready (pre-ICE tuples={len(pre_ice)}); "
                f"(legacy RTP port {host}:{port} unused with pyVoIP); {len(loc)} local ICE candidate(s)",
                flush=True,
            )

            tout = float(os.environ.get("REVERSE_SIP_INVITE_TIMEOUT", "60"))
            self._pyvoip_call, self._pyvoip_bridge, _txq = await dial_and_bridge_audio(
                mb, uri, wait_answer_timeout=tout
            )
            print("[reverse] pyVoIP call answered — audio bridge running", flush=True)

            await finish_matrix_webrtc_setup(
                self.bot, client, mb,
                room_id=room_id, call_id=call_id, version=version,
                bot_party=bot_party, element_party_id=element_party,
                answer_sdp=answer, local_ice_candidates=loc,
            )
            print(
                f"[reverse] Matrix signalling done for call_id={call_id} "
                f"(if audio is silent, check ICE / provider / REVERSE_PYVOIP_MY_IP).",
                flush=True,
            )
        except Exception as e:
            print(f"[reverse] {e}", file=sys.stderr)
            if self._pyvoip_bridge:
                try:
                    self._pyvoip_bridge.stop()
                except Exception:
                    pass
                self._pyvoip_bridge = None
            hangup_call(self._pyvoip_call)
            self._pyvoip_call = None
            mb.detach_pyvoip()
            from nio import RoomSendError
            p = MatrixPayloadCreator.create_hangup_payload(
                call_id, version, bot_party, "ice_failed"
            )["content"]
            r = await client.room_send(room_id, "m.call.hangup", p)
            if isinstance(r, RoomSendError):
                print(r, file=sys.stderr)


async def run() -> None:
    await ReverseBridge().run()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[reverse] stopped")
