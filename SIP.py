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
        """Class for representing a SIP message."""
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
            # Status line: SIP/2.0 status_code reason_phrase (some clients send "200 Ok" instead of "200 OK")
            if len(tokens) >= 2:
                method = " ".join(tokens[1:])
                if len(tokens) >= 3 and tokens[1] == "200" and tokens[2].lower() == "ok":
                    method = "200 OK"
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
        """Split headers and body from sip message. Blank line can be \\n\\n or \\r\\n\\r\\n (RFC 3261)."""
        s = self.message_string
        for sep in ("\r\n\r\n", "\n\n"):
            if sep in s:
                i = s.index(sep)
                headers = s[:i]
                body = s[i + len(sep) :].strip()
                return headers, body if body else None
        return s, None

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
        self,
        from_uri: str,
        to_uri: str,
        call_id: str,
        cseq: int,
        via: str | None = None,
        request_uri: str | None = None,
    ) -> SIPMessage:
        """Create SIP BYE request. via = where UAS sends 200 OK; request_uri = clean URI for BYE line (else to_uri)."""
        via_line = via or "SIP/2.0/UDP example.com;branch=z9hG4bK776asdhds"
        req_uri = request_uri if request_uri is not None else to_uri
        message = f"""BYE {req_uri} SIP/2.0
Via: {via_line}
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
        contact: str | None = None,
    ) -> str:
        """Create SIP 200 OK message. For 200 OK to INVITE, pass contact so UAC can send ACK (RFC 3261)."""
        sdp = sdp if sdp else ""
        content_type = "Content-Type: application/sdp" if sdp else ""
        contact_line = f"Contact: {contact}\r\n" if contact else ""
        message = f"""SIP/2.0 200 OK
Via: {via}
Max-Forwards: 70
From: {from_uri}
To: {to_uri}
Call-ID: {call_id}
CSeq: {cseq} {response_to}
{contact_line}{content_type}
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

    def create_service_unavailable(
        self, from_uri: str, to_uri: str, call_id: str, cseq: int, via: str
    ) -> str:
        """Create SIP 503 Service Unavailable (e.g. media bridge not ready)."""
        message = f"""SIP/2.0 503 Service Unavailable
Via: {via}
Max-Forwards: 70
From: {from_uri}
To: {to_uri}
Call-ID: {call_id}
CSeq: {cseq} INVITE
Retry-After: 60
Content-Length: 0\r\n\r\n"""
        return _to_crlf(message)