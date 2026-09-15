import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply, get_client_prefix


@on_cmd(["purge", "p"], desc="Delete a sequence of messages by count or replied target", usage="[count]")
async def purge_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    message_ids = []

    # Mode 1: Reply to a message -> purge everything from that message to current
    if message.reply_to_message:
        start_id = min(message.reply_to_message.id, message.id)
        end_id = max(message.reply_to_message.id, message.id)
        diff = end_id - start_id

        # Scan real message history if window is reasonable (<= 300) to avoid phantom IDs
        scan_client = client
        if getattr(client, "me", None) and client.me.is_bot:
            from pygramx.client import PyGramClient
            if PyGramClient.USERBOT_CLIENT:
                scan_client = PyGramClient.USERBOT_CLIENT

        if diff <= 300 and not (getattr(scan_client, "me", None) and scan_client.me.is_bot):
            try:
                async for msg in scan_client.get_chat_history(chat_id, limit=diff + 10):
                    if start_id <= msg.id <= end_id:
                        message_ids.append(msg.id)
                    elif msg.id < start_id:
                        break
            except Exception:
                message_ids.clear()

        if not message_ids:
            if diff > 1000:
                start_id = end_id - 1000
            message_ids = list(range(start_id, end_id + 1))
    else:
        # Mode 2: Purge last N messages
        text = message.text or message.caption or ""
        args = text.split(maxsplit=1)
        count = 1
        if len(args) > 1 and args[1].isdigit():
            count = min(int(args[1]), 100)

        scan_client = client
        is_bot = bool(getattr(client, "bot_token", None)) or (
            getattr(client, "me", None) and client.me.is_bot
        )
        if is_bot:
            from pygramx.client import PyGramClient
            if PyGramClient.USERBOT_CLIENT:
                scan_client = PyGramClient.USERBOT_CLIENT
            else:
                notice = await edit_or_reply(
                    message,
                    "Bot accounts cannot scan chat history due to Telegram MTProto restrictions.\n"
                    "Reply to a message to purge or run this command via userbot."
                )
                await asyncio.sleep(3)
                try:
                    await notice.delete()
                except Exception:
                    pass
                return

        async for msg in scan_client.get_chat_history(chat_id, limit=count + 1):
            message_ids.append(msg.id)

    if not message_ids:
        notice = await edit_or_reply(message, "`No messages found to delete.`")
        await asyncio.sleep(2)
        try:
            await notice.delete()
        except Exception:
            pass
        return

    # Delete in chunks of 100 (Telegram API maximum)
    chunks = [message_ids[i:i + 100] for i in range(0, len(message_ids), 100)]
    deleted_count = 0
    errors = []

    for chunk in chunks:
        try:
            res = await client.delete_messages(chat_id=chat_id, message_ids=chunk)
            if isinstance(res, int) and not isinstance(res, bool):
                deleted_count += res
            elif isinstance(res, bool):
                deleted_count += len(chunk) if res else 0
            else:
                deleted_count += len(chunk)
            if len(chunks) > 1:
                await asyncio.sleep(0.3)
        except Exception as e:
            errors.append(str(e))
            continue

    status_text = f"Successfully deleted `{deleted_count}` message{'s' if deleted_count != 1 else ''}."
    if errors and deleted_count == 0:
        status_text = f"Failed to delete messages: `{errors[0]}`"
    elif errors:
        status_text = f"Deleted `{deleted_count}` message(s). Note: `{errors[0]}`"

    try:
        notice = await client.send_message(
            chat_id=chat_id,
            text=status_text,
        )
        await asyncio.sleep(3)
        await notice.delete()
    except Exception:
        pass


@on_cmd(["purgeme", "pme"], desc="Delete only your own recent messages in any chat", usage="[count]")
async def purgeme_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    limit = 10
    if len(args) > 1 and args[1].isdigit():
        limit = min(max(int(args[1]), 1), 100)

    my_id = (getattr(client, "me", None) and getattr(client.me, "id", None))
    if not my_id:
        try:
            me = await client.get_me()
            my_id = me.id
        except Exception:
            pass

    to_delete = [message.id]
    scan_limit = limit * 4  # Scan wider window to capture enough self-authored messages

    async for msg in client.get_chat_history(chat_id, limit=scan_limit):
        if len(to_delete) >= limit + 1:
            break
        if msg.id == message.id:
            continue
        is_mine = (
            getattr(msg, "outgoing", False)
            or (msg.from_user and msg.from_user.id == my_id)
            or (msg.from_user and getattr(msg.from_user, "is_self", False))
        )
        if is_mine:
            to_delete.append(msg.id)

    if not to_delete:
        notice = await edit_or_reply(message, "`No messages from your account found.`")
        await asyncio.sleep(2)
        try:
            await notice.delete()
        except Exception:
            pass
        return

    # Delete collected messages
    chunks = [to_delete[i:i + 100] for i in range(0, len(to_delete), 100)]
    deleted_count = 0
    errors = []

    for chunk in chunks:
        try:
            res = await client.delete_messages(chat_id=chat_id, message_ids=chunk)
            if isinstance(res, int) and not isinstance(res, bool):
                deleted_count += res
            elif isinstance(res, bool):
                deleted_count += len(chunk) if res else 0
            else:
                deleted_count += len(chunk)
            if len(chunks) > 1:
                await asyncio.sleep(0.3)
        except Exception as e:
            errors.append(str(e))
            continue

    status_text = f"Successfully purged `{deleted_count}` of your messages."
    if errors and deleted_count == 0:
        status_text = f"Failed to purge messages: `{errors[0]}`"

    try:
        notice = await client.send_message(
            chat_id=chat_id,
            text=status_text,
        )
        await asyncio.sleep(3)
        await notice.delete()
    except Exception:
        pass


@on_cmd(["del", "d"], desc="Delete the replied message immediately")
async def delete_cmd(client: Client, message: Message):
    if not message.reply_to_message:
        notice = await edit_or_reply(message, "`Please reply to a message to delete it.`")
        await asyncio.sleep(2)
        try:
            await notice.delete()
        except Exception:
            pass
        return

    target = message.reply_to_message
    try:
        await target.delete()
    except Exception as e:
        err_msg = await edit_or_reply(message, f"Could not delete message: `{e}`")
        await asyncio.sleep(2)
        try:
            await err_msg.delete()
        except Exception:
            pass
        return

    try:
        await message.delete()
    except Exception:
        pass
