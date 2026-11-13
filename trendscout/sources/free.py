"""Sources that need no API key: Hacker News, Google Trends RSS, Google News RSS."""

from __future__ import annotations

import time

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import httpx

from ..model import Item

HN_TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
GOOGLE_TRENDS_RSS = "https://trends.google.com/trending/rss?geo={geo}"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl={hl}-{gl}&gl={gl}&ceid={gl}:{hl}"
HN_SEARCH = "https://hn.algolia.com/api/v1/search"
HN_SEARCH_DAYS = 7
HN_SEARCH_MIN_POINTS = 5

_HT = "{https://trends.google.com/trending/rss}"


def _iso(rfc822: str | None) -> str | None:
    if not rfc822:
        return None
    try:
        return parsedate_to_datetime(rfc822).isoformat()
    except (TypeError, ValueError):
        return None


def _traffic(text: str | None) -> float | None:
    """'500+' or '20K+' -> 500.0 or 20000.0."""
    if not text:
        return None
    m = re.match(r"([\d.,]+)\s*([KkMm]?)", text.strip())
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return n * {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1)


def parse_google_trends_rss(xml: str) -> list[Item]:
    root = ET.fromstring(xml)
    items: list[Item] = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        traffic = _traffic(node.findtext(f"{_HT}approx_traffic"))
        news = node.find(f"{_HT}news_item")
        items.append(
            Item(
                source="google_trends_rss",
                platform="google",
                title=title,
                rank=len(items) + 1,
                url=f"https://trends.google.com/trends/explore?q={quote_plus(title)}",
                metrics={"searches": traffic} if traffic is not None else {},
                published_at=_iso(node.findtext("pubDate")),
                extra={"headline": news.findtext(f"{_HT}news_item_title") if news is not None else None},
            )
        )
    return items


def parse_google_news_rss(xml: str, keyword: str) -> list[Item]:
    root = ET.fromstring(xml)
    items: list[Item] = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = node.findtext("link")
        if not title or not link:
            continue
        source = node.find("source")
        publisher = source.text if source is not None else None
        # Google appends " - Publisher" to every headline; drop it.
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3]
        items.append(
            Item(
                source="news_keyword",
                platform="news",
                title=title,
                rank=len(items) + 1,
                url=link,
                keyword=keyword,
                published_at=_iso(node.findtext("pubDate")),
                extra={"publisher": publisher},
            )
        )
    return items


def parse_hn_item(story: dict[str, Any], rank: int) -> Item | None:
    if not story or story.get("type") != "story" or not story.get("title") or story.get("dead"):
        return None
    sid = story.get("id")
    return Item(
        source="hackernews",
        platform="hackernews",
        title=story["title"].strip(),
        rank=rank,
        url=story.get("url") or f"https://news.ycombinator.com/item?id={sid}",
        metrics={k: float(story[v]) for k, v in (("points", "score"), ("comments", "descendants")) if isinstance(story.get(v), int)},
        extra={"discussion": f"https://news.ycombinator.com/item?id={sid}"},
    )


def parse_hn_search(body: dict[str, Any], keyword: str) -> list[Item]:
    """Stories from the Hacker News search (Algolia), most relevant first."""
    items: list[Item] = []
    for hit in body.get("hits") or []:
        title = (hit.get("title") or "").strip()
        sid = hit.get("objectID") or hit.get("story_id")
        if not title or not sid:
            continue
        metrics = {k: float(hit[v]) for k, v in (("points", "points"), ("comments", "num_comments")) if isinstance(hit.get(v), int)}
        items.append(Item(
            source="hackernews_keyword",
            platform="hackernews",
            title=title,
            rank=len(items) + 1,
            url=hit.get("url") or f"https://news.ycombinator.com/item?id={sid}",
            metrics=metrics,
            keyword=keyword,
            published_at=hit.get("created_at"),
            extra={"discussion": f"https://news.ycombinator.com/item?id={sid}"},
        ))
    return items


def fetch_hn_search(http: httpx.Client, keyword: str, now: float | None = None) -> list[Item]:
    """Hacker News stories of the last week whose title mentions the keyword. Free, no key."""
    since = int((now if now is not None else time.time()) - HN_SEARCH_DAYS * 86400)
    params = {
        "query": keyword, "tags": "story", "restrictSearchableAttributes": "title",
        "numericFilters": f"created_at_i>{since},points>={HN_SEARCH_MIN_POINTS}", "hitsPerPage": 30,
    }
    return parse_hn_search(http.get(HN_SEARCH, params=params).raise_for_status().json(), keyword)


def fetch_hackernews(http: httpx.Client, limit: int) -> list[Item]:
    ids = http.get(HN_TOP).raise_for_status().json()[:limit]
    items: list[Item] = []
    for sid in ids:
        item = parse_hn_item(http.get(HN_ITEM.format(id=sid)).raise_for_status().json(), len(items) + 1)
        if item:
            items.append(item)
    return items


def fetch_google_trends_rss(http: httpx.Client, geo: str) -> list[Item]:
    return parse_google_trends_rss(http.get(GOOGLE_TRENDS_RSS.format(geo=geo)).raise_for_status().text)


def fetch_google_news_rss(http: httpx.Client, keyword: str, gl: str, hl: str) -> list[Item]:
    # when:2d keeps it to the last two days, so "new" means published since the last scan.
    url = GOOGLE_NEWS_RSS.format(q=quote_plus(f"{keyword} when:2d"), gl=gl.upper(), hl=hl.lower())
    return parse_google_news_rss(http.get(url).raise_for_status().text, keyword)
