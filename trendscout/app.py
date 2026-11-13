"""The local web app behind `trendscout ui`.

A small JSON API on Python's standard library HTTP server, plus the static files
in trendscout/ui. It listens on 127.0.0.1 by default and is meant for one person
on their own machine.

Protection from other websites open in the same browser:
  - every /api call must carry the per-launch token in the X-TrendScout-Token
    header. A custom header forces a CORS preflight that this server never
    approves, and the token is only in the URL printed to the terminal;
  - the Host header must be a loopback name, which defeats DNS rebinding;
  - secret values are never sent to the browser, only whether they are set.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import yaml

from . import __version__, alerts, background, render, scheduling, service, store, timeline
from .config import (
    X_COUNTRIES,
    Config,
    ConfigError,
    dotenv_path,
    dump,
    from_dict,
    load,
    merged_env,
    read_dotenv,
    save,
    to_dict,
    with_state_dir,
    write_dotenv,
)

TOKEN_HEADER = "X-TrendScout-Token"
MAX_BODY = 1_000_000
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


# ------------------------------------------------------------------ jobs


class Busy(Exception):
    pass


@dataclass
class Job:
    id: int
    kind: str  # run, try, preview, send, briefing
    label: str
    status: str = "running"  # running, done, failed
    started_at: datetime = field(default_factory=_now)
    finished_at: datetime | None = None
    log: list[str] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    scheduled: bool = False

    def to_dict(self, since: int = 0) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "scheduled": self.scheduled,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "log": self.log[since:],
            "log_size": len(self.log),
            "result": self.result,
            "error": self.error,
        }


class Jobs:
    """One job at a time: runs, single-source tries, test sends and briefings share credits and files."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self.current: Job | None = None

    @property
    def busy(self) -> bool:
        return self.current is not None and self.current.status == "running"

    def start(self, kind: str, label: str, fn: Callable[[Callable[[str], None]], Any], scheduled: bool = False) -> Job:
        with self._lock:
            if self.busy:
                assert self.current is not None
                raise Busy(f"busy: {self.current.label} is still running")
            self._seq += 1
            job = Job(self._seq, kind, label, scheduled=scheduled)
            self.current = job
        threading.Thread(target=self._run, args=(job, fn), daemon=True, name=f"job-{job.id}").start()
        return job

    @staticmethod
    def _run(job: Job, fn: Callable[[Callable[[str], None]], Any]) -> None:
        try:
            job.result = fn(job.log.append)
            job.status = "done"
        except Exception as exc:
            job.error = str(exc) if isinstance(exc, (ValueError, ConfigError)) else f"{type(exc).__name__}: {exc}"[:400]
            job.log.append(f"Failed: {job.error}")
            job.status = "failed"
        finally:
            job.finished_at = _now()


# ------------------------------------------------------------------ schedule


