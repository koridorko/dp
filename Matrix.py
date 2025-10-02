import random
from enum import Enum


class MatrixEventType(Enum):
    INVITE = "m.call.invite"
    CANDIDATE = "m.call.candidate"
    ANSWER = "m.call.answer"
    HANGUP = "m.call.hangup"
    SELECT_ANSWER = "m.call.select_answer"


BIG_INT = 1000000000000


class MatrixMessage:
    def __init__(self, json_data) -> None:
        self.json_data = json_data
        # this parameter should be correctly be called event_type
        # but to keep it similar to SIPMessage class, we will use message_type
        self.message_type: MatrixEventType | None = None


class MatrixPayloadCreator:
    @staticmethod
    def create_invite_payload(
        sdp: str, call_id: str, version: int, lifetime: int = 60000
    ) -> dict:
        """Create payload for m.call.invite event"""
        payload = {
            "content": {
                "body": "SIP to Matrix call",
                "call_id": call_id,
                "version": version,
                "lifetime": lifetime,
                "offer": {"type": "offer", "sdp": sdp},
            },
            "event_type": MatrixEventType.INVITE.value,
            "txn_id": "12345",  # This should be unique for each transaction
        }
        return payload

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
    def create_hangup_payload(call_id: str, version: int) -> dict:
        """Create payload for m.call.hangup event"""
        payload = {
            "content": {"call_id": call_id, "version": version},
            "event_type": MatrixEventType.HANGUP.value,
            "txn_id": "12347",  # This should be unique for each transaction
        }
        return payload

    @staticmethod
    def create_candidate_payload(
        call_id: str, version: int, sdp_mid: str, sdp_mline_index: int
    ) -> dict:
        """Create payload for m.call.candidate event"""
        payload = {
            "content": {
                "call_id": call_id,
                "version": version,
            },
            "candidate": {"candidate": "candidate:1 1 UDP 2130706431"},
            "sdpMid": sdp_mid,
            "sdpMLineIndex": sdp_mline_index,
            "event_type": MatrixEventType.CANDIDATE.value,
            "txn_id": str(
                random.randint(0, BIG_INT)
            ),  # This should be unique for each transaction
        }

        return payload

    @staticmethod
    def create_select_answer_payload(call_id: str, version: int, sdp: str) -> dict:
        """Create payload for m.call.select_answer event"""
        payload = {
            "content": {
                "call_id": call_id,
                "version": version,
                "answer": {"type": "answer", "sdp": sdp},
            },
            "event_type": MatrixEventType.SELECT_ANSWER.value,
            "txn_id": str(
                random.randint(0, BIG_INT)
            ),  # This should be unique for each transaction
        }
        return payload
