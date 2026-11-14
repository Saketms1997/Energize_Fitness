from __future__ import annotations

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QComboBox, QCompleter, QDateEdit, QHBoxLayout, QLabel, QLineEdit, QTabWidget,
                               QVBoxLayout, QWidget)

from database.connection import session_scope
from services import attendance_service as att, member_service, trainer_service
from services.attendance_service import NoActiveMembershipError
from ui.common import confirm, notify, show_error
from ui.widgets.page import Page, make_button
from ui.widgets.table import DataTable


def _month_edit() -> QDateEdit:
    edit = QDateEdit(QDate.currentDate())
    edit.setDisplayFormat("MMMM yyyy")
    edit.setCalendarPopup(True)
    return edit


class _Panel(QWidget):
    """Check-in/out, a day list and a monthly summary for either members or trainers."""

    def __init__(self, trainers: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.trainers = trainers
        noun = "trainer" if trainers else "member"
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.person = QComboBox()
        self.person.setEditable(True)
        self.person.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.person.lineEdit().setPlaceholderText(f"Type to search {noun}...")
        completer = QCompleter(self.person.model(), self.person)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.person.setCompleter(completer)
        self.person.setMinimumWidth(320)
        top.addWidget(self.person, 1)
        top.addWidget(make_button("Check in", self.check_in, "primary"))
        top.addWidget(make_button("Check out", self.check_out))
        self.inside = QLabel("")
        self.inside.setObjectName("muted")
        top.addWidget(self.inside)
        layout.addLayout(top)

        day_bar = QHBoxLayout()
        self.day = QDateEdit(QDate.currentDate())
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("dd MMM yyyy")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter this day's list by name")
        day_bar.addWidget(QLabel("Date"))
        day_bar.addWidget(self.day)
        day_bar.addWidget(self.search, 1)
        layout.addLayout(day_bar)
        self.table = DataTable(["ID", "Name", "Check in", "Check out", "Duration"], stretch=1)
        layout.addWidget(self.table, 2)

        month_bar = QHBoxLayout()
        self.month = _month_edit()
        month_bar.addWidget(QLabel("Monthly summary"))
        month_bar.addWidget(self.month)
        month_bar.addStretch(1)
        layout.addLayout(month_bar)
        self.summary = DataTable(["ID", "Name", "Days present", "Total time"], stretch=1)
        layout.addWidget(self.summary, 1)

        self.day.dateChanged.connect(lambda _: self.refresh_lists())
        self.search.textChanged.connect(lambda _: self.refresh_lists())
        self.month.dateChanged.connect(lambda _: self.refresh_lists())

    def refresh(self) -> None:
        with session_scope() as s:
            if self.trainers:
                people = [(t.id, t.full_name) for t in trainer_service.list_trainers(s, active=True)]
            else:
                people = [(r.id, f"{r.code} - {r.name}") for r in member_service.search_members(s)
                          if r.is_active]
        current = self.person.currentData()
        self.person.clear()
        for pid, label in people:
            self.person.addItem(label, pid)
        if current is None:
            self.person.setCurrentIndex(-1)
            self.person.setEditText("")
        self.refresh_lists()

    def refresh_lists(self) -> None:
        day, month = self.day.date().toPython(), self.month.date().toPython()
        try:
            with session_scope() as s:
                if self.trainers:
                    rows = att.trainer_attendance_for_date(s, day, self.search.text())
                    summary = att.trainer_monthly_summary(s, month.year, month.month)
                    inside = ""
                else:
                    rows = att.attendance_for_date(s, day, self.search.text())
                    summary = att.monthly_summary(s, month.year, month.month)
                    inside = f"Inside now: {len(att.currently_checked_in(s))}"
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        self.inside.setText(inside)
        self.table.set_rows([(r.id, [r.code, r.name, r.check_in, r.check_out,
                                     "inside" if r.minutes is None else f"{r.minutes // 60}h {r.minutes % 60:02d}m"])
                             for r in rows])
        self.summary.set_rows([(r.person_id, [r.code, r.name, r.days_present,
                                              f"{r.total_minutes // 60}h {r.total_minutes % 60:02d}m"]) for r in summary])

    def _selected(self):
        idx = self.person.currentIndex()
        if idx < 0 or self.person.currentText() != self.person.itemText(idx):
            notify(f"Choose a {'trainer' if self.trainers else 'member'} from the list.", "info")
            return None
        return self.person.itemData(idx), self.person.currentText()

    def check_in(self) -> None:
        picked = self._selected()
        if picked is None:
            return
        pid, label = picked
        try:
            try:
                with session_scope() as s:
                    (att.trainer_check_in if self.trainers else att.check_in)(s, pid)
            except NoActiveMembershipError as exc:
                if not confirm(self, "No active membership", f"{exc}\n\nAllow this check-in anyway?", "Allow"):
                    return
                with session_scope() as s:
                    att.check_in(s, pid, allow_without_membership=True)
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc, "Cannot check in")
        notify(f"{label} checked in.")
        self.refresh_lists()

    def check_out(self) -> None:
        picked = self._selected()
        if picked is None:
            return
        pid, label = picked
        try:
            with session_scope() as s:
                (att.trainer_check_out if self.trainers else att.check_out)(s, pid)
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc, "Cannot check out")
        notify(f"{label} checked out.")
        self.refresh_lists()


class AttendancePage(Page):
    def __init__(self, parent=None):
        super().__init__("Attendance", "Check members and trainers in and out; review daily and monthly records.", parent)
        self.tabs = QTabWidget()
        self.members, self.trainers = _Panel(False), _Panel(True)
        self.tabs.addTab(self.members, "Members")
        self.tabs.addTab(self.trainers, "Trainers")
        self.root.addWidget(self.tabs, 1)
        self.tabs.currentChanged.connect(lambda _: self.refresh())

    def refresh(self) -> None:
        self.tabs.currentWidget().refresh()
