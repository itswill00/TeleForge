import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from datetime import datetime, timezone, timedelta
import logging
from typing import Optional, Set, Tuple
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus, ChatMembersFilter
from pyrogram.errors import FloodWait, ChatAdminRequired, UserAdminInvalid, RPCError
from pyrogram.types import Message, User, ChatPermissions, ChatPrivileges
from pygramx import on_cmd, db
from pygramx.client import PyGramClient
from pygramx.utils import edit_or_reply, get_client_prefix

logger = logging.getLogger("pygramx.admin")

_ACTIVE_TAG_CHATS: Set[int] = set()

async def _check_admin_privilege(client: Client, chat_id: int, perm_name: str) -> bool:
    """Verify whether client has the requested admin privilege in the chat."""
    try:
        me = await client.get_chat_member(chat_id, "me")
        if me.status == ChatMemberStatus.OWNER:
            return True
        if me.status == ChatMemberStatus.ADMINISTRATOR:
            if not perm_name:
                return True
            privs = getattr(me, "privileges", None)
            return bool(privs and getattr(privs, perm_name, False))
        return False
    except Exception as e:
        logger.debug("Failed to verify admin status: %s", e)
        return False

async def _resolve_target(client: Client, message: Message) -> Tuple[Optional[User], str]:
    """Resolve target user from replied message or argument, and return remaining text."""
    reply = message.reply_to_message
    text = message.text or message.caption or ""
    parts = text.split(maxsplit=1)

    if reply and reply.from_user:
        extra = parts[1].strip() if len(parts) > 1 else ""
        return reply.from_user, extra

    if len(parts) > 1:
        subparts = text.split(maxsplit=2)
        raw_target = subparts[1]
        extra = subparts[2].strip() if len(subparts) > 2 else ""
        user_query = int(raw_target) if raw_target.lstrip("-").isdigit() else raw_target
        try:
            target_user = await client.get_users(user_query)
            return target_user, extra
        except Exception:
            return None, ""

    return None, ""

def _parse_time_and_reason(extra_str: str) -> Tuple[Optional[datetime], str, str]:
    """Parse time string (10m, 2h, 1d) into datetime and extract reason."""
    if not extra_str:
        return None, "Permanent", ""

    tokens = extra_str.split()
    first_token = tokens[0].lower()
    duration_sec = None
    human = "Permanent"
    reason = extra_str

    if len(first_token) >= 2:
        val = first_token[:-1]
        unit = first_token[-1]
        if val.isdigit():
            num = int(val)
            if unit == "m":
                duration_sec = num * 60
                human = f"{num} minute(s)"
                reason = " ".join(tokens[1:])
            elif unit == "h":
                duration_sec = num * 3600
                human = f"{num} hour(s)"
                reason = " ".join(tokens[1:])
            elif unit == "d":
                duration_sec = num * 86400
                human = f"{num} day(s)"
                reason = " ".join(tokens[1:])
            elif unit == "w":
                duration_sec = num * 604800
                human = f"{num} week(s)"
                reason = " ".join(tokens[1:])

    until_date = None
    if duration_sec:
        until_date = datetime.now(timezone.utc) + timedelta(seconds=duration_sec)

    return until_date, human, reason.strip()

@on_cmd(["ban", "b"], desc="Ban user from the chat", usage="[reply|@user|id] [reason]")
async def ban_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_ban = await _check_admin_privilege(client, message.chat.id, "can_restrict_members")
    if not can_ban:
        return await edit_or_reply(message, "You need administrator permission to ban members in this chat.")

    reply = message.reply_to_message
    if reply and reply.sender_chat and not reply.from_user:
        try:
            await client.ban_chat_member(message.chat.id, reply.sender_chat.id)
            return await edit_or_reply(
                message,
                f"**Banned Channel**\n• **Channel:** {reply.sender_chat.title} (`{reply.sender_chat.id}`)"
            )
        except Exception as e:
            return await edit_or_reply(message, f"Failed to ban channel: `{e}`")

    target, reason = await _resolve_target(client, message)
    if not target:
        return await edit_or_reply(message, "Reply to a user or specify @username/id to ban.")

    if message.from_user and target.id == message.from_user.id:
        return await edit_or_reply(message, "You cannot ban yourself.")

    try:
        await client.ban_chat_member(message.chat.id, target.id)
        card = (
            f"**Banned User**\n"
            f"• **User:** {target.mention(target.first_name)} (`{target.id}`)"
        )
        if reason:
            card += f"\n• **Reason:** {reason}"
        await edit_or_reply(message, card)
    except UserAdminInvalid:
        await edit_or_reply(message, "Cannot ban this user because they are an administrator or owner.")
    except RPCError as e:
        await edit_or_reply(message, f"Failed to ban user: `{e}`")

