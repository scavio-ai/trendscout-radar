"""Configuration: config.yaml for what to watch, environment variables for secrets."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import jinja2
import yaml

# X takes country names, not codes.
X_COUNTRIES = {
    "US": "UnitedStates",
    "GB": "UnitedKingdom",
    "CA": "Canada",
    "AU": "Australia",
    "IN": "India",
    "IE": "Ireland",
    "NZ": "NewZealand",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "IT": "Italy",
    "NL": "Netherlands",
    "BR": "Brazil",
    "MX": "Mexico",
    "JP": "Japan",
}

ALL_SOURCES = (
    "tiktok_searches",
    "tiktok_videos",
    "tiktok_sounds",
    "google_trending",
    "x_trends",
    "reddit_trending",
    "reddit_popular",
    "hackernews",
    "youtube_keyword",
    "tiktok_keyword",
    "reddit_keyword",
    "news_keyword",
    "hackernews_keyword",
)


# Sources that stay off unless config.yaml turns them on.
DEFAULT_OFF = {"reddit_trending"}


def default_sources() -> dict[str, bool]:
    return {s: s not in DEFAULT_OFF for s in ALL_SOURCES}


class ConfigError(ValueError):
    pass


MAX_TOPICS = 25
SEARCH_PLATFORMS = ("youtube", "tiktok", "reddit")  # paid per-topic searches, in display order
CHANNEL_IDS = ("telegram", "discord", "slack", "ntfy", "email", "webhook")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ALERT_MODES = ("new", "summary", "every")


@dataclass
class Topic:
    """Something the user follows. An item belongs to the topic when its title
    contains any `match` phrase, every `must` word, and no `exclude` word."""

    name: str
    match: list[str] = field(default_factory=list)  # empty means [name]
    must: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    search: list[str] = field(default_factory=list)  # paid searches: some of youtube, tiktok, reddit
    news: bool = True  # free Google News search for the topic
    hackernews: bool = True  # free Hacker News search for the topic
    notify: list[str] | None = None  # alert channels; None = every channel that is set up, [] = none
    schedule: Schedule | None = None  # when this topic alerts; None = the shared schedule

    @property
    def phrases(self) -> list[str]:
        return self.match or [self.name]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "match": list(self.match), "must": list(self.must), "exclude": list(self.exclude),
                "search": list(self.search), "news": self.news, "hackernews": self.hackernews,
                "notify": None if self.notify is None else list(self.notify),
                "schedule": self.schedule.to_dict() if self.schedule else None}


@dataclass
class Alerts:
    send: str = "new"  # new: only what is new; summary: everything seen today; every: every scan
    breakout_platforms: int = 3


@dataclass
class Schedule:
    """When scans run while TrendScout is running: every N minutes, or at set times."""

    enabled: bool = True
    every: int | None = None  # minutes
    at: list[str] = field(default_factory=lambda: ["08:00", "18:00"])
    days: list[str] = field(default_factory=lambda: list(WEEKDAYS))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"enabled": self.enabled}
        if self.every:
            d["every"] = _minutes_text(self.every)
        else:
            d["at"] = list(self.at)
        d["days"] = list(self.days)
        return d


def _minutes_text(minutes: int) -> str:
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _str_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where} must be a list of words")
    out: list[str] = []
    for v in value:
        v = v.strip()
        if v and v.lower() not in (x.lower() for x in out):
            out.append(v)
    return out


def _topics(raw: Any) -> list[Topic]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError("topics must be a list")
    if len(raw) > MAX_TOPICS:
        raise ConfigError(f"topics: at most {MAX_TOPICS}")
    topics: list[Topic] = []
    for entry in raw:
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, Mapping):
            raise ConfigError("topics: each topic is a name or a mapping with a name")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise ConfigError("topics: every topic needs a name")
        if any(t.name.lower() == name.lower() for t in topics):
            raise ConfigError(f"topics: {name!r} is listed twice")
        search = entry.get("search", [])
        if search is True:  # the short form: every platform
            search = list(SEARCH_PLATFORMS)
        elif search is False or search is None:
            search = []
        elif isinstance(search, str):
            search = [search]
        if not isinstance(search, list) or any(s not in SEARCH_PLATFORMS for s in search):
            raise ConfigError(f"topics: search for {name!r} must be true, false or some of {', '.join(SEARCH_PLATFORMS)}")
        search = [s for s in SEARCH_PLATFORMS if s in search]
        news = entry.get("news", True)
        if not isinstance(news, bool):
            raise ConfigError(f"topics: news for {name!r} must be true or false")
        hackernews = entry.get("hackernews", True)
        if not isinstance(hackernews, bool):
            raise ConfigError(f"topics: hackernews for {name!r} must be true or false")
        notify = entry.get("notify")
        if notify is not None:
            notify = [notify] if isinstance(notify, str) else notify
            if not isinstance(notify, list) or any(c not in CHANNEL_IDS for c in notify):
                raise ConfigError(f"topics: notify for {name!r} must be some of {', '.join(CHANNEL_IDS)}")
            notify = [c for c in CHANNEL_IDS if c in notify]
        topic_schedule = _schedule(entry["schedule"]) if entry.get("schedule") else None
        topics.append(Topic(
            name=name,
            match=_str_list(entry.get("match"), f"topics: match for {name!r}"),
            must=_str_list(entry.get("must"), f"topics: must for {name!r}"),
            exclude=_str_list(entry.get("exclude"), f"topics: exclude for {name!r}"),
            search=search,
            news=news,
            hackernews=hackernews,
            notify=notify,
            schedule=topic_schedule,
        ))
    return topics


def _alerts(raw: Any) -> Alerts:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise ConfigError("alerts must be a mapping")
    send = str(raw.get("send", "new"))
    if send not in ALERT_MODES:
        raise ConfigError(f"alerts: send must be one of {', '.join(ALERT_MODES)}, got {send!r}")
    return Alerts(send=send, breakout_platforms=_int(raw, "breakout_platforms", 3, 2, 5, prefix="alerts: "))


_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_minutes(text: str) -> int:
    m = re.fullmatch(r"(\d+)\s*([mhd])", str(text).strip().lower())
    if not m:
        raise ConfigError("schedule: every must be a number plus m, h or d, e.g. 30m, 3h, 1d")
    minutes = int(m.group(1)) * {"m": 1, "h": 60, "d": 1440}[m.group(2)]
    if minutes < 15:
        raise ConfigError("schedule: the shortest interval is 15m")
    return minutes


def _schedule(raw: Any) -> Schedule:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise ConfigError("schedule must be a mapping")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("schedule: enabled must be true or false")
    days = [d.lower()[:3] for d in _str_list(raw.get("days", list(WEEKDAYS)), "schedule: days")]
    bad = [d for d in days if d not in WEEKDAYS]
    if bad or not days:
        raise ConfigError("schedule: days must be some of mon, tue, wed, thu, fri, sat, sun")
    days = [d for d in WEEKDAYS if d in days]
    if raw.get("every"):
        return Schedule(enabled=enabled, every=parse_minutes(raw["every"]), at=[], days=days)
    at_raw = raw.get("at", ["08:00", "18:00"])
    if isinstance(at_raw, (int, str)) and not isinstance(at_raw, bool):
        at_raw = [at_raw]
    if isinstance(at_raw, list):
        # YAML reads an unquoted 18:00 as the number 1080 (minutes); turn it back into a time.
        at_raw = [f"{v // 60:02d}:{v % 60:02d}" if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 1440 else v for v in at_raw]
    at = _str_list(at_raw, "schedule: at")
    if not at:
        raise ConfigError("schedule: give at least one time, e.g. at: [\"08:00\"]")
    times = []
    for value in at:
        m = _TIME.match(value)
        if not m:
            raise ConfigError(f"schedule: {value!r} is not a time like 08:00")
        times.append(f"{int(m.group(1)):02d}:{m.group(2)}")
    return Schedule(enabled=enabled, every=None, at=sorted(set(times)), days=days)


@dataclass
class Config:
    country: str = "US"
    language: str = "en"
    topics: list[Topic] = field(default_factory=list)
    alerts: Alerts = field(default_factory=Alerts)
    schedule: Schedule = field(default_factory=Schedule)
    sources: dict[str, bool] = field(default_factory=default_sources)
    tiktok_video_calls: int = 2
    hackernews_limit: int = 30
    max_credits_per_run: int = 60
    top_n: int = 10
    data_dir: Path = Path("data")
    reports_dir: Path = Path("reports")
    site_dir: Path = Path("site")
    keep_days: int = 30
    site_url: str | None = None
    site_exclude: list[str] = field(default_factory=list)  # extra words hidden on the public page
    # Secrets and endpoints, from the environment.
    scavio_api_key: str | None = None
    scavio_base_url: str = "https://api.scavio.dev"
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)

    @property
    def x_country(self) -> str:
        return X_COUNTRIES.get(self.country.upper(), "UnitedStates")

    def enabled(self, source: str) -> bool:
        return bool(self.sources.get(source, False))


def _int(raw: Mapping[str, Any], key: str, default: int, lo: int, hi: int, prefix: str = "") -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise ConfigError(f"{prefix}{key} must be a whole number from {lo} to {hi}, got {value!r}")
    return value


def from_dict(raw: Mapping[str, Any], env: Mapping[str, str]) -> Config:
    if not isinstance(raw, Mapping):
        raise ConfigError("config must be a YAML mapping")

    if "keywords" in raw:
        raise ConfigError("keywords was renamed to topics: rename the key in config.yaml")

    sources = default_sources()
    for name, on in (raw.get("sources") or {}).items():
        if name not in ALL_SOURCES:
            raise ConfigError(f"unknown source {name!r}; valid sources: {', '.join(ALL_SOURCES)}")
        if not isinstance(on, bool):
            raise ConfigError(f"sources.{name} must be true or false")
        sources[name] = on

    country = str(raw.get("country", "US")).upper()
    if len(country) != 2 or not country.isalpha():
        raise ConfigError(f"country must be a two-letter code such as US or GB, got {country!r}")

    def secret(name: str) -> str | None:
        value = (env.get(name) or "").strip()
        return value or None

    return Config(
        country=country,
        language=str(raw.get("language", "en")).lower(),
        topics=_topics(raw.get("topics")),
        alerts=_alerts(raw.get("alerts")),
        schedule=_schedule(raw.get("schedule")),
        sources=sources,
        tiktok_video_calls=_int(raw, "tiktok_video_calls", 2, 1, 5),
        hackernews_limit=_int(raw, "hackernews_limit", 30, 1, 100),
        max_credits_per_run=_int(raw, "max_credits_per_run", 60, 1, 5000),
        top_n=_int(raw, "top_n", 10, 1, 50),
        data_dir=Path(raw.get("data_dir", "data")),
        reports_dir=Path(raw.get("reports_dir", "reports")),
        site_dir=Path(raw.get("site_dir", "site")),
        keep_days=_int(raw, "keep_days", 30, 1, 365),
        site_url=raw.get("site_url") or None,
        site_exclude=_str_list(raw.get("site_exclude"), "site_exclude"),
        scavio_api_key=secret("SCAVIO_API_KEY"),
        scavio_base_url=secret("SCAVIO_BASE_URL") or "https://api.scavio.dev",
        llm_api_key=secret("LLM_API_KEY"),
        llm_base_url=secret("LLM_BASE_URL") or "https://api.openai.com/v1",
        llm_model=secret("LLM_MODEL"),
        env=dict(env),
    )


def read_dotenv(path: Path) -> dict[str, str]:
    """KEY=VALUE lines from a .env file; comments and blank lines ignored."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values


