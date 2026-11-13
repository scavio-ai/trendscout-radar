"""The public page: safe to share, built to be found, archive kept out of search."""

import json
import re
import xml.etree.ElementTree as ET

import yaml

from trendscout import cli, config as C, render, safety
from trendscout.config import Topic
from trendscout.model import Item, Snapshot
from trendscout.rank import build_report

SITE = "https://trends.example.org/"


def items():
    return [
        Item("tiktok_searches", "tiktok", "grwm skincare", 1),
        Item("tiktok_searches", "tiktok", "fucking hilarious prank", 2),
        Item("tiktok_searches", "tiktok", "class reunion ideas", 3),
        Item("google_trending", "google", "creatine gummies", 1, metrics={"searches": 20000}),
        Item("google_trending", "google", "essex weather", 2),
        Item("x_trends", "x", "Coachella", 1, url="https://x.com/search?q=Coachella"),
        Item("reddit_popular", "reddit", "Look at this view", 1, url="https://reddit.com/r/a/1", extra={"subreddit": "pics", "nsfw": True}),
        Item("reddit_popular", "reddit", "Creatine changed my life", 2, url="https://reddit.com/r/b/2", extra={"subreddit": "fitness"}),
        Item("hackernews", "hackernews", "Show HN: </script><b>x</b>", 1, url="https://news.ycombinator.com/item?id=1", metrics={"points": 120}),
    ]


def report(prev=True):
    before = Snapshot("2026-11-12T06:00:00+00:00", items=items()[:2]) if prev else None
    return build_report(Snapshot("2026-11-12T09:00:00+00:00", items=items()), before, 10, [Topic("Creatine"), Topic("Skincare")])


def test_blocklist_whole_words_plurals_and_stars():
    rx = safety.pattern()
    assert rx.search("best porn sites") and rx.search("NUDES leaked") and rx.search("what the f**k")
    for clean in ("class reunion", "essex weather", "sextant", "cocktail recipes", "scunthorpe united", "assassin"):
        assert not rx.search(clean), clean
    assert safety.pattern(["kardashian"]).search("Kim Kardashian news")


def test_safe_report_drops_blocked_and_nsfw_items():
    r = safety.safe_report(report())
    titles = [e.item.title for s in r.sections for e in s.entries]
    assert "fucking hilarious prank" not in titles and "Look at this view" not in titles
    assert {"grwm skincare", "class reunion ideas", "essex weather", "Creatine changed my life"} <= set(titles)
    # the original report is untouched
    assert "fucking hilarious prank" in [e.item.title for s in report().sections for e in s.entries]


def test_page_meta_titles_answers_and_jsonld():
    page = render.page_meta(safety.safe_report(report()), SITE)
    assert page["title"] == "What's Trending Today on TikTok, Google, X, Reddit and Hacker News"
    assert len(page["description"]) <= 158 and page["description"].startswith("Trending now: class reunion ideas, grwm skincare")
    assert "Live lists from TikTok" in page["description"]
    assert page["url"] == SITE and page["image"] == SITE + "og.png"
    questions = [a["q"] for a in page["faq"]]
    assert questions[0] == "What is trending on TikTok today?"
    assert "Can I get alerts for my own topics?" in questions
    assert "About every 3 hours" in next(a["a"] for a in page["faq"] if a["q"].startswith("How often"))
    assert "TikTok, X and Reddit come from the Scavio API" in next(a["a"] for a in page["faq"] if a["q"].startswith("Where"))
    assert "<" not in page["jsonld"]
    data = json.loads(page["jsonld"])
    assert [d["@type"] for d in data] == ["WebPage", "FAQPage"]
    assert data[0]["url"] == SITE and data[0]["mainEntity"][0]["@type"] == "ItemList"


def test_write_all_builds_an_indexable_page_and_a_hidden_archive(tmp_path):
    r = report()
    render.write_all(r, tmp_path / "reports", tmp_path / "site", SITE, ["coachella"])
    site = tmp_path / "site"
    html = (site / "index.html").read_text()
    assert f'<link rel="canonical" href="{SITE}">' in html
    assert 'property="og:image" content="https://trends.example.org/og.png"' in html
    assert "<h1>What's trending today</h1>" in html and 'id="tiktok-searches"' in html
    assert "fucking" not in html and "Look at this view" not in html and "Coachella" not in html
    assert "<b>x</b>" not in html
    jsonld = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
    json.loads(jsonld)

    archive = next((site / "archive").glob("*.html")).read_text()
    assert 'content="noindex, follow"' in archive and "ld+json" not in archive and "canonical" not in archive

    assert (site / "robots.txt").read_text().endswith(f"Sitemap: {SITE}sitemap.xml\n")
    loc = ET.fromstring((site / "sitemap.xml").read_text()).find("{http://www.sitemaps.org/schemas/sitemap/0.9}url/{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
    assert loc.text == SITE
    feed = ET.fromstring((site / "feed.xml").read_text())
    feed_titles = [i.findtext("title") for i in feed.iter("item")]
    assert feed_titles and not any("fucking" in t for t in feed_titles)
    assert (site / "og.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    public = json.loads((site / "report.json").read_text())
    assert "fucking" not in json.dumps(public)
    # the local report keeps everything
    assert "fucking hilarious prank" in (tmp_path / "reports" / "latest.json").read_text()


def test_without_a_site_url_there_is_no_canonical_or_sitemap(tmp_path):
    render.write_all(report(prev=False), tmp_path / "reports", tmp_path / "site", None)
    html = (tmp_path / "site" / "index.html").read_text()
    assert "canonical" not in html and "og:image" not in html
    assert not (tmp_path / "site" / "sitemap.xml").exists()
    assert "Sitemap" not in (tmp_path / "site" / "robots.txt").read_text()


def test_site_exclude_reads_and_writes_back():
    c = C.from_dict({"site_exclude": ["Kardashian", "kardashian", " spoiler "]}, {})
    assert c.site_exclude == ["Kardashian", "spoiler"]
    assert C.from_dict(yaml.safe_load(C.dump(c)), {}).site_exclude == ["Kardashian", "spoiler"]


def test_report_command_keeps_topics(tmp_path, monkeypatch):
    from trendscout import store

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("topics: [Creatine]\n")
    cfg = C.load(tmp_path / "config.yaml", env={})
    store.save(Snapshot("2026-11-12T09:00:00+00:00", items=items()), cfg.data_dir)
    assert cli.main(["report", "-c", str(tmp_path / "config.yaml")]) == 0
    saved = json.loads((tmp_path / "reports" / "latest.json").read_text())
    assert [t["name"] for t in saved["topics"]] == ["Creatine"]
    assert saved["topics"][0]["total"] >= 2
