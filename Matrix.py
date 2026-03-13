import random
import string
from enum import Enum


class MatrixEventType(Enum):
    INVITE = "m.call.invite"
    CANDIDATE = "m.call.candidate"
    ANSWER = "m.call.answer"
    HANGUP = "m.call.hangup"
    SELECT_ANSWER = "m.call.select_answer"


BIG_INT = 1000000000000


def make_party_id(length: int = 8) -> str:
    """Matrix VoIP party_id: 1–255 chars from [0-9a-zA-Z._~-]. Recommended 8."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


class MatrixMessage:
    def __init__(self, json_data) -> None:
        self.json_data = json_data
        # this parameter should be correctly be called event_type
        # but to keep it similar to SIPMessage class, we will use message_type
        self.message_type: MatrixEventType | None = None


class MatrixPayloadCreator:
    @staticmethod
    def create_invite_payload(
        sdp: str,
        call_id: str,
        version: str | int = "1",
        lifetime: int = 60000,
        party_id: str | None = None,
        invitee: str | None = None,
    ) -> dict:
        """Create payload for m.call.invite event. Returns full payload with 'content' and 'event_type'."""
        content = {
            "call_id": call_id,
            "version": str(version),
            "lifetime": lifetime,
            "offer": {"type": "offer", "sdp": sdp},
            "party_id": party_id or make_party_id(),
        }
        if invitee:
            content["invitee"] = invitee
        return {
            "content": content,
            "event_type": MatrixEventType.INVITE.value,
        }

    @staticmethod
    def create_answer_payload(sdp: str, call_id: str, version: int) -> dict:
        """Create payload for m.call.answer event"""
        payload = {
            "content": {
                "call_id": call_id,
                "version": version,
                "answer": {"type": "answer", "sdp": sdp},
            },
            "event_type": MatrixEventType.ANSWER.value,
            "txn_id": "12346",  # This should be unique for each transaction
        }
        return payload

    @staticmethod
    def create_hangup_payload(
        call_id: str, version: str | int = "1", party_id: str = "", reason: str = "user_hangup"
    ) -> dict:
        """Create payload for m.call.hangup event."""
        content = {
            "call_id": call_id,
            "version": str(version),
            "party_id": party_id,
            "reason": reason,
        }
        return {"content": content, "event_type": MatrixEventType.HANGUP.value}

    @staticmethod
    def create_candidate_payload(
        call_id: str,
        version: str | int,
        candidate: str,
        sdp_mid: str,
        sdp_mline_index: int,
    ) -> dict:
        """Create payload for single m.call.candidate event (one candidate)."""
        return {
            "content": {
                "call_id": call_id,
                "version": str(version),
                "candidate": candidate,
                "sdpMid": sdp_mid,
                "sdpMLineIndex": sdp_mline_index,
            },
            "event_type": MatrixEventType.CANDIDATE.value,
        }

    @staticmethod
    def create_candidates_payload(
        call_id: str,
        version: str | int,
        candidates: list[dict],
        party_id: str = "",
    ) -> dict:
        """Create payload for m.call.candidates event (batch). Spec requires party_id and end-of-candidates (empty string)."""
        out = [
            {
                "candidate": c.get("candidate", ""),
                "sdpMid": c.get("sdpMid", "0"),
                "sdpMLineIndex": c.get("sdpMLineIndex", 0),
            }
            for c in candidates
        ]
        out.append({"candidate": "", "sdpMid": "0", "sdpMLineIndex": 0})
        content = {
            "call_id": call_id,
            "version": str(version),
            "candidates": out,
        }
        if party_id:
            content["party_id"] = party_id
        return {
            "content": content,
            "event_type": "m.call.candidates",
        }

    @staticmethod
    def create_select_answer_payload(
        call_id: str,
        version: str | int = "1",
        party_id: str = "",
        selected_party_id: str = "",
    ) -> dict:
        """Create payload for m.call.select_answer. Spec: selected_party_id = party_id from the answer we chose."""
        content = {
            "call_id": call_id,
            "version": str(version),
            "party_id": party_id,
            "selected_party_id": selected_party_id,
        }
        return {"content": content, "event_type": MatrixEventType.SELECT_ANSWER.value}
