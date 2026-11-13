"""Shared helpers. Fixtures under tests/fixtures are real API responses captured
2026-11-11 (trimmed for size), so parsers are tested against true field names."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# Scavio path -> fixture file
SCAVIO_ROUTES = {
    "/api/v1/tiktok/trending/searches": "tiktok_trending_searches.json",
    "/api/v1/tiktok/trending/videos": "tiktok_trending_videos.json",
    "/api/v1/tiktok/music/chart": "tiktok_music_chart.json",
    "/api/v2/google/trending": "google_trending.json",
    "/api/v1/x/trending": "x_trending.json",
    "/api/v1/reddit/trending": "reddit_trending_nonempty.json",
    "/api/v1/reddit/popular": "reddit_popular.json",
    "/api/v1/youtube/search": "youtube_search.json",
    "/api/v1/tiktok/search/videos": "tiktok_search_videos.json",
    "/api/v1/reddit/search": "reddit_search.json",
}

COSTS = {"/api/v1/youtube/search": 2}


def load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def scavio_handler(fail: dict[str, int] | None = None, calls: list | None = None) -> Callable:
    """A MockTransport handler serving fixtures; `fail` maps a path to a status code."""
    fail = fail or {}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if calls is not None:
            calls.append((path, request.headers.get("x-client-source")))
        if path == "/api/v1/usage":
            return httpx.Response(200, json={"plan": "free", "credit_balance": 50})
        if path in fail:
            return httpx.Response(fail[path], json={"error": "simulated failure"})
        if path not in SCAVIO_ROUTES:
            return httpx.Response(404, json={"error": "unknown path"})
        body = load_json(SCAVIO_ROUTES[path])
        body["credits_used"] = COSTS.get(path, 1)
        return httpx.Response(200, json=body)

    return handle


def free_handler(fail_hosts: set[str] | None = None) -> Callable:
    fail_hosts = fail_hosts or set()
    top = load_json("free_hn_top.json")
    item = load_json("free_hn_item.json")

    def handle(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in fail_hosts:
            return httpx.Response(503, text="down")
        if host == "hacker-news.firebaseio.com":
            if request.url.path.endswith("topstories.json"):
                return httpx.Response(200, json=top)
            return httpx.Response(200, json={**item, "id": int(request.url.path.rsplit("/", 1)[1].split(".")[0])})
        if host == "trends.google.com":
            return httpx.Response(200, text=load_text("free_google_trends.rss"))
        if host == "hn.algolia.com":
            return httpx.Response(200, json=load_json("free_hn_search.json"))
        if host == "news.google.com":
            return httpx.Response(200, text=load_text("free_google_news.rss"))
        return httpx.Response(404)

    return handle


@pytest.fixture
def no_sleep() -> list[float]:
    return []
