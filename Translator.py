"""Translator class: translates between SIP and Matrix events."""

from Matrix import MatrixEventType, MatrixMessage, MatrixPayloadCreator
from SIP import SIPException, SIPMessage, SIPMessageCreator, SIPMessageType


class TranslatorException(Exception):
    """Base class for Translator exceptions"""

    pass


def _normalize_sdp(sdp: str | None) -> str:
    """Normalize SDP for Matrix: CRLF/CR to LF only. Prevents clients from showing 'missed' instead of ringing."""
    if not sdp:
        return ""
    return sdp.replace("\r\n", "\n").replace("\r", "\n").strip()


class Translator:
    def __init__(self) -> None:
        self.sip_creator = SIPMessageCreator()
        self.matrix_creator = MatrixPayloadCreator()

    # we have those message that we need to translate

    # m.call.invite <-> INVITE
    # m.call.answer <-> 200 OK (for INVITE)
    # m.call.select_answer <-> ACK
    # m.call.hangup <-> BYE
    # this translator class will have public methods for translating each of those messages
    # wrapped in one method with argument: message,message_type

    def translate(self, message: SIPMessage | MatrixMessage) -> dict | SIPMessage:
        """Translate message from one type to another"""

        if isinstance(message, SIPMessage):
            match message.message_type:
                case SIPMessageType.INVITE:
                    # translate to m.call.invite
                    # with usage of the matrix_creator (message)
                    sdp = _normalize_sdp(message.body_str)
                    call_id = message.headers.get("Call-ID", None)

                    if call_id and sdp:
                        version = 0
                        payload = self.matrix_creator.create_invite_payload(
                            sdp, call_id, version
                        )

                        return payload

                    else:
                        raise SIPException("Invalid SIP message. Can't translate.")

                case SIPMessageType.ACK:
                    # translatest to matrix select_answer event
                    call_id = message.headers.get("Call-ID", None)
                    version = 0
                    sdp = _normalize_sdp(message.body_str)

                    if sdp and call_id:
                        payload = self.matrix_creator.create_select_answer_payload(
                            call_id, version, sdp
                        )
                        return payload

                    else:
                        raise SIPException("Invalid SIP message. Can't translate.")

                case SIPMessageType.BYE:
                    # translate to m.call.hangup
                    call_id = message.headers.get("Call-ID", None)
                    version = 0

                    if call_id:
                        payload = self.matrix_creator.create_hangup_payload(
                            call_id, version
                        )
                        return payload
                    else:
                        raise SIPException("Invalid SIP message. Can't translate.")

                case SIPMessageType.OK:
                    # translate to m.call.answer
                    call_id = message.headers.get("Call-ID", None)
                    version = 0
                    sdp = _normalize_sdp(message.body_str)

                    if sdp and call_id:
                        payload = self.matrix_creator.create_answer_payload(
                            sdp, call_id, version
                        )
                        return payload
                    else:
                        raise SIPException("Invalid SIP message. Can't translate.")

                case _:
                    raise TranslatorException(
                        f"Cannot translate SIP message type: {message.message_type}"
                    )

        elif isinstance(message, MatrixMessage):
            match message.message_type:
                case MatrixEventType.INVITE:
                    # translate to INVITE
                    sdp = message.json_data.get("content", {}).get("sdp", "")
                    call_id = message.json_data.get("content", {}).get("call_id", "")
                    from_header = message.json_data.get("content", {}).get("from", "")
                    to_header = message.json_data.get("content", {}).get("to", "")
                    cseq = 1  # we will start with cseq 1 for new calls
                    return self.sip_creator.create_invite(
                        from_header, to_header, call_id, cseq, sdp
                    )

                case MatrixEventType.SELECT_ANSWER:
                    # translate to ACK
                    call_id = message.json_data.get("content", {}).get("call_id", "")
                    from_header = message.json_data.get("content", {}).get("from", "")
                    to_header = message.json_data.get("content", {}).get("to", "")
                    cseq = 2  # ACK will have cseq 2
                    return self.sip_creator.create_ack(
                        from_header, to_header, call_id, cseq
                    )
                case MatrixEventType.HANGUP:
                    # translate to BYE
                    call_id = message.json_data.get("content", {}).get("call_id", "")
                    from_header = message.json_data.get("content", {}).get("from", "")
                    to_header = message.json_data.get("content", {}).get("to", "")

                    cseq = 3  # BYE will have cseq 3
                    return self.sip_creator.create_bye(
                        from_header, to_header, call_id, cseq
                    )
                case MatrixEventType.ANSWER:
                    # translate to 200 OK
                    sdp = message.json_data.get("content", {}).get("sdp", "")
                    call_id = message.json_data.get("content", {}).get("call_id", "")
                    from_header = message.json_data.get("content", {}).get("from", "")
                    to_header = message.json_data.get("content", {}).get("to", "")
                    cseq = 2  # 200 OK will have cseq 2
                    return self.sip_creator.create_ok(
                        from_header, to_header, call_id, cseq, sdp
                    )
                case _:
                    raise TranslatorException(
                        f"Cannot translate Matrix message type: {message.message_type}"
                    )
        else:
            raise TranslatorException("Invalid message type")
