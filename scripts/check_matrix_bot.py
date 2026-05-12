#!/usr/bin/env python3
# Author: Štefan Gajdošík <xgajdo30@stud.fit.vut.cz>
"""
Check Matrix bot: loads bot/.env, tries login.
Run from project root: poetry run python scripts/check_matrix_bot.py
Prints which env vars are set (without values).
"""

import asyncio
import os
import sys

# Project root: one level up from scripts/
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_root)

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment,misc]

if load_dotenv is not None:
    loaded = load_dotenv(os.path.join(_root, "bot", ".env"))
    print("bot/.env:", "loaded" if loaded else "not found")
else:
    print("python-dotenv not installed, using system env")

from bot.MatrixBot import MatrixBot

REQUIRED = ("MATRIX_BOT_USERNAME", "MATRIX_BOT_PASSWORD", "MATRIX_BOT_HOMESERVER")
for name in REQUIRED:
    val = os.environ.get(name, "")
    ok = "OK" if val else "MISSING"
    print(f"  {name}: {ok}")
missing = [n for n in REQUIRED if not os.environ.get(n)]
if missing:
    print("Missing:", missing)
    sys.exit(1)

print("Creating MatrixBot and logging in...")
try:
    bot = MatrixBot()
except Exception as e:
    print("MatrixBot() failed:", e)
    sys.exit(1)


async def try_connect():
    try:
        ok = await bot.connect_to_server()
        if ok:
            print("connect_to_server() OK, user_id:", getattr(bot, "user_id", "?"))
            return True
        return False
    except Exception as e:
        print("connect_to_server() failed:", type(e).__name__, e)
        return False


if asyncio.run(try_connect()):
    print("Bot ready.")
else:
    sys.exit(1)
