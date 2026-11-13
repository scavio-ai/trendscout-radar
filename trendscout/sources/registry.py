"""Which sources run, what each costs, and how each is fetched."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import httpx

from ..client import ScavioClient
from ..config import Config
from ..model import Item
from . import free, scavio


@dataclass
class Context:
    config: Config
    client: ScavioClient | None
    http: httpx.Client


@dataclass(frozen=True)
class SourceSpec:
    name: str
    label: str
    platform: str
    cost: int  # Scavio credits per run (per keyword for keyword sources); 0 = free
    needs_key: bool
    fetch: Callable[..., list[Item]]


def _scavio(path: str, body: dict, parse: Callable[[dict], list[Item]]) -> Callable[[Context], list[Item]]:
    def run(ctx: Context) -> list[Item]:
        assert ctx.client is not None
        return parse(ctx.client.post(path, body))

    return run


def _tiktok_videos(ctx: Context) -> list[Item]:
    """Each call samples about 6 trending videos; merge several calls, drop repeats."""
    assert ctx.client is not None
    merged: list[Item] = []
    seen: set[str] = set()
    for _ in range(ctx.config.tiktok_video_calls):
        for item in scavio.parse_tiktok_videos(ctx.client.post("/api/v1/tiktok/trending/videos", {"count": 15})):
            vid = str(item.extra.get("id"))
            if vid not in seen:
                seen.add(vid)
                item.rank = len(merged) + 1
                merged.append(item)
    merged.sort(key=lambda i: -i.metrics.get("views", 0))
    for n, item in enumerate(merged, 1):
        item.rank = n
    return merged


def _google_trending(ctx: Context) -> list[Item]:
    assert ctx.client is not None
    body = {"geo": ctx.config.country, "hl": ctx.config.language, "hours": 24, "sort": "search_volume", "status": "active"}
    return scavio.parse_google_trending(ctx.client.post("/api/v2/google/trending", body))


def _x_trends(ctx: Context) -> list[Item]:
    assert ctx.client is not None
    return scavio.parse_x_trends(ctx.client.post("/api/v1/x/trending", {"country": ctx.config.x_country}))


GLOBAL_SOURCES: dict[str, SourceSpec] = {
    s.name: s
    for s in [
        SourceSpec("tiktok_searches", "TikTok trending searches", "tiktok", 1, True,
                   _scavio("/api/v1/tiktok/trending/searches", {}, scavio.parse_tiktok_searches)),
        SourceSpec("tiktok_videos", "TikTok trending videos", "tiktok", 1, True, _tiktok_videos),
        SourceSpec("tiktok_sounds", "TikTok viral sounds", "tiktok", 1, True,
                   _scavio("/api/v1/tiktok/music/chart", {"chart": "viral", "count": 20}, scavio.parse_tiktok_sounds)),
        SourceSpec("google_trending", "Google Trending Now", "google", 1, True, _google_trending),
        SourceSpec("x_trends", "X trending", "x", 1, True, _x_trends),
        SourceSpec("reddit_trending", "Reddit trending searches", "reddit", 1, True,
                   _scavio("/api/v1/reddit/trending", {}, scavio.parse_reddit_trending)),
        SourceSpec("reddit_popular", "Reddit popular", "reddit", 1, True,
                   _scavio("/api/v1/reddit/popular", {}, scavio.parse_reddit_popular)),
        SourceSpec("hackernews", "Hacker News", "hackernews", 0, False,
                   lambda ctx: free.fetch_hackernews(ctx.http, ctx.config.hackernews_limit)),
        # Keyless fallback for google_trending; planned only when there is no key.
        SourceSpec("google_trends_rss", "Google Trends (RSS)", "google", 0, False,
                   lambda ctx: free.fetch_google_trends_rss(ctx.http, ctx.config.country)),
    ]
}


def _kw(path: str, body: Callable[[str, Config], dict], parse: Callable[[dict, str], list[Item]]):
    def run(ctx: Context, keyword: str) -> list[Item]:
        assert ctx.client is not None
        return parse(ctx.client.post(path, body(keyword, ctx.config)), keyword)

    return run


KEYWORD_SOURCES: dict[str, SourceSpec] = {
    s.name: s
    for s in [
        SourceSpec("youtube_keyword", "YouTube (this week, by views)", "youtube", 2, True,
                   _kw("/api/v1/youtube/search",
                       lambda k, c: {"search": k, "upload_date": "this_week", "sort_by": "view_count"},
                       scavio.parse_youtube_search)),
        SourceSpec("tiktok_keyword", "TikTok (last 7 days, most liked)", "tiktok", 1, True,
                   _kw("/api/v1/tiktok/search/videos",
                       lambda k, c: {"keyword": k, "count": 20, "sort_type": "1", "publish_time": "7"},
                       scavio.parse_tiktok_search)),
        SourceSpec("reddit_keyword", "Reddit posts", "reddit", 1, True,
                   _kw("/api/v1/reddit/search", lambda k, c: {"query": k}, scavio.parse_reddit_search)),
        # Free for every topic, key or not.
        SourceSpec("hackernews_keyword", "Hacker News", "hackernews", 0, False,
                   lambda ctx, k: free.fetch_hn_search(ctx.http, k)),
        SourceSpec("news_keyword", "Google News", "news", 0, False,
                   lambda ctx, k: free.fetch_google_news_rss(ctx.http, k, ctx.config.country, ctx.config.language)),
    ]
}


@dataclass(frozen=True)
class PlannedCall:
    spec: SourceSpec
    keyword: str | None = None
    repeats: int = 1  # tiktok_videos makes several calls per run

    @property
    def cost(self) -> int:
        return self.spec.cost * self.repeats

    @property
    def label(self) -> str:
        return f"{self.spec.label}: {self.keyword}" if self.keyword else self.spec.label


def plan_sources(config: Config) -> list[PlannedCall]:
    """The calls one scan makes, in order: free sources first, then by cost.

    Every topic gets the free Google News search unless `news: false`, plus the
    paid searches on the platforms in its `search` list when there is a key.
    """
    has_key = bool(config.scavio_api_key)
    calls: list[PlannedCall] = []

    for name, spec in GLOBAL_SOURCES.items():
        if name == "google_trends_rss":
            if not has_key and config.enabled("google_trending"):
                calls.append(PlannedCall(spec))
            continue
        if not config.enabled(name) or (spec.needs_key and not has_key):
            continue
        calls.append(PlannedCall(spec, repeats=config.tiktok_video_calls if name == "tiktok_videos" else 1))

    for topic in config.topics:
        for name, spec in KEYWORD_SOURCES.items():
            if not config.enabled(name):
                continue
            if name == "news_keyword" and not topic.news:
                continue
            if name == "hackernews_keyword" and not topic.hackernews:
                continue
            if spec.needs_key and not (has_key and spec.platform in topic.search):
                continue
            calls.append(PlannedCall(spec, topic.name))

    calls.sort(key=lambda c: c.cost == 0, reverse=True)  # free first; stable otherwise
    return calls


def search_cost() -> int:
    """Credits one topic with `search: true` adds to every scan."""
    return sum(s.cost for s in KEYWORD_SOURCES.values())


def estimate_credits(config: Config) -> int:
    return sum(c.cost for c in plan_sources(config))
