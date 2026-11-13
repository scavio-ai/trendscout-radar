"""What the CLI and the local UI both do: plan, run, try one source, test a key or a channel.

Every function takes a Config and a `log` callback, so the CLI can print and the UI
can stream the same lines to the browser.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx

from . import __version__, alerts, render, scheduling, store, timeline
from .client import ScavioClient, ScavioError
from .collect import collect
from .config import X_COUNTRIES, Config, Topic, _atomic_write, _topics
from .model import Snapshot
from .notify import enabled_channels, send_one
from .rank import Report, build_report
from .sources import GLOBAL_SOURCES, KEYWORD_SOURCES, Context, estimate_credits, plan_sources
from .sources.registry import search_cost
from .summarize import summarize
from .topics import match_topics

Log = Callable[[str], None]

SIGNUP_URL = "https://scavio.dev/?utm_source=github&utm_medium=cli&utm_campaign=trendscout"


def _quiet(_: str) -> None:
    pass


def parse_interval(text: str) -> int:
    """'30m', '6h', '1d' -> seconds. The shortest interval is 15 minutes."""
    m = re.fullmatch(r"(\d+)\s*([mhd])", text.strip().lower())
    if not m:
        raise ValueError("use a number plus m, h or d, e.g. 30m, 6h, 1d")
    seconds = int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]
    if seconds < 15 * 60:
        raise ValueError("the shortest interval is 15m")
    return seconds


def make_http() -> httpx.Client:
    return httpx.Client(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": f"trendscout/{__version__} (+https://github.com/scavio-ai/trendscout)"},
    )


def make_client(config: Config) -> ScavioClient | None:
    return ScavioClient(config.scavio_api_key, config.scavio_base_url) if config.scavio_api_key else None


def site_url(config: Config) -> str | None:
    return config.site_url or (config.env.get("SITE_URL") or "").strip() or None


# ------------------------------------------------------------------ secrets


@dataclass(frozen=True)
class EnvVar:
    name: str
    label: str
    secret: bool
    placeholder: str = ""


@dataclass(frozen=True)
class EnvGroup:
    id: str
    label: str
    kind: str  # "key", "briefing" or "channel"
    vars: tuple[EnvVar, ...]
    help: str = ""


ENV_GROUPS: tuple[EnvGroup, ...] = (
    EnvGroup("scavio", "Scavio API", "key", (EnvVar("SCAVIO_API_KEY", "API key", True, "Paste your Scavio API key"),),
             "Unlocks TikTok, YouTube, Reddit and X. Without it, free sources only."),
    EnvGroup("llm", "AI briefing", "briefing", (
        EnvVar("LLM_API_KEY", "API key", True, "sk-..."),
        EnvVar("LLM_MODEL", "Model", False, "gpt-5.5"),
        EnvVar("LLM_BASE_URL", "Base URL", False, "https://api.openai.com/v1"),
    ), "Any OpenAI-compatible endpoint: OpenAI, OpenRouter, Ollama, LM Studio."),
    EnvGroup("telegram", "Telegram", "channel", (
        EnvVar("TELEGRAM_BOT_TOKEN", "Bot token", True, "123456:ABC..."),
        EnvVar("TELEGRAM_CHAT_ID", "Chat ID", False, "-100123456789"),
    ), "Create a bot with @BotFather, add it to a chat, then use that chat's ID."),
    EnvGroup("discord", "Discord", "channel", (EnvVar("DISCORD_WEBHOOK_URL", "Webhook URL", True, "https://discord.com/api/webhooks/..."),),
             "Channel settings, Integrations, Webhooks, New Webhook."),
    EnvGroup("slack", "Slack", "channel", (EnvVar("SLACK_WEBHOOK_URL", "Webhook URL", True, "https://hooks.slack.com/services/..."),),
             "An incoming webhook from a Slack app."),
    EnvGroup("ntfy", "ntfy", "channel", (
        EnvVar("NTFY_TOPIC", "Topic", True, "my-trend-radar-7f3k"),
        EnvVar("NTFY_SERVER", "Server", False, "https://ntfy.sh"),
    ), "Push notifications to your phone. Pick a topic name nobody can guess."),
    EnvGroup("email", "Email", "channel", (
        EnvVar("SMTP_HOST", "SMTP host", False, "smtp.gmail.com"),
        EnvVar("SMTP_PORT", "Port", False, "587"),
        EnvVar("SMTP_USER", "Username", False, "you@example.com"),
        EnvVar("SMTP_PASSWORD", "Password", True, "app password"),
        EnvVar("EMAIL_FROM", "From", False, "you@example.com"),
        EnvVar("EMAIL_TO", "To (comma-separated)", False, "you@example.com"),
    ), "Any SMTP server. Port 465 uses SSL, anything else STARTTLS."),
    EnvGroup("webhook", "Webhook", "channel", (EnvVar("WEBHOOK_URL", "URL", True, "https://example.com/hooks/trends"),),
             "Receives the full JSON report as a POST."),
)

ENV_VARS: dict[str, EnvVar] = {v.name: v for g in ENV_GROUPS for v in g.vars}


def _hint(var: EnvVar, value: str) -> str | None:
    if not var.secret:
        return value
    return "..." + value[-4:] if len(value) >= 12 else None


def secret_status(file_env: Mapping[str, str], real_env: Mapping[str, str]) -> list[dict[str, Any]]:
    """What the UI may know about each variable: set or not, where from, and a hint. Never a secret value."""
    groups = []
    for g in ENV_GROUPS:
        vars_ = []
        for v in g.vars:
            real = (real_env.get(v.name) or "").strip()
            from_file = (file_env.get(v.name) or "").strip()
            value, source = (real, "environment") if real else (from_file, ".env") if from_file else ("", None)
            vars_.append({
                "name": v.name,
                "label": v.label,
                "secret": v.secret,
                "placeholder": v.placeholder,
                "set": bool(value),
                "source": source,
                "hint": _hint(v, value) if value else None,
            })
        merged = {**file_env, **real_env}
        enabled = (g.id in enabled_channels(merged)) if g.kind == "channel" else all(x["set"] for x in vars_ if x["name"] != "LLM_BASE_URL")
        groups.append({"id": g.id, "label": g.label, "kind": g.kind, "help": g.help, "enabled": enabled, "vars": vars_})
    return groups


def redact(text: str, env: Mapping[str, str]) -> str:
    """Remove any secret value that slipped into an error message."""
    for name, var in ENV_VARS.items():
        value = (env.get(name) or "").strip()
        if var.secret and len(value) >= 6:
            text = text.replace(value, "***")
    return text


# ------------------------------------------------------------------ plan


def source_catalog() -> list[dict[str, Any]]:
    out = []
    for name, spec in GLOBAL_SOURCES.items():
        if name == "google_trends_rss":
            continue
        out.append({"name": name, "label": spec.label, "platform": spec.platform, "cost": spec.cost,
                    "needs_key": spec.needs_key, "kind": "global", "free_fallback": name == "google_trending"})
    for name, spec in KEYWORD_SOURCES.items():
        out.append({"name": name, "label": spec.label, "platform": spec.platform, "cost": spec.cost,
                    "needs_key": spec.needs_key, "kind": "keyword", "free_fallback": False})
    return out


def plan(config: Config) -> dict[str, Any]:
    calls = plan_sources(config)
    per_run = estimate_credits(config)
    now_local = datetime.now().astimezone()
    per_day = scheduling.scans_per_day_all(config, now_local)
    nxt = scheduling.next_scan(config, now_local)
    return {
        "has_key": bool(config.scavio_api_key),
        "calls": [{"label": c.label, "source": c.spec.name, "keyword": c.keyword, "cost": c.cost, "platform": c.spec.platform} for c in calls],
        "per_run": per_run,
        "capped_at": config.max_credits_per_run,
        "over_cap": per_run > config.max_credits_per_run,
        "per_month": {str(n): per_run * n * 30 for n in (1, 2, 4)},
        "scans_per_day": round(per_day, 2),
        "per_month_on_schedule": round(per_run * per_day * 30),
        "schedule_text": scheduling.describe(config.schedule),
        "own_schedules": [{"topic": t.name, "text": scheduling.describe(t.schedule)} for t in config.topics if t.schedule],
        "next_scan_at": nxt.isoformat(timespec="minutes") if nxt else None,
        "search_cost": search_cost(),
        "channels": enabled_channels(config.env),
        "briefing": bool(config.llm_api_key and config.llm_model),
        "countries": sorted(X_COUNTRIES),
    }


def key_status(config: Config) -> dict[str, Any]:
    """Plan and credits left for the configured key. Free: spends no credits."""
    if not config.scavio_api_key:
        return {"ok": False, "set": False, "message": "No SCAVIO_API_KEY", "signup_url": SIGNUP_URL}
    client = ScavioClient(config.scavio_api_key, config.scavio_base_url, timeout=20, max_retries=0)
    try:
        usage = client.usage()
    except ScavioError as exc:
        return {"ok": False, "set": True, "message": redact(str(exc), config.env)}
    except httpx.HTTPError as exc:
        return {"ok": False, "set": True, "message": f"network error: {type(exc).__name__}"}
    finally:
        client.close()
    return {"ok": True, "set": True, "plan": usage.get("plan"), "credits": usage.get("credit_balance")}


# ------------------------------------------------------------------ run


@dataclass
class RunResult:
    ok: bool
    taken_at: str | None = None
    items: int = 0
    signals: int = 0
    new: int = 0
    credits_used: int = 0
    credits_remaining: int | None = None
    errors: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    sent: dict[str, str] = field(default_factory=dict)
    alert: str | None = None  # the alert's title, or None when nothing was worth sending
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _prune_outputs(config: Config, now: datetime) -> None:
    store.prune(config.data_dir, config.keep_days, now)
    cutoff = now.timestamp() - config.keep_days * 86400
    for folder, pattern in ((config.reports_dir, "????-??-??-????.md"), (config.site_dir / "archive", "*.html")):
        if folder.exists():
            for p in folder.glob(pattern):
                try:
                    stamp = datetime.strptime(p.stem, "%Y-%m-%d-%H%M").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if stamp.timestamp() < cutoff:
                    p.unlink()


def _briefing(report: Report, config: Config, http: httpx.Client) -> None:
    if config.llm_api_key and config.llm_model:
        try:
            report.summary = summarize(report, config.llm_api_key, config.llm_base_url, config.llm_model, http)
        except Exception as exc:
            report.errors["ai_briefing"] = redact(f"{type(exc).__name__}: {str(exc)[:150]}", config.env)


class ScanBusy(RuntimeError):
    pass


@contextlib.contextmanager
def scan_lock(data_dir: Path):
    """One scan at a time per folder, across processes (the app, the CLI, the background service)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # Windows: no cross-process lock
        yield
        return
    with open(data_dir / ".scan.lock", "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ScanBusy("another scan is running in this folder") from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


Due = tuple[set[str], bool]  # (topics whose alert time has come, whether the shared schedule has)


def run_once(config: Config, notify: bool, log: Log = _quiet, now: datetime | None = None, due: Due | None = None) -> RunResult:
    """One scan. `due` comes from the scheduler; None (a manual scan, the CLI,
    GitHub Actions) alerts every topic."""
    with scan_lock(config.data_dir):
        return _run_once(config, notify, log, now, due)


ALERT_STATE = "alerts.json"


def _alert_state(data_dir: Path) -> dict[str, str]:
    """When each topic last had its alert turn."""
    try:
        return json.loads((data_dir / ALERT_STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _send_alerts(config: Config, report: Report, tl, previous, snap: Snapshot, http, url, log: Log, due: Due | None) -> tuple[dict[str, str], list[str]]:
    """Send each channel the topics routed to it whose alert time has come."""
    now_local = datetime.now().astimezone()
    due_names, shared_due = due if due is not None else ({t.name for t in config.topics}, True)
    due_topics = [t for t in config.topics if t.name in due_names]
    state = _alert_state(config.data_dir)
    since = {t.name: state[t.name] for t in due_topics if t.name in state}
    channels = enabled_channels(config.env)
    sent: dict[str, str] = {}
    titles: list[str] = []
    if not channels:
        log("No alert channel is set up, so no alert was sent.")
    for channel in channels:
        topics = [t for t in due_topics if t.notify is None or channel in t.notify]
        if config.topics and not topics and not shared_due:
            continue
        build = dict(site_url=url, top_n=config.top_n, since=since, breakouts=shared_due, fallback=not config.topics)
        digest = alerts.build(report, config.alerts, topics, tl, now_local, **build)
        if digest is None and previous is None and topics:
            # The very first scan has nothing to compare with; send what matches now,
            # so a new user sees an alert arrive instead of silence.
            digest = alerts.build(report, config.alerts, topics, tl, now_local, mode="every", **build)
            if digest:
                digest.title = "First scan. " + digest.title
        if digest is None or (config.topics and not digest.blocks):
            log(f"{channel}: nothing new for its topics, so no alert.")
            continue
        sent[channel] = send_one(channel, digest, report, config.env, http)
        titles.append(digest.title)
        log(f"Sent to {channel} ({digest.title}): {sent[channel]}")
    for t in due_topics:
        state[t.name] = snap.taken_at
    _atomic_write(config.data_dir / ALERT_STATE, json.dumps(state, ensure_ascii=False, indent=1))
    return sent, titles


def _run_once(config: Config, notify: bool, log: Log, now: datetime | None, due: Due | None) -> RunResult:
    now = now or datetime.now(timezone.utc)
    client = make_client(config)
    if client is None:
        log(f"No SCAVIO_API_KEY set: free sources only. TikTok, YouTube, Reddit and X need a key: {SIGNUP_URL}")

    with make_http() as http:
        log(f"Scanning ({len(plan_sources(config))} calls, up to {estimate_credits(config)} credits) ...")
        snap = collect(config, client, http, now=now, log=log)
        if not snap.items:
            log("Nothing collected; keeping the previous scan. Errors:")
            for k, v in {**snap.errors, **snap.skipped}.items():
                log(f"  {k}: {v}")
            return RunResult(ok=False, errors=snap.errors, skipped=snap.skipped)

        previous = store.previous(config.data_dir, snap.taken_at)
        store.save(snap, config.data_dir)
        tl = timeline.record(config.data_dir, snap, config.keep_days)
        report = build_report(snap, previous, config.top_n, config.topics, tl)
        if config.llm_api_key and config.llm_model:
            log("Writing the AI briefing ...")
        _briefing(report, config, http)

        url = site_url(config)
        paths = render.write_all(report, config.reports_dir, config.site_dir, url, config.site_exclude)
        log("")
        matched = sum(tr.total for tr in report.topics)
        new_matched = sum(tr.new_count for tr in report.topics)
        log(f"{len(snap.items)} items, {matched} matching your topics ({new_matched} new), {len(report.cross)} on more than one platform.")
        log(f"Credits used: {snap.credits_used}" + (f" ({snap.credits_remaining} left)" if snap.credits_remaining is not None else ""))
        log(f"Report: {paths['markdown']}  |  Page: {paths['html']}")
        if snap.errors:
            log("Failed sources: " + ", ".join(snap.errors))
        if snap.skipped:
            log("Skipped sources: " + ", ".join(snap.skipped))

        sent: dict[str, str] = {}
        titles: list[str] = []
        if notify:
            sent, titles = _send_alerts(config, report, tl, previous, snap, http, url, log, due)

    _prune_outputs(config, now)
    return RunResult(
        ok=True,
        taken_at=snap.taken_at,
        items=len(snap.items),
        signals=len(report.cross),
        new=new_matched,
        credits_used=snap.credits_used,
        credits_remaining=snap.credits_remaining,
        errors=dict(report.errors),
        skipped=dict(snap.skipped),
        sent=sent,
        alert="; ".join(dict.fromkeys(titles)) or None,
        paths={k: str(v) for k, v in paths.items()},
    )


# ------------------------------------------------------------------ one source


def try_source(config: Config, name: str, keyword: str | None = None, log: Log = _quiet) -> dict[str, Any]:
    """Call one source on its own and return what it parsed. Nothing is saved."""
    if name in GLOBAL_SOURCES:
        spec = GLOBAL_SOURCES[name]
        keyword = None
    elif name in KEYWORD_SOURCES:
        spec = KEYWORD_SOURCES[name]
        keyword = (keyword or "").strip()
        if not keyword:
            raise ValueError("this source needs a keyword")
    else:
        raise ValueError(f"unknown source {name!r}")

    client = make_client(config)
    if spec.needs_key and client is None:
        raise ValueError("this source needs a SCAVIO_API_KEY")
    cost = spec.cost * (config.tiktok_video_calls if name == "tiktok_videos" else 1)
    label = f"{spec.label}: {keyword}" if keyword else spec.label
    log(f"Calling {label} ({f'up to {cost} credits' if cost else 'free'}) ...")
    started = datetime.now(timezone.utc)
    with make_http() as http:
        ctx = Context(config=config, client=client, http=http)
        try:
            items = spec.fetch(ctx, keyword) if keyword else spec.fetch(ctx)
        except ScavioError as exc:
            raise ValueError(redact(str(exc), config.env)) from exc
        finally:
            if client is not None:
                client.close()
    seconds = (datetime.now(timezone.utc) - started).total_seconds()
    log(f"{len(items)} items in {seconds:.1f}s")
    return {
        "source": name,
        "label": label,
        "keyword": keyword,
        "seconds": round(seconds, 1),
        "credits_used": client.credits_used if client else 0,
        "credits_remaining": client.credits_remaining if client else None,
        "items": [i.to_dict() for i in items],
    }


# ------------------------------------------------------------------ reports


def snapshot_id(path: Path) -> str:
    return path.stem


_SNAPSHOT_ID = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{6}Z$")


def snapshot_path(config: Config, sid: str) -> Path:
    if not _SNAPSHOT_ID.match(sid):
        raise ValueError("bad snapshot id")
    return config.data_dir / "snapshots" / f"{sid}.json"


def load_snapshot(config: Config, sid: str | None) -> Snapshot | None:
    if sid is None:
        return store.latest(config.data_dir)
    path = snapshot_path(config, sid)
    if not path.is_file():
        return None
    return Snapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))


def report_for(config: Config, snap: Snapshot) -> Report:
    tl = timeline.current(config.data_dir)
    report = build_report(snap, store.previous(config.data_dir, snap.taken_at), config.top_n, config.topics, tl)
    latest = config.reports_dir / "latest.json"
    if latest.is_file():  # the AI briefing is not part of the snapshot; reuse it if it belongs to this scan
        try:
            saved = json.loads(latest.read_text(encoding="utf-8"))
            if saved.get("taken_at") == snap.taken_at:
                report.summary = saved.get("summary")
                if "ai_briefing" in saved.get("errors", {}):
                    report.errors["ai_briefing"] = saved["errors"]["ai_briefing"]
        except (OSError, ValueError):
            pass
    return report


def empty_report(topics: list[Topic] | None = None) -> Report:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return build_report(Snapshot(now), None, 10, topics or [], None)


def alert_preview(config: Config, mode: str | None = None, channel: str | None = None) -> alerts.Digest:
    """What an alert would say for the latest scan (in `every` style unless asked otherwise).
    With a channel, only the topics routed to it."""
    snap = store.latest(config.data_dir)
    report = report_for(config, snap) if snap else empty_report(config.topics)
    tl = timeline.current(config.data_dir)
    now = datetime.now().astimezone()
    topics = [t for t in config.topics if channel is None or t.notify is None or channel in t.notify]
    digest = alerts.build(report, config.alerts, topics, tl, now, mode=mode or "every", site_url=site_url(config),
                          top_n=config.top_n, fallback=not config.topics)
    if digest is None:  # "only new" with nothing new
        return alerts.Digest("No alert this time", now.strftime("%b %d, %H:%M").replace(" 0", " "), [],
                             empty_note="Nothing new for your topics since the last scan, so no alert would be sent.")
    if snap is None:
        digest.empty_note = "Your first scan has not run yet. After it does, alerts list what is new for your topics."
    return digest


def send_test(config: Config, channel: str, log: Log = _quiet) -> str:
    """Send what an alert looks like right now to one channel, marked as a test."""
    digest = alert_preview(config, channel=channel)
    digest.title = "Test alert. " + digest.title
    snap = store.latest(config.data_dir)
    report = report_for(config, snap) if snap else empty_report(config.topics)
    log(f"Sending a test alert to {channel} ...")
    with make_http() as http:
        status = send_one(channel, digest, report, config.env, http)
    status = redact(status, config.env)
    log(f"{channel}: {status}")
    return status


def preview_topic(config: Config, raw_topic: Any, log: Log = _quiet) -> dict[str, Any]:
    """What a topic would match: the latest scan plus a fresh, free Google News search."""
    topics = _topics([raw_topic])
    topic = topics[0]
    snap = store.latest(config.data_dir)
    items = [i for i in (snap.items if snap else []) if i.keyword is None]
    with make_http() as http:
        ctx = Context(config=config, client=None, http=http)
        for name, on, label in (("news_keyword", topic.news, "Google News"), ("hackernews_keyword", topic.hackernews, "Hacker News")):
            if not on:
                continue
            log(f"Searching {label} for {topic.name} ...")
            try:
                items += KEYWORD_SOURCES[name].fetch(ctx, topic.name)
            except Exception as exc:  # the scan's own items still give a preview
                log(f"{label} did not answer: {type(exc).__name__}")
    tl = timeline.current(config.data_dir)
    result = match_topics(topics, items, tl, "", 30)[0]
    for h in result.hits:
        h.new = False
    log(f"{result.total} matches")
    return {"name": topic.name, "total": result.total, "hits": [h.to_dict() for h in result.hits],
            "from_scan": sum(1 for h in result.hits if h.trending)}


def briefing(config: Config, log: Log = _quiet) -> str:
    if not (config.llm_api_key and config.llm_model):
        raise ValueError("set LLM_API_KEY and LLM_MODEL first")
    snap = store.latest(config.data_dir)
    if snap is None:
        raise ValueError("run the radar once first; the briefing summarizes the latest report")
    report = report_for(config, snap)
    log(f"Asking {config.llm_model} for a briefing ...")
    with make_http() as http:
        try:
            return summarize(report, config.llm_api_key, config.llm_base_url, config.llm_model, http)
        except httpx.HTTPStatusError as exc:
            raise ValueError(f"the model endpoint answered HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ValueError(redact(f"{type(exc).__name__}: {exc}", config.env)) from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError("the model endpoint returned an unexpected response") from exc
