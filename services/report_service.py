"""Tabular reports with Excel (openpyxl) and PDF (ReportLab) export."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.orm import Session

from services import (attendance_service, clock, expense_service, member_service, payment_service,
                      settings_service)


@dataclass
class ReportTable:
    title: str
    headers: list[str]
    rows: list[list]
    footer: list[tuple[str, str]] = field(default_factory=list)  # label, value summary lines


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    if isinstance(value, datetime):
        return value.astimezone().strftime("%d %b %Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    return str(value)


def _hhmm(minutes: int | None) -> str:
    return "" if minutes is None else f"{minutes // 60}h {minutes % 60:02d}m"


# ---- report builders ----------------------------------------------------------------------

def daily_collection(session: Session, day: date) -> ReportTable:
    rows = payment_service.list_payments(session, "confirmed", first=day, last=day)
    summary = payment_service.collection_summary(session, day, day)
    return ReportTable(
        f"Daily collection - {day:%d %b %Y}",
        ["Receipt", "Member", "Type", "Method", "Reference", "Amount"],
        [[r.receipt_no, f"{r.member_name} ({r.member_code})", r.kind, r.method, r.reference or "",
          r.amount if r.kind == "payment" else -r.amount] for r in rows],
        [("Payments", _fmt(summary.payments_total)), ("Refunds", _fmt(summary.refunds_total)),
         ("Net collection", _fmt(summary.net))] + [(f"  {m}", _fmt(t)) for m, t in summary.by_method.items()])


def monthly_collection(session: Session, year: int, month: int) -> ReportTable:
    first, last = clock.month_bounds(year, month)
    rows = payment_service.list_payments(session, "confirmed", first=first, last=last)
    summary = payment_service.collection_summary(session, first, last)
    return ReportTable(
        f"Monthly collection - {first:%B %Y}",
        ["Date", "Receipt", "Member", "Type", "Method", "Amount"],
        [[r.paid_at, r.receipt_no, f"{r.member_name} ({r.member_code})", r.kind, r.method,
          r.amount if r.kind == "payment" else -r.amount] for r in rows],
        [("Payments", _fmt(summary.payments_total)), ("Refunds", _fmt(summary.refunds_total)),
         ("Net collection", _fmt(summary.net))] + [(f"  {m}", _fmt(t)) for m, t in summary.by_method.items()])


def attendance_daily(session: Session, day: date) -> ReportTable:
    rows = attendance_service.attendance_for_date(session, day)
    return ReportTable(f"Daily attendance - {day:%d %b %Y}",
                       ["Member ID", "Name", "Check in", "Check out", "Duration"],
                       [[r.code, r.name, r.check_in, r.check_out, _hhmm(r.minutes)] for r in rows],
                       [("Visits", str(len(rows)))])


def attendance_monthly(session: Session, year: int, month: int) -> ReportTable:
    rows = attendance_service.monthly_summary(session, year, month)
    return ReportTable(f"Monthly attendance - {date(year, month, 1):%B %Y}",
                       ["Member ID", "Name", "Days present", "Total time"],
                       [[r.code, r.name, r.days_present, _hhmm(r.total_minutes)] for r in rows],
                       [("Members who attended", str(len(rows)))])


def trainer_attendance_monthly(session: Session, year: int, month: int) -> ReportTable:
    rows = attendance_service.trainer_monthly_summary(session, year, month)
    return ReportTable(f"Trainer attendance - {date(year, month, 1):%B %Y}",
                       ["Trainer", "Days present", "Total time"],
                       [[r.name, r.days_present, _hhmm(r.total_minutes)] for r in rows])


def expenses_monthly(session: Session, year: int, month: int) -> ReportTable:
    report = expense_service.monthly_report(session, year, month)
    return ReportTable(f"Monthly expenses - {date(year, month, 1):%B %Y}",
                       ["Date", "Category", "Description", "Method", "Amount"],
                       [[e.expense_date, e.category, e.description or "", e.payment_method, e.amount]
                        for e in report.expenses],
                       [("Total expenses", _fmt(report.total))] + [(f"  {c}", _fmt(t)) for c, t in report.by_category.items()])


def members_list(session: Session, status: str = "all") -> ReportTable:
    rows = member_service.search_members(session, status=status)
    return ReportTable("Members", ["Member ID", "Name", "Phone", "Joined", "Membership", "Expiry"],
                       [[r.code, r.name, r.phone, r.joined, r.membership_state, r.expiry] for r in rows],
                       [("Members", str(len(rows)))])


def outstanding_payments(session: Session) -> ReportTable:
    rows = payment_service.outstanding_subscriptions(session)
    total = sum((r.outstanding for r in rows), Decimal("0.00"))
    return ReportTable("Outstanding payments", ["Member", "Plan", "Period end", "Due", "Paid", "Outstanding"],
                       [[f"{r.member_name} ({r.member_code})", r.plan_name, r.end_date, r.amount_due, r.paid, r.outstanding]
                        for r in rows], [("Total outstanding", _fmt(total))])


def expiring_memberships(session: Session) -> ReportTable:
    from services import membership_service

    rows = membership_service.list_subscriptions(session, "expiring")
    return ReportTable("Memberships expiring soon", ["Member", "Plan", "Expires"],
                       [[f"{r.member_name} ({r.member_code})", r.plan_name, r.end_date] for r in rows])


# ---- exporters ----------------------------------------------------------------------------

def export_xlsx(table: ReportTable, path: Path | str) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = table.title[:31].replace("/", "-")
    ws.append([table.title])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(table.headers)
    for cell in ws[2]:
        cell.font = Font(bold=True)
    for row in table.rows:
        ws.append([v.astimezone().replace(tzinfo=None) if isinstance(v, datetime) else v for v in row])
    ws.append([])
    for label, value in table.footer:
        ws.append([label.strip(), value])
    for idx, header in enumerate(table.headers, 1):
        width = max([len(str(header))] + [len(_fmt(r[idx - 1])) for r in table.rows] or [10])
        ws.column_dimensions[chr(64 + idx)].width = min(max(width + 2, 10), 50)
    path = Path(path)
    wb.save(path)
    return path


def export_pdf(table: ReportTable, path: Path | str, gym_name: str = "") -> Path:
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), title=table.title,
                            leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    story = []
    if gym_name:
        story.append(Paragraph(f"<b>{gym_name}</b>", styles["Title"]))
    story += [Paragraph(table.title, styles["Heading2"]),
              Paragraph(f"Generated {datetime.now():%d %b %Y %H:%M}", styles["Normal"]), Spacer(1, 10)]
    data = [table.headers] + [[_fmt(v) for v in row] for row in table.rows] or [table.headers]
    grid = Table(data, repeatRows=1)
    grid.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ff6a3d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f8")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9cde0")),
    ]))
    story.append(grid)
    if table.footer:
        story.append(Spacer(1, 12))
        for label, value in table.footer:
            story.append(Paragraph(f"<b>{label.strip()}</b>: {value}", styles["Normal"]))
    doc.build(story)
    return Path(path)
