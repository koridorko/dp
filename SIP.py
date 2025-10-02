from enum import Enum


class SIPMessageType(Enum):
    INVITE = "INVITE"
    ACK = "ACK"
    BYE = "BYE"
    REGISTER = "REGISTER"
    OK = "200 OK"


class SIPException(Exception):
    """Base class for SIP exceptions"""

    pass


class SIPMessage:
    def __init__(self, message: str):
        """Class for respresenting sip message"""
        self.message_string = message
        self.message_type: SIPMessageType | None = None
        self.sip_version: float | None = None
        self.starting_line: str | None = self.get_starting_line()

        # setting the data from starting line
        if self.starting_line:
            self.get_data_from_starting_line(self.starting_line)
        else:
            raise SIPException("Invalid SIP message: Missing starting line")

        headers_str, body_str = self.split_headers_and_body()

        self.headers: dict[str, str] = self.parse_headers(headers_str)
        # body is not mandatory in all sip messages
        # we will keep both sdp as string and parsed body as dict
        self.body_str: str | None = body_str
        self.body: dict[str, str] | None = self.parse_body(body_str)

    def get_starting_line(self) -> str | None:
        """Extract starting line from sip message"""
        lines = self.message_string.split("\n")
        # starting line is the first line of the message
        if not lines:
            # when not complete message is obtained (should never happen)
            raise SIPException("Empty SIP message")
        return lines[0].strip()

    def get_data_from_starting_line(self, starting_line: str) -> None:
        """Extract method and sip version from starting line"""
        # method will be first word in starting line
        method = starting_line.split(" ")[0].strip()
        # the case with 200 OK needs to be covered separately
        if method == "200":
            method = "200 OK"
        ########################################################
        # ^ ugly hack to cover 200 OK case ^ # (change when time will allow)
        ########################################################
        if method in SIPMessageType.__members__:
            self.message_type = SIPMessageType[method]
        else:
            raise SIPException(f"Unknown SIP method: {method}")

        # sip version will be last word in starting line
        sip_version = starting_line.split(" ")[-1].strip()
        if sip_version.startswith("SIP/"):
            # we are keeping only version number
            version_float = float(sip_version.split("/")[1])
            self.sip_version = version_float
        else:
            raise SIPException(f"Unknown SIP version: {sip_version}")

    def split_headers_and_body(self) -> tuple[str, str | None]:
        """Split headers and body from sip message"""
        # headers and body are separated by a blank line
        parts = self.message_string.split("\n\n", 1)
        headers = parts[0]
        body = parts[1] if len(parts) > 1 else None
        return headers, body

    def parse_headers(self, headers: str) -> dict[str, str]:
        """Parse headers from sip message"""
        headers_dict = {}
        lines = headers.split("\n")[1:]  # skip starting line
        for line in lines:
            if ": " in line:
                key, value = line.split(": ", 1)
                headers_dict[key.strip()] = value.strip()
        return headers_dict

    def parse_body(self, body: str | None) -> dict[str, str] | None:
        """Parse body from sip message"""
        # this is the case when message is headers only, e.g., BYE message
        if not body:
            return None
        # mostly for parsing out SDP body which is in format of key=value
        body_dict = {
            key: values
            for key, values in (
                line.split("=", 1) for line in body.split("\n") if "=" in line
            )
        }
        return body_dict

    def __str__(self) -> str:
        return f"""SIP Message:
Type: {self.message_type}
SIP Version: {self.sip_version}
Starting Line: {self.starting_line}
Headers: {self.headers}
Body: {self.body}"""


class SIPMessageCreator:
    """Class for creating SIP messages from given parameters"""

    def __init__(self) -> None:
        pass

    def create_invite(
        self, from_uri: str, to_uri: str, call_id: str, cseq: int, sdp: str
    ) -> SIPMessage:
        """Create SIP INVITE message"""
        message = f"""INVITE {to_uri} SIP/2.0
Via: SIP/2.0/UDP example.com;branch=z9hG4bK776asdhds
Max-Forwards: 70
From: {from_uri}
To: {to_uri}
Call-ID: {call_id}
CSeq: {cseq} INVITE
Content-Type: application/sdp
Content-Length: {len(sdp)}\r\n\r\n{sdp}"""
        return SIPMessage(message)

    def create_ack(
        self, from_uri: str, to_uri: str, call_id: str, cseq: int
    ) -> SIPMessage:
        """Create SIP ACK message"""
        message = f"""ACK {to_uri} SIP/2.0
Via: SIP/2.0/UDP example.com;branch=z9hG4bK776asdhds
Max-Forwards: 70
From: {from_uri}
To: {to_uri}
Call-ID: {call_id}
CSeq: {cseq} ACK
Content-Length: 0\r\n\r\n"""
        return SIPMessage(message)

    def create_bye(
        self, from_uri: str, to_uri: str, call_id: str, cseq: int
    ) -> SIPMessage:
        """Create SIP BYE message"""
        message = f"""BYE {to_uri} SIP/2.0
Via: SIP/2.0/UDP example.com;branch=z9hG4bK776asdhds
Max-Forwards: 70
From: {from_uri}
To: {to_uri}
Call-ID: {call_id}
CSeq: {cseq} BYE
Content-Length: 0\r\n\r\n"""
        return SIPMessage(message)

    def create_ok(
        self, from_uri: str, to_uri: str, call_id: str, cseq: int, sdp: str
    ) -> SIPMessage:
        """Create SIP 200 OK message"""
        message = f"""SIP/2.0 200 OK
Via: SIP/2.0/UDP example.com;branch=z9hG4bK776asdhds
Max-Forwards: 70
From: {from_uri}
To: {to_uri}
Call-ID: {call_id}
CSeq: {cseq} INVITE
Content-Type: application/sdp
Content-Length: {len(sdp)}\r\n\r\n{sdp}"""
        return SIPMessage(message)

