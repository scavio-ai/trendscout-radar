from datetime import datetime, timezone

from conftest import load_json, load_text

from trendscout.model import clean_title, normalize, plain
from trendscout.sources import free, scavio

NOW = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)


def test_tiktok_searches_drop_creator_handles():
    items = scavio.parse_tiktok_searches(load_json("tiktok_trending_searches.json"))
    titles = [i.title for i in items]
    assert len(items) > 50
    assert "GTA 5 Goodbye Trend" in titles or any("Trend" in t for t in titles)
    # handles such as "_tezza_1" or "tylee.1" are not trends
    assert not any(t.startswith("_") or t.endswith(".1") for t in titles)
    assert all(i.platform == "tiktok" and i.url.startswith("https://www.tiktok.com/search?q=") for i in items)
    assert [i.rank for i in items] == list(range(1, len(items) + 1))


def test_tiktok_videos_have_views_and_urls():
    items = scavio.parse_tiktok_videos(load_json("tiktok_trending_videos.json"))
    assert items
    for i in items:
        assert i.url and "/video/" in i.url
        assert i.metrics["views"] > 0
        assert "#" not in i.title or i.title.startswith("#")


def test_tiktok_sounds():
    items = scavio.parse_tiktok_sounds(load_json("tiktok_music_chart.json"))
    assert items[0].title == "Ramalama (Bang Bang) - Róisín Murphy"
    assert items[0].metrics["videos"] > 0
    assert items[0].url == "https://www.tiktok.com/music/x-6919100601457215489"


def test_google_trending():
    items = scavio.parse_google_trending(load_json("google_trending.json"))
    assert len(items) > 20
    assert items[0].metrics["searches"] >= items[-1].metrics.get("searches", 0)
    assert items[0].url.startswith("https://trends.google.com/")


def test_x_trends():
    items = scavio.parse_x_trends(load_json("x_trending.json"))
    assert len(items) == 50
    assert items[0].title == "Veterans Day"
    assert items[0].url == "https://x.com/search?q=Veterans%20Day"


def test_reddit_trending_empty_and_nonempty():
    assert scavio.parse_reddit_trending(load_json("reddit_trending.json")) == []
    items = scavio.parse_reddit_trending(load_json("reddit_trending_nonempty.json"))
    assert [i.title for i in items][:2] == ["General Motors $220 Million OnStar Settlement", "UC San Diego AI Town Hall"]


def test_reddit_popular_skips_promoted_posts():
    body = load_json("reddit_popular.json")
    raw = body["data"]["posts"]
    items = scavio.parse_reddit_popular(body)
    assert len(items) == sum(1 for p in raw if p.get("subreddit"))
    assert items[0].url == "https://www.reddit.com/r/HistoryMemes/comments/1xtj80y/"
    assert items[0].metrics["upvotes"] == 3526


def test_youtube_search_videos_only():
    items = scavio.parse_youtube_search(load_json("youtube_search.json"), "ai agents")
    assert items and all(i.platform == "youtube" and i.keyword == "ai agents" for i in items)
    assert items[0].metrics["views"] > 0 and items[0].extra["channel"]


def test_tiktok_keyword_reads_search_item_list():
    items = scavio.parse_tiktok_search(load_json("tiktok_search_videos.json"), "ai agents")
    assert len(items) == 6
    assert items[0].url == "https://www.tiktok.com/@marcinteodoru/video/7706737780012977439"
    assert items[0].metrics["views"] == 525200


def test_reddit_keyword_drops_old_threads():
    body = load_json("reddit_search.json")
    items = scavio.parse_reddit_search(body, "ai agents", now=NOW)
    assert items, "recent posts must survive"
    assert len(items) <= len(body["data"]["results"])
    for i in items:
        created = datetime.fromisoformat(i.published_at.replace("+0000", "+00:00"))
        assert (NOW - created).days <= scavio.REDDIT_KEYWORD_MAX_AGE_DAYS


def test_free_google_trends_rss():
    items = free.parse_google_trends_rss(load_text("free_google_trends.rss"))
    assert items[0].title == "uc davis basketball"
    assert items[0].metrics["searches"] == 500


def test_free_google_news_rss_strips_publisher_suffix():
    items = free.parse_google_news_rss(load_text("free_google_news.rss"), "ai agents")
    assert items
    first = items[0]
    assert first.extra["publisher"] == "The New York Times"
    assert not first.title.endswith("- The New York Times")


def test_free_hn_item():
    item = free.parse_hn_item(load_json("free_hn_item.json"), 1)
    assert item.title == "AWS Finally Built Its Own AWS Emulator"
    assert item.metrics == {"points": 207.0, "comments": 56.0}
    assert free.parse_hn_item({"type": "job", "title": "x"}, 1) is None


def test_traffic_parsing():
    assert free._traffic("500+") == 500
    assert free._traffic("20K+") == 20000
    assert free._traffic("1M+") == 1_000_000
    assert free._traffic(None) is None


def test_text_helpers():
    assert plain("𝙍𝙚𝙡𝙖𝙭𝙞𝙣𝙜 𝙎𝙠𝙞𝙣𝙘𝙖𝙧𝙚") == "Relaxing Skincare"
    assert clean_title("A honey mask cures all #skincare #natural") == "A honey mask cures all"
    assert clean_title("#fyp #trending") == "#fyp #trending"
    assert normalize("GTA 5 Goodbye!") == "gta 5 goodbye"


def test_free_hn_search():
    items = free.parse_hn_search(load_json("free_hn_search.json"), "ai agents")
    assert len(items) == 8 and items[0].metrics["points"] >= items[-1].metrics["points"]
    assert all(i.source == "hackernews_keyword" and i.keyword == "ai agents" and i.url for i in items)
    assert items[0].extra["discussion"].startswith("https://news.ycombinator.com/item?id=")
