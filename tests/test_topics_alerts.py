"""Topics, the timeline, alerts, the schedule and the background service files."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from trendscout import alerts, background, scheduling, store, timeline
from trendscout.config import Alerts, Schedule, Topic
from trendscout.model import Item, Snapshot
from trendscout.rank import build_report
from trendscout.topics import matches

UTC = timezone.utc
T0 = "2026-11-12T08:00:00+00:00"
T1 = "2026-11-12T12:00:00+00:00"
NOW = datetime(2026, 11, 12, 12, 5, tzinfo=UTC)


def it(title, source="google_trending", platform="google", rank=1, keyword=None, url=None, **extra):
    return Item(source, platform, title, rank, url=url or f"https://e/{abs(hash(title))}", keyword=keyword, extra=extra)


# ---------------------------------------------------------------- matching


def test_match_rules_whole_words_must_and_exclude():
    t = Topic("AI", match=["ai", "chatgpt"], must=[], exclude=["crypto"])
    assert matches(t, it("ChatGPT gets a new voice"))
    assert matches(t, it("Is AI coming for your job?"))
    assert not matches(t, it("Said in an email"))  # 'ai' inside a word does not count
    assert not matches(t, it("AI crypto coin pumps"))  # excluded
    t2 = Topic("Apple", must=["iphone"])
    assert matches(t2, it("Apple iPhone 18 leak")) and not matches(t2, it("Apple pie recipe"))


def test_own_search_results_skip_the_phrase_but_keep_rules():
    t = Topic("creatine", exclude=["giveaway"])
    assert matches(t, it("Gym gains in 30 days", source="youtube_keyword", platform="youtube", keyword="creatine"))
    assert not matches(t, it("Huge giveaway", source="youtube_keyword", platform="youtube", keyword="creatine"))
    assert not matches(t, it("Gym gains", source="youtube_keyword", platform="youtube", keyword="other"))


def test_related_queries_count_and_junk_titles_do_not():
    assert matches(Topic("veterans"), it("texas roadhouse", related=["veterans day free meals"]))
    assert not matches(Topic("bk"), it("Video by @bk", source="tiktok_videos", platform="tiktok"))


# ---------------------------------------------------------------- timeline


def test_timeline_counts_and_rank_range(tmp_path):
    a0 = it("Jensen", source="x_trends", platform="x", rank=9, url="https://x/j")
    a1 = it("Jensen", source="x_trends", platform="x", rank=2, url="https://x/j")
    s0, s1 = Snapshot(T0, [a0]), Snapshot(T1, [a1])
    for s in (s0, s1):
        store.save(s, tmp_path)
        tl = timeline.record(tmp_path, s, 30)
    seen = tl[a1.key]
    assert (seen.first, seen.last, seen.count, seen.best, seen.worst) == (T0, T1, 2, 2, 9)
    # Rebuilt from the saved scans when the file is gone.
    timeline.path(tmp_path).unlink()
    assert timeline.current(tmp_path)[a1.key].count == 2


# ---------------------------------------------------------------- alerts


def _report(prev_items, cur_items, topics):
    prev, cur = Snapshot(T0, prev_items), Snapshot(T1, cur_items)
    tl: timeline.Timeline = {}
    timeline.update(tl, prev)
    timeline.update(tl, cur)
    return build_report(cur, prev, 10, topics, tl), tl


def test_new_mode_is_quiet_when_nothing_new():
    old = it("Matcha latte prices climb", source="news_keyword", platform="news", keyword="Matcha")
    r, tl = _report([old], [old], [Topic("Matcha")])
    assert alerts.build(r, Alerts(send="new"), [Topic("Matcha")], tl, NOW) is None


def test_new_mode_lists_only_new_items_topic_first():
    old = it("Matcha latte prices climb", source="news_keyword", platform="news", keyword="Matcha")
    fresh = it("Matcha shortage hits Kyoto", source="news_keyword", platform="news", keyword="Matcha", publisher="NHK")
    unrelated = it("lakers vs bucks", source="google_trending")
    r, tl = _report([old], [old, fresh, unrelated], [Topic("Matcha")])
    d = alerts.build(r, Alerts(send="new"), [Topic("Matcha")], tl, NOW)
    assert d.title == "1 new in Matcha"
    assert [ln.title for b in d.blocks for ln in b.lines] == ["Matcha shortage hits Kyoto"]
    assert "lakers" not in alerts.render(d, "plain", 5000)


def test_trending_detail_shows_rank_range_and_since():
    a0 = it("Matcha", source="tiktok_searches", platform="tiktok", rank=12, url="https://t/m")
    a1 = it("Matcha", source="tiktok_searches", platform="tiktok", rank=3, url="https://t/m")
    r, tl = _report([a0], [a1], [Topic("Matcha")])
    d = alerts.build(r, Alerts(send="every"), [Topic("Matcha")], tl, NOW)
    ln = d.blocks[0].lines[0]
    assert (ln.rank, ln.window, ln.times) == ("#3-#12", "08:00 ~ 12:00", 2)
    assert ln.meta == "[#3-#12] - [08:00 ~ 12:00] (seen 2 times)"
    assert "1. [TikTok] Matcha [#3-#12] - [08:00 ~ 12:00] (seen 2 times)" in alerts.render(d, "plain", 5000)


def test_breakouts_need_enough_platforms():
    items = [it("Jensen", source="x_trends", platform="x"), it("jensen huang", source="google_trending"),
             it("Jensen leaves Nvidia", source="hackernews", platform="hackernews")]
    r, tl = _report([], items, [])
    assert alerts.build(r, Alerts(send="every", breakout_platforms=3), [], tl, NOW).blocks[-1].heading.startswith("Trending on 3+")
    d = alerts.build(r, Alerts(send="every", breakout_platforms=4), [], tl, NOW)
    assert not any(b.heading.startswith("Trending on") for b in d.blocks)


def test_render_drops_whole_lines_and_says_how_many():
    lines = [alerts.Line("News", f"Headline number {n} " + "x" * 60, f"https://n/{n}", "NHK") for n in range(5)]
    d = alerts.Digest("5 new in Matcha", "Nov 12, 12:05", [alerts.Block("Matcha", "5 new", lines)])
    text = alerts.render(d, "discord", 500)
    assert len(text) <= 500 and "and " in text and "more" in text
    assert text.count("Headline number") < 5
    assert "](<https://n/0>)" in text  # links do not unfurl


# ---------------------------------------------------------------- schedule


def test_next_run_at_times_and_days():
    s = Schedule(at=["08:00", "18:00"], days=["mon", "tue", "wed", "thu", "fri"])
    thu_noon = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)  # a Thursday
    assert scheduling.next_run(s, thu_noon) == thu_noon.replace(hour=18)
    fri_night = datetime(2026, 11, 13, 19, 0, tzinfo=UTC)
    assert scheduling.next_run(s, fri_night) == datetime(2026, 11, 16, 8, 0, tzinfo=UTC)  # Monday
    assert scheduling.next_run(Schedule(enabled=False), thu_noon) is None


def test_next_run_interval_catches_up():
    s = Schedule(every=180, at=[])
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    assert scheduling.next_run(s, now, last=now - timedelta(hours=5)) == now  # overdue: now
    assert scheduling.next_run(s, now, last=now - timedelta(hours=1)) == now + timedelta(hours=2)
    assert scheduling.scans_per_day(s) == 8
    assert scheduling.describe(s) == "Every 3 hours, every day"
    assert scheduling.describe(Schedule(at=["09:00", "13:00", "17:00"], days=["mon", "tue", "wed", "thu", "fri"])) == "09:00, 13:00 and 17:00, weekdays"


# ---------------------------------------------------------------- background


def test_service_files_point_at_this_config(tmp_path):
    cfg = tmp_path / "my radar" / "config.yaml"
    plist = background.launchd_plist(cfg, 8765)
    assert "<string>dev.scavio.trendscout</string>" in plist and f"<string>{cfg}</string>" in plist
    assert "<string>--no-browser</string>" in plist and "ThrottleInterval" in plist
    unit = background.systemd_unit(cfg, 8765)
    assert f'"{cfg}"' in unit and f"WorkingDirectory={cfg.parent}" in unit
    assert background._configured_path(plist) == str(cfg)


def test_status_without_install(monkeypatch, tmp_path):
    monkeypatch.setattr(background, "plist_path", lambda: Path(tmp_path / "none.plist"))
    monkeypatch.setattr(background, "unit_path", lambda: Path(tmp_path / "none.service"))
    st = background.status()
    assert not st.installed and not st.running


# ---------------------------------------------------------------- per-topic channels and alert times


def test_topic_notify_and_schedule_round_trip():
    import yaml

    from trendscout.config import dump, from_dict

    c = from_dict({"topics": [{"name": "A", "notify": ["slack"], "schedule": {"at": ["09:00"], "days": ["mon"]}}, {"name": "B", "notify": []}, "C"]}, {})
    assert c.topics[0].notify == ["slack"] and c.topics[0].schedule.at == ["09:00"]
    assert c.topics[1].notify == [] and c.topics[2].notify is None and c.topics[2].schedule is None
    assert from_dict(yaml.safe_load(dump(c)), {}).topics == c.topics


def test_due_and_combined_scan_times():
    from trendscout.config import from_dict

    c = from_dict({"schedule": {"at": ["18:00"]}, "topics": ["Shared", {"name": "Early", "schedule": {"at": ["09:00"]}}]}, {})
    prev = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
    assert scheduling.next_scan(c, prev, prev) == datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    names, shared = scheduling.due(c, prev, datetime(2026, 11, 12, 9, 0, tzinfo=UTC))
    assert names == {"Early"} and shared is False
    names, shared = scheduling.due(c, datetime(2026, 11, 12, 9, 0, tzinfo=UTC), datetime(2026, 11, 12, 18, 0, tzinfo=UTC))
    assert names == {"Shared"} and shared is True


def test_alert_covers_everything_since_the_topics_last_alert():
    # Seen at the 08:00 scan, after the 07:00 alert, so the 09:00 alert must still list it.
    early = it("Matcha prices jump", source="news_keyword", platform="news", keyword="Matcha", url="https://n/1")
    later = it("Matcha festival opens", source="news_keyword", platform="news", keyword="Matcha", url="https://n/2")
    s8, s9 = Snapshot("2026-11-12T08:00:00+00:00", [early]), Snapshot("2026-11-12T09:00:00+00:00", [early, later])
    tl: timeline.Timeline = {}
    timeline.update(tl, s8)
    timeline.update(tl, s9)
    r = build_report(s9, s8, 10, [Topic("Matcha")], tl)
    plain = alerts.build(r, Alerts(send="new"), [Topic("Matcha")], tl, NOW)
    assert [ln.title for ln in plain.blocks[0].lines] == ["Matcha festival opens"]  # new this scan only
    since = alerts.build(r, Alerts(send="new"), [Topic("Matcha")], tl, NOW, since={"Matcha": "2026-11-12T07:00:00+00:00"})
    assert {ln.title for ln in since.blocks[0].lines} == {"Matcha prices jump", "Matcha festival opens"}


def test_each_channel_gets_only_its_topics(tmp_path, monkeypatch):
    from trendscout import service
    from trendscout.config import from_dict

    c = from_dict({"topics": [{"name": "Matcha", "notify": ["slack"]}, {"name": "Creatine", "notify": ["discord"]}, {"name": "Quiet", "notify": []}]},
                  {"SLACK_WEBHOOK_URL": "s", "DISCORD_WEBHOOK_URL": "d"})
    c.data_dir = tmp_path
    items = [it("Matcha latte", source="news_keyword", platform="news", keyword="Matcha", url="https://n/m"),
             it("Creatine study", source="news_keyword", platform="news", keyword="Creatine", url="https://n/c"),
             it("Quiet thing", source="news_keyword", platform="news", keyword="Quiet", url="https://n/q")]
    snap = Snapshot(T1, items)
    tl: timeline.Timeline = {}
    timeline.update(tl, snap)
    r = build_report(snap, None, 10, c.topics, tl)
    got = {}
    monkeypatch.setattr(service, "send_one", lambda ch, digest, report, env, http: got.setdefault(ch, [b.heading for b in digest.blocks]) and "ok")
    sent, _ = service._send_alerts(c, r, tl, None, snap, None, None, lambda m: None, None)
    assert got == {"slack": ["Matcha"], "discord": ["Creatine"]}
    state = service._alert_state(tmp_path)
    assert set(state) == {"Matcha", "Creatine", "Quiet"}
