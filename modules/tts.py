import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply, get_client_prefix

USER_AGENT = "Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"

# List of common 2-letter language codes supported by Google TTS
SUPPORTED_LANGS = {
    "id", "en", "ja", "ko", "jv", "su", "ar", "es", "fr", "de", "ru", "pt", "it", "zh", "th", "vi", "hi"
}

def _chunk_text(text: str, max_chars: int = 150) -> list[str]:
    """Split text into sentence-sized chunks under max_chars for Google TTS."""
    sentences = re.split(r"([.!?,;\n]+)", text)
    chunks = []
    current = ""
    for piece in sentences:
        if len(current) + len(piece) <= max_chars:
            current += piece
        else:
            if current.strip():
                chunks.append(current.strip())
            current = piece
    if current.strip():
        chunks.append(current.strip())
    # Fallback for words longer than max_chars
    final_chunks = []
    for c in chunks:
        while len(c) > max_chars:
            final_chunks.append(c[:max_chars])
            c = c[max_chars:]
        if c:
            final_chunks.append(c)
    return final_chunks

def _fetch_tts_mp3(text: str, lang: str = "id") -> bytes:
    """Fetch multi-chunk MP3 bytes from Google TTS endpoint."""
    chunks = _chunk_text(text)
    combined = bytearray()
    for chunk in chunks:
        query = urllib.parse.quote(chunk)
        url = f"https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&tl={lang}&q={query}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            combined.extend(resp.read())
    return bytes(combined)

@on_cmd(["tts", "voice"], desc="Convert text to Google voice message", usage="[lang] <text or reply>")
async def tts_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    text = message.text or message.caption or ""
    parts = text.split(maxsplit=2)
    prefix = get_client_prefix(client)

    lang = "id"
    payload = ""

    # Parse optional language prefix (e.g. .tts en hello or .tts -en hello)
    if len(parts) > 1:
        cand = parts[1].lstrip("-").lower()
        if cand in SUPPORTED_LANGS:
            lang = cand
            if len(parts) > 2:
                payload = parts[2].strip()
        else:
            payload = text.split(maxsplit=1)[1].strip()

    # If no payload in command text, check replied message
    if not payload and reply:
        payload = reply.text or reply.caption or ""

    if not payload:
        return await edit_or_reply(
            message,
            f"Usage: `{prefix}tts [lang] <text>` or reply to a text message.\n"
            f"Languages: `id`, `en`, `ja`, `ko`, `jv`, `ar`, `es`, `fr`, etc.",
        )

    status_msg = await edit_or_reply(message, f"Generating voice ({lang})...")

    os.makedirs("downloads", exist_ok=True)
    ts = int(time.time() * 1000)
    mp3_path = f"downloads/tts_{ts}.mp3"
    ogg_path = f"downloads/tts_{ts}.ogg"

    try:
        mp3_data = _fetch_tts_mp3(payload, lang=lang)
        if not mp3_data:
            return await edit_or_reply(status_msg, "Failed to generate speech audio.")

        with open(mp3_path, "wb") as f:
            f.write(mp3_data)

        # Convert to OGG Opus voice message via ffmpeg if available
        voice_file = mp3_path
        res = subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_path, "-c:a", "libopus", "-b:a", "32k", ogg_path],
            capture_output=True,
            timeout=10.0,
        )
        if res.returncode == 0 and os.path.isfile(ogg_path):
            voice_file = ogg_path

        reply_to_id = reply.id if reply else None

        await client.send_voice(
            chat_id=message.chat.id,
            voice=voice_file,
            caption=f"Voice: `{lang}`",
            reply_to_message_id=reply_to_id,
        )
        await status_msg.delete()

    except Exception as e:
        await edit_or_reply(status_msg, f"TTS failed: {e}")
    finally:
        for p in (mp3_path, ogg_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
