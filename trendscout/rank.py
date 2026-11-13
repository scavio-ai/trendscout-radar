"""Turn a snapshot (and the one before it) into a report.

Three things matter to a reader, in this order:
  1. Topics: what matched the topics they follow, new items first.
  2. Cross-platform: a term trending on one platform that also shows up on
     another. The strongest signal the radar produces on its own.
  3. Everything else: what entered each trending list since the last scan, or
     jumped up it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Topic
from .model import Item, Signal, Snapshot, normalize
from .timeline import Timeline
from .topics import TopicResult, match_topics

# Sources whose items are short trend terms rather than posts or videos.
TERM_SOURCES = {"tiktok_searches", "google_trending", "google_trends_rss", "x_trends", "reddit_trending"}

SOURCE_LABELS = {
    "tiktok_searches": "TikTok trending searches",
    "tiktok_videos": "TikTok trending videos (sample)",
    "tiktok_sounds": "TikTok viral sounds",
    "google_trending": "Google Trending Now",
    "google_trends_rss": "Google Trends",
    "x_trends": "X trending",
    "reddit_trending": "Reddit trending searches",
    "reddit_popular": "Reddit popular",
    "hackernews": "Hacker News",
    "youtube_keyword": "YouTube",
    "tiktok_keyword": "TikTok",
    "reddit_keyword": "Reddit",
    "news_keyword": "News",
    "hackernews_keyword": "Hacker News",
}

SOURCE_ORDER = list(SOURCE_LABELS)

# Words that make a term too generic to match across platforms on their own.
_GENERIC = {
    "trend", "trends", "trending", "viral", "new", "video", "videos", "song", "dance", "dances",
    "lyrics", "today", "tonight", "news", "live", "the", "a", "an", "and", "of", "for", "to", "in",
    "on", "is", "me", "my", "you", "your", "love", "day", "game", "music", "official", "best",
}
UP_THRESHOLD = 3  # rank positions gained to count as climbing

# Sources that return a random sample on every call: "new" there means sampled,
# not rising, so they are ranked by their metric and never badged.
SAMPLED_SOURCES = {"tiktok_videos"}


@dataclass
class Entry:
    item: Item
    status: str  # "new", "up", "steady", "first" (no earlier run to compare with)
    change: int = 0  # rank positions gained since the last run


@dataclass
class Section:
    source: str
    label: str
    entries: list[Entry]


@dataclass
class Report:
    taken_at: str
    previous_at: str | None
    cross: list[Signal]
    sections: list[Section]
    topics: list[TopicResult]
    credits_used: int
    credits_remaining: int | None
    errors: dict[str, str]
    skipped: dict[str, str]
    summary: str | None = None
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def new_count(self) -> int:
        return sum(1 for s in self.sections for e in s.entries if e.status == "new")


def match_term(text: str) -> str:
    """The part of a trend term worth matching: 'GTA 5 Goodbye Trend' -> 'gta 5 goodbye'."""
    words = normalize(text).split()
    while words and words[-1] in {"trend", "trends", "trending", "ideas", "challenge"}:
        words.pop()
    return " ".join(words)


def is_specific(term: str) -> bool:
    words = term.split()
    meaningful = [w for w in words if w not in _GENERIC]
    if not meaningful:
        return False
    if len(words) == 1:
        return len(words[0]) >= 5 and not words[0].isdigit()
    return sum(len(w) for w in meaningful) >= 5


def _contains(haystack: str, needle: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def cross_platform(snapshot: Snapshot, previous: Snapshot | None, limit: int) -> list[Signal]:
    global_items = [i for i in snapshot.items if i.keyword is None]
    terms: dict[str, list[Item]] = {}
    for item in global_items:
        if item.source in TERM_SOURCES:
            t = match_term(item.title)
            if is_specific(t):
                terms.setdefault(t, []).append(item)

    prev_cross = {s.title.lower() for s in cross_platform(previous, None, 10_000)} if previous else set()

    texts = [(i, normalize(i.title)) for i in global_items]
    signals: list[Signal] = []
    for term, term_items in terms.items():
        evidence: list[Item] = list(term_items)
        for item, text in texts:
            if item in evidence:
                continue
            if _contains(text, term):
                evidence.append(item)
        platforms = sorted({i.platform for i in evidence})
        if len(platforms) < 2:
            continue
        lead = min(term_items, key=lambda i: i.rank)
        is_new = previous is not None and lead.title.lower() not in prev_cross
        score = 10 * len(platforms) + sum(1 / max(i.rank, 1) for i in evidence) + (5 if is_new else 0)
        status = "new" if is_new else ("first" if previous is None else "steady")
        signals.append(Signal(title=lead.title, score=round(score, 3), status=status, platforms=platforms, items=evidence[:6]))

    signals.sort(key=lambda s: -s.score)
    return signals[:limit]


def _entries(items: list[Item], prev_ranks: dict[str, int] | None) -> list[Entry]:
    out: list[Entry] = []
    for item in sorted(items, key=lambda i: i.rank):
        if prev_ranks is None:
            out.append(Entry(item, "first"))
        elif item.key not in prev_ranks:
            out.append(Entry(item, "new"))
        else:
            gained = prev_ranks[item.key] - item.rank
            out.append(Entry(item, "up" if gained >= UP_THRESHOLD else "steady", max(gained, 0)))
    return out


def _by_source(items: list[Item]) -> dict[str, list[Item]]:
    grouped: dict[str, list[Item]] = {}
    for i in items:
        grouped.setdefault(i.source, []).append(i)
    return grouped


def build_report(
    snapshot: Snapshot,
    previous: Snapshot | None,
    top_n: int,
    topics: list[Topic] | None = None,
    tl: Timeline | None = None,
) -> Report:
    prev_by_source = _by_source([i for i in previous.items if i.keyword is None]) if previous else {}

    sections: list[Section] = []
    grouped = _by_source([i for i in snapshot.items if i.keyword is None])
    for source in sorted(grouped, key=lambda s: SOURCE_ORDER.index(s) if s in SOURCE_ORDER else 99):
        prev_ranks = None
        if previous is not None and source not in SAMPLED_SOURCES:
            prev_ranks = {i.key: i.rank for i in prev_by_source.get(source, [])}
        entries = _entries(grouped[source], prev_ranks)
        if source in SAMPLED_SOURCES:
            entries = [Entry(e.item, "steady") for e in entries]
        # New entries first, then climbers, then the rest, each in list order.
        order = {"new": 0, "up": 1, "first": 2, "steady": 2}
        entries.sort(key=lambda e: (order[e.status], e.item.rank))
        sections.append(Section(source, SOURCE_LABELS.get(source, source), entries[:top_n]))

    return Report(
        taken_at=snapshot.taken_at,
        previous_at=previous.taken_at if previous else None,
        cross=cross_platform(snapshot, previous, top_n),
        sections=sections,
        topics=match_topics(topics or [], snapshot.items, tl, snapshot.taken_at, top_n, is_first_scan=previous is None),
        credits_used=snapshot.credits_used,
        credits_remaining=snapshot.credits_remaining,
        errors=snapshot.errors,
        skipped=snapshot.skipped,
        counts={s: len(v) for s, v in _by_source(snapshot.items).items()},
    )
