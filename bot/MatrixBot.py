"""File with implementation of Matrix Bot Class"""

import os


from nio import AsyncClient, SyncResponse


class MatrixBotException(Exception):
    """Base class for Matrix Bot exceptions"""

    pass


class MatrixBot:
    """Class representing a Matrix bot"""

    def __init__(self) -> None:
        # initializing bot with env variables
        # HACK:
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

        # try:
        #     self.connect_to_server()
        # except Exception as e:
        #     raise MatrixBotException(f"Failed to connect to Matrix server: {e}")

    async def connect_to_server(self) -> None:
        """method to connect to the matrix server"""
        # self client will be latter used for sending and receiving messages

        self.client = AsyncClient(self.homeserver, self.user_id)
        self.client.access_token = self.access_token
        response = await self.client.sync()
        if not isinstance(response, SyncResponse):
            raise MatrixBotException("Failed to sync with the Matrix server")

        print("Connected to Matrix server successfully")


def test_bot_initialization():
    """Test function to check bot initialization"""
    try:
        bot = MatrixBot()
        print(bot)
        print("Matrix Bot initialized successfully")
    except MatrixBotException as e:
        print(f"Matrix Bot initialization failed: {e}")


if __name__ == "__main__":
    test_bot_initialization()
