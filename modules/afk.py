import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import logging
import time
from typing import Dict, List, Tuple
from pyrogram import Client, filters
from pyrogram.types import Message
import config
from pygramx import on_cmd, db
from pygramx.client import PyGramClient
from pygramx.utils import format_uptime, edit_or_reply

logger = logging.getLogger("pygramx.afk")

# In-memory cooldown to avoid spamming the same chat: {chat_id: timestamp}
_COOLDOWN_MAP: Dict[int, float] = {}
_COOLDOWN_SECONDS: float = 60.0

# Track sent AFK response messages for automatic cleanup upon return: [(chat_id, message_id)]
_AFK_SENT_MSGS: List[Tuple[int, int]] = []


async def _log_afk_event(text: str):
    """Deliver an AFK status update from the companion bot to log chat or owner."""
    from pygramx.utils import send_auto_notification
    await send_auto_notification(text)


@on_cmd("afk", desc="Set away status with an optional note and media reply", usage="[note | reply to media]")
async def afk_toggle(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    now = time.time()

    reply = message.reply_to_message
    media_type = None
    file_id = None

    if reply:
        if reply.sticker:
            media_type = "sticker"
            file_id = reply.sticker.file_id
        elif reply.photo:
            media_type = "photo"
            file_id = reply.photo.file_id
        elif reply.animation:
            media_type = "animation"
            file_id = reply.animation.file_id
        elif reply.video:
            media_type = "video"
            file_id = reply.video.file_id
        elif reply.document:
            media_type = "document"
            file_id = reply.document.file_id

    if len(args) > 1:
        reason = args[1].strip()
    elif reply and (reply.text or reply.caption):
        reason = (reply.text or reply.caption).strip()
    else:
        reason = "Currently away."

    data = {
        "is_afk": True,
        "reason": reason,
        "time": now,
        "media_type": media_type,
        "file_id": file_id,
    }
    db.set("AFK_DATA", data)
    _COOLDOWN_MAP.clear()
    _AFK_SENT_MSGS.clear()

    media_note = f"\n• **Media:** `{media_type}`" if media_type else ""
    await edit_or_reply(
        message,
        f"**Status: Away (AFK)**\n• **Note:** {reason}{media_note}",
    )

    asyncio.create_task(_log_afk_event(
        f"**User Status: Away (AFK)**\n"
        f"• **Note:** {reason}{media_note}\n"
        f"• **Active:** `Yes`"
    ))


@Client.on_message(filters.me, group=-1)
async def afk_unsetter(client: Client, message: Message):
    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )
    if is_bot:
        return

    afk_data = db.get("AFK_DATA")
    if not afk_data or not afk_data.get("is_afk"):
        return

    # Ignore command triggers so running bot tools does not deactivate AFK
    text = (message.text or message.caption or "").strip()
    user_trig = config.get_trigger()
    bot_trig = config.get_bot_trigger()
    prefixes = tuple({user_trig, bot_trig, "/", "!", "."})
    if any(text.startswith(p) for p in prefixes):
        return

    start_time = afk_data.get("time", time.time())
    elapsed = time.time() - start_time

    # Ignore accidental immediate messages within 5 seconds of setting AFK
    if elapsed < 5.0:
        return

    db.delete("AFK_DATA")
    _COOLDOWN_MAP.clear()

    dur_str = format_uptime(elapsed)
    ret_msg = None
    try:
        ret_msg = await message.reply_text(
            f"**Returned Online**\n• **Time Away:** `{dur_str}`"
        )
    except Exception:
        pass

    # Clean up all AFK response bubbles sent across chats during absence
    msgs_to_clean = list(_AFK_SENT_MSGS)
    _AFK_SENT_MSGS.clear()

    async def _clean_afk_bubbles():
        for c_id, m_id in msgs_to_clean:
            try:
                await client.delete_messages(c_id, m_id)
                await asyncio.sleep(0.08)
            except Exception:
                pass

    asyncio.create_task(_clean_afk_bubbles())

    # Auto-delete "Returned Online" notification after 12 seconds for group cleanliness
    if ret_msg:
        async def _auto_delete_returned(msg: Message, delay: float = 12.0):
            await asyncio.sleep(delay)
            try:
                await msg.delete()
            except Exception:
                pass

        asyncio.create_task(_auto_delete_returned(ret_msg, 12.0))

    asyncio.create_task(_log_afk_event(
        f"**User Status: Returned Online**\n"
        f"• **Time Away:** `{dur_str}`\n"
        f"• **Responded Mentions Cleaned:** `{len(msgs_to_clean)}`"
    ))


@Client.on_message(~filters.me & (filters.private | filters.mentioned), group=-2)
async def afk_responder(client: Client, message: Message):
    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )
    if is_bot:
        return

    # Never spam the central LOG_CHAT with AFK replies
    log_chat = config.get_log_chat()
    if log_chat and str(message.chat.id) == str(log_chat):
        return

    # Ignore service messages, self, and other bots to prevent spam loops
    if message.service or not message.from_user or getattr(message.from_user, "is_self", False) or getattr(message.from_user, "is_bot", False):
        return

    afk_data = db.get("AFK_DATA")
    if not afk_data or not afk_data.get("is_afk"):
        return

    # Harmonize with PMPermit: Never double-respond to unapproved DM senders
    if message.chat.type.name == "PRIVATE":
        try:
            from modules.pmpermit import is_pmpermit_enabled, get_approved_users
            if is_pmpermit_enabled() and message.from_user.id not in get_approved_users():
                return
        except Exception:
            pass

    chat_id = message.chat.id
    now = time.time()

    # Apply 60s cooldown per chat to prevent flood
    if chat_id in _COOLDOWN_MAP and (now - _COOLDOWN_MAP[chat_id]) < _COOLDOWN_SECONDS:
        return

    _COOLDOWN_MAP[chat_id] = now
    start_time = afk_data.get("time", now)
    elapsed = now - start_time
    dur_str = format_uptime(elapsed)
    reason = afk_data.get("reason", "Currently away.")
    media_type = afk_data.get("media_type")
    file_id = afk_data.get("file_id")

    caption = (
        f"**Currently Away**\n"
        f"• **Note:** {reason}\n"
        f"• **Duration:** `{dur_str}`"
    )

    sent = None
    try:
        if media_type == "sticker" and file_id:
            try:
                s1 = await message.reply_sticker(file_id)
                if s1:
                    _AFK_SENT_MSGS.append((chat_id, s1.id))
            except Exception:
                pass
            sent = await message.reply_text(caption)
        elif media_type == "photo" and file_id:
            sent = await message.reply_photo(file_id, caption=caption)
        elif media_type == "animation" and file_id:
            sent = await message.reply_animation(file_id, caption=caption)
        elif media_type == "video" and file_id:
            sent = await message.reply_video(file_id, caption=caption)
        elif media_type == "document" and file_id:
            sent = await message.reply_document(file_id, caption=caption)
        else:
            sent = await message.reply_text(caption)
    except Exception as e:
        logger.debug("Failed to send AFK media reply, falling back to text: %s", e)
        try:
            sent = await message.reply_text(caption)
        except Exception:
            pass

    if sent:
        _AFK_SENT_MSGS.append((chat_id, sent.id))
        if len(_AFK_SENT_MSGS) > 60:
            _AFK_SENT_MSGS.pop(0)
