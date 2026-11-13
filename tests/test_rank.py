import copy

from trendscout.model import Item, Snapshot
from trendscout.rank import build_report, cross_platform, is_specific, match_term


def item(source, platform, title, rank, **kw):
    return Item(source=source, platform=platform, title=title, rank=rank, **kw)


def snap(items, at="2026-11-11T12:00:00+00:00"):
    return Snapshot(taken_at=at, items=items)


def test_match_term_and_specificity():
    assert match_term("GTA 5 Goodbye Trend") == "gta 5 goodbye"
    assert match_term("Dances for 2 Trend ideas") == "dances for 2"
    assert not is_specific("love")
    assert not is_specific("dance trend")
    assert is_specific("coachella")
    assert is_specific("rex ryan")


def test_cross_platform_needs_two_platforms():
    s = snap([
        item("x_trends", "x", "Coachella", 5),
        item("google_trending", "google", "coachella 2027 lineup", 7),
        item("x_trends", "x", "Merab", 2),  # only on X
        item("hackernews", "hackernews", "Coachella announces lineup", 3),
    ])
    sig = cross_platform(s, None, 10)
    assert [x.title for x in sig] == ["Coachella"]
    assert sig[0].platforms == ["google", "hackernews", "x"]
    assert sig[0].status == "first"


def test_cross_platform_does_not_match_inside_words():
    s = snap([
        item("x_trends", "x", "Umar", 1),
        item("google_trending", "google", "umbrella humarket", 1),
    ])
    assert cross_platform(s, None, 10) == []


def test_first_run_is_a_baseline():
    s = snap([item("x_trends", "x", f"Topic {n}", n) for n in range(1, 6)])
    r = build_report(s, None, 10)
    assert r.previous_at is None
    assert {e.status for e in r.sections[0].entries} == {"first"}
    assert r.new_count == 0


def test_new_and_climbing_against_previous_run():
    prev = snap([item("x_trends", "x", t, n) for n, t in enumerate(["A1", "B2", "C3", "D4", "E5", "F6"], 1)])
    cur_titles = ["F6", "A1", "New Thing", "B2", "C3", "D4"]
    cur = snap([item("x_trends", "x", t, n) for n, t in enumerate(cur_titles, 1)], at="2026-11-11T18:00:00+00:00")
    r = build_report(cur, prev, 10)
    entries = {e.item.title: e for e in r.sections[0].entries}
    assert entries["New Thing"].status == "new"
    assert entries["F6"].status == "up" and entries["F6"].change == 5
    assert entries["A1"].status == "steady"
    # new first, then climbers
    assert [e.item.title for e in r.sections[0].entries][:2] == ["New Thing", "F6"]
    assert r.new_count == 1


def test_topic_hits_sort_by_views_and_flag_new():
    from trendscout.config import Topic
    from trendscout.timeline import update

    a = item("youtube_keyword", "youtube", "Low", 1, keyword="k", metrics={"views": 10}, url="https://y/1")
    b = item("youtube_keyword", "youtube", "High", 2, keyword="k", metrics={"views": 500}, url="https://y/2")
    prev = snap([copy.deepcopy(a)])
    cur = snap([a, b], at="2026-11-11T18:00:00+00:00")
    tl = {}
    update(tl, prev)
    update(tl, cur)
    r = build_report(cur, prev, 10, [Topic("k")], tl)
    hits = r.topics[0].hits
    assert [h.item.title for h in hits] == ["High", "Low"]  # the new one first, then by views
    assert [h.new for h in hits] == [True, False]


def test_sampled_sources_are_never_badged():
    prev = snap([item("tiktok_videos", "tiktok", "Old", 1, url="https://t/1")])
    cur = snap([item("tiktok_videos", "tiktok", "Fresh sample", 1, url="https://t/2")], at="2026-11-11T18:00:00+00:00")
    r = build_report(cur, prev, 10)
    assert [e.status for e in r.sections[0].entries] == ["steady"]
    assert r.new_count == 0


def test_identity_survives_title_cleanup():
    prev = snap([item("tiktok_searches", "tiktok", "Me reheating nachos #bts", 1, url="https://t/v/1")])
    cur = snap([item("tiktok_searches", "tiktok", "Me reheating nachos", 1, url="https://t/v/1")], at="2026-11-11T18:00:00+00:00")
    assert build_report(cur, prev, 10).sections[0].entries[0].status == "steady"
