import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import logging
import os
import time
from typing import Dict, List, Optional
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message
import config
from pygramx import on_cmd, db
from pygramx.client import PyGramClient
from pygramx.utils import edit_or_reply, get_client_prefix

logger = logging.getLogger("pygramx.pmpermit")

# In-memory warning tracker & flood prevention
_WARN_MAP: Dict[int, int] = {}
_LAST_WARN_TIME: Dict[int, float] = {}
_LAST_WARN_MSG_ID: Dict[int, int] = {}
_LOG_NOTIF_MAP: Dict[int, int] = {}

MAX_WARNS: int = 4
WARN_COOLDOWN_SECONDS: float = 10.0
OFFICIAL_TELEGRAM_IDS = {777000, 42777}


def is_pmpermit_enabled() -> bool:
    return bool(db.get("PMPERMIT_ENABLED", False))


def get_approved_users() -> List[int]:
    return db.get("APPROVED_USERS", [])


def add_approved_user(user_id: int) -> None:
    approved = get_approved_users()
    if user_id not in approved:
        approved.append(user_id)
        db.set("APPROVED_USERS", approved)


def remove_approved_user(user_id: int) -> None:
    approved = get_approved_users()
    if user_id in approved:
        approved.remove(user_id)
        db.set("APPROVED_USERS", approved)


async def notify_log_channel(user, warns: int):
    """Deliver real-time unapproved DM notification to log chat with interactive buttons."""
    log_chat = config.get_log_chat() or config.get_owner_id()
    if not log_chat:
        return

    name = user.first_name or "Unknown"
    uname = f"@{user.username}" if user.username else f"`{user.id}`"
    text = (
        "**Unauthorized Direct Message**\n"
        f"• **User:** {name} ({uname})\n"
        f"• **ID:** `{user.id}`\n"
        f"• **Warning:** `{warns}/{MAX_WARNS}`\n"
        "• **Action Required:** Choose authorization status below."
    )
    raw_buttons = [
        [
            {"text": "« Approve PM »", "callback_data": f"pm:appr:{user.id}", "style": "success"},
            {"text": "« Block PM »", "callback_data": f"pm:blck:{user.id}", "style": "danger"},
        ]
    ]

    from modules.system import _bot_api_call
    from modules.inline import _raw_to_markup

    old_log_msg_id = _LOG_NOTIF_MAP.get(user.id)
    if old_log_msg_id:
        res = await _bot_api_call("editMessageText", {
            "chat_id": log_chat,
            "message_id": old_log_msg_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": raw_buttons},
        })
        if res and res.get("ok"):
            return

        bot_client = getattr(PyGramClient, "BOT_CLIENT", None)
        if bot_client:
            try:
                markup = _raw_to_markup(raw_buttons)
                await bot_client.edit_message_text(log_chat, old_log_msg_id, text, reply_markup=markup)
                return
            except Exception:
                pass

    res = await _bot_api_call("sendMessage", {
        "chat_id": log_chat,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": raw_buttons},
    })
    if res and res.get("ok") and res.get("result", {}).get("message_id"):
        _LOG_NOTIF_MAP[user.id] = res["result"]["message_id"]
        if len(_LOG_NOTIF_MAP) > 100:
            _LOG_NOTIF_MAP.pop(next(iter(_LOG_NOTIF_MAP)), None)
        return

    bot_client = getattr(PyGramClient, "BOT_CLIENT", None)
    if bot_client:
        try:
            markup = _raw_to_markup(raw_buttons)
            msg = await bot_client.send_message(log_chat, text, reply_markup=markup)
            if msg:
                _LOG_NOTIF_MAP[user.id] = msg.id
                if len(_LOG_NOTIF_MAP) > 100:
                    _LOG_NOTIF_MAP.pop(next(iter(_LOG_NOTIF_MAP)), None)
        except Exception as e:
            logger.debug("Failed to send PM log alert: %s", e)


