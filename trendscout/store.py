"""Snapshots on disk: one JSON file per run under data/snapshots/.

Plain files, so a GitHub Actions run can commit its history back to the repo
and a fork keeps its own history without a database.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .model import Snapshot


def _dir(data_dir: Path) -> Path:
    return data_dir / "snapshots"


def _name(taken_at: str) -> str:
    # 2026-11-11T19:30:00+00:00 -> 2026-11-11T193000Z.json (sortable, filesystem-safe)
    dt = datetime.fromisoformat(taken_at)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H%M%SZ") + ".json"


def snapshot_id(taken_at: str) -> str:
    """The file stem of a snapshot, used as its id: 2026-11-11T193000Z."""
    return _name(taken_at).removesuffix(".json")


def save(snapshot: Snapshot, data_dir: Path) -> Path:
    d = _dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / _name(snapshot.taken_at)
    path.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def all_paths(data_dir: Path) -> list[Path]:
    d = _dir(data_dir)
    return sorted(d.glob("*.json")) if d.exists() else []


def previous(data_dir: Path, before: str) -> Snapshot | None:
    """The latest snapshot taken strictly before `before` (an ISO timestamp)."""
    cutoff = _name(before)
    older = [p for p in all_paths(data_dir) if p.name < cutoff]
    if not older:
        return None
    return Snapshot.from_dict(json.loads(older[-1].read_text(encoding="utf-8")))


def latest(data_dir: Path) -> Snapshot | None:
    paths = all_paths(data_dir)
    return Snapshot.from_dict(json.loads(paths[-1].read_text(encoding="utf-8"))) if paths else None


def prune(data_dir: Path, keep_days: int, now: datetime) -> int:
    cutoff = (now.astimezone(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%dT%H%M%SZ")
    removed = 0
    for p in all_paths(data_dir):
        if p.stem < cutoff:
            p.unlink()
            removed += 1
    return removed
