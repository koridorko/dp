
from .SIP import SIPMessage

class SIPtoMatrixInitializer():
    ### This is for now just simple skelet of the class for translating SIP messages to Matrix messages
    ### The class will be implemented in the future
    ### maybe not every message type will be implemented, but the most important ones will be
    def __init__(self):
        pass

    def translateMessage(self, message: SIPMessage):
        match message.msg_type:
            case "INVITE":
                return self.translate_invite(message)
            case "ACK":
                return self.translate_ack(message)
            case "BYE":
                return self.translate_bye(message)
            case "CANCEL":
                return self.translate_cancel(message)
            case "REGISTER":
                return self.translate_register(message)
            case "OPTIONS":
                return self.translate_options(message)
            case "INFO":
                return self.translate_info(message)
            case "UPDATE":
                return self.translate_update(message)
            case "RINGING":
                return self.translate_ringing(message)
            case "OK":
                return self.translate_ok(message)
            case "NOT_FOUND":
                return self.translate_not_found(message)
            case "BUSY":
                return self.translate_busy(message)
            case "UNAUTHORIZED":
                return self.translate_unauthorized(message)
            case "FORBIDDEN":
                return self.translate_forbidden(message)
            case "INTERNAL_SERVER_ERROR":
                return self.translate_internal_server_error(message)
            case _:
                print("Invalid message type")
                exit(22)

    def translate_invite(self, message: SIPMessage):
        pass

    def translate_ack(self, message: SIPMessage):
        pass

    def translate_bye(self, message: SIPMessage):
        pass

    def translate_cancel(self, message: SIPMessage):
        pass

    def translate_register(self, message: SIPMessage):
        pass

    def translate_options(self, message: SIPMessage):
        pass

    def translate_info(self, message: SIPMessage):
        pass

    def translate_update(self, message: SIPMessage):
        pass

    def translate_ringing(self, message: SIPMessage):
        pass

    def translate_ok(self, message: SIPMessage):
        pass

    def translate_not_found(self, message: SIPMessage):
        pass

    def translate_busy(self, message: SIPMessage):
        pass

    def translate_unauthorized(self, message: SIPMessage):
        pass

    def translate_forbidden(self, message: SIPMessage):
        pass

    def translate_internal_server_error(self, message: SIPMessage):
        pass

