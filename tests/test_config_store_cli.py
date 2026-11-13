from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from conftest import free_handler, scavio_handler

from trendscout import cli, service, store
from trendscout.client import ScavioClient
from trendscout.config import ConfigError, from_dict, load
from trendscout.model import Item, Snapshot

ROOT = Path(__file__).parent.parent


def test_root_config_matches_packaged_example():
    assert (ROOT / "config.yaml").read_text() == (ROOT / "trendscout" / "config.example.yaml").read_text()
    c = load(ROOT / "config.yaml", env={})
    assert c.topics and c.scavio_api_key is None
    assert c.x_country == "UnitedStates"


@pytest.mark.parametrize(
    "raw",
    [
        {"keywords": ["ai"]},
        {"topics": "ai"},
        {"topics": [""]},
        {"topics": ["a", "A"]},
        {"topics": [{"name": "x", "search": "yes"}]},
        {"topics": [f"k{i}" for i in range(26)]},
        {"alerts": {"send": "sometimes"}},
        {"schedule": {"at": ["25:00"]}},
        {"schedule": {"every": "5m"}},
        {"schedule": {"days": ["someday"]}},
        {"sources": {"myspace": True}},
        {"sources": {"x_trends": "yes"}},
        {"country": "USA"},
        {"top_n": 0},
        {"max_credits_per_run": True},
    ],
)
def test_invalid_config(raw):
    with pytest.raises(ConfigError):
        from_dict(raw, {})


def test_secrets_come_from_env():
    c = from_dict({}, {"SCAVIO_API_KEY": " sk_1 ", "LLM_API_KEY": "", "LLM_MODEL": "gpt-5.5"})
    assert c.scavio_api_key == "sk_1"
    assert c.llm_api_key is None and c.llm_model == "gpt-5.5"


def test_store_previous_and_prune(tmp_path):
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for d in (0, 20, 40):
        store.save(Snapshot((t0 + timedelta(days=d)).isoformat(), items=[Item("x_trends", "x", f"d{d}", 1)]), tmp_path)
    prev = store.previous(tmp_path, (t0 + timedelta(days=40)).isoformat())
    assert prev.items[0].title == "d20"
    assert store.latest(tmp_path).items[0].title == "d40"
    assert store.prune(tmp_path, 30, t0 + timedelta(days=41)) == 1
    assert len(store.all_paths(tmp_path)) == 2


def _patch_network(monkeypatch):
    real_init = ScavioClient.__init__

    def init(self, api_key, base_url="https://api.scavio.dev", **kw):
        real_init(self, api_key, base_url, transport=httpx.MockTransport(scavio_handler()), sleep=lambda s: None)

    monkeypatch.setattr(ScavioClient, "__init__", init)
    monkeypatch.setattr(service, "make_http", lambda: httpx.Client(transport=httpx.MockTransport(free_handler())))


def test_run_twice_end_to_end(tmp_path, monkeypatch, capsys):
    _patch_network(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("topics: [ai agents]\nhackernews_limit: 2\n")
    monkeypatch.setenv("SCAVIO_API_KEY", "k")
    for var in ("TELEGRAM_BOT_TOKEN", "DISCORD_WEBHOOK_URL", "SLACK_WEBHOOK_URL", "NTFY_TOPIC", "WEBHOOK_URL", "SMTP_HOST", "LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    assert cli.main(["run", "--no-notify"]) == 0
    first = (tmp_path / "reports" / "latest.md").read_text()
    assert "First run" in first

    # Second run, a moment later: compares against the first snapshot.
    snaps = store.all_paths(tmp_path / "data")
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    monkeypatch.setattr(service, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: later), "strptime": datetime.strptime}))
    assert cli.main(["run", "--no-notify"]) == 0
    assert len(store.all_paths(tmp_path / "data")) == len(snaps) + 1
    second = (tmp_path / "reports" / "latest.md").read_text()
    assert "Compared with the run at" in second
    assert (tmp_path / "site" / "index.html").exists()


def test_check_without_key(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCAVIO_API_KEY", raising=False)
    (tmp_path / "config.yaml").write_text("topics: [ai agents]\n")
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "free sources only" in out and "(free)" in out


def test_bad_config_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("country: USA\n")
    assert cli.main(["check"]) == 2
    assert "Config error" in capsys.readouterr().out


def test_state_dir_moves_outputs(tmp_path, monkeypatch):
    _patch_network(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("topics: []\nhackernews_limit: 1\n")
    monkeypatch.setenv("SCAVIO_API_KEY", "k")
    assert cli.main(["run", "--no-notify", "--state-dir", "state"]) == 0
    assert (tmp_path / "state" / "reports" / "latest.md").exists()
    assert (tmp_path / "state" / "data" / "snapshots").exists()
    assert not (tmp_path / "reports").exists()


def test_dotenv_is_read_but_env_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("topics: []\n")
    (tmp_path / ".env").write_text("# comment\nSCAVIO_API_KEY='from-file'\nexport LLM_MODEL=gpt-5.5\n")
    monkeypatch.delenv("SCAVIO_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    c = load("config.yaml")
    assert c.scavio_api_key == "from-file" and c.llm_model == "gpt-5.5"
    monkeypatch.setenv("SCAVIO_API_KEY", "from-env")
    assert load("config.yaml").scavio_api_key == "from-env"


def test_topic_search_forms():
    c = from_dict({"topics": [{"name": "a", "search": True}, {"name": "b", "search": "reddit"}, {"name": "c", "search": ["tiktok", "youtube"]}]}, {})
    assert [t.search for t in c.topics] == [["youtube", "tiktok", "reddit"], ["reddit"], ["youtube", "tiktok"]]
    with pytest.raises(ConfigError):
        from_dict({"topics": [{"name": "a", "search": ["myspace"]}]}, {})
    with pytest.raises(ConfigError):
        from_dict({"topics": [{"name": "a", "news": "no"}]}, {})


def test_written_config_reads_back_with_every_source():
    import yaml

    from trendscout.config import ALL_SOURCES, dump

    c = from_dict({"sources": {s: False for s in ALL_SOURCES}}, {})
    again = from_dict(yaml.safe_load(dump(c)), {})
    assert again.sources == c.sources and set(again.sources) == set(ALL_SOURCES)
