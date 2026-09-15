import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import json
import time
import logging
import urllib.request
from typing import Dict, Any, List, Optional, Tuple

from pyrogram import Client
from pyrogram.types import Message
from pygramx import on_cmd
from pygramx.utils import edit_or_reply, get_client_prefix

logger = logging.getLogger("pygramx.finance")

# ponytail: free public APIs only (Binance + Kraken fallback, exchangerate-api). No keys.
_RATES: Dict[str, tuple] = {}
RATES_TTL = 21600  # 6 hours


def _get(url: str) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TeleForge/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        logger.warning("finance fetch failed %s: %s", url, e)
        return None


def _fmt_price(v: float) -> str:
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:,.4f}"
    return f"{v:,.8f}".rstrip("0").rstrip(".")


COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin",
    "XRP": "ripple", "DOGE": "dogecoin", "ADA": "cardano", "AVAX": "avalanche-2",
    "LINK": "chainlink", "DOT": "polkadot", "TRX": "tron", "LTC": "litecoin",
    "MATIC": "matic-network", "ATOM": "cosmos", "NEAR": "near", "TON": "the-open-network",
}


def _crypto_price(sym: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Returns (data, source). Binance primary, Coinbase/CoinGecko/Kraken fallbacks."""
    d = _get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}USDT")
    if d and d.get("lastPrice"):
        return d, "Binance"
    # Coinbase public spot (rarely geo-blocked)
    cb = _get(f"https://api.coinbase.com/v2/prices/{sym}-USD/spot")
    try:
        amount = (cb or {}).get("data", {}).get("amount")
        if amount and float(amount) > 0:
            return {"lastPrice": amount}, "Coinbase"
    except (ValueError, TypeError):
        pass
    # CoinGecko free (known coins only)
    if sym in COINGECKO_IDS:
        cg = _get(
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={COINGECKO_IDS[sym]}&vs_currencies=usd&include_24hr_change=true"
        )
        try:
            entry = (cg or {}).get(COINGECKO_IDS[sym], {})
            if entry.get("usd"):
                out: Dict[str, Any] = {"lastPrice": str(entry["usd"])}
                if entry.get("usd_24h_change") is not None:
                    out["priceChangePercent"] = str(entry["usd_24h_change"])
                return out, "CoinGecko"
        except (AttributeError, TypeError):
            pass
    # Kraken last resort
    kraken_sym = "XBT" if sym == "BTC" else sym
    k = _get(f"https://api.kraken.com/0/public/Ticker?pair={kraken_sym}USD")
    try:
        res = (k or {}).get("result", {})
        first = next(iter(res.values()))
        c = first.get("c", ["0"])[0]
        if float(c) > 0:
            return {"lastPrice": c}, "Kraken"
    except (StopIteration, ValueError, TypeError, AttributeError, KeyError):
        pass
    return None, ""


@on_cmd(
    ["crypto", "coin", "btc"],
    desc="Check cryptocurrency price and 24h change",
    usage="[symbol]",
)
async def crypto_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    parts = text.split()
    raw = parts[1].upper() if len(parts) > 1 else "BTC"
    sym = "".join(c for c in raw if c.isalnum())[:10] or "BTC"
    if sym in ("USDT", "USDC", "DAI"):
        await edit_or_reply(message, f"**{sym}** is a stablecoin pegged near `$1.00`.")
        return

    status = await edit_or_reply(message, f"Fetching **{sym}** price...")
    data, src = await asyncio.to_thread(_crypto_price, sym)
    if not data:
        await edit_or_reply(status, f"Symbol **{sym}** was not found. Try `BTC`, `ETH`, or `SOL`.")
        return
    try:
        price = float(data["lastPrice"])
    except (KeyError, ValueError, TypeError):
        await edit_or_reply(status, f"Price data for **{sym}** is unavailable right now.")
        return

    lines = [f"**{sym}/USDT**", f"• **Price:** `${_fmt_price(price)}`"]
    try:
        chg = float(data.get("priceChangePercent", 0.0))
        arrow = "▲" if chg >= 0 else "▼"
        lines.append(f"• **24h:** `{arrow} {chg:+.2f}%`")
        lines.append(f"• **24h High:** `${_fmt_price(float(data['highPrice']))}`")
        lines.append(f"• **24h Low:** `${_fmt_price(float(data['lowPrice']))}`")
    except (KeyError, ValueError, TypeError):
        pass
    lines.append(f"• **Source:** {src}")
    await edit_or_reply(status, "\n".join(lines))


def _rates(base: str) -> Optional[Dict[str, Any]]:
    if base in _RATES and time.time() - _RATES[base][0] < RATES_TTL:
        return _RATES[base][1]
    d = _get(f"https://open.er-api.com/v6/latest/{base}")
    if d and d.get("result") == "success" and d.get("rates"):
        _RATES[base] = (time.time(), d)
        return d
    return None


@on_cmd(
    ["kurs", "fx", "convert"],
    desc="Convert currencies using live exchange rates",
    usage="<amount> <FROM> to <TO>",
)
async def kurs_cmd(client: Client, message: Message):
    text = message.text or message.caption or ""
    parts = text.split()
    tokens = [t.upper() for t in parts[1:] if t.upper() != "TO"]
    if not tokens:
        prefix = get_client_prefix(client)
        await edit_or_reply(message, f"Usage: `{prefix}kurs 100 USD to IDR`")
        return

    amount = 1.0
    codes: List[str] = []
    for t in tokens:
        try:
            amount = float(t.replace(",", ""))
            if not 0 < amount <= 1e12:
                raise ValueError
        except ValueError:
            code = "".join(c for c in t if c.isalpha())[:3]
            if len(code) == 3:
                codes.append(code)
    if len(codes) < 2:
        await edit_or_reply(message, "Specify two currencies, e.g. `USD to IDR`.")
        return
    src, dst = codes[0], codes[-1]

    status = await edit_or_reply(message, f"Converting **{amount:g} {src}** to **{dst}**...")
    d = await asyncio.to_thread(_rates, src)
    if not d:
        await edit_or_reply(status, f"Currency **{src}** was not found. Use 3-letter codes like USD, EUR, IDR.")
        return
    rate = (d.get("rates") or {}).get(dst)
    if rate is None:
        await edit_or_reply(status, f"Currency **{dst}** was not found. Use 3-letter codes like USD, EUR, IDR.")
        return
    total = amount * float(rate)
    await edit_or_reply(
        status,
        f"**{amount:g} {src} = {total:,.2f} {dst}**\n"
        f"• **Rate:** `1 {src} = {float(rate):,.4f} {dst}`",
    )
