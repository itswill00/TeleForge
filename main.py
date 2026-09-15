#!/usr/bin/env python3
import asyncio

# Ensure active event loop exists for Python 3.12+ / 3.14+ compatibility
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

import logging
import sys
import time
from pyrogram.errors import Unauthorized, ApiIdInvalid
import config
from pygramx import PyGramClient

logging.basicConfig(
    level=config.get_log_level(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pygramx")

def main():
    config.validate()

    reconnect_delay = 5

    while True:
        clients = []

        app = PyGramClient(
            name="pygramx",
            session_string=config.SESSION_STRING,
            api_id=config.get_api_id(),
            api_hash=config.API_HASH,
            plugins=dict(root="modules"),
            workdir=".",
        )
        clients.append(app)

        if config.BOT_TOKEN:
            bot = PyGramClient(
                name="pygramx_bot",
                api_id=config.get_api_id(),
                api_hash=config.API_HASH,
                bot_token=config.BOT_TOKEN,
                plugins=dict(root="modules"),
                workdir=".",
            )
            clients.append(bot)

        mode_desc = "userbot + companion bot" if len(clients) > 1 else "userbot"
        logger.info("Starting TeleForge (%s)...", mode_desc)

        try:
            if len(clients) == 1:
                app.run()
            else:
                from pyrogram import compose
                compose(clients)
            break
        except (KeyboardInterrupt, SystemExit):
            logger.info("TeleForge stopped by user signal.")
            break
        except (Unauthorized, ApiIdInvalid) as e:
            logger.critical("Fatal authentication error: %s. Stopping TeleForge.", e)
            sys.exit(1)
        except Exception as e:
            logger.warning(
                "Connection interrupted: %s. Reconnecting in %d seconds...",
                e,
                reconnect_delay,
            )
            time.sleep(reconnect_delay)
            # Recreate event loop for clean recovery
            asyncio.set_event_loop(asyncio.new_event_loop())

if __name__ == "__main__":
    main()
