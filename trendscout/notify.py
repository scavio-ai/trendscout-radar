"""Delivery channels. Each is switched on by setting its environment variables.

  Telegram  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  Discord   DISCORD_WEBHOOK_URL
  Slack     SLACK_WEBHOOK_URL
  ntfy      NTFY_TOPIC (NTFY_SERVER defaults to https://ntfy.sh)
  Email     SMTP_HOST, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO (SMTP_PORT defaults to 587)
  Webhook   WEBHOOK_URL (receives the alert and the full report as JSON)

One channel failing does not stop the others.
"""

from __future__ import annotations

import base64
import json
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable, Mapping

import httpx

from .alerts import Digest, render, to_html
from .rank import Report
from .render import to_json


@dataclass(frozen=True)
class Request:
    url: str
    json: dict | None = None
    content: bytes | None = None
    headers: dict[str, str] | None = None


def telegram(d: Digest, report: Report, env: Mapping[str, str]) -> Request:
    return Request(
        url=f"https://api.telegram.org/bot{env['TELEGRAM_BOT_TOKEN']}/sendMessage",
        json={
            "chat_id": env["TELEGRAM_CHAT_ID"],
            "text": render(d, "telegram", 4000),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


def discord(d: Digest, report: Report, env: Mapping[str, str]) -> Request:
    # allowed_mentions: a trend title must never ping @everyone.
    return Request(url=env["DISCORD_WEBHOOK_URL"], json={"content": render(d, "discord", 1950), "allowed_mentions": {"parse": []}})


def slack(d: Digest, report: Report, env: Mapping[str, str]) -> Request:
    return Request(url=env["SLACK_WEBHOOK_URL"], json={"text": render(d, "slack", 3500), "unfurl_links": False, "unfurl_media": False})


def _header(value: str) -> str:
    # HTTP headers are ASCII; ntfy reads RFC 2047 encoded words for anything else.
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(value.encode()).decode() + "?="


def ntfy(d: Digest, report: Report, env: Mapping[str, str]) -> Request:
    server = (env.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    headers = {"Title": _header(f"TrendScout: {d.title}")}
    if d.url:
        headers["Click"] = d.url
    return Request(url=f"{server}/{env['NTFY_TOPIC']}", content=render(d, "plain", 3800).encode(), headers=headers)


def webhook(d: Digest, report: Report, env: Mapping[str, str]) -> Request:
    body = {"schema": "trendscout.alert/1", "alert": d.to_dict(), "report": json.loads(to_json(report))}
    return Request(
        url=env["WEBHOOK_URL"],
        content=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )


HTTP_CHANNELS: dict[str, tuple[tuple[str, ...], Callable[..., Request]]] = {
    "telegram": (("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"), telegram),
    "discord": (("DISCORD_WEBHOOK_URL",), discord),
    "slack": (("SLACK_WEBHOOK_URL",), slack),
    "ntfy": (("NTFY_TOPIC",), ntfy),
    "webhook": (("WEBHOOK_URL",), webhook),
}
EMAIL_VARS = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO")


def _has(env: Mapping[str, str], names: tuple[str, ...]) -> bool:
    return all((env.get(n) or "").strip() for n in names)


def enabled_channels(env: Mapping[str, str]) -> list[str]:
    names = [name for name, (needs, _) in HTTP_CHANNELS.items() if _has(env, needs)]
    if _has(env, EMAIL_VARS):
        names.append("email")
    return names


def email_message(d: Digest, env: Mapping[str, str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"TrendScout: {d.title}"
    msg["From"] = env["EMAIL_FROM"]
    msg["To"] = ", ".join(a.strip() for a in env["EMAIL_TO"].split(",") if a.strip())
    msg.set_content(render(d, "plain", 100_000))
    msg.add_alternative(to_html(d), subtype="html")
    return msg


def send_email(msg: EmailMessage, env: Mapping[str, str]) -> None:
    host = env["SMTP_HOST"]
    port = int(env.get("SMTP_PORT") or 587)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as s:
            s.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=context)
            s.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
            s.send_message(msg)


def _error(exc: Exception) -> str:
    # Never echo the URL: Telegram and webhook URLs carry secrets.
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return f"{type(exc).__name__}: HTTP {status}" if status else type(exc).__name__


def send_one(
    channel: str,
    digest: Digest,
    report: Report,
    env: Mapping[str, str],
    http: httpx.Client,
    smtp_send: Callable[[EmailMessage, Mapping[str, str]], None] = send_email,
) -> str:
    """Send to one channel. Returns "ok" or a short error that never contains a secret."""
    if channel == "email":
        if not _has(env, EMAIL_VARS):
            return "not configured"
        try:
            smtp_send(email_message(digest, env), env)
            return "ok"
        except Exception as exc:
            return f"{type(exc).__name__}: {str(exc)[:120]}"
    if channel not in HTTP_CHANNELS:
        return "unknown channel"
    needs, build = HTTP_CHANNELS[channel]
    if not _has(env, needs):
        return "not configured"
    try:
        req = build(digest, report, env)
        resp = http.post(req.url, json=req.json, content=req.content, headers=req.headers, timeout=30)
        resp.raise_for_status()
        return "ok"
    except Exception as exc:  # report and keep going
        return _error(exc)


def send_all(
    digest: Digest,
    report: Report,
    env: Mapping[str, str],
    http: httpx.Client,
    smtp_send: Callable[[EmailMessage, Mapping[str, str]], None] = send_email,
) -> dict[str, str]:
    """Send to every configured channel. Returns {channel: "ok" | error message}."""
    return {name: send_one(name, digest, report, env, http, smtp_send) for name in enabled_channels(env)}
