import asyncio
import io
import logging
from typing import Optional, Any
from pyrogram.types import Message
from pyrogram.errors import FloodWait, MessageNotModified

logger = logging.getLogger("pygramx.msg")

MAX_MESSAGE_LENGTH = 4096

def get_client_prefix(client: Any) -> str:
    """
    Determine active command prefix for client context.
    Returns dynamic bot trigger for companion bot instances, or dynamic trigger for userbot.
    """
    import config
    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and getattr(client.me, "is_bot", False)
    )
    return config.get_bot_trigger() if is_bot else config.get_trigger()


async def edit_or_reply(
    message: Message,
    text: str,
    disable_web_page_preview: bool = True,
    parse_mode: Optional[Any] = None,
    reply_markup: Optional[Any] = None,
) -> Message:
    """
    Safely edit outgoing message or reply if cannot edit.
    Handles Telegram 4096 character limits and FloodWait retries.
    """
    if len(text) > MAX_MESSAGE_LENGTH:
        truncated = text[: MAX_MESSAGE_LENGTH - 50]
        if truncated.replace("```", "").count("`") % 2 != 0:
            truncated += "`"
        if truncated.count("```") % 2 != 0:
            text = truncated + "\n```\n... [output truncated]"
        else:
            text = truncated + "\n\n... [output truncated]"

    kwargs = {
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode is not None:
        kwargs["parse_mode"] = parse_mode
    if reply_markup is not None:
        if isinstance(reply_markup, list):
            try:
                from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                if reply_markup and isinstance(reply_markup[0], list):
                    converted = []
                    for row in reply_markup:
                        new_row = []
                        for item in row:
                            if isinstance(item, dict):
                                new_row.append(
                                    InlineKeyboardButton(
                                        item.get("text", ""),
                                        callback_data=item.get("callback_data"),
                                        url=item.get("url"),
                                    )
                                )
                            else:
                                new_row.append(item)
                        converted.append(new_row)
                    reply_markup = InlineKeyboardMarkup(converted)
                else:
                    reply_markup = InlineKeyboardMarkup(reply_markup)
            except Exception:
                pass
        kwargs["reply_markup"] = reply_markup


    while True:
        try:
            if getattr(message, "outgoing", False) or (
                message.from_user and getattr(message.from_user, "is_self", False)
            ):
                try:
                    return await message.edit_text(**kwargs)
                except MessageNotModified:
                    return message
                except Exception as edit_err:
                    err_lower = str(edit_err).lower()
                    if any(x in err_lower for x in ("author", "cant_edit", "cannot_edit", "invalid", "deleted", "channel")):
                        return await message.reply_text(**kwargs)
                    raise edit_err
            return await message.reply_text(**kwargs)
        except FloodWait as e:
            logger.warning("FloodWait: Sleeping for %s seconds", e.value)
            await asyncio.sleep(e.value)
        except MessageNotModified:
            return message
        except Exception as e:
            err_str = str(e).lower()
            if "entity" in err_str or "parse" in err_str or "markdown" in err_str:
                try:
                    from pyrogram.enums import ParseMode
                    kwargs_plain = dict(kwargs)
                    kwargs_plain["parse_mode"] = ParseMode.DISABLED
                    if getattr(message, "outgoing", False) or (
                        message.from_user and getattr(message.from_user, "is_self", False)
                    ):
                        return await message.edit_text(**kwargs_plain)
                    return await message.reply_text(**kwargs_plain)
                except Exception:
                    pass
            logger.error("Failed to edit or reply: %s", e)
            raise e

async def send_large_output(
    message: Message,
    content: str,
    caption: str = "",
    filename: str = "output.txt",
) -> Message:
    """
    Intelligently deliver output: inline if <= 4000 characters,
    or as an attached text document if exceeding limits.
    """
    if len(content) <= 3900:
        body = f"{caption}\n```\n{content}\n```" if caption else f"```\n{content}\n```"
        return await edit_or_reply(message, body)

    # Output exceeds inline limit: deliver as clean document file
    bio = io.BytesIO(content.encode("utf-8"))
    bio.name = filename
    doc_caption = caption if caption else f"Output exceeds 4000 chars ({len(content)} bytes)."

    client = message._client
    chat_id = message.chat.id
    reply_to = message.reply_to_message.id if message.reply_to_message else (
        message.id if not getattr(message, "outgoing", False) else None
    )

    # Delete progress message if we edited our own
    try:
        if getattr(message, "outgoing", False) or (
            message.from_user and getattr(message.from_user, "is_self", False)
        ):
            await message.delete()
    except Exception:
        pass

    return await client.send_document(
        chat_id=chat_id,
        document=bio,
        caption=doc_caption,
        reply_to_message_id=reply_to,
    )