async def _update_callback_message(query: CallbackQuery, text: str, raw_buttons: list[list[dict]]):
    from modules.system import _bot_api_call
    from modules.inline import _raw_to_markup

    msg = query.message
    if msg and config.BOT_TOKEN:
        res = await _bot_api_call("editMessageText", {
            "chat_id": msg.chat.id,
            "message_id": msg.id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": raw_buttons},
        })
        if res and res.get("ok"):
            return

    markup = _raw_to_markup(raw_buttons)
    try:
        if msg:
            await msg.edit_text(text, reply_markup=markup)
        elif query.inline_message_id:
            await query.edit_message_text(text, reply_markup=markup)
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^pm:"))
async def pmpermit_callbacks(client: Client, query: CallbackQuery):
    owner_id = getattr(PyGramClient, "OWNER_ID", None) or config.get_owner_id()
    if not query.from_user or not owner_id or query.from_user.id != owner_id:
        await query.answer("Unauthorized: owner only.", show_alert=True)
        return

    data = query.data or ""
    parts = data.split(":")
    if len(parts) < 3:
        return await query.answer("Invalid request.")

    action = parts[1]
    try:
        user_id = int(parts[2])
    except ValueError:
        return await query.answer("Invalid user ID.")

    user_client = getattr(PyGramClient, "USERBOT_CLIENT", None)

    if action == "appr":
        add_approved_user(user_id)
        _WARN_MAP.pop(user_id, None)
        _LAST_WARN_TIME.pop(user_id, None)
        old_warn_msg_id = _LAST_WARN_MSG_ID.pop(user_id, None)
        if user_client:
            if old_warn_msg_id:
                try:
                    await user_client.delete_messages(user_id, old_warn_msg_id)
                except Exception:
                    pass
            try:
                await user_client.unblock_user(user_id)
            except Exception:
                pass

        new_text = (
            "**Direct Message Authorized**\n"
            f"• **User:** `{user_id}`\n"
            "• **Status:** `Approved via Log Chat`"
        )
        new_buttons = [
            [
                {"text": "« Disapprove »", "callback_data": f"pm:disappr:{user_id}", "style": "danger"},
                {"text": "« Block »", "callback_data": f"pm:blck:{user_id}", "style": "danger"},
            ]
        ]
        await _update_callback_message(query, new_text, new_buttons)
        await query.answer("User approved.")
        return

    elif action == "blck":
        remove_approved_user(user_id)
        _WARN_MAP.pop(user_id, None)
        _LAST_WARN_TIME.pop(user_id, None)
        old_warn_msg_id = _LAST_WARN_MSG_ID.pop(user_id, None)
        if user_client:
            if old_warn_msg_id:
                try:
                    await user_client.delete_messages(user_id, old_warn_msg_id)
                except Exception:
                    pass
            try:
                await user_client.block_user(user_id)
            except Exception:
                pass

        new_text = (
            "**Direct Message Blocked**\n"
            f"• **User:** `{user_id}`\n"
            "• **Status:** `Blocked via Log Chat`"
        )
        new_buttons = [
            [
                {"text": "« Unblock »", "callback_data": f"pm:unblck:{user_id}", "style": "primary"},
            ]
        ]
        await _update_callback_message(query, new_text, new_buttons)
        await query.answer("User blocked.")
        return

    elif action == "disappr":
        remove_approved_user(user_id)
        _WARN_MAP.pop(user_id, None)
        _LAST_WARN_TIME.pop(user_id, None)
        new_text = (
            "**Direct Message Revoked**\n"
            f"• **User:** `{user_id}`\n"
            "• **Status:** `Authorization revoked`"
        )
        new_buttons = [
            [
                {"text": "« Re-Approve »", "callback_data": f"pm:appr:{user_id}", "style": "success"},
                {"text": "« Block »", "callback_data": f"pm:blck:{user_id}", "style": "danger"},
            ]
        ]
        await _update_callback_message(query, new_text, new_buttons)
        await query.answer("Authorization revoked.")
        return

    elif action == "unblck":
        if user_client:
            try:
                await user_client.unblock_user(user_id)
            except Exception:
                pass
        new_text = (
            "**Direct Message Unblocked**\n"
            f"• **User:** `{user_id}`\n"
            "• **Status:** `Unblocked, pending authorization`"
        )
        new_buttons = [
            [
                {"text": "« Approve PM »", "callback_data": f"pm:appr:{user_id}", "style": "success"},
                {"text": "« Block »", "callback_data": f"pm:blck:{user_id}", "style": "danger"},
            ]
        ]
        await _update_callback_message(query, new_text, new_buttons)
        await query.answer("User unblocked.")
        return


