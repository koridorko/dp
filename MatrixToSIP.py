from .Matrix import MatrixMessage

class MatrixToSIPInitializer():
    def __init__(self) -> None:
        pass

    def translateMessage(self, message: MatrixMessage, matrix_message_type: str):
        match matrix_message_type:
            case "m.call.invite":
                return self.translate_invite(message)
            case "m.call.answer":
                return self.translate_answer(message)
            case "m.call.hangup":
                return self.translate_hangup(message)
            case _:
                print("Invalid message type")
                exit(22)

    def translate_invite(self, message: MatrixMessage):
        pass

    def translate_answer(self, message: MatrixMessage):
        pass

    def translate_hangup(self, message: MatrixMessage):
        pass

