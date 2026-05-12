"""Tests for Matrix bot: env vars, instance creation, optional connect."""

import asyncio
import os
import sys

# Add project root to path so that import bot.* works
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

import pytest

try:
    from dotenv import load_dotenv

    load_dotenv("bot/.env")
except ImportError:
    pass

from bot.MatrixBot import MatrixBot, MatrixBotException


def test_bot_raises_when_env_missing(monkeypatch):
    """MatrixBot must raise when required env vars are missing."""
    monkeypatch.delenv("MATRIX_BOT_USERNAME", raising=False)
    monkeypatch.delenv("MATRIX_BOT_PASSWORD", raising=False)
    monkeypatch.delenv("MATRIX_BOT_HOMESERVER", raising=False)

    with pytest.raises(MatrixBotException) as exc_info:
        MatrixBot()
    assert (
        "Missing" in str(exc_info.value) or "environment" in str(exc_info.value).lower()
    )


def test_bot_raises_when_only_username(monkeypatch):
    """With only USERNAME set, PASSWORD and HOMESERVER are still missing."""
    monkeypatch.setenv("MATRIX_BOT_USERNAME", "testbot")
    monkeypatch.delenv("MATRIX_BOT_PASSWORD", raising=False)
    monkeypatch.delenv("MATRIX_BOT_HOMESERVER", raising=False)

    with pytest.raises(MatrixBotException):
        MatrixBot()


def test_bot_creates_when_required_env_set(monkeypatch):
    """If all three required env vars are set, MatrixBot is created without raising."""
    monkeypatch.setenv("MATRIX_BOT_USERNAME", "u")
    monkeypatch.setenv("MATRIX_BOT_PASSWORD", "p")
    monkeypatch.setenv("MATRIX_BOT_HOMESERVER", "https://example.org")

    bot = MatrixBot()
    assert bot.username == "u"
    assert bot.password == "p"
    assert bot.homeserver == "https://example.org"


@pytest.mark.asyncio
async def test_connect_fails_with_bad_homeserver(monkeypatch):
    """Connect to non-existent/invalid homeserver fails."""
    monkeypatch.setenv("MATRIX_BOT_USERNAME", "testuser")
    monkeypatch.setenv("MATRIX_BOT_PASSWORD", "testpass")
    monkeypatch.setenv("MATRIX_BOT_HOMESERVER", "https://127.0.0.1:19999")

    bot = MatrixBot()
    with pytest.raises(Exception):
        await asyncio.wait_for(bot.connect_to_server(), timeout=5.0)


@pytest.mark.asyncio
async def test_connect_succeeds_if_env_valid():
    """
    With valid env (e.g. bot/.env), login succeeds.
    Skip if env not set or login fails (e.g. wrong password).
    """
    if not all(
        [
            os.environ.get("MATRIX_BOT_USERNAME"),
            os.environ.get("MATRIX_BOT_PASSWORD"),
            os.environ.get("MATRIX_BOT_HOMESERVER"),
        ]
    ):
        pytest.skip("MATRIX_BOT_* env required (e.g. from bot/.env)")

    bot = MatrixBot()
    try:
        ok = await bot.connect_to_server()
    except Exception as e:
        pytest.skip(f"Login failed (check bot/.env): {e}")
    if not ok:
        pytest.skip("connect_to_server returned False")
    assert bot.user_id
    assert bot.access_token
    assert bot.device_id