def dotenv_path(config_path: str | Path) -> Path:
    """The .env file next to config.yaml."""
    return Path(config_path).parent / ".env"


def merged_env(config_path: str | Path) -> dict[str, str]:
    # Real environment variables win over .env, so CI secrets are never shadowed.
    return {**read_dotenv(dotenv_path(config_path)), **os.environ}


def load(path: str | Path, env: Mapping[str, str] | None = None) -> Config:
    if env is None:
        env = merged_env(path)
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"{p} not found. Copy config.example.yaml to {p} to start.")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{p} is not valid YAML: {exc}") from exc
    return from_dict(raw, env)


def with_state_dir(config: Config, state_dir: str | Path | None) -> Config:
    """Put data/, reports/ and site/ under one folder (used by GitHub Actions)."""
    if state_dir:
        root = Path(state_dir)
        config.data_dir = root / config.data_dir
        config.reports_dir = root / config.reports_dir
        config.site_dir = root / config.site_dir
    return config


# ------------------------------------------------------------------ writing


def to_dict(config: Config) -> dict[str, Any]:
    """The config.yaml fields of a Config (no secrets)."""
    return {
        "country": config.country,
        "language": config.language,
        "topics": [tp.to_dict() for tp in config.topics],
        "alerts": {"send": config.alerts.send, "breakout_platforms": config.alerts.breakout_platforms},
        "schedule": config.schedule.to_dict(),
        "sources": {name: bool(config.sources.get(name, False)) for name in ALL_SOURCES},
        "tiktok_video_calls": config.tiktok_video_calls,
        "hackernews_limit": config.hackernews_limit,
        "max_credits_per_run": config.max_credits_per_run,
        "top_n": config.top_n,
        "data_dir": config.data_dir.as_posix(),
        "reports_dir": config.reports_dir.as_posix(),
        "site_dir": config.site_dir.as_posix(),
        "keep_days": config.keep_days,
        "site_url": config.site_url,
        "site_exclude": list(config.site_exclude),
    }


