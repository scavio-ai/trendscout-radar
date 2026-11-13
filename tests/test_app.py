"""The local web app: security checks, config and .env round trips, jobs, history."""

from __future__ import annotations

import os
import stat
import threading
import time

import httpx
import pytest
import yaml

from conftest import free_handler, scavio_handler

from trendscout import app as app_mod
from trendscout import service
from trendscout.client import ScavioClient
from trendscout.config import ConfigError, dump, from_dict, load, read_dotenv, write_dotenv

TOKEN = "test-token-123"
CHANNEL_VARS = ("TELEGRAM_BOT_TOKEN", "DISCORD_WEBHOOK_URL", "SLACK_WEBHOOK_URL", "NTFY_TOPIC", "WEBHOOK_URL", "SMTP_HOST", "LLM_API_KEY", "LLM_MODEL")


def _patch_network(monkeypatch):
    real_init = ScavioClient.__init__

    def init(self, api_key, base_url="https://api.scavio.dev", **kw):
        real_init(self, api_key, base_url, transport=httpx.MockTransport(scavio_handler()), sleep=lambda s: None)

    monkeypatch.setattr(ScavioClient, "__init__", init)
    monkeypatch.setattr(service, "make_http", lambda: httpx.Client(transport=httpx.MockTransport(free_handler())))


@pytest.fixture
def ui(tmp_path, monkeypatch):
    """A running server on a free port, and an httpx client that carries the token."""
    for var in ("SCAVIO_API_KEY", *CHANNEL_VARS):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("topics: [ai agents]\nhackernews_limit: 2\n")
    server = app_mod.make_server(tmp_path / "config.yaml", None, "127.0.0.1", 0, TOKEN)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    with httpx.Client(base_url=base, headers={app_mod.TOKEN_HEADER: TOKEN}, timeout=10) as client:
        client.tmp = tmp_path  # type: ignore[attr-defined]
        yield client
    server.shutdown()
    server.server_close()


def _wait(ui) -> dict:
    for _ in range(200):
        job = ui.get("/api/job").json()
        if job and job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# ---------------------------------------------------------------- security


def test_static_needs_no_token_and_sends_csp(ui):
    resp = httpx.get(str(ui.base_url) + "/")
    assert resp.status_code == 200 and "<html" in resp.text.lower()
    assert "default-src 'self'" in resp.headers["content-security-policy"]
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert httpx.get(str(ui.base_url) + "/../config.yaml").status_code == 404
    for asset in ("/app.js", "/app.css", "/favicon.svg"):
        assert httpx.get(str(ui.base_url) + asset).status_code == 200


def test_api_requires_token(ui):
    assert httpx.get(str(ui.base_url) + "/api/state").status_code == 401
    assert httpx.get(str(ui.base_url) + "/api/state", headers={app_mod.TOKEN_HEADER: "nope"}).status_code == 401
    assert ui.get("/api/state").status_code == 200


def test_foreign_host_is_rejected(ui):
    # DNS rebinding: the browser sends the attacker's host name.
    resp = ui.get("/api/state", headers={"Host": "evil.example:8765"})
    assert resp.status_code == 403
    assert ui.get("/api/state", headers={"Host": "localhost:8765"}).status_code == 200


