"""Keep the public report page safe to share and to index.

Live trending lists sometimes carry profanity or adult content. The public page
(site/) drops those items; the local app and the reports/ files keep everything.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .model import Item, plain
from .rank import Report

# Whole words, matched case-insensitively, with an optional plural "s".
# Deliberately short: it catches what would get a page filtered by SafeSearch
# or embarrass the person sharing it, not every rude word.
BLOCKED_WORDS = (
    "anal", "bitch", "blowjob", "boobs", "camgirl", "cock", "cum", "cunt", "deepthroat", "dick",
    "dildo", "erotic", "fag", "faggot", "fap", "fuck", "fucked", "fucker", "fucking", "handjob",
    "hentai", "horny", "milf", "naked", "nigga", "nigger", "nipple", "nsfw", "nude", "nudes",
    "onlyfans", "orgasm", "porn", "porno", "pornhub", "pussy", "rape", "retard", "sex", "sexy",
    "shit", "slut", "stripper", "tits", "whore", "xxx",
)

# Starred spellings such as f*ck, f**k and sh*t.
_STARRED = r"f\*+c?k\w*|sh\*+t\w*|b\*+t?ch\w*"


def pattern(extra: list[str] | tuple[str, ...] = ()) -> re.Pattern[str]:
    words = sorted({w.strip().lower() for w in (*BLOCKED_WORDS, *extra) if w.strip()}, key=len, reverse=True)
    alts = "|".join(re.escape(w) for w in words)
    return re.compile(rf"(?<!\w)(?:(?:{alts})s?|{_STARRED})(?!\w)", re.IGNORECASE)


def blocked(item: Item, rx: re.Pattern[str]) -> bool:
    if item.extra.get("nsfw"):
        return True
    text = " ".join(str(v) for v in (item.title, item.extra.get("subreddit"), item.extra.get("channel")) if v)
    return bool(rx.search(plain(text)))


def safe_report(report: Report, extra: list[str] | tuple[str, ...] = ()) -> Report:
    """A copy of the report without blocked items."""
    rx = pattern(extra)
    sections = [replace(s, entries=[e for e in s.entries if not blocked(e.item, rx)]) for s in report.sections]
    topics = []
    for tr in report.topics:
        hits = [h for h in tr.hits if not blocked(h.item, rx)]
        dropped = len(tr.hits) - len(hits)
        new_dropped = sum(1 for h in tr.hits if h.new) - sum(1 for h in hits if h.new)
        topics.append(replace(tr, hits=hits, total=max(0, tr.total - dropped), new_count=max(0, tr.new_count - new_dropped)))
    cross = [s for s in report.cross if not rx.search(plain(s.title)) and not any(blocked(i, rx) for i in s.items)]
    return replace(report, sections=[s for s in sections if s.entries], topics=topics, cross=cross)
