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
from typing import Dict, Any, List, Optional, Tuple

from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply

logger = logging.getLogger("pygramx.weather")

# ponytail: live-only via free Open-Meteo (no key). 10-min forecast cache, 24h geo cache.
_GEO: Dict[str, tuple] = {}
_WX: Dict[str, tuple] = {}
GEO_TTL = 86400
WX_TTL = 600

WMO = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 71: "Light snow", 73: "Snow",
    75: "Heavy snow", 80: "Light showers", 81: "Showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Storm with hail", 99: "Severe storm with hail",
}


def _get(url: str) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TeleForge/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        logger.warning("weather fetch failed: %s", e)
        return None


def _geocode(city: str) -> Optional[Dict[str, Any]]:
    key = city.strip().lower()
    if key in _GEO and time.time() - _GEO[key][0] < GEO_TTL:
        return _GEO[key][1]
    q = urllib.parse.quote(city.strip())
    data = _get(f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1&language=en&format=json")
    res = (data or {}).get("results") if data else None
    if not res:
        return None
    _GEO[key] = (time.time(), res[0])
    return res[0]


def _forecast(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    key = f"{round(lat, 2)},{round(lon, 2)}"
    if key in _WX and time.time() - _WX[key][0] < WX_TTL:
        return _WX[key][1]
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&timezone=auto&forecast_days=4"
    )
    data = _get(url)
    if data and "current" in data:
        _WX[key] = (time.time(), data)
        return data
    return None


@on_cmd(
    ["weather", "forecast", "wx"],
    desc="Check current weather and 3-day forecast for any city",
    usage="<city>",
)
async def weather_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    parts = text.split(maxsplit=1)
    city = parts[1].strip() if len(parts) > 1 else "Jakarta"
    if len(city) > 100:
        await edit_or_reply(message, "City name is too long (max 100 characters).")
        return

    status = await edit_or_reply(message, f"Fetching weather for **{city}**...")
    geo = await asyncio.to_thread(_geocode, city)
    if not geo:
        await edit_or_reply(status, f"City **{city}** was not found. Try a larger nearby city.")
        return
    wx = await asyncio.to_thread(_forecast, geo["latitude"], geo["longitude"])
    if not wx:
        await edit_or_reply(status, "Weather service is unreachable. Try again in a minute.")
        return

    cur = wx["current"]
    cond = WMO.get(cur.get("weather_code"), "Unknown")
    name = geo.get("name", city)
    country = geo.get("country", "")
    place = f"{name}, {country}" if country else name
    lines = [
        f"**Weather - {place}**",
        f"• **Condition:** {cond}",
        f"• **Temperature:** `{cur.get('temperature_2m')}°C` (feels `{cur.get('apparent_temperature')}°C`)",
        f"• **Humidity:** `{cur.get('relative_humidity_2m')}%`",
        f"• **Wind:** `{cur.get('wind_speed_10m')} km/h`",
    ]
    daily = wx.get("daily", {})
    times: List[str] = daily.get("time", [])[1:4]
    if times:
        lines.append("\n**3-Day Forecast**")
        for i, day in enumerate(times, 1):
            code = (daily.get("weather_code") or [None] * 4)[i]
            hi = (daily.get("temperature_2m_max") or ["?"] * 4)[i]
            lo = (daily.get("temperature_2m_min") or ["?"] * 4)[i]
            pop = (daily.get("precipitation_probability_max") or [0] * 4)[i]
            lines.append(f"• `{day}` **{WMO.get(code, 'Unknown')}** `{lo}°/{hi}°` (rain `{pop}%`)")
    await edit_or_reply(status, "\n".join(lines))
