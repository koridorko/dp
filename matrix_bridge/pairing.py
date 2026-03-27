"""1:1 room pairing for Matrix→SIP reverse bridge (env + invite handling)."""

from __future__ import annotations

import asyncio
import os
import sys
import time

from bot.MatrixBot import MatrixBot

from sip_bridge.matrix_sync import nio_sync


def load_bridge_user_id() -> str:
    """
    Human Matrix user ID for 1:1-only mode.

    Set env **MY_MATRIX_USERNAME** to the full MXID, e.g. @alice:matrix.org
    """
    uid = (os.environ.get("MY_MATRIX_USERNAME") or "").strip()
    if not uid.startswith("@"):
        print(
            "[reverse] Set MY_MATRIX_USERNAME to your Matrix ID "
            "(e.g. @you:matrix.org) for 1:1 pairing.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return uid


async def wait_for_paired_room(bot: MatrixBot, peer_user_id: str) -> str:
    """
    Block until we are in a joined room with exactly [bot, peer_user_id], accepting invites first.
    """
    client = bot.client
    last_log = 0.0
    from nio import JoinError

    while True:
        sync = await nio_sync(client)
        if sync and getattr(sync, "rooms", None):
            invite_map = getattr(sync.rooms, "invite", None) or {}
            for room_id in list(invite_map.keys()):
                print(f"[reverse] Accepting invite to room …{room_id[-8:]}", flush=True)
                jr = await client.join(room_id)
                if isinstance(jr, JoinError):
                    print(f"[reverse] join failed: {jr}", file=sys.stderr)

        rid = await bot.find_room_with_only_user(peer_user_id)
        if rid:
            return rid

        now = time.time()
        if now - last_log > 45:
            last_log = now
            print(
                f"[reverse] Waiting: invite this bot from {peer_user_id} into a 1:1 room "
                f"(exactly two members: you and the bot).",
                flush=True,
            )
        await asyncio.sleep(0.5)
