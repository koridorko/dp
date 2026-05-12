"""
Matrix sync and call-event parsing (nio-based).
Room finding, m.call.answer/candidates/hangup, nio schema patch.
"""

import json
import sys
import urllib.parse
import urllib.request

try:
    from nio import schemas as nio_schemas
except ImportError:
    nio_schemas = None

from .config import SYNC_POLL_MS
from .sdp_utils import sdp_summary


async def nio_sync(client) -> "SyncResponse | None":
    """Await client.sync(timeout); return SyncResponse or None. Must be called from async context (same loop as bot)."""
    try:
        return await client.sync(timeout=SYNC_POLL_MS)
    except Exception as e:
        print(f"[SYNC] nio sync failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def events_from_room(room) -> list:
    """Events from room.timeline, .state, .ephemeral (nio room)."""
    out = []
    for attr in ("timeline", "state", "ephemeral"):
        container = getattr(room, attr, None)
        if container and getattr(container, "events", None):
            out.extend(container.events)
    return out


def get_event_type_and_content(event) -> tuple[str | None, dict]:
    """(type, content) from nio event or source dict."""
    raw = getattr(event, "source", event) if not isinstance(event, dict) else event
    if not isinstance(raw, dict):
        return None, {}
    return raw.get("type"), raw.get("content") or {}


def parse_sync_for_answer_with_party_id(
    sync_response, room_id: str, call_id: str
) -> tuple[str, str] | None:
    """Return (sdp, party_id) from m.call.answer in sync_response for room_id/call_id."""
    if not getattr(sync_response, "rooms", None) or not getattr(
        sync_response.rooms, "join", None
    ):
        return None
    room = sync_response.rooms.join.get(room_id)
    if not room:
        return None
    for event in events_from_room(room):
        ev_type, content = get_event_type_and_content(event)
        if ev_type == "m.call.answer" and content.get("call_id") == call_id:
            sdp = (content.get("answer") or {}).get("sdp")
            if sdp:
                return (sdp, content.get("party_id") or "")
    return None


def trickle_ice_tuples_from_m_call_event(
    ev_type: str,
    content: dict,
) -> list[tuple[str, str | None, int | None]]:
    """m.call.candidates / m.call.candidate -> [(candidate, sdpMid, sdpMLineIndex), ...]."""
    out: list[tuple[str, str | None, int | None]] = []
    if ev_type == "m.call.candidates":
        for c in content.get("candidates") or []:
            cand = (
                c
                if isinstance(c, str)
                else (c.get("candidate") if isinstance(c, dict) else None)
            )
            if not cand or not str(cand).strip():
                continue
            mid = c.get("sdpMid") if isinstance(c, dict) else None
            idx = c.get("sdpMLineIndex") if isinstance(c, dict) else None
            if idx is not None and not isinstance(idx, int):
                try:
                    idx = int(idx)
                except (TypeError, ValueError):
                    idx = 0
            out.append((str(cand).strip(), mid, idx))
    elif ev_type == "m.call.candidate":
        cand = content.get("candidate")
        if isinstance(cand, dict):
            cand = cand.get("candidate")
        if cand and str(cand).strip():
            mid = content.get("sdpMid")
            idx = content.get("sdpMLineIndex")
            if idx is not None and not isinstance(idx, int):
                try:
                    idx = int(idx)
                except (TypeError, ValueError):
                    idx = 0
            out.append((str(cand).strip(), mid, idx))
    return out


def parse_sync_for_candidates_from_response(
    sync_response, room_id: str, call_id: str
) -> list[tuple[str, str | None, int | None]]:
    """ICE candidates from m.call.candidates in sync_response; (candidate, sdpMid, sdpMLineIndex)."""
    return [
        t[:3] for t in _parse_candidates_with_party(sync_response, room_id, call_id)
    ]


def parse_sync_for_candidates_by_party(
    sync_response, room_id: str, call_id: str, answer_party_id: str
) -> list[str]:
    """ICE candidates only from answerer (party_id == answer_party_id); returns list of candidate strings for SDP."""
    out = []
    for cand, _mid, _idx, pid in _parse_candidates_with_party(
        sync_response, room_id, call_id
    ):
        if pid and pid == answer_party_id and cand:
            out.append(cand)
    return out


def _parse_candidates_with_party(
    sync_response, room_id: str, call_id: str
) -> list[tuple[str, str | None, int | None, str]]:
    """(candidate, sdpMid, sdpMLineIndex, party_id) from m.call.candidates."""
    out = []
    if not getattr(sync_response, "rooms", None) or not getattr(
        sync_response.rooms, "join", None
    ):
        return out
    room = sync_response.rooms.join.get(room_id)
    if not room:
        return out
    for event in events_from_room(room):
        ev_type, content = get_event_type_and_content(event)
        if content.get("call_id") != call_id:
            continue
        party_id = content.get("party_id") or ""
        if ev_type == "m.call.candidates":
            for c in content.get("candidates") or []:
                cand = (
                    c
                    if isinstance(c, str)
                    else (c.get("candidate") if isinstance(c, dict) else None)
                )
                if not cand or not str(cand).strip():
                    continue
                mid = c.get("sdpMid") if isinstance(c, dict) else None
                idx = c.get("sdpMLineIndex") if isinstance(c, dict) else None
                if idx is not None and not isinstance(idx, int):
                    try:
                        idx = int(idx)
                    except (TypeError, ValueError):
                        idx = 0
                out.append((str(cand).strip(), mid, idx, party_id))
        elif ev_type == "m.call.candidate":
            cand = content.get("candidate")
            if isinstance(cand, dict):
                cand = cand.get("candidate")
            if cand and str(cand).strip():
                mid = content.get("sdpMid")
                idx = content.get("sdpMLineIndex")
                if idx is not None and not isinstance(idx, int):
                    try:
                        idx = int(idx)
                    except (TypeError, ValueError):
                        idx = 0
                out.append((str(cand).strip(), mid, idx, party_id))
    return out


def parse_sync_for_hangup_from_response(
    sync_response, room_id: str, call_id: str
) -> tuple[bool, str, str]:
    """(True, reason, party_id) if m.call.hangup for call_id in sync_response, else (False, '', '')."""
    if not getattr(sync_response, "rooms", None) or not getattr(
        sync_response.rooms, "join", None
    ):
        return (False, "", "")
    room = sync_response.rooms.join.get(room_id)
    if not room:
        return (False, "", "")
    for event in events_from_room(room):
        ev_type, content = get_event_type_and_content(event)
        if ev_type == "m.call.hangup" and content.get("call_id") == call_id:
            return (
                True,
                content.get("reason", "") or "",
                content.get("party_id", "") or "",
            )
    return (False, "", "")


def log_sync_call_events_from_response(
    sync_response, room_id: str, call_id: str
) -> None:
    """Log m.call.* events for call_id (debug)."""
    if not getattr(sync_response, "rooms", None) or not getattr(
        sync_response.rooms, "join", None
    ):
        return
    room = sync_response.rooms.join.get(room_id)
    if not room:
        return
    for event in events_from_room(room):
        ev_type, content = get_event_type_and_content(event)
        if (
            not (ev_type and ev_type.startswith("m.call."))
            or content.get("call_id") != call_id
        ):
            continue
        pid = content.get("party_id", "")
        if ev_type == "m.call.answer":
            answer_sdp = (content.get("answer") or {}).get("sdp") or ""
            print(
                f"[Element->bot] m.call.answer party_id={pid!r} answer_sdp_len={len(answer_sdp)}"
            )
            print(f"[Element->bot] m.call.answer SDP: {sdp_summary(answer_sdp)}")
        elif ev_type == "m.call.candidates":
            n = len(content.get("candidates") or [])
            print(f"[Element->bot] m.call.candidates party_id={pid!r} n_candidates={n}")
        elif ev_type == "m.call.hangup":
            print(
                f"[Element->bot] m.call.hangup party_id={pid!r} reason={content.get('reason', '')!r}"
            )
        else:
            print(f"[Element->bot] {ev_type} party_id={pid!r}")


def parse_sync_for_joined_member(sync_response, room_id: str, user_id: str) -> bool:
    """True if user_id has membership join in room_id."""
    if not getattr(sync_response, "rooms", None) or not getattr(
        sync_response.rooms, "join", None
    ):
        return False
    room = sync_response.rooms.join.get(room_id)
    if not room:
        return False
    for attr in ("timeline", "state"):
        container = getattr(room, attr, None)
        if not container or not getattr(container, "events", None):
            continue
        for event in container.events:
            ev_type, content = get_event_type_and_content(event)
            if ev_type != "m.room.member":
                continue
            state_key = getattr(event, "state_key", None) or (
                getattr(event, "source", None) or {}
            ).get("state_key")
            if state_key != user_id:
                continue
            if content.get("membership") == "join":
                return True
    return False


def _joined_members_from_nio_room(room) -> set:
    want = {}
    for event in events_from_room(room):
        ev_type, content = get_event_type_and_content(event)
        if ev_type != "m.room.member":
            continue
        state_key = getattr(event, "state_key", None) or (
            getattr(event, "source", None) or {}
        ).get("state_key")
        if state_key is None:
            continue
        want[state_key] = content.get("membership") == "join"
    return {uid for uid, is_join in want.items() if is_join}


def find_room_with_only_user_from_sync_response(
    sync_response, bot_user_id: str, target_user_id: str
) -> str | None:
    """Room where joined members are exactly {bot, target}."""
    if not getattr(sync_response, "rooms", None) or not getattr(
        sync_response.rooms, "join", None
    ):
        return None
    want = {bot_user_id, target_user_id}
    for rid, room in sync_response.rooms.join.items():
        if _joined_members_from_nio_room(room) == want:
            return rid
    return None


def _room_joined_members_via_api(client, room_id: str) -> set | None:
    try:
        base = client.homeserver.rstrip("/")
        token = getattr(client, "access_token", None)
        url = f"{base}/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}/joined_members?access_token={urllib.parse.quote(str(token), safe='')}"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return set((data.get("joined") or {}).keys())
    except Exception:
        return None


def _get_joined_room_ids(client) -> list[str]:
    try:
        base = client.homeserver.rstrip("/")
        token = getattr(client, "access_token", None)
        url = f"{base}/_matrix/client/v3/joined_rooms?access_token={urllib.parse.quote(str(token), safe='')}"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=5) as resp:
            data = json.loads(resp.read().decode())
        return list(data.get("joined_rooms") or [])
    except Exception:
        return []


