"""Dashboard: tracked-item store, feed parsing/aggregation, API, and the
conversational track/untrack tools."""
import os

import pytest
from httpx import AsyncClient, ASGITransport

from victoria.dashboard.store import DashboardStore
from victoria.dashboard import feeds
from victoria.main import app


# ── Store ──────────────────────────────────────────────────────────────
def _store(tmp_path):
    return DashboardStore(path=os.path.join(tmp_path, "dashboard.json"))


def test_defaults(tmp_path):
    s = _store(tmp_path)
    cfg = s.get()
    assert cfg["cities"] and cfg["stocks"] and cfg["news"]


def test_add_stock_normalises_symbol(tmp_path):
    s = _store(tmp_path)
    ok, _ = s.add("stock", "tsla")
    assert ok and "TSLA" in s.get()["stocks"]


def test_add_city_capitalises(tmp_path):
    s = _store(tmp_path)
    s.add("city", "san  francisco")
    assert "San Francisco" in s.get()["cities"]


def test_news_kind_not_broken_by_trailing_s(tmp_path):
    """Regression: normalising 'news' must not become 'new' (rstrip('s') bug)."""
    s = _store(tmp_path)
    s.remove("news", "cnn")               # start clean
    ok, msg = s.add("news", "CNN")
    assert ok and "cnn" in s.get()["news"], msg


def test_news_alias_and_unsupported(tmp_path):
    s = _store(tmp_path)
    s.remove("news", "foxnews")
    ok, _ = s.add("news", "Fox News")     # alias -> foxnews
    assert ok and "foxnews" in s.get()["news"]
    ok, msg = s.add("news", "Drudge")     # no feed
    assert not ok and "CNN" in msg and "Fox News" in msg


def test_remove(tmp_path):
    s = _store(tmp_path)
    s.add("stock", "TSLA")
    ok, _ = s.remove("stock", "tsla")
    assert ok and "TSLA" not in s.get()["stocks"]


def test_persists_across_instances(tmp_path):
    p = os.path.join(tmp_path, "dashboard.json")
    DashboardStore(path=p).add("city", "Reykjavik")
    assert "Reykjavik" in DashboardStore(path=p).get()["cities"]


# ── Feed parsing / aggregation ──────────────────────────────────────────
def test_parse_rss():
    xml = (b"<rss><channel>"
           b"<item><title>First</title><link>http://x/1</link></item>"
           b"<item><title>Second</title><link>http://x/2</link></item>"
           b"<item><title>Third</title><link>http://x/3</link></item>"
           b"</channel></rss>")
    items = feeds.parse_rss(xml, "CNN", limit=2)
    assert items == [
        {"source": "CNN", "title": "First", "url": "http://x/1"},
        {"source": "CNN", "title": "Second", "url": "http://x/2"},
    ]


async def test_fetch_stocks_sorts_by_price_and_caps(monkeypatch):
    prices = {"A": 10, "B": 500, "C": 250, "D": None, "E": 90, "F": 300}

    async def fake_one(client, sym):
        return {"symbol": sym, "name": sym, "price": prices[sym]}

    monkeypatch.setattr(feeds, "_one_stock", fake_one)
    out = await feeds.fetch_stocks(list(prices), top=5)
    assert [q["symbol"] for q in out] == ["B", "F", "C", "E", "A"]  # desc, None dropped, top 5


# ── API endpoints ───────────────────────────────────────────────────────
async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_config_endpoint():
    async with await _client() as c:
        r = await c.get("/v1/dashboard/config")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"cities", "stocks", "news"}


async def test_data_endpoints(monkeypatch):
    async def fw(cities): return [{"city": "Dallas", "time": "16:22", "tempF": 93}]
    async def fs(symbols): return [{"symbol": "AAPL", "name": "Apple Inc.", "price": 155.25}]
    async def fn(sources): return [{"source": "CNN", "title": "H", "url": "http://x/1"}]
    monkeypatch.setattr(feeds, "fetch_weather", fw)
    monkeypatch.setattr(feeds, "fetch_stocks", fs)
    monkeypatch.setattr(feeds, "fetch_news", fn)

    async with await _client() as c:
        w = (await c.get("/v1/dashboard/weather")).json()["items"]
        s = (await c.get("/v1/dashboard/stocks")).json()["items"]
        n = (await c.get("/v1/dashboard/news")).json()["items"]
    assert w[0]["tempF"] == 93 and w[0]["time"] == "16:22"
    assert s[0]["symbol"] == "AAPL"
    assert n[0]["url"] == "http://x/1"


# ── Conversational tools ────────────────────────────────────────────────
async def test_track_untrack_tools(tmp_path, monkeypatch):
    from victoria.tools import dashboard_tools
    store = _store(tmp_path)
    monkeypatch.setattr(dashboard_tools, "dashboard_store", store)

    msg = await dashboard_tools.track_dashboard("stock", "NFLX")
    assert "NFLX" in store.get()["stocks"] and "NFLX" in msg
    await dashboard_tools.untrack_dashboard("stock", "NFLX")
    assert "NFLX" not in store.get()["stocks"]
