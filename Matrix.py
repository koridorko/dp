from enum import Enum

class MatrixEventType(Enum):
    INVITE = "m.call.invite"
    CANDIDATE = "m.call.candidate"
    ANSWER = "m.call.answer"
    HANGUP = "m.call.hangup"
    SELECT_ANSWER = "m.call.select_answer"

class MatrixMessage():
    def __init__(self, json_data) -> None:
        self.json_data = json_data
        # this parameter should be correctly be called event_type
        # but to keep it similar to SIPMessage class, we will use message_type
        self.message_type: MatrixEventType | None = None