@on_cmd("pmpermit", desc="Configure private message protection", usage="[on/off]")
async def pmpermit_toggle(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    curr_prefix = get_client_prefix(client)
    if len(args) < 2:
        status = "Enabled" if is_pmpermit_enabled() else "Disabled"
        await edit_or_reply(message, f"**Private Message Protection:** `{status}`\nUsage: `{curr_prefix}pmpermit on` or `{curr_prefix}pmpermit off`")
        return

    mode = args[1].lower().strip()
    if mode in ["on", "enable", "true"]:
        db.set("PMPERMIT_ENABLED", True)
        await edit_or_reply(message, "**Private Message Protection:** `Enabled`. Direct messages from unrecognized users require authorization.")
    elif mode in ["off", "disable", "false"]:
        db.set("PMPERMIT_ENABLED", False)
        _WARN_MAP.clear()
        _LAST_WARN_TIME.clear()
        _LAST_WARN_MSG_ID.clear()
        await edit_or_reply(message, "**Private Message Protection:** `Disabled`. Direct messages are open to all users.")
    else:
        await edit_or_reply(message, f"`Usage: {curr_prefix}pmpermit on|off`")


@on_cmd(["a", "approve"], desc="Authorize a user to send direct messages", usage="[@username/id/reply]")
async def approve_cmd(client: Client, message: Message):
    user_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
    elif message.chat.type.name == "PRIVATE":
        user_id = message.chat.id
    else:
        text = message.text or message.caption or ""
        args = text.split(maxsplit=1)
        if len(args) > 1:
            raw_target = args[1].strip()
            target_query = int(raw_target) if raw_target.lstrip("-").isdigit() else raw_target
            try:
                target = await client.get_users(target_query)
                user_id = target.id
            except Exception:
                await edit_or_reply(message, f"Could not find specified user `{raw_target}`.")
                return

    if not user_id:
        await edit_or_reply(message, "`Please specify a user (@username/id) or reply to their message.`")
        return

    add_approved_user(user_id)
    _WARN_MAP.pop(user_id, None)
    _LAST_WARN_TIME.pop(user_id, None)
    old_warn = _LAST_WARN_MSG_ID.pop(user_id, None)
    if old_warn:
        try:
            await client.delete_messages(user_id, old_warn)
        except Exception:
            pass

    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )
    if not is_bot:
        try:
            await client.unblock_user(user_id)
        except Exception:
            pass

    await edit_or_reply(message, f"Direct messages authorized for user `{user_id}`.")


@on_cmd(["da", "disapprove"], desc="Revoke direct message authorization and block user", usage="[@username/id/reply]")
async def disapprove_cmd(client: Client, message: Message):
    user_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
    elif message.chat.type.name == "PRIVATE":
        user_id = message.chat.id
    else:
        text = message.text or message.caption or ""
        args = text.split(maxsplit=1)
        if len(args) > 1:
            raw_target = args[1].strip()
            target_query = int(raw_target) if raw_target.lstrip("-").isdigit() else raw_target
            try:
                target = await client.get_users(target_query)
                user_id = target.id
            except Exception:
                await edit_or_reply(message, f"Could not find specified user `{raw_target}`.")
                return

    if not user_id:
        await edit_or_reply(message, "`Please specify a user (@username/id) or reply to their message.`")
        return

    remove_approved_user(user_id)
    _WARN_MAP.pop(user_id, None)
    _LAST_WARN_TIME.pop(user_id, None)
    old_warn = _LAST_WARN_MSG_ID.pop(user_id, None)
    if old_warn:
        try:
            await client.delete_messages(user_id, old_warn)
        except Exception:
            pass

    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )
    if not is_bot:
        try:
            await client.block_user(user_id)
        except Exception:
            pass

    await edit_or_reply(message, f"Authorization revoked for user `{user_id}`. User has been blocked.")


