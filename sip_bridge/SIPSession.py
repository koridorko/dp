# import requests
from enum import Enum
from typing import Optional


class SIPSessionState(Enum):
    # TODO: gonna make some session states for the
    #       tracking of the current user session :)
    BEFORE = 0
    INVITE_SEND = 1
    FIRST_RESPONSE = 2
    SECOND_RESPOSE = 3
    CALL = 4
    AFTER_HANG = 5


class BridgeCallState(Enum):
    """State of one SIP↔Matrix bridged call."""

    WAITING_MATRIX_ANSWER = "waiting_matrix_answer"
    WAITING_SIP_ACK = "waiting_sip_ack"
    IN_CALL = "in_call"
    ENDED = "ended"


class BridgeCallSession:
    """One active bridged call: SIP side + Matrix side."""

    def __init__(
        self,
        call_id: str,
        sip_client_addr: tuple[str, int],
        from_header: str,
        to_header: str,
        via: str,
        cseq: int,
        matrix_user_id: str,
    ):
        self.call_id = call_id
        self.sip_client_addr = sip_client_addr
        self.from_header = from_header
        self.to_header = to_header
        self.via = via
        self.cseq = cseq
        self.matrix_user_id = matrix_user_id

        self.state = BridgeCallState.WAITING_MATRIX_ANSWER
        self.room_id: Optional[str] = None
        self.party_id: Optional[str] = None
        self.matrix_answer_sdp: Optional[str] = None

    def to_tagged(self) -> str:
        """To header with server tag (for SIP responses)."""
        tag = "tag=server-12345678"
        return self.to_header + ";" + tag


class SIPSession:
    def __init__(self):
        self.state: SIPSessionState
        self.pseudo_sip_uri: str
        self.registred: bool = False

    def register(self, uri: str) -> None:
        self.pseudo_sip_uri = uri
        self.registred = True
