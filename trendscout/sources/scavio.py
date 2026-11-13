"""Parsers for Scavio API responses.

Every parser takes the raw JSON body and returns Items. Field names follow the
real responses captured in tests/fixtures; a missing field is skipped, never
guessed.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, quote_plus

from datetime import datetime, timedelta, timezone

from ..model import Item, clean_title, plain

# TikTok's trending-search list mixes real trends with creator handles such as
# "_tezza_1" or "tylee.1". A handle has no spaces and carries a digit, "_" or ".".
_HANDLE = re.compile(r"^[\w.]+$")


def _looks_like_handle(term: str) -> bool:
    t = term.strip()
    return bool(_HANDLE.match(t)) and " " not in t and any(c in t for c in "._0123456789")


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _metrics(**values: Any) -> dict[str, float]:
    out = {}
    for k, v in values.items():
        n = _num(v)
        if n is not None:
            out[k] = n
    return out


def _reddit_permalink(post: dict[str, Any]) -> str | None:
    pid, sub = post.get("post_id"), post.get("subreddit")
    if isinstance(pid, str) and sub:
        return f"https://www.reddit.com/r/{sub}/comments/{pid.removeprefix('t3_')}/"
    return post.get("url")


# ---------------------------------------------------------------- global feeds


def parse_tiktok_searches(body: dict[str, Any]) -> list[Item]:
    words = (body.get("data") or {}).get("trending_search_words") or []
    items: list[Item] = []
    for w in words:
        term = (w.get("trendingSearchWord") or "").strip()
        if not term or _looks_like_handle(term):
            continue
        items.append(
            Item(
                source="tiktok_searches",
                platform="tiktok",
                title=term,
                rank=len(items) + 1,
                url=f"https://www.tiktok.com/search?q={quote_plus(term)}",
            )
        )
    return items


def parse_tiktok_videos(body: dict[str, Any]) -> list[Item]:
    videos = (body.get("data") or {}).get("itemList") or []
    items: list[Item] = []
    for v in videos:
        if v.get("isAd"):
            continue
        author = (v.get("author") or {}).get("uniqueId")
        vid = v.get("id")
        stats = v.get("stats") or {}
        tags = [c.get("title") for c in v.get("challenges") or [] if c.get("title")]
        desc = clean_title(v.get("desc") or "")
        title = desc or (f"#{tags[0]}" if tags else f"Video by @{author}")
        items.append(
            Item(
                source="tiktok_videos",
                platform="tiktok",
                title=title[:200],
                rank=len(items) + 1,
                url=f"https://www.tiktok.com/@{author}/video/{vid}" if author and vid else None,
                metrics=_metrics(
                    views=stats.get("playCount"),
                    likes=stats.get("diggCount"),
                    shares=stats.get("shareCount"),
                    comments=stats.get("commentCount"),
                ),
                extra={"author": author, "hashtags": tags, "id": vid},
            )
        )
    return items


def parse_tiktok_sounds(body: dict[str, Any]) -> list[Item]:
    sounds = (body.get("data") or {}).get("music_list") or []
    items: list[Item] = []
    for s in sounds:
        info = s.get("music_info") or {}
        title = (info.get("title") or "").strip()
        if not title:
            continue
        author = (info.get("author") or "").strip()
        sid = info.get("id_str")
        items.append(
            Item(
                source="tiktok_sounds",
                platform="tiktok",
                title=f"{title} - {author}" if author else title,
                rank=len(items) + 1,
                url=f"https://www.tiktok.com/music/x-{sid}" if sid else None,
                metrics=_metrics(videos=info.get("user_count")),
                extra={"tags": info.get("theme_tags") or []},
            )
        )
    return items


def parse_google_trending(body: dict[str, Any]) -> list[Item]:
    items: list[Item] = []
    for t in body.get("trends") or []:
        title = plain(t.get("title") or "")
        if not title:
            continue
        items.append(
            Item(
                source="google_trending",
                platform="google",
                title=title,
                rank=len(items) + 1,
                url=t.get("trend_url"),
                metrics=_metrics(searches=t.get("search_volume"), growth_pct=t.get("growth_percentage")),
                extra={"related": (t.get("related_queries") or [])[:5]},
            )
        )
    return items


def parse_x_trends(body: dict[str, Any]) -> list[Item]:
    items: list[Item] = []
    for t in (body.get("data") or {}).get("trends") or []:
        name = (t.get("name") or "").strip()
        if not name:
            continue
        items.append(
            Item(
                source="x_trends",
                platform="x",
                title=name,
                rank=len(items) + 1,
                url=f"https://x.com/search?q={quote(name)}",
                extra={"context": t.get("context")},
            )
        )
    return items


def parse_reddit_trending(body: dict[str, Any]) -> list[Item]:
    items: list[Item] = []
    for t in (body.get("data") or {}).get("trending") or []:
        q = (t.get("query") or "").strip()
        if not q:
            continue
        items.append(
            Item(
                source="reddit_trending",
                platform="reddit",
                title=q,
                rank=len(items) + 1,
                url=f"https://www.reddit.com/search/?q={quote_plus(q)}",
            )
        )
    return items


def _age_days(created_at: Any, now: datetime) -> float | None:
    if not isinstance(created_at, str):
        return None
    try:
        dt = datetime.fromisoformat(created_at.replace("+0000", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt) / timedelta(days=1)


def _reddit_posts(
    posts: list[dict[str, Any]],
    source: str,
    keyword: str | None = None,
    max_age_days: float | None = None,
    now: datetime | None = None,
) -> list[Item]:
    now = now or datetime.now(timezone.utc)
    items: list[Item] = []
    for p in posts:
        title = plain(p.get("title") or "")
        # Promoted posts come back with no subreddit and no score.
        if not title or not p.get("subreddit"):
            continue
        if max_age_days is not None:
            age = _age_days(p.get("created_at"), now)
            if age is not None and age > max_age_days:
                continue
        items.append(
            Item(
                source=source,
                platform="reddit",
                title=title[:300],
                rank=len(items) + 1,
                url=_reddit_permalink(p),
                metrics=_metrics(upvotes=p.get("score"), comments=p.get("num_comments")),
                keyword=keyword,
                published_at=p.get("created_at"),
                extra={"subreddit": p.get("subreddit"), **({"nsfw": True} if p.get("is_nsfw") else {})},
            )
        )
    return items


def parse_reddit_popular(body: dict[str, Any]) -> list[Item]:
    return _reddit_posts((body.get("data") or {}).get("posts") or [], "reddit_popular")


# ---------------------------------------------------------------- keyword feeds


def parse_youtube_search(body: dict[str, Any], keyword: str) -> list[Item]:
    items: list[Item] = []
    for v in (body.get("data") or {}).get("results") or []:
        if v.get("type") != "video" or not v.get("title"):
            continue
        items.append(
            Item(
                source="youtube_keyword",
                platform="youtube",
                title=plain(v["title"]),
                rank=len(items) + 1,
                url=v.get("url"),
                metrics=_metrics(views=v.get("view_count")),
                keyword=keyword,
                published_at=v.get("published_time"),
                extra={"channel": (v.get("channel") or {}).get("name")},
            )
        )
    return items


def parse_tiktok_search(body: dict[str, Any], keyword: str) -> list[Item]:
    data = body.get("data") or {}
    # Keyword search returns videos under search_item_list[].aweme_info; older
    # responses used aweme_list. Read both.
    videos = [x.get("aweme_info") for x in data.get("search_item_list") or [] if x.get("aweme_info")]
    videos += data.get("aweme_list") or []
    items: list[Item] = []
    seen: set[str] = set()
    for v in videos:
        vid = v.get("aweme_id")
        if not vid or vid in seen or v.get("is_ads"):
            continue
        seen.add(vid)
        author = (v.get("author") or {}).get("unique_id")
        stats = v.get("statistics") or {}
        desc = clean_title(v.get("desc") or "")
        items.append(
            Item(
                source="tiktok_keyword",
                platform="tiktok",
                title=(desc or f"Video by @{author}")[:200],
                rank=len(items) + 1,
                url=f"https://www.tiktok.com/@{author}/video/{vid}" if author else None,
                metrics=_metrics(
                    views=stats.get("play_count"),
                    likes=stats.get("digg_count"),
                    shares=stats.get("share_count"),
                    comments=stats.get("comment_count"),
                ),
                keyword=keyword,
                extra={"author": author, "id": vid},
            )
        )
    return items


# Reddit search ranks by relevance only and often returns year-old threads; a
# watchlist is about what is happening now.
REDDIT_KEYWORD_MAX_AGE_DAYS = 30


def parse_reddit_search(body: dict[str, Any], keyword: str, now: datetime | None = None) -> list[Item]:
    return _reddit_posts(
        (body.get("data") or {}).get("results") or [],
        "reddit_keyword",
        keyword,
        max_age_days=REDDIT_KEYWORD_MAX_AGE_DAYS,
        now=now,
    )