@on_cmd(["unban", "ub"], desc="Unban user from the chat", usage="[reply|@user|id]")
async def unban_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_unban = await _check_admin_privilege(client, message.chat.id, "can_restrict_members")
    if not can_unban:
        return await edit_or_reply(message, "You need administrator permission to unban members in this chat.")

    reply = message.reply_to_message
    if reply and reply.sender_chat and not reply.from_user:
        try:
            await client.unban_chat_member(message.chat.id, reply.sender_chat.id)
            return await edit_or_reply(
                message,
                f"**Unbanned Channel**\n• **Channel:** {reply.sender_chat.title} (`{reply.sender_chat.id}`)"
            )
        except Exception as e:
            return await edit_or_reply(message, f"Failed to unban channel: `{e}`")

    target, _ = await _resolve_target(client, message)
    if not target:
        return await edit_or_reply(message, "Reply to a user or specify @username/id to unban.")

    try:
        await client.unban_chat_member(message.chat.id, target.id)
        await edit_or_reply(
            message,
            f"**Unbanned User**\n• **User:** {target.mention(target.first_name)} (`{target.id}`)"
        )
    except RPCError as e:
        await edit_or_reply(message, f"Failed to unban user: `{e}`")

@on_cmd(["kick", "k"], desc="Kick user from the chat", usage="[reply|@user|id] [reason]")
async def kick_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_kick = await _check_admin_privilege(client, message.chat.id, "can_restrict_members")
    if not can_kick:
        return await edit_or_reply(message, "You need administrator permission to kick members in this chat.")

    reply = message.reply_to_message
    if reply and reply.sender_chat and not reply.from_user:
        return await edit_or_reply(message, "Channels cannot be kicked individually. Use `.ban` to block this channel.")

    target, reason = await _resolve_target(client, message)
    if not target:
        return await edit_or_reply(message, "Reply to a user or specify @username/id to kick.")

    if message.from_user and target.id == message.from_user.id:
        return await edit_or_reply(message, "You cannot kick yourself.")

    try:
        await client.ban_chat_member(message.chat.id, target.id)
        await client.unban_chat_member(message.chat.id, target.id)
        card = (
            f"**Kicked User**\n"
            f"• **User:** {target.mention(target.first_name)} (`{target.id}`)"
        )
        if reason:
            card += f"\n• **Reason:** {reason}"
        await edit_or_reply(message, card)
    except UserAdminInvalid:
        await edit_or_reply(message, "Cannot kick this user because they are an administrator or owner.")
    except RPCError as e:
        await edit_or_reply(message, f"Failed to kick user: `{e}`")

@on_cmd(["mute", "m"], desc="Mute user from sending messages", usage="[reply|@user|id] [time] [reason]")
async def mute_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_mute = await _check_admin_privilege(client, message.chat.id, "can_restrict_members")
    if not can_mute:
        return await edit_or_reply(message, "You need administrator permission to restrict members in this chat.")

    reply = message.reply_to_message
    if reply and reply.sender_chat and not reply.from_user:
        return await edit_or_reply(message, "Channels cannot be muted individually. Use `.ban` to block this channel.")

    target, extra = await _resolve_target(client, message)
    if not target:
        return await edit_or_reply(message, "Reply to a user or specify @username/id to mute.")

    if message.from_user and target.id == message.from_user.id:
        return await edit_or_reply(message, "You cannot mute yourself.")

    until_date, human_time, reason = _parse_time_and_reason(extra)
    try:
        kwargs = {"permissions": ChatPermissions(can_send_messages=False)}
        if until_date:
            kwargs["until_date"] = until_date
        await client.restrict_chat_member(message.chat.id, target.id, **kwargs)
        card = (
            f"**Muted User**\n"
            f"• **User:** {target.mention(target.first_name)} (`{target.id}`)\n"
            f"• **Duration:** {human_time}"
        )
        if reason:
            card += f"\n• **Reason:** {reason}"
        await edit_or_reply(message, card)
    except UserAdminInvalid:
        await edit_or_reply(message, "Cannot mute this user because they are an administrator or owner.")
    except RPCError as e:
        await edit_or_reply(message, f"Failed to mute user: `{e}`")

