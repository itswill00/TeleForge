import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply, send_large_output, get_client_prefix

# ponytail: offline 2026 calendars (no API exists for free). Check official sites for exact dates.
TENNIS = [
    {"name": "Australian Open", "city": "Melbourne", "surface": "Hard", "start": "2026-01-18", "end": "2026-02-01"},
    {"name": "Roland Garros", "city": "Paris", "surface": "Clay", "start": "2026-05-24", "end": "2026-06-07"},
    {"name": "Wimbledon", "city": "London", "surface": "Grass", "start": "2026-06-29", "end": "2026-07-12"},
    {"name": "US Open", "city": "New York", "surface": "Hard", "start": "2026-08-31", "end": "2026-09-13"},
]

BADMIN = [
    {"name": "Malaysia Open", "level": "Super 1000", "city": "Kuala Lumpur", "start": "2026-01-06", "end": "2026-01-11"},
    {"name": "Indonesia Masters", "level": "Super 500", "city": "Jakarta", "start": "2026-01-20", "end": "2026-01-25"},
    {"name": "All England Open", "level": "Super 1000", "city": "Birmingham", "start": "2026-03-17", "end": "2026-03-22"},
    {"name": "Indonesia Open", "level": "Super 1000", "city": "Jakarta", "start": "2026-06-02", "end": "2026-06-07"},
    {"name": "Japan Open", "level": "Super 750", "city": "Tokyo", "start": "2026-07-21", "end": "2026-07-26"},
    {"name": "China Open", "level": "Super 1000", "city": "Changzhou", "start": "2026-09-01", "end": "2026-09-06"},
    {"name": "Denmark Open", "level": "Super 750", "city": "Odense", "start": "2026-10-13", "end": "2026-10-18"},
    {"name": "World Tour Finals", "level": "Finals", "city": "Hangzhou", "start": "2026-12-09", "end": "2026-12-13"},
]


def _d(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _status(ev: Dict[str, str], now: datetime) -> str:
    s, e = _d(ev["start"]), _d(ev["end"])
    if now > e:
        return "Completed"
    if s <= now <= e:
        return "Live now"
    days = (s.date() - now.date()).days
    if days == 0:
        return "Starts today"
    if days == 1:
        return "Starts tomorrow"
    return f"{days} days remaining"


def _card(ev: Dict[str, str], now: datetime, tag: str) -> str:
    extra = ev.get("surface") or ev.get("level", "")
    return (
        f"**{tag} - {ev['name']}**\n"
        f"• **Venue:** {ev['city']} ({extra})\n"
        f"• **Dates:** `{ev['start']}` to `{ev['end']}`\n"
        f"• **Status:** {_status(ev, now)}"
    )


def _handle(title: str, tag: str, events: List[Dict[str, str]], query: str, prefix: str) -> Tuple[str, bool]:
    now = datetime.now(timezone.utc)
    q = query.lower()
    if q in ("schedule", "list", "calendar", "all"):
        lines = [f"**{title} 2026 Calendar ({len(events)} events)**"]
        for ev in events:
            lines.append(f"• **{ev['name']}** ({ev['city']}) | `{ev['start']}` (`{_status(ev, now)}`)")
        return "\n".join(lines), len("\n".join(lines)) > 3900
    if q and q != "next":
        hit = next((e for e in events if q in e["name"].lower() or q in e["city"].lower()), None)
        if not hit:
            return f"{tag} event matching '{query}' was not found.", False
        return _card(hit, now, tag), False
    nxt = next((e for e in events if _d(e["end"]) >= now), None)
    if not nxt:
        return f"No upcoming {title} events remaining in 2026.", False
    out = _card(nxt, now, tag)
    others = [e for e in events if _d(e["end"]) >= now and e is not nxt][:3]
    if others:
        out += "\n\n**Also Upcoming**\n" + "\n".join(
            f"• **{e['name']}** | `{e['start']}` (`{_status(e, now)}`)" for e in others
        )
    return out, False


@on_cmd(
    ["tennis", "atp", "wta", "slams"],
    desc="Check 2026 Grand Slam tennis schedule and countdown",
    usage="[next|schedule|<name>]",
)
async def tennis_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    parts = text.split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else "next"
    out, big = _handle("Grand Slam Tennis", "Tennis", TENNIS, query, get_client_prefix(client))
    if big:
        await send_large_output(message, out, filename="tennis_schedule.txt")
    else:
        await edit_or_reply(message, out)


@on_cmd(
    ["badmin", "badminton", "bwf"],
    desc="Check 2026 BWF World Tour badminton schedule and countdown",
    usage="[next|schedule|<name>]",
)
async def badmin_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    parts = text.split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else "next"
    out, big = _handle("BWF World Tour", "Badminton", BADMIN, query, get_client_prefix(client))
    if big:
        await send_large_output(message, out, filename="badminton_schedule.txt")
    else:
        await edit_or_reply(message, out)