def _yaml_scalar(value: Any) -> str:
    # Dump inside a flow list so PyYAML quotes only when the value needs it.
    return yaml.safe_dump([value], default_flow_style=True, allow_unicode=True, width=10_000).strip()[1:-1]


_writer = jinja2.Environment(
    loader=jinja2.PackageLoader("trendscout", "templates"),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    autoescape=False,
)
_writer.filters["y"] = _yaml_scalar
_writer.globals["source_notes"] = {
    "tiktok_searches": "~90 trending TikTok search terms",
    "tiktok_videos": "a sample of trending videos (see tiktok_video_calls)",
    "tiktok_sounds": "Viral 50 sound chart",
    "google_trending": "Google Trending Now (free RSS without a key)",
    "x_trends": "X trending topics for your country",
    "reddit_trending": "Reddit trending searches (often empty; off by default)",
    "reddit_popular": "r/popular front page",
    "hackernews": "Hacker News top stories",
    "youtube_keyword": "for topics that search youtube",
    "tiktok_keyword": "for topics that search tiktok",
    "reddit_keyword": "for topics that search reddit",
    "news_keyword": "Google News for every topic",
    "hackernews_keyword": "Hacker News search for every topic",
}
def _schedule_flow(s: Mapping[str, Any]) -> str:
    parts = []
    if s.get("enabled") is False:
        parts.append("enabled: false")
    if s.get("every"):
        parts.append(f"every: {s['every']}")
    elif s.get("at"):
        parts.append("at: [" + ", ".join(_yaml_scalar(a) for a in s["at"]) + "]")
    days = s.get("days") or list(WEEKDAYS)
    if list(days) != list(WEEKDAYS):
        parts.append("days: [" + ", ".join(days) + "]")
    return "{" + ", ".join(parts) + "}"


