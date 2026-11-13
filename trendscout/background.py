"""Keep TrendScout running when its window is closed, so scheduled scans and alerts continue.

macOS: a LaunchAgent in ~/Library/LaunchAgents. Linux: a systemd user service in
~/.config/systemd/user. Both start `python -m trendscout ui --no-browser` at
login, in the folder that holds config.yaml, and restart it if it stops.
Windows is not supported here; run TrendScout on GitHub Actions instead.

Background processes do not see variables exported in your shell profile, so
keys must be saved in TrendScout (the .env file next to config.yaml).
"""

from __future__ import annotations

import os
import platform
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, unescape

LABEL = "dev.scavio.trendscout"
UNIT = "trendscout.service"


@dataclass
class Status:
    supported: bool
    installed: bool
    running: bool
    system: str
    detail: str = ""
    config_path: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def system() -> str:
    return {"Darwin": "macos", "Linux": "linux"}.get(platform.system(), "other")


def command(config_path: Path, port: int) -> list[str]:
    return [sys.executable, "-m", "trendscout", "ui", "--no-browser", "--port", str(port), "-c", str(config_path)]


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT


def log_path(config_path: Path) -> Path:
    return config_path.parent / "trendscout.log"


def launchd_plist(config_path: Path, port: int) -> str:
    args = "\n".join(f"    <string>{escape(a)}</string>" for a in command(config_path, port))
    log = escape(str(log_path(config_path)))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(str(config_path.parent))}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ThrottleInterval</key>
  <integer>60</integer>
  <key>StandardOutPath</key>
  <string>{log}</string>
  <key>StandardErrorPath</key>
  <string>{log}</string>
</dict>
</plist>
"""


def systemd_unit(config_path: Path, port: int) -> str:
    def q(arg: str) -> str:
        return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"' if any(c in arg for c in ' "\\') else arg

    return f"""[Unit]
Description=TrendScout trend radar
After=network-online.target

[Service]
WorkingDirectory={config_path.parent}
ExecStart={" ".join(q(a) for a in command(config_path, port))}
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
"""


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


def _configured_path(text: str) -> str | None:
    """The config.yaml a plist or unit file points at."""
    m = re.search(r"<string>-c</string>\s*<string>([^<]*)</string>", text)
    if m:
        return unescape(m.group(1))
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            args = shlex.split(line.removeprefix("ExecStart="))
            if "-c" in args and args.index("-c") + 1 < len(args):
                return args[args.index("-c") + 1]
    return None


def status() -> Status:
    sysname = system()
    if sysname == "macos":
        p = plist_path()
        if not p.is_file():
            return Status(True, False, False, sysname)
        res = _run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
        running = res.returncode == 0 and "state = running" in res.stdout
        return Status(True, True, running, sysname, config_path=_configured_path(p.read_text()))
    if sysname == "linux":
        p = unit_path()
        if not p.is_file():
            return Status(True, False, False, sysname)
        res = _run(["systemctl", "--user", "is-active", UNIT])
        return Status(True, True, res.stdout.strip() == "active", sysname, config_path=_configured_path(p.read_text()))
    return Status(False, False, False, sysname, "Background scanning works on macOS and Linux. On Windows, run TrendScout on GitHub Actions.")


def install(config_path: Path, port: int = 8765) -> Status:
    config_path = config_path.resolve()
    sysname = system()
    if sysname == "macos":
        p = plist_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        domain = f"gui/{os.getuid()}"
        _run(["launchctl", "bootout", f"{domain}/{LABEL}"])  # replace an older install
        p.write_text(launchd_plist(config_path, port), encoding="utf-8")
        res = _run(["launchctl", "bootstrap", domain, str(p)])
        if res.returncode != 0:
            raise RuntimeError(
                "macOS did not start the background service: "
                + (res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}")
                + ". Check System Settings, General, Login Items, and allow Python to run in the background."
            )
        return status()
    if sysname == "linux":
        p = unit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(systemd_unit(config_path, port), encoding="utf-8")
        for args in (["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", UNIT]):
            res = _run(args)
            if res.returncode != 0:
                raise RuntimeError(f"systemd refused: {res.stderr.strip() or res.stdout.strip()}")
        return status()
    raise RuntimeError(status().detail)


def uninstall() -> Status:
    sysname = system()
    if sysname == "macos":
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
        plist_path().unlink(missing_ok=True)
    elif sysname == "linux":
        _run(["systemctl", "--user", "disable", "--now", UNIT])
        unit_path().unlink(missing_ok=True)
        _run(["systemctl", "--user", "daemon-reload"])
    return status()
