class BridgeError(Exception):
    pass


class ConversationNotFoundError(BridgeError):
    pass


class MessageNotFoundError(BridgeError):
    pass


class InvalidMessageError(BridgeError):
    pass
