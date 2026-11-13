from datetime import datetime, timezone

import httpx

from conftest import free_handler, scavio_handler

from trendscout.client import ScavioClient
from trendscout.collect import collect
from trendscout.config import from_dict
from trendscout.sources import estimate_credits, plan_sources

NOW = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)


def cfg(key="k", **raw):
    raw.setdefault("topics", [{"name": "ai agents", "search": True}])
    raw.setdefault("hackernews_limit", 3)
    env = {"SCAVIO_API_KEY": key} if key else {}
    return from_dict(raw, env)


def client(fail=None, calls=None):
    return ScavioClient("k", transport=httpx.MockTransport(scavio_handler(fail, calls)), sleep=lambda s: None)


def http(fail_hosts=None):
    return httpx.Client(transport=httpx.MockTransport(free_handler(fail_hosts)))


def test_plan_with_key_matches_estimate():
    c = cfg()
    names = [p.spec.name for p in plan_sources(c)]
    assert names[0] == "hackernews"  # free sources first
    assert "google_trends_rss" not in names
    assert "reddit_trending" not in names  # off by default
    # 7 global credits (tiktok_videos x2) + 4 for a searched topic; its news is free
    assert estimate_credits(c) == 1 + 2 + 1 + 1 + 1 + 1 + 4


def test_topics_without_search_add_no_cost():
    c = cfg(topics=["ai agents", "skincare"])
    names = [(p.spec.name, p.keyword) for p in plan_sources(c)]
    assert ("news_keyword", "ai agents") in names and ("news_keyword", "skincare") in names
    assert not any(n in ("youtube_keyword", "tiktok_keyword", "reddit_keyword") for n, _ in names)
    assert estimate_credits(c) == 7


def test_plan_without_key_uses_free_fallbacks():
    c = cfg(key=None, topics=[{"name": "ai agents", "search": True}, "skincare"])
    names = [p.spec.name for p in plan_sources(c)]
    assert names == ["hackernews", "google_trends_rss", "hackernews_keyword", "news_keyword", "hackernews_keyword", "news_keyword"]
    assert estimate_credits(c) == 0


def test_full_collect_with_key():
    calls = []
    snap = collect(cfg(), client(calls=calls), http(), now=NOW)
    assert not snap.errors and not snap.skipped
    assert snap.credits_used == estimate_credits(cfg())
    sources = {i.source for i in snap.items}
    assert {"tiktok_searches", "tiktok_videos", "google_trending", "x_trends", "youtube_keyword", "news_keyword", "hackernews"} <= sources
    assert all(src == "trendscout" for _, src in calls)


def test_one_failing_source_does_not_stop_the_run():
    snap = collect(cfg(), client(fail={"/api/v1/x/trending": 500}), http(fail_hosts={"hacker-news.firebaseio.com"}), now=NOW)
    assert "x_trends" in snap.errors and "hackernews" in snap.errors
    assert any(i.source == "google_trending" for i in snap.items)


def test_out_of_credits_stops_remaining_paid_calls():
    calls = []
    snap = collect(cfg(), client(fail={"/api/v1/tiktok/trending/searches": 402}, calls=calls), http(), now=NOW)
    assert "tiktok_searches" in snap.errors
    paid_after = [p for p, _ in calls if p != "/api/v1/tiktok/trending/searches"]
    assert paid_after == []  # nothing else billed after the 402
    assert snap.skipped and all("402" in why for why in snap.skipped.values())
    assert any(i.source == "hackernews" for i in snap.items)  # free sources still ran


def test_budget_cap_skips_calls_that_would_overspend():
    snap = collect(cfg(max_credits_per_run=4), client(), http(), now=NOW)
    assert snap.credits_used <= 4
    assert any("max_credits_per_run" in why for why in snap.skipped.values())


def test_topic_search_per_platform_and_news_off():
    c = cfg(topics=[{"name": "a", "search": ["reddit"]}, {"name": "b", "search": ["youtube", "tiktok"], "news": False}])
    calls = {(p.spec.name, p.keyword) for p in plan_sources(c)}
    assert ("reddit_keyword", "a") in calls and ("youtube_keyword", "a") not in calls and ("news_keyword", "a") in calls
    assert ("youtube_keyword", "b") in calls and ("tiktok_keyword", "b") in calls and ("news_keyword", "b") not in calls
    assert estimate_credits(c) == 7 + 1 + 2 + 1