@on_cmd(["unmute", "um"], desc="Lift message restriction from user", usage="[reply|@user|id]")
async def unmute_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_unmute = await _check_admin_privilege(client, message.chat.id, "can_restrict_members")
    if not can_unmute:
        return await edit_or_reply(message, "You need administrator permission to lift restrictions in this chat.")

    target, _ = await _resolve_target(client, message)
    if not target:
        return await edit_or_reply(message, "Reply to a user or specify @username/id to unmute.")

    try:
        chat = await client.get_chat(message.chat.id)
        perms = chat.permissions or ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_other_messages=True,
            can_send_polls=True,
            can_add_web_page_previews=True,
            can_invite_users=True,
        )
        await client.restrict_chat_member(message.chat.id, target.id, permissions=perms)
        await edit_or_reply(
            message,
            f"**Unmuted User**\n• **User:** {target.mention(target.first_name)} (`{target.id}`)"
        )
    except RPCError as e:
        await edit_or_reply(message, f"Failed to unmute user: `{e}`")

@on_cmd("pin", desc="Pin replied message in the chat", usage="[loud|notify|silent]")
async def pin_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_pin = await _check_admin_privilege(client, message.chat.id, "can_pin_messages")
    if not can_pin:
        return await edit_or_reply(message, "You need administrator permission to pin messages in this chat.")

    reply = message.reply_to_message
    if not reply:
        return await edit_or_reply(message, "Reply to a message to pin it.")

    args = (message.text or message.caption or "").split()
    notify = len(args) > 1 and args[1].lower() in ("loud", "notify", "alert")

    try:
        await client.pin_chat_message(message.chat.id, reply.id, disable_notification=not notify)
        mode_str = "with notification" if notify else "silently"
        await edit_or_reply(message, f"Pinned message (`{reply.id}`) {mode_str}.")
    except RPCError as e:
        await edit_or_reply(message, f"Failed to pin message: `{e}`")

@on_cmd("unpin", desc="Unpin message in the chat", usage="[all]")
async def unpin_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_unpin = await _check_admin_privilege(client, message.chat.id, "can_pin_messages")
    if not can_unpin:
        return await edit_or_reply(message, "You need administrator permission to unpin messages in this chat.")

    args = (message.text or message.caption or "").split()
    if len(args) > 1 and args[1].lower() == "all":
        try:
            await client.unpin_all_chat_messages(message.chat.id)
            return await edit_or_reply(message, "Unpinned all messages in this chat.")
        except RPCError as e:
            return await edit_or_reply(message, f"Failed to unpin all messages: `{e}`")

    reply = message.reply_to_message
    target_id = reply.id if reply else 0

    try:
        await client.unpin_chat_message(message.chat.id, message_id=target_id)
        if target_id:
            await edit_or_reply(message, f"Unpinned message (`{target_id}`).")
        else:
            await edit_or_reply(message, "Unpinned latest pinned message.")
    except RPCError as e:
        await edit_or_reply(message, f"Failed to unpin message: `{e}`")

@on_cmd("promote", desc="Promote user to administrator", usage="[reply|@user|id] [title]")
async def promote_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_promote = await _check_admin_privilege(client, message.chat.id, "can_promote_members")
    if not can_promote:
        return await edit_or_reply(message, "You need administrator permission to promote members in this chat.")

    target, title = await _resolve_target(client, message)
    if not target:
        return await edit_or_reply(message, "Reply to a user or specify @username/id to promote.")

    privileges = ChatPrivileges(
        can_change_info=True,
        can_delete_messages=True,
        can_restrict_members=True,
        can_invite_users=True,
        can_pin_messages=True,
        can_manage_video_chats=True,
    )

    try:
        await client.promote_chat_member(message.chat.id, target.id, privileges=privileges)
        if title:
            try:
                await client.set_administrator_title(message.chat.id, target.id, title[:16])
            except Exception:
                pass
        card = f"**Promoted Member**\n• **Admin:** {target.mention(target.first_name)} (`{target.id}`)"
        if title:
            card += f"\n• **Title:** `{title[:16]}`"
        await edit_or_reply(message, card)
    except RPCError as e:
        await edit_or_reply(message, f"Failed to promote member: `{e}`")

@on_cmd("demote", desc="Demote administrator back to member", usage="[reply|@user|id]")
async def demote_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_promote = await _check_admin_privilege(client, message.chat.id, "can_promote_members")
    if not can_promote:
        return await edit_or_reply(message, "You need administrator permission to demote members in this chat.")

    target, _ = await _resolve_target(client, message)
    if not target:
        return await edit_or_reply(message, "Reply to a user or specify @username/id to demote.")

    privileges = ChatPrivileges(
        can_change_info=False,
        can_delete_messages=False,
        can_restrict_members=False,
        can_invite_users=False,
        can_pin_messages=False,
        can_manage_video_chats=False,
        can_promote_members=False,
    )

    try:
        await client.promote_chat_member(message.chat.id, target.id, privileges=privileges)
        await edit_or_reply(
            message,
            f"**Demoted Admin**\n• **User:** {target.mention(target.first_name)} (`{target.id}`)"
        )
    except RPCError as e:
        await edit_or_reply(message, f"Failed to demote member: `{e}`")

