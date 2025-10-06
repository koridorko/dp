"""File with implementation of Matrix Bot Class"""

import asyncio
import os
import sys

from nio import AsyncClient, LoginResponse


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
        self.client: AsyncClient
        # will be added later
        self.device_id: str

        if not all(
            [
                # we need just those 3 for initial login
                # user_id could be created but also obtained from login
                # device_id is obtained from login same as access_token
                self.username,
                self.password,
                self.homeserver,
            ]
        ):
            raise MatrixBotException(
                "Missing one or more required environment variables for Matrix Bot"
            )

    async def connect_to_server(self) -> bool:
        """method to connect to the matrix server"""
        # self client will be latter used for sending and receiving messages
        self.client = AsyncClient(self.homeserver, self.username)
        resp = await self.client.login(self.password)
        if isinstance(resp, LoginResponse):
            user_id = resp.user_id
            acces_token = resp.access_token
            device_id = resp.device_id

            if not device_id or not acces_token or not user_id:
                raise MatrixBotException(
                    "The login response did not contain all needed data!"
                )
            else:
                self.device_id = device_id
                self.user_id = user_id
                self.access_token = acces_token
                # we will update client with this data
                self.client_after_login_update()
                return True
        else:
            print("This is big big error")
            sys.stdout.flush()
            raise MatrixBotException

    def client_after_login_update(self) -> None:
        """This is a method that sets up needed things for sending latter
        requests to the Matrix server"""
        self.client.access_token = self.access_token
        self.client.user_id = self.user_id
        self.client.device_id = self.device_id


async def initialize_bot() -> MatrixBot:
    """Test function to check bot initialization"""
    bot = MatrixBot()
    initialization_succesfull = await bot.connect_to_server()
    print(initialization_succesfull)
    if not initialization_succesfull:
        raise MatrixBotException(
            "Could not connect to server specified in MATRIX_BOT_HOMESERVER env variable!"
        )
    return bot


async def start():
    global bot
    bot = await initialize_bot()

    # NOTE: will need to find way to allways do this :)
    #       maybe with finally somewhere sensible :)
    await bot.client.close()


# for now just for debugging purposes
asyncio.run(start())
