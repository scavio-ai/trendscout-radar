"""When the next scan is due, from the `schedule:` block of config.yaml.

Times are in the machine's local time zone. A scan missed while the computer
slept is not made up later, except for interval schedules: if the last scan is
older than the interval, the next one is due right away.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from .config import WEEKDAYS, Schedule


def _allowed(dt: datetime, days: list[str]) -> bool:
    return WEEKDAYS[dt.weekday()] in days


def next_run(s: Schedule, now: datetime, last: datetime | None = None) -> datetime | None:
    """The next time a scan should start, or None when the schedule is off.

    `now` and `last` must be timezone-aware.
    """
    if not s.enabled or not s.days:
        return None
    if s.every:
        step = timedelta(minutes=s.every)
        due = max(last + step, now) if last else now + step
        for _ in range(8):  # each pass returns or moves to the next day
            if _allowed(due, s.days):
                return due
            # Jump to the start of the next day, then keep looking.
            due = datetime.combine(due.date() + timedelta(days=1), time(0, 0), tzinfo=due.tzinfo)
        return None
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        if WEEKDAYS[day.weekday()] not in s.days:
            continue
        for hhmm in s.at:
            h, m = (int(x) for x in hhmm.split(":"))
            due = datetime.combine(day, time(h, m), tzinfo=now.tzinfo)
            if due > now:
                return due
    return None


def scans_per_day(s: Schedule) -> float:
    if not s.enabled or not s.days:
        return 0.0
    per_active_day = (1440 / s.every) if s.every else len(s.at)
    return per_active_day * len(s.days) / 7


def describe(s: Schedule) -> str:
    """'Every 3 hours' or '08:00 and 18:00, weekdays'."""
    if not s.enabled:
        return "Off"
    if s.every:
        m = s.every
        what = f"every {m // 60} hours" if m % 60 == 0 and m > 60 else ("every hour" if m == 60 else f"every {m} minutes")
        if m % 1440 == 0:
            what = "once a day" if m == 1440 else f"every {m // 1440} days"
    else:
        what = " and ".join([", ".join(s.at[:-1]), s.at[-1]] if len(s.at) > 1 else s.at)
    if len(s.days) == 7:
        days = "every day"
    elif s.days == ["mon", "tue", "wed", "thu", "fri"]:
        days = "weekdays"
    elif s.days == ["sat", "sun"]:
        days = "weekends"
    else:
        days = ", ".join(d.capitalize() for d in s.days)
    return f"{what[0].upper()}{what[1:]}, {days}"


# ---------------------------------------------------------------- topics with their own times


def all_schedules(config) -> list[Schedule]:
    """The shared schedule plus every topic's own. Scans run at their combined times."""
    return [config.schedule] + [t.schedule for t in config.topics if t.schedule]


def next_scan(config, now: datetime, last: datetime | None = None) -> datetime | None:
    times = [t for t in (next_run(s, now, last) for s in all_schedules(config)) if t is not None]
    return min(times) if times else None


def _due(s: Schedule, prev: datetime | None, now: datetime) -> bool:
    if prev is None:
        return True
    nxt = next_run(s, prev, last=prev)
    return nxt is not None and nxt <= now + timedelta(minutes=1)


def due(config, prev: datetime | None, now: datetime) -> tuple[set[str], bool]:
    """(topics whose alert time has come since `prev`, whether the shared schedule has)."""
    names = {t.name for t in config.topics if _due(t.schedule or config.schedule, prev, now)}
    return names, _due(config.schedule, prev, now)


def scans_per_day_all(config, now: datetime) -> float:
    """Scans a day across every schedule, measured over the coming week."""
    end = now + timedelta(days=7)
    count, t, last = 0, now, None
    while count < 7 * 24 * 4 + 1:
        nxt = next_scan(config, t, last)
        if nxt is None or nxt > end:
            break
        count += 1
        t, last = nxt + timedelta(seconds=1), nxt
    return count / 7