@on_cmd(["title", "settitle"], desc="Set custom title for an administrator", usage="[reply|@user|id] <title>")
async def title_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_promote = await _check_admin_privilege(client, message.chat.id, "can_promote_members")
    if not can_promote:
        return await edit_or_reply(message, "You need administrator permission to modify admin titles.")

    target, new_title = await _resolve_target(client, message)
    if not target or not new_title:
        return await edit_or_reply(message, "Usage: reply or specify user and title: `.title <user> <title>`")

    try:
        await client.set_administrator_title(message.chat.id, target.id, new_title[:16])
        await edit_or_reply(
            message,
            f"**Admin Title Updated**\n"
            f"• **Admin:** {target.mention(target.first_name)} (`{target.id}`)\n"
            f"• **New Title:** `{new_title[:16]}`"
        )
    except RPCError as e:
        await edit_or_reply(message, f"Failed to set admin title: `{e}`")

@on_cmd(["admins", "staff"], desc="List chat administrators and owner", usage="")
async def admins_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    status_msg = await edit_or_reply(message, "`Fetching group administrators...`")
    admins = []
    bots = []
    owner = None

    try:
        async for member in client.get_chat_members(message.chat.id, filter=ChatMembersFilter.ADMINISTRATORS):
            if member.status == ChatMemberStatus.OWNER:
                owner = member
            elif member.user and member.user.is_bot:
                bots.append(member)
            else:
                admins.append(member)
    except RPCError as e:
        return await edit_or_reply(status_msg, f"Failed to retrieve administrators: `{e}`")

    total = len(admins) + len(bots) + (1 if owner else 0)
    lines = [
        f"**Chat Administrators ({total})**",
    ]

    if owner and owner.user:
        u = owner.user
        title_str = f" [{owner.custom_title}]" if getattr(owner, "custom_title", None) else ""
        lines.append(f"\n• **Owner:**\n  {u.mention(u.first_name)}{title_str} (`{u.id}`)")

    if admins:
        lines.append(f"\n• **Admins ({len(admins)}):**")
        for m in admins:
            u = m.user
            if u:
                title_str = f" [{m.custom_title}]" if getattr(m, "custom_title", None) else ""
                lines.append(f"  - {u.mention(u.first_name)}{title_str} (`{u.id}`)")

    if bots:
        lines.append(f"\n• **Bots ({len(bots)}):**")
        for m in bots:
            u = m.user
            if u:
                lines.append(f"  - {u.mention(u.first_name)} (`{u.id}`)")

    await edit_or_reply(status_msg, "\n".join(lines))

@on_cmd("warn", desc="Issue warning to user (3 warnings = auto ban)", usage="[reply|@user|id] [reason]")
async def warn_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_ban = await _check_admin_privilege(client, message.chat.id, "can_restrict_members")
    if not can_ban:
        return await edit_or_reply(message, "You need administrator permission to warn members.")

    target, reason = await _resolve_target(client, message)
    if not target:
        return await edit_or_reply(message, "Reply to a user or specify @username/id to warn.")

    if message.from_user and target.id == message.from_user.id:
        return await edit_or_reply(message, "You cannot warn yourself.")

    warn_key = f"WARNS_{message.chat.id}_{target.id}"
    warns = db.get(warn_key, [])
    if not isinstance(warns, list):
        warns = []

    entry = reason if reason else "No reason specified"
    warns.append(entry)
    db.set(warn_key, warns)

    count = len(warns)
    limit = 3
    if count >= limit:
        db.delete(warn_key)
        try:
            await client.ban_chat_member(message.chat.id, target.id)
            await edit_or_reply(
                message,
                f"**Warning Limit Exceeded ({count}/{limit})**\n"
                f"• **User:** {target.mention(target.first_name)} (`{target.id}`)\n"
                f"• **Action:** Banned from chat automatically."
            )
        except Exception as e:
            await edit_or_reply(
                message,
                f"**Warning Limit Exceeded ({count}/{limit})**\n"
                f"• **User:** {target.mention(target.first_name)} (`{target.id}`)\n"
                f"• **Error banning user:** `{e}`"
            )
    else:
        await edit_or_reply(
            message,
            f"**User Warned ({count}/{limit})**\n"
            f"• **User:** {target.mention(target.first_name)} (`{target.id}`)\n"
            f"• **Reason:** {entry}"
        )

