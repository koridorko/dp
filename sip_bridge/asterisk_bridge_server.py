#! /usr/bin/env python3
"""
Asterisk + Matrix bridge:
- Asterisk receives SIP (Linphone), sends call to Stasis(matrix-bridge, EXTEN).
- We create External Media channel (RTP to our port, ulaw), bridge with SIP channel.
- We send m.call.invite to Matrix user (register.yaml: extension -> matrix_user_id).
- Media: Asterisk (ulaw) <-> MediaBridge (ulaw<->48k PCM) <-> WebRTC (Opus) <-> Element.

Run: ./scripts/run_asterisk.sh
Then: poetry run python -m sip_bridge
Linphone: sip:111@127.0.0.1 (111 from register.yaml -> Matrix user)
"""

import asyncio
import logging
import os
import sys
import time
import traceback

import yaml

try:
    from dotenv import load_dotenv

    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(_root, "bot", ".env"))
except ImportError:
    pass

from bot.MatrixBot import MatrixBot
from Matrix import MatrixPayloadCreator, make_party_id

from . import matrix_sync
from .ari_client import (
    channel_still_exists,
    delete_channel,
    resolve_caller_sip_identity,
    run_ari_websocket,
)
from .config import (ANSWER_TIMEOUT_SEC, DEFAULT_RTP_PORT, MINIMAL_INVITE_SDP,
                     WAIT_CANDIDATES_AFTER_ANSWER_SEC,
                     WAIT_JOIN_ROOM_SEC)
from .sdp_utils import inject_ice_candidates_into_sdp

# MediaBridge: RTP (ulaw from Asterisk) <-> WebRTC (Opus to Element)
try:
    from MediaBridge import MediaBridge

    _MEDIABRIDGE_AVAILABLE = True
except ImportError:
    MediaBridge = None
    _MEDIABRIDGE_AVAILABLE = False


async def _notify_matrix_sip_ended(
    bridge: "AsteriskBridge",
    call_id: str,
    room_id: str,
    party_id: str,
) -> None:
    """Tell Element the call ended because the SIP leg hung up (Linphone / Asterisk)."""
    if not bridge.matrix_bot:
        return
    try:
        await bridge.matrix_bot.send_hangup(
            room_id,
            MatrixPayloadCreator.create_hangup_payload(
                call_id, "1", party_id, "sip_hangup"
            )["content"],
        )
        print(
            f"[Bridge] Sent m.call.hangup to Matrix (SIP leg ended) call_id={call_id}",
            flush=True,
        )
    except Exception as e:
        print(f"[Bridge] m.call.hangup after SIP end failed: {e}", file=sys.stderr)


async def _watch_call_session(bridge: "AsteriskBridge", call_id: str) -> None:
    """
    While the call is active:
    - Poll ARI GET /channels/{sip_id}: when Linphone hangs up, Asterisk removes the PJSIP channel (404).
      That is reliable even if the ARI WebSocket does not deliver ChannelDestroyed (e.g. after native bridge).
    - Poll Matrix sync for m.call.hangup from the remote user; then ARI-hangup the SIP channel.
    """
    loop = asyncio.get_event_loop()

    while True:
        await asyncio.sleep(0.35)
        if bridge._sessions_lock is None or not bridge.matrix_bot:
            return
        async with bridge._sessions_lock:
            if call_id not in bridge.sessions:
                return
            snap = dict(bridge.sessions[call_id])
        room_id = snap["room_id"]
        our_party = snap["party_id"]
        answer_party_id = snap.get("answer_party_id") or ""
        sip_cid = snap["sip_channel_id"]

        base, user, pwd = bridge.ari_base, bridge.ari_user, bridge.ari_pass
        if base and user is not None and pwd is not None:
            still = await loop.run_in_executor(
                None,
                lambda: channel_still_exists(base, user, pwd, sip_cid),
            )
            if not still:
                async with bridge._sessions_lock:
                    if call_id not in bridge.sessions:
                        return
                    bridge.sessions.pop(call_id, None)
                await _notify_matrix_sip_ended(bridge, call_id, room_id, our_party)
                return

        sync_resp = await matrix_sync.nio_sync(bridge.matrix_bot.client)
        if not sync_resp:
            continue
        ok, _reason, hid_party = matrix_sync.parse_sync_for_hangup_from_response(
            sync_resp, room_id, call_id
        )
        if not ok:
            continue

        # Our hangup already sent (e.g. from SIP poll or ChannelDestroyed)
        if hid_party == our_party:
            async with bridge._sessions_lock:
                bridge.sessions.pop(call_id, None)
            return

        # Remote Matrix client hung up (any party_id other than the bot's)
        if hid_party and hid_party != our_party:
            if answer_party_id and hid_party != answer_party_id:
                continue
            async with bridge._sessions_lock:
                if call_id not in bridge.sessions:
                    return
                bridge.sessions.pop(call_id, None)
            loop = asyncio.get_event_loop()
            base, user, pwd = bridge.ari_base, bridge.ari_user, bridge.ari_pass
            if base and user is not None and pwd is not None:
                sc = sip_cid
                await loop.run_in_executor(
                    None,
                    lambda: delete_channel(base, user, pwd, sc),
                )
            print(
                f"[Bridge] Matrix hangup (party_id={hid_party}); ARI hangup SIP {sip_cid}",
                flush=True,
            )
            return


