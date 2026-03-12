#! /bin/python
"""This is a file containing implementation of SIP bridge"""

import asyncio
import os
import socket
import threading
import time
import yaml
from SIP import SIPException, SIPMessage, SIPMessageType, SIPMessageCreator
from .SIPSession import BridgeCallSession, BridgeCallState

# NOTE: totaly arbitrary, may be changed later
BUFFER_SIZE = 2048


async def _bot_worker(bridge: "SIPBridge") -> None:
    """Runs in a dedicated thread: login Matrix bot and keep loop alive for later use."""
    try:
        from bot.MatrixBot import MatrixBot

        bot = MatrixBot()
        await bot.connect_to_server()
        bridge.matrix_bot = bot
        bridge.matrix_loop = asyncio.get_running_loop()
        bridge._bot_ready = True
        print("Matrix bot connected.")
    except Exception as e:
        print("Matrix bot failed to connect:", e)
        bridge._bot_ready = False
    while True:
        await asyncio.sleep(3600)


class SIPBridge:
    """Implementation of custom sip server/Bridge"""

    # NOTE: this server can be run locally with : poetry run python -m sip_bridge.SIP_bridge_server

    def __init__(self):
        self.server_ip: str = os.environ.get("SIP_BRIDGE_HOST", "0.0.0.0")
        self.port: int = int(os.environ.get("SIP_BRIDGE_PORT", "5060"))
        self.protocol: str = "UDP"

        self.message_creator = SIPMessageCreator()
        self.current_session: BridgeCallSession | None = None
        self.matrix_bot = None
        self.matrix_loop = None
        self._bot_ready = False

    def message_from_bytes(self, incomming_message: bytes):
        # construct SIPMessage object from bytes obtained via socket
        # decode for changing bytes to string
        return SIPMessage(incomming_message.decode("utf-8"))

    def send_response(self, socket: socket.socket, message: str, addr: str) -> None:
        socket.sendto(message.encode(), addr)

    def handle_register(self, sip_message: SIPMessage, sock: socket.socket, addr: str):
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

    def handle_invite(self, sip_message: SIPMessage, sock: socket.socket, addr: str):
        print("Invite received")
        print(sip_message)
        obtained_headers = sip_message.headers

        call_id = obtained_headers.get("Call-ID")
        cseq = int(obtained_headers.get("CSeq", "").split()[0])
        via = obtained_headers.get("Via", "")
        from_header = obtained_headers.get("From", "")
        to_header = obtained_headers.get("To", "")

        tag = "tag=server-12345678"
        to = to_header + ";" + tag

        # 100 Trying
        trying = self.message_creator.create_trying(
            from_header, to_header, call_id, cseq, via
        )
        self.send_response(sock, trying, addr)

        # there will be matrix side handling
        # and then we will send the answer
        # after we have all the respones from the matrix side needed
        # first we will read register.yaml to map phone numbers to matrix user ids

        registry: dict[str, str]
        with open("register.yaml", "r") as file:
            registry = yaml.safe_load(file) or {}

        sip_identifier = to_header.split("@")[0].replace("sip:", "").strip()
        matrix_user_id = registry.get(sip_identifier) if registry else None

        if matrix_user_id is None:
            print(f"Matrix user id for SIP identifier '{sip_identifier}' not found in registry")
            # we will send sip failure signaling message to the client
            failure = self.message_creator.create_not_found(
                from_header, to, call_id, cseq, via
            )
            self.send_response(sock, failure, addr)
            return

        print(f"Matrix user id for {matrix_user_id} is {matrix_user_id}")


        # 180 Ringing
        ringing = self.message_creator.create_ringing(
            from_header, to, call_id, cseq, via
        )
        self.send_response(sock, ringing, addr)

        # Create and store session for this call (Krok 1)
        self.current_session = BridgeCallSession(
            call_id=call_id,
            sip_client_addr=addr,
            from_header=from_header,
            to_header=to_header,
            via=via,
            cseq=cseq,
            matrix_user_id=matrix_user_id,
        )

    def run_server(self) -> None:
        """Run SIP server with one persistent UDP socket; Matrix bot runs in a background thread."""
        print("Server running :) !")

        # One persistent socket so we can send 200 OK / BYE later from bot thread (Krok 2)
        soc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            soc.bind((self.server_ip, self.port))
        except OSError as e:
            if e.errno == 98:  # Address already in use
                raise OSError(
                    f"Port {self.port} already in use. "
                    f"Stop the other process (e.g. previous server) or set SIP_BRIDGE_PORT=5061"
                ) from e
            raise
        print(f"Listening on {self.server_ip}:{self.port}")

        # # Start Matrix bot in background thread (same process)
        # bot_thread = threading.Thread(
        #     target=asyncio.run,
        #     args=(_bot_worker(self),),
        #     daemon=True,
        # )
        # bot_thread.start()
        # # Give bot time to connect before first INVITE
        # time.sleep(2)

        try:
            while True:
                message, adress = soc.recvfrom(BUFFER_SIZE)
                print(f"Recieved {len(message)} from adress: {adress}.")
                if len(message) < 8:
                    print("Obtained keep alive message")
                    continue
                sip_mess = self.message_from_bytes(message)
                match sip_mess.message_type:
                    case SIPMessageType.REGISTER:
                        self.handle_register(sip_mess, soc, adress)
                    case SIPMessageType.INVITE:
                        self.handle_invite(sip_mess, soc, adress)
                    case _:
                        print("Unhandled SIP message type:", sip_mess.message_type)
                        print(sip_mess)
        except KeyboardInterrupt:
            soc.close()
            print("Server closed ✅")


# NOTE:
#       This works for now, now we will add switch according to message type
#       and some "session" so we can track in which part of the initialization we are :)
#
bridge = SIPBridge()

bridge.run_server()
