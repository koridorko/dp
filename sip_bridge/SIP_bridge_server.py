#! /bin/python
"""This is a file containing implementation of SIP bridge"""

import asyncio
import socket
from SIP import SIPMessage

# NOTE: totaly arbitrary, may be changed later
BUFFER_SIZE = 2048


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

    def message_from_bytes(self, incomming_message: bytes):
        return SIPMessage(incomming_message.decode("utf-8"))

    def run_server(self) -> None:
        # TODO: synchronous for now, later needed to make async
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as soc:
            soc.bind((self.server_ip, self.port))
            print(f"UDP server running on ip: {self.server_ip} and port: {self.port}")
            while True:
                message, addres = soc.recvfrom(BUFFER_SIZE)
                # we have message now in the buffer, we can parse it
                print(f"Recieved {len(message)} from adress: {addres}.")
                sip_mess = self.message_from_bytes(message)

                print(sip_mess)


bridge = SIPBridge()

bridge.run_server()
