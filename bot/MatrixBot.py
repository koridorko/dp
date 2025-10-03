"""File with implementation of Matrix Bot Class"""

import os
import sys

from nio import AsyncClient, SyncResponse


class MatrixBotException(Exception):
    """Base class for Matrix Bot exceptions"""

    pass


class MatrixBot:
    """Class representing a Matrix bot"""

    def __init__(self) -> None:
        # initializing bot with env variables
        # NOTE:
        # using empty string to ensure it is not None type and don't have to
        # duplicate all the checks after __init__ function
        self.username = os.environ.get("MATRIX_BOT_USERNAME", "")
        self.password = os.environ.get("MATRIX_BOT_PASSWORD", "")
        self.homeserver = os.environ.get("MATRIX_BOT_HOMESERVER", "")
        self.access_token = os.environ.get("MATRIX_BOT_ACCESS_TOKEN", "")
        self.user_id = os.environ.get("MATRIX_BOT_USER_ID", "")
        self.client: AsyncClient | None = None

        if not all(
            [
                self.username,
                self.password,
                self.homeserver,
                self.access_token,
                self.user_id,
            ]
        ):
            raise MatrixBotException(
                "Missing one or more required environment variables for Matrix Bot"
            )

    async def connect_to_server(self) -> bool:
        """method to connect to the matrix server"""
        # self client will be latter used for sending and receiving messages
        self.client = AsyncClient(self.homeserver, self.user_id)
        self.client.access_token = self.access_token
        response = await self.client.sync()
        if not isinstance(response, SyncResponse):
            return False
        print("Connected to Matrix server successfully", file=sys.stderr)
        return True


def initialize_bot() -> MatrixBot:
    """Test function to check bot initialization"""
    bot = MatrixBot()
    if not bot.connect_to_server():
        raise MatrixBotException(
            "Could not connect to server specified in MATRIX_BOT_HOMESERVER env variable!"
        )
    return bot


# NOTE: usage bude cca nieco ako:
#       clovek si zapne (bude to v kontajneroch)
#       SIP server a Matrix bota
#       (oba si bude musiet "nakonfigurovat")
#       teda:
#           pre bota si bude musiet zohnat credentials a upravit env
#           toho bota bude vyuzivat tak ze ked bude chciet volat SIP -> Matrix
#       a tiez bude mat  nakonfigurovany sip server, ktory bude pouzity na volanie Matrix -> SIP
#       a bude to vlastne fungovat tak ze matrix acc -> matrix bot -> SIP server -> Iny sip user


if __name__ == "__main__":
    bot = initialize_bot()
