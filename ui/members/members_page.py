from __future__ import annotations

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QComboBox, QDateEdit, QFileDialog, QHBoxLayout, QLabel, QLineEdit

from database.connection import session_scope
from services import member_service
from ui.common import confirm, notify, pretty_state, show_error
from ui.members.member_dialogs import MemberDetailDialog, edit_member
from ui.members.registration_dialog import RegistrationDialog
from ui.memberships.renew import renew_member
from ui.widgets.page import Page, button_row, make_button
from ui.widgets.table import DataTable

HEADERS = ["Member ID", "Name", "Phone", "Email", "Joined", "Membership", "Plan", "Expires", "Active"]
MIN_DATE = QDate(2000, 1, 1)


def _optional_date() -> QDateEdit:
    edit = QDateEdit(MIN_DATE)
    edit.setMinimumDate(MIN_DATE)
    edit.setSpecialValueText("Any")
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("dd MMM yyyy")
    return edit


class MembersPage(Page):
    def __init__(self, parent=None):
        super().__init__("Members", "Register, search and manage gym members.", parent)
        self.add_action("Register member", self.register)
        self.add_action("Export Excel", self.export, "secondary")

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, phone, email or member ID")
        self.status = QComboBox()
        for label, value in (("All members", "all"), ("Active membership", "active"), ("Expiring soon", "expiring"),
                             ("Expired", "expired"), ("Payment pending", "pending"), ("No membership", "none"),
                             ("Deactivated", "inactive")):
            self.status.addItem(label, value)
        self.joined_from, self.joined_to = _optional_date(), _optional_date()
        bar.addWidget(self.search, 1)
        bar.addWidget(self.status)
        bar.addWidget(QLabel("Joined from"))
        bar.addWidget(self.joined_from)
        bar.addWidget(QLabel("to"))
        bar.addWidget(self.joined_to)
        self.root.addLayout(bar)

        self.table = DataTable(HEADERS, stretch=1)
        self.root.addWidget(self.table, 1)
        self.count = QLabel("")
        self.count.setObjectName("muted")
        self.toggle_btn = make_button("Deactivate", self.toggle_active)
        self.root.addLayout(button_row(
            make_button("Details", self.details), make_button("Edit", self.edit),
            make_button("Renew membership", self.renew), self.toggle_btn))
        self.root.addWidget(self.count)

        self.search.textChanged.connect(lambda _: self.refresh())
        self.status.currentIndexChanged.connect(lambda _: self.refresh())
        self.joined_from.dateChanged.connect(lambda _: self.refresh())
        self.joined_to.dateChanged.connect(lambda _: self.refresh())
        self.table.row_activated.connect(lambda _: self.details())
        self.table.selection_changed.connect(lambda _: self._sync_toggle())
        self._rows: dict = {}

    def _filters(self) -> dict:
        def pick(edit: QDateEdit):
            return None if edit.date() == MIN_DATE else edit.date().toPython()

        return {"text": self.search.text(), "status": self.status.currentData(),
                "joined_from": pick(self.joined_from), "joined_to": pick(self.joined_to)}

    def refresh(self) -> None:
        try:
            with session_scope() as s:
                rows = member_service.search_members(s, **self._filters())
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        self._rows = {r.id: r for r in rows}
        self.table.set_rows([(r.id, [r.code, r.name, r.phone, r.email, r.joined, pretty_state(r.membership_state)
                                     if r.is_active else "Inactive", r.plan_name, r.expiry, r.is_active])
                             for r in rows])
        self.count.setText(f"{len(rows)} member(s)")
        self._sync_toggle()

    def _selected(self):
        member_id = self.table.selected_id()
        if member_id is None:
            notify("Select a member first.", "info")
        return member_id

    def _sync_toggle(self) -> None:
        row = self._rows.get(self.table.selected_id())
        self.toggle_btn.setText("Activate" if row is not None and not row.is_active else "Deactivate")

    def register(self) -> None:
        if RegistrationDialog(self).exec():
            self.refresh()

    def details(self) -> None:
        if (mid := self._selected()) is not None:
            MemberDetailDialog(mid, self).exec()

    def edit(self) -> None:
        if (mid := self._selected()) is not None and edit_member(self, mid):
            self.refresh()

    def renew(self) -> None:
        if (mid := self._selected()) is not None and renew_member(self, mid):
            self.refresh()

    def toggle_active(self) -> None:
        mid = self._selected()
        if mid is None:
            return
        row = self._rows[mid]
        want = not row.is_active
        verb = "Activate" if want else "Deactivate"
        if not confirm(self, f"{verb} member", f"{verb} {row.name}?" + ("" if want else
                       "\nThey will not be able to check in. History is kept."), verb):
            return
        try:
            with session_scope() as s:
                member_service.set_member_active(s, mid, want)
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        notify(f"{row.name} {'activated' if want else 'deactivated'}.")
        self.refresh()

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export members", "members.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            with session_scope() as s:
                member_service.export_members_xlsx(member_service.search_members(s, **self._filters()), path)
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        notify(f"Exported {len(self._rows)} member(s).")