@on_cmd("warns", desc="View active warnings for user", usage="[reply|@user|id]")
async def warns_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    target, _ = await _resolve_target(client, message)
    if not target:
        target = message.from_user

    if not target:
        return await edit_or_reply(message, "Specify user or reply to check warnings.")

    warn_key = f"WARNS_{message.chat.id}_{target.id}"
    warns = db.get(warn_key, [])
    if not isinstance(warns, list) or not warns:
        return await edit_or_reply(message, f"User {target.mention(target.first_name)} has `0/3` warnings.")

    lines = [
        f"**Active Warnings for {target.mention(target.first_name)} ({len(warns)}/3):**"
    ]
    for i, w in enumerate(warns, 1):
        lines.append(f"{i}. {w}")
    await edit_or_reply(message, "\n".join(lines))

@on_cmd(["resetwarns", "unwarn", "rmwarn"], desc="Reset warnings for user", usage="[reply|@user|id]")
async def resetwarns_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_ban = await _check_admin_privilege(client, message.chat.id, "can_restrict_members")
    if not can_ban:
        return await edit_or_reply(message, "You need administrator permission to reset warnings.")

    target, _ = await _resolve_target(client, message)
    if not target:
        return await edit_or_reply(message, "Reply to a user or specify @username/id to reset warnings.")

    warn_key = f"WARNS_{message.chat.id}_{target.id}"
    db.delete(warn_key)
    await edit_or_reply(message, f"Reset all warnings for {target.mention(target.first_name)}.")

@on_cmd("lock", desc="Lock chat permissions for members", usage="<messages|media|stickers|polls|invites|all>")
async def lock_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_change = await _check_admin_privilege(client, message.chat.id, "can_change_info")
    if not can_change:
        return await edit_or_reply(message, "You need administrator permission to configure chat permissions.")

    args = (message.text or message.caption or "").split()
    if len(args) < 2:
        return await edit_or_reply(
            message,
            "Specify what to lock: `messages`, `media`, `stickers`, `polls`, `invites`, `previews`, or `all`."
        )

    target_lock = args[1].lower()
    chat = await client.get_chat(message.chat.id)
    cur = chat.permissions or ChatPermissions()

    kwargs = {
        "can_send_messages": cur.can_send_messages,
        "can_send_media_messages": cur.can_send_media_messages,
        "can_send_other_messages": cur.can_send_other_messages,
        "can_send_polls": cur.can_send_polls,
        "can_add_web_page_previews": cur.can_add_web_page_previews,
        "can_change_info": cur.can_change_info,
        "can_invite_users": cur.can_invite_users,
        "can_pin_messages": cur.can_pin_messages,
    }

    if target_lock in ("all", "chat"):
        for k in kwargs:
            kwargs[k] = False
        desc = "all permissions"
    elif target_lock in ("messages", "msg"):
        kwargs["can_send_messages"] = False
        desc = "messages"
    elif target_lock in ("media", "photos", "videos"):
        kwargs["can_send_media_messages"] = False
        desc = "media"
    elif target_lock in ("stickers", "sticker", "gifs", "gif"):
        kwargs["can_send_other_messages"] = False
        desc = "stickers and GIFs"
    elif target_lock in ("polls", "poll"):
        kwargs["can_send_polls"] = False
        desc = "polls"
    elif target_lock in ("invites", "invite"):
        kwargs["can_invite_users"] = False
        desc = "member invites"
    elif target_lock in ("previews", "links"):
        kwargs["can_add_web_page_previews"] = False
        desc = "web page previews"
    else:
        return await edit_or_reply(message, "Invalid lock option. Valid: `messages`, `media`, `stickers`, `polls`, `invites`, `previews`, `all`.")

    try:
        new_perms = ChatPermissions(**kwargs)
        await client.set_chat_permissions(message.chat.id, new_perms)
        await edit_or_reply(message, f"**Chat Permissions Updated**\n• Locked `{desc}` for normal members.")
    except RPCError as e:
        await edit_or_reply(message, f"Failed to lock permissions: `{e}`")