class Scheduler:
    """Starts scans on the config.yaml schedule while the app is running.

    It re-reads the schedule at least once a minute, so a saved change applies
    without a restart.
    """

    def __init__(self, app: App) -> None:
        self.app = app
        self.next_at: datetime | None = None
        self.last: dict[str, Any] | None = None
        self._wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="scheduler").start()

    def poke(self) -> None:
        self._wake.set()

    def to_dict(self) -> dict[str, Any]:
        return {"next_at": _iso(self.next_at), "last": self.last}

    @staticmethod
    def _last_scan(config: Config) -> datetime | None:
        paths = store.all_paths(config.data_dir)
        if not paths:
            return None
        try:
            return datetime.strptime(paths[-1].stem, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _loop(self) -> None:
        while True:
            try:
                config = self.app.config()
                now = datetime.now().astimezone()
                last = self._last_scan(config)
                self.next_at = scheduling.next_scan(config, now, last.astimezone() if last else None)
            except ConfigError:
                self.next_at = None
            wait = 60.0 if self.next_at is None else min(60.0, (self.next_at - datetime.now().astimezone()).total_seconds())
            if wait > 0:
                self._wake.wait(timeout=wait)
                self._wake.clear()
                continue
            try:
                config = self.app.config()
                last = self._last_scan(config)
                due = scheduling.due(config, last.astimezone() if last else None, datetime.now().astimezone())
                job = self.app.start_run(True, scheduled=True, due=due)
                self.last = {"at": _iso(_now()), "status": "started"}
                while job.status == "running":  # the next time is computed from this scan
                    time.sleep(2)
                self.last["status"] = job.status
            except (Busy, ConfigError) as exc:
                self.last = {"at": _iso(_now()), "status": f"skipped: {exc}"}
                self._wake.wait(timeout=60)
                self._wake.clear()


# ------------------------------------------------------------------ app


class App:
    def __init__(self, config_path: Path, state_dir: str | None, token: str, port: int = 8765) -> None:
        self.config_path = config_path
        self.port = port
        self.state_dir = state_dir
        self.token = token
        self.jobs = Jobs()
        self.scheduler = Scheduler(self)
        self._summaries: dict[str, tuple[float, dict[str, Any]]] = {}

    # config ------------------------------------------------------------

    def config(self) -> Config:
        return with_state_dir(load(self.config_path), self.state_dir)

    def config_or_default(self) -> Config:
        """config.yaml, or the defaults with your keys while the setup wizard is still open."""
        if not self.config_path.exists():
            return with_state_dir(from_dict({}, merged_env(self.config_path)), self.state_dir)
        return self.config()

    def config_from(self, raw: Any) -> Config:
        """A Config from an unsaved form, with the real secrets, for estimates."""
        return with_state_dir(from_dict(raw, merged_env(self.config_path)), self.state_dir)

    def defaults(self) -> dict[str, Any]:
        example = resources.files("trendscout").joinpath("config.example.yaml").read_text(encoding="utf-8")
        return to_dict(from_dict(yaml.safe_load(example), {}))

    # jobs --------------------------------------------------------------

    def start_run(self, notify: bool, scheduled: bool = False, due: service.Due | None = None) -> Job:
        config = self.config()
        label = "Scheduled run" if scheduled else "Run"
        return self.jobs.start("run", label, lambda log: service.run_once(config, notify, log, due=due).to_dict(), scheduled=scheduled)

    # history -----------------------------------------------------------

    def summaries(self, config: Config) -> list[dict[str, Any]]:
        out = []
        seen = set()
        for path in reversed(store.all_paths(config.data_dir)):
            sid = path.stem
            seen.add(sid)
            mtime = path.stat().st_mtime
            cached = self._summaries.get(sid)
            if cached and cached[0] == mtime:
                out.append(cached[1])
                continue
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            platforms: dict[str, int] = {}
            for item in d.get("items", []):
                platforms[item.get("platform", "?")] = platforms.get(item.get("platform", "?"), 0) + 1
            summary = {
                "id": sid,
                "taken_at": d.get("taken_at"),
                "items": len(d.get("items", [])),
                "platforms": platforms,
                "credits_used": d.get("credits_used", 0),
                "credits_remaining": d.get("credits_remaining"),
                "errors": d.get("errors", {}),
                "skipped": d.get("skipped", {}),
            }
            self._summaries[sid] = (mtime, summary)
            out.append(summary)
        for sid in list(self._summaries):
            if sid not in seen:
                del self._summaries[sid]
        return out


# ------------------------------------------------------------------ routes

Route = Callable[[App, dict[str, Any], dict[str, str]], tuple[int, Any]]


def _config_view(app: App) -> dict[str, Any]:
    view: dict[str, Any] = {
        "path": str(app.config_path.resolve()),
        "exists": app.config_path.exists(),
        "sources": service.source_catalog(),
        "countries": sorted(X_COUNTRIES),
        "defaults": app.defaults(),
        "error": None,
        "config": None,
        "plan": None,
    }
    try:
        config = app.config()
    except ConfigError as exc:
        view["error"] = str(exc)
        return view
    view["config"] = to_dict(load(app.config_path))  # paths as written, without --state-dir
    view["plan"] = service.plan(config)
    return view


def r_state(app: App, body: dict, q: dict) -> tuple[int, Any]:
    has_config, error, latest, count, has_key = app.config_path.exists(), None, None, 0, False
    schedule_text, topics = None, 0
    has_key = bool((merged_env(app.config_path).get("SCAVIO_API_KEY") or "").strip())
    try:
        config = app.config()
        schedule_text, topics = scheduling.describe(config.schedule), len(config.topics)
        paths = store.all_paths(config.data_dir)
        count = len(paths)
        latest = paths[-1].stem if paths else None
    except ConfigError as exc:
        error = str(exc)
    job = app.jobs.current
    return 200, {
        "version": __version__,
        "cwd": str(Path.cwd()),
        "config_path": str(app.config_path.resolve()),
        "has_config": has_config,
        "config_error": error,
        "has_key": has_key,
        "snapshots": count,
        "latest": latest,
        "job": job.to_dict(len(job.log)) if job else None,
        "schedule": app.scheduler.to_dict(),
        "schedule_text": schedule_text,
        "topics": topics,
    }


def r_init(app: App, body: dict, q: dict) -> tuple[int, Any]:
    """Create config.yaml: from the setup wizard's answers, or from the example."""
    if app.config_path.exists():
        return 409, {"error": f"{app.config_path} already exists"}
    if body.get("config") is not None:
        save(body["config"], app.config_path)
    else:
        example = resources.files("trendscout").joinpath("config.example.yaml").read_text(encoding="utf-8")
        app.config_path.parent.mkdir(parents=True, exist_ok=True)
        app.config_path.write_text(example, encoding="utf-8")
    app.scheduler.poke()
    return 200, _config_view(app)


def r_get_config(app: App, body: dict, q: dict) -> tuple[int, Any]:
    return 200, _config_view(app)


def r_put_config(app: App, body: dict, q: dict) -> tuple[int, Any]:
    save(body.get("config"), app.config_path)
    app.scheduler.poke()
    return 200, _config_view(app)


def r_estimate(app: App, body: dict, q: dict) -> tuple[int, Any]:  # also used by the setup wizard
    return 200, service.plan(app.config_from(body.get("config")))


def r_get_secrets(app: App, body: dict, q: dict) -> tuple[int, Any]:
    path = dotenv_path(app.config_path)
    return 200, {"path": str(path.resolve()), "groups": service.secret_status(read_dotenv(path), os.environ)}


def r_put_secrets(app: App, body: dict, q: dict) -> tuple[int, Any]:
    values = body.get("values")
    if not isinstance(values, dict) or not all(isinstance(v, str) for v in values.values()):
        raise ValueError("values must be an object of strings")
    for name in values:
        if name not in service.ENV_VARS:
            raise ValueError(f"unknown setting {name}")
        if (os.environ.get(name) or "").strip():
            raise ValueError(f"{name} is set in your environment, which overrides .env; change it there")
    write_dotenv(dotenv_path(app.config_path), values)
    return r_get_secrets(app, body, q)


def r_check(app: App, body: dict, q: dict) -> tuple[int, Any]:
    config = app.config()
    return 200, {"plan": service.plan(config), "key": service.key_status(config)}


def r_test_scavio(app: App, body: dict, q: dict) -> tuple[int, Any]:
    return 200, service.key_status(app.config_or_default())


def r_run(app: App, body: dict, q: dict) -> tuple[int, Any]:
    return 202, app.start_run(bool(body.get("notify", False))).to_dict()


def r_try(app: App, body: dict, q: dict) -> tuple[int, Any]:
    config = app.config()
    name, keyword = str(body.get("source") or ""), body.get("keyword")
    label = f"Try {name}" + (f": {keyword}" if keyword else "")
    return 202, app.jobs.start("try", label, lambda log: service.try_source(config, name, keyword, log)).to_dict()


def r_test_channel(app: App, body: dict, q: dict) -> tuple[int, Any]:
    config = app.config_from(body["config"]) if body.get("config") is not None else app.config_or_default()
    channel = str(body.get("channel") or "")
    return 202, app.jobs.start("send", f"Test {channel}", lambda log: {"status": service.send_test(config, channel, log)}).to_dict()


def r_test_llm(app: App, body: dict, q: dict) -> tuple[int, Any]:
    config = app.config()
    return 202, app.jobs.start("briefing", "AI briefing", lambda log: {"text": service.briefing(config, log)}).to_dict()


def r_job(app: App, body: dict, q: dict) -> tuple[int, Any]:
    job = app.jobs.current
    since = int(q.get("since") or 0) if (q.get("since") or "0").isdigit() else 0
    return 200, job.to_dict(since) if job else None


def r_snapshots(app: App, body: dict, q: dict) -> tuple[int, Any]:
    return 200, app.summaries(app.config())


def r_delete_snapshot(app: App, body: dict, q: dict) -> tuple[int, Any]:
    if app.jobs.busy:
        raise Busy("wait for the current job to finish")
    config = app.config()
    path = service.snapshot_path(config, q.get("id") or "")
    if not path.is_file():
        return 404, {"error": "no such scan"}
    path.unlink()
    timeline.save(config.data_dir, timeline.rebuild(config.data_dir))
    return 200, app.summaries(config)


def r_report(app: App, body: dict, q: dict) -> tuple[int, Any]:
    config = app.config()
    snap = service.load_snapshot(config, q.get("id") or None)
    if snap is None:
        return 404, {"error": "no snapshot yet: run the radar first"}
    report = service.report_for(config, snap)
    data = json.loads(render.to_json(report))
    data["id"] = store.snapshot_id(snap.taken_at)
    if q.get("items") == "1":
        data["items"] = [i.to_dict() for i in snap.items]
    if q.get("markdown") == "1":
        data["markdown"] = render.to_markdown(report, service.site_url(config))
    return 200, data


def r_get_schedule(app: App, body: dict, q: dict) -> tuple[int, Any]:
    return 200, app.scheduler.to_dict()


def r_preview_topic(app: App, body: dict, q: dict) -> tuple[int, Any]:
    config = app.config_or_default()  # also works in the setup wizard
    raw = body.get("topic")
    name = raw.get("name") if isinstance(raw, dict) else raw
    return 202, app.jobs.start("preview", f"Preview {name}", lambda log: service.preview_topic(config, raw, log)).to_dict()


def r_alert_preview(app: App, body: dict, q: dict) -> tuple[int, Any]:
    mode = q.get("mode") if q.get("mode") in ("new", "summary", "every") else None
    config = app.config()
    if body.get("config") is not None:  # unsaved settings from the editor
        config = app.config_from(body["config"])
    digest = service.alert_preview(config, mode)
    return 200, {**digest.to_dict(), "text": alerts.render(digest, "plain", 100_000)}


def r_config_yaml(app: App, body: dict, q: dict) -> tuple[int, Any]:
    """The config.yaml that saving the current form would write."""
    return 200, {"text": dump(from_dict(body.get("config") or {}, {}))}


def r_telegram_chats(app: App, body: dict, q: dict) -> tuple[int, Any]:
    """Chats that messaged the bot recently, so nobody has to dig for a chat ID."""
    import httpx

    token = (merged_env(app.config_path).get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise ValueError("save the bot token first")
    try:
        resp = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    except httpx.HTTPError as exc:
        raise ValueError(f"Telegram did not answer ({type(exc).__name__})") from None
    if resp.status_code == 401 or resp.status_code == 404:
        raise ValueError("Telegram does not know this bot token; copy it again from @BotFather")
    chats: dict[int, dict[str, Any]] = {}
    for update in resp.json().get("result", []):
        msg = update.get("message") or update.get("channel_post") or update.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or str(chat["id"])
            chats[chat["id"]] = {"id": str(chat["id"]), "name": name, "type": chat.get("type")}
    return 200, list(chats.values())


def r_get_background(app: App, body: dict, q: dict) -> tuple[int, Any]:
    st = background.status().to_dict()
    # Background processes cannot see variables exported in a shell profile.
    st["env_only"] = [n for n in service.ENV_VARS if (os.environ.get(n) or "").strip() and n not in read_dotenv(dotenv_path(app.config_path))]
    st["this_config"] = str(app.config_path.resolve())
    return 200, st


def r_put_background(app: App, body: dict, q: dict) -> tuple[int, Any]:
    on = bool(body.get("on"))
    try:
        if on:
            background.install(app.config_path, app.port)
        else:
            background.uninstall()
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    return r_get_background(app, body, q)


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/api/state"): r_state,
    ("POST", "/api/init"): r_init,
    ("GET", "/api/config"): r_get_config,
    ("PUT", "/api/config"): r_put_config,
    ("POST", "/api/estimate"): r_estimate,
    ("GET", "/api/secrets"): r_get_secrets,
    ("PUT", "/api/secrets"): r_put_secrets,
    ("POST", "/api/check"): r_check,
    ("POST", "/api/test/scavio"): r_test_scavio,
    ("POST", "/api/test/channel"): r_test_channel,
    ("POST", "/api/test/llm"): r_test_llm,
    ("POST", "/api/run"): r_run,
    ("POST", "/api/try"): r_try,
    ("GET", "/api/job"): r_job,
    ("GET", "/api/snapshots"): r_snapshots,
    ("DELETE", "/api/snapshots"): r_delete_snapshot,
    ("GET", "/api/report"): r_report,
    ("GET", "/api/schedule"): r_get_schedule,
    ("POST", "/api/preview-topic"): r_preview_topic,
    ("POST", "/api/alert-preview"): r_alert_preview,
    ("POST", "/api/config/yaml"): r_config_yaml,
    ("POST", "/api/telegram/chats"): r_telegram_chats,
    ("GET", "/api/background"): r_get_background,
    ("PUT", "/api/background"): r_put_background,
}


def _field(message: str) -> str | None:
    """The config field a ConfigError is about, so the UI can mark it."""
    head = message.split(" ", 1)[0].rstrip(":")
    if head.startswith("sources."):
        return "sources"
    if message.startswith("unknown source"):
        return "sources"
    return head if head.replace("_", "").isalpha() else None


# ------------------------------------------------------------------ HTTP


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: App, bind_host: str) -> None:
        super().__init__(address, Handler)
        self.app = app
        self.allowed_hosts = None if bind_host in ("0.0.0.0", "::", "") else LOOPBACK | {bind_host}


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"
    server_version = f"trendscout/{__version__}"

    def log_message(self, format: str, *args: Any) -> None:  # quiet; the terminal shows the URL only
        pass

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    # helpers -------------------------------------------------------------

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        allowed = self.server.allowed_hosts
        if allowed is None:
            return True
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            host = host[1 : host.find("]")] if "]" in host else host
        else:
            host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return host in allowed

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("request too large")
        if not length:
            return {}
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise ValueError("send JSON")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError("send a JSON object")
        return data

    def _static(self, path: str) -> None:
        if path not in STATIC:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        name, ctype = STATIC[path]
        body = resources.files("trendscout").joinpath("ui", name).read_bytes()
        self._send(200, body, ctype)

    # dispatch ------------------------------------------------------------

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            self._send(403, b"forbidden host", "text/plain; charset=utf-8")
            return
        url = urlsplit(self.path)
        if not url.path.startswith("/api/"):
            if method == "GET":
                self._static(url.path)
            else:
                self._send(405, b"method not allowed", "text/plain; charset=utf-8")
            return

        app = self.server.app
        token = self.headers.get(TOKEN_HEADER) or ""
        if not hmac.compare_digest(token.encode(), app.token.encode()):
            self._json(401, {"error": "missing or wrong token: open the link printed in the terminal"})
            return
        route = ROUTES.get((method, url.path))
        if route is None:
            self._json(404, {"error": "no such endpoint"})
            return
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            status, data = route(app, self._body(), query)
        except ConfigError as exc:
            status, data = 400, {"error": str(exc), "field": _field(str(exc))}
        except Busy as exc:
            status, data = 409, {"error": str(exc)}
        except (ValueError, json.JSONDecodeError) as exc:
            status, data = 400, {"error": str(exc)}
        except Exception as exc:  # never a traceback, never a secret
            env = merged_env(app.config_path)
            status, data = 500, {"error": service.redact(f"{type(exc).__name__}: {exc}", env)[:300]}
        self._json(status, data)


