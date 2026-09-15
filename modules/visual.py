import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import io
import os
import subprocess
import tempfile
import time
import requests
from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply, get_client_prefix

def _make_code_card(code_text: str) -> io.BytesIO:
    font = ImageFont.load_default()
    sanitized = code_text.replace("\t", "    ")
    lines = sanitized.splitlines()[:100]
    padding = 24
    header_h = 36
    line_h = 18
    
    max_line = max(len(line) for line in lines) if lines else 20
    width = max(max_line * 8 + padding * 2, 420)
    height = header_h + len(lines) * line_h + padding * 2

    img = Image.new("RGBA", (int(width), int(height)), (26, 27, 38, 255))
    draw = ImageDraw.Draw(img)

    # Mac style window dots
    draw.ellipse([18, 14, 28, 24], fill=(247, 118, 142))
    draw.ellipse([34, 14, 44, 24], fill=(224, 175, 104))
    draw.ellipse([50, 14, 60, 24], fill=(158, 206, 106))

    y = header_h + 10
    for line in lines:
        draw.text((22, y), line, font=font, fill=(192, 202, 245))
        y += line_h

    buf = io.BytesIO()
    buf.name = "carbon.png"
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def _make_quote_sticker(sender_name: str, text: str, time_str: str) -> str:
    font = ImageFont.load_default()
    
    # Word wrapping
    lines = []
    for paragraph in text.splitlines():
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        cur = ""
        for w in words:
            if len(cur + " " + w) > 34:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            lines.append(cur)

    lines = lines[:25]
    line_h = 18
    max_w = max(len(l) for l in lines) if lines else 10
    text_w = max(max_w * 8, len(sender_name) * 8 + 40, 160)
    w = min(text_w + 44, 480)
    h = 42 + len(lines) * line_h + 24

    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Message bubble
    draw.rounded_rectangle([4, 4, w - 4, h - 4], radius=14, fill=(30, 32, 48, 245))

    # Sender name
    draw.text((18, 14), sender_name, font=font, fill=(122, 162, 247))

    # Message text
    y = 36
    for line in lines:
        draw.text((18, y), line, font=font, fill=(240, 242, 248))
        y += line_h

    # Timestamp
    draw.text((w - 52, h - 20), time_str, font=font, fill=(140, 144, 162))

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        png_path = f.name
        img.save(png_path, format="PNG")

    webp_path = png_path.replace(".png", ".webp")
    res = subprocess.run(
        ["ffmpeg", "-y", "-i", png_path, "-vcodec", "libwebp", "-lossless", "1", webp_path],
        capture_output=True,
        timeout=10.0,
    )
    if os.path.exists(png_path):
        os.remove(png_path)
    if res.returncode != 0 or not os.path.exists(webp_path) or os.path.getsize(webp_path) == 0:
        raise RuntimeError("Failed to encode WebP sticker via FFmpeg")
    return webp_path

@on_cmd(["q", "quote"], desc="Turn replied message into a clean quote sticker", usage="")
async def quote_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply:
        return await edit_or_reply(message, "Reply to a text message to create a quote sticker.")

    text = reply.text or reply.caption
    if not text:
        return await edit_or_reply(message, "Replied message has no text to quote.")

    status_msg = await edit_or_reply(message, "Generating quote sticker...")

    sender = reply.from_user
    if sender:
        name = sender.first_name + (f" {sender.last_name}" if sender.last_name else "")
    elif reply.sender_chat:
        name = reply.sender_chat.title or "Channel"
    else:
        name = "Unknown"

    time_str = reply.date.strftime("%H:%M") if reply.date else time.strftime("%H:%M")

    try:
        webp_path = _make_quote_sticker(name, text, time_str)
        try:
            await client.send_sticker(
                chat_id=message.chat.id,
                sticker=webp_path,
                reply_to_message_id=reply.id,
            )
            await status_msg.delete()
        finally:
            if os.path.exists(webp_path):
                os.remove(webp_path)
    except Exception as err:
        await edit_or_reply(status_msg, f"Failed to generate quote: `{err}`")

@on_cmd(["carbon"], desc="Format code or text into a stylish image", usage="<code or reply>")
async def carbon_cmd(client: Client, message: Message):
    code_text = ""
    reply = message.reply_to_message

    parts = message.text.split(None, 1) if message.text else []
    if len(parts) > 1:
        code_text = parts[1].strip()
    elif reply:
        code_text = (reply.text or reply.caption or "").strip()

    if not code_text:
        prefix = get_client_prefix(client)
        return await edit_or_reply(
            message,
            f"Provide code or reply to a message:\n`{prefix}carbon print('hello')`",
        )

    status_msg = await edit_or_reply(message, "Rendering code image...")
    try:
        image_buf = _make_code_card(code_text)
        reply_id = reply.id if reply else message.id
        await client.send_photo(
            chat_id=message.chat.id,
            photo=image_buf,
            caption="Rendered with Carbon",
            reply_to_message_id=reply_id,
        )
        await status_msg.delete()
    except Exception as err:
        await edit_or_reply(status_msg, f"Failed to render carbon card: `{err}`")

@on_cmd(["paste"], desc="Upload text snippet to pastebin", usage="<text or reply>")
async def paste_cmd(client: Client, message: Message):
    content = ""
    reply = message.reply_to_message

    parts = message.text.split(None, 1) if message.text else []
    if len(parts) > 1:
        content = parts[1].strip()
    elif reply:
        if reply.text or reply.caption:
            content = (reply.text or reply.caption).strip()
        elif reply.document:
            if reply.document.file_size and reply.document.file_size > 512 * 1024:
                return await edit_or_reply(message, "Document is too large (maximum 512 KB).")
            local_doc = await client.download_media(reply)
            if local_doc and os.path.isfile(local_doc):
                try:
                    with open(local_doc, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                finally:
                    if os.path.exists(local_doc):
                        os.remove(local_doc)

    if not content:
        prefix = get_client_prefix(client)
        return await edit_or_reply(
            message,
            f"Provide text or reply to a message:\n`{prefix}paste my text content`",
        )

    status_msg = await edit_or_reply(message, "Uploading text to pastebin...")

    try:
        resp = requests.post(
            "https://paste.rs",
            data=content.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
            timeout=10.0,
        )
        if resp.status_code in [200, 201] and resp.text.startswith("http"):
            url = resp.text.strip()
            await edit_or_reply(
                status_msg,
                f"**Paste Created**\n• **URL:** {url}\n• **Lines:** `{len(content.splitlines())}`",
            )
        else:
            await edit_or_reply(status_msg, f"Pastebin returned unexpected status: `{resp.status_code}`")
    except Exception as err:
        await edit_or_reply(status_msg, f"Failed to upload paste: `{err}`")
