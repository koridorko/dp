from enum import Enum


class SIPMessageType(Enum):
    # Request methods
    INVITE = "INVITE"
    ACK = "ACK"
    BYE = "BYE"
    REGISTER = "REGISTER"
    # Response status lines (SIP/2.0 status reason)
    OK = "200 OK"
    TRYING = "100 Trying"
    RINGING = "180 Ringing"


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
        """Extract method/status and sip version from starting line."""
        tokens = starting_line.split()
        if not tokens:
            raise SIPException("Empty starting line")

        # Response: "SIP/2.0 100 Trying" -> first token is "SIP/2.0"
        # Request:  "INVITE sip:... SIP/2.0" -> first token is method
        if tokens[0] == "SIP/2.0":
            # Status line: SIP/2.0 status_code reason_phrase
            if len(tokens) >= 2:
                method = " ".join(tokens[1:])  # e.g. "100 Trying", "200 OK"
            else:
                raise SIPException("Invalid SIP response: missing status code")
            # Version is first token
            sip_version_str = tokens[0]
        else:
            method = tokens[0]
            sip_version_str = tokens[-1] if len(tokens) > 1 else ""

        if method not in [e.value for e in SIPMessageType]:
            raise SIPException(f"Unknown SIP method or status: {method}")

        self.message_type = SIPMessageType(method)

        if sip_version_str.startswith("SIP/"):
            self.sip_version = float(sip_version_str.split("/")[1])
        else:
            raise SIPException(f"Unknown SIP version: {sip_version_str}")

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
                if key == "CSeq":
                    value = value.split(" ")[0]
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


def _to_crlf(message: str) -> str:
    """SIP uses CRLF as line terminator (RFC 3261). Normalize to CRLF."""
    return message.replace("\r\n", "\n").replace("\n", "\r\n")


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
        return SIPMessage(_to_crlf(message))

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
        return SIPMessage(_to_crlf(message))

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
        return SIPMessage(_to_crlf(message))

    def create_ok(
        self,
        from_uri,
        to_uri,
        call_id,
        cseq,
        sdp,
        response_to,
        via,
    ) -> str:
        """Create SIP 200 OK message"""
        sdp = sdp if sdp else ""
        content_type = "Content-Type: application/sdp" if sdp else ""
        message = f"""SIP/2.0 200 OK
Via: {via}
Max-Forwards: 70
From: {from_uri}
To: {to_uri}
Call-ID: {call_id}
CSeq: {cseq} {response_to}
{content_type}
Content-Length: {len(sdp)}\r\n\r\n{sdp}"""
        return _to_crlf(message)

    def create_trying(
        self, from_uri: str, to_uri: str, call_id: str, cseq: int, via: str
    ) -> str:
        """Create SIP 100 Trying message"""
        message = f"""SIP/2.0 100 Trying
Via: {via}
Max-Forwards: 70
From: {from_uri}
To: {to_uri}
Call-ID: {call_id}
CSeq: {cseq} TRYING
Content-Length: 0\r\n\r\n"""
        return _to_crlf(message)

    def create_ringing(
        self, from_uri: str, to_uri: str, call_id: str, cseq: int, via: str
    ) -> str:
        """Create SIP 180 Ringing message"""
        message = f"""SIP/2.0 180 Ringing
Via: {via}
Max-Forwards: 70
From: {from_uri}
To: {to_uri}
Call-ID: {call_id}
CSeq: {cseq} RINGING
Content-Length: 0\r\n\r\n"""
        return _to_crlf(message)

    def create_not_found(
        self, from_uri: str, to_uri: str, call_id: str, cseq: int, via: str
    ) -> str:
        """Create SIP 404 Not Found message"""
        message = f"""SIP/2.0 404 Not Found
Via: {via}
Max-Forwards: 70
From: {from_uri}
To: {to_uri}
Call-ID: {call_id}
CSeq: {cseq} NOT FOUND
Content-Length: 0\r\n\r\n"""
        return _to_crlf(message)