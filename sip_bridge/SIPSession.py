# import requests
from enum import Enum


class SIPSessionState(Enum):
    # TODO: gonna make some session states for the
    #       tracking of the current user session :)
    BEFORE = 0
    INVITE_SEND = 1
    FIRST_RESPONSE = 2
    SECOND_RESPOSE = 3
    CALL = 4
    AFTER_HANG = 5


class SIPSession:
    def __init__(self):
        self.state: SIPSessionState
        self.pseudo_sip_uri: str
        self.registred: bool = False

    def register(self, uri: str) -> None:
        self.pseudo_sip_uri = uri
        self.registred = True
