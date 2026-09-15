import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import os
import subprocess
import time
from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply, get_client_prefix

def _has_audio_stream(file_path: str) -> bool:
    """Check if file contains an active audio stream using ffprobe."""
    try:
        res = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a",
                "-show_entries", "stream=codec_type",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        return "audio" in res.stdout.lower()
    except Exception:
        return False

def _get_media_duration(file_path: str) -> int:
    """Get media duration in seconds using ffprobe."""
    try:
        res = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if res.returncode == 0 and res.stdout.strip():
            return int(float(res.stdout.strip()))
    except Exception:
        pass
    return 0

def _is_video_media(reply: Message | None) -> bool:
    """Check if message contains playable or convertible video media."""
    if not reply:
        return False
    return bool(
        reply.video
        or reply.video_note
        or reply.animation
        or (reply.sticker and getattr(reply.sticker, "is_video", False))
        or (reply.document and getattr(reply.document, "mime_type", "").startswith("video/"))
    )

def _is_audio_media(reply: Message | None) -> bool:
    """Check if message contains audio or voice media."""
    if not reply:
        return False
    return bool(
        reply.audio
        or reply.voice
        or (reply.document and getattr(reply.document, "mime_type", "").startswith("audio/"))
    )

def _parse_time(t_str: str) -> float | None:
    """Parse time string in seconds, MM:SS, or HH:MM:SS format to total seconds."""
    if not t_str or not isinstance(t_str, str):
        return None
    t_str = t_str.strip()
    parts = t_str.split(":")
    if len(parts) == 1:
        try:
            val = float(parts[0])
            return val if val >= 0 else None
        except ValueError:
            return None
    elif len(parts) == 2:
        try:
            m, s = float(parts[0]), float(parts[1])
            return (m * 60 + s) if m >= 0 and s >= 0 else None
        except ValueError:
            return None
    elif len(parts) == 3:
        try:
            h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
            return (h * 3600 + m * 60 + s) if h >= 0 and m >= 0 and s >= 0 else None
        except ValueError:
            return None
    return None

