import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from datetime import datetime, timezone
from pyrogram import Client
from pyrogram.enums import ChatType, UserStatus
from pyrogram.types import Message, User, Chat
from pygramx import on_cmd
from pygramx.utils import edit_or_reply, get_client_prefix


def _format_user_status(user: User) -> str:
    status = getattr(user, "status", None)
    if not status:
        return "Unknown"
    if status == UserStatus.ONLINE:
        return "Online"
    elif status == UserStatus.OFFLINE:
        if getattr(user, "last_online_date", None):
            dt = datetime.fromtimestamp(user.last_online_date, timezone.utc)
            return f"Offline ({dt.strftime('%Y-%m-%d %H:%M UTC')})"
        return "Offline"
    elif status == UserStatus.RECENTLY:
        return "Recently"
    elif status == UserStatus.LAST_WEEK:
        return "Within last week"
    elif status == UserStatus.LAST_MONTH:
        return "Within last month"
    elif status == UserStatus.LONG_AGO:
        return "Long time ago"
    return str(status).split(".")[-1].capitalize()


async def _format_user_card(client: Client, user: User) -> str:
    # Attempt to retrieve chat profile for bio & complete DC metadata
    bio = None
    dc_id = getattr(user, "dc_id", None)
    try:
        full_chat = await client.get_chat(user.id)
        if full_chat:
            bio = getattr(full_chat, "bio", None) or getattr(full_chat, "description", None)
            if not dc_id:
                dc_id = getattr(full_chat, "dc_id", None)
    except Exception:
        pass

    first_name = user.first_name or ""
    last_name = f" {user.last_name}" if user.last_name else ""
    full_name = (first_name + last_name).strip() or "Unnamed"
    username = f"@{user.username}" if user.username else "None"
    dc_str = f"DC {dc_id}" if dc_id else "Unknown"
    is_premium = "Yes" if getattr(user, "is_premium", False) else "No"
    is_bot = "Yes" if getattr(user, "is_bot", False) else "No"
    is_verified = "Yes" if getattr(user, "is_verified", False) else "No"
    status_str = _format_user_status(user)
    profile_link = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"

    flags = []
    if getattr(user, "is_scam", False):
        flags.append("Scam")
    if getattr(user, "is_fake", False):
        flags.append("Fake")
    if getattr(user, "is_restricted", False):
        flags.append("Restricted")
    flag_line = f"• **Flags:** `{', '.join(flags)}`\n" if flags else ""

    bio_line = f"• **Bio:** `{bio}`\n" if bio else ""

    return (
        "**User Info**\n"
        f"• **Name:** {full_name}\n"
        f"• **User ID:** `{user.id}`\n"
        f"• **Username:** {username}\n"
        f"• **Status:** `{status_str}`\n"
        f"• **Data Center:** `{dc_str}`\n"
        f"• **Verified:** `{is_verified}`\n"
        f"• **Premium:** `{is_premium}`\n"
        f"• **Bot:** `{is_bot}`\n"
        f"{flag_line}"
        f"{bio_line}"
        f"• **Profile:** [Direct Link]({profile_link})"
    )


async def _format_chat_card(client: Client, chat: Chat) -> str:
    title = chat.title or "Unknown"
    username = f"@{chat.username}" if chat.username else "None (Private)"
    chat_type = getattr(chat.type, "name", str(chat.type)).replace("_", " ").title()
    dc_str = f"DC {chat.dc_id}" if getattr(chat, "dc_id", None) else "Unknown"

    members_count = getattr(chat, "members_count", None)
    if members_count is None:
        try:
            members_count = await client.get_chat_members_count(chat.id)
        except Exception:
            members_count = None
    members_str = f"`{members_count:,}`" if members_count is not None else "`Unavailable`"

    desc = getattr(chat, "description", None) or getattr(chat, "bio", None)
    desc_line = f"• **Description:** `{desc}`\n" if desc else ""

    protected = "Yes" if getattr(chat, "has_protected_content", False) else "No"
    slowmode = getattr(chat, "slow_mode_delay", None)
    slow_str = f"`{slowmode}s`" if slowmode else "`Disabled`"

    linked = getattr(chat, "linked_chat", None)
    linked_line = ""
    if linked:
        l_title = getattr(linked, "title", "Linked Chat")
        l_id = getattr(linked, "id", "")
        linked_line = f"• **Linked Chat:** {l_title} (`{l_id}`)\n"

    link = getattr(chat, "invite_link", None)
    if not link and chat.username:
        link = f"https://t.me/{chat.username}"
    link_line = f"• **Link:** [Open Chat]({link})\n" if link else ""

    return (
        "**Chat Info**\n"
        f"• **Title:** {title}\n"
        f"• **Chat ID:** `{chat.id}`\n"
        f"• **Type:** `{chat_type}`\n"
        f"• **Username:** {username}\n"
        f"• **Members:** {members_str}\n"
        f"• **Data Center:** `{dc_str}`\n"
        f"• **Slow Mode:** {slow_str}\n"
        f"• **Protected Content:** `{protected}`\n"
        f"{linked_line}"
        f"{desc_line}"
        f"{link_line}".rstrip()
    )


