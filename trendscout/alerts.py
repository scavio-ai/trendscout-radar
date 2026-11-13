"""What an alert says, and how each channel shows it.

An alert leads with the user's topics. Depending on `alerts.send` it carries:
  new      only items first seen in this scan; no alert at all when there are none
  summary  everything that matched since midnight (local time)
  every    the current matches, after every scan
plus terms trending on `alerts.breakout_platforms` or more platforms at once.

Each channel has a size limit. When a message is too long, whole lines are
dropped from the longest topic first and replaced by "and N more", so a topic
is never cut off half way.
"""

from __future__ import annotations

import copy
import html
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable

from .config import Alerts, Topic
from .model import Item
from .rank import Report
from .timeline import Seen, Timeline
from .topics import TRENDING_SOURCES, Hit, match_topics, seen_today

PLATFORM_NAMES = {
    "tiktok": "TikTok", "youtube": "YouTube", "reddit": "Reddit", "google": "Google",
    "x": "X", "hackernews": "Hacker News", "news": "News",
}
LINES_PER_BLOCK = 5
TITLE_CHARS = 120


@dataclass
class Line:
    """One item, laid out like a TrendRadar push line:
    [Platform] Title [#5-#13] - [08:00 ~ 12:00] (seen 15 times)"""

    platform: str
    title: str
    url: str | None
    rank: str = ""  # "#5" or "#5-#13", for items on a trending list
    window: str = ""  # "08:00 ~ 12:00": first and last scan that saw it
    times: int = 0  # scans that saw it
    info: str = ""  # numbers, publisher and age, for search results and news
    new: bool = False

    @property
    def meta(self) -> str:
        """Everything after the title, in one string."""
        parts = []
        if self.rank:
            parts.append(f"[{self.rank}]")
        if self.window:
            parts.append(f"- [{self.window}]")
        if self.times > 1:
            parts.append(f"(seen {self.times} times)")
        if self.info:
            parts.append(("- " if parts else "") + self.info)
        return " ".join(parts)


@dataclass
class Block:
    heading: str
    note: str = ""
    lines: list[Line] = field(default_factory=list)
    more: int = 0


@dataclass
class Digest:
    title: str
    when: str
    blocks: list[Block]
    summary: str | None = None
    url: str | None = None
    empty_note: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ wording


def _clock(iso: str, now_local: datetime) -> str:
    dt = datetime.fromisoformat(iso).astimezone(now_local.tzinfo)
    if dt.date() == now_local.date():
        return dt.strftime("%H:%M")
    return dt.strftime("%b %d").replace(" 0", " ")


def _human(n: float) -> str:
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suffix
    return str(int(n))


