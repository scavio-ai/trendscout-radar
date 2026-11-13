"""Data types shared by sources, ranking and rendering."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

_WORD = re.compile(r"[a-z0-9]+")
_TAG = re.compile(r"(?<!\w)[#@][\w.]+")
_SPACE = re.compile(r"\s+")


def plain(text: str) -> str:
    """Fold styled Unicode letters ('𝙍𝙚𝙡𝙖𝙭') to plain ones and collapse whitespace."""
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def clean_title(text: str) -> str:
    """Display title for a social post: drop #hashtags and @mentions, keep the words.

    A caption that is nothing but hashtags keeps its hashtags.
    """
    text = plain(text)
    stripped = _SPACE.sub(" ", _TAG.sub("", text)).strip(" -|:,")
    return stripped if len(stripped) >= 3 else text


def normalize(text: str) -> str:
    """Lowercase and keep only word characters, so 'GTA 5!' and 'gta 5' compare equal."""
    return " ".join(_WORD.findall(plain(text).lower()))


@dataclass
class Item:
    """One entry from one source: a search term, a video, a post or an article."""

    source: str
    platform: str
    title: str
    rank: int
    url: str | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    keyword: str | None = None
    published_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable identity across runs: the source plus the URL, or the normalized
        title when there is no URL. A URL survives caption edits and title cleanup."""
        return f"{self.source}:{self.url or normalize(self.title)}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Item:
        return cls(
            source=d["source"],
            platform=d["platform"],
            title=d["title"],
            rank=int(d["rank"]),
            url=d.get("url"),
            metrics=dict(d.get("metrics") or {}),
            keyword=d.get("keyword"),
            published_at=d.get("published_at"),
            extra=dict(d.get("extra") or {}),
        )


@dataclass
class Snapshot:
    """Everything one run collected, plus what went wrong."""

    taken_at: str
    items: list[Item] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    credits_used: int = 0
    credits_remaining: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "taken_at": self.taken_at,
            "credits_used": self.credits_used,
            "credits_remaining": self.credits_remaining,
            "errors": self.errors,
            "skipped": self.skipped,
            "items": [i.to_dict() for i in self.items],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Snapshot:
        return cls(
            taken_at=d["taken_at"],
            items=[Item.from_dict(i) for i in d.get("items") or []],
            errors=dict(d.get("errors") or {}),
            skipped=dict(d.get("skipped") or {}),
            credits_used=int(d.get("credits_used") or 0),
            credits_remaining=d.get("credits_remaining"),
        )


@dataclass
class Signal:
    """A ranked finding for the report."""

    title: str
    score: float
    status: str  # "new", "up", "steady"
    platforms: list[str]
    items: list[Item]

    @property
    def lead(self) -> Item:
        return self.items[0]