@on_cmd("id", desc="Display current chat and user identifier", usage="")
async def id_cmd(client: Client, message: Message):
    chat_type = getattr(message.chat.type, "name", str(message.chat.type)).replace("_", " ").title()
    lines = [
        f"• **Chat ID:** `{message.chat.id}`",
        f"• **Chat Type:** `{chat_type}`",
    ]

    if getattr(message, "message_thread_id", None):
        lines.append(f"• **Topic ID:** `{message.message_thread_id}`")

    if message.reply_to_message:
        reply = message.reply_to_message
        lines.append(f"• **Replied Message ID:** `{reply.id}`")
        if reply.from_user:
            lines.append(f"• **Replied User ID:** `{reply.from_user.id}`")
        elif reply.sender_chat:
            lines.append(f"• **Replied Sender ID:** `{reply.sender_chat.id}`")

        if reply.forward_from:
            lines.append(f"• **Forwarded User ID:** `{reply.forward_from.id}`")
        elif reply.forward_from_chat:
            lines.append(f"• **Forwarded Chat ID:** `{reply.forward_from_chat.id}`")

    my_user = message.from_user
    if not my_user:
        try:
            my_user = await client.get_me()
        except Exception:
            pass
    if my_user:
        lines.append(f"• **Account ID:** `{my_user.id}`")

    await edit_or_reply(message, "**ID Details:**\n" + "\n".join(lines))


@on_cmd(["info", "whois"], desc="Display detailed profile or chat information", usage="[@username/id]")
async def info_cmd(client: Client, message: Message):
    # Case 1: Replied message target
    if message.reply_to_message:
        reply = message.reply_to_message
        if reply.from_user:
            try:
                target_user = await client.get_users(reply.from_user.id)
            except Exception:
                target_user = reply.from_user
            card = await _format_user_card(client, target_user)
            return await edit_or_reply(message, card)
        elif reply.sender_chat:
            try:
                target_chat = await client.get_chat(reply.sender_chat.id)
            except Exception:
                target_chat = reply.sender_chat
            card = await _format_chat_card(client, target_chat)
            return await edit_or_reply(message, card)

    # Case 2: Argument specified
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    if len(args) > 1:
        raw_query = args[1].strip()
        query = int(raw_query) if raw_query.lstrip("-").isdigit() else raw_query

        # Try user lookup first
        try:
            target_user = await client.get_users(query)
            if target_user:
                card = await _format_user_card(client, target_user)
                return await edit_or_reply(message, card)
        except Exception:
            pass

        # Try chat lookup
        try:
            target_chat = await client.get_chat(query)
            if target_chat:
                card = await _format_chat_card(client, target_chat)
                return await edit_or_reply(message, card)
        except Exception:
            pass

        return await edit_or_reply(message, f"No user or chat found matching `{raw_query}`.")

    # Case 3: No argument and no reply -> inspect self
    self_user = message.from_user
    if not self_user:
        try:
            self_user = await client.get_me()
        except Exception:
            pass

    if self_user:
        try:
            full_self = await client.get_users(self_user.id)
        except Exception:
            full_self = self_user
        card = await _format_user_card(client, full_self)
        return await edit_or_reply(message, card)

    await edit_or_reply(message, "Unable to resolve user information.")


@on_cmd(["chatinfo", "cinfo", "chat"], desc="Display detailed information about a chat or channel", usage="[chat_id|@username]")
async def chatinfo_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)

    target_id = None
    if len(args) > 1:
        raw_target = args[1].strip()
        target_id = int(raw_target) if raw_target.lstrip("-").isdigit() else raw_target
    elif message.reply_to_message and message.reply_to_message.sender_chat:
        target_id = message.reply_to_message.sender_chat.id
    else:
        target_id = message.chat.id

    status_msg = await edit_or_reply(message, "`Fetching chat details...`")

    try:
        chat = await client.get_chat(target_id)
        card = await _format_chat_card(client, chat)
        await edit_or_reply(status_msg, card)
    except Exception as e:
        await edit_or_reply(status_msg, f"Failed to retrieve chat info: `{e}`")
