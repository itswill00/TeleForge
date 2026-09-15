import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from collections import deque
import ast
import importlib
import json
import logging
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import requests
import pyrogram
from pyrogram import Client, filters
from pyrogram.errors import MessageNotModified
from pyrogram.enums import ParseMode
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultPhoto,
    InputTextMessageContent,
)
import config
from pygramx import on_cmd, db
from pygramx.client import PyGramClient
from pygramx.utils import (
    format_uptime,
    format_latency,
    format_bytes,
    get_device_model,
    edit_or_reply,
    send_large_output,
    get_client_prefix,
    get_portable_home,
    find_power_supply_dir,
)

logger = logging.getLogger("pygramx.system")


REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

async def _get_profile_photo(client: Client) -> str | None:
    """
    Retrieve cached path of profile photo for the active client (bot or user).
    Returns path to downloaded image or None if no photo is configured.
    """
    try:
        user = await client.get_me()
        if not user or not user.photo:
            return None

        os.makedirs("downloads", exist_ok=True)
        photo_path = os.path.join(REPO_DIR, "downloads", f"pfp_{user.id}.jpg")

        # Cache valid for 30 minutes to avoid redundant downloads
        if os.path.isfile(photo_path) and (time.time() - os.path.getmtime(photo_path)) < 1800:
            return photo_path

        downloaded = await client.download_media(user.photo.big_file_id, file_name=photo_path)
        if downloaded and os.path.isfile(downloaded):
            return downloaded
    except Exception as e:
        logger.debug("Failed to retrieve profile photo: %s", e)
    return None

@on_cmd("ping", desc="Measure message round-trip edit latency")
async def ping_cmd(client: Client, message: Message):
    start = time.perf_counter()
    msg = await edit_or_reply(message, "`Pinging...`")
    latency = (time.perf_counter() - start) * 1000
    dc_id = getattr(client, "session", None) and getattr(client.session, "dc_id", None)
    dc_str = f" • **DC:** `{dc_id}`" if dc_id else ""
    await edit_or_reply(msg, f"**Pong:** `{format_latency(latency)}`{dc_str}")

@on_cmd("alive", desc="Display service uptime and current status")
async def alive_cmd(client: Client, message: Message):
    uptime_str = format_uptime(time.time() - PyGramClient.START_TIME)
    user = await client.get_me()
    first = user.first_name or ""
    last = f" {user.last_name}" if user.last_name else ""
    name = (first + last).strip() or "Unnamed"
    total_cmds = len(PyGramClient.COMMANDS)
    db_keys = len(db)

    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(user, "is_bot", False)
    )
    client_type = "Companion Bot" if is_bot else "Userbot"
    log_target = config.get_log_chat()
    log_str = f"`{log_target}`" if log_target else "`Not configured`"
    active_prefix = config.get_bot_trigger() if is_bot else config.get_trigger()

    py_ver = sys.version.split()[0]
    pyro_ver = getattr(pyrogram, "__version__", "2.0")
    rss_bytes = _get_process_rss()
    bot_ram_str = format_bytes(rss_bytes) if rss_bytes else "Unavailable"
    sys_uptime_str = _get_system_uptime()

    text = (
        f"**TeleForge · {client_type}**\n"
        f"• **Status:** `Active`\n"
        f"• **Account:** {name} (`{user.id}`)\n"
        f"• **Headquarters:** {log_str}\n"
        f"• **Host:** `{get_device_model()}`\n"
        f"• **Uptime:** Bot: `{uptime_str}` | System: `{sys_uptime_str}`\n"
        f"• **RAM Usage:** `{bot_ram_str}`\n"
        f"• **Available Commands:** `{total_cmds}`\n"
        f"• **Stored Settings:** `{db_keys}` entries\n"
        f"• **Trigger:** `{active_prefix}`\n"
        f"• **Python:** `{py_ver}` • **Pyrogram:** `{pyro_ver}`"
    )

    custom_tpl = db.get("CUSTOM_ALIVE_TEXT")
    if custom_tpl:
        try:
            text = custom_tpl.format(
                name=name,
                id=user.id,
                device=get_device_model(),
                uptime=uptime_str,
                sys_uptime=sys_uptime_str,
                ram=bot_ram_str,
                commands=total_cmds,
                trigger=active_prefix,
                python=py_ver,
                pyrogram=pyro_ver,
                status="Active",
            )
        except Exception:
            pass

    custom_pic = db.get("ALIVE_PIC")
    photo = custom_pic or await _get_profile_photo(client)
    if photo:
        try:
            if getattr(message, "outgoing", False) or (
                message.from_user and getattr(message.from_user, "is_self", False)
            ):
                await message.delete()
            return await client.send_photo(
                chat_id=message.chat.id,
                photo=photo,
                caption=text,
                reply_to_message_id=message.id if not getattr(message, "outgoing", False) else None,
            )
        except Exception as e:
            logger.debug("Failed to deliver alive photo: %s", e)

    await edit_or_reply(message, text)


@on_cmd(["setalive", "alivemsg"], desc="Set custom alive status template with dynamic variables", usage="<template | reply>")
async def setalive_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    prefix = get_client_prefix(client)

    tpl = args[1].strip() if len(args) > 1 else (reply.text or reply.caption if reply else "")
    if not tpl:
        vars_hint = "{name}, {id}, {device}, {uptime}, {ram}, {commands}, {trigger}"
        return await edit_or_reply(
            message,
            f"**Usage:** `{prefix}setalive <template text>`\n"
            f"• Available variables: `{vars_hint}`\n"
            f"• Example: `{prefix}setalive TeleForge active on {device} | Uptime: {uptime}`"
        )

    db.set("CUSTOM_ALIVE_TEXT", tpl)
    await edit_or_reply(message, f"**Custom Alive Template Saved:**\n`{tpl}`")


@on_cmd("resetalive", desc="Reset alive status message to default")
async def resetalive_cmd(client: Client, message: Message):
    db.delete("CUSTOM_ALIVE_TEXT")
    await edit_or_reply(message, "Custom alive template has been reset to default.")


@on_cmd(["setalivepic", "alivepic"], desc="Set custom photo for alive status", usage="[url | reply to photo | reset]")
async def alivepic_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    target = args[1].strip() if len(args) > 1 else ""

    if target.lower() == "reset":
        db.delete("ALIVE_PIC")
        return await edit_or_reply(message, "Alive photo reset to default profile photo.")

    if not target and reply and reply.photo:
        target = reply.photo.file_id
        db.set("ALIVE_PIC", target)
        return await edit_or_reply(message, "Alive photo updated from replied photo.")

    if target.startswith("http"):
        db.set("ALIVE_PIC", target)
        return await edit_or_reply(message, f"Alive photo updated:\n`{target}`")

    await edit_or_reply(message, "Usage: reply to a photo with `.alivepic` or provide an image URL.")


CATEGORY_INFO = {
    "admin": {
        "title": "Admin & Moderation",
        "short": "Admin",
        "symbol": "•",
        "desc": "Group moderation, mentions, and cleanups",
    },
    "android": {
        "title": "Android & Kernel",
        "short": "Android",
        "symbol": "•",
        "desc": "Boot image, kernel banner, super partition, and hardware sensors",
    },
    "media": {
        "title": "Media & Audio",
        "short": "Media",
        "symbol": "•",
        "desc": "Circular video notes, voice conversion, and TTS",
    },
    "security": {
        "title": "Security & Sessions",
        "short": "Security",
        "symbol": "•",
        "desc": "PM protection, authorization, and active session audit",
    },
    "stickers": {
        "title": "Stickers",
        "short": "Stickers",
        "symbol": "•",
        "desc": "Sticker kanging and custom pack management",
    },
    "system": {
        "title": "System & Core",
        "short": "System",
        "symbol": "•",
        "desc": "Bot status, ping, logs, trigger settings, and process control",
    },
    "tools": {
        "title": "Tools & Utilities",
        "short": "Tools",
        "symbol": "•",
        "desc": "Shell, eval, database variables, user/chat info, and AFK",
    },
    "transfer": {
        "title": "Transfer & Cloud",
        "short": "Transfer",
        "symbol": "•",
        "desc": "Local file upload/download, Catbox, Litterbox, and self-destruct",
    },
    "sport": {
        "title": "Motorsport & Racing",
        "short": "Sport",
        "symbol": "•",
        "desc": "Grand Prix schedules and sessions for F1, WEC, WRC, and MotoGP",
    },
    "astronomy": {
        "title": "Astronomy & Space",
        "short": "Astronomy",
        "symbol": "•",
        "desc": "NASA APOD, Moon phase, ISS live tracking, and celestial events",
    },
    "aviation": {
        "title": "Aviation & ADS-B Radar",
        "short": "Aviation",
        "symbol": "•",
        "desc": "Live overhead aircraft tracking, flight search, and transponder radar",
    },
    "seismo": {
        "title": "Earthquake & Seismology",
        "short": "Seismo",
        "symbol": "•",
        "desc": "BMKG real-time earthquake monitoring, shakemaps, and tsunami alerts",
    },
}