async def _run_ffmpeg(cmd: list[str], timeout: float = 60.0) -> bool:
    """Execute an FFmpeg command asynchronously with timeout and guaranteed process cleanup."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode == 0
    except (asyncio.TimeoutError, Exception):
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return False

@on_cmd(["round", "tovideo", "telescope"], desc="Convert replied video or gif into circular video note", usage="")
async def round_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply or not _is_video_media(reply):
        return await edit_or_reply(message, "Reply to a video, GIF, or video sticker to convert.")

    status_msg = await edit_or_reply(message, "Processing video note...")

    os.makedirs("downloads", exist_ok=True)
    ts = int(time.time() * 1000)
    raw_path = f"downloads/raw_vid_{ts}"
    out_path = f"downloads/round_{ts}.mp4"
    downloaded = None

    try:
        downloaded = await reply.download(file_name=raw_path)
        if not downloaded or not os.path.isfile(downloaded):
            return await edit_or_reply(status_msg, "Failed to download video from Telegram.")

        has_audio = _has_audio_stream(downloaded)

        # Crop to square 1:1, scale to 384x384, cap duration at 60s
        if has_audio:
            cmd = [
                "ffmpeg", "-y",
                "-i", downloaded,
                "-t", "60",
                "-filter_complex", r"[0:v]crop=min(iw\,ih):min(iw\,ih),scale=384:384:flags=lanczos,format=yuv420p[v]",
                "-map", "[v]",
                "-map", "0:a",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-b:a", "64k",
                out_path
            ]
        else:
            # Generate silent audio track for seamless Telegram video note compatibility
            cmd = [
                "ffmpeg", "-y",
                "-i", downloaded,
                "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=44100",
                "-t", "60",
                "-filter_complex", r"[0:v]crop=min(iw\,ih):min(iw\,ih),scale=384:384:flags=lanczos,format=yuv420p[v]",
                "-map", "[v]",
                "-map", "1:a",
                "-shortest",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-b:a", "32k",
                out_path
            ]

        ok = await _run_ffmpeg(cmd, timeout=60.0)
        if not ok or not os.path.isfile(out_path):
            return await edit_or_reply(status_msg, "Failed to encode circular video note.")

        duration = _get_media_duration(out_path)
        reply_to_id = reply.id if reply else None

        await client.send_video_note(
            chat_id=message.chat.id,
            video_note=out_path,
            duration=duration,
            length=384,
            reply_to_message_id=reply_to_id,
        )
        await status_msg.delete()

    except Exception as e:
        await edit_or_reply(status_msg, f"Video note conversion failed: {e}")
    finally:
        for p in (downloaded, raw_path, out_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

@on_cmd(["tovn", "tovoice", "vn"], desc="Convert replied video or audio into voice note", usage="")
async def tovn_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply or not (_is_video_media(reply) or _is_audio_media(reply)):
        return await edit_or_reply(message, "Reply to an audio, video, or voice message to convert.")

    status_msg = await edit_or_reply(message, "Extracting voice note...")

    os.makedirs("downloads", exist_ok=True)
    ts = int(time.time() * 1000)
    raw_path = f"downloads/raw_audio_{ts}"
    out_path = f"downloads/voice_{ts}.ogg"
    downloaded = None

    try:
        downloaded = await reply.download(file_name=raw_path)
        if not downloaded or not os.path.isfile(downloaded):
            return await edit_or_reply(status_msg, "Failed to download media from Telegram.")

        if not _has_audio_stream(downloaded):
            return await edit_or_reply(status_msg, "The replied media contains no audio stream.")

        # Convert to OGG Opus standard Telegram voice format
        cmd = [
            "ffmpeg", "-y",
            "-i", downloaded,
            "-vn",
            "-c:a", "libopus",
            "-b:a", "32k",
            "-ar", "24000",
            out_path
        ]

        ok = await _run_ffmpeg(cmd, timeout=30.0)
        if not ok or not os.path.isfile(out_path):
            return await edit_or_reply(status_msg, "Failed to encode voice note.")

        duration = _get_media_duration(out_path)
        reply_to_id = reply.id if reply else None

        await client.send_voice(
            chat_id=message.chat.id,
            voice=out_path,
            duration=duration,
            reply_to_message_id=reply_to_id,
        )
        await status_msg.delete()

    except Exception as e:
        await edit_or_reply(status_msg, f"Voice conversion failed: {e}")
    finally:
        for p in (downloaded, raw_path, out_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

async def _apply_audio_effect(client: Client, message: Message, filter_str: str, effect_name: str):
    reply = message.reply_to_message
    if not reply or not (reply.audio or reply.voice or reply.video):
        return await edit_or_reply(message, f"Reply to an audio or voice note to apply {effect_name} effect.")

    status_msg = await edit_or_reply(message, f"Applying {effect_name} effect...")

    os.makedirs("downloads", exist_ok=True)
    ts = int(time.time() * 1000)
    raw_path = f"downloads/fx_raw_{ts}"
    out_path = f"downloads/fx_out_{ts}.ogg"

    try:
        downloaded = await reply.download(file_name=raw_path)
        if not downloaded or not os.path.isfile(downloaded):
            return await edit_or_reply(status_msg, "Failed to download media from Telegram.")

        if not _has_audio_stream(downloaded):
            return await edit_or_reply(status_msg, "The replied media contains no audio stream.")

        cmd = [
            "ffmpeg", "-y",
            "-i", downloaded,
            "-vn",
            "-af", filter_str,
            "-c:a", "libopus",
            "-b:a", "32k",
            out_path
        ]

        ok = await _run_ffmpeg(cmd, timeout=30.0)
        if not ok or not os.path.isfile(out_path):
            return await edit_or_reply(status_msg, f"Failed to apply {effect_name} effect.")

        duration = _get_media_duration(out_path)
        reply_to_id = reply.id if reply else None

        await client.send_voice(
            chat_id=message.chat.id,
            voice=out_path,
            duration=duration,
            caption=f"Effect: {effect_name}",
            reply_to_message_id=reply_to_id,
        )
        await status_msg.delete()
    except Exception as err:
        await edit_or_reply(status_msg, f"Effect error: {err}")
    finally:
        for p in (downloaded if "downloaded" in locals() else None, raw_path, out_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

@on_cmd(["bass", "boost"], desc="Boost bass on replied audio or voice note", usage="")
async def bass_cmd(client: Client, message: Message):
    await _apply_audio_effect(client, message, "bass=g=14", "Bass Boost")

@on_cmd(["fast", "speedup"], desc="Speed up replied audio to 1.5x", usage="")
async def fast_cmd(client: Client, message: Message):
    await _apply_audio_effect(client, message, "atempo=1.5", "1.5x Speed")

@on_cmd(["slow", "slowed", "slowmo"], desc="Slow down replied audio to 0.75x", usage="")
async def slow_cmd(client: Client, message: Message):
    await _apply_audio_effect(client, message, "atempo=0.75", "0.75x Slow")

@on_cmd(["echo"], desc="Add echo effect to replied audio", usage="")
async def echo_cmd(client: Client, message: Message):
    await _apply_audio_effect(client, message, "aecho=0.8:0.9:1000:0.3", "Echo")

@on_cmd(["reverse", "areverse"], desc="Reverse replied audio", usage="")
async def reverse_cmd(client: Client, message: Message):
    await _apply_audio_effect(client, message, "areverse", "Reverse")

@on_cmd(["trim", "cut"], desc="Trim video or audio losslessly", usage="<start> <end>")
async def trim_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply or not (_is_video_media(reply) or _is_audio_media(reply)):
        return await edit_or_reply(message, "Reply to an audio, video, or voice message to trim.")

    args = (message.text or message.caption or "").split()
    if len(args) < 3:
        prefix = get_client_prefix(client)
        return await edit_or_reply(message, f"Usage: `{prefix}trim <start> <end>` (e.g. `{prefix}trim 00:05 00:20`)")

    start_sec = _parse_time(args[1])
    end_sec = _parse_time(args[2])
    if start_sec is None or end_sec is None:
        return await edit_or_reply(message, "Invalid timestamp format. Use SS (e.g. 5) or MM:SS (e.g. 01:20).")
    if start_sec >= end_sec:
        return await edit_or_reply(message, "Start time must be strictly less than end time.")

    status_msg = await edit_or_reply(message, "Trimming media losslessly...")

    os.makedirs("downloads", exist_ok=True)
    ts = int(time.time() * 1000)
    raw_path = f"downloads/raw_trim_{ts}"
    out_path = None
    downloaded = None

    try:
        downloaded = await reply.download(file_name=raw_path)
        if not downloaded or not os.path.isfile(downloaded):
            return await edit_or_reply(status_msg, "Failed to download media.")

        ext = os.path.splitext(downloaded)[1] or (".ogg" if reply.voice else ".mp4")
        out_path = f"downloads/trim_{ts}{ext}"

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-to", str(end_sec),
            "-i", downloaded,
            "-c", "copy",
            out_path,
        ]
        ok = await _run_ffmpeg(cmd, timeout=45.0)
        if not ok or not os.path.isfile(out_path):
            return await edit_or_reply(status_msg, "Failed to trim media. Check time format.")

        duration = _get_media_duration(out_path)
        reply_to_id = reply.id if reply else None

        if reply.voice:
            await client.send_voice(message.chat.id, out_path, duration=duration, reply_to_message_id=reply_to_id)
        elif reply.audio:
            await client.send_audio(message.chat.id, out_path, duration=duration, reply_to_message_id=reply_to_id)
        elif reply.video_note and duration <= 60:
            await client.send_video_note(message.chat.id, out_path, duration=duration, reply_to_message_id=reply_to_id)
        else:
            await client.send_video(message.chat.id, out_path, duration=duration, reply_to_message_id=reply_to_id)

        await status_msg.delete()
    except Exception as e:
        await edit_or_reply(status_msg, f"Trim failed: {e}")
    finally:
        for p in (downloaded, raw_path, out_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

@on_cmd(["togif", "gif"], desc="Convert video or video note to silent looping GIF", usage="")
async def togif_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply or not _is_video_media(reply):
        return await edit_or_reply(message, "Reply to a video or video note to convert to GIF.")

    status_msg = await edit_or_reply(message, "Converting to looping GIF...")

    os.makedirs("downloads", exist_ok=True)
    ts = int(time.time() * 1000)
    raw_path = f"downloads/raw_gif_{ts}"
    out_path = f"downloads/gif_{ts}.mp4"
    downloaded = None

    try:
        downloaded = await reply.download(file_name=raw_path)
        if not downloaded or not os.path.isfile(downloaded):
            return await edit_or_reply(status_msg, "Failed to download media.")

        cmd = [
            "ffmpeg", "-y",
            "-i", downloaded,
            "-an",
            "-t", "60",
            "-vf", "scale='min(480,iw)':-2",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast",
            out_path,
        ]
        ok = await _run_ffmpeg(cmd, timeout=60.0)
        if not ok or not os.path.isfile(out_path):
            return await edit_or_reply(status_msg, "Failed to encode GIF.")

        await client.send_animation(
            chat_id=message.chat.id,
            animation=out_path,
            reply_to_message_id=reply.id,
        )
        await status_msg.delete()
    except Exception as e:
        await edit_or_reply(status_msg, f"GIF conversion failed: {e}")
    finally:
        for p in (downloaded, raw_path, out_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

@on_cmd(["frame", "screenshot", "ss"], desc="Extract high-res frame from video at timestamp", usage="[timestamp]")
async def frame_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply or not _is_video_media(reply):
        return await edit_or_reply(message, "Reply to a video to extract a frame.")

    args = (message.text or message.caption or "").split(maxsplit=1)
    raw_ts = args[1].strip() if len(args) > 1 else "00:00:01"
    parsed_sec = _parse_time(raw_ts)
    if parsed_sec is None:
        return await edit_or_reply(message, "Invalid timestamp format. Use SS (e.g. 10) or MM:SS (e.g. 01:20).")

    status_msg = await edit_or_reply(message, f"Extracting frame at `{raw_ts}`...")

    os.makedirs("downloads", exist_ok=True)
    ts = int(time.time() * 1000)
    raw_path = f"downloads/raw_frame_{ts}"
    out_path = f"downloads/frame_{ts}.jpg"
    downloaded = None

    try:
        downloaded = await reply.download(file_name=raw_path)
        if not downloaded or not os.path.isfile(downloaded):
            return await edit_or_reply(status_msg, "Failed to download video.")

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(parsed_sec),
            "-i", downloaded,
            "-vframes", "1",
            "-q:v", "2",
            out_path,
        ]
        ok = await _run_ffmpeg(cmd, timeout=20.0)
        if not ok or not os.path.isfile(out_path):
            return await edit_or_reply(status_msg, "Failed to capture frame. Check timestamp.")

        await client.send_photo(
            chat_id=message.chat.id,
            photo=out_path,
            caption=f"Frame at `{raw_ts}`",
            reply_to_message_id=reply.id,
        )
        await status_msg.delete()
    except Exception as e:
        await edit_or_reply(status_msg, f"Frame extraction failed: {e}")
    finally:
        for p in (downloaded, raw_path, out_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

@on_cmd(["mutevid", "stripaudio", "mutev"], desc="Remove audio track from video losslessly", usage="")
async def mutevid_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply or not _is_video_media(reply):
        return await edit_or_reply(message, "Reply to a video to strip its audio.")

    status_msg = await edit_or_reply(message, "Removing audio track losslessly...")

    os.makedirs("downloads", exist_ok=True)
    ts = int(time.time() * 1000)
    raw_path = f"downloads/raw_mute_{ts}"
    out_path = f"downloads/mute_{ts}.mp4"
    downloaded = None

    try:
        downloaded = await reply.download(file_name=raw_path)
        if not downloaded or not os.path.isfile(downloaded):
            return await edit_or_reply(status_msg, "Failed to download video.")

        cmd = [
            "ffmpeg", "-y",
            "-i", downloaded,
            "-an",
            "-c:v", "copy",
            out_path,
        ]
        ok = await _run_ffmpeg(cmd, timeout=30.0)
        if not ok or not os.path.isfile(out_path):
            return await edit_or_reply(status_msg, "Failed to strip audio.")

        duration = _get_media_duration(out_path)
        await client.send_video(
            chat_id=message.chat.id,
            video=out_path,
            duration=duration,
            reply_to_message_id=reply.id,
        )
        await status_msg.delete()
    except Exception as e:
        await edit_or_reply(status_msg, f"Audio stripping failed: {e}")
    finally:
        for p in (downloaded, raw_path, out_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