@on_cmd("block", desc="Block a user from sending direct messages", usage="[@username/id/reply]")
async def block_cmd(client: Client, message: Message):
    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )
    if is_bot:
        return await edit_or_reply(message, "`This command can only be executed by userbot.`")

    user_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
    elif message.chat.type.name == "PRIVATE":
        user_id = message.chat.id
    else:
        text = message.text or message.caption or ""
        args = text.split(maxsplit=1)
        if len(args) > 1:
            raw_target = args[1].strip()
            target_query = int(raw_target) if raw_target.lstrip("-").isdigit() else raw_target
            try:
                target = await client.get_users(target_query)
                user_id = target.id
            except Exception:
                await edit_or_reply(message, f"Could not find specified user `{raw_target}`.")
                return

    if not user_id:
        await edit_or_reply(message, "`Please specify a user (@username/id) or reply to their message.`")
        return

    remove_approved_user(user_id)
    _WARN_MAP.pop(user_id, None)
    _LAST_WARN_TIME.pop(user_id, None)
    old_warn = _LAST_WARN_MSG_ID.pop(user_id, None)
    if old_warn:
        try:
            await client.delete_messages(user_id, old_warn)
        except Exception:
            pass

    try:
        await client.block_user(user_id)
        await edit_or_reply(message, f"User `{user_id}` has been blocked.")
    except Exception as e:
        await edit_or_reply(message, f"Failed to block user `{user_id}`: {e}")


@on_cmd("unblock", desc="Unblock a user or all blocked users", usage="[@username/id/reply/all]")
async def unblock_cmd(client: Client, message: Message):
    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )
    if is_bot:
        return await edit_or_reply(message, "`This command can only be executed by userbot.`")

    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    target_arg = args[1].strip().lower() if len(args) > 1 else ""

    if target_arg == "all":
        status_msg = await edit_or_reply(message, "Fetching blocked contacts list...")
        try:
            from pyrogram.raw.functions.contacts import GetBlocked

            count = 0
            limit = 100
            offset = 0
            while True:
                blocked = await client.invoke(GetBlocked(offset=offset, limit=limit))
                if not blocked.users:
                    break
                for peer_user in blocked.users:
                    try:
                        await client.unblock_user(peer_user.id)
                        count += 1
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass
                if len(blocked.users) < limit:
                    break
                offset += len(blocked.users)

            await edit_or_reply(status_msg, f"Unblocked `{count}` users successfully.")
        except Exception as e:
            await edit_or_reply(status_msg, f"Error unblocking users: {e}")
        return

    user_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
    elif target_arg:
        target_query = int(target_arg) if target_arg.lstrip("-").isdigit() else target_arg
        try:
            target = await client.get_users(target_query)
            user_id = target.id
        except Exception:
            await edit_or_reply(message, f"Could not find specified user `{target_arg}`.")
            return
    elif message.chat.type.name == "PRIVATE":
        user_id = message.chat.id

    if not user_id:
        await edit_or_reply(message, "`Please specify a user (@username/id), reply to their message, or use 'all'.`")
        return

    try:
        await client.unblock_user(user_id)
        await edit_or_reply(message, f"User `{user_id}` has been unblocked.")
    except Exception as e:
        await edit_or_reply(message, f"Failed to unblock user `{user_id}`: {e}")


@on_cmd(["listapproved", "listappr", "lista"], desc="List all users authorized to send direct messages")
async def list_approved_cmd(client: Client, message: Message):
    approved = get_approved_users()
    if not approved:
        return await edit_or_reply(message, "**Authorized Users:** `None`.\nNo users are currently permitted to PM.")

    status_msg = await edit_or_reply(message, f"Fetching details for `{len(approved)}` authorized user(s)...")

    lines = []
    for idx, uid in enumerate(approved, 1):
        try:
            u = await client.get_users(uid)
            name = u.first_name or "Unknown"
            uname = f"@{u.username}" if u.username else "No username"
            lines.append(f"{idx}. {name} ({uname}) — `{uid}`")
        except Exception:
            lines.append(f"{idx}. User `{uid}`")

    if len(lines) <= 25:
        text = f"**Authorized Users ({len(approved)}):**\n" + "\n".join(lines)
        return await edit_or_reply(status_msg, text)

    file_path = "approved_users.txt"
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"TeleForge Approved Users ({len(approved)})\n")
            f.write("=" * 40 + "\n\n")
            f.write("\n".join(lines) + "\n")

        await client.send_document(
            chat_id=message.chat.id,
            document=file_path,
            caption=f"**TeleForge Authorized PM List**\n• Total: `{len(approved)}` user(s)",
            reply_to_message_id=message.id,
        )
        await status_msg.delete()
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


