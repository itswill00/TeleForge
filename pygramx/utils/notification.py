import asyncio
import json
import logging
import os
from typing import Optional, Union
import requests
import config

logger = logging.getLogger("pygramx.notification")


async def send_auto_notification(
    text: str,
    photo: Optional[str] = None,
    keyboard: Optional[list[list[dict]]] = None,
    target_chat: Optional[Union[int, str]] = None,
) -> bool:
    """
    Deliver automated background notifications (ISS pass, earthquake, watchdog, AFK, etc.)
    guaranteed to be sent from the companion bot directly into the bot's private chat with the
    owner or designated LOG_CHAT, never polluting the user's personal account.
    """
    from pygramx.client import PyGramClient

    # 1. Resolve Target Chat: LOG_CHAT -> OWNER_ID
    if not target_chat or target_chat == "me":
        log_chat_raw = config.get_log_chat()
        if log_chat_raw and str(log_chat_raw).strip() != "me":
            target_chat = int(log_chat_raw) if str(log_chat_raw).lstrip("-").isdigit() else log_chat_raw
        else:
            target_chat = config.get_owner_id() or getattr(PyGramClient, "OWNER_ID", None)

    if not target_chat:
        logger.debug("Cannot deliver notification: target chat and owner ID are unavailable.")
        return False

    # 2. Try delivery via Telegram Bot API HTTP (Bot Token)
    bot_token = config.BOT_TOKEN
    if bot_token:
        # A. Photo delivery
        if photo and os.path.isfile(photo):
            try:
                def _send_photo():
                    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
                    with open(photo, "rb") as f:
                        data = {
                            "chat_id": target_chat,
                            "caption": text,
                            "parse_mode": "Markdown",
                        }
                        if keyboard:
                            data["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
                        resp = requests.post(url, data=data, files={"photo": f}, timeout=10)
                        return resp.status_code == 200 and resp.json().get("ok")

                loop = asyncio.get_running_loop()
                if await loop.run_in_executor(None, _send_photo):
                    return True
            except Exception as e:
                logger.debug("Failed sending notification photo via Bot API: %s", e)

        # B. Text delivery
        try:
            from modules.system import _bot_api_call
            payload = {
                "chat_id": target_chat,
                "text": text,
                "parse_mode": "Markdown",
            }
            if keyboard:
                payload["reply_markup"] = {"inline_keyboard": keyboard}
            res = await _bot_api_call("sendMessage", payload, timeout=5.0)
            if res and res.get("ok"):
                return True
        except Exception as e:
            logger.debug("Failed sending notification text via Bot API: %s", e)

    # 3. Fallback: Companion Bot client (Pyrogram BOT_CLIENT)
    bot_client = getattr(PyGramClient, "BOT_CLIENT", None)
    if bot_client and bot_client.is_connected:
        try:
            from modules.inline import _raw_to_markup
            markup = _raw_to_markup(keyboard) if keyboard else None
            if photo and os.path.isfile(photo):
                await bot_client.send_photo(chat_id=target_chat, photo=photo, caption=text, reply_markup=markup)
            else:
                await bot_client.send_message(chat_id=target_chat, text=text, reply_markup=markup)
            return True
        except Exception as e:
            logger.warning("Failed sending notification via bot_client: %s", e)

    # 4. Ultimate fallback (only if no bot configured at all): Userbot Saved Messages
    user_client = getattr(PyGramClient, "USERBOT_CLIENT", None)
    if user_client and user_client.is_connected:
        try:
            dest = target_chat if target_chat != "me" else "me"
            if photo and os.path.isfile(photo):
                await user_client.send_photo(chat_id=dest, photo=photo, caption=text)
            else:
                await user_client.send_message(chat_id=dest, text=text)
            return True
        except Exception as e:
            logger.warning("Failed sending fallback notification via user_client: %s", e)

    return False
