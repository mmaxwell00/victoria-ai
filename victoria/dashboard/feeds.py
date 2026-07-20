"""Async fetchers for the dashboard boxes. Every fetch is resilient — a failing
source yields a placeholder rather than breaking the box or the others.

Data sources (all free, no API key):
- weather: wttr.in  (temp in °F + the city's LOCAL time, one call per city)
- stocks:  Yahoo Finance v8 chart endpoint (price + company name)
- news:    NBC News + Fox News RSS, parsed with the stdlib XML parser
"""
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx

from victoria.dashboard.store import SUPPORTED_NEWS

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (Victoria dashboard)"}
# wttr.in returns its plain text/format output ONLY to curl-like agents; a
# browser UA makes it serve the full HTML page instead.
_UA_CURL = {"User-Agent": "curl/8.4.0"}


# ── Weather ────────────────────────────────────────────────────────────
async def _one_city(client: httpx.AsyncClient, city: str) -> dict:
    """Temperature (°F) from the j1 JSON feed and the city's LOCAL time from the
    %T format. Kept as two calls and passed via httpx `params` so the '%' in the
    format code is encoded correctly (a combined '%t|%T' URL gets mangled)."""
    loc = quote(city)
    try:
        jr, tr = await asyncio.gather(
            client.get(f"https://wttr.in/{loc}?format=j1"),
            client.get(f"https://wttr.in/{loc}?format=%T"),
            return_exceptions=True,
        )
        temp_f = None
        if not isinstance(jr, Exception) and jr.status_code == 200:
            temp_f = int(jr.json()["current_condition"][0]["temp_F"])
        hhmm = "--:--"
        if not isinstance(tr, Exception) and tr.status_code == 200:
            m = re.match(r"\d\d:\d\d", tr.text.strip())
            if m:
                hhmm = m.group()
        if temp_f is None:
            raise ValueError("no temp")
        return {"city": city, "time": hhmm, "tempF": temp_f}
    except Exception:
        logger.debug("weather fetch failed for %s", city, exc_info=True)
        return {"city": city, "time": "--:--", "tempF": None}


async def fetch_weather(cities: list[str]) -> list[dict]:
    if not cities:
        return []
    async with httpx.AsyncClient(timeout=8.0, headers=_UA_CURL) as client:
        return list(await asyncio.gather(*[_one_city(client, c) for c in cities]))


# ── Stocks ─────────────────────────────────────────────────────────────
async def _one_stock(client: httpx.AsyncClient, symbol: str) -> dict:
    try:
        r = await client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}",
            params={"interval": "1d", "range": "1d"},
        )
        r.raise_for_status()
        meta = r.json()["chart"]["result"][0]["meta"]
        return {
            "symbol": meta.get("symbol", symbol),
            "name": meta.get("longName") or meta.get("shortName") or symbol,
            "price": meta.get("regularMarketPrice"),
        }
    except Exception:
        logger.debug("stock fetch failed for %s", symbol, exc_info=True)
        return {"symbol": symbol, "name": symbol, "price": None}


async def fetch_stocks(symbols: list[str], top: int = 5) -> list[dict]:
    """Top `top` tracked stocks by share price (unpriced ones sink to the end)."""
    if not symbols:
        return []
    async with httpx.AsyncClient(timeout=8.0, headers=_UA) as client:
        quotes = list(await asyncio.gather(*[_one_stock(client, s) for s in symbols]))
    quotes.sort(key=lambda q: (q["price"] is not None, q["price"] or 0), reverse=True)
    return quotes[:top]


# ── News ───────────────────────────────────────────────────────────────
def parse_rss(xml_bytes: bytes, source: str, limit: int) -> list[dict]:
    """Pull the first `limit` <item> title/link pairs from an RSS document."""
    out: list[dict] = []
    root = ET.fromstring(xml_bytes)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if title and link:
            out.append({"source": source, "title": title, "url": link})
        if len(out) >= limit:
            break
    return out


async def _one_source(client: httpx.AsyncClient, key: str, per_source: int) -> list[dict]:
    src = SUPPORTED_NEWS.get(key)
    if not src:
        return []
    try:
        r = await client.get(src["rss"])
        r.raise_for_status()
        return parse_rss(r.content, src["name"], per_source)
    except Exception:
        logger.debug("news fetch failed for %s", key, exc_info=True)
        return []


async def fetch_news(sources: list[str], per_source: int = 4) -> list[dict]:
    if not sources:
        return []
    async with httpx.AsyncClient(timeout=8.0, headers=_UA, follow_redirects=True) as client:
        groups = await asyncio.gather(*[_one_source(client, s, per_source) for s in sources])
    return [item for group in groups for item in group]
