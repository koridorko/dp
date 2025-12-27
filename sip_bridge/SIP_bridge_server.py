#! /bin/python
"""This is a file containing implementation of SIP bridge"""

# import as ayncio
import socket
from SIP import SIPException, SIPMessage, SIPMessageType, SIPMessageCreator

# NOTE: totaly arbitrary, may be changed later
BUFFER_SIZE = 2048


class SIPBridge:
    """Implementation of custom sip server/Bridge"""

    # NOTE: this server can be run locally with : poetry run python -m sip_bridge.SIP_bridge_server

    def __init__(self):
        self.server_ip: str = "0.0.0.0"
        self.port: int = 5060
        self.protocol: str = "UDP"

        self.message_creator = SIPMessageCreator()

    def message_from_bytes(self, incomming_message: bytes):
        # construct SIPMessage object from bytes obtained via socket
        # decode for changing bytes to string
        return SIPMessage(incomming_message.decode("utf-8"))

    def send_response(self, socket: socket.socket, message: str, addr: str) -> None:
        socket.sendto(message.encode(), addr)

    def handle_register(self, sip_message: SIPMessage, sock: socket.socket, addr):
        obtained_headers = sip_message.headers
        # NOTE: we need to take the tag from From part and add it to the to part in server response

        tag = "tag=server-12345678"
        to = obtained_headers.get("To", "") + ";" + tag
        response_message = self.message_creator.create_ok(
            # we are mostly copying from the REGISTER request
            obtained_headers.get("From"),
            to,
            obtained_headers.get("Call-ID"),
            int(obtained_headers.get("CSeq", "")),
            # we are not sending sdp in response to register
            None,
            # we are sending response to register message
            "REGISTER",
            obtained_headers.get("Via", ""),
        )
        print("sending: ", response_message)
        self.send_response(sock, response_message, addr)

    def run_server(self) -> None:
        # TODO: synchronous for now, later needed to make async !!!!
        # or we actually don't since server will be run localy and
        # we need just one session/call in the moment ????
        print("Server running :) !")
        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as soc:
                soc.bind((self.server_ip, self.port))
                try:
                    message, adress = soc.recvfrom(BUFFER_SIZE)
                    # we have message now in the buffer, we can parse it
                    print(f"Recieved {len(message)} from adress: {adress}.")
                    # if the message is shorter than 8 bytes it is probably ICMP ping
                    if len(message) < 8:
                        print("Obtained keep alive message")
                        continue
                    sip_mess = self.message_from_bytes(message)
                    message_type = SIPMessageType(sip_mess.message_type)
                    match sip_mess.message_type:
                        case SIPMessageType.REGISTER:
                            self.handle_register(sip_mess, soc, adress)
                        case _:
                            print("Something went very wrong! :)")
                            print(sip_mess)
                            exit(1)
                except KeyboardInterrupt:
                    soc.close()
                    print("Server closed ✅")
                    exit(0)
                # finally:
                #     soc.close()
                #     print("Server closed ✅")
                #


# NOTE:
#       This works for now, now we will add switch according to message type
#       and some "session" so we can track in which part of the initialization we are :)
#
bridge = SIPBridge()

bridge.run_server()
