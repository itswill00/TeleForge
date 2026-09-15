import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import ast
import logging
import operator
import time
import urllib.request
from typing import Any, Optional

from pyrogram import Client, filters, ContinuePropagation
from pyrogram.errors import MessageNotModified
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from pyrogram.enums import ParseMode
import config
from pygramx.client import PyGramClient
from pygramx.database import db
from pygramx.utils import format_uptime

logger = logging.getLogger("pygramx.inline")

# ponytail: inline hub (Ultroid-style patterns). system.py keeps help+whisper;
# this hub owns alive/calc/settings/paste prefixes only. No overlap, no double-answer.

PREFIXES = ("alive", "calc", "settings", "set", "setter", "paste")


def _is_owner(user_id: Optional[int]) -> bool:
    owner_id = getattr(PyGramClient, "OWNER_ID", None) or config.get_owner_id()
    return bool(user_id and owner_id and user_id == owner_id)


def owns_query(q: str) -> bool:
    """True if this hub should answer the query (called from system.py guard)."""
    head = ((q or "").strip().split(maxsplit=1) or [""])[0].lower()
    return head in PREFIXES


def _hub_query_filter(_, __, inline_query: InlineQuery) -> bool:
    return owns_query(inline_query.query or "")


# --- calc: safe arithmetic via ast (no eval) ---
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _safe_eval(expr: str) -> float:
    tree = ast.parse(expr, mode="eval")

    def _node(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return _node(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](_node(n.left), _node(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](_node(n.operand))
        raise ValueError("unsupported expression")

    return _node(tree)


def _calc_article(expr: str) -> InlineQueryResultArticle:
    expr = expr.strip()[:100]
    if not expr:
        text = "Usage: `@bot calc 12 * (3 + 4)`"
        title, desc = "Calculator", "Type an expression after calc"
    else:
        try:
            val = _safe_eval(expr)
            out = f"{int(val)}" if val.is_integer() else f"{val:.6g}"
            text = f"`{expr}`\n= **{out}**"
            title, desc = f"= {out}", expr
        except Exception:
            text = f"`{expr}`\n= **Invalid expression** (numbers and + - * / % ** only)"
            title, desc = "Invalid expression", expr
    return InlineQueryResultArticle(
        id=f"calc_{abs(hash(expr)) % 10**8}",
        title=title,
        description=desc,
        input_message_content=InputTextMessageContent(text),
    )


# --- alive card ---
def _alive_article() -> InlineQueryResultArticle:
    uptime = format_uptime(time.time() - PyGramClient.START_TIME)
    total = len([c for c in PyGramClient.COMMANDS if not PyGramClient.COMMANDS[c].get("is_alias")])
    text = (
        "**TeleForge is alive**\n"
        f"• **Uptime:** {uptime}\n"
        f"• **Commands:** `{total}`\n"
        f"• **Trigger:** `{config.get_trigger()}`"
    )
    return InlineQueryResultArticle(
        id="alive_card",
        title="TeleForge Alive",
        description=f"Uptime {uptime}, {total} commands",
        input_message_content=InputTextMessageContent(text),
    )


# --- settings UI: boolean toggles and quick actions ---
_TOGGLE_DEFS = [
    ("pm", "PMPERMIT_ENABLED", "PM Permit", False),
    ("wd", "WATCHDOG_ENABLED", "Watchdog", True),
    ("afk", "AFK", "AFK Mode", False),
    ("seismo", "seismo_notify_enabled", "Quake Alert", True),
    ("iss", "iss_notify_enabled", "ISS Pass", True),
]


def _get_toggle_status(code: str) -> bool:
    if code == "afk":
        afk = db.get("AFK_DATA")
        return bool(afk and afk.get("is_afk"))
    for c, key, _, default in _TOGGLE_DEFS:
        if c == code:
            val = db.get(key)
            return bool(val) if val is not None else default
    return False


def _set_toggle_status(code: str, new_val: bool) -> None:
    if code == "afk":
        if new_val:
            db.set("AFK_DATA", {"is_afk": True, "reason": "Currently away.", "time": time.time()})
        else:
            db.set("AFK_DATA", {"is_afk": False})
        return
    for c, key, _, _ in _TOGGLE_DEFS:
        if c == code:
            db.set(key, new_val)
            return


def _raw_to_markup(raw: list[list[dict]]) -> InlineKeyboardMarkup:
    rows = []
    for row in raw:
        r = []
        for btn in row:
            if "callback_data" in btn:
                r.append(InlineKeyboardButton(btn["text"], callback_data=btn["callback_data"]))
            elif "url" in btn:
                r.append(InlineKeyboardButton(btn["text"], url=btn["url"]))
        if r:
            rows.append(r)
    return InlineKeyboardMarkup(rows)


def _settings_raw_buttons() -> list[list[dict]]:
    pm_on = _get_toggle_status("pm")
    wd_on = _get_toggle_status("wd")
    afk_on = _get_toggle_status("afk")
    seismo_on = _get_toggle_status("seismo")
    iss_on = _get_toggle_status("iss")

    return [
        [
            {"text": f"« PM: {'ON' if pm_on else 'OFF'} »", "callback_data": "in:tog:pm", "style": "success" if pm_on else "danger"},
            {"text": f"« Watchdog: {'ON' if wd_on else 'OFF'} »", "callback_data": "in:tog:wd", "style": "success" if wd_on else "danger"},
        ],
        [
            {"text": f"« AFK: {'ON' if afk_on else 'OFF'} »", "callback_data": "in:tog:afk", "style": "success" if afk_on else "danger"},
            {"text": f"« Quake: {'ON' if seismo_on else 'OFF'} »", "callback_data": "in:tog:seismo", "style": "success" if seismo_on else "danger"},
        ],
        [
            {"text": f"« ISS Alert: {'ON' if iss_on else 'OFF'} »", "callback_data": "in:tog:iss", "style": "success" if iss_on else "danger"},
            {"text": "« Live Ping »", "callback_data": "in:act:ping", "style": "primary"},
        ],
        [
            {"text": "« Alive Stats »", "callback_data": "in:act:alive", "style": "primary"},
            {"text": "« Restart Bot »", "callback_data": "in:act:restart", "style": "danger"},
        ],
        [
            {"text": "« Close Menu »", "callback_data": "in:close", "style": "danger"},
        ],
    ]


def _settings_text_and_markup() -> tuple[str, list[list[dict]], InlineKeyboardMarkup]:
    lines = [
        "**TeleForge Control Center**\n",
    ]
    for code, _, label, _ in _TOGGLE_DEFS:
        on = _get_toggle_status(code)
        lines.append(f"• **{label}:** `{'ON' if on else 'OFF'}`")

    lines += [
        f"\n• **User trigger:** `{config.get_trigger()}`",
        f"• **Bot trigger:** `{config.get_bot_trigger()}`",
        f"• **Log chat:** `{config.get_log_chat()}`",
        "\nTap any button below to toggle feature or run quick action:",
    ]

    raw_buttons = _settings_raw_buttons()
    markup = _raw_to_markup(raw_buttons)
    return "\n".join(lines), raw_buttons, markup


def _settings_article() -> InlineQueryResultArticle:
    text, _, markup = _settings_text_and_markup()
    return InlineQueryResultArticle(
        id="settings_hub",
        title="TeleForge Control Center",
        description="Toggle PM permit, watchdog, AFK, alerts and actions",
        input_message_content=InputTextMessageContent(text),
        reply_markup=markup,
    )


# --- paste: raw text -> paste.rs URL ---
def _paste_upload(text: str) -> Optional[str]:
    try:
        req = urllib.request.Request(
            "https://paste.rs",
            data=text.encode("utf-8"),
            headers={"User-Agent": "TeleForge/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            url = r.read().decode().strip()
            return url if url.startswith("http") else None
    except Exception as e:
        logger.warning("inline paste failed: %s", e)
        return None


@Client.on_inline_query(filters.create(_hub_query_filter))
async def inline_hub(client: Client, inline_query: InlineQuery):
    q = (inline_query.query or "").strip()
    if not owns_query(q):
        raise ContinuePropagation
    if not _is_owner(inline_query.from_user.id if inline_query.from_user else None):
        return await inline_query.answer([], cache_time=1)

    head, _, rest = q.partition(" ")
    head, rest = head.lower(), rest.strip()

    try:
        if head == "alive":
            results = [_alive_article()]
        elif head == "calc":
            results = [_calc_article(rest)]
        elif head in ("settings", "set", "setter"):
            text, raw_buttons, markup = _settings_text_and_markup()
            from modules.system import _bot_api_call
            payload_result = {
                "type": "article",
                "id": "settings_hub",
                "title": "TeleForge Control Center",
                "description": "Toggle PM permit, watchdog, AFK, alerts and actions",
                "input_message_content": {
                    "message_text": text,
                    "parse_mode": "Markdown",
                },
                "reply_markup": {"inline_keyboard": raw_buttons},
            }
            res = await _bot_api_call("answerInlineQuery", {
                "inline_query_id": inline_query.id,
                "results": [payload_result],
                "cache_time": 1,
                "is_personal": True,
            }, timeout=5.0)
            if res and res.get("ok"):
                return
            results = [_settings_article()]
        elif head == "paste":
            if not rest:
                results = [InlineQueryResultArticle(
                    id="paste_usage",
                    title="Paste: empty text",
                    description="Usage: @bot paste <text>",
                    input_message_content=InputTextMessageContent("Usage: `@bot paste <text>`"),
                )]
            else:
                url = await asyncio.to_thread(_paste_upload, rest[:5000])
                if url:
                    results = [InlineQueryResultArticle(
                        id=f"paste_{abs(hash(rest)) % 10**8}",
                        title="Paste uploaded",
                        description=url,
                        input_message_content=InputTextMessageContent(f"**Paste:** {url}"),
                    )]
                else:
                    results = [InlineQueryResultArticle(
                        id="paste_fail",
                        title="Paste failed",
                        description="Service unreachable, try again",
                        input_message_content=InputTextMessageContent("Paste upload failed, try again."),
                    )]
        else:
            return
        await inline_query.answer(results=results, cache_time=1, is_personal=True)
    except Exception as e:
        logger.error("inline hub error: %s", e)


async def _update_settings_menu(client: Client, query: CallbackQuery, text: str, raw_buttons: list[list[dict]], markup: InlineKeyboardMarkup):
    from modules.system import _bot_api_call
    inline_id = query.inline_message_id
    msg = query.message

    if config.BOT_TOKEN and inline_id:
        res = await _bot_api_call("editMessageText", {
            "inline_message_id": inline_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": raw_buttons},
        })
        if res and res.get("ok"):
            return

    if config.BOT_TOKEN and msg:
        res = await _bot_api_call("editMessageText", {
            "chat_id": msg.chat.id,
            "message_id": msg.id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": raw_buttons},
        })
        if res and res.get("ok"):
            return

    # Fallback to Pyrogram
    if inline_id:
        try:
            await client.edit_inline_text(inline_message_id=inline_id, text=text, reply_markup=markup)
            return
        except Exception:
            pass
    elif msg:
        try:
            await msg.edit_text(text, reply_markup=markup)
            return
        except Exception:
            pass


@Client.on_callback_query(filters.regex(r"^in:"))
async def inline_callbacks(client: Client, query: CallbackQuery):
    if not query.from_user or not _is_owner(query.from_user.id):
        await query.answer("Unauthorized: owner only.", show_alert=True)
        return
    data = query.data or ""
    try:
        if data == "in:close":
            await query.answer("Menu closed. Auto-deleting in 60s.")
            inline_id = query.inline_message_id
            msg = query.message
            chat_id = msg.chat.id if msg else None
            msg_id = msg.id if msg else None
            key = inline_id or f"{chat_id}:{msg_id}"

            closed_text = (
                "**TeleForge Control Center**\n"
                "• `Menu closed.`\n"
                "• Bubble will auto-delete in 60s for group cleanliness."
            )
            reopen_raw = [
                [
                    {"text": "« Open Again »", "callback_data": "in:reopen", "style": "success"},
                    {"text": "« Delete Now »", "callback_data": "in:purge", "style": "danger"},
                ]
            ]
            reopen_markup = _raw_to_markup(reopen_raw)
            await _update_settings_menu(client, query, closed_text, reopen_raw, reopen_markup)

            from modules.system import schedule_auto_clean
            asyncio.create_task(schedule_auto_clean(key, chat_id, msg_id, inline_id, delay=60.0))
            return

        if data == "in:reopen":
            inline_id = query.inline_message_id
            msg = query.message
            chat_id = msg.chat.id if msg else None
            msg_id = msg.id if msg else None
            key = inline_id or f"{chat_id}:{msg_id}"
            from modules.system import cancel_auto_clean
            cancel_auto_clean(key)
            text, raw_buttons, markup = _settings_text_and_markup()
            await _update_settings_menu(client, query, text, raw_buttons, markup)
            await query.answer("Control Center reopened.")
            return

        if data == "in:purge":
            inline_id = query.inline_message_id
            msg = query.message
            chat_id = msg.chat.id if msg else None
            msg_id = msg.id if msg else None
            key = inline_id or f"{chat_id}:{msg_id}"
            from modules.system import cancel_auto_clean, delete_tracked_message
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
                        await bot_client.edit_inline_text(inline_message_id=inline_id, text="`Deleted.`", reply_markup=None)
                except Exception:
                    pass
            return

        if data.startswith("in:tog:"):
            code = data.split(":", 2)[2]
            target_def = next((d for d in _TOGGLE_DEFS if d[0] == code), None)
            if not target_def:
                await query.answer("Unknown toggle.")
                return
            _, _, label, _ = target_def
            cur = _get_toggle_status(code)
            new_val = not cur
            _set_toggle_status(code, new_val)
            text, raw_buttons, markup = _settings_text_and_markup()
            await _update_settings_menu(client, query, text, raw_buttons, markup)
            await query.answer(f"{label}: {'ON' if new_val else 'OFF'}")
            return
        if data == "in:act:ping":
            dc_id = getattr(client, "session", None) and getattr(client.session, "dc_id", None)
            dc_str = f" • DC{dc_id}" if dc_id else ""
            await query.answer(f"Pong! Active{dc_str}", show_alert=True)
            return
        if data == "in:act:alive":
            uptime = format_uptime(time.time() - PyGramClient.START_TIME)
            total = len([c for c in PyGramClient.COMMANDS if not PyGramClient.COMMANDS[c].get("is_alias")])
            await query.answer(f"TeleForge Active\nUptime: {uptime}\nCommands: {total}", show_alert=True)
            return
        if data == "in:act:restart":
            await query.answer("Restarting TeleForge...", show_alert=True)
            try:
                from modules.system import _graceful_restart
                await _graceful_restart()
            except Exception as e:
                logger.error("Restart via settings error: %s", e)
            return
    except MessageNotModified:
        await query.answer()
    except Exception as e:
        logger.error("inline callback error: %s", e)
        await query.answer(f"Error: {e}", show_alert=True)
