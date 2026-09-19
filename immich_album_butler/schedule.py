"""The `auto-update-schedule` grammar.

Cron is precise and unreadable. Since an album needs refreshing daily at worst,
a handful of English-shaped forms covers everything and can be read back by
whoever edits the config a year from now:

    daily 03:30          weekly sun 04:00       monthly 1 05:00
    every 6h             every 30m              manual

`manual` means the daemon never touches that album on its own; only an explicit
`run --once` does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2, "thu": 3, "thur": 3, "thurs": 3,
    "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

MANUAL = "manual"


class ScheduleError(ValueError):
    """An `auto-update-schedule` value that cannot be understood."""


@dataclass(frozen=True)
class Schedule:
    """A parsed schedule. `kind` is manual, daily, weekly, monthly or interval."""

    kind: str
    hour: int = 0
    minute: int = 0
    weekday: int = 0          # weekly: 0 = Monday
    day: int = 1              # monthly: day of month, 1-31
    interval: timedelta | None = None
    text: str = MANUAL

    def __str__(self) -> str:
        return self.text

    @property
    def automatic(self) -> bool:
        return self.kind != MANUAL

    def next_after(self, moment: datetime) -> datetime | None:
        """The first run strictly after `moment`, or None when manual.

        `moment` must be timezone-aware; the result carries the same zone.
        """
        if self.kind == MANUAL:
            return None
        if self.kind == "interval":
            assert self.interval is not None
            return moment + self.interval

        candidate = moment.replace(hour=self.hour, minute=self.minute,
                                   second=0, microsecond=0)
        if self.kind == "daily":
            if candidate <= moment:
                candidate += timedelta(days=1)
            return candidate

        if self.kind == "weekly":
            ahead = (self.weekday - candidate.weekday()) % 7
            candidate += timedelta(days=ahead)
            if candidate <= moment:
                candidate += timedelta(days=7)
            return candidate

        if self.kind == "monthly":
            candidate = _on_day(candidate, self.day)
            if candidate <= moment:
                candidate = _on_day(_next_month(candidate), self.day)
            return candidate

        raise AssertionError(f"unhandled schedule kind {self.kind!r}")

    def is_due(self, last_run: datetime | None, now: datetime) -> bool:
        """Whether this album should run now, given when it last ran."""
        if not self.automatic:
            return False
        if last_run is None:
            return True      # never run: do it at the first opportunity
        due = self.next_after(last_run)
        return due is not None and due <= now


def _next_month(moment: datetime) -> datetime:
    first = moment.replace(day=1)
    return (first + timedelta(days=32)).replace(day=1, hour=moment.hour,
                                                minute=moment.minute,
                                                second=0, microsecond=0)


def _on_day(moment: datetime, day: int) -> datetime:
    """Clamp to the last day of the month, so `monthly 31` works in February."""
    last = _days_in_month(moment.year, moment.month)
    return moment.replace(day=min(day, last))


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


_TIME = r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
_DAILY = re.compile(rf"^daily\s+{_TIME}$", re.I)
_WEEKLY = re.compile(rf"^weekly\s+(?P<day>[a-z]+)\s+{_TIME}$", re.I)
_MONTHLY = re.compile(rf"^monthly\s+(?P<dom>\d{{1,2}})\s+{_TIME}$", re.I)
_EVERY = re.compile(r"^every\s+(?P<count>\d+)\s*(?P<unit>[hm])(?:ours?|inutes?)?$", re.I)


def parse(text: str) -> Schedule:
    """Parse one schedule string, raising ScheduleError with a usable message."""
    if not isinstance(text, str):
        raise ScheduleError(f"schedule must be a string, got {type(text).__name__}")
    value = " ".join(text.strip().split())
    if not value:
        raise ScheduleError("schedule is empty")

    if value.lower() == MANUAL:
        return Schedule(kind=MANUAL, text=MANUAL)

    if match := _DAILY.match(value):
        hour, minute = _time_of(match, value)
        return Schedule("daily", hour=hour, minute=minute, text=value)

    if match := _WEEKLY.match(value):
        day = match.group("day").lower()
        if day not in DAY_NAMES:
            raise ScheduleError(
                f"{value!r}: {match.group('day')!r} is not a weekday "
                f"(use {', '.join(DAYS)})")
        hour, minute = _time_of(match, value)
        return Schedule("weekly", hour=hour, minute=minute,
                        weekday=DAY_NAMES[day], text=value)

    if match := _MONTHLY.match(value):
        dom = int(match.group("dom"))
        if not 1 <= dom <= 31:
            raise ScheduleError(f"{value!r}: day of month must be 1-31, got {dom}")
        hour, minute = _time_of(match, value)
        return Schedule("monthly", hour=hour, minute=minute, day=dom, text=value)

    if match := _EVERY.match(value):
        count = int(match.group("count"))
        if count < 1:
            raise ScheduleError(f"{value!r}: interval must be at least 1")
        unit = match.group("unit").lower()
        delta = timedelta(hours=count) if unit == "h" else timedelta(minutes=count)
        if delta < timedelta(minutes=5):
            raise ScheduleError(f"{value!r}: intervals under 5 minutes are not allowed")
        return Schedule("interval", interval=delta, text=value)

    raise ScheduleError(
        f"{value!r} is not a schedule. Use one of: 'daily HH:MM', "
        f"'weekly <day> HH:MM', 'monthly <day-of-month> HH:MM', "
        f"'every <N>h', 'every <N>m', or 'manual'.")


def _time_of(match: re.Match[str], value: str) -> tuple[int, int]:
    hour, minute = int(match.group("hour")), int(match.group("minute"))
    if hour > 23 or minute > 59:
        raise ScheduleError(f"{value!r}: {hour:02d}:{minute:02d} is not a valid time")
    return hour, minute