def _load_register() -> dict:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "register.yaml")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


async def handle_incoming_call(
    bridge: "AsteriskBridge",
    sip_channel_id: str,
    extension: str,
) -> None:
    """One SIP channel entered Stasis; create External Media already done by ARI. Send m.call.invite and wait for answer."""
    registry = _load_register()
    matrix_user_id = registry.get(extension) if extension else None
    if not matrix_user_id:
        print(f"[Asterisk] No register for extension {extension!r}; ignoring.")
        return

    if not bridge.media_bridge or not bridge.matrix_bot:
        print("[Asterisk] MediaBridge or bot not ready.")
        return

    call_id = f"asterisk-{sip_channel_id[:12]}"
    party_id = make_party_id()
    room_id = None
    bot = bridge.matrix_bot
    client = bot.client
    bot_user_id = getattr(bot, "user_id", "") or getattr(client, "user_id", "")

    # Find or create room
    sync_resp = await matrix_sync.nio_sync(client)
    if sync_resp and bot_user_id:
        room_id = matrix_sync.find_room_with_only_user_from_sync_response(
            sync_resp, bot_user_id, matrix_user_id
        )
    if not room_id and bot_user_id:
        room_id = matrix_sync.find_room_with_only_user_via_joined_rooms_api(
            client, bot_user_id, matrix_user_id
        )
    if not room_id:
        room_id = await bot.find_room_with_only_user(matrix_user_id)
    if not room_id:
        room_id = await bot.create_room_and_invite_user(matrix_user_id)
        deadline = time.time() + WAIT_JOIN_ROOM_SEC
        while time.time() < deadline:
            sync_join = await matrix_sync.nio_sync(client)
            if sync_join and matrix_sync.parse_sync_for_joined_member(
                sync_join, room_id, matrix_user_id
            ):
                break
            await asyncio.sleep(0.5)

    # Create WebRTC offer and send m.call.invite

    # Notify room with caller SIP identity (From / PJSIP remote) before m.call.invite
    caller_identity = ""
    base, auser, apwd = bridge.ari_base, bridge.ari_user, bridge.ari_pass
    if base and auser is not None and apwd is not None:
        sip_domain = (os.environ.get("ASTERISK_SIP_DOMAIN") or "").strip()
        loop = asyncio.get_event_loop()
        caller_identity = await loop.run_in_executor(
            None,
            lambda: resolve_caller_sip_identity(
                base, auser, apwd, sip_channel_id, sip_domain
            ),
        )

    try:
        print(f"[Asterisk] Sending message to {matrix_user_id}")
        client = bot.client
        if caller_identity:
            body = f"Incoming call from {caller_identity} at matrix bridge"
        else:
            body = f"Calling you from {extension} at matrix bridge"
        await client.room_send(
            room_id, "m.room.message", {"msgtype": "m.text", "body": body}
        )
        print(
            f"[Asterisk] Creating offer for ext={extension} caller={caller_identity or '?'} room={room_id}"
        )
        offer_sdp = await asyncio.get_event_loop().run_in_executor(
            None, bridge.media_bridge.create_offer
        )
    except Exception as e:
        print(f"[Asterisk] create_offer failed: {e}", file=sys.stderr)
        offer_sdp = MINIMAL_INVITE_SDP

    content = {
        "call_id": call_id,
        "version": "1",
        "lifetime": 60000,
        "offer": {"type": "offer", "sdp": offer_sdp},
        "party_id": party_id,
        "invitee": matrix_user_id,
    }
    await bot.send_call_invite(room_id, content)
    print(
        f"[Asterisk] Sent m.call.invite room={room_id} ext={extension} -> {matrix_user_id}"
    )

    cands = bridge.media_bridge.get_local_ice_candidates()
    if cands:
        try:
            await bot.send_call_candidates(
                room_id,
                MatrixPayloadCreator.create_candidates_payload(
                    call_id, "1", cands, party_id=party_id
                )["content"],
            )
        except Exception as e:
            print(f"[Asterisk] send_call_candidates: {e}", file=sys.stderr)

    # Wait for m.call.answer
    deadline = time.time() + ANSWER_TIMEOUT_SEC
    answer_data = None
    while time.time() < deadline:
        sync_resp = await matrix_sync.nio_sync(client)
        answer_data = (
            matrix_sync.parse_sync_for_answer_with_party_id(sync_resp, room_id, call_id)
            if sync_resp
            else None
        )
        if answer_data:
            break
        await asyncio.sleep(0.3)

    if not answer_data:
        print(f"[Asterisk] Timeout waiting for m.call.answer call_id={call_id}")
        await bot.send_hangup(
            room_id,
            MatrixPayloadCreator.create_hangup_payload(
                call_id, "1", party_id, "timeout"
            )["content"],
        )
        return

    sdp, answer_party_id = answer_data
    candidates_collected = []
    if sync_resp and answer_party_id:
        candidates_collected.extend(
            matrix_sync.parse_sync_for_candidates_by_party(
                sync_resp, room_id, call_id, answer_party_id
            )
        )
    deadline_cand = time.time() + WAIT_CANDIDATES_AFTER_ANSWER_SEC
    while time.time() < deadline_cand:
        extra = await matrix_sync.nio_sync(client)
        if extra and answer_party_id:
            more = matrix_sync.parse_sync_for_candidates_by_party(
                extra, room_id, call_id, answer_party_id
            )
            for c in more:
                if c and c not in candidates_collected:
                    candidates_collected.append(c)
        await asyncio.sleep(0.1)

    sdp_with_candidates = inject_ice_candidates_into_sdp(sdp, candidates_collected)
    bridge.media_bridge.set_remote_answer(sdp_with_candidates)
    for c, mid, ml in matrix_sync.parse_sync_for_candidates_from_response(
        sync_resp or (), room_id, call_id
    ):
        try:
            bridge.media_bridge.add_remote_candidate(c, mid, ml)
        except Exception:
            pass

    await bot.send_select_answer(
        room_id,
        MatrixPayloadCreator.create_select_answer_payload(
            call_id, "1", party_id, answer_party_id
        )["content"],
    )
    print("[Asterisk] Call connected.")

    if bridge._sessions_lock is None:
        return
    async with bridge._sessions_lock:
        bridge.sessions[call_id] = {
            "room_id": room_id,
            "sip_channel_id": sip_channel_id,
            "party_id": party_id,
            "answer_party_id": answer_party_id,
        }
    asyncio.create_task(_watch_call_session(bridge, call_id))


class AsteriskBridge:
    def __init__(self):
        self.matrix_bot: MatrixBot | None = None
        self.matrix_loop = None
        self.media_bridge = None
        self.sessions: dict = {}
        self._sessions_lock: asyncio.Lock | None = None
        self._call_lock: asyncio.Lock | None = None
        self.ari_base: str | None = None
        self.ari_user: str | None = None
        self.ari_pass: str | None = None

    def _on_incoming_call(self, sip_channel_id: str, extension: str) -> None:
        """Called from ARI WebSocket task - schedule handle_incoming_call."""
        if self.matrix_loop:
            asyncio.create_task(self._handle_with_lock(sip_channel_id, extension))

    async def _on_sip_channel_destroyed(self, channel_id: str) -> None:
        """SIP leg hung up (Linphone); notify Matrix with m.call.hangup."""
        if self._sessions_lock is None or not self.matrix_bot:
            return
        async with self._sessions_lock:
            hit: tuple[str, dict] | None = None
            for cid, s in list(self.sessions.items()):
                if s.get("sip_channel_id") == channel_id:
                    hit = (cid, dict(s))
                    break
            if not hit:
                return
            call_id, sess = hit
            self.sessions.pop(call_id, None)

        await _notify_matrix_sip_ended(self, call_id, sess["room_id"], sess["party_id"])

    async def _handle_with_lock(self, sip_channel_id: str, extension: str) -> None:
        if self._call_lock is None:
            return
        async with self._call_lock:
            await handle_incoming_call(self, sip_channel_id, extension)


