import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import json
import os
import re
import subprocess
import time
from typing import Optional, Tuple
from PIL import Image
import requests
from pyrogram import Client
from pyrogram.types import Message
import config
from pygramx import on_cmd, db
from pygramx.client import PyGramClient
from pygramx.utils import edit_or_reply, get_client_prefix

MAX_STATIC_PACK = 120
MAX_DYNAMIC_PACK = 50

def _get_bot_info() -> Tuple[Optional[str], Optional[str]]:
    """Get companion bot token and username."""
    token = config.BOT_TOKEN
    if not token:
        return None, None
    try:
        res = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        data = res.json()
        if data.get("ok"):
            return token, data["result"]["username"]
    except Exception:
        pass
    return token, None

def _extract_emoji(text: str) -> Optional[str]:
    """Extract emoji from command arguments if provided."""
    args = text.split(maxsplit=1)
    if len(args) > 1:
        emojis = re.findall(r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]", args[1])
        if emojis:
            return emojis[0]
        return args[1].strip()[:2]
    return None

def _prepare_photo_sticker(input_path: str, output_path: str) -> bool:
    """Resize photo preserving aspect ratio to fit 512x512 PNG."""
    try:
        with Image.open(input_path) as im:
            im = im.convert("RGBA")
            w, h = im.size
            if w >= h:
                nw, nh = 512, max(1, int(round((h * 512) / w)))
            else:
                nw, nh = max(1, int(round((w * 512) / h))), 512
            im_resized = im.resize((nw, nh), Image.Resampling.LANCZOS)
            im_resized.save(output_path, "PNG")
            return True
    except Exception:
        # Fallback to dwebp if input was a webp file
        res = subprocess.run(["dwebp", input_path, "-o", output_path], capture_output=True)
        if res.returncode == 0 and os.path.isfile(output_path):
            try:
                with Image.open(output_path) as im:
                    im = im.convert("RGBA")
                    w, h = im.size
                    if w >= h:
                        nw, nh = 512, max(1, int(round((h * 512) / w)))
                    else:
                        nw, nh = max(1, int(round((w * 512) / h))), 512
                    im.resize((nw, nh), Image.Resampling.LANCZOS).save(output_path, "PNG")
                return True
            except Exception:
                pass
    return False

