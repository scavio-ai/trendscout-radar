"""Render a Report as Markdown, HTML, JSON, or a short chat digest."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime
from importlib import resources
from pathlib import Path
from xml.sax.saxutils import escape

from jinja2 import Environment, PackageLoader, select_autoescape

from . import __version__, safety
from .model import Item, normalize
from .alerts import PLATFORM_NAMES, detail
from .rank import Entry, Report

REPO_URL = "https://github.com/scavio-ai/trendscout"
SCAVIO_URL = "https://scavio.dev/?utm_source=github&utm_medium=report&utm_campaign=trendscout"


def human(n: float | None) -> str:
    if n is None:
        return ""
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            v = n / div
            return f"{v:.1f}{suffix}".replace(".0" + suffix, suffix)
    return f"{int(n)}"


def metric_text(item: Item) -> str:
    m = item.metrics
    parts = []
    for key, label in (
        ("searches", "searches"),
        ("views", "views"),
        ("videos", "videos"),
        ("upvotes", "upvotes"),
        ("points", "points"),
        ("comments", "comments"),
    ):
        if key in m:
            parts.append(f"{human(m[key])} {label}")
    if "growth_pct" in m:
        parts.append(f"+{human(m['growth_pct'])}%")
    for key in ("subreddit", "channel", "publisher", "context"):
        v = item.extra.get(key)
        if v:
            parts.append(f"r/{v}" if key == "subreddit" else str(v))
            break
    return " | ".join(parts[:3])


def badge(entry: Entry) -> str:
    if entry.status == "new":
        return "NEW"
    if entry.status == "up":
        return f"UP {entry.change}"
    return ""


def when(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso


def _link(title: str, url: str | None) -> str:
    safe = title.replace("[", "(").replace("]", ")").replace("\n", " ")
    return f"[{safe}]({url})" if url else safe


# ------------------------------------------------------------------ markdown


def to_markdown(report: Report, site_url: str | None = None) -> str:
    out = [f"# Trend radar - {when(report.taken_at)}", ""]
    if report.previous_at:
        out.append(f"Compared with the run at {when(report.previous_at)}. {report.new_count} new entries.")
    else:
        out.append("First run: everything below is the baseline. The next run will flag what is new and climbing.")
    out.append("")

    if report.summary:
        out += ["## Briefing", "", report.summary, ""]

    now_local = datetime.now().astimezone()
    for tr in report.topics:
        if not tr.hits:
            continue
        note = f"{tr.new_count} new, {tr.total} matching" if report.previous_at else f"{tr.total} matching"
        out += [f"## {tr.topic.name} ({note})", ""]
        for h in tr.hits:
            tag = " **NEW**" if h.new else ""
            info = detail(h.item, h.seen, now_local)
            out.append(f"- {_link(h.item.title[:160], h.item.url)}{tag} - {PLATFORM_NAMES.get(h.item.platform, h.item.platform)}" + (f", {info}" if info else ""))
        out.append("")

    if report.cross:
        out += ["## Rising on more than one platform", ""]
        for s in report.cross:
            tag = " **NEW**" if s.status == "new" else ""
            out.append(f"- **{s.title}**{tag} - {', '.join(s.platforms)}")
            for it in s.items[1:4]:
                out.append(f"  - {it.platform}: {_link(it.title[:120], it.url)}")
        out.append("")

    for section in report.sections:
        out += [f"## {section.label}", ""]
        for n, e in enumerate(section.entries, 1):
            b = badge(e)
            meta = metric_text(e.item)
            line = f"{n}. {_link(e.item.title[:160], e.item.url)}"
            if b:
                line += f" **{b}**"
            if meta:
                line += f" - {meta}"
            out.append(line)
        out.append("")

    out += _footer_md(report, site_url)
    return "\n".join(out).rstrip() + "\n"


def _footer_md(report: Report, site_url: str | None) -> list[str]:
    out = ["---", ""]
    if report.credits_used:
        line = f"Scavio credits used this run: {report.credits_used}"
        if report.credits_remaining is not None:
            line += f" ({report.credits_remaining} left)"
        out.append(line + ".")
    if report.errors:
        out.append("Sources that failed: " + "; ".join(f"{k} ({v})" for k, v in report.errors.items()) + ".")
    if report.skipped:
        out.append("Sources skipped: " + "; ".join(f"{k} ({v})" for k, v in report.skipped.items()) + ".")
    if site_url:
        out.append(f"Full report: {site_url}")
    out.append(f"Made with [TrendScout]({REPO_URL}) v{__version__}.")
    return out


# ------------------------------------------------------------------ html / json

_env = Environment(loader=PackageLoader("trendscout", "templates"), autoescape=select_autoescape(["html", "j2"]))
_env.filters["human"] = human
_env.filters["when"] = when
_env.filters["hm"] = lambda iso: datetime.fromisoformat(iso).astimezone().strftime("%H:%M") if iso else ""
_env.globals.update(
    metric_text=metric_text, badge=badge, version=__version__, repo_url=REPO_URL, scavio_url=SCAVIO_URL,
    platform_name=lambda p: PLATFORM_NAMES.get(p, p),
    hit_detail=lambda h: detail(h.item, h.seen, datetime.now().astimezone()),
)


def to_html(report: Report, archive: list[str] | None = None, site_url: str | None = None, indexable: bool = True) -> str:
    page = page_meta(report, site_url if indexable else None)
    return _env.get_template("report.html.j2").render(r=report, archive=archive or [], page=page, indexable=indexable)


# ------------------------------------------------------------------ public page

# Section headings on the public page, worded the way people search for them.
PAGE_HEADINGS = {
    "tiktok_searches": "Trending searches on TikTok",
    "tiktok_videos": "Trending videos on TikTok",
    "tiktok_sounds": "Viral sounds on TikTok",
    "google_trending": "Trending on Google",
    "google_trends_rss": "Trending on Google",
    "x_trends": "Trending on X",
    "reddit_trending": "Trending searches on Reddit",
    "reddit_popular": "Popular on Reddit",
    "hackernews": "Top of Hacker News",
}
_PLATFORM_ORDER = ["tiktok", "google", "x", "reddit", "hackernews"]
# Sources whose first entries answer "what is trending on <platform> today".
_ANSWER_SOURCES = [
    ("tiktok_searches", "What is trending on TikTok today?", "The top TikTok searches right now are"),
    ("google_trending", "What is trending on Google today?", "The top Google searches right now are"),
    ("google_trends_rss", "What is trending on Google today?", "The top Google searches right now are"),
    ("x_trends", "What is trending on X today?", "The top trends on X right now are"),
    ("reddit_popular", "What is popular on Reddit today?", "The top posts on Reddit right now are"),
    ("hackernews", "What is on top of Hacker News today?", "The top Hacker News stories right now are"),
]


def _join(words: list[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    if len(text) <= n:
        return text
    return text[: n - 3].rsplit(" ", 1)[0].rstrip(" ,.;:-") + "..."


def _long_date(iso: str) -> str:
    d = datetime.fromisoformat(iso).astimezone(timezone.utc)
    return f"{d:%A, %B} {d.day}, {d.year} at {d:%H:%M} UTC"


def _cadence(report: Report) -> int | None:
    """Hours between this scan and the one before, when that looks like a schedule."""
    if not report.previous_at:
        return None
    gap = datetime.fromisoformat(report.taken_at) - datetime.fromisoformat(report.previous_at)
    hours = round(gap.total_seconds() / 3600)
    return hours if 1 <= hours <= 24 else None


def page_meta(report: Report, site_url: str | None = None) -> dict:
    """Title, description, headings, FAQ and structured data for the public page."""
    sections = [s for s in report.sections if s.entries]
    present = {s.entries[0].item.platform for s in sections}
    platforms = [PLATFORM_NAMES.get(p, p) for p in _PLATFORM_ORDER if p in present]
    title = f"What's Trending Today on {_join(platforms)}" if platforms else "What's Trending Today"

    by_source = {s.source: s for s in sections}
    answers, top_terms = [], []
    for source, question, lead in _ANSWER_SOURCES:
        sec = by_source.get(source)
        if not sec or any(a["q"] == question for a in answers):
            continue
        quoted = source in ("reddit_popular", "hackernews")
        names = [_clip(e.item.title, 70) for e in sec.entries[:3]]
        if not quoted:
            top_terms += [n for n in names if n.lower() not in (t.lower() for t in top_terms)]
        shown = [f'"{n}"' for n in names] if quoted else names
        answers.append({"q": question, "a": f"{lead} {_join(shown)}. The full list is above, updated {_long_date(report.taken_at)}."})

    hours = _cadence(report)
    when_text = _long_date(report.taken_at)
    answers.append({
        "q": "How often is this page updated?",
        "a": (f"About every {hours} hours" if hours and hours > 1 else "Every hour" if hours == 1 else "On a schedule")
        + f". The last update was {when_text}. Each update compares the lists with the one before, so NEW marks what just appeared.",
    })
    paid = [PLATFORM_NAMES[p] for p in ("tiktok", "youtube", "x", "reddit") if p in present]
    free = [n for n in ("Google", "Hacker News") if n in platforms]
    origin = []
    if paid:
        origin.append(f"{_join(paid)} come{'s' if len(paid) == 1 else ''} from the Scavio API")
    if free:
        origin.append(f"{_join(free)} come{'s' if len(free) == 1 else ''} from {'its' if len(free) == 1 else 'their'} public feeds")
    answers.append({
        "q": "Where does the data come from?",
        "a": (_join(origin) + ". " if origin else "")
        + "Nothing is picked by hand. Items with profanity or adult content are left out of this page.",
    })
    answers.append({
        "q": "Can I get alerts for my own topics?",
        "a": "Yes. TrendScout, the open-source radar that builds this page, follows the topics you choose and sends "
        "what is new to Telegram, Discord, Slack, email, ntfy or a webhook. It is free and MIT licensed.",
    })

    d = datetime.fromisoformat(report.taken_at).astimezone(timezone.utc)
    parts = []
    if top_terms:
        parts.append(f"Trending now: {_join(top_terms[:3])}.")
    parts.append(f"Live lists from {_join(platforms) or 'the web'}, updated {d:%b} {d.day}, {d:%H:%M} UTC.")
    parts.append("Free and open source.")
    description = ""
    for part in parts:
        if len(description) + len(part) + 1 <= 158:
            description = f"{description} {part}".strip()
    description = description or _clip(parts[0], 158)

    url = site_url.rstrip("/") + "/" if site_url else None
    headings = {s.source: PAGE_HEADINGS.get(s.source, s.label) for s in sections}
    data = [{
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": title,
        "description": description,
        "inLanguage": "en",
        "dateModified": report.taken_at,
        **({"url": url} if url else {}),
        "mainEntity": [
            {
                "@type": "ItemList",
                "name": headings[s.source],
                "numberOfItems": len(s.entries),
                "itemListOrder": "https://schema.org/ItemListOrderAscending",
                "itemListElement": [
                    {"@type": "ListItem", "position": n, "name": _clip(e.item.title, 150), **({"url": e.item.url} if e.item.url else {})}
                    for n, e in enumerate(s.entries, 1)
                ],
            }
            for s in sections
        ],
    }, {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": a["q"], "acceptedAnswer": {"@type": "Answer", "text": a["a"]}} for a in answers],
    }]
    # Titles come from the open web: escape "<" so nothing can close the script tag.
    jsonld = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return {
        "title": title,
        "platforms": _join(platforms),
        "description": description,
        "url": url,
        "image": f"{url}og.png" if url else None,
        "updated": when_text,
        "headings": headings,
        "faq": answers,
        "jsonld": jsonld,
    }


def to_feed(report: Report, site_url: str | None, limit: int = 50) -> str:
    """RSS 2.0: what is new in this scan (everything on the first scan), newest lists first."""
    first = not report.previous_at
    items: list[tuple[Item, str]] = []
    for tr in report.topics:
        items += [(h.item, tr.topic.name) for h in tr.hits if h.new or first]
    for sec in report.sections:
        entries = sec.entries[:3] if first else [e for e in sec.entries if e.status == "new"]
        items += [(e.item, PAGE_HEADINGS.get(sec.source, sec.label)) for e in entries]
    seen: set[str] = set()
    when_ = format_datetime(datetime.fromisoformat(report.taken_at).astimezone(timezone.utc))
    home = site_url.rstrip("/") + "/" if site_url else REPO_URL
    out = []
    for item, category in items:
        guid = item.url or f"{item.source}:{normalize(item.title)}"
        if guid in seen:
            continue
        seen.add(guid)
        name = PLATFORM_NAMES.get(item.platform, item.platform)
        out.append(
            "<item>"
            f"<title>{escape(f'[{name}] ' + _clip(item.title, 200))}</title>"
            f"<link>{escape(item.url or home)}</link>"
            f'<guid isPermaLink="{"true" if item.url else "false"}">{escape(guid)}</guid>'
            f"<category>{escape(category)}</category>"
            f"<pubDate>{when_}</pubDate>"
            "</item>"
        )
        if len(out) >= limit:
            break
    self_link = f'<atom:link href="{escape(home)}feed.xml" rel="self" type="application/rss+xml"/>' if site_url else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
        "<title>What's trending today (TrendScout)</title>"
        f"<link>{escape(home)}</link>"
        "<description>New on the TikTok, Google, X, Reddit and Hacker News trending lists, from an open-source trend radar.</description>"
        f"<lastBuildDate>{when_}</lastBuildDate>{self_link}"
        + "".join(out)
        + "</channel></rss>\n"
    )


def to_sitemap(report: Report, site_url: str) -> str:
    url = site_url.rstrip("/") + "/"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{escape(url)}</loc><lastmod>{report.taken_at}</lastmod></url>"
        "</urlset>\n"
    )


def to_robots(site_url: str | None) -> str:
    out = "User-agent: *\nAllow: /\n"
    if site_url:
        out += f"Sitemap: {site_url.rstrip('/')}/sitemap.xml\n"
    return out


def to_json(report: Report) -> str:
    def item(i: Item) -> dict:
        return i.to_dict()

    data = {
        "schema": "trendscout.report/2",
        "taken_at": report.taken_at,
        "previous_at": report.previous_at,
        "summary": report.summary,
        "cross_platform": [
            {"title": s.title, "score": s.score, "status": s.status, "platforms": s.platforms, "items": [item(i) for i in s.items]}
            for s in report.cross
        ],
        "sections": [
            {"source": s.source, "label": s.label, "entries": [{"status": e.status, "change": e.change, **item(e.item)} for e in s.entries]}
            for s in report.sections
        ],
        "topics": [tr.to_dict() for tr in report.topics],
        "credits_used": report.credits_used,
        "credits_remaining": report.credits_remaining,
        "errors": report.errors,
        "skipped": report.skipped,
    }
    return json.dumps(data, ensure_ascii=False, indent=1)


def write_all(
    report: Report, reports_dir: Path, site_dir: Path, site_url: str | None, exclude: list[str] | tuple[str, ...] = ()
) -> dict[str, Path]:
    """Write reports/latest.{md,json}, a dated Markdown copy, and the public site.

    The site leaves out items with profanity or adult content (see safety.py);
    the reports keep everything.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(report.taken_at).strftime("%Y-%m-%d-%H%M")
    md = to_markdown(report, site_url)
    paths = {
        "markdown": reports_dir / "latest.md",
        "json": reports_dir / "latest.json",
        "dated": reports_dir / f"{stamp}.md",
    }
    paths["markdown"].write_text(md, encoding="utf-8")
    paths["dated"].write_text(md, encoding="utf-8")
    paths["json"].write_text(to_json(report), encoding="utf-8")

    public = safety.safe_report(report, exclude)
    archive_dir = site_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    # Archive pages are for people following a link, not for search engines.
    (archive_dir / f"{stamp}.html").write_text(to_html(public, indexable=False), encoding="utf-8")
    archive = sorted((p.stem for p in archive_dir.glob("*.html")), reverse=True)[:60]
    paths["html"] = site_dir / "index.html"
    paths["html"].write_text(to_html(public, archive, site_url), encoding="utf-8")
    (site_dir / "report.json").write_text(to_json(public), encoding="utf-8")
    (site_dir / "feed.xml").write_text(to_feed(public, site_url), encoding="utf-8")
    (site_dir / "robots.txt").write_text(to_robots(site_url), encoding="utf-8")
    if site_url:
        (site_dir / "sitemap.xml").write_text(to_sitemap(public, site_url), encoding="utf-8")
    with resources.as_file(resources.files("trendscout") / "assets" / "og.png") as og:
        if og.is_file():
            shutil.copyfile(og, site_dir / "og.png")
    return paths
