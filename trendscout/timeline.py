"""How long each item has been around: first seen, last seen, times seen, rank range.

Kept in data/timeline.json and updated after every scan, so a report or an alert
can say "on the list since 08:00, seen 4 times, best rank 2" without reading
every saved scan. If the file is missing it is rebuilt from the saved scans.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import store
from .config import _atomic_write
from .model import Item, Snapshot

FILE = "timeline.json"
_KEEP_EXTRA = ("publisher", "subreddit", "channel", "author", "context")


@dataclass
class Seen:
    first: str  # ISO time of the first scan that had it
    last: str  # ISO time of the latest scan that had it
    count: int  # number of scans that had it
    best: int  # best (lowest) rank
    worst: int  # worst (highest) rank
    item: dict[str, Any]  # the item as last seen, slimmed

    def to_item(self) -> Item:
        return Item.from_dict(self.item)


Timeline = dict[str, Seen]


def _slim(item: Item) -> dict[str, Any]:
    d = item.to_dict()
    d["extra"] = {k: v for k, v in item.extra.items() if k in _KEEP_EXTRA and v}
    return d


def path(data_dir: Path) -> Path:
    return data_dir / FILE


def load(data_dir: Path) -> Timeline | None:
    p = path(data_dir)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {k: Seen(**v) for k, v in raw.get("items", {}).items()}
    except (OSError, ValueError, TypeError):
        return None


def save(data_dir: Path, tl: Timeline) -> None:
    data = {"version": 1, "items": {k: asdict(v) for k, v in tl.items()}}
    _atomic_write(path(data_dir), json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def update(tl: Timeline, snap: Snapshot) -> None:
    seen_now: set[str] = set()
    for item in snap.items:
        key = item.key
        if key in seen_now:
            continue
        seen_now.add(key)
        s = tl.get(key)
        if s is None:
            tl[key] = Seen(snap.taken_at, snap.taken_at, 1, item.rank, item.rank, _slim(item))
            continue
        if s.last != snap.taken_at:
            s.count += 1
            s.last = snap.taken_at
        s.best = min(s.best, item.rank)
        s.worst = max(s.worst, item.rank)
        s.item = _slim(item)


def prune(tl: Timeline, keep_days: int, now: datetime) -> None:
    cutoff = now - timedelta(days=keep_days)
    for key in [k for k, s in tl.items() if datetime.fromisoformat(s.last) < cutoff]:
        del tl[key]


def rebuild(data_dir: Path) -> Timeline:
    tl: Timeline = {}
    for p in store.all_paths(data_dir):
        try:
            update(tl, Snapshot.from_dict(json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, ValueError, KeyError):
            continue
    return tl


def record(data_dir: Path, snap: Snapshot, keep_days: int) -> Timeline:
    """Add a scan to the timeline (rebuilding it first if needed) and save it."""
    tl = load(data_dir)
    if tl is None:
        tl = rebuild(data_dir)  # includes snap if it is already saved
    update(tl, snap)
    prune(tl, keep_days, datetime.fromisoformat(snap.taken_at).astimezone(timezone.utc))
    save(data_dir, tl)
    return tl


def current(data_dir: Path) -> Timeline:
    tl = load(data_dir)
    if tl is None:
        tl = rebuild(data_dir)
        if tl:
            save(data_dir, tl)
    return tl
