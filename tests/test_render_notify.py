import json

import httpx

from datetime import datetime, timezone

from trendscout import alerts, notify, render
from trendscout.config import Alerts, Topic
from trendscout.model import Item, Snapshot
from trendscout.rank import build_report

SECRET_TOKEN = "123456:SECRET-TOKEN"


def report(with_prev=False):
    items = [
        Item("x_trends", "x", "Coachella", 1, url="https://x.com/search?q=Coachella"),
        Item("google_trending", "google", "coachella 2027 lineup", 1, metrics={"searches": 100000, "growth_pct": 1000}),
        Item("tiktok_videos", "tiktok", "<script>alert(1)</script> dance", 1, url="https://www.tiktok.com/@a/video/1", metrics={"views": 2_000_000}),
        Item("youtube_keyword", "youtube", "Agents explained", 1, keyword="ai agents", metrics={"views": 5000}, extra={"channel": "Chan"}),
    ]
    prev = Snapshot("2026-11-11T06:00:00+00:00", items=[]) if with_prev else None
    topics = [Topic("ai agents"), Topic("Coachella")]
    return build_report(Snapshot("2026-11-11T12:00:00+00:00", items=items, credits_used=7, credits_remaining=43), prev, 10, topics)


NOW = datetime(2026, 11, 11, 13, 0, tzinfo=timezone.utc)


def digest(r=None):
    r = r or report()
    return alerts.build(r, Alerts(), [Topic("ai agents"), Topic("Coachella")], None, NOW, mode="every", site_url="https://site/")


def test_human_numbers():
    assert render.human(2_000_000) == "2M"
    assert render.human(1_250) == "1.2K"
    assert render.human(950) == "950"


def test_markdown_sections():
    md = render.to_markdown(report(), site_url="https://me.github.io/trendscout/")
    assert "## Rising on more than one platform" in md and "**Coachella**" in md
    assert "## ai agents (1 matching)" in md and md.index("## ai agents") < md.index("## Rising")
    assert "2M views" in md
    assert "Scavio credits used this run: 7 (43 left)." in md
    assert "Full report: https://me.github.io/trendscout/" in md


def test_html_escapes_titles():
    html = render.to_html(report())
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_alert_formats_respect_length_limits():
    d = digest()
    for fmt in ("plain", "discord", "telegram", "slack"):
        for limit in (300, 1900, 4000):
            assert len(alerts.render(d, fmt, limit)) <= limit


def test_json_report_schema():
    data = json.loads(render.to_json(report()))
    assert data["schema"] == "trendscout.report/2"
    assert data["cross_platform"][0]["title"] == "Coachella"
    assert [tp["name"] for tp in data["topics"]] == ["ai agents", "Coachella"]
    assert data["topics"][0]["hits"][0]["title"] == "Agents explained"


def test_write_all(tmp_path):
    paths = render.write_all(report(), tmp_path / "reports", tmp_path / "site", None)
    for p in paths.values():
        assert p.exists() and p.stat().st_size > 0
    assert (tmp_path / "site" / "report.json").exists()


def test_enabled_channels():
    env = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "SLACK_WEBHOOK_URL": " ", "NTFY_TOPIC": "radar"}
    assert notify.enabled_channels(env) == ["telegram", "ntfy"]
    assert "email" in notify.enabled_channels({k: "x" for k in notify.EMAIL_VARS})


def test_send_all_isolates_failures_and_hides_secrets():
    sent = []

    def h(req):
        sent.append(str(req.url))
        if "telegram" in req.url.host:
            return httpx.Response(401, json={"ok": False})
        return httpx.Response(204)

    env = {
        "TELEGRAM_BOT_TOKEN": SECRET_TOKEN,
        "TELEGRAM_CHAT_ID": "42",
        "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/abc",
        "NTFY_TOPIC": "radar",
        **{k: "x" for k in notify.EMAIL_VARS},
        "EMAIL_TO": "a@example.com, b@example.com",
    }
    mails = []
    with httpx.Client(transport=httpx.MockTransport(h)) as http:
        results = notify.send_all(digest(), report(), env, http, smtp_send=lambda msg, e: mails.append(msg))
    assert results["discord"] == "ok" and results["ntfy"] == "ok" and results["email"] == "ok"
    assert results["telegram"].startswith("HTTPStatusError")
    assert SECRET_TOKEN not in results["telegram"]
    assert mails[0]["To"] == "a@example.com, b@example.com"
    assert len(sent) == 3


def test_payloads():
    r, d = report(), digest()
    tg = notify.telegram(d, r, {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "9"})
    assert tg.json["chat_id"] == "9" and tg.json["parse_mode"] == "HTML" and len(tg.json["text"]) <= 4000
    assert "&lt;script&gt;" in tg.json["text"] or "script" not in tg.json["text"]
    dc = notify.discord(d, r, {"DISCORD_WEBHOOK_URL": "u"}).json
    assert len(dc["content"]) <= 1950 and dc["allowed_mentions"] == {"parse": []}
    n = notify.ntfy(d, r, {"NTFY_TOPIC": "t"})
    assert n.url == "https://ntfy.sh/t" and n.headers["Click"] == "https://site/"
    wh = json.loads(notify.webhook(d, r, {"WEBHOOK_URL": "u"}).content)
    assert wh["schema"] == "trendscout.alert/1" and wh["alert"]["title"] and wh["report"]["topics"]


def test_ntfy_title_survives_non_ascii():
    d = digest()
    d.title = "Neu: Matcha-Trends"
    assert notify.ntfy(d, report(), {"NTFY_TOPIC": "t"}).headers["Title"].isascii()
    d.title = "Café"
    assert notify.ntfy(d, report(), {"NTFY_TOPIC": "t"}).headers["Title"].startswith("=?UTF-8?B?")
