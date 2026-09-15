import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import os
from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply, get_client_prefix, format_bytes

@on_cmd(["up", "upload"], desc="Upload local file to Telegram", usage="<path>")
async def upload_file_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    prefix = get_client_prefix(client)
    if len(args) < 2:
        return await edit_or_reply(message, f"Usage: `{prefix}up <path>`")

    path = os.path.expanduser(args[1].strip())
    if not os.path.exists(path):
        alt = os.path.join(os.path.expanduser("~"), path)
        if os.path.exists(alt):
            path = alt

    if not os.path.exists(path):
        return await edit_or_reply(message, f"File not found: `{path}`")
    if not os.path.isfile(path):
        if os.path.isdir(path):
            return await edit_or_reply(message, f"`{path}` is a directory. Use `{prefix}zipmod` to package it.")
        return await edit_or_reply(message, f"Target is not a regular file: `{path}`")

    file_size = os.path.getsize(path)
    if file_size > 2000 * 1024 * 1024:
        return await edit_or_reply(
            message,
            f"File too large: `{format_bytes(file_size)}` exceeds Telegram 2.0 GB limit.",
        )

    filename = os.path.basename(path)
    status = await edit_or_reply(message, f"Uploading `{filename}` ({format_bytes(file_size)})...")

    try:
        await client.send_document(
            chat_id=message.chat.id,
            document=path,
            caption=f"`{filename}` ({format_bytes(file_size)})",
        )
        await status.delete()
    except Exception as e:
        await edit_or_reply(status, f"Upload failed: {e}")

@on_cmd(["dl", "download"], desc="Download replied media to Termux", usage="[path]")
async def download_file_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply or not (reply.media or reply.document):
        return await edit_or_reply(message, "Reply to a document or media to download.")

    text = message.text or message.caption or ""
    args = text.split(maxsplit=1)
    target_dir = os.path.expanduser(args[1].strip()) if len(args) > 1 else "downloads/"

    if target_dir.endswith("/"):
        os.makedirs(target_dir, exist_ok=True)

    status = await edit_or_reply(message, "Downloading media...")
    try:
        saved_path = await reply.download(file_name=target_dir)
        size_str = ""
        if os.path.isfile(saved_path):
            size_str = f" (`{format_bytes(os.path.getsize(saved_path))}`)"
        await edit_or_reply(status, f"Saved to: `{saved_path}`{size_str}")
    except Exception as e:
        await edit_or_reply(status, f"Download failed: {e}")

@on_cmd("sd", desc="Send self-destructing message", usage="<sec> <text>")
async def self_destruct_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    parts = text.split(maxsplit=2)
    prefix = get_client_prefix(client)
    if len(parts) < 3 or not parts[1].isdigit():
        return await edit_or_reply(message, f"Usage: `{prefix}sd <seconds> <text>`")

    sec = int(parts[1])
    if sec < 1 or sec > 86400:
        return await edit_or_reply(message, "Duration must be between 1 and 86400 seconds.")

    payload = parts[2]
    msg = await edit_or_reply(message, payload)

    async def _auto_delete():
        await asyncio.sleep(sec)
        try:
            await msg.delete()
        except Exception:
            pass
        if msg.id != message.id:
            try:
                await message.delete()
            except Exception:
                pass

    asyncio.create_task(_auto_delete())
