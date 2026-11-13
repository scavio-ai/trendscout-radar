"""Minimal HTTP client for the Scavio API.

Requests run one at a time. Scavio's free plan allows a single request in
flight, so a sequential client works on every plan without tuning.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import httpx

from . import __version__

DEFAULT_BASE_URL = "https://api.scavio.dev"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class ScavioError(Exception):
    """A request that failed after retries, or one that retrying cannot fix."""

    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message

    @property
    def is_auth(self) -> bool:
        """The key is missing, invalid or out of credits: every later call would fail too."""
        return self.status in (401, 402, 403)


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200] or resp.reason_phrase
    if isinstance(body, dict):
        return str(body.get("error") or body.get("message") or body)[:200]
    return str(body)[:200]


class ScavioClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 90.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Client-Source": "trendscout",
                "User-Agent": f"trendscout/{__version__}",
            },
        )
        self._max_retries = max_retries
        self._sleep = sleep
        self.credits_used = 0
        self.credits_remaining: int | None = None

    def close(self) -> None:
        self._http.close()

    def usage(self) -> dict[str, Any]:
        """Plan and credit balance. Free: it spends no credits."""
        resp = self._http.get("/api/v1/usage")
        if resp.status_code != 200:
            raise ScavioError(resp.status_code, _error_message(resp))
        return resp.json()

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                resp = self._http.post(path, json=body)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise ScavioError(0, f"network error: {exc}") from exc
                self._sleep(self._backoff(attempt, None))
                attempt += 1
                continue

            if resp.status_code == 200:
                data = resp.json()
                self._track(data)
                return data
            if resp.status_code in RETRY_STATUSES and attempt < self._max_retries:
                self._sleep(self._backoff(attempt, resp.headers.get("retry-after")))
                attempt += 1
                continue
            raise ScavioError(resp.status_code, _error_message(resp))

    def _track(self, data: dict[str, Any]) -> None:
        used = data.get("credits_used")
        if isinstance(used, (int, float)):
            self.credits_used += int(used)
        remaining = data.get("credits_remaining")
        if isinstance(remaining, (int, float)):
            self.credits_remaining = int(remaining)

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return min(2.0 * (2**attempt), 30.0)