_writer.filters["schedule"] = _schedule_flow
_writer.filters["flow"] = lambda values: "[" + ", ".join(_yaml_scalar(v) for v in values) + "]"


def dump(config: Config) -> str:
    """config.yaml text with the explanatory comments kept."""
    return _writer.get_template("config.yaml.j2").render(c=to_dict(config))


def _atomic_write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save(raw: Mapping[str, Any], path: str | Path) -> Config:
    """Validate a config mapping and write it to config.yaml. Raises ConfigError, leaving the file untouched."""
    config = from_dict(raw, {})
    _atomic_write(Path(path), dump(config))
    return config


_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _env_value(value: str) -> str:
    if value and not re.search(r"[\s#'\"]", value):
        return value
    return "'" + value + "'" if "'" not in value else '"' + value + '"'


def write_dotenv(path: str | Path, updates: Mapping[str, str | None]) -> None:
    """Set or remove keys in a .env file, keeping every other line. Empty or None removes the key.

    The file is written atomically and readable by its owner only.
    """
    for key, value in updates.items():
        if not _ENV_NAME.match(key):
            raise ConfigError(f"invalid variable name {key!r}")
        if value is not None and ("\n" in value or "\r" in value):
            raise ConfigError(f"{key}: value must be a single line")
        if value and "'" in value and '"' in value:
            raise ConfigError(f"{key}: value cannot contain both kinds of quote")
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
    pending = {k: (v or "").strip() for k, v in updates.items()}
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = stripped.partition("=")[0].strip().removeprefix("export ").strip() if "=" in stripped and not stripped.startswith("#") else None
        if key in updates:
            value = pending.pop(key, "")  # a repeated key is dropped after its first line
            if value:
                out.append(f"{key}={_env_value(value)}")
            continue
        out.append(line)
    for key, value in pending.items():
        if value:
            out.append(f"{key}={_env_value(value)}")
    _atomic_write(p, "\n".join(out) + ("\n" if out else ""), mode=0o600)
