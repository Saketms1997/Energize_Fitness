from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from database.models import Attendance, Expense, Member, Payment, Subscription
from services import clock, membership_service
from services.membership_service import NO_MEMBERSHIP

ZERO = Decimal("0.00")


@dataclass
class DashboardStats:
    total_members: int = 0
    active_members: int = 0
    expired_memberships: int = 0
    expiring_soon: int = 0
    todays_attendance: int = 0
    monthly_revenue: Decimal = ZERO
    outstanding_payments: Decimal = ZERO
    monthly_expenses: Decimal = ZERO
    renewals_this_month: int = 0
    renewal_rate_90d: float | None = None  # None when nothing ended in the window
    status_distribution: dict[str, int] = field(default_factory=dict)
    revenue_by_month: list[tuple[str, Decimal]] = field(default_factory=list)
    attendance_by_day: list[tuple[date, int]] = field(default_factory=list)
    renewals_by_month: list[tuple[str, int]] = field(default_factory=list)

    @property
    def net_revenue(self) -> Decimal:
        return self.monthly_revenue - self.monthly_expenses


def _sum_revenue(session: Session, first: date, last: date) -> Decimal:
    start, end = clock.local_range_bounds(first, last)
    signed = case((Payment.kind == "refund", -Payment.amount), else_=Payment.amount)
    total = session.scalar(select(func.coalesce(func.sum(signed), 0)).where(
        Payment.status == "confirmed", Payment.deleted_at.is_(None),
        Payment.paid_at >= start, Payment.paid_at < end))
    return Decimal(total)


def _month_label(first: date) -> str:
    return first.strftime("%b %Y")


def get_stats(session: Session, today: date | None = None) -> DashboardStats:
    today = today or clock.today()
    stats = DashboardStats()
    members = list(session.execute(
        select(Member.id, Member.is_active).where(Member.deleted_at.is_(None))))
    stats.total_members = len(members)
    states = membership_service.status_map(session, today)
    dist: Counter = Counter()
    for member_id, is_active in members:
        state = states.get(member_id, NO_MEMBERSHIP).state if is_active else "inactive"
        dist[state] += 1
    stats.status_distribution = dict(dist)
    stats.active_members = dist["active"] + dist["expiring"]
    stats.expiring_soon = dist["expiring"]
    stats.expired_memberships = dist["expired"]

    start, end = clock.local_day_bounds(today)
    stats.todays_attendance = session.scalar(select(func.count(func.distinct(Attendance.member_id))).where(
        Attendance.deleted_at.is_(None), Attendance.check_in_at >= start, Attendance.check_in_at < end)) or 0

    first, last = clock.month_bounds(today.year, today.month)
    stats.monthly_revenue = _sum_revenue(session, first, last)
    stats.monthly_expenses = Decimal(session.scalar(select(func.coalesce(func.sum(Expense.amount), 0)).where(
        Expense.deleted_at.is_(None), Expense.expense_date >= first, Expense.expense_date <= last)))

    paid = membership_service.paid_amounts(session)
    for sub_id, due in session.execute(select(Subscription.id, Subscription.amount_due).where(
            Subscription.deleted_at.is_(None), Subscription.status != "cancelled")):
        stats.outstanding_payments += max(ZERO, due - paid.get(sub_id, ZERO))

    stats.renewals_this_month = session.scalar(select(func.count()).select_from(Subscription).where(
        Subscription.deleted_at.is_(None), Subscription.renewed_from_id.is_not(None),
        Subscription.status != "cancelled", Subscription.start_date >= first, Subscription.start_date <= last)) or 0

    window_start = today - timedelta(days=90)
    ended = list(session.scalars(select(Subscription.id).where(
        Subscription.deleted_at.is_(None), Subscription.status == "active",
        Subscription.end_date >= window_start, Subscription.end_date < today)))
    if ended:
        renewed = set(session.scalars(select(Subscription.renewed_from_id).where(
            Subscription.renewed_from_id.in_(ended), Subscription.status != "cancelled",
            Subscription.deleted_at.is_(None))))
        stats.renewal_rate_90d = len(renewed) / len(ended)

    cursor = clock.add_months(today, -5)
    for i in range(6):
        m_first = clock.add_months(cursor, i)
        m_first, m_last = clock.month_bounds(m_first.year, m_first.month)
        stats.revenue_by_month.append((_month_label(m_first), _sum_revenue(session, m_first, m_last)))
        stats.renewals_by_month.append((_month_label(m_first), session.scalar(
            select(func.count()).select_from(Subscription).where(
                Subscription.deleted_at.is_(None), Subscription.renewed_from_id.is_not(None),
                Subscription.status != "cancelled", Subscription.start_date >= m_first,
                Subscription.start_date <= m_last)) or 0))

    day0 = today - timedelta(days=13)
    range_start, range_end = clock.local_range_bounds(day0, today)
    per_day: dict[date, set] = {day0 + timedelta(days=i): set() for i in range(14)}
    for member_id, at in session.execute(select(Attendance.member_id, Attendance.check_in_at).where(
            Attendance.deleted_at.is_(None), Attendance.check_in_at >= range_start,
            Attendance.check_in_at < range_end)):
        per_day.setdefault(clock.to_local_date(at), set()).add(member_id)
    stats.attendance_by_day = [(d, len(v)) for d, v in sorted(per_day.items())]
    return stats