@on_cmd("unlock", desc="Unlock chat permissions for members", usage="<messages|media|stickers|polls|invites|all>")
async def unlock_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_change = await _check_admin_privilege(client, message.chat.id, "can_change_info")
    if not can_change:
        return await edit_or_reply(message, "You need administrator permission to configure chat permissions.")

    args = (message.text or message.caption or "").split()
    if len(args) < 2:
        return await edit_or_reply(
            message,
            "Specify what to unlock: `messages`, `media`, `stickers`, `polls`, `invites`, `previews`, or `all`."
        )

    target_lock = args[1].lower()
    chat = await client.get_chat(message.chat.id)
    cur = chat.permissions or ChatPermissions()

    kwargs = {
        "can_send_messages": cur.can_send_messages,
        "can_send_media_messages": cur.can_send_media_messages,
        "can_send_other_messages": cur.can_send_other_messages,
        "can_send_polls": cur.can_send_polls,
        "can_add_web_page_previews": cur.can_add_web_page_previews,
        "can_change_info": cur.can_change_info,
        "can_invite_users": cur.can_invite_users,
        "can_pin_messages": cur.can_pin_messages,
    }

    if target_lock in ("all", "chat"):
        kwargs["can_send_messages"] = True
        kwargs["can_send_media_messages"] = True
        kwargs["can_send_other_messages"] = True
        kwargs["can_send_polls"] = True
        kwargs["can_add_web_page_previews"] = True
        kwargs["can_invite_users"] = True
        desc = "standard member permissions"
    elif target_lock in ("messages", "msg"):
        kwargs["can_send_messages"] = True
        desc = "messages"
    elif target_lock in ("media", "photos", "videos"):
        kwargs["can_send_media_messages"] = True
        desc = "media"
    elif target_lock in ("stickers", "sticker", "gifs", "gif"):
        kwargs["can_send_other_messages"] = True
        desc = "stickers and GIFs"
    elif target_lock in ("polls", "poll"):
        kwargs["can_send_polls"] = True
        desc = "polls"
    elif target_lock in ("invites", "invite"):
        kwargs["can_invite_users"] = True
        desc = "member invites"
    elif target_lock in ("previews", "links"):
        kwargs["can_add_web_page_previews"] = True
        desc = "web page previews"
    else:
        return await edit_or_reply(message, "Invalid unlock option. Valid: `messages`, `media`, `stickers`, `polls`, `invites`, `previews`, `all`.")

    try:
        new_perms = ChatPermissions(**kwargs)
        await client.set_chat_permissions(message.chat.id, new_perms)
        await edit_or_reply(message, f"**Chat Permissions Updated**\n• Unlocked `{desc}` for normal members.")
    except RPCError as e:
        await edit_or_reply(message, f"Failed to unlock permissions: `{e}`")

@on_cmd(["slowmode", "sm"], desc="Configure chat slow mode", usage="[0/10/30/60/300/900/3600]")
async def slowmode_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await edit_or_reply(message, "This command can only be used in groups and supergroups.")

    can_change = await _check_admin_privilege(client, message.chat.id, "can_change_info")
    if not can_change:
        return await edit_or_reply(message, "You need administrator permission to configure slow mode.")

    args = (message.text or message.caption or "").split()
    if len(args) < 2:
        return await edit_or_reply(
            message,
            "Specify slow mode interval: `0` (off), `10`, `30`, `60`, `300`, `900`, or `3600` seconds."
        )

    raw = args[1].lower()
    if raw in ("off", "disable"):
        seconds = 0
    elif raw.isdigit():
        seconds = int(raw)
    else:
        return await edit_or_reply(message, "Valid slow mode values: `0`, `10`, `30`, `60`, `300`, `900`, `3600`.")

    valid_values = (0, 10, 30, 60, 300, 900, 3600)
    if seconds not in valid_values:
        return await edit_or_reply(message, f"Telegram only supports: {', '.join(f'`{v}`' for v in valid_values)}.")

    try:
        await client.set_slow_mode(message.chat.id, seconds)
        if seconds == 0:
            await edit_or_reply(message, "Slow mode has been disabled.")
        else:
            await edit_or_reply(message, f"Slow mode set to `{seconds}` second(s).")
    except RPCError as e:
        await edit_or_reply(message, f"Failed to set slow mode: `{e}`")

