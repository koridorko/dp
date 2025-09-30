"""Sorce for Translator class, which will translate between sip and mattrix events"""


from enum import Enum
from SIP import SIPMessage, SIPMessageType, SIPMessageCreator
from Matrix import MatrixMessage, MatrixEventType

class TranslatorException(Exception):
    """Base class for Translator exceptions"""
    pass

class Translator():
    def __init__(self) -> None:
        self.sip_creator = SIPMessageCreator()

    # we have those message that we need to translate 
    
    # m.call.invite <-> INVITE
    # m.call.answer <-> 200 OK (for INVITE)
    # m.call.select_answer <-> ACK
    # m.call.hangup <-> BYE
    # this translator class will have public methods for translating each of those messages
    # wrapped in one method with argument: message,message_type
    
    def translate(self, message: SIPMessage | MatrixMessage) -> MatrixMessage | SIPMessage:
        """Translate message from one type to another"""
        
        if isinstance(message, SIPMessage):
            match message.message_type:
                case SIPMessageType.INVITE:
                    # translate to m.call.invite
                    json_data = {
                        "type": MatrixEventType.INVITE.value,
                        "content": {
                            "sdp": message.body_str,
                            "version": 0,
                            "call_id": message.headers.get("Call-ID", ""),
                            "from": message.headers.get("From", ""),
                            "to": message.headers.get("To", ""),
                        }
                    }
                    return MatrixMessage(json_data)
                
                case SIPMessageType.ACK:
                    # translate to m.call.select_answer
                    json_data = {
                        "type": MatrixEventType.SELECT_ANSWER.value,
                        "content": {
                            "call_id": message.headers.get("Call-ID", ""),
                        }
                    }
                    return MatrixMessage(json_data)
                
                case SIPMessageType.BYE:
                    # translate to m.call.hangup
                    json_data = {
                        "type": MatrixEventType.HANGUP.value,
                        "content": {
                            "call_id": message.headers.get("Call-ID", ""),
                        }
                    }
                    return MatrixMessage(json_data)
                
                case SIPMessageType.OK:
                    # translate to m.call.answer
                    json_data = {
                        "type": MatrixEventType.ANSWER.value,
                        "content": {
                            "sdp": message.body_str,
                            "version": 0,
                            "call_id": message.headers.get("Call-ID", ""),
                            "from": message.headers.get("From", ""),
                            "to": message.headers.get("To", ""),
                        }
                    }
                    return MatrixMessage(json_data)
                case _:
                    raise TranslatorException(f"Cannot translate SIP message type: {message.message_type}")

        elif isinstance(message, MatrixMessage):
            match message.message_type:
                case MatrixEventType.INVITE:
                    # translate to INVITE
                    sdp = message.json_data.get("content", {}).get("sdp", "")
                    call_id = message.json_data.get("content", {}).get("call_id", "")
                    from_header = message.json_data.get("content", {}).get("from", "")
                    to_header = message.json_data.get("content", {}).get("to", "")
                    cseq = 1  # we will start with cseq 1 for new calls
                    return self.sip_creator.create_invite(from_header,
                                                        to_header,
                                                        call_id, 
                                                        cseq, 
                                                        sdp)        
        
                case MatrixEventType.SELECT_ANSWER:
                    # translate to ACK
                    call_id = message.json_data.get("content", {}).get("call_id", "")
                    from_header = message.json_data.get("content", {}).get("from", "")
                    to_header = message.json_data.get("content", {}).get("to", "")
                    cseq = 2  # ACK will have cseq 2
                    return self.sip_creator.create_ack(from_header,
                                                    to_header,
                                                    call_id,
                                                    cseq)
                case MatrixEventType.HANGUP:
                    # translate to BYE
                    call_id = message.json_data.get("content", {}).get("call_id", "")
                    from_header = message.json_data.get("content", {}).get("from", "")
                    to_header = message.json_data.get("content", {}).get("to", "")

                    cseq = 3  # BYE will have cseq 3
                    return self.sip_creator.create_bye(from_header,
                                                    to_header,
                                                    call_id,
                                                    cseq)
                case MatrixEventType.ANSWER:
                    # translate to 200 OK
                    sdp = message.json_data.get("content", {}).get("sdp", "")
                    call_id = message.json_data.get("content", {}).get("call_id", "")
                    from_header = message.json_data.get("content", {}).get("from", "")
                    to_header = message.json_data.get("content", {}).get("to", "")
                    cseq = 2  # 200 OK will have cseq 2
                    return self.sip_creator.create_ok(from_header,
                                                    to_header,
                                                    call_id,
                                                    cseq,
                                                    sdp)
                case _:
                    raise TranslatorException(f"Cannot translate Matrix message type: {message.message_type}")
        else:
            raise TranslatorException("Invalid message type")