"""Shared Matrix voice-call completion: m.call.answer, local ICE, remote trickle, select_answer."""

from __future__ import annotations

import asyncio
import os
import sys
import time

from bot.MatrixBot import MatrixBot
from Matrix import MatrixPayloadCreator
from nio import RoomSendError

from .config import WAIT_CANDIDATES_AFTER_ANSWER_SEC
from .matrix_sync import nio_sync, parse_sync_for_candidates_by_party


async def finish_matrix_webrtc_setup(
    bot: MatrixBot,
    client,
    media_bridge,
    *,
    room_id: str,
    call_id: str,
    version: str,
    bot_party: str,
    element_party_id: str,
    answer_sdp: str,
    local_ice_candidates: list[dict],
) -> None:
    """Send m.call.answer, trickle locals, poll remote candidates into MediaBridge, m.call.select_answer."""
    content = {
        "call_id": call_id,
        "version": str(version),
        "party_id": bot_party,
        "answer": {"type": "answer", "sdp": answer_sdp},
    }
    _bp = bot_party[:20] + ("…" if len(bot_party) > 20 else "")
    print(
        f"[Matrix] m.call.answer (sdp {len(answer_sdp)} B, "
        f"{len(local_ice_candidates)} local ICE) bot_party={_bp!r}",
        flush=True,
    )
    resp = await client.room_send(room_id, "m.call.answer", content)
    if isinstance(resp, RoomSendError):
        print(f"[matrix_trickle] m.call.answer failed: {resp}", file=sys.stderr)
        return

    if local_ice_candidates:
        payload = MatrixPayloadCreator.create_candidates_payload(
            call_id, version, local_ice_candidates, party_id=bot_party
        )["content"]
        try:
            await bot.send_call_candidates(room_id, payload)
            print(
                f"[Matrix] sent {len(local_ice_candidates)} local ICE candidate(s)",
                flush=True,
            )
        except Exception as e:
            print(f"[matrix_trickle] send_call_candidates: {e}", file=sys.stderr)

    if not element_party_id:
        print(
            "[Matrix] WARNING: invite had empty party_id — cannot match remote m.call.candidates; "
            "WebRTC may connect without trickle ICE and audio can fail.",
            file=sys.stderr,
        )

    collected: list[str] = []
    deadline = time.time() + float(
        os.environ.get(
            "REVERSE_WAIT_CANDIDATES_SEC",
            str(max(WAIT_CANDIDATES_AFTER_ANSWER_SEC, 3.0)),
        )
    )
    while time.time() < deadline:
        sync = await nio_sync(client)
        if sync and element_party_id and media_bridge:
            for cand in parse_sync_for_candidates_by_party(
                sync, room_id, call_id, element_party_id
            ):
                if cand and cand not in collected:
                    collected.append(cand)
                    try:
                        media_bridge.add_remote_candidate(cand, "0", 0)
                    except Exception:
                        pass
        await asyncio.sleep(0.1)

    _ep = (
        (element_party_id[:16] + "…")
        if len(element_party_id) > 16
        else (element_party_id or "(empty)")
    )
    print(
        f"[Matrix] trickle window done: {len(collected)} remote candidate(s) → MediaBridge "
        f"(element_party={_ep!r})",
        flush=True,
    )
    try:
        await bot.send_select_answer(
            room_id,
            MatrixPayloadCreator.create_select_answer_payload(
                call_id, version, bot_party, element_party_id
            )["content"],
        )
        print("[Matrix] m.call.select_answer sent", flush=True)
    except Exception as e:
        print(f"[matrix_trickle] select_answer: {e}", file=sys.stderr)
