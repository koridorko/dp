"""File with implementation of Matrix Bot Class"""

import asyncio
import os

from nio import (
    AsyncClient,
    LoginResponse,
    RoomCreateError,
    RoomInviteError,
    RoomSendError,
)


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
            msg = getattr(resp, "message", str(resp))
            raise MatrixBotException(
                f"Login failed (server returned non-LoginResponse): {msg}"
            )

    def client_after_login_update(self) -> None:
        """This is a method that sets up needed things for sending latter
        requests to the Matrix server"""
        self.client.access_token = self.access_token
        self.client.user_id = self.user_id
        self.client.device_id = self.device_id

    async def find_room_with_only_user(self, matrix_user_id: str) -> str | None:
        """Return a room_id where the only members are this bot and matrix_user_id, or None.
        Call after a sync so client.rooms is up to date."""
        rooms = getattr(self.client, "rooms", None) or {}
        for room_id, room in rooms.items():
            users = getattr(room, "users", None) or {}
            if len(users) != 2:
                continue
            if self.client.user_id in users and matrix_user_id in users:
                return room_id
        return None

    async def create_room_and_invite_user(self, matrix_user_id: str) -> str:
        """Create a room and invite the user. Returns room_id."""
        try:
            create_resp = await self.client.room_create(invite=[matrix_user_id])
        except TypeError:
            create_resp = await self.client.room_create()
        if isinstance(create_resp, RoomCreateError):
            raise MatrixBotException(f"room_create failed: {create_resp}")
        room_id = create_resp.room_id
        try:
            invite_resp = await self.client.room_invite(room_id, matrix_user_id)
            if isinstance(invite_resp, RoomInviteError):
                pass
        except Exception:
            pass
        return room_id

    async def send_call_invite(self, room_id: str, content: dict) -> None:
        """Send m.call.invite event to a room. content = event content dict."""
        resp = await self.client.room_send(
            room_id=room_id,
            message_type="m.call.invite",
            content=content,
        )
        if isinstance(resp, RoomSendError):
            raise MatrixBotException(f"room_send m.call.invite failed: {resp}")

    async def send_select_answer(self, room_id: str, content: dict) -> None:
        """Send m.call.select_answer to a room."""
        resp = await self.client.room_send(
            room_id=room_id,
            message_type="m.call.select_answer",
            content=content,
        )
        if isinstance(resp, RoomSendError):
            raise MatrixBotException(f"room_send m.call.select_answer failed: {resp}")

    async def send_hangup(self, room_id: str, content: dict) -> None:
        """Send m.call.hangup to a room."""
        resp = await self.client.room_send(
            room_id=room_id,
            message_type="m.call.hangup",
            content=content,
        )
        if isinstance(resp, RoomSendError):
            raise MatrixBotException(f"room_send m.call.hangup failed: {resp}")

    async def send_call_candidates(self, room_id: str, content: dict) -> None:
        """Send m.call.candidates to a room. content must have call_id, version, candidates list."""
        resp = await self.client.room_send(
            room_id=room_id,
            message_type="m.call.candidates",
            content=content,
        )
        if isinstance(resp, RoomSendError):
            raise MatrixBotException(f"room_send m.call.candidates failed: {resp}")


async def initialize_bot() -> MatrixBot:
    """Test function to check bot initialization"""
    bot = MatrixBot()
    initialization_succesfull = await bot.connect_to_server()
    print(initialization_succesfull)
    print(bot.__dict__)
    if not initialization_succesfull:
        raise MatrixBotException(
            "Could not connect to server specified in MATRIX_BOT_HOMESERVER env variable!"
        )
    return bot


async def start():
    global bot
    bot = await initialize_bot()
    await bot.client.close()


if __name__ == "__main__":
    asyncio.run(start())