def test_no_cors_headers(ui):
    resp = ui.get("/api/state", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers


# ---------------------------------------------------------------- config


def test_config_get_and_save_keeps_comments(ui):
    view = ui.get("/api/config").json()
    assert [tp["name"] for tp in view["config"]["topics"]] == ["ai agents"]
    assert {s["name"] for s in view["sources"]} >= {"tiktok_searches", "youtube_keyword"}
    assert view["plan"]["has_key"] is False

    cfg = view["config"] | {"topics": [{"name": "creatine"}, {"name": "k: with # odd 'chars'", "exclude": ["#ad"]}], "top_n": 5}
    cfg["sources"]["x_trends"] = False
    saved = ui.put("/api/config", json={"config": cfg})
    assert saved.status_code == 200, saved.text
    text = (ui.tmp / "config.yaml").read_text()
    assert "# Safety limit" in text
    raw = yaml.safe_load(text)
    assert raw["topics"] == ["creatine", {"name": "k: with # odd 'chars'", "exclude": ["#ad"]}] and raw["top_n"] == 5
    assert raw["sources"]["x_trends"] is False


def test_invalid_config_is_rejected_and_file_untouched(ui):
    before = (ui.tmp / "config.yaml").read_text()
    resp = ui.put("/api/config", json={"config": {"country": "USA"}})
    assert resp.status_code == 400
    assert resp.json()["field"] == "country"
    assert (ui.tmp / "config.yaml").read_text() == before


def test_estimate_uses_unsaved_form(ui, monkeypatch):
    monkeypatch.setenv("SCAVIO_API_KEY", "k")
    view = ui.get("/api/config").json()
    one = ui.post("/api/estimate", json={"config": view["config"]}).json()["per_run"]
    free = ui.post("/api/estimate", json={"config": view["config"] | {"topics": ["a", "b"]}}).json()["per_run"]
    searched = ui.post("/api/estimate", json={"config": view["config"] | {"topics": ["a", {"name": "b", "search": True}]}}).json()["per_run"]
    assert free == one and searched == one + 4


def test_init_creates_config(ui):
    (ui.tmp / "config.yaml").unlink()
    assert ui.get("/api/state").json()["has_config"] is False
    assert ui.post("/api/init").status_code == 200
    assert load(ui.tmp / "config.yaml", env={}).topics
    assert ui.post("/api/init").status_code == 409


# ---------------------------------------------------------------- secrets


def test_secrets_never_sent_back(ui):
    secret = "sk_live_abcdefghijklmnop"
    resp = ui.put("/api/secrets", json={"values": {"SCAVIO_API_KEY": secret, "TELEGRAM_CHAT_ID": "-100123"}})
    assert resp.status_code == 200
    assert secret not in resp.text
    groups = {g["id"]: g for g in resp.json()["groups"]}
    key = groups["scavio"]["vars"][0]
    assert key["set"] and key["source"] == ".env" and key["hint"] == "...mnop"
    chat = groups["telegram"]["vars"][1]
    assert chat["hint"] == "-100123"  # not a secret, shown in full
    env_file = ui.tmp / ".env"
    assert read_dotenv(env_file)["SCAVIO_API_KEY"] == secret
    assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o600
    # The new key is picked up without a restart.
    assert ui.get("/api/state").json()["has_key"] is True
    # Clearing removes it.
    ui.put("/api/secrets", json={"values": {"SCAVIO_API_KEY": ""}})
    assert "SCAVIO_API_KEY" not in read_dotenv(env_file)


def test_secrets_reject_unknown_and_environment_owned(ui, monkeypatch):
    assert ui.put("/api/secrets", json={"values": {"PATH": "/tmp"}}).status_code == 400
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/x")
    resp = ui.put("/api/secrets", json={"values": {"SLACK_WEBHOOK_URL": "y"}})
    assert resp.status_code == 400 and "environment" in resp.json()["error"]
    groups = {g["id"]: g for g in ui.get("/api/secrets").json()["groups"]}
    assert groups["slack"]["vars"][0]["source"] == "environment" and groups["slack"]["enabled"]


def test_secret_value_with_newline_rejected(ui):
    assert ui.put("/api/secrets", json={"values": {"WEBHOOK_URL": "a\nEVIL=1"}}).status_code == 400


# ---------------------------------------------------------------- jobs


def test_run_job_and_history(ui, monkeypatch):
    _patch_network(monkeypatch)
    ui.put("/api/secrets", json={"values": {"SCAVIO_API_KEY": "sk_test_key_123456"}})
    started = ui.post("/api/run", json={"notify": False})
    assert started.status_code == 202
    job = _wait(ui)
    assert job["status"] == "done", job
    assert job["result"]["ok"] and job["result"]["items"] > 0
    assert any("Credits used" in line for line in job["log"])

    snaps = ui.get("/api/snapshots").json()
    assert len(snaps) == 1 and snaps[0]["items"] == job["result"]["items"]
    report = ui.get("/api/report", params={"items": "1", "markdown": "1"}).json()
    assert report["id"] == snaps[0]["id"]
    assert report["sections"] and report["items"] and report["markdown"].startswith("#")

    assert ui.get("/api/report", params={"id": "../../etc/passwd"}).status_code == 400
    assert ui.request("DELETE", "/api/snapshots", params={"id": snaps[0]["id"]}).json() == []
    assert ui.get("/api/report").status_code == 404


def test_try_free_source(ui, monkeypatch):
    _patch_network(monkeypatch)
    assert ui.post("/api/try", json={"source": "hackernews"}).status_code == 202
    job = _wait(ui)
    assert job["status"] == "done" and job["result"]["credits_used"] == 0
    assert len(job["result"]["items"]) == 2


def test_try_needs_key_and_keyword(ui, monkeypatch):
    _patch_network(monkeypatch)
    ui.post("/api/try", json={"source": "x_trends"})
    assert "SCAVIO_API_KEY" in _wait(ui)["error"]
    ui.post("/api/try", json={"source": "youtube_keyword"})
    assert "keyword" in _wait(ui)["error"]


def test_one_job_at_a_time():
    jobs = app_mod.Jobs()
    gate = threading.Event()
    jobs.start("run", "Run", lambda log: gate.wait(5))
    with pytest.raises(app_mod.Busy):
        jobs.start("try", "Try", lambda log: None)
    gate.set()
    for _ in range(100):
        if not jobs.busy:
            break
        time.sleep(0.01)
    jobs.start("try", "Try", lambda log: None)


def test_test_channel_without_config(ui, monkeypatch):
    _patch_network(monkeypatch)
    ui.post("/api/test/channel", json={"channel": "discord"})
    assert _wait(ui)["result"]["status"] == "not configured"


def test_schedule_follows_config(ui):
    view = ui.get("/api/config").json()
    saved = ui.put("/api/config", json={"config": view["config"] | {"schedule": {"every": "3h"}}}).json()
    assert saved["plan"]["schedule_text"] == "Every 3 hours, every day"
    assert saved["plan"]["next_scan_at"]
    bad = ui.put("/api/config", json={"config": view["config"] | {"schedule": {"every": "5m"}}})
    assert bad.status_code == 400 and bad.json()["field"] == "schedule"


def test_wizard_init_with_answers(ui):
    (ui.tmp / "config.yaml").unlink()
    answers = {"topics": [{"name": "Matcha", "match": ["matcha", "hojicha"]}], "alerts": {"send": "summary"}, "schedule": {"at": ["09:00"]}}
    assert ui.post("/api/init", json={"config": answers}).status_code == 200
    c = load(ui.tmp / "config.yaml", env={})
    assert c.topics[0].phrases == ["matcha", "hojicha"] and c.alerts.send == "summary" and c.schedule.at == ["09:00"]


def test_alert_preview_before_any_scan(ui):
    d = ui.post("/api/alert-preview").json()
    assert d["title"] and "first scan" in d["empty_note"]


# ---------------------------------------------------------------- writers


def test_dump_round_trips_awkward_values():
    raw = {"topics": ["yes", "null", {"name": "a: b", "match": ["#tag", "it's"], "must": ["1e3"], "exclude": ["x, y"]}],
           "site_url": "https://a.b/c#d", "schedule": {"at": [1080, "8:05"], "days": ["Mon", "fri"]}}
    c = from_dict(raw, {})
    again = from_dict(yaml.safe_load(dump(c)), {})
    assert again.topics == c.topics and again.schedule == c.schedule
    assert c.schedule.at == ["08:05", "18:00"] and c.schedule.days == ["mon", "fri"]
    assert yaml.safe_load(dump(c))["site_url"] == "https://a.b/c#d"


def test_write_dotenv_keeps_other_lines(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# my comment\nFOO=1\nSCAVIO_API_KEY=old\nSCAVIO_API_KEY=dup\n\nBAR='x y'\n")
    write_dotenv(p, {"SCAVIO_API_KEY": "new", "NTFY_TOPIC": "my topic #1"})
    text = p.read_text()
    assert text.startswith("# my comment\nFOO=1\nSCAVIO_API_KEY=new\n")
    assert "dup" not in text
    env = read_dotenv(p)
    assert env["NTFY_TOPIC"] == "my topic #1" and env["BAR"] == "x y"
    write_dotenv(p, {"FOO": None})
    assert "FOO" not in read_dotenv(p)
    with pytest.raises(ConfigError):
        write_dotenv(p, {"lower": "x"})


# ---------------------------------------------------------------- previews, background, telegram


def test_preview_topic_uses_free_news(ui, monkeypatch):
    _patch_network(monkeypatch)
    assert ui.post("/api/preview-topic", json={"topic": {"name": "ai agents"}}).status_code == 202
    job = _wait(ui)
    assert job["status"] == "done", job
    assert job["result"]["name"] == "ai agents" and job["result"]["total"] > 0
    assert all(h["new"] is False for h in job["result"]["hits"])


def test_alert_preview_new_mode_says_when_nothing_would_send(ui, monkeypatch):
    _patch_network(monkeypatch)
    ui.post("/api/run", json={"notify": False})
    assert _wait(ui)["status"] == "done"
    d = ui.post("/api/alert-preview", params={"mode": "new"}).json()
    # The first scan marks nothing new, so "only new" would not send.
    assert d["title"] == "No alert this time" and "no alert would be sent" in d["empty_note"]
    every = ui.post("/api/alert-preview", params={"mode": "every"}).json()
    assert every["blocks"]


def test_background_status_and_env_only_keys(ui, monkeypatch):
    monkeypatch.setattr(app_mod.background, "status", lambda: app_mod.background.Status(True, False, False, "macos"))
    monkeypatch.setenv("SCAVIO_API_KEY", "from-shell-1234")
    st = ui.get("/api/background").json()
    assert st["supported"] and not st["installed"] and st["env_only"] == ["SCAVIO_API_KEY"]


def test_telegram_chat_finder_needs_a_token(ui):
    resp = ui.post("/api/telegram/chats")
    assert resp.status_code == 400 and "token" in resp.json()["error"]


def test_state_reports_key_before_setup(ui, monkeypatch):
    (ui.tmp / "config.yaml").unlink()
    monkeypatch.setenv("SCAVIO_API_KEY", "k")
    st = ui.get("/api/state").json()
    assert st["has_config"] is False and st["has_key"] is True


def test_first_scan_sends_current_matches(ui, monkeypatch):
    _patch_network(monkeypatch)
    received = []
    monkeypatch.setattr(service, "send_one", lambda channel, digest, report, env, http: received.append(digest) or "ok")
    monkeypatch.setenv("WEBHOOK_URL", "http://127.0.0.1:1/hook")
    ui.post("/api/run", json={"notify": True})
    job = _wait(ui)
    assert job["status"] == "done"
    assert received and received[0].title.startswith("First scan.")


def test_config_yaml_preview_and_alert_text(ui):
    view = ui.get("/api/config").json()
    text = ui.post("/api/config/yaml", json={"config": view["config"] | {"topics": ["Matcha"]}}).json()["text"]
    assert "  - Matcha" in text and "# Topics you follow" in text
    assert ui.post("/api/config/yaml", json={"config": {"country": "USA"}}).status_code == 400
    assert "TrendScout:" in ui.post("/api/alert-preview").json()["text"]