@on_cmd(["kang", "steal"], desc="Add replied sticker or photo to custom pack", usage="[emoji]")
async def kang_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply or not (
        reply.photo
        or reply.sticker
        or (reply.document and getattr(reply.document, "mime_type", "").startswith("image/"))
    ):
        return await edit_or_reply(message, "Reply to a photo or sticker to kang it.")

    token, bot_username = _get_bot_info()
    if not token or not bot_username:
        return await edit_or_reply(
            message,
            "Companion BOT_TOKEN is required for managing stickers.\nConfigure BOT_TOKEN in `.env` to enable kang.",
        )

    owner_id = getattr(PyGramClient, "OWNER_ID", None) or config.get_owner_id()
    if not owner_id and message.from_user:
        owner_id = message.from_user.id
    if not owner_id:
        return await edit_or_reply(message, "Could not determine owner user ID.")

    text = message.text or message.caption or ""
    emoji = _extract_emoji(text)
    if not emoji:
        if reply.sticker and reply.sticker.emoji:
            emoji = reply.sticker.emoji
        else:
            emoji = "🤔"

    status_msg = await edit_or_reply(message, "Processing sticker...")

    sticker_format = "static"
    if reply.sticker:
        if reply.sticker.is_animated:
            sticker_format = "animated"
        elif reply.sticker.is_video:
            sticker_format = "video"

    os.makedirs("downloads", exist_ok=True)
    ts = int(time.time() * 1000)
    raw_file = f"downloads/raw_{ts}"
    final_file = None
    created_temp = False

    try:
        downloaded = await reply.download(file_name=raw_file)
        if not downloaded or not os.path.isfile(downloaded):
            return await edit_or_reply(status_msg, "Failed to download media from Telegram.")

        # If it is already a sticker, use downloaded file directly (already valid format)
        if reply.sticker:
            final_file = downloaded
        else:
            # Photo or image document: resize to 512x512 PNG
            final_file = f"downloads/sticker_{ts}.png"
            created_temp = True
            ok = _prepare_photo_sticker(downloaded, final_file)
            if not ok or not os.path.isfile(final_file):
                return await edit_or_reply(status_msg, "Failed to convert image to sticker format.")

        prefix_map = {"static": "k", "animated": "ka", "video": "kv"}
        p_prefix = prefix_map.get(sticker_format, "k")
        max_limit = MAX_STATIC_PACK if sticker_format == "static" else MAX_DYNAMIC_PACK

        pack_vol_key = f"KANG_VOL_{sticker_format}_{owner_id}"
        pack_vol = db.get(pack_vol_key, 1)

        # Resolve owner display name (never use companion bot username)
        if message.from_user and not message.from_user.is_bot:
            user_display = message.from_user.username or message.from_user.first_name or "My"
        else:
            user = await client.get_me()
            if not getattr(user, "is_bot", False):
                user_display = user.username or user.first_name or "My"
            else:
                user_display = "My"

        pack_name = f"{p_prefix}_{owner_id}_{pack_vol}_by_{bot_username}"
        pack_title = f"{user_display}'s {sticker_format.capitalize()} Vol.{pack_vol}"

        # Inspect pack existence via Telegram Bot API
        check_url = f"https://api.telegram.org/bot{token}/getStickerSet"
        pack_resp = requests.get(check_url, params={"name": pack_name}, timeout=10).json()

        pack_exists = pack_resp.get("ok", False)
        if pack_exists:
            # Sync title from Telegram so message always matches the real pack title
            pack_title = pack_resp["result"].get("title", pack_title)
            current_stickers = pack_resp["result"].get("stickers", [])
            if len(current_stickers) >= max_limit:
                pack_vol += 1
                db.set(pack_vol_key, pack_vol)
                pack_name = f"{p_prefix}_{owner_id}_{pack_vol}_by_{bot_username}"
                pack_title = f"{user_display}'s {sticker_format.capitalize()} Vol.{pack_vol}"
                pack_exists = False

        sticker_item = {
            "sticker": "attach://sticker",
            "format": sticker_format,
            "emoji_list": [emoji],
        }

        # Upload to Bot API
        mime_type = "image/png" if sticker_format == "static" else "application/octet-stream"
        with open(final_file, "rb") as sf:
            files = {"sticker": (os.path.basename(final_file), sf, mime_type)}

            if not pack_exists:
                data = {
                    "user_id": owner_id,
                    "name": pack_name,
                    "title": pack_title,
                    "stickers": json.dumps([sticker_item]),
                }
                api_url = f"https://api.telegram.org/bot{token}/createNewStickerSet"
                res = requests.post(api_url, data=data, files=files, timeout=30).json()
            else:
                data = {
                    "user_id": owner_id,
                    "name": pack_name,
                    "sticker": json.dumps(sticker_item),
                }
                api_url = f"https://api.telegram.org/bot{token}/addStickerToSet"
                res = requests.post(api_url, data=data, files=files, timeout=30).json()

        # Handle size exceeded error if Telegram pack filled up concurrently
        if not res.get("ok"):
            err_desc = res.get("description", "")
            if "STICKERS_TOO_MUCH" in err_desc or "STICKERPACK_SIZE_EXCEEDED" in err_desc:
                pack_vol += 1
                db.set(pack_vol_key, pack_vol)
                pack_name = f"{p_prefix}_{owner_id}_{pack_vol}_by_{bot_username}"
                pack_title = f"{user_display}'s {sticker_format.capitalize()} Vol.{pack_vol}"
                with open(final_file, "rb") as sf:
                    files = {"sticker": (os.path.basename(final_file), sf, mime_type)}
                    data = {
                        "user_id": owner_id,
                        "name": pack_name,
                        "title": pack_title,
                        "stickers": json.dumps([sticker_item]),
                    }
                    res = requests.post(f"https://api.telegram.org/bot{token}/createNewStickerSet", data=data, files=files, timeout=30).json()

        if not res.get("ok"):
            return await edit_or_reply(status_msg, f"Sticker API Error: {res.get('description', 'Unknown error')}")

        pack_info = requests.get(f"https://api.telegram.org/bot{token}/getStickerSet", params={"name": pack_name}, timeout=10).json()
        count_str = ""
        new_sticker_id = None
        if pack_info.get("ok"):
            stickers_list = pack_info["result"].get("stickers", [])
            count_str = f" ({len(stickers_list)}/{max_limit})"
            if stickers_list:
                new_sticker_id = stickers_list[-1]["file_id"]

        pack_link = f"https://t.me/addstickers/{pack_name}"
        card = (
            "**Sticker Kanged**\n"
            f"• **Emoji:** {emoji}\n"
            f"• **Format:** `{sticker_format.capitalize()}`\n"
            f"• **Pack:** [{pack_title}{count_str}]({pack_link})"
        )
        await edit_or_reply(status_msg, card)
        if new_sticker_id:
            try:
                await client.send_sticker(chat_id=message.chat.id, sticker=new_sticker_id)
            except Exception:
                pass

    except Exception as e:
        await edit_or_reply(status_msg, f"Kang failed: {e}")
    finally:
        for p in (downloaded, final_file if created_temp else None):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

@on_cmd(["packinfo", "stickers"], desc="Show your current sticker pack links and status", usage="")
async def packinfo_cmd(client: Client, message: Message):
    token, bot_username = _get_bot_info()
    if not token or not bot_username:
        return await edit_or_reply(message, "Companion BOT_TOKEN is not configured in `.env`.")

    owner_id = getattr(PyGramClient, "OWNER_ID", None) or config.get_owner_id()
    if not owner_id and message.from_user:
        owner_id = message.from_user.id
    if not owner_id:
        return await edit_or_reply(message, "Could not determine owner ID.")

    status_msg = await edit_or_reply(message, "Fetching pack info...")

    types = [
        ("Static", "k", "static", MAX_STATIC_PACK),
        ("Animated", "ka", "animated", MAX_DYNAMIC_PACK),
        ("Video", "kv", "video", MAX_DYNAMIC_PACK),
    ]

    lines = ["**Your Sticker Packs**"]
    check_url = f"https://api.telegram.org/bot{token}/getStickerSet"

    for label, prefix, format_key, max_size in types:
        vol = db.get(f"KANG_VOL_{format_key}_{owner_id}", 1)
        pack_name = f"{prefix}_{owner_id}_{vol}_by_{bot_username}"
        try:
            resp = requests.get(check_url, params={"name": pack_name}, timeout=5).json()
            if resp.get("ok"):
                title = resp["result"].get("title") or f"{label} Vol.{vol}"
                count = len(resp["result"].get("stickers", []))
                lines.append(f"• **{title}:** [{count}/{max_size} stickers](https://t.me/addstickers/{pack_name})")
            else:
                lines.append(f"• **{label} (Vol.{vol}):** Not created yet")
        except Exception:
            lines.append(f"• **{label} (Vol.{vol}):** Could not query status")

    await edit_or_reply(status_msg, "\n".join(lines))
