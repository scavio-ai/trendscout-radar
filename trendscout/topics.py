"""Match a scan's items to the user's topics. Costs nothing: it reads what was collected.

An item belongs to a topic when its text contains any of the topic's `match`
phrases (the name when there are none), every `must` word, and no `exclude`
word, all as whole words, case-insensitive. Items a topic's own searches found
(Google News, and TikTok, YouTube and Reddit when `search` is on) only need to
pass `must` and `exclude`: the search already chose them for the topic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .config import Topic
from .model import Item, normalize
from .timeline import Seen, Timeline

# Lists of what is trending everywhere, as opposed to searches for one topic.
TRENDING_SOURCES = {
    "tiktok_searches", "tiktok_videos", "tiktok_sounds", "google_trending", "google_trends_rss",
    "x_trends", "reddit_trending", "reddit_popular", "hackernews",
}

# Titles that carry no information on their own.
_JUNK = re.compile(r"^(video by @\S+|#\S+|[\W_]+)$", re.IGNORECASE)


def _has(text: str, phrase: str) -> bool:
    p = normalize(phrase)
    return bool(p) and re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", text) is not None


def _text(item: Item) -> str:
    parts = [item.title]
    related = item.extra.get("related")
    if isinstance(related, list):
        parts += [r for r in related if isinstance(r, str)]
    return normalize(" ".join(parts))


def passes_rules(topic: Topic, text: str) -> bool:
    return all(_has(text, w) for w in topic.must) and not any(_has(text, w) for w in topic.exclude)


def matches(topic: Topic, item: Item) -> bool:
    if is_junk(item):
        return False
    text = _text(item)
    if not passes_rules(topic, text):
        return False
    if item.keyword is not None:
        return item.keyword.lower() == topic.name.lower()
    return any(_has(text, p) for p in topic.phrases)


def is_junk(item: Item) -> bool:
    return bool(_JUNK.match(item.title.strip()))


@dataclass
class Hit:
    item: Item
    new: bool  # first seen in this scan
    seen: Seen | None
    trending: bool  # from a trending list rather than a search

    def to_dict(self) -> dict:
        d = {"new": self.new, "trending": self.trending, **self.item.to_dict()}
        if self.seen:
            d["seen"] = {"first": self.seen.first, "last": self.seen.last, "count": self.seen.count,
                         "best": self.seen.best, "worst": self.seen.worst}
        return d


@dataclass
class TopicResult:
    topic: Topic
    hits: list[Hit] = field(default_factory=list)
    total: int = 0  # matches before the top_n cut
    new_count: int = 0  # new matches before the top_n cut

    def to_dict(self) -> dict:
        return {"name": self.topic.name, "total": self.total, "new": self.new_count,
                "search": self.topic.search, "hits": [h.to_dict() for h in self.hits]}


def _order(hit: Hit) -> tuple:
    # What is on a public trending list comes first (that is the signal), by rank;
    # then search results, new before seen, by their numbers or by date (news).
    if hit.trending:
        value = float(hit.item.rank)
    else:
        m = hit.item.metrics
        weight = m.get("views") or m.get("searches") or m.get("upvotes") or m.get("points") or m.get("likes") or 0
        value = -float(weight) if weight else -_timestamp(hit.item.published_at) / 1e12
    return (not hit.trending, not hit.new, value)


def _timestamp(iso: str | None) -> float:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def match_topics(
    topics: list[Topic],
    items: list[Item],
    tl: Timeline | None,
    taken_at: str,
    limit: int,
    is_first_scan: bool = False,
) -> list[TopicResult]:
    results = []
    for topic in topics:
        hits: list[Hit] = []
        seen_titles: set[str] = set()
        for item in items:
            if not matches(topic, item):
                continue
            title_key = normalize(item.title)
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            s = tl.get(item.key) if tl else None
            new = not is_first_scan and (s is None or s.first == taken_at)
            hits.append(Hit(item, new, s, item.source in TRENDING_SOURCES))
        hits.sort(key=_order)
        results.append(TopicResult(topic, hits[:limit], len(hits), sum(1 for h in hits if h.new)))
    return results


def seen_today(tl: Timeline, now_local: datetime) -> list[Item]:
    """Items first seen today (local time), for the daily summary."""
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for s in tl.values():
        if datetime.fromisoformat(s.first).astimezone(now_local.tzinfo) >= start:
            out.append(s.to_item())
    return out
