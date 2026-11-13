"""trendscout command line.

  trendscout init            create config.yaml from the example
  trendscout check           validate config, show the plan and its credit cost (spends nothing)
  trendscout run             collect, compare, write reports, notify
  trendscout run --every 6h  keep running on a schedule (Docker, a home server)
  trendscout report          rebuild reports from the latest snapshot (no fetching)
  trendscout ui              open the app: topics, alerts, scans, settings
  trendscout background on   keep scanning on your schedule when the app is closed
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from importlib import resources
from pathlib import Path

from . import __version__, render, service, store
from .config import Config, ConfigError, load, with_state_dir

SIGNUP_URL = service.SIGNUP_URL


def _print(msg: str = "") -> None:
    print(msg, flush=True)


# ------------------------------------------------------------------ commands


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.config)
    if target.exists():
        _print(f"{target} already exists; leaving it alone.")
        return 0
    example = resources.files("trendscout").joinpath("config.example.yaml")
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    _print(f"Created {target}. Edit the topics, then run: trendscout check")
    return 0


def _load(args: argparse.Namespace) -> Config:
    """Load config; --state-dir puts data/, reports/ and site/ under one folder."""
    return with_state_dir(load(args.config), getattr(args, "state_dir", None))


def cmd_check(args: argparse.Namespace) -> int:
    config = _load(args)
    p = service.plan(config)
    _print(f"Config OK: country {config.country}, {len(config.topics)} topic(s).")
    _print("")
    _print("This run would call:")
    for c in p["calls"]:
        cost = f"{c['cost']} credit{'s' if c['cost'] != 1 else ''}" if c["cost"] else "free"
        _print(f"  - {c['label']} ({cost})")
    _print("")
    if config.scavio_api_key:
        _print(f"Scavio credits per run: up to {p['per_run']} (capped by max_credits_per_run = {config.max_credits_per_run}).")
        for runs in (1, 2, 4):
            _print(f"  {runs} run(s) a day -> about {p['per_month'][str(runs)]:,} credits a month")
        key = service.key_status(config)
        if not key["ok"]:
            _print(f"Scavio key problem: {key['message']}")
            return 1
        _print(f"Scavio key OK: plan {key.get('plan')}, {key.get('credits')} credits left.")
    else:
        _print("No SCAVIO_API_KEY: running on free sources only (Hacker News, Google Trends, Google News).")
        _print(f"Add a key to unlock TikTok, YouTube, Reddit and X. Free key: {SIGNUP_URL}")
    _print("")
    _print(f"Schedule: {p['schedule_text']}" + (f" (next scan {p['next_scan_at'].replace('T', ' ')})" if p["next_scan_at"] else "")
           + ". It applies while trendscout ui runs; on GitHub the workflow's cron line decides.")
    _print("Alerts: " + {"new": "only what is new", "summary": "a summary of today", "every": "every scan"}[config.alerts.send]
           + ", to " + (", ".join(p["channels"]) if p["channels"] else "no channel yet (reports are still written to disk)"))
    _print("AI briefing: " + ("on, model " + str(config.llm_model) if p["briefing"] else "off (set LLM_API_KEY and LLM_MODEL to turn on)"))
    return 0


def run_once(config: Config, notify: bool) -> int:
    return 0 if service.run_once(config, notify, log=_print).ok else 1


def _interval(text: str) -> int:
    try:
        return service.parse_interval(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def cmd_run(args: argparse.Namespace) -> int:
    if not args.every:
        return run_once(_load(args), notify=not args.no_notify)
    while True:
        try:
            run_once(_load(args), notify=not args.no_notify)
        except Exception as exc:  # keep the loop alive; the next run may succeed
            _print(f"Run failed: {type(exc).__name__}: {exc}")
        _print(f"Next run in {args.every // 60} minutes.")
        time.sleep(args.every)


def cmd_ui(args: argparse.Namespace) -> int:
    from .app import serve

    return serve(
        config_path=Path(args.config),
        state_dir=args.state_dir,
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
    )


def cmd_background(args: argparse.Namespace) -> int:
    from . import background

    if args.action == "on":
        if not Path(args.config).exists():
            _print(f"{args.config} not found. Run trendscout ui first and finish the setup.")
            return 2
        try:
            st = background.install(Path(args.config), args.port)
        except RuntimeError as exc:
            _print(str(exc))
            return 1
        _print("Background scanning is on. TrendScout starts at login and keeps scanning on your schedule.")
    elif args.action == "off":
        st = background.uninstall()
        _print("Background scanning is off.")
    else:
        st = background.status()
    _print(f"Installed: {'yes' if st.installed else 'no'}, running: {'yes' if st.running else 'no'}" + (f". {st.detail}" if st.detail else ""))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    config = _load(args)
    snap = store.latest(config.data_dir)
    if snap is None:
        _print("No snapshots yet. Run: trendscout run")
        return 1
    report = service.report_for(config, snap)
    paths = render.write_all(report, config.reports_dir, config.site_dir, service.site_url(config), config.site_exclude)
    _print(f"Rebuilt {paths['markdown']} and {paths['html']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trendscout", description="Open-source trend radar for TikTok, YouTube, Reddit, Google and X.")
    parser.add_argument("--version", action="version", version=f"trendscout {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_config(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("-c", "--config", default=os.environ.get("TRENDSCOUT_CONFIG", "config.yaml"))
        p.add_argument("--state-dir", help="folder that holds data/, reports/ and site/ (default: current folder)")
        return p

    with_config(sub.add_parser("init", help="create config.yaml")).set_defaults(func=cmd_init)
    with_config(sub.add_parser("check", help="validate config and show the credit cost")).set_defaults(func=cmd_check)
    run = with_config(sub.add_parser("run", help="collect, compare, report, notify"))
    run.add_argument("--no-notify", action="store_true", help="write reports but send nothing")
    run.add_argument("--every", type=_interval, help="repeat on an interval, e.g. 6h")
    run.set_defaults(func=cmd_run)
    with_config(sub.add_parser("report", help="rebuild reports from the latest snapshot")).set_defaults(func=cmd_report)
    ui = with_config(sub.add_parser("ui", help="open the local web app"))
    ui.add_argument("--host", default="127.0.0.1", help="interface to listen on (default 127.0.0.1; use 0.0.0.0 in Docker)")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    ui.set_defaults(func=cmd_ui)
    bg = with_config(sub.add_parser("background", help="keep scanning when the app is closed (macOS, Linux)"))
    bg.add_argument("action", choices=["on", "off", "status"])
    bg.add_argument("--port", type=int, default=8765)
    bg.set_defaults(func=cmd_background)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        _print(f"Config error: {exc}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
