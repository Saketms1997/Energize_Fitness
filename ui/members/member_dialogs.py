from __future__ import annotations

import uuid

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget

from database.connection import session_scope
from services import member_service, settings_service
from ui.common import money, notify, pretty_state
from ui.members.registration_dialog import GENDERS, IMAGE_FILTER, member_fields
from ui.widgets.forms import Field, ask_form
from ui.widgets.table import DataTable


def edit_member(parent: QWidget | None, member_id: uuid.UUID) -> bool:
    with session_scope() as s:
        m = member_service.get_member(s, member_id)
        values = {k: getattr(m, k) for k in ("full_name", "phone", "email", "address", "gender", "date_of_joining",
                                              "emergency_contact_name", "emergency_contact_phone", "notes")}
        require_gender = bool(settings_service.get(s, "gym.require_gender"))
        code = m.member_code
    fields = [Field("code", "Member ID", "readonly", value=code)] + member_fields(require_gender, values)

    def submit(v: dict) -> None:
        v = {k: x for k, x in v.items() if k != "code"}
        with session_scope() as s:
            member_service.update_member(s, member_id, **v)

    ok, _ = ask_form(parent, "Edit member", fields, submit)
    if ok:
        notify("Member updated.")
    return ok


class MemberDetailDialog(QDialog):
    def __init__(self, member_id: uuid.UUID, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(900, 600)
        with session_scope() as s:
            h = member_service.member_history(s, member_id)
            m = h["member"]
            photo = member_service.photo_path(m)
            states = {}
            from services import membership_service

            info = membership_service.status_map(s).get(m.id)
            self.setWindowTitle(f"{m.full_name} ({m.member_code})")
            head = [("Member ID", m.member_code), ("Phone", m.phone), ("Email", m.email or "-"),
                    ("Joined", f"{m.date_of_joining:%d %b %Y}"), ("Status", "Active" if m.is_active else "Inactive"),
                    ("Membership", pretty_state(info.state) if info else "No membership"),
                    ("Emergency contact", f"{m.emergency_contact_name or '-'} {m.emergency_contact_phone or ''}"),
                    ("Address", m.address or "-")]
            subs = [(r.id, [r.plan_name, r.start_date, r.end_date, r.amount_due, r.paid, r.outstanding,
                            pretty_state(r.state)]) for r in h["subscriptions"]]
            pays = [(p.id, [p.receipt_no, p.paid_at, p.kind, p.method, p.reference, p.amount, p.status])
                    for p in h["payments"]]
            visits = [(a.id, [a.check_in_at, a.check_out_at,
                              None if a.check_out_at is None else
                              f"{int((a.check_out_at - a.check_in_at).total_seconds() // 60)} min"])
                      for a in h["attendance"]]
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        pic = QLabel()
        pic.setFixedSize(110, 110)
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pic.setStyleSheet("background:#1e2230; border-radius:12px;")
        if photo:
            pix = QPixmap(str(photo))
            pic.setPixmap(pix.scaled(106, 106, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation))
        else:
            pic.setText("No photo")
        top.addWidget(pic)
        text = QLabel("<br>".join(f"<span style='color:#8b93ab'>{k}:</span> <b>{v}</b>" for k, v in head))
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        top.addWidget(text, 1)
        layout.addLayout(top)
        tabs = QTabWidget()
        for title, headers, rows in (
            ("Memberships", ["Plan", "Start", "End", "Fee", "Paid", "Outstanding", "State"], subs),
            ("Payments", ["Receipt", "Date", "Type", "Method", "Reference", "Amount", "Status"], pays),
            ("Attendance", ["Check in", "Check out", "Duration"], visits),
        ):
            table = DataTable(headers)
            table.set_rows(rows)
            tabs.addTab(table, f"{title} ({len(rows)})")
        layout.addWidget(tabs, 1)
        close = QPushButton("Close")
        close.setObjectName("secondary")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
