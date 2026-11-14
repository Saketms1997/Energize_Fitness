from __future__ import annotations

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QComboBox, QDateEdit, QFileDialog, QHBoxLayout, QLabel

from database.connection import session_scope
from services import report_service as rs, settings_service
from ui.common import fmt, notify, show_error
from ui.widgets.page import Page
from ui.widgets.table import DataTable

# name -> (builder(session, params), parameter kind)
REPORTS = {
    "Daily collection": (lambda s, d: rs.daily_collection(s, d), "day"),
    "Monthly collection": (lambda s, d: rs.monthly_collection(s, d.year, d.month), "month"),
    "Daily attendance": (lambda s, d: rs.attendance_daily(s, d), "day"),
    "Monthly attendance": (lambda s, d: rs.attendance_monthly(s, d.year, d.month), "month"),
    "Trainer attendance (monthly)": (lambda s, d: rs.trainer_attendance_monthly(s, d.year, d.month), "month"),
    "Monthly expenses": (lambda s, d: rs.expenses_monthly(s, d.year, d.month), "month"),
    "Members list": (lambda s, d: rs.members_list(s), "none"),
    "Outstanding payments": (lambda s, d: rs.outstanding_payments(s), "none"),
    "Memberships expiring soon": (lambda s, d: rs.expiring_memberships(s), "none"),
}


class ReportsPage(Page):
    def __init__(self, parent=None):
        super().__init__("Reports", "Preview a report, then export it to Excel or PDF.", parent)
        self.add_action("Export Excel", lambda: self.export("xlsx"), "secondary")
        self.add_action("Export PDF", lambda: self.export("pdf"))
        bar = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItems(list(REPORTS))
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date_label = QLabel("Date")
        bar.addWidget(QLabel("Report"))
        bar.addWidget(self.kind, 1)
        bar.addWidget(self.date_label)
        bar.addWidget(self.date)
        self.root.addLayout(bar)
        self.table = DataTable([""], stretch=None)
        self.root.addWidget(self.table, 1)
        self.footer = QLabel("")
        self.footer.setStyleSheet("font-weight: 600;")
        self.footer.setWordWrap(True)
        self.root.addWidget(self.footer)
        self.kind.currentIndexChanged.connect(lambda _: self._params())
        self.date.dateChanged.connect(lambda _: self.refresh())
        self._table = None
        self._params()

    def _params(self) -> None:
        kind = REPORTS[self.kind.currentText()][1]
        self.date.setVisible(kind != "none")
        self.date_label.setVisible(kind != "none")
        self.date.setDisplayFormat("MMMM yyyy" if kind == "month" else "dd MMM yyyy")
        self.refresh()

    def refresh(self) -> None:
        build = REPORTS[self.kind.currentText()][0]
        try:
            with session_scope() as s:
                self._table = build(s, self.date.date().toPython())
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        t = self._table
        self.table.setColumnCount(len(t.headers))
        self.table.setHorizontalHeaderLabels(t.headers)
        self.table.set_rows([(i, row) for i, row in enumerate(t.rows)])
        self.footer.setText("     ".join(f"{k.strip()}: {v}" for k, v in t.footer))

    def export(self, fmt_name: str) -> None:
        if self._table is None:
            return
        default = f"{self.kind.currentText().replace(' ', '_').lower()}.{fmt_name}"
        path, _ = QFileDialog.getSaveFileName(self, "Export report", default,
                                              "Excel (*.xlsx)" if fmt_name == "xlsx" else "PDF (*.pdf)")
        if not path:
            return
        try:
            if fmt_name == "xlsx":
                rs.export_xlsx(self._table, path)
            else:
                with session_scope() as s:
                    gym = settings_service.get(s, "gym.name")
                rs.export_pdf(self._table, path, gym)
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        notify(f"Report exported to {path}")