async def main() -> None:
    # Always run from repo root (register.yaml, bot/.env).
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(_repo_root)

    matrix_sync.patch_nio_call_candidates_schema()
    bridge = AsteriskBridge()

    # RTP host for External Media (Asterisk sends RTP here)
    rtp_host = os.environ.get("BRIDGE_RTP_HOST", "127.0.0.1").strip()
    rtp_port = int(os.environ.get("BRIDGE_RTP_PORT", str(DEFAULT_RTP_PORT)))
    advertised = os.environ.get("MEDIABRIDGE_ADVERTISED_HOST", "").strip() or rtp_host

    sip_codec = (os.environ.get("BRIDGE_SIP_CODEC", "ulaw") or "ulaw").strip().lower()

    if _MEDIABRIDGE_AVAILABLE and MediaBridge:
        bridge.media_bridge = MediaBridge(
            sip_rtp_port=rtp_port,
            listen_host="0.0.0.0",
            advertised_host=advertised or None,
            sip_codec=sip_codec,
        )
        bridge.media_bridge.start()
        print(f"[Bridge] MediaBridge started ({sip_codec}) on {rtp_host}:{rtp_port}")
    else:
        print("MediaBridge not available.", file=sys.stderr)
        sys.exit(1)

    # Matrix bot
    for var in ("MATRIX_BOT_USERNAME", "MATRIX_BOT_PASSWORD", "MATRIX_BOT_HOMESERVER"):
        if not os.environ.get(var, "").strip():
            print(
                f"[Bridge] Missing env var {var}. Load bot/.env (source bot/.env) or export it.",
                file=sys.stderr,
            )
            sys.exit(1)
    bot = MatrixBot()
    await bot.connect_to_server()
    bridge.matrix_bot = bot
    bridge.matrix_loop = asyncio.get_event_loop()
    bridge._call_lock = asyncio.Lock()
    bridge._sessions_lock = asyncio.Lock()
    print("[Bridge] Matrix bot connected.")

    # ARI
    ari_base = os.environ.get("ASTERISK_ARI_URL", "http://127.0.0.1:8088").strip()
    ari_user = os.environ.get("ASTERISK_ARI_USER", "matrix_bridge").strip()
    ari_pass = os.environ.get("ASTERISK_ARI_PASSWORD", "matrixbridge").strip()
    app_name = os.environ.get("ASTERISK_STASIS_APP", "matrix-bridge").strip()
    bridge.ari_base = ari_base
    bridge.ari_user = ari_user
    bridge.ari_pass = ari_pass

    ari_task = asyncio.create_task(
        run_ari_websocket(
            ari_base,
            ari_user,
            ari_pass,
            app_name,
            rtp_host,
            rtp_port,
            on_incoming_call=bridge._on_incoming_call,
            loop=bridge.matrix_loop,
            format_codec=sip_codec,
            on_sip_channel_destroyed=bridge._on_sip_channel_destroyed,
        )
    )

    print(f"[Bridge] ARI WebSocket connecting to {ari_base} app={app_name}")
    try:
        await ari_task
    finally:
        ari_task.cancel()
        try:
            await ari_task
        except asyncio.CancelledError:
            pass
        if bot and getattr(bot, "client", None):
            try:
                await bot.client.close()
            except Exception:
                pass


def run() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Bridge] Stopped.")
    except Exception as e:
        print(f"[Bridge] Error: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run()
