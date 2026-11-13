"""Run every planned source once and gather the results into a Snapshot.

One source failing never stops the run. An auth or credit failure (401, 402,
403) stops the remaining Scavio calls, because every one of them would fail the
same way; free sources still run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import httpx

from .client import ScavioClient, ScavioError
from .config import Config
from .model import Snapshot
from .sources import Context, plan_sources

Log = Callable[[str], None]


def _quiet(_: str) -> None:
    pass


def collect(
    config: Config,
    client: ScavioClient | None,
    http: httpx.Client,
    now: datetime | None = None,
    log: Log = _quiet,
) -> Snapshot:
    now = now or datetime.now(timezone.utc)
    snap = Snapshot(taken_at=now.isoformat(timespec="seconds"))
    ctx = Context(config=config, client=client, http=http)
    auth_failure: str | None = None

    for call in plan_sources(config):
        sid = f"{call.spec.name}:{call.keyword}" if call.keyword else call.spec.name
        if call.spec.needs_key:
            if client is None:
                snap.skipped[sid] = "no SCAVIO_API_KEY"
                continue
            if auth_failure:
                snap.skipped[sid] = auth_failure
                continue
            if client.credits_used + call.cost > config.max_credits_per_run:
                snap.skipped[sid] = f"over max_credits_per_run ({config.max_credits_per_run})"
                continue

        log(f"  {call.label} ...")
        try:
            items = call.spec.fetch(ctx, call.keyword) if call.keyword else call.spec.fetch(ctx)
        except ScavioError as exc:
            snap.errors[sid] = str(exc)
            if exc.is_auth:
                auth_failure = f"stopped after {exc}"
            log(f"    failed: {exc}")
            continue
        except Exception as exc:  # a free source down, or a response we could not read
            snap.errors[sid] = f"{type(exc).__name__}: {exc}"[:300]
            log(f"    failed: {snap.errors[sid]}")
            continue
        snap.items.extend(items)
        log(f"    {len(items)} items")

    if client is not None:
        snap.credits_used = client.credits_used
        snap.credits_remaining = client.credits_remaining
    return snap
