from __future__ import annotations

import uuid
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.models import Member, MembershipPlan, Subscription
from services import clock, payment_service, settings_service
from services.errors import BusinessRuleError


def _money(value) -> str:
    return f"INR {value:,.2f}"


def generate_receipt_pdf(session: Session, payment_id: uuid.UUID, folder: Path | None = None) -> Path:
    payment = payment_service.get_payment(session, payment_id)
    if payment.status != "confirmed" or not payment.receipt_no:
        raise BusinessRuleError("Receipts exist only for confirmed payments.")
    member = session.get(Member, payment.member_id)
    sub = session.get(Subscription, payment.subscription_id) if payment.subscription_id else None
    plan = session.get(MembershipPlan, sub.plan_id) if sub else None
    gym = {k: settings_service.get(session, f"gym.{k}") for k in ("name", "address", "phone", "logo_path")}

    folder = folder or get_settings().data_dir / "receipts"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{payment.receipt_no}.pdf"
    width, height = A5
    pdf = canvas.Canvas(str(out), pagesize=A5)
    pdf.setTitle(f"Receipt {payment.receipt_no}")

    y = height - 18 * mm
    logo = get_settings().data_dir / gym["logo_path"] if gym["logo_path"] else None
    if logo and logo.is_file():
        try:
            pdf.drawImage(str(logo), 15 * mm, y - 8 * mm, width=18 * mm, height=18 * mm,
                          preserveAspectRatio=True, mask="auto")
        except Exception:  # noqa: BLE001 - a bad logo must never block a receipt
            pass
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(38 * mm, y, gym["name"])
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(colors.grey)
    pdf.drawString(38 * mm, y - 5 * mm, (gym["address"] or "")[:70])
    pdf.drawString(38 * mm, y - 9.5 * mm, f"Phone: {gym['phone']}" if gym["phone"] else "")
    pdf.setFillColor(colors.black)
    pdf.line(15 * mm, y - 14 * mm, width - 15 * mm, y - 14 * mm)

    title = "REFUND RECEIPT" if payment.kind == "refund" else "PAYMENT RECEIPT"
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(15 * mm, y - 22 * mm, title)
    rows = [
        ("Receipt no.", payment.receipt_no),
        ("Date", clock.to_local_date(payment.paid_at).strftime("%d %b %Y")),
        ("Member", f"{member.full_name} ({member.member_code})"),
        ("Phone", member.phone),
    ]
    if plan and sub:
        rows += [("Plan", plan.name),
                 ("Period", f"{sub.start_date:%d %b %Y} to {sub.end_date:%d %b %Y}")]
    rows += [("Payment method", payment.method), ("Reference", payment.reference or "-"),
             ("Received by", payment.confirmed_by or "-")]
    line_y = y - 31 * mm
    for label, value in rows:
        pdf.setFont("Helvetica", 9)
        pdf.setFillColor(colors.grey)
        pdf.drawString(15 * mm, line_y, label)
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(50 * mm, line_y, str(value))
        line_y -= 6 * mm
    pdf.line(15 * mm, line_y + 2 * mm, width - 15 * mm, line_y + 2 * mm)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(15 * mm, line_y - 7 * mm,
                   f"{'Refunded' if payment.kind == 'refund' else 'Amount paid'}: {_money(payment.amount)}")
    if sub and payment.kind == "payment":
        balance = payment_service.outstanding(session, sub.id)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(15 * mm, line_y - 14 * mm, f"Balance due: {_money(balance)}")
    pdf.setFont("Helvetica-Oblique", 8)
    pdf.setFillColor(colors.grey)
    pdf.drawString(15 * mm, 12 * mm, "Thank you. This is a computer generated receipt.")
    pdf.showPage()
    pdf.save()
    return out
