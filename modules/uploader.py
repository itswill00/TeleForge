import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import os
import time
import logging
import requests
from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply, get_client_prefix, format_bytes

logger = logging.getLogger("pygramx.uploader")

CATBOX_API_URL = "https://catbox.moe/user/api.php"
LITTERBOX_API_URL = "https://litterbox.catbox.moe/resources/internals/api.php"
USER_AGENT = "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"

def _upload_to_catbox(file_path: str) -> str:
    with open(file_path, "rb") as f:
        response = requests.post(
            CATBOX_API_URL,
            headers={"User-Agent": USER_AGENT},
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (os.path.basename(file_path), f)},
            timeout=60,
        )
    if response.status_code == 200 and response.text.startswith("http"):
        return response.text.strip()
    raise RuntimeError(f"Upload failed (HTTP {response.status_code}): {response.text[:200]}")

def _upload_to_litterbox(file_path: str, duration: str = "24h") -> str:
    with open(file_path, "rb") as f:
        response = requests.post(
            LITTERBOX_API_URL,
            headers={"User-Agent": USER_AGENT},
            data={"reqtype": "fileupload", "time": duration},
            files={"fileToUpload": (os.path.basename(file_path), f)},
            timeout=60,
        )
    if response.status_code == 200 and response.text.startswith("http"):
        return response.text.strip()
    raise RuntimeError(f"Upload failed (HTTP {response.status_code}): {response.text[:200]}")

@on_cmd(["catbox", "upl"], desc="Upload replied media or file to Catbox.moe", usage="")
async def catbox_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply or not (
        reply.media
        or reply.text
        or reply.document
        or reply.photo
        or reply.video
        or reply.audio
        or reply.voice
        or reply.sticker
        or reply.animation
    ):
        prefix = get_client_prefix(client)
        await edit_or_reply(message, f"Reply to any media, document, or file with `{prefix}catbox` to upload.")
        return

    status_msg = await edit_or_reply(message, "`Downloading media for upload...`")
    download_path = None
    start_time = time.time()

    try:
        # Check size if available
        media_obj = getattr(reply, reply.media.value, None) if reply.media else None
        file_size = getattr(media_obj, "file_size", 0) if media_obj else 0
        if file_size > 200 * 1024 * 1024:
            await edit_or_reply(
                status_msg,
                f"File size (`{format_bytes(file_size)}`) exceeds Catbox's 200 MB limit. Use Litterbox instead.",
            )
            return

        if reply.media:
            download_path = await client.download_media(reply)
        else:
            text_content = reply.text or reply.caption or ""
            import tempfile
            fd, download_path = tempfile.mkstemp(prefix="snippet_", suffix=".txt")
            with open(fd, "w", encoding="utf-8") as f:
                f.write(text_content)

        if not download_path or not os.path.exists(download_path):
            await edit_or_reply(status_msg, "Failed to prepare file from Telegram message.")
            return

        actual_size = os.path.getsize(download_path)
        filename = os.path.basename(download_path)
        await edit_or_reply(status_msg, f"`Uploading {filename} ({format_bytes(actual_size)}) to Catbox...`")

        loop = asyncio.get_running_loop()
        file_url = await loop.run_in_executor(None, _upload_to_catbox, download_path)
        elapsed = time.time() - start_time

        await edit_or_reply(
            status_msg,
            f"**File Uploaded to Catbox**\n"
            f"• **File:** `{filename}`\n"
            f"• **Size:** `{format_bytes(actual_size)}`\n"
            f"• **Duration:** `{elapsed:.2f}s`\n"
            f"• **Link:** {file_url}",
        )
    except Exception as e:
        logger.exception("Catbox upload error: %s", e)
        await edit_or_reply(status_msg, f"**Upload Error:**\n`{e}`")
    finally:
        if download_path and os.path.exists(download_path):
            try:
                os.remove(download_path)
            except Exception:
                pass

@on_cmd(["litterbox", "litter"], desc="Upload replied media to Litterbox (auto-expires)", usage="[1h|12h|24h|72h]")
async def litterbox_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply or not (
        reply.media
        or reply.text
        or reply.document
        or reply.photo
        or reply.video
        or reply.audio
        or reply.voice
        or reply.sticker
        or reply.animation
    ):
        prefix = get_client_prefix(client)
        await edit_or_reply(
            message,
            f"Reply to any media or file with `{prefix}litterbox [1h|12h|24h|72h]` to upload with auto-expiry.",
        )
        return

    text = message.text or message.caption or ""
    args = text.split()
    duration = "24h"
    if len(args) > 1 and args[1].lower() in ("1h", "12h", "24h", "72h"):
        duration = args[1].lower()

    status_msg = await edit_or_reply(message, f"`Downloading media for Litterbox ({duration})...`")
    download_path = None
    start_time = time.time()

    try:
        if reply.media:
            download_path = await client.download_media(reply)
        else:
            text_content = reply.text or reply.caption or ""
            import tempfile
            fd, download_path = tempfile.mkstemp(prefix="snippet_", suffix=".txt")
            with open(fd, "w", encoding="utf-8") as f:
                f.write(text_content)

        if not download_path or not os.path.exists(download_path):
            await edit_or_reply(status_msg, "Failed to prepare file from Telegram message.")
            return

        actual_size = os.path.getsize(download_path)
        filename = os.path.basename(download_path)
        await edit_or_reply(
            status_msg,
            f"`Uploading {filename} ({format_bytes(actual_size)}) to Litterbox ({duration})...`",
        )

        loop = asyncio.get_running_loop()
        file_url = await loop.run_in_executor(None, _upload_to_litterbox, download_path, duration)
        elapsed = time.time() - start_time

        await edit_or_reply(
            status_msg,
            f"**File Uploaded to Litterbox**\n"
            f"• **File:** `{filename}`\n"
            f"• **Size:** `{format_bytes(actual_size)}`\n"
            f"• **Expiry:** `{duration}`\n"
            f"• **Duration:** `{elapsed:.2f}s`\n"
            f"• **Link:** {file_url}",
        )
    except Exception as e:
        logger.exception("Litterbox upload error: %s", e)
        await edit_or_reply(status_msg, f"**Upload Error:**\n`{e}`")
    finally:
        if download_path and os.path.exists(download_path):
            try:
                os.remove(download_path)
            except Exception:
                pass
