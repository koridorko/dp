"""This is a file containing implementation of SIP bridge"""

import asyncio


class SIPBridge:
    """Implementation of custom sip server/Bridge"""

    # NOTE: we will have sip server where we will handle
    #       SIP messages according to our bridge
    #       functionality in this file will be both or accepting the callls
    #       from matrrix bot and calling the matrix bot
    def __init__(self):
        self.server_ip: str = "0.0.0.0"
        self.port: int = 5060
        self.protocol: str = "UDP"