@on_cmd(["cleanservice", "cs"], desc="Clean service messages or toggle real-time cleaner", usage="[on|off|status|limit]")
async def clean_service_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await edit_or_reply(message, "This command can only be used in groups and supergroups.")
        return

    prefix = get_client_prefix(client)
    args = (message.text or message.caption or "").split()
    action = args[1].lower() if len(args) > 1 else ""

    # Subcommand: on / enable
    if action in ("on", "enable", "start"):
        can_delete = await _check_admin_privilege(client, message.chat.id, "can_delete_messages")
        if not can_delete:
            await edit_or_reply(message, "You need administrator permission to delete messages in this chat.")
            return

        db.set(f"CLEANSERVICE_{message.chat.id}", True)
        await edit_or_reply(
            message,
            "**Service Message Cleaner Enabled**\n"
            "• Real-time service cleaner is now ACTIVE for this group.\n"
            "• Join, leave, photo, pin, and title updates will be purged automatically.",
        )
        return

    # Subcommand: off / disable
    if action in ("off", "disable", "stop"):
        db.delete(f"CLEANSERVICE_{message.chat.id}")
        await edit_or_reply(
            message,
            "**Service Message Cleaner Disabled**\n"
            "• Real-time service cleaner is now INACTIVE for this group.",
        )
        return

    # Subcommand: status / info
    if action in ("status", "info"):
        is_active = bool(db.get(f"CLEANSERVICE_{message.chat.id}"))
        state_str = "ACTIVE" if is_active else "INACTIVE"
        await edit_or_reply(
            message,
            f"**Service Message Cleaner Status**\n"
            f"• Mode: `{state_str}`\n\n"
            f"Use `{prefix}cleanservice on` to enable or `{prefix}cleanservice off` to disable.",
        )
        return

    # Subcommand: manual scan & purge history
    can_delete = await _check_admin_privilege(client, message.chat.id, "can_delete_messages")
    if not can_delete:
        await edit_or_reply(message, "You need administrator permission to delete messages in this chat.")
        return

    limit = 100
    if action.isdigit():
        limit = min(max(int(action), 10), 500)

    # Determine which client can scan history (bots are forbidden by Telegram MTProto from calling GetHistory)
    scan_client = client
    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )
    if is_bot:
        if PyGramClient.USERBOT_CLIENT:
            scan_client = PyGramClient.USERBOT_CLIENT
        else:
            await edit_or_reply(
                message,
                "Bot accounts cannot scan chat history due to Telegram MTProto restrictions.\n"
                f"Please run `{prefix}cleanservice` from your user account or enable real-time cleaning via `{prefix}cleanservice on`.",
            )
            return

    status_msg = await edit_or_reply(message, f"`Scanning last {limit} messages for service notifications...`")

    service_msg_ids = []
    try:
        async for msg in scan_client.get_chat_history(message.chat.id, limit=limit):
            if getattr(msg, "service", False):
                service_msg_ids.append(msg.id)
    except Exception as e:
        await edit_or_reply(status_msg, f"**Failed to scan chat history:**\n`{e}`")
        return

    if not service_msg_ids:
        await edit_or_reply(status_msg, f"No service messages found in the last {limit} messages.")
        return

    deleted_count = 0
    for i in range(0, len(service_msg_ids), 100):
        chunk = service_msg_ids[i:i + 100]
        try:
            await client.delete_messages(message.chat.id, chunk)
            deleted_count += len(chunk)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await client.delete_messages(message.chat.id, chunk)
                deleted_count += len(chunk)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Error during service deletion chunk: %s", e)
            if PyGramClient.USERBOT_CLIENT and PyGramClient.USERBOT_CLIENT != client:
                try:
                    await PyGramClient.USERBOT_CLIENT.delete_messages(message.chat.id, chunk)
                    deleted_count += len(chunk)
                except Exception:
                    pass

    await edit_or_reply(
        status_msg,
        f"**Service Messages Cleaned**\n• Removed `{deleted_count}` service message(s) successfully.",
    )

@Client.on_message(filters.group & filters.service, group=5)
async def auto_clean_service_handler(client: Client, message: Message):
    """Background listener to purge service notifications in real-time when enabled."""
    if not message.chat:
        return
    if not db.get(f"CLEANSERVICE_{message.chat.id}"):
        return
    try:
        await message.delete()
    except Exception:
        pass

@on_cmd(["zombies", "kickdeleted"], desc="Inspect or kick deleted accounts in group", usage="[clean]")
async def zombies_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await edit_or_reply(message, "This command can only be used in groups and supergroups.")
        return

    prefix = get_client_prefix(client)
    args = (message.text or message.caption or "").split()
    should_clean = len(args) > 1 and args[1].lower() in ("clean", "kick", "purge")

    if should_clean:
        can_kick = await _check_admin_privilege(client, message.chat.id, "can_restrict_members")
        if not can_kick:
            await edit_or_reply(message, "You need administrator permission to ban/restrict members in this chat.")
            return

    status_msg = await edit_or_reply(message, "`Scanning group members for deleted accounts...`")

    scan_client = client
    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )
    if is_bot and PyGramClient.USERBOT_CLIENT:
        scan_client = PyGramClient.USERBOT_CLIENT

    deleted_members = []
    try:
        async for member in scan_client.get_chat_members(message.chat.id):
            if member.user and member.user.is_deleted:
                deleted_members.append(member.user.id)
    except ChatAdminRequired:
        await edit_or_reply(status_msg, "Failed to scan members: Administrator access required to list chat members.")
        return
    except Exception as e:
        await edit_or_reply(status_msg, f"**Failed to scan chat members:**\n`{e}`")
        return

    total_deleted = len(deleted_members)
    if total_deleted == 0:
        await edit_or_reply(status_msg, "No deleted accounts found in this group.")
        return

    if not should_clean:
        await edit_or_reply(
            status_msg,
            f"**Deleted Accounts Found**\n"
            f"• Total: `{total_deleted}` deleted account(s)\n\n"
            f"To remove them, run: `{prefix}zombies clean`",
        )
        return

    kicked_count = 0
    failed_count = 0

    for user_id in deleted_members:
        try:
            await client.ban_chat_member(message.chat.id, user_id)
            await client.unban_chat_member(message.chat.id, user_id)
            kicked_count += 1
            await asyncio.sleep(0.3)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await client.ban_chat_member(message.chat.id, user_id)
                await client.unban_chat_member(message.chat.id, user_id)
                kicked_count += 1
            except Exception:
                failed_count += 1
        except (ChatAdminRequired, UserAdminInvalid):
            failed_count += 1
        except Exception as e:
            logger.debug("Failed to kick deleted user %d: %s", user_id, e)
            failed_count += 1

    result_text = (
        f"**Deleted Accounts Cleaned**\n"
        f"• Removed: `{kicked_count}`\n"
    )
    if failed_count:
        result_text += f"• Failed/Skipped: `{failed_count}`\n"
    await edit_or_reply(status_msg, result_text)

