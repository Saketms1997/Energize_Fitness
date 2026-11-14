"""Pure membership date/state rules (no database)."""
from __future__ import annotations

from datetime import date, timedelta


def end_date_for(start: date, duration_days: int) -> date:
    """Last day covered. A 30-day plan starting on the 1st ends on the 30th (inclusive)."""
    if duration_days <= 0:
        raise ValueError("duration_days must be positive")
    return start + timedelta(days=duration_days - 1)


def subscription_state(status: str, start: date, end: date, today: date, expiring_days: int = 7) -> str:
    """Display state of one subscription: pending/cancelled/upcoming/active/expiring/expired."""
    if status in ("cancelled", "pending"):
        return status
    if today < start:
        return "upcoming"
    if today > end:
        return "expired"
    return "expiring" if (end - today).days <= expiring_days else "active"
