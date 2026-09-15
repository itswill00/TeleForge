"""
TeleForge core package
"""
import asyncio

# Ensure active event loop exists for Python 3.12+ / 3.14+ compatibility with Pyrogram sync wrapper
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# Patch Pyrogram 2.0.x 32-bit channel ID limitation to support modern 64-bit channel IDs
try:
    import pyrogram.utils
    pyrogram.utils.MIN_CHANNEL_ID = -1000000000000000000
    pyrogram.utils.MAX_USER_ID = 1000000000000000000
except Exception:
    pass

from pygramx.client import PyGramClient, AltClient
from pygramx.decorators import on_cmd
from pygramx.database import LocalDB, db

__all__ = ["PyGramClient", "AltClient", "on_cmd", "LocalDB", "db"]