CATEGORY_ALIASES = {
    "admin": "admin",
    "group": "admin",
    "moderation": "admin",
    "purge": "admin",
    "android": "android",
    "kernel": "android",
    "rom": "android",
    "media": "media",
    "audio": "media",
    "video": "media",
    "voice": "media",
    "tts": "media",
    "security": "security",
    "session": "security",
    "sessions": "security",
    "pm": "security",
    "pmpermit": "security",
    "stickers": "stickers",
    "sticker": "stickers",
    "system": "system",
    "core": "system",
    "tools": "tools",
    "tool": "tools",
    "util": "tools",
    "utils": "tools",
    "utility": "tools",
    "network": "tools",
    "net": "tools",
    "sockets": "tools",
    "scan": "tools",
    "portscan": "tools",
    "transfer": "transfer",
    "upload": "transfer",
    "uploader": "transfer",
    "cloud": "transfer",
    "sport": "sport",
    "sports": "sport",
    "racing": "sport",
    "motorsport": "sport",
    "f1": "sport",
    "astronomy": "astronomy",
    "astro": "astronomy",
    "space": "astronomy",
    "aviation": "aviation",
    "radar": "aviation",
    "planes": "aviation",
    "flight": "aviation",
    "seismo": "seismo",
    "gempa": "seismo",
    "earthquake": "seismo",
    "quake": "seismo",
}

_REST_COOLDOWN_UNTIL = 0.0
REST_COOLDOWN_SECONDS = 5  # short backoff on network failure

_BOT_API_SESSION: requests.Session | None = None

def _get_bot_api_session() -> requests.Session:
    global _BOT_API_SESSION
    if _BOT_API_SESSION is None:
        _BOT_API_SESSION = requests.Session()
    return _BOT_API_SESSION

async def _bot_api_call(method: str, payload: dict, timeout: float = 5.0) -> dict | None:
    """Execute raw Telegram Bot API HTTP call for features unsupported by Pyrogram TL layer."""
    global _REST_COOLDOWN_UNTIL
    if time.time() < _REST_COOLDOWN_UNTIL:
        return None  # REST recently failed: take fast MTProto path immediately
    token = config.BOT_TOKEN
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        def _fetch():
            sess = _get_bot_api_session()
            resp = sess.post(url, json=payload, timeout=timeout)
            return resp.json()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _fetch)
    except Exception as e:
        logger.debug("Bot API %s call error: %s", method, e)
        _REST_COOLDOWN_UNTIL = time.time() + REST_COOLDOWN_SECONDS
        return None

async def _bot_api_send_photo(chat_id: int, photo_path: str, caption: str, keyboard: list[list[dict]], reply_to_message_id: int | None = None) -> bool:
    """Send photo with pre-styled buttons in a single HTTP request to eliminate render latency."""
    token = config.BOT_TOKEN
    if not token or not os.path.isfile(photo_path):
        return False
    global _REST_COOLDOWN_UNTIL
    if time.time() < _REST_COOLDOWN_UNTIL:
        return False
    def _send():
        with open(photo_path, "rb") as f:
            data = {
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": "Markdown",
                "reply_markup": json.dumps({"inline_keyboard": keyboard}),
            }
            if reply_to_message_id:
                data["reply_to_message_id"] = reply_to_message_id
            resp = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto", data=data, files={"photo": f}, timeout=5)
            return resp.status_code == 200 and resp.json().get("ok")
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _send)
    except Exception as e:
        logger.debug("Failed bot api sendPhoto: %s", e)
        _REST_COOLDOWN_UNTIL = time.time() + REST_COOLDOWN_SECONDS
        return False

