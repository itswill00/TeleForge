import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import json
import time
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply, send_large_output, get_client_prefix

logger = logging.getLogger("pygramx.football")

WIB = timezone(timedelta(hours=7))

# ponytail: live-only via free OpenLigaDB (no key), 5-min cache. No bundled dataset.
LEAGUES = {
    "epl": ("epl", "English Premier League"),
    "bl1": ("bl1", "Bundesliga"),
    "ucl": ("ucl", "UEFA Champions League"),
    "uel": ("uel", "UEFA Europa League"),
    "wc": ("wm26", "FIFA World Cup 2026"),
}

_CACHE: Dict[str, tuple] = {}
TTL = 300


def _get(url: str) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TeleForge/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        logger.warning("football fetch failed %s: %s", url, e)
        return None


def _matches(shortcut: str) -> List[Dict[str, Any]]:
    now = time.time()
    if shortcut in _CACHE and now - _CACHE[shortcut][0] < TTL:
        return _CACHE[shortcut][1]
    data = _get(f"https://api.openligadb.de/getmatchdata/{shortcut}") or []
    _CACHE[shortcut] = (now, data)
    return data


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _fmt_time(s: Optional[str]) -> str:
    dt = _parse_dt(s)
    if not dt:
        return s or "TBA"
    return f"{dt.strftime('%Y-%m-%d %H:%M')} UTC ({dt.astimezone(WIB).strftime('%H:%M')} WIB)"


def _score(m: Dict[str, Any]) -> str:
    res = m.get("matchResults") or []
    if not res:
        return "vs"
    r = res[-1]
    return f"{r.get('pointsTeam1', 0)} - {r.get('pointsTeam2', 0)}"


def _fmt_match(m: Dict[str, Any]) -> str:
    t1 = (m.get("team1") or {}).get("shortName", "?")
    t2 = (m.get("team2") or {}).get("shortName", "?")
    grp = (m.get("group") or {}).get("groupName", "")
    when = _fmt_time(m.get("matchDateTimeUTC") or m.get("matchDateTime"))
    if m.get("matchIsFinished"):
        return f"• **{t1}** {_score(m)} **{t2}** | `{grp}` (FT)"
    return f"• **{t1}** vs **{t2}** | `{when}` (`{grp}`)"


def _resolve(arg: str) -> tuple:
    a = (arg or "").lower().strip()
    for alias, (shortcut, title) in LEAGUES.items():
        if a in (alias, shortcut, title.lower()):
            return shortcut, title
    return "bl1", "Bundesliga"


@on_cmd(
    ["bola", "football", "soccer"],
    desc="Check football fixtures, live scores, results and standings",
    usage="[next|live|results|table] [epl|bl1|ucl|uel|wc]",
)
async def bola_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    parts = text.split()
    args = [p.lower() for p in parts[1:]]

    prefix = get_client_prefix(client)
    sub = args[0] if args else "next"
    league_arg = args[1] if len(args) > 1 else args[0] if args else ""
    if sub in LEAGUES or sub in [v[0] for v in LEAGUES.values()]:
        league_arg, sub = sub, "next"
    shortcut, title = _resolve(league_arg) if league_arg else ("bl1", "Bundesliga")

    if sub in ("table", "standings", "standing", "points"):
        table = await asyncio.to_thread(_get, f"https://api.openligadb.de/getbltable/{shortcut}/2025")
        if not table:
            table = await asyncio.to_thread(_get, f"https://api.openligadb.de/getbltable/{shortcut}/2026")
        if not table:
            await edit_or_reply(message, f"**{title} Standings**\nNo table data available yet.")
            return
        lines = [f"**{title} Standings**"]
        for i, row in enumerate(table[:10], 1):
            name = row.get("shortName") or row.get("teamName", "?")
            lines.append(
                f"{i}. **{name}** - {row.get('points', 0)} pts "
                f"(`{row.get('matches', 0)}P {row.get('won', 0)}W {row.get('draw', 0)}D {row.get('lost', 0)}L`)"
            )
        if len(table) > 10:
            lines.append(f"\nShowing Top 10. Use `{prefix}bola table {shortcut} all` for full table.")
            if args and "all" in args:
                extra = [f"{i}. **{r.get('shortName') or r.get('teamName')}** - {r.get('points', 0)} pts" for i, r in enumerate(table, 1)]
                await send_large_output(message, "\n".join([f"**{title} Full Standings**"] + extra), filename="football_table.txt")
                return
        await edit_or_reply(message, "\n".join(lines))
        return

    matches = await asyncio.to_thread(_matches, shortcut)
    if not matches:
        await edit_or_reply(message, f"**{title}**\nNo fixture data available.")
        return

    now = datetime.now(timezone.utc)
    if sub in ("live",):
        live = [m for m in matches if not m.get("matchIsFinished") and (_parse_dt(m.get("matchDateTimeUTC") or m.get("matchDateTime")) or now) <= now]
        if not live:
            await edit_or_reply(message, f"**{title} Live**\nNo matches currently in progress.")
            return
        await edit_or_reply(message, f"**{title} Live ({len(live)})**\n" + "\n".join(_fmt_match(m) for m in live))
        return

    if sub in ("results", "result", "scores", "ft"):
        done = [m for m in matches if m.get("matchIsFinished")][-10:]
        if not done:
            await edit_or_reply(message, f"**{title} Results**\nNo finished matches yet.")
            return
        await edit_or_reply(message, f"**{title} Latest Results**\n" + "\n".join(_fmt_match(m) for m in reversed(done)))
        return

    upcoming = sorted(
        [m for m in matches if not m.get("matchIsFinished")],
        key=lambda m: str(m.get("matchDateTimeUTC") or m.get("matchDateTime")),
    )
    if not upcoming:
        await edit_or_reply(message, f"**{title}**\nSeason concluded, no upcoming fixtures.")
        return
    nxt = upcoming[0]
    lines = [f"**{title} - Next Match**", _fmt_match(nxt)]
    if len(upcoming) > 1:
        lines.append("\n**Upcoming**")
        lines += [_fmt_match(m) for m in upcoming[1:5]]
    lines.append(f"\nUse `{prefix}bola table {shortcut}` for standings, `{prefix}bola results {shortcut}` for results.")
    await edit_or_reply(message, "\n".join(lines))


@on_cmd(["epl"], desc="Check English Premier League fixtures and standings", usage="[next|table|results|live]")
async def epl_cmd(client: Client, message: Message):
    message.text = (message.text or "").replace("epl", "bola epl", 1)
    await bola_cmd(client, message)


@on_cmd(["ucl"], desc="Check UEFA Champions League fixtures and standings", usage="[next|table|results|live]")
async def ucl_cmd(client: Client, message: Message):
    message.text = (message.text or "").replace("ucl", "bola ucl", 1)
    await bola_cmd(client, message)
