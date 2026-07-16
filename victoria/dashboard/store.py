"""Persisted dashboard preferences: which cities, stocks, and news sources the
operator has asked Victoria to track. Stored as JSON next to the database so it
survives restarts. Single-process app, so plain file read/write is fine."""
import json
import logging
import os
import re
from typing import Optional

from victoria.config import settings

logger = logging.getLogger(__name__)

# News sources we can actually pull headlines from (must have a usable RSS feed).
# Drudge Report is intentionally absent — it publishes no feed.
SUPPORTED_NEWS: dict[str, dict] = {
    "cnn": {"name": "CNN", "rss": "http://rss.cnn.com/rss/cnn_topstories.rss"},
    "foxnews": {"name": "Fox News", "rss": "https://moxie.foxnews.com/google-publisher/latest.xml"},
}

# Aliases so "Fox News", "fox", "foxnews" all resolve to the same key.
_NEWS_ALIASES = {
    "cnn": "cnn",
    "fox": "foxnews", "foxnews": "foxnews", "fox news": "foxnews",
}

DEFAULTS = {
    "cities": ["Dallas", "New York", "Seattle", "London"],
    "stocks": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"],
    "news": ["cnn", "foxnews"],
}

_KINDS = ("city", "stock", "news")
_KIND_ALIAS = {
    "city": "city", "cities": "city",
    "stock": "stock", "stocks": "stock", "ticker": "stock", "tickers": "stock",
    "news": "news", "source": "news", "sources": "news",
    "headline": "news", "headlines": "news",
}


def _default_path() -> str:
    # Sit beside the SQLite DB (e.g. data/victoria.db -> data/dashboard.json).
    db = getattr(settings, "db_path", "data/victoria.db") or "data/victoria.db"
    return os.path.join(os.path.dirname(db) or ".", "dashboard.json")


class DashboardStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path or _default_path()
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path) as f:
                data = json.load(f)
            # Merge over defaults so a partial/old file still has every key.
            return {k: data.get(k, list(v)) for k, v in DEFAULTS.items()}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {k: list(v) for k, v in DEFAULTS.items()}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            logger.exception("Could not persist dashboard config to %s", self.path)

    def get(self) -> dict:
        """A copy of the tracked lists (safe for callers to read/serialise)."""
        return {k: list(v) for k, v in self._data.items()}

    # -- normalisation --------------------------------------------------
    @staticmethod
    def _norm_city(v: str) -> str:
        return " ".join(w.capitalize() for w in v.strip().split())

    @staticmethod
    def _norm_stock(v: str) -> str:
        # Keep letters, digits, dot, hyphen (BRK.B, RDS-A); uppercase.
        return re.sub(r"[^A-Za-z0-9.\-]", "", v).upper()

    @staticmethod
    def _norm_news(v: str) -> Optional[str]:
        return _NEWS_ALIASES.get(re.sub(r"\s+", " ", v.strip().lower()))

    # -- mutations ------------------------------------------------------
    def add(self, kind: str, value: str) -> tuple[bool, str]:
        kind = _KIND_ALIAS.get((kind or "").strip().lower(), "")
        if kind not in _KINDS:
            return False, "I can track a city, a stock, or a news source."
        if not value or not value.strip():
            return False, "I need a name to track."

        if kind == "city":
            city = self._norm_city(value)
            if any(c.lower() == city.lower() for c in self._data["cities"]):
                return True, f"I'm already tracking the weather for {city}."
            self._data["cities"].append(city)
            self._save()
            return True, f"Tracking the weather for {city} now."

        if kind == "stock":
            sym = self._norm_stock(value)
            if not sym:
                return False, "That doesn't look like a ticker symbol."
            if sym in self._data["stocks"]:
                return True, f"{sym} is already on your watchlist."
            self._data["stocks"].append(sym)
            self._save()
            return True, f"Added {sym} to your stock watchlist."

        # news
        key = self._norm_news(value)
        if not key or key not in SUPPORTED_NEWS:
            supported = ", ".join(s["name"] for s in SUPPORTED_NEWS.values())
            return False, f"I can only pull headlines from: {supported}."
        if key in self._data["news"]:
            return True, f"{SUPPORTED_NEWS[key]['name']} is already in your headlines."
        self._data["news"].append(key)
        self._save()
        return True, f"Added {SUPPORTED_NEWS[key]['name']} to your headlines."

    def remove(self, kind: str, value: str) -> tuple[bool, str]:
        kind = _KIND_ALIAS.get((kind or "").strip().lower(), "")
        if kind not in _KINDS:
            return False, "I can only drop a city, a stock, or a news source."

        if kind == "city":
            city = self._norm_city(value)
            before = len(self._data["cities"])
            self._data["cities"] = [c for c in self._data["cities"] if c.lower() != city.lower()]
            if len(self._data["cities"]) == before:
                return False, f"I wasn't tracking {city}."
            self._save()
            return True, f"Stopped tracking {city}."

        if kind == "stock":
            sym = self._norm_stock(value)
            if sym not in self._data["stocks"]:
                return False, f"{sym} wasn't on your watchlist."
            self._data["stocks"].remove(sym)
            self._save()
            return True, f"Removed {sym} from your watchlist."

        key = self._norm_news(value) or value.strip().lower()
        if key not in self._data["news"]:
            return False, "That source wasn't in your headlines."
        self._data["news"].remove(key)
        self._save()
        name = SUPPORTED_NEWS.get(key, {}).get("name", key)
        return True, f"Removed {name} from your headlines."


dashboard_store = DashboardStore()