def _age(iso: str | None, now_local: datetime) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    hours = (now_local - dt.astimezone(now_local.tzinfo)).total_seconds() / 3600
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{int(hours)} h ago"
    days = int(hours // 24)
    return "1 day ago" if days == 1 else f"{days} days ago"


def fields(item: Item, seen: Seen | None, now_local: datetime) -> tuple[str, str, int, str]:
    """(rank, window, times, info) for one item."""
    rank = window = ""
    times = 0
    if item.source in TRENDING_SOURCES:
        rank = f"#{seen.best}-#{seen.worst}" if seen and seen.best != seen.worst else f"#{item.rank}"
        if seen:
            first, last = _clock(seen.first, now_local), _clock(seen.last, now_local)
            window = first if first == last else f"{first} ~ {last}"
            times = seen.count
    info: list[str] = []
    m = item.metrics
    for key, label in (("views", "views"), ("searches", "searches"), ("upvotes", "upvotes"), ("points", "points"), ("likes", "likes")):
        if m.get(key):
            info.append(f"{_human(m[key])} {label}")
            break
    if item.extra.get("subreddit"):
        info.append(f"r/{item.extra['subreddit']}")
    elif item.extra.get("publisher") or item.extra.get("channel"):
        info.append(str(item.extra.get("publisher") or item.extra.get("channel")))
    if item.source not in TRENDING_SOURCES:
        age = _age(item.published_at, now_local)
        if age:
            info.append(age)
    return rank, window, times, ", ".join(info)


def detail(item: Item, seen: Seen | None, now_local: datetime) -> str:
    """The same context as one plain string, for reports."""
    rank, window, times, info = fields(item, seen, now_local)
    return Line("", "", None, rank, window, times, info).meta


def _line(hit_item: Item, seen: Seen | None, new: bool, now_local: datetime) -> Line:
    title = hit_item.title if len(hit_item.title) <= TITLE_CHARS else hit_item.title[: TITLE_CHARS - 3].rstrip() + "..."
    rank, window, times, info = fields(hit_item, seen, now_local)
    return Line(PLATFORM_NAMES.get(hit_item.platform, hit_item.platform), title, hit_item.url, rank, window, times, info, new)


# ------------------------------------------------------------------ building


def build(
    report: Report,
    alerts: Alerts,
    topics: list[Topic],
    tl: Timeline | None,
    now_local: datetime,
    mode: str | None = None,
    site_url: str | None = None,
    top_n: int = 10,
    since: dict[str, str] | None = None,
    breakouts: bool = True,
    fallback: bool = True,
) -> Digest | None:
    """The alert for this scan, or None when there is nothing worth sending.

    `topics` are the topics this alert covers (for one channel, the topics routed
    to it). `since` maps a topic to the time of its last alert; in "new" mode such
    a topic lists everything first seen after that, across every scan in between.
    `breakouts` adds terms trending on several platforms; `fallback` lists new
    trending terms when no topics exist at all.
    """
    mode = mode or alerts.send
    when = now_local.strftime("%b %d, %H:%M").replace(" 0", " ")
    blocks: list[Block] = []
    new_total = 0

    if topics:
        wanted = {t.name for t in topics}
        if mode == "summary" and tl:
            results = match_topics(topics, seen_today(tl, now_local), tl, "", 10_000)
        else:
            results = [r for r in report.topics if r.topic.name in wanted]
            if mode == "new" and since and tl:
                results = [_since_last_alert(r, since.get(r.topic.name), tl, top_n) for r in results]
        for r in results:
            hits: list[Hit] = [h for h in r.hits if h.new] if mode == "new" else list(r.hits)
            total = r.new_count if mode == "new" else r.total
            if not hits:
                continue
            new_total += r.new_count
            note = f"{total} new" if mode == "new" else (f"{total} today" if mode == "summary" else f"{total} matching")
            shown = hits[:LINES_PER_BLOCK]
            blocks.append(Block(r.topic.name, note, [_line(h.item, h.seen, h.new, now_local) for h in shown], max(0, total - len(shown))))
    elif fallback:
        # No topics yet: the newest entries on the trending lists people care about most.
        for section in report.sections:
            if section.source not in ("tiktok_searches", "google_trending", "google_trends_rss", "x_trends"):
                continue
            picks = [e for e in section.entries if e.status == "new" or mode != "new"][:3]
            if picks:
                blocks.append(Block(section.label, "new" if mode == "new" else "", [
                    _line(e.item, tl.get(e.item.key) if tl else None, e.status == "new", now_local) for e in picks
                ]))

    wide = [s for s in report.cross if len(s.platforms) >= alerts.breakout_platforms and (mode != "new" or s.status == "new")] if breakouts else []
    if wide:
        lines = []
        for s in wide[:LINES_PER_BLOCK]:
            names = ", ".join(PLATFORM_NAMES.get(p, p) for p in s.platforms)
            lines.append(Line(names, s.title, s.lead.url, info=f"trending on {len(s.platforms)} platforms", new=s.status == "new"))
        blocks.append(Block(f"Trending on {alerts.breakout_platforms}+ platforms at once", "", lines, max(0, len(wide) - LINES_PER_BLOCK)))

    if not blocks and mode == "new":
        return None

    named = [b.heading for b in blocks if not b.heading.startswith("Trending on")]
    if mode == "new":
        title = f"{new_total} new in {_join(named)}" if named and new_total else "New trends"
    elif mode == "summary":
        title = "Today in " + _join([t.name for t in topics]) if topics else "Today's trends"
    else:
        title = "Latest for " + _join([t.name for t in topics]) if topics else "Latest trends"
    empty = None if blocks else "Nothing matched your topics this time."
    return Digest(title, when, blocks, report.summary, site_url, empty)


def _since_last_alert(result, since: str | None, tl: Timeline, top_n: int):
    """The topic's matches first seen after its last alert, from every scan since then."""
    if not since:
        return result
    cutoff = datetime.fromisoformat(since)
    fresh = [s.to_item() for s in tl.values() if datetime.fromisoformat(s.first) > cutoff]
    again = match_topics([result.topic], fresh, tl, "", top_n)[0]
    for h in again.hits:
        h.new = True
    again.new_count = again.total
    return again


def _join(names: list[str]) -> str:
    if len(names) <= 3:
        return ", ".join(names)
    return ", ".join(names[:2]) + f" and {len(names) - 2} more"


# ------------------------------------------------------------------ formats


def _plain(d: Digest) -> str:
    out = [f"TrendScout: {d.title}", d.when]
    if d.summary:
        out += ["", d.summary]
    if d.empty_note:
        out += ["", d.empty_note]
    for b in d.blocks:
        out += ["", f"{b.heading}: {b.note}" if b.note else b.heading]
        for n, ln in enumerate(b.lines, 1):
            out.append(f"  {n}. [{ln.platform}] {ln.title}" + (f" {ln.meta}" if ln.meta else "") + (" NEW" if ln.new else ""))
            if ln.url:
                out.append(f"     {ln.url}")
        if b.more:
            out.append(f"  and {b.more} more")
    if d.url:
        out += ["", f"Full report: {d.url}"]
    return "\n".join(out)


def _discord(d: Digest) -> str:
    def esc(s: str) -> str:
        return s.replace("[", "(").replace("]", ")").replace("*", "").replace("_", " ").replace("`", "'")

    out = [f"**TrendScout: {esc(d.title)}**  {d.when}"]
    if d.summary:
        out += ["", d.summary]
    if d.empty_note:
        out += ["", d.empty_note]
    for b in d.blocks:
        out += ["", f"**{esc(b.heading)}**" + (f": {b.note}" if b.note else "")]
        for n, ln in enumerate(b.lines, 1):
            title = f"[{esc(ln.title)}](<{ln.url}>)" if ln.url else esc(ln.title)
            out.append(f"{n}. ({esc(ln.platform)}) {title}" + (f" {ln.meta}" if ln.meta else "") + (" **NEW**" if ln.new else ""))
        if b.more:
            out.append(f"and {b.more} more")
    if d.url:
        out += ["", f"Full report: <{d.url}>"]
    return "\n".join(out)


def _telegram(d: Digest) -> str:
    e = html.escape
    out = [f"<b>TrendScout: {e(d.title)}</b>", e(d.when)]
    if d.summary:
        out += ["", e(d.summary)]
    if d.empty_note:
        out += ["", e(d.empty_note)]
    for b in d.blocks:
        out += ["", f"<b>{e(b.heading)}</b>" + (f": {e(b.note)}" if b.note else "")]
        for n, ln in enumerate(b.lines, 1):
            title = f'<a href="{e(ln.url, quote=True)}">{e(ln.title)}</a>' if ln.url else e(ln.title)
            meta = f" <i>{e(ln.meta)}</i>" if ln.meta else ""
            out.append(f"{n}. [{e(ln.platform)}] {title}{meta}" + (" <b>NEW</b>" if ln.new else ""))
        if b.more:
            out.append(f"and {b.more} more")
    if d.url:
        out += ["", f'<a href="{e(d.url, quote=True)}">Full report</a>']
    return "\n".join(out)


def _slack(d: Digest) -> str:
    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    out = [f"*TrendScout: {esc(d.title)}*  {d.when}"]
    if d.summary:
        out += ["", esc(d.summary)]
    if d.empty_note:
        out += ["", d.empty_note]
    for b in d.blocks:
        out += ["", f"*{esc(b.heading)}*" + (f": {b.note}" if b.note else "")]
        for n, ln in enumerate(b.lines, 1):
            title = f"<{ln.url}|{esc(ln.title).replace('|', '-')}>" if ln.url else esc(ln.title)
            meta = f" _{esc(ln.meta)}_" if ln.meta else ""
            out.append(f"{n}. [{esc(ln.platform)}] {title}{meta}" + (" *NEW*" if ln.new else ""))
        if b.more:
            out.append(f"and {b.more} more")
    if d.url:
        out += ["", f"<{d.url}|Full report>"]
    return "\n".join(out)


def to_html(d: Digest) -> str:
    """A small HTML email body."""
    e = html.escape
    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:640px;color:#1b1e24;line-height:1.45">',
        f'<h2 style="margin:0 0 4px;font-size:20px">{e(d.title)}</h2><div style="color:#5d636e;font-size:13px">{e(d.when)}</div>',
    ]
    if d.summary:
        parts.append(f'<p style="margin:16px 0;padding:10px 14px;border-left:3px solid #2446e8;background:#f2f3ee">{e(d.summary)}</p>')
    if d.empty_note:
        parts.append(f"<p>{e(d.empty_note)}</p>")
    for b in d.blocks:
        parts.append(f'<h3 style="margin:22px 0 6px;font-size:16px">{e(b.heading)}' + (f' <span style="color:#5d636e;font-weight:normal">({e(b.note)})</span>' if b.note else "") + "</h3><ol style=\"padding-left:22px;margin:0\">")
        for ln in b.lines:
            title = f'<a href="{e(ln.url or "", quote=True)}" style="color:#2446e8">{e(ln.title)}</a>' if ln.url else e(ln.title)
            badge = ' <b style="color:#2446e8">NEW</b>' if ln.new else ""
            parts.append(f'<li style="margin:6px 0"><span style="color:#6b7280;font-size:13px">[{e(ln.platform)}]</span> {title}{badge}<br><span style="color:#6b7280;font-size:13px">{e(ln.meta)}</span></li>')
        if b.more:
            parts.append(f'<li style="color:#5d636e">and {b.more} more</li>')
        parts.append("</ol>")
    if d.url:
        parts.append(f'<p style="margin-top:22px"><a href="{e(d.url, quote=True)}">Open the full report</a></p>')
    parts.append('<p style="color:#8a909a;font-size:12px;margin-top:22px">Sent by TrendScout.</p></div>')
    return "".join(parts)


FORMATS: dict[str, Callable[[Digest], str]] = {"plain": _plain, "discord": _discord, "telegram": _telegram, "slack": _slack}


def render(d: Digest, fmt: str, max_chars: int) -> str:
    """The message in a channel's format, shortened line by line to fit `max_chars`."""
    draw = FORMATS[fmt]
    d = copy.deepcopy(d)
    text = draw(d)
    while len(text) > max_chars:
        biggest = max((b for b in d.blocks if b.lines), key=lambda b: len(b.lines), default=None)
        if biggest is None:
            if d.summary:
                d.summary = None
                text = draw(d)
                continue
            return text[: max_chars - 3] + "..."
        biggest.lines.pop()
        biggest.more += 1
        text = draw(d)
    return text