async def _bot_api_send_message(chat_id: int, text: str, keyboard: list[list[dict]], reply_to_message_id: int | None = None) -> bool:
    """Send text with pre-styled buttons in a single HTTP request to eliminate render latency."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": keyboard},
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    res = await _bot_api_call("sendMessage", payload)
    return bool(res and res.get("ok"))

def _raw_to_markup(rows: list[list[dict]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(btn["text"], callback_data=btn.get("callback_data")) for btn in r]
        for r in rows
    ])

def _build_help_home_content() -> tuple[str, list[list[dict]]]:
    primary_cmds = {k: v for k, v in PyGramClient.COMMANDS.items() if not v.get("is_alias")}
    grouped: dict[str, dict[str, dict]] = {}
    for cmd_name, meta in primary_cmds.items():
        cat = meta.get("category", "general")
        grouped.setdefault(cat, {})[cmd_name] = meta

    defined_order = [
        "admin",
        "android",
        "media",
        "security",
        "stickers",
        "system",
        "tools",
        "transfer",
        "sport",
        "astronomy",
        "aviation",
        "seismo",
    ]
    all_cats = [c for c in defined_order if c in grouped] + sorted(c for c in grouped if c not in defined_order)

    bot_prefix = config.get_bot_trigger()
    text = (
        f"**TeleForge Bot Menu**\n"
        f"• **Host:** `{get_device_model()}`\n"
        f"• **Active Commands:** `{len(primary_cmds)}` across `{len(grouped)}` categories\n"
        f"• **Prefix:** `{bot_prefix}`\n\n"
        f"Select a category below to explore available commands:"
    )

    buttons = []
    row = []
    for cat in all_cats:
        c_data = CATEGORY_INFO.get(cat, {})
        short = c_data.get("short", cat.title())
        row.append({"text": f"« {short} »", "callback_data": f"help_cat:{cat}", "style": "primary"})
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([{"text": "« Close Menu »", "callback_data": "help_close", "style": "danger"}])

    return text, buttons

def _build_help_cat_content(cat: str) -> tuple[str, list[list[dict]]]:
    primary_cmds = {k: v for k, v in PyGramClient.COMMANDS.items() if not v.get("is_alias")}
    cat_cmds = {k: v for k, v in primary_cmds.items() if v.get("category") == cat}
    c_data = CATEGORY_INFO.get(cat, {})
    title = c_data.get("title", cat.title())
    desc = c_data.get("desc", "")
    bot_prefix = config.get_bot_trigger()

    text = (
        f"**Category: {title}** ({len(cat_cmds)} commands)\n"
        f"• **Summary:** {desc}\n"
        f"• **Prefix:** `{bot_prefix}`\n\n"
        f"Select a command below to inspect syntax:"
    )

    buttons = []
    row = []
    for cmd_name in sorted(cat_cmds.keys()):
        row.append({"text": f"« {cmd_name} »", "callback_data": f"help_cmd:{cmd_name}:{cat}", "style": "primary"})
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        {"text": "« Back »", "callback_data": "help_home", "style": "success"},
        {"text": "« Close »", "callback_data": "help_close", "style": "danger"},
    ])

    return text, buttons

def _build_help_cmd_content(cmd_name: str, cat: str) -> tuple[str, list[list[dict]]]:
    bot_prefix = config.get_bot_trigger()
    meta = PyGramClient.COMMANDS.get(cmd_name, {})
    cat_key = meta.get("category", cat)
    c_data = CATEGORY_INFO.get(cat_key, {})
    cat_title = c_data.get("title", cat_key.title())

    if meta.get("is_alias"):
        primary = meta.get("primary")
        primary_meta = PyGramClient.COMMANDS.get(primary, {})
        desc = primary_meta.get("desc") or meta.get("desc") or "No description provided."
        usage = f" `{bot_prefix}{cmd_name} {primary_meta['usage']}`" if primary_meta.get("usage") else f" `{bot_prefix}{cmd_name}`"
        aliases = primary_meta.get("aliases", [])
    else:
        desc = meta.get("desc") or "No description provided."
        usage = f" `{bot_prefix}{cmd_name} {meta['usage']}`" if meta.get("usage") else f" `{bot_prefix}{cmd_name}`"
        aliases = meta.get("aliases", [])

    alias_line = f"\n• **Aliases:** {', '.join(f'`{bot_prefix}{a}`' for a in aliases)}" if aliases else ""

    text = (
        f"**Command:** `{bot_prefix}{cmd_name}`\n"
        f"• **Category:** {cat_title}\n"
        f"• **Description:** {desc}\n"
        f"• **Syntax:**{usage}"
        f"{alias_line}"
    )

    buttons = [
        [
            {"text": f"« Back to {c_data.get('short', cat_title)} »", "callback_data": f"help_cat:{cat_key}", "style": "success"},
            {"text": "« Home »", "callback_data": "help_home", "style": "primary"},
        ]
    ]

    return text, buttons

_HELP_MSG_TRACKER: list[tuple[int, int, float]] = []
_CLEANUP_TOKENS: dict[str, str] = {}

def _extract_msg_id(updates) -> int | None:
    if not updates:
        return None
    if hasattr(updates, "updates"):
        for u in updates.updates:
            msg = getattr(u, "message", None)
            if msg and getattr(msg, "id", None):
                return msg.id
            elif getattr(u, "id", None):
                return u.id
    elif hasattr(updates, "id"):
        return updates.id
    return None

def cancel_auto_clean(key: str) -> None:
    _CLEANUP_TOKENS.pop(key, None)

async def delete_tracked_message(chat_id: int | None = None, msg_id: int | None = None) -> bool:
    """Delete a tracked message via userbot client."""
    user_client = getattr(PyGramClient, "USERBOT_CLIENT", None)
    if not user_client:
        return False
    if chat_id and msg_id:
        try:
            await user_client.delete_messages(chat_id, msg_id)
            for i in range(len(_HELP_MSG_TRACKER) - 1, -1, -1):
                c_id, m_id, _ = _HELP_MSG_TRACKER[i]
                if c_id == chat_id and m_id == msg_id:
                    _HELP_MSG_TRACKER.pop(i)
                    break
            return True
        except Exception:
            pass
    now = time.time()
    for i in range(len(_HELP_MSG_TRACKER) - 1, -1, -1):
        c_id, m_id, ts = _HELP_MSG_TRACKER[i]
        if (chat_id is None or c_id == chat_id) and (now - ts) < 86400:
            try:
                await user_client.delete_messages(c_id, m_id)
                _HELP_MSG_TRACKER.pop(i)
                return True
            except Exception:
                pass
    return False

async def schedule_auto_clean(key: str, chat_id: int | None = None, msg_id: int | None = None, inline_id: str | None = None, delay: float = 60.0):
    token = uuid.uuid4().hex
    _CLEANUP_TOKENS[key] = token
    await asyncio.sleep(delay)
    if _CLEANUP_TOKENS.get(key) == token:
        _CLEANUP_TOKENS.pop(key, None)
        deleted = await delete_tracked_message(chat_id, msg_id)
        if not deleted and inline_id:
            try:
                bot_token = config.BOT_TOKEN
                if bot_token:
                    await _bot_api_call("editMessageCaption", {
                        "inline_message_id": inline_id,
                        "caption": "",
                        "reply_markup": {"inline_keyboard": []},
                    })
            except Exception:
                pass
            try:
                bot_token = config.BOT_TOKEN
                if bot_token:
                    await _bot_api_call("editMessageText", {
                        "inline_message_id": inline_id,
                        "text": "`Closed.`",
                        "reply_markup": {"inline_keyboard": []},
                    })
            except Exception:
                pass

@Client.on_callback_query(filters.regex(r"^help_"))
async def help_callback_query(client: Client, query: CallbackQuery):
    owner_id = getattr(PyGramClient, "OWNER_ID", None) or config.get_owner_id()
    if not query.from_user or not owner_id or query.from_user.id != owner_id:
        await query.answer("Unauthorized: Only the bot owner can navigate this menu.", show_alert=True)
        return

    data = query.data or ""
    msg = query.message
    inline_id = query.inline_message_id
    has_photo = bool(msg and msg.photo)

    async def _update_menu(content_text: str, raw_buttons: list[list[dict]]):
        if config.BOT_TOKEN and inline_id:
            res = await _bot_api_call("editMessageCaption", {
                "inline_message_id": inline_id,
                "caption": content_text,
                "parse_mode": "Markdown",
                "reply_markup": {"inline_keyboard": raw_buttons},
            })
            if not res or not res.get("ok"):
                await _bot_api_call("editMessageText", {
                    "inline_message_id": inline_id,
                    "text": content_text,
                    "parse_mode": "Markdown",
                    "reply_markup": {"inline_keyboard": raw_buttons},
                })
            return

        if config.BOT_TOKEN and msg:
            method = "editMessageCaption" if has_photo else "editMessageText"
            key = "caption" if has_photo else "text"
            res = await _bot_api_call(method, {
                "chat_id": msg.chat.id,
                "message_id": msg.id,
                key: content_text,
                "parse_mode": "Markdown",
                "reply_markup": {"inline_keyboard": raw_buttons},
            })
            if res and res.get("ok"):
                return

        # Fallback to Pyrogram
        markup = _raw_to_markup(raw_buttons)
        if inline_id:
            try:
                await client.edit_inline_caption(inline_message_id=inline_id, caption=content_text, reply_markup=markup)
                return
            except MessageNotModified:
                return
            except Exception:
                pass

            try:
                await client.edit_inline_text(inline_message_id=inline_id, text=content_text, reply_markup=markup)
                return
            except MessageNotModified:
                return
            except Exception as e:
                logger.debug("Failed to edit inline message: %s", e)
            return

        if has_photo:
            await query.edit_message_caption(caption=content_text, reply_markup=markup)
        else:
            await query.edit_message_text(text=content_text, reply_markup=markup)

    try:
        if data == "help_home":
            inline_id = query.inline_message_id
            msg = query.message
            key = inline_id or f"{msg.chat.id if msg else 0}:{msg.id if msg else 0}"
            cancel_auto_clean(key)
            text, raw_buttons = _build_help_home_content()
            await _update_menu(text, raw_buttons)
            await query.answer()

        elif data.startswith("help_cat:"):
            cat = data.split(":", 1)[1]
            text, raw_buttons = _build_help_cat_content(cat)
            await _update_menu(text, raw_buttons)
            await query.answer()

        elif data.startswith("help_cmd:"):
            parts = data.split(":")
            cmd_name = parts[1]
            cat = parts[2] if len(parts) > 2 else "general"
            text, raw_buttons = _build_help_cmd_content(cmd_name, cat)
            await _update_menu(text, raw_buttons)
            await query.answer()

        elif data == "help_close":
            await query.answer("Menu closed. Auto-deleting in 60s.")
            inline_id = query.inline_message_id
            msg = query.message
            chat_id = msg.chat.id if msg else None
            msg_id = msg.id if msg else None
            key = inline_id or f"{chat_id}:{msg_id}"

            closed_text = (
                "**TeleForge Help Catalog**\n"
                "• `Menu closed.`\n"
                "• Bubble will auto-delete in 60s for group cleanliness."
            )
            reopen_raw = [
                [
                    {"text": "« Open Again »", "callback_data": "help_home", "style": "success"},
                    {"text": "« Delete Now »", "callback_data": "help_purge", "style": "danger"},
                ]
            ]
            await _update_menu(closed_text, reopen_raw)
            asyncio.create_task(schedule_auto_clean(key, chat_id, msg_id, inline_id, delay=60.0))
            return

        elif data == "help_purge":
            inline_id = query.inline_message_id
            msg = query.message
            chat_id = msg.chat.id if msg else None
            msg_id = msg.id if msg else None
            key = inline_id or f"{chat_id}:{msg_id}"
            cancel_auto_clean(key)
            await query.answer("Deleted.")
            deleted = await delete_tracked_message(chat_id, msg_id)
            if not deleted and msg:
                try:
                    await msg.delete()
                    deleted = True
                except Exception:
                    pass
            if not deleted and inline_id:
                try:
                    bot_client = getattr(PyGramClient, "BOT_CLIENT", None)
                    if bot_client:
                        await bot_client.edit_inline_caption(inline_message_id=inline_id, caption="", reply_markup=None)
                except Exception:
                    try:
                        bot_client = getattr(PyGramClient, "BOT_CLIENT", None)
                        if bot_client:
                            await bot_client.edit_inline_text(inline_message_id=inline_id, text="`Deleted.`", reply_markup=None)
                    except Exception:
                        pass
            return

    except MessageNotModified:
        await query.answer()
    except Exception as e:
        logger.error("Help callback query error: %s", e)
        await query.answer(f"Error: {e}", show_alert=True)

_CACHED_PFP_URL: str | None = None
_PFP_FETCH_LOCK: bool = False
DEFAULT_HELP_PIC = "https://files.catbox.moe/esvjuc.jpg"

def _get_cached_photo_url() -> str:
    global _CACHED_PFP_URL
    custom = db.get("HELP_PIC") or getattr(config, "HELP_PIC", None)
    if custom and str(custom).strip().startswith("http"):
        return str(custom).strip()

    if _CACHED_PFP_URL and _CACHED_PFP_URL.startswith("http"):
        return _CACHED_PFP_URL

    db_url = db.get("CACHED_PFP_URL")
    if db_url and str(db_url).strip().startswith("http"):
        _CACHED_PFP_URL = str(db_url).strip()
        return _CACHED_PFP_URL

    return DEFAULT_HELP_PIC

async def _background_cache_pfp():
    global _CACHED_PFP_URL, _PFP_FETCH_LOCK
    if _PFP_FETCH_LOCK:
        return
    _PFP_FETCH_LOCK = True
    try:
        user_client = getattr(PyGramClient, "USERBOT_CLIENT", None)
        bot_client = getattr(PyGramClient, "BOT_CLIENT", None)
        target_client = user_client or bot_client
        if not target_client:
            return
        local_pfp = await _get_profile_photo(target_client)
        if not local_pfp and bot_client and bot_client != target_client:
            local_pfp = await _get_profile_photo(bot_client)
        if not local_pfp or not os.path.isfile(local_pfp):
            return

        def _upload():
            ua = "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36"
            fname = os.path.basename(local_pfp)
            try:
                with open(local_pfp, "rb") as f:
                    resp = requests.post(
                        "https://catbox.moe/user/api.php",
                        headers={"User-Agent": ua},
                        data={"reqtype": "fileupload"},
                        files={"fileToUpload": (fname, f)},
                        timeout=10,
                    )
                    if resp.status_code == 200 and resp.text.strip().startswith("https://"):
                        return resp.text.strip()
            except Exception:
                pass

            try:
                with open(local_pfp, "rb") as f:
                    resp = requests.post(
                        "https://litterbox.catbox.moe/resources/internals/api.php",
                        headers={"User-Agent": ua},
                        data={"reqtype": "fileupload", "time": "72h"},
                        files={"fileToUpload": (fname, f)},
                        timeout=10,
                    )
                    if resp.status_code == 200 and resp.text.strip().startswith("https://"):
                        return resp.text.strip()
            except Exception:
                pass
            return None

        loop = asyncio.get_running_loop()
        url = await loop.run_in_executor(None, _upload)
        if url:
            _CACHED_PFP_URL = url
            db.set("CACHED_PFP_URL", url)
    finally:
        _PFP_FETCH_LOCK = False

_WHISPERS: dict[str, dict] = {}

@Client.on_inline_query()
async def help_inline_query(client: Client, inline_query: InlineQuery):
    owner_id = getattr(PyGramClient, "OWNER_ID", None) or config.get_owner_id()
    q_text = (inline_query.query or "").strip()

    # Feature: Secret Whisper (@bot wspr @target <message>)
    if q_text.startswith("wspr"):
        parts = q_text.split(maxsplit=2)
        if len(parts) < 3:
            return await inline_query.answer([
                InlineQueryResultArticle(
                    id="wspr_usage",
                    title="🔒 Whisper: Incomplete Query",
                    description="Usage: @bot wspr @username <secret_message>",
                    input_message_content=InputTextMessageContent(
                        message_text="Usage: `@bot wspr @username <secret_message>`",
                    ),
                )
            ], cache_time=1, is_personal=True)

        target = parts[1].strip()
        secret_msg = parts[2].strip()
        wid = uuid.uuid4().hex[:8]

        now = time.time()
        whispers = db.get("WHISPERS", {})
        # Purge stale whispers > 24h
        whispers = {k: v for k, v in whispers.items() if now - v.get("ts", 0) <= 86400}
        whispers[wid] = {
            "from_id": inline_query.from_user.id if inline_query.from_user else 0,
            "target": target.lower(),
            "text": secret_msg,
            "ts": now,
        }
        db.set("WHISPERS", whispers)

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔒 Show Secret", callback_data=f"wspr:{wid}")]
        ])
        results = [
            InlineQueryResultArticle(
                id=f"wspr_{wid}",
                title=f"🔒 Secret Whisper to {target}",
                description=f"Only you and {target} can open this message",
                input_message_content=InputTextMessageContent(
                    message_text=f"🔒 **A secret whisper for** {target}\n_Only the author and recipient can open this message._",
                    parse_mode=ParseMode.MARKDOWN,
                ),
                reply_markup=markup,
            )
        ]
        return await inline_query.answer(results=results, cache_time=1, is_personal=True)

    if not inline_query.from_user or not owner_id or inline_query.from_user.id != owner_id:
        return await inline_query.answer([], cache_time=1)

    # Inline hub (modules/inline.py) owns these prefixes; skip to avoid double-answer
    try:
        from modules.inline import owns_query
        if owns_query(q_text):
            return
    except Exception:
        pass

    q_sub = ""
    if q_text.lower().startswith("help"):
        parts = q_text.split(maxsplit=1)
        if len(parts) > 1:
            q_sub = parts[1].strip().lower()

    if q_sub:
        matched_cat = None
        if q_sub in CATEGORY_INFO:
            matched_cat = q_sub
        elif q_sub in CATEGORY_ALIASES and CATEGORY_ALIASES[q_sub] in CATEGORY_INFO:
            matched_cat = CATEGORY_ALIASES[q_sub]

        if matched_cat:
            text, raw_buttons = _build_help_cat_content(matched_cat)
        elif q_sub in PyGramClient.COMMANDS:
            cat_key = PyGramClient.COMMANDS[q_sub].get("category", "general")
            text, raw_buttons = _build_help_cmd_content(q_sub, cat_key)
        else:
            text, raw_buttons = _build_help_home_content()
    else:
        text, raw_buttons = _build_help_home_content()

    photo_url = _get_cached_photo_url()

    if not photo_url:
        asyncio.create_task(_background_cache_pfp())

    if config.BOT_TOKEN:
        payload_result = {
            "type": "photo" if photo_url else "article",
            "id": "help_menu",
            "title": "TeleForge Help",
            "description": "Interactive command catalog",
            "reply_markup": {"inline_keyboard": raw_buttons},
        }
        if photo_url:
            payload_result["photo_url"] = photo_url
            payload_result["thumb_url"] = photo_url
            payload_result["caption"] = text
            payload_result["parse_mode"] = "Markdown"
        else:
            payload_result["input_message_content"] = {
                "message_text": text,
                "parse_mode": "Markdown",
            }

        res = await _bot_api_call("answerInlineQuery", {
            "inline_query_id": inline_query.id,
            "results": [payload_result],
            "cache_time": 1,
            "is_personal": True,
        }, timeout=2.5)
        if res and res.get("ok"):
            return

    markup = _raw_to_markup(raw_buttons)
    if photo_url:
        results = [
            InlineQueryResultPhoto(
                id="help_menu",
                title="TeleForge Interactive Help",
                description="Open interactive command catalog with inline buttons",
                photo_url=photo_url,
                thumb_url=photo_url,
                caption=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=markup,
            )
        ]
    else:
        results = [
            InlineQueryResultArticle(
                id="help_menu",
                title="TeleForge Interactive Help",
                description="Open interactive command catalog with inline buttons",
                input_message_content=InputTextMessageContent(
                    message_text=text,
                    parse_mode=ParseMode.MARKDOWN,
                ),
                reply_markup=markup,
            )
        ]
    await inline_query.answer(results=results, cache_time=1, is_personal=True)

@on_cmd(["helppic", "sethelppic"], desc="Set or reset custom help menu photo URL", usage="[url|reset]")
async def helppic_cmd(client: Client, message: Message):
    global _CACHED_PFP_URL
    prefix = get_client_prefix(client)
    args = (message.text or message.caption or "").split(maxsplit=1)
    if len(args) < 2:
        current = db.get("HELP_PIC")
        if current:
            return await edit_or_reply(message, f"Current custom help picture:\n`{current}`\n\nUse `{prefix}helppic reset` to use profile photo.")
        return await edit_or_reply(message, f"Using user profile photo by default.\nTo set custom URL: `{prefix}helppic <url>`")

    target = args[1].strip()
    if target.lower() in ("reset", "clear", "default"):
        db.delete("HELP_PIC")
        db.delete("CACHED_PFP_URL")
        _CACHED_PFP_URL = None
        return await edit_or_reply(message, "Help picture reset to default profile photo.")

    if not target.startswith("http"):
        return await edit_or_reply(message, "Please provide a valid image URL starting with `http://` or `https://`.")

    db.set("HELP_PIC", target)
    _CACHED_PFP_URL = target
    await edit_or_reply(message, f"Help picture updated:\n`{target}`")

async def _deliver_help_menu(
    client: Client,
    message: Message,
    text: str,
    raw_buttons: list[list[dict]],
    photo_path: str | None = None,
    query_str: str = "help",
):
    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )
    reply_to = message.id if not getattr(message, "outgoing", False) else None

    # Bot mode: deliver directly
    if is_bot:
        if photo_path and await _bot_api_send_photo(message.chat.id, photo_path, text, raw_buttons, reply_to):
            return
        if not photo_path and await _bot_api_send_message(message.chat.id, text, raw_buttons, reply_to):
            return
        markup = _raw_to_markup(raw_buttons)
        if photo_path:
            try:
                return await client.send_photo(
                    chat_id=message.chat.id,
                    photo=photo_path,
                    caption=text,
                    reply_markup=markup,
                    reply_to_message_id=reply_to,
                )
            except Exception as e:
                logger.debug("Failed to send bot help photo: %s", e)
        return await edit_or_reply(message, text, reply_markup=markup)

    # Userbot mode: deliver interactive inline buttons via companion bot ('via @bot')
    bot_client = getattr(PyGramClient, "BOT_CLIENT", None)
    bot_username = (
        (getattr(bot_client, "me", None) and bot_client.me.username)
        or db.get("BOT_USERNAME")
    )
    if not bot_username and config.BOT_TOKEN and bot_client:
        try:
            bme = await bot_client.get_me()
            bot_username = bme.username
            if bot_username:
                db.set("BOT_USERNAME", bot_username)
        except Exception:
            pass

    if bot_username:
        try:
            results = await client.get_inline_bot_results(bot_username, query_str)
            if results and results.results:
                updates = await client.send_inline_bot_result(
                    chat_id=message.chat.id,
                    query_id=results.query_id,
                    result_id=results.results[0].id,
                    reply_to_message_id=message.reply_to_message.id if message.reply_to_message else None,
                )
                msg_id = _extract_msg_id(updates)
                if msg_id:
                    _HELP_MSG_TRACKER.append((message.chat.id, msg_id, time.time()))
                    if len(_HELP_MSG_TRACKER) > 50:
                        _HELP_MSG_TRACKER.pop(0)
                await message.delete()
                return
        except Exception as e:
            logger.warning("Inline help via @%s skipped: %s", bot_username, e)

    # Fallback when inline query is unavailable: deliver via companion bot directly
    if bot_client:
        try:
            markup = _raw_to_markup(raw_buttons)
            if photo_path:
                msg = await bot_client.send_photo(
                    chat_id=message.chat.id,
                    photo=photo_path,
                    caption=text,
                    reply_markup=markup,
                    reply_to_message_id=reply_to,
                )
            else:
                msg = await bot_client.send_message(
                    chat_id=message.chat.id,
                    text=text,
                    reply_markup=markup,
                    reply_to_message_id=reply_to,
                )
            if msg:
                _HELP_MSG_TRACKER.append((message.chat.id, msg.id, time.time()))
                if len(_HELP_MSG_TRACKER) > 50:
                    _HELP_MSG_TRACKER.pop(0)
                await message.delete()
                return
        except Exception as e:
            logger.debug("Failed to deliver help via bot_client: %s", e)

    # Clean fallback without text dump
    markup = _raw_to_markup(raw_buttons)
    return await edit_or_reply(
        message,
        f"{text}\n\n_Configure BOT_TOKEN in .env to enable interactive inline buttons._",
    )


@on_cmd("help", desc="Open interactive command catalog with inline buttons", usage="[category|command]")
async def help_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    active_prefix = get_client_prefix(client)

    photo = await _get_profile_photo(client)

    # Mode 1: No arguments or 'all' -> interactive home catalog with category buttons
    if len(args) == 1 or args[1].lower().strip() == "all":
        b_text, b_raw = _build_help_home_content()
        return await _deliver_help_menu(client, message, b_text, b_raw, photo, "help")

    raw_target = args[1].lower().strip().lstrip(active_prefix).lstrip(".").lstrip("/")

    # Mode 2: Category inspection -> category card with command buttons
    matched_cat = None
    if raw_target in CATEGORY_INFO:
        matched_cat = raw_target
    elif raw_target in CATEGORY_ALIASES and CATEGORY_ALIASES[raw_target] in CATEGORY_INFO:
        matched_cat = CATEGORY_ALIASES[raw_target]

    if matched_cat:
        b_text, b_raw = _build_help_cat_content(matched_cat)
        return await _deliver_help_menu(client, message, b_text, b_raw, photo, f"help {matched_cat}")

    # Mode 3: Command syntax inspection -> command card with interactive buttons
    meta = PyGramClient.COMMANDS.get(raw_target)
    if meta:
        cat_key = meta.get("category", "general")
        b_text, b_raw = _build_help_cmd_content(raw_target, cat_key)
        return await _deliver_help_menu(client, message, b_text, b_raw, photo, f"help {raw_target}")

    # Target not recognized
    await edit_or_reply(
        message,
        f"Command or category `{raw_target}` not found.\n"
        f"Tap `{active_prefix}help` to browse commands interactively.",
    )


async def _graceful_restart():
    """Flush database WAL and disconnect MTProto sessions before process replacement."""
    try:
        from pygramx.database import db
        with db._get_connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    except Exception:
        pass

    try:
        from pygramx.client import PyGramClient
        for c in (PyGramClient.USERBOT_CLIENT, PyGramClient.BOT_CLIENT):
            if c and getattr(c, "is_connected", False):
                await c.stop(block=False)
    except Exception:
        pass

    os.execl(sys.executable, sys.executable, *sys.argv)

@on_cmd("restart", desc="Gracefully restart the userbot process")
async def restart_cmd(client: Client, message: Message):
    status_msg = await edit_or_reply(message, "`Restarting TeleForge userbot...`")
    db.set("RESTART_NOTICE", {
        "chat_id": message.chat.id,
        "message_id": status_msg.id,
        "time": time.time(),
    })
    await _graceful_restart()

async def _run_git(*args: str) -> tuple[int, str, str]:
    """Execute a git command asynchronously in the repo root directory."""
    env = os.environ.copy()
    env["HOME"] = get_portable_home()

    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=REPO_DIR,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        return proc.returncode, stdout.decode().strip(), stderr.decode().strip()
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:
            pass
        return -1, "", "Command timed out after 30 seconds."

@on_cmd(["update", "gitpull"], desc="Check for updates and pull latest commits from remote")
async def update_cmd(client: Client, message: Message):
    status_msg = await edit_or_reply(message, "`Checking remote for updates...`")

    # Dynamically detect active branch and upstream remote
    code, current_branch, _ = await _run_git("rev-parse", "--abbrev-ref", "HEAD")
    branch = current_branch.strip() if code == 0 and current_branch.strip() else "main"

    code, upstream, _ = await _run_git("rev-parse", "--abbrev-ref", "@{upstream}")
    remote = "origin"
    target_branch = branch
    if code == 0 and upstream and "/" in upstream:
        parts = upstream.strip().split("/", 1)
        remote, target_branch = parts[0], parts[1]

    upstream_ref = f"{remote}/{target_branch}"
    code, _, err = await _run_git("fetch", remote, target_branch)
    if code != 0:
        await edit_or_reply(status_msg, f"**Update check failed:**\n`{err or 'Failed to reach remote repository.'}`")
        return

    code, count_str, _ = await _run_git("rev-list", "--count", f"HEAD..{upstream_ref}")
    count = int(count_str) if count_str.isdigit() else 0

    code, current_head, _ = await _run_git("rev-parse", "--short", "HEAD")
    code, current_msg, _ = await _run_git("log", "-1", "--format=%s (%cr)")

    if count == 0:
        card = (
            f"**TeleForge is up to date.**\n"
            f"• **Branch:** `{branch}` (`{upstream_ref}`)\n"
            f"• **Commit:** `{current_head}`\n"
            f"• **Summary:** {current_msg}"
        )
        await edit_or_reply(status_msg, card)
        return

    code, incoming_log, _ = await _run_git("log", f"HEAD..{upstream_ref}", "--oneline", "-n", "5")
    await edit_or_reply(status_msg, f"`Applying update ({count} commit{'s' if count > 1 else ''})...`")

    pull_code, pull_out, pull_err = await _run_git("pull", remote, target_branch)
    if pull_code != 0:
        await edit_or_reply(status_msg, f"**Update failed during git pull:**\n`{pull_err or pull_out}`")
        return

    # Check if requirements.txt changed and install new packages automatically
    code, changed_files, _ = await _run_git("diff", f"{current_head}..HEAD", "--name-only")
    if "requirements.txt" in changed_files.splitlines():
        await edit_or_reply(status_msg, "`Installing updated dependencies from requirements.txt...`")
        env = os.environ.copy()
        env["HOME"] = get_portable_home()
        pip_proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            os.path.join(REPO_DIR, "requirements.txt"),
            "--quiet",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=REPO_DIR,
            env=env,
        )
        await pip_proc.communicate()

    code, new_head, _ = await _run_git("rev-parse", "--short", "HEAD")
    code, new_msg, _ = await _run_git("log", "-1", "--format=%s")

    db.set("RESTART_NOTICE", {
        "chat_id": message.chat.id,
        "message_id": status_msg.id,
        "time": time.time(),
        "header": "TeleForge updated & restarted successfully!",
        "extra": f"• **Commit:** `{new_head}` ({new_msg})\n• **Updated:** {count} commit(s)",
    })

    await _graceful_restart()

def _parse_temp(raw_str: str) -> float:
    try:
        val = float(raw_str.strip())
        if val > 1000:
            return val / 1000.0
        elif val > 100:
            return val / 10.0
        return val
    except Exception:
        return 0.0

def _get_battery_stats() -> dict[str, str] | None:
    direct_path = find_power_supply_dir()
    if direct_path and os.path.isdir(direct_path):
        try:
            with open(f"{direct_path}/capacity") as f1, \
                 open(f"{direct_path}/status") as f2:
                temp_c = 0.0
                if os.path.isfile(f"{direct_path}/temp"):
                    with open(f"{direct_path}/temp") as f3:
                        temp_c = _parse_temp(f3.read())
                return {
                    "level": f"{f1.read().strip()}%",
                    "status": f2.read().strip(),
                    "temp": f"{temp_c:.1f}°C" if temp_c > 0 else "Normal",
                }
        except Exception:
            pass

    target_path = direct_path or "/sys/class/power_supply/battery"
    try:
        res = subprocess.run(
            ["su", "-c", f"cat {target_path}/capacity {target_path}/status {target_path}/temp {target_path}/health 2>/dev/null"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=1.2,
        )
        if res.returncode == 0:
            lines = res.stdout.strip().splitlines()
            if len(lines) >= 2:
                temp_c = _parse_temp(lines[2]) if len(lines) > 2 else 0.0
                health = lines[3].strip() if len(lines) > 3 else "Normal"
                return {
                    "level": f"{lines[0].strip()}%",
                    "status": lines[1].strip(),
                    "temp": f"{temp_c:.1f}°C" if temp_c > 0 else "Normal",
                    "health": health,
                }
    except Exception:
        pass

    if direct_path and os.path.isdir(direct_path):
        try:
            if os.path.isfile(f"{direct_path}/temp"):
                with open(f"{direct_path}/temp") as f3:
                    temp_c = _parse_temp(f3.read())
                    return {
                        "level": "Unavailable",
                        "status": "Unknown",
                        "temp": f"{temp_c:.1f}°C",
                        "health": "Normal",
                    }
        except Exception:
            pass

    return None

def _get_memory_usage() -> dict[str, int | float] | None:
    try:
        with open("/proc/meminfo", "r") as f:
            lines = f.readlines()
        mem = {}
        for line in lines:
            parts = line.split(":")
            if len(parts) == 2:
                mem[parts[0].strip()] = int(parts[1].split()[0])
        total_kb = mem.get("MemTotal", 0)
        avail_kb = mem.get("MemAvailable", 0)
        used_kb = total_kb - avail_kb
        pct = (used_kb / total_kb * 100) if total_kb else 0.0

        swap_total_kb = mem.get("SwapTotal", 0)
        swap_free_kb = mem.get("SwapFree", 0)
        swap_used_kb = swap_total_kb - swap_free_kb
        swap_pct = (swap_used_kb / swap_total_kb * 100) if swap_total_kb else 0.0

        return {
            "total": total_kb * 1024,
            "used": used_kb * 1024,
            "available": avail_kb * 1024,
            "percent": pct,
            "swap_total": swap_total_kb * 1024,
            "swap_used": swap_used_kb * 1024,
            "swap_percent": swap_pct,
        }
    except Exception:
        return None

def _get_process_rss() -> int:
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 0

def _get_system_load() -> str:
    if hasattr(os, "getloadavg"):
        try:
            l1, l5, l15 = os.getloadavg()
            return f"{l1:.2f}, {l5:.2f}, {l15:.2f}"
        except Exception:
            pass
    try:
        res = subprocess.run(["uptime"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1.0)
        out = res.stdout.strip()
        if "load average:" in out:
            return out.split("load average:")[1].strip()
        elif "load averages:" in out:
            return out.split("load averages:")[1].strip()
    except Exception:
        pass
    return "Unavailable"

def _get_system_uptime() -> str:
    try:
        res = subprocess.run(["uptime"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1.0)
        out = res.stdout.strip()
        if " up " in out:
            part = out.split(" up ")[1]
            for k in ("load average", "load averages"):
                if k in part:
                    part = part.split(k)[0]
                    break
            tokens = [t.strip() for t in part.split(",") if "user" not in t and t.strip()]
            if tokens:
                return ", ".join(tokens)
    except Exception:
        pass
    return "Unavailable"

def _get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except Exception:
        return "Offline"

def _get_os_info() -> str:
    try:
        if os.path.exists("/system/build.prop") or "com.termux" in os.environ.get("PREFIX", ""):
            res = subprocess.run(
                ["getprop", "ro.build.version.release"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            rel = res.stdout.strip()
            res_sdk = subprocess.run(
                ["getprop", "ro.build.version.sdk"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            sdk = res_sdk.stdout.strip()
            if rel:
                sdk_str = f" (API {sdk})" if sdk else ""
                return f"Android {rel}{sdk_str}"
        return f"{platform.system()} {platform.release()}"
    except Exception:
        return platform.system() or "Linux"

@on_cmd(["sys", "stats"], desc="Display system status, memory usage, and hardware statistics")
async def sys_cmd(client: Client, message: Message):
    dev_name = get_device_model()
    os_info = _get_os_info()
    kernel_ver = os.uname().release
    arch = platform.machine()
    cpu_count = os.cpu_count() or 1

    batt = _get_battery_stats()
    is_android = "com.termux" in os.environ.get("PREFIX", "") or os.path.exists("/system/build.prop")

    if batt:
        health_part = f", {batt['health']}" if batt.get("health") else ""
        batt_str = f"`{batt['level']}` ({batt['status']}, `{batt['temp']}`{health_part})"
    elif not is_android and not os.path.exists("/sys/class/power_supply"):
        batt_str = "`Not present (AC Powered / Cloud Server)`"
    else:
        batt_str = "`Unavailable (root privileges required)`"

    mem = _get_memory_usage()
    if mem:
        mem_str = f"`{format_bytes(mem['used'])}` / `{format_bytes(mem['total'])}` (`{mem['percent']:.1f}%`)"
        if mem.get("swap_total", 0) > 0:
            swap_line = f"• **ZRAM / Swap:** `{format_bytes(mem['swap_used'])}` / `{format_bytes(mem['swap_total'])}` (`{mem['swap_percent']:.1f}%`)\n"
        else:
            swap_line = ""
    else:
        mem_str = "`Unavailable`"
        swap_line = ""

    h_total, h_free = 0, 0
    try:
        h_total, h_used, h_free = shutil.disk_usage(os.path.expanduser("~"))
        disk_home_str = f"`{format_bytes(h_used)}` / `{format_bytes(h_total)}` (`{format_bytes(h_free)}` free)"
    except Exception:
        disk_home_str = "`Unavailable`"

    sd_line = ""
    if os.path.exists("/sdcard"):
        try:
            s_total, s_used, s_free = shutil.disk_usage("/sdcard")
            if s_total != h_total or s_free != h_free:
                sd_line = f"• **Shared Storage:** `{format_bytes(s_used)}` / `{format_bytes(s_total)}` (`{format_bytes(s_free)}` free)\n"
        except Exception:
            pass

    storage_label = "Storage (Home)" if sd_line else "Storage"

    load_str = _get_system_load()
    sys_uptime_str = _get_system_uptime()
    bot_uptime_str = format_uptime(time.time() - PyGramClient.START_TIME)

    rss_bytes = _get_process_rss()
    bot_ram_str = format_bytes(rss_bytes) if rss_bytes else "Unavailable"
    local_ip = _get_local_ip()

    log_target = config.get_log_chat()
    alert_str = f"`{log_target}`" if log_target else "`Not configured`"

    py_ver = sys.version.split()[0]
    pyro_ver = getattr(pyrogram, "__version__", "2.0")

    card = (
        f"**System Status**\n"
        f"• **Host:** `{dev_name}`\n"
        f"• **OS & Kernel:** `{os_info}` (`{kernel_ver}`, `{arch}`)\n"
        f"• **CPU & Cores:** `{cpu_count} Cores` (Load: `{load_str}`)\n"
        f"• **Battery:** {batt_str}\n"
        f"• **Memory (RAM):** {mem_str}\n"
        f"{swap_line}"
        f"• **{storage_label}:** {disk_home_str}\n"
        f"{sd_line}"
        f"• **Network (LAN):** `{local_ip}`\n"
        f"• **Python:** `{py_ver}` • **Pyrogram:** `{pyro_ver}`\n"
        f"• **Bot Memory:** `{bot_ram_str}` (PID: `{os.getpid()}`)\n"
        f"• **Uptime:** Bot: `{bot_uptime_str}` | System: `{sys_uptime_str}`\n"
        f"• **Notification Chat:** {alert_str}"
    )
    await edit_or_reply(message, card)

@on_cmd(["setlog", "setalert"], desc="Set current chat or specified ID as notification destination", usage="[chat_id]")
async def setlog_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    if len(args) > 1:
        target = args[1].strip()
        try:
            target_id = int(target)
        except ValueError:
            target_id = target
    else:
        target_id = message.chat.id

    db.set("LOG_CHAT", target_id)
    await edit_or_reply(
        message,
        f"**Notification Destination Configured**\n"
        f"• **Target Chat:** `{target_id}`\n"
        f"• Temperature alerts and critical notifications will be delivered to this chat."
    )

@on_cmd(["getlog", "logchat"], desc="Inspect current notification destination")
async def getlog_cmd(client: Client, message: Message):
    target = config.get_log_chat()
    prefix = get_client_prefix(client)
    if not target:
        await edit_or_reply(
            message,
            f"**Notification Destination:** `Not configured`\n"
            f"Send `{prefix}setlog` inside your private group to designate it as the notification chat."
        )
        return

    await edit_or_reply(
        message,
        f"**Notification Destination:** `{target}`\n"
        f"Active for temperature alerts and system notifications."
    )

@on_cmd(["settrigger", "setprefix", "setbottrigger"], desc="Change command prefix for userbot or companion bot", usage="[user|bot] <prefix>")
async def settrigger_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    parts = text.split()
    curr_prefix = get_client_prefix(client)
    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )

    user_trig = config.get_trigger()
    bot_trig = config.get_bot_trigger()

    # Mode 1: No arguments -> show active prefix for both clients
    if len(parts) < 2:
        card = (
            f"**Command Trigger Prefixes**\n"
            f"• **Userbot Prefix:** `{user_trig}`\n"
            f"• **Companion Bot Prefix:** `{bot_trig}`\n\n"
            f"Usage:\n"
            f"• `{curr_prefix}settrigger <prefix>` (changes active client prefix)\n"
            f"• `{curr_prefix}settrigger bot <prefix>` or `{curr_prefix}setbottrigger <prefix>` (sets companion bot prefix)\n"
            f"• `{curr_prefix}settrigger user <prefix>` (sets userbot prefix)"
        )
        return await edit_or_reply(message, card)

    first_word = parts[0].lower().lstrip(curr_prefix)
    target_type = None
    new_trigger = None

    if "setbottrigger" in first_word:
        target_type = "bot"
        new_trigger = parts[1].strip()
    elif len(parts) >= 3 and parts[1].lower() in ("bot", "user"):
        target_type = parts[1].lower()
        new_trigger = parts[2].strip()
    else:
        target_type = "bot" if is_bot else "user"
        new_trigger = parts[1].strip()

    if not new_trigger or len(new_trigger) > 3 or any(c.isalnum() for c in new_trigger):
        return await edit_or_reply(
            message,
            "`Invalid prefix. Please choose 1 to 3 non-alphanumeric symbols (e.g. '!', ',', '-', '/', '#').`"
        )

    if target_type == "bot":
        if new_trigger == user_trig:
            return await edit_or_reply(
                message,
                f"`Conflict: Companion bot prefix cannot be identical to userbot prefix ('{user_trig}').`"
            )
        old_val = bot_trig
        db.set("BOT_TRIGGER", new_trigger)
        await edit_or_reply(
            message,
            f"**Companion Bot Prefix Updated**\n"
            f"• **Previous:** `{old_val}`\n"
            f"• **Active:** `{new_trigger}`\n\n"
            f"Companion bot now responds to `{new_trigger}ping`, `{new_trigger}help`, etc."
        )
    else:
        if new_trigger == bot_trig:
            return await edit_or_reply(
                message,
                f"`Conflict: Userbot prefix cannot be identical to companion bot prefix ('{bot_trig}').`"
            )
        old_val = user_trig
        db.set("TRIGGER", new_trigger)
        await edit_or_reply(
            message,
            f"**Userbot Prefix Updated**\n"
            f"• **Previous:** `{old_val}`\n"
            f"• **Active:** `{new_trigger}`\n\n"
            f"Userbot now responds to `{new_trigger}ping`, `{new_trigger}alive`, etc."
        )


@on_cmd("watchdog", desc="Configure battery thermal monitor", usage="[on/off/threshold_celsius]")
async def watchdog_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    curr_trig = get_client_prefix(client)
    if len(args) < 2:
        status = "Enabled" if config.is_watchdog_enabled() else "Disabled"
        thresh = config.get_battery_threshold()
        await edit_or_reply(
            message,
            f"**Battery Thermal Monitor**\n"
            f"• **Status:** `{status}`\n"
            f"• **Alert Threshold:** `{thresh}°C`\n\n"
            f"Usage: `{curr_trig}watchdog on|off` or `{curr_trig}watchdog 44`"
        )
        return

    arg = args[1].lower().strip()
    if arg in ["on", "enable", "true"]:
        db.set("WATCHDOG_ENABLED", True)
        await edit_or_reply(message, "**Battery Thermal Monitor:** `Enabled`.")
    elif arg in ["off", "disable", "false"]:
        db.set("WATCHDOG_ENABLED", False)
        await edit_or_reply(message, "**Battery Thermal Monitor:** `Disabled`.")
    else:
        try:
            val = float(arg)
            if val < 35.0 or val > 60.0:
                await edit_or_reply(message, "`Temperature threshold must be between 35°C and 60°C.`")
                return
            db.set("BATTERY_THRESHOLD", val)
            await edit_or_reply(message, f"**Thermal Alert Threshold Updated:** `{val}°C`.")
        except ValueError:
            await edit_or_reply(message, f"`Usage: {curr_trig}watchdog on|off|<temperature_celsius>`")

@on_cmd(["tail", "logs"], desc="Display recent application log lines", usage="[lines]")
async def tail_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    limit = 25
    if len(args) > 1:
        try:
            val = int(args[1].strip())
            limit = min(max(val, 1), 100)
        except ValueError:
            pass

    log_path = os.path.join(REPO_DIR, "pygramx.log")
    if not os.path.isfile(log_path):
        return await edit_or_reply(message, "Log file `pygramx.log` was not found.")

    status_msg = await edit_or_reply(message, f"`Reading last {limit} log lines...`")
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = list(deque(f, maxlen=limit))
    except Exception as e:
        return await edit_or_reply(status_msg, f"Failed to read logs: {e}")

    if not lines:
        return await edit_or_reply(status_msg, "Log file is currently empty.")

    log_content = "".join(lines).strip()
    await send_large_output(
        status_msg,
        log_content,
        caption=f"**TeleForge Logs (last {len(lines)} lines):**",
        filename="pygramx_tail.log",
    )

@on_cmd("backup", desc="Create an atomic SQLite database backup snapshot")
async def backup_cmd(client: Client, message: Message):
    status_msg = await edit_or_reply(message, "`Generating database snapshot...`")
    db_path = getattr(db, "db_path", os.path.join(REPO_DIR, "pygramx.db"))
    if not os.path.isfile(db_path):
        return await edit_or_reply(status_msg, "Database file `pygramx.db` was not found.")

    log_target = config.get_log_chat()
    is_group = str(message.chat.id).startswith("-100") or str(getattr(message.chat, "type", "")).lower() in ("group", "supergroup")

    if log_target:
        dest_chat = log_target
    elif is_group:
        dest_chat = "me"
    else:
        dest_chat = message.chat.id

    ts = time.strftime("%Y%m%d_%H%M%S")
    tmp_backup = os.path.join(tempfile.gettempdir(), f"pygramx_backup_{ts}.db")

    try:
        with sqlite3.connect(db_path) as src, sqlite3.connect(tmp_backup) as dst:
            src.backup(dst)

        size_str = format_bytes(os.path.getsize(tmp_backup))
        caption = (
            f"**Database Backup Snapshot**\n"
            f"• **Timestamp:** `{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}`\n"
            f"• **Total Keys:** `{len(db)}`\n"
            f"• **Size:** `{size_str}`"
        )

        await client.send_document(
            chat_id=dest_chat,
            document=tmp_backup,
            caption=caption,
        )

        if dest_chat != message.chat.id:
            await edit_or_reply(
                status_msg,
                f"**Database Backup Snapshot**\n"
                f"• Snapshot generated (`{size_str}`, `{len(db)}` keys).\n"
                f"• Delivered securely to `{dest_chat}`."
            )
        else:
            await status_msg.delete()

    except Exception as e:
        await edit_or_reply(status_msg, f"Backup failed: {e}")
    finally:
        if os.path.isfile(tmp_backup):
            try:
                os.remove(tmp_backup)
            except Exception:
                pass


@Client.on_callback_query(filters.regex(r"^wspr:"))
async def whisper_callback_query(client: Client, query: CallbackQuery):
    wid = (query.data or "").split(":", 1)[1]
    whispers = db.get("WHISPERS", {})
    whisper = whispers.get(wid)
    if not whisper:
        return await query.answer("🔒 This secret whisper has expired or does not exist.", show_alert=True)

    uid = query.from_user.id if query.from_user else 0
    uname = ((query.from_user and query.from_user.username) or "").lower()
    target = str(whisper["target"]).lower().lstrip("@")

    is_author = uid == whisper["from_id"]
    is_target = (target == str(uid)) or (target == uname and bool(uname))

    if is_author or is_target:
        await query.answer(f"🔓 Secret Message:\n\n{whisper['text']}", show_alert=True)
    else:
        await query.answer("🔒 This secret whisper is not meant for you!", show_alert=True)


@on_cmd("wspr", desc="Send a secret whisper via inline companion bot", usage="[@username|id] <secret_message>")
async def wspr_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=2)
    prefix = get_client_prefix(client)
    reply = message.reply_to_message

    target = ""
    secret_text = ""

    # Smart reply targeting
    if reply and reply.from_user:
        replied_u = reply.from_user
        inferred_target = f"@{replied_u.username}" if replied_u.username else str(replied_u.id)
        if len(args) == 2:
            target = inferred_target
            secret_text = args[1].strip()
        elif len(args) >= 3:
            if args[1].startswith("@") or args[1].lstrip("-").isdigit():
                target = args[1].strip()
                secret_text = args[2].strip()
            else:
                target = inferred_target
                secret_text = text.split(maxsplit=1)[1].strip()
    elif len(args) >= 3:
        target = args[1].strip()
        secret_text = args[2].strip()

    if not target or not secret_text:
        return await edit_or_reply(
            message,
            f"Usage:\n"
            f"• `{prefix}wspr <@username|id> <secret_message>`\n"
            f"• Or reply to a user with `{prefix}wspr <secret_message>`"
        )

    bot_client = getattr(PyGramClient, "BOT_CLIENT", None)
    bot_username = getattr(bot_client, "me", None) and bot_client.me.username
    if not bot_username and config.BOT_TOKEN and bot_client:
        try:
            bme = await bot_client.get_me()
            bot_username = bme.username
        except Exception:
            pass

    if not bot_username:
        return await edit_or_reply(message, "Companion bot is required for secret whispers. Configure BOT_TOKEN.")

    try:
        results = await client.get_inline_bot_results(bot_username, f"wspr {target} {secret_text}")
        if results and results.results:
            await client.send_inline_bot_result(
                chat_id=message.chat.id,
                query_id=results.query_id,
                result_id=results.results[0].id,
                reply_to_message_id=message.reply_to_message.id if message.reply_to_message else None,
            )
            await message.delete()
            return
        await edit_or_reply(message, "Failed generating whisper result.")
    except Exception as e:
        await edit_or_reply(message, f"Whisper failed: {e}")


BUILTIN_MODULES = {
    "system", "tools", "admin", "media", "pmpermit", "purge",
    "security", "transfer", "network", "android", "astronomy",
    "seismo", "sport", "stickers"
}

@on_cmd(["install", "loadmod"], desc="Install or hot-reload a Python plugin dynamically without restart", usage="[reply to .py | name/url]")
async def install_plugin_cmd(client: Client, message: Message):
    prefix = get_client_prefix(client)
    reply = message.reply_to_message
    code = ""
    mod_name = ""

    # Mode 1: Reply to a document file
    if reply and reply.document:
        doc = reply.document
        if not (doc.file_name and doc.file_name.endswith(".py")):
            return await edit_or_reply(message, "Please reply to a Python (`.py`) document.")
        status = await edit_or_reply(message, f"`Downloading plugin {doc.file_name}...`")
        mod_name = os.path.splitext(doc.file_name)[0].lower()
        mod_name = re.sub(r"[^a-zA-Z0-9_]", "_", mod_name)
        target_path = os.path.join(REPO_DIR, "modules", f"{mod_name}.py")
        downloaded = await reply.download(file_name=target_path)
        with open(downloaded, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()

    # Mode 2: Reply to text code or direct URL
    elif reply and (reply.text or reply.caption):
        code = reply.text or reply.caption or ""
        args = (message.text or message.caption or "").split(maxsplit=1)
        if len(args) > 1:
            mod_name = args[1].strip().lower()
        else:
            mod_name = f"plugin_{int(time.time())}"
        status = await edit_or_reply(message, f"`Installing plugin {mod_name}...`")

    else:
        args = (message.text or message.caption or "").split(maxsplit=1)
        if len(args) < 2:
            return await edit_or_reply(
                message,
                f"Usage:\n"
                f"• Reply to a `.py` file with `{prefix}install`\n"
                f"• Or `{prefix}install <raw_github_url>`\n"
                f"• Or reply to python code with `{prefix}install <name>`"
            )
        target = args[1].strip()
        status = await edit_or_reply(message, "`Fetching plugin code...`")
        if target.startswith("http://") or target.startswith("https://"):
            try:
                resp = requests.get(target, timeout=15)
                if resp.status_code != 200:
                    return await edit_or_reply(status, f"HTTP Error fetching plugin: `{resp.status_code}`")
                code = resp.text
                url_name = os.path.splitext(os.path.basename(target.split("?")[0]))[0]
                mod_name = url_name if url_name else f"plugin_{int(time.time())}"
            except Exception as e:
                return await edit_or_reply(status, f"Failed fetching URL: {e}")
        elif os.path.isfile(target):
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                code = f.read()
            mod_name = os.path.splitext(os.path.basename(target))[0]
        else:
            return await edit_or_reply(status, f"Target not recognized: `{target}`")

    mod_name = re.sub(r"[^a-zA-Z0-9_]", "_", mod_name).strip("_")
    if not mod_name:
        return await edit_or_reply(status, "Invalid plugin name generated.")

    if mod_name in BUILTIN_MODULES:
        return await edit_or_reply(status, f"Cannot overwrite protected core module `{mod_name}`.")

    # Syntax verification via AST
    try:
        ast.parse(code)
    except SyntaxError as e:
        return await edit_or_reply(status, f"**Syntax Error in plugin:**\n`{e}` (line {e.lineno})")

    # Unload old version if already present
    full_mod = f"modules.{mod_name}"
    if full_mod in sys.modules:
        PyGramClient.unload_module(full_mod)

    # Write to disk
    target_file = os.path.join(REPO_DIR, "modules", f"{mod_name}.py")
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(code)

    # Import dynamically
    try:
        mod = importlib.import_module(full_mod)
        importlib.reload(mod)
        PyGramClient.load_module_handlers(mod)
    except Exception as e:
        if os.path.isfile(target_file):
            try:
                os.remove(target_file)
            except Exception:
                pass
        PyGramClient.unload_module(full_mod)
        return await edit_or_reply(status, f"**Failed loading plugin:**\n`{e}`")

    # Find newly registered commands
    new_cmds = [
        k for k, v in PyGramClient.COMMANDS.items()
        if not v.get("is_alias") and v.get("module") == full_mod
    ]

    cmd_list_str = ", ".join(f"`{prefix}{c}`" for c in sorted(new_cmds)) if new_cmds else "None"
    await edit_or_reply(
        status,
        f"**Plugin Installed Successfully**\n"
        f"• **Module:** `{mod_name}`\n"
        f"• **File:** `modules/{mod_name}.py`\n"
        f"• **Commands ({len(new_cmds)}):** {cmd_list_str}\n\n"
        f"Commands are active immediately without restart."
    )

@on_cmd(["uninstall", "unloadmod"], desc="Uninstall a dynamically loaded plugin", usage="<module_name>")
async def uninstall_plugin_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    prefix = get_client_prefix(client)
    if len(args) < 2:
        return await edit_or_reply(message, f"Usage: `{prefix}uninstall <plugin_name>`")

    mod_name = args[1].strip().lower().removesuffix(".py")
    if mod_name in BUILTIN_MODULES:
        return await edit_or_reply(message, f"Protected module `{mod_name}` cannot be uninstalled.")

    target_file = os.path.join(REPO_DIR, "modules", f"{mod_name}.py")
    full_mod = f"modules.{mod_name}"

    if not os.path.isfile(target_file) and full_mod not in sys.modules:
        return await edit_or_reply(message, f"Plugin `{mod_name}` was not found in `modules/`.")

    removed = PyGramClient.unload_module(full_mod)
    if os.path.isfile(target_file):
        try:
            os.remove(target_file)
        except Exception as e:
            logger.debug("Failed to remove file: %s", e)

    # Clean pycache
    pycache_dir = os.path.join(REPO_DIR, "modules", "__pycache__")
    if os.path.isdir(pycache_dir):
        for f in os.listdir(pycache_dir):
            if f.startswith(f"{mod_name}."):
                try:
                    os.remove(os.path.join(pycache_dir, f))
                except Exception:
                    pass

    cmd_str = ", ".join(f"`{c}`" for c in removed) if removed else "None"
    await edit_or_reply(
        message,
        f"**Plugin Uninstalled**\n"
        f"• **Module:** `{mod_name}`\n"
        f"• **Removed Commands:** {cmd_str}\n"
        f"• File deleted from `modules/`."
    )

@on_cmd(["plugins", "listmods"], desc="List all installed modules and plugins")
async def plugins_cmd(client: Client, message: Message):
    modules_dir = os.path.join(REPO_DIR, "modules")
    if not os.path.isdir(modules_dir):
        return await edit_or_reply(message, "Modules directory not found.")

    all_files = sorted(f for f in os.listdir(modules_dir) if f.endswith(".py") and not f.startswith("__"))
    builtin_list = []
    custom_list = []

    for f in all_files:
        name = f.removesuffix(".py")
        mod_key = f"modules.{name}"
        cmds = [k for k, v in PyGramClient.COMMANDS.items() if not v.get("is_alias") and v.get("module") == mod_key]
        item = f"`{name}` ({len(cmds)} cmds)"
        if name in BUILTIN_MODULES:
            builtin_list.append(item)
        else:
            custom_list.append(item)

    custom_str = "\n".join(f"• {x}" for x in custom_list) if custom_list else "• _No custom plugins installed._"
    builtin_str = "\n".join(f"• {x}" for x in builtin_list)

    text = (
        f"**TeleForge Plugin Catalog**\n"
        f"• **Total Modules:** `{len(all_files)}`\n\n"
        f"**Custom Plugins ({len(custom_list)}):**\n{custom_str}\n\n"
        f"**Core Builtin Modules ({len(builtin_list)}):**\n{builtin_str}"
    )
    await edit_or_reply(message, text)

@on_cmd(["settings", "setter", "set"], desc="Open interactive control center with inline toggle buttons")
async def settings_cmd(client: Client, message: Message):
    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )

    from modules.inline import _settings_text_and_markup

    text, raw_buttons, markup = _settings_text_and_markup()
    reply_to = message.reply_to_message.id if message.reply_to_message else None

    # Step 1: Deliver directly with native Bot API 9.4 styled colored buttons
    if config.BOT_TOKEN:
        if await _bot_api_send_message(message.chat.id, text, raw_buttons, reply_to):
            await message.delete()
            return

    if is_bot:
        return await edit_or_reply(message, text, reply_markup=markup)

    # Step 2: Userbot fallback via companion bot inline query ('via @bot')
    bot_client = getattr(PyGramClient, "BOT_CLIENT", None)
    bot_username = (
        (getattr(bot_client, "me", None) and bot_client.me.username)
        or db.get("BOT_USERNAME")
    )
    if not bot_username and config.BOT_TOKEN and bot_client:
        try:
            bme = await bot_client.get_me()
            bot_username = bme.username
            if bot_username:
                db.set("BOT_USERNAME", bot_username)
        except Exception:
            pass

    if bot_username:
        try:
            results = await client.get_inline_bot_results(bot_username, "settings")
            if results and results.results:
                updates = await client.send_inline_bot_result(
                    chat_id=message.chat.id,
                    query_id=results.query_id,
                    result_id=results.results[0].id,
                    reply_to_message_id=reply_to,
                )
                msg_id = _extract_msg_id(updates)
                if msg_id:
                    _HELP_MSG_TRACKER.append((message.chat.id, msg_id, time.time()))
                    if len(_HELP_MSG_TRACKER) > 50:
                        _HELP_MSG_TRACKER.pop(0)
                    # Immediate reply_markup update to force-render Bot API 9.4 styles without user interaction
                    try:
                        await _bot_api_call("editMessageReplyMarkup", {
                            "chat_id": message.chat.id,
                            "message_id": msg_id,
                            "reply_markup": {"inline_keyboard": raw_buttons},
                        }, timeout=3.0)
                    except Exception:
                        pass
                await message.delete()
                return
        except Exception as e:
            logger.warning("Settings inline query via @%s skipped: %s", bot_username, e)

    return await edit_or_reply(message, text, reply_markup=markup)


