

from pprint import pprint

class SIPMessage():
    def __init__(self, message: str):
        self.message = message
        self.headers = {}
        self.body = ""
        self.first_line = ''
        self.msg_type = self.get_message_type_first_line()
        self.parse()


    def parse(self):
        message = self.message.split("\n")
        splited_message = self.message.split("\n")
        splited_message = [line.strip() for line in splited_message]

        if "" in splited_message:
            header_part = splited_message[1:splited_message.index('')]
            body_part = splited_message[splited_message.index('')+1:]
        else:
            header_part = splited_message
            body_part = []

        self.headers = {line.split(": ")[0].strip(): line.split(": ")[1].strip() for line in header_part if ": " in line}
        self.body = {line.split("=")[0].strip(): line.split("=")[1].strip() for line in body_part if "=" in line}

    def get_message_type_first_line(self):
        first_line = self.message.split("\n")[0]
        result = first_line.split(" ")[0]
        self.first_line = self.message.split("\n")[0].strip().replace(f"{result} ", "") 
        return result


    def __str__(self):
        res = ''
        res += f"Message type: {self.msg_type}\n"
        for key, value in self.headers.items():
            res += f"HEADER= {key}: {value}\n"
        res += "--------------------------------------------------------------\n"

        for key, value in self.body.items():
            res += f"BODY: {key}: {value}\n"
        return res
    


inv_msg = """INVITE sip:john.doe@example.com SIP/2.0
Via: SIP/2.0/UDP 192.0.2.1:5060;branch=z9hG4bK776asdhds
Max-Forwards: 70
To: <sip:john.doe@example.com>
From: "Jane Doe" <sip:jane.doe@example.com>;tag=1928301774
Call-ID: a84b4c76e66710
CSeq: 314159 INVITE
Contact: <sip:jane.doe@192.0.2.1>
Content-Type: application/sdp
Content-Length: 151

v=0
o=jane.doe 2890844526 2890842807 IN IP4 192.0.2.1
s=-
c=IN IP4 192.0.2.1
t=0 0
m=audio 49170 RTP/AVP 0
a=rtpmap:0 PCMU/8000"""


sip_inv = SIPMessage(inv_msg)


print(sip_inv)
pprint(sip_inv.__dict__)



bye_msg = """BYE sip:jane.doe@192.0.2.1 SIP/2.0
Via: SIP/2.0/UDP 192.0.2.2:5060;branch=z9hG4bK776asdhds
Max-Forwards: 70
To: "Jane Doe" <sip:jane.doe@example.com>;tag=1928301774
From: "John Doe" <sip:john.doe@example.com>;tag=8371789120
Call-ID: a84b4c76e66710
CSeq: 231 BYE
Contact: <sip:john.doe@192.0.2.2>
Content-Length: 0"""


sip_bye = SIPMessage(bye_msg)
print(sip_bye)
pprint(sip_bye.__dict__)