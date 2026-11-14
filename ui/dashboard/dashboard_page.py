from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from database.connection import ConnectionStatus, session_scope
from services import dashboard_service
from ui.async_utils import run_async
from ui.common import money, show_error
from ui.widgets.cards import KeyValueCard, StatCard
from ui.widgets.charts import ChartCanvas

_CARDS = [
    ("Total members", "Registered, not deleted"),
    ("Active members", "Membership running today"),
    ("Expired memberships", "Lapsed, not renewed"),
    ("Expiring soon", "Inside the alert window"),
    ("Today's attendance", "Distinct members checked in"),
    ("Monthly revenue", "Collected this month, net of refunds"),
    ("Outstanding payments", "Fees still to collect"),
    ("Monthly expenses", "Spent this month"),
    ("Net revenue", "Revenue minus expenses"),
]
_DB_ROWS = ["Status", "Database", "Server version", "Latency", "Schema revision", "Last checked"]


class DashboardPage(QWidget):
    def __init__(self, on_refresh, parent: QWidget | None = None):
        super().__init__(parent)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("content")
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(18)

        title = QLabel("Dashboard")
        title.setObjectName("pageTitle")
        self.subtitle = QLabel("Overview of your gym.")
        self.subtitle.setObjectName("pageSubtitle")
        layout.addWidget(title)
        layout.addWidget(self.subtitle)
        self.alert = QLabel("")
        self.alert.setStyleSheet("color:#f5b83d; font-weight:600;")
        layout.addWidget(self.alert)

        grid = QGridLayout()
        grid.setSpacing(16)
        self.cards: dict[str, StatCard] = {}
        for i, (name, caption) in enumerate(_CARDS):
            card = StatCard(name, "-", caption)
            self.cards[name] = card
            grid.addWidget(card, i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)

        self.renewals = StatCard("Membership renewals", "-", "")
        layout.addWidget(self.renewals)

        self.revenue_chart = ChartCanvas("Revenue - last 6 months")
        self.attendance_chart = ChartCanvas("Attendance - last 14 days")
        self.status_chart = ChartCanvas("Members by membership state")
        self.renewal_chart = ChartCanvas("Renewals - last 6 months")
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        row1.setSpacing(16)
        row2.setSpacing(16)
        row1.addWidget(self.revenue_chart, 1)
        row1.addWidget(self.attendance_chart, 1)
        row2.addWidget(self.status_chart, 1)
        row2.addWidget(self.renewal_chart, 1)
        layout.addLayout(row1)
        layout.addLayout(row2)

        self.db_card = KeyValueCard("Database connection", _DB_ROWS)
        layout.addWidget(self.db_card)
        refresh = QPushButton("Re-check connection")
        refresh.setObjectName("secondary")
        refresh.clicked.connect(on_refresh)
        layout.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        self.stats = None

    def refresh(self) -> None:
        def load():
            with session_scope() as s:
                return dashboard_service.get_stats(s)

        run_async(load, self.show_stats, lambda exc: show_error(self, exc, "Dashboard"))

    def show_stats(self, st: dashboard_service.DashboardStats) -> None:
        self.stats = st
        values = {
            "Total members": str(st.total_members), "Active members": str(st.active_members),
            "Expired memberships": str(st.expired_memberships), "Expiring soon": str(st.expiring_soon),
            "Today's attendance": str(st.todays_attendance), "Monthly revenue": money(st.monthly_revenue),
            "Outstanding payments": money(st.outstanding_payments), "Monthly expenses": money(st.monthly_expenses),
            "Net revenue": money(st.net_revenue),
        }
        for name, value in values.items():
            self.cards[name].set_value(value)
        rate = ("no memberships ended in the last 90 days" if st.renewal_rate_90d is None else
                f"{st.renewal_rate_90d:.0%} of memberships that ended in the last 90 days were renewed")
        self.renewals.set_value(f"{st.renewals_this_month} this month", rate)
        self.alert.setText(f"{st.expiring_soon} membership(s) expiring soon." if st.expiring_soon else "")
        self.revenue_chart.bar([m for m, _ in st.revenue_by_month], [float(v) for _, v in st.revenue_by_month],
                               money=True)
        self.attendance_chart.line([f"{d:%d %b}" for d, _ in st.attendance_by_day],
                                   [n for _, n in st.attendance_by_day])
        labels = {"active": "Active", "expiring": "Expiring", "expired": "Expired", "pending": "Awaiting payment",
                  "upcoming": "Upcoming", "none": "No membership", "inactive": "Deactivated"}
        self.status_chart.donut({labels.get(k, k): v for k, v in st.status_distribution.items() if v})
        self.renewal_chart.bar([m for m, _ in st.renewals_by_month], [n for _, n in st.renewals_by_month])

    def show_status(self, status: ConnectionStatus) -> None:
        card = self.db_card
        card.dot.set_state("ok" if status.ok else "error")
        card.set_value("Status", status.message)
        card.set_value("Database", status.database or "-")
        card.set_value("Server version", status.server_version or "-")
        card.set_value("Latency", f"{status.latency_ms:.0f} ms" if status.latency_ms is not None else "-")
        card.set_value("Schema revision", status.schema_revision or "-")
        card.set_value("Last checked", datetime.now().strftime("%H:%M:%S"))