@Client.on_message(filters.private & ~filters.me & ~filters.bot & ~filters.service, group=1)
async def pmpermit_gatekeeper(client: Client, message: Message):
    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )
    if is_bot:
        return

    if not is_pmpermit_enabled():
        return

    user = message.from_user
    if not user or user.is_contact or user.is_self:
        return

    owner_id = (getattr(client, "me", None) and getattr(client.me, "id", None)) or config.get_owner_id()
    if owner_id and user.id == owner_id:
        return

    # Whitelist official Telegram accounts, verified accounts, and support
    if (
        user.id in OFFICIAL_TELEGRAM_IDS
        or getattr(user, "is_support", False)
        or getattr(user, "is_verified", False)
    ):
        return

    if user.id in get_approved_users():
        return

    # Auto-delete any media sent by unauthorized users
    if message.media:
        try:
            await message.delete()
        except Exception:
            pass

    # Count every unauthorized attempt towards warning limit
    warns = _WARN_MAP.get(user.id, 0) + 1
    _WARN_MAP[user.id] = warns

    # Notify log channel with interactive Approve / Block buttons
    asyncio.create_task(notify_log_channel(user, warns))

    if warns >= MAX_WARNS:
        old_warn = _LAST_WARN_MSG_ID.pop(user.id, None)
        if old_warn:
            try:
                await client.delete_messages(user.id, old_warn)
            except Exception:
                pass

        try:
            await message.reply_text(
                "You have exceeded the maximum number of attempts without receiving authorization. This conversation has been blocked automatically."
            )
        except Exception:
            pass
        try:
            await client.block_user(user.id)
        except Exception:
            pass
        _WARN_MAP.pop(user.id, None)
        _LAST_WARN_TIME.pop(user.id, None)
        return

    # Throttle warning message replies to prevent bot FloodWait
    now = time.time()
    last_warn = _LAST_WARN_TIME.get(user.id, 0.0)
    if (now - last_warn) < WARN_COOLDOWN_SECONDS:
        return

    _LAST_WARN_TIME[user.id] = now

    # Delete previous warning bubble before sending the new one
    old_warn = _LAST_WARN_MSG_ID.pop(user.id, None)
    if old_warn:
        try:
            await client.delete_messages(user.id, old_warn)
        except Exception:
            pass

    warning_text = (
        f"Hello. I am currently unavailable.\n"
        f"Please state your purpose clearly and wait for my response before sending further messages.\n"
        f"Notice: Exceeding repeated attempts will result in an automatic block. (Warning {warns}/{MAX_WARNS})"
    )
    try:
        sent = await message.reply_text(warning_text)
        if sent:
            _LAST_WARN_MSG_ID[user.id] = sent.id
            if len(_LAST_WARN_MSG_ID) > 100:
                _LAST_WARN_MSG_ID.pop(next(iter(_LAST_WARN_MSG_ID)), None)
    except Exception:
        pass


@Client.on_message(filters.private & (filters.me | filters.outgoing), group=2)
async def pmpermit_outgoing(client: Client, message: Message):
    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )
    if is_bot or not is_pmpermit_enabled():
        return

    # Do not auto-approve on command execution
    text = (message.text or message.caption or "").strip()
    user_trig = config.get_trigger()
    bot_trig = config.get_bot_trigger()
    prefixes = tuple({user_trig, bot_trig, "/", "!", "."})
    if any(text.startswith(p) for p in prefixes):
        return

    chat_id = message.chat.id
    if chat_id not in get_approved_users():
        add_approved_user(chat_id)
        _WARN_MAP.pop(chat_id, None)
        _LAST_WARN_TIME.pop(chat_id, None)
        old_warn = _LAST_WARN_MSG_ID.pop(chat_id, None)
        if old_warn:
            try:
                await client.delete_messages(chat_id, old_warn)
            except Exception:
                pass