# ------------------------------------------------------------------ entry point


ALREADY_RUNNING = 3  # exit code when another TrendScout already serves this folder


def make_server(config_path: Path, state_dir: str | None, host: str, port: int, token: str) -> Server:
    app = App(config_path, state_dir, token, port)
    last: OSError | None = None
    for p in range(port, port + 10) if port else [0]:
        try:
            return Server((host, p), app, host)
        except OSError as exc:  # port in use: try the next one
            last = exc
    raise SystemExit(f"Could not listen on {host}:{port}-{port + 9}: {last}")


def token_path(config_path: Path) -> Path:
    return config_path.resolve().parent / ".trendscout-token"


def load_token(config_path: Path) -> str:
    """The app's access token: kept next to config.yaml, readable only by you, so a
    background instance and the link you open agree across restarts."""
    env = (os.environ.get("TRENDSCOUT_UI_TOKEN") or "").strip()
    if env:
        return env
    p = token_path(config_path)
    try:
        token = p.read_text(encoding="utf-8").strip()
        if len(token) >= 16:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(18)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    return token


def find_running(config_path: Path, token: str, port: int) -> str | None:
    """The URL of a TrendScout already serving this config.yaml, if one is up."""
    import httpx

    want = str(config_path.resolve())
    for p in range(port, port + 10):
        try:
            resp = httpx.get(f"http://127.0.0.1:{p}/api/state", headers={TOKEN_HEADER: token}, timeout=1.5)
        except httpx.HTTPError:
            continue
        if resp.status_code == 200 and resp.json().get("config_path") == want:
            return f"http://127.0.0.1:{p}/?token={token}"
    return None


def serve(
    config_path: Path,
    state_dir: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> int:
    token = load_token(config_path)
    running = find_running(config_path, token, port) if host in ("127.0.0.1", "localhost") else None
    if running:
        print(f"TrendScout is already running for this folder:\n\n  {running}\n", flush=True)
        if open_browser:
            webbrowser.open(running)
            return 0
        return ALREADY_RUNNING  # the background service retries until the other one stops
    server = make_server(config_path, state_dir, host, port, token)
    shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{shown}:{server.server_address[1]}/?token={token}"
    print(f"TrendScout {__version__} is running at:\n\n  {url}\n", flush=True)
    print(f"Config: {config_path.resolve()}", flush=True)
    if host in ("0.0.0.0", "::"):
        print("Listening on every interface. Anyone who can reach this port and has the link can use your keys.", flush=True)
    print("Scans run on the schedule in config.yaml while this is running. Press Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