@on_cmd(["tagall", "mention"], desc="Mention group members safely in batches", usage="[custom_text]")
async def tagall_cmd(client: Client, message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await edit_or_reply(message, "This command can only be used in groups and supergroups.")
        return

    chat_id = message.chat.id
    if chat_id in _ACTIVE_TAG_CHATS:
        prefix = get_client_prefix(client)
        await edit_or_reply(message, f"A mention process is already active. Stop it with `{prefix}canceltag`.")
        return

    prefix = get_client_prefix(client)
    text = message.text or message.caption or ""
    parts = text.split(maxsplit=1)
    custom_message = parts[1].strip() if len(parts) > 1 else "Attention everyone"

    _ACTIVE_TAG_CHATS.add(chat_id)
    status_msg = await edit_or_reply(
        message,
        f"`Starting group mention. To cancel anytime, use {prefix}canceltag...`",
    )

    BATCH_SIZE = 5
    MAX_MENTIONS = 100
    batch: list[str] = []
    total_mentioned = 0
    limit_reached = False

    scan_client = client
    is_bot = bool(getattr(client, "bot_token", None)) or (
        getattr(client, "me", None) and client.me.is_bot
    )
    if is_bot and PyGramClient.USERBOT_CLIENT:
        scan_client = PyGramClient.USERBOT_CLIENT

    try:
        async for member in scan_client.get_chat_members(chat_id):
            if chat_id not in _ACTIVE_TAG_CHATS:
                break

            if total_mentioned >= MAX_MENTIONS:
                limit_reached = True
                break

            user = member.user
            if not user or user.is_deleted or user.is_bot:
                continue

            mention_text = user.mention(user.first_name or "Member")
            batch.append(mention_text)

            if len(batch) >= BATCH_SIZE:
                mention_line = f"{custom_message}\n" + " ".join(batch)
                try:
                    await client.send_message(chat_id, mention_line)
                    total_mentioned += len(batch)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await client.send_message(chat_id, mention_line)
                    total_mentioned += len(batch)
                except Exception as e:
                    logger.debug("Failed to send mention chunk: %s", e)

                batch.clear()
                await asyncio.sleep(1.5)

        if batch and chat_id in _ACTIVE_TAG_CHATS:
            mention_line = f"{custom_message}\n" + " ".join(batch)
            try:
                await client.send_message(chat_id, mention_line)
                total_mentioned += len(batch)
            except Exception:
                pass

        if chat_id in _ACTIVE_TAG_CHATS:
            completion_msg = f"**Mention Complete**\n• Successfully mentioned `{total_mentioned}` group members."
            if limit_reached:
                completion_msg += f"\n• Safety limit reached (`{MAX_MENTIONS}` members max per run to prevent Telegram flood restrictions)."
            await edit_or_reply(status_msg, completion_msg)
    except ChatAdminRequired:
        await edit_or_reply(status_msg, "Administrator rights required to view member list in this group.")
    except Exception as e:
        await edit_or_reply(status_msg, f"**Mention error:** `{e}`")
    finally:
        _ACTIVE_TAG_CHATS.discard(chat_id)

@on_cmd(["canceltag", "stoptag"], desc="Cancel an ongoing group mention", usage="")
async def canceltag_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    if chat_id in _ACTIVE_TAG_CHATS:
        _ACTIVE_TAG_CHATS.discard(chat_id)
        await edit_or_reply(message, "Ongoing group mention has been stopped.")
    else:
        await edit_or_reply(message, "No active group mention in this chat.")
