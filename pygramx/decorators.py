import functools
import logging
import traceback
from typing import Union, List, Callable
from pyrogram import Client, filters
from pyrogram.types import Message
import config
from pygramx.client import PyGramClient
from pygramx.utils.message import edit_or_reply

logger = logging.getLogger("pygramx.cmd")

def _make_dual_cmd_filter(cmd_list: List[str]):
    cmd_set = {c.lower() for c in cmd_list}

    async def _filter_func(_, client: Client, message: Message):
        text = message.text or message.caption
        if not text:
            return False

        from pygramx.database import db

        custom_trigs = db.get("CUSTOM_TRIGGERS")
        if custom_trigs and isinstance(custom_trigs, list):
            valid_user_trigs = set(custom_trigs)
        else:
            valid_user_trigs = {config.get_trigger()}

        bot_trigger = config.get_bot_trigger()
        first_token = text.split(maxsplit=1)[0]
        used_prefix = None
        prefixes = sorted(valid_user_trigs | {bot_trigger}, key=len, reverse=True)
        for prefix in prefixes:
            if first_token.startswith(prefix):
                used_prefix = prefix
                break

        if not used_prefix:
            return False

        # Parse command word and optional @bot_username suffix
        cmd_parts = first_token[len(used_prefix):].split("@")
        cmd_word = cmd_parts[0].lower()

        resolved_cmd = cmd_word
        if cmd_word not in cmd_set:
            custom_aliases = db.get("CUSTOM_ALIASES") or {}
            resolved_cmd = custom_aliases.get(cmd_word, cmd_word)
            if resolved_cmd not in cmd_set:
                return False

        is_bot_client = bool(getattr(client, "bot_token", None)) or (
            getattr(client, "me", None) and client.me.is_bot
        )

        # Handle @bot_username targeting
        if len(cmd_parts) > 1 and cmd_parts[1]:
            target_bot_user = cmd_parts[1].lower()
            if is_bot_client:
                bot_me = getattr(client, "me", None)
                if bot_me and bot_me.username and bot_me.username.lower() != target_bot_user:
                    return False
            else:
                return False

        owner_id = getattr(PyGramClient, "OWNER_ID", None) or config.get_owner_id()

        if is_bot_client:
            # Companion bot: strictly enforce owner authorization
            if not message.from_user or not owner_id or message.from_user.id != owner_id:
                return False

            # Companion bot only responds to its configured bot_trigger
            if used_prefix != bot_trigger:
                return False

            return True
        else:
            # Userbot client: only respond to own account messages
            is_self_user = (
                (message.from_user and getattr(message.from_user, "is_self", False))
                or getattr(message, "outgoing", False)
            )
            if not is_self_user:
                return False

            # If companion bot is active and has distinct trigger, ignore bot_trigger commands
            if config.BOT_TOKEN and used_prefix == bot_trigger and used_prefix not in valid_user_trigs:
                return False

            if used_prefix not in valid_user_trigs:
                return False

            return True

    return filters.create(_filter_func)

MODULE_TO_CATEGORY = {
    "admin": "admin",
    "purge": "admin",
    "android": "android",
    "media": "media",
    "tts": "media",
    "pmpermit": "security",
    "session": "security",
    "stickers": "stickers",
    "system": "system",
    "tools": "tools",
    "context": "tools",
    "info": "tools",
    "afk": "tools",
    "network": "tools",
    "motorsport": "sport",
    "football": "sport",
    "racket": "sport",
    "weather": "weather",
    "finance": "finance",
    "pray": "prayer",
    "astronomy": "astronomy",
    "skyradar": "aviation",
    "seismo": "seismo",
    "transfer": "transfer",
    "uploader": "transfer",
    "inline": "system",
}

def on_cmd(
    cmd: Union[str, List[str]],
    desc: str = "",
    usage: str = "",
    category: Optional[str] = None,
):
    """
    Decorator for TeleForge commands.
    Supports dual-client routing: triggers on userbot (self) across all chats,
    and enables companion bot responses in private DM and the central LOG_CHAT group.
    """
    cmd_list = [cmd] if isinstance(cmd, str) else list(cmd)
    primary_cmd = cmd_list[0]
    aliases = cmd_list[1:] if len(cmd_list) > 1 else None

    def decorator(func: Callable):
        # Resolve category from explicit argument or function module
        mod_name = getattr(func, "__module__", "")
        raw_mod = mod_name.split(".")[-1] if mod_name else "system"
        cmd_cat = category or MODULE_TO_CATEGORY.get(raw_mod, raw_mod)

        # Register command metadata and aliases for dynamic help
        PyGramClient.register_command(
            primary_cmd, desc=desc, usage=usage, aliases=aliases, category=cmd_cat, module=mod_name
        )
        @functools.wraps(func)
        async def wrapper(client: Client, message: Message, *args, **kwargs):
            try:
                await func(client, message, *args, **kwargs)
            except Exception as e:
                logger.exception("Error executing command '%s': %s", primary_cmd, e)
                tb = traceback.format_exc()
                error_text = (
                    f"**Command Error:** `{primary_cmd}`\n"
                    f"```\n{tb}\n```"
                )
                await edit_or_reply(message, error_text)

        # Attach dual-client on_message handler
        cmd_filter = _make_dual_cmd_filter(cmd_list)
        Client.on_message(cmd_filter)(wrapper)

        if hasattr(wrapper, "handlers") and wrapper.handlers:
            handler = wrapper.handlers[0][0]
            PyGramClient.MODULE_HANDLERS.setdefault(mod_name, []).append(handler)

            for cl in [PyGramClient.USERBOT_CLIENT, PyGramClient.BOT_CLIENT]:
                if cl and hasattr(cl, "add_handler") and getattr(cl, "is_connected", False):
                    try:
                        cl.add_handler(handler)
                    except Exception as e:
                        logger.debug("Failed attaching live handler: %s", e)

        return wrapper

    return decorator