def find_room_with_only_user_via_api_room_ids(
    client, room_ids: list[str], bot_user_id: str, target_user_id: str
) -> str | None:
    want = {bot_user_id, target_user_id}
    for rid in room_ids:
        members = _room_joined_members_via_api(client, rid)
        if members == want:
            return rid
    return None


def find_room_with_only_user_via_joined_rooms_api(
    client, bot_user_id: str, target_user_id: str
) -> str | None:
    want = {bot_user_id, target_user_id}
    for rid in _get_joined_room_ids(client):
        members = _room_joined_members_via_api(client, rid)
        if members == want:
            return rid
    return None


def sync_response_to_debug_dict(sync_response, room_id: str, call_id: str) -> dict:
    """Minimal dict for debug dump."""
    out = {
        "rooms_join_keys": [],
        "our_room_id": room_id,
        "our_room_in_join": False,
        "call_id": call_id,
        "call_events_seen": [],
    }
    if not getattr(sync_response, "rooms", None) or not getattr(
        sync_response.rooms, "join", None
    ):
        return out
    join = sync_response.rooms.join
    out["rooms_join_keys"] = list(join.keys())
    out["our_room_in_join"] = room_id in join
    room = join.get(room_id) or {}
    for sec in ("timeline", "state", "ephemeral"):
        container = getattr(room, sec, None)
        events = getattr(container, "events", None) or []
        for e in events:
            typ, content = get_event_type_and_content(e)
            if typ and typ.startswith("m.call."):
                out["call_events_seen"].append(
                    {
                        "section": sec,
                        "type": typ,
                        "content_keys": (
                            list(content.keys()) if isinstance(content, dict) else []
                        ),
                        "call_id_match": (
                            content.get("call_id") == call_id
                            if isinstance(content, dict)
                            else False
                        ),
                    }
                )
    return out


def dump_sync_debug_from_response(
    sync_response, room_id: str, call_id: str, dump_path: str = "sync_debug.json"
) -> None:
    out = sync_response_to_debug_dict(sync_response, room_id, call_id)
    try:
        with open(dump_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"[ANSWER] Debug: wrote sync summary to {dump_path}", file=sys.stderr)
    except Exception as e:
        print(f"[ANSWER] Debug dump failed: {e}", file=sys.stderr)


def patch_nio_call_candidates_schema() -> None:
    """Relax nio m.call.candidates so only 'candidate' is required (Matrix spec end-of-candidates)."""
    if nio_schemas is None:
        return
    try:
        schema = getattr(nio_schemas.Schemas, "call_candidates", None)
        if isinstance(schema, dict):
            items = (
                (schema.get("properties") or {})
                .get("content", {})
                .get("properties", {})
                .get("candidates", {})
                .get("items")
            )
            if isinstance(items, dict) and "required" in items:
                items["required"] = ["candidate"]
    except Exception:
        pass
