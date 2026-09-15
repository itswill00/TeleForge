import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import json
import time
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply

logger = logging.getLogger("pygramx.pray")

# ponytail: free Aladhan API (no key). Kemenag method first, regional fallbacks. Day cache.
_CACHE: Dict[str, tuple] = {}
TTL = 21600  # 6 hours
METHODS = (20, 11, 3)  # Kemenag Indonesia, Singapore, Muslim World League
ORDER = ["Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha"]


def _get(url: str) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TeleForge/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        logger.warning("pray fetch failed: %s", e)
        return None


def _timings(city: str, country: str) -> Tuple[Optional[Dict[str, str]], str]:
    key = f"{city.lower()}|{country.lower()}|{datetime.now(timezone.utc).date()}"
    if key in _CACHE and time.time() - _CACHE[key][0] < TTL:
        return _CACHE[key][1], _CACHE[key][2]
    for method in METHODS:
        q = urllib.parse.urlencode({"city": city, "country": country, "method": method})
        d = _get(f"https://api.aladhan.com/v1/timingsByCity?{q}")
        try:
            timings = d["data"]["timings"]
            clean = {k: v.split()[0] for k, v in timings.items() if k in ORDER}
            if len(clean) == 6:
                tz = d["data"]["meta"].get("timezone", "UTC")
                _CACHE[key] = (time.time(), clean, tz)
                return clean, tz
        except (KeyError, TypeError, AttributeError):
            continue
    return None, ""


@on_cmd(
    ["pray", "sholat", "prayer"],
    desc="Check daily prayer times and next prayer countdown",
    usage="[city] [country]",
)
async def pray_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    parts = text.split(maxsplit=2)
    rest = parts[1].strip() if len(parts) > 1 else ""
    if "," in rest:
        city, country = [s.strip() for s in rest.split(",", 1)]
    else:
        words = rest.split()
        city = " ".join(words[:2]) if words else "Jakarta"
        country = " ".join(words[2:]) if len(words) > 2 else "Indonesia"
    city = city[:60] or "Jakarta"
    country = country[:60] or "Indonesia"

    status = await edit_or_reply(message, f"Fetching prayer times for **{city}**...")
    timings, tz = await asyncio.to_thread(_timings, city, country)
    if not timings:
        await edit_or_reply(status, f"Prayer times for **{city}** are unavailable. Check the city spelling.")
        return

    now_local: Optional[datetime] = None
    try:
        from zoneinfo import ZoneInfo
        now_local = datetime.now(ZoneInfo(tz))
    except Exception:
        now_local = None

    next_name, next_in = "", ""
    if now_local:
        hm = now_local.strftime("%H:%M")
        for name in ORDER:
            if timings[name] > hm:
                next_name = name
                t = datetime.strptime(timings[name], "%H:%M").replace(
                    year=now_local.year, month=now_local.month, day=now_local.day
                )
                delta = t - now_local.replace(second=0, microsecond=0)
                h, rem = divmod(int(delta.total_seconds()), 3600)
                m = rem // 60
                next_in = f"{h}h {m}m" if h else f"{m}m"
                break
        if not next_name:
            next_name = "Fajr"
            next_in = "tomorrow"

    lines = [f"**Prayer Times - {city.title()}**"]
    for name in ORDER:
        mark = " ◀ next" if name == next_name else ""
        lines.append(f"• **{name}:** `{timings[name]}`{mark}")
    if next_name and next_in:
        lines.append(f"\nNext prayer **{next_name}** in `{next_in}` (`{tz}`)")
    await edit_or_reply(status, "\n".join(lines))
