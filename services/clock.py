"""Time helpers. Timestamps are stored as UTC; 'today' is the gym's local calendar day."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    return datetime.now().date()


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start, end) of a local calendar day as UTC-aware datetimes."""
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day + timedelta(days=1), time.min).astimezone()
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def local_range_bounds(first: date, last_inclusive: date) -> tuple[datetime, datetime]:
    return local_day_bounds(first)[0], local_day_bounds(last_inclusive)[1]


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """First and last day of a month."""
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


def to_local_date(value: datetime) -> date:
    return value.astimezone().date()


def add_months(d: date, months: int) -> date:
    """First day of the month that is ``months`` away from ``d``'s month."""
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    return date(y, m + 1, 1)
