from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QComboBox

from database.connection import session_scope
from services import trainer_service
from ui.common import notify, show_error
from ui.widgets.forms import Field, ask_form
from ui.widgets.page import Page, button_row, make_button
from ui.widgets.table import DataTable


class TrainersPage(Page):
    def __init__(self, parent=None):
        super().__init__("Trainers", "Trainer profiles. Use the Attendance page to check trainers in and out.", parent)
        self.add_action("Add trainer", self.add)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, phone or specialization")
        self.active = QComboBox()
        for label, value in (("All", None), ("Active", True), ("Inactive", False)):
            self.active.addItem(label, value)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.active)
        self.root.addLayout(bar)
        self.table = DataTable(["Name", "Phone", "Email", "Specialization", "Joined", "Active"], stretch=0)
        self.root.addWidget(self.table, 1)
        self.toggle_btn = make_button("Deactivate", self.toggle)
        self.root.addLayout(button_row(make_button("Edit", self.edit), self.toggle_btn))
        self.search.textChanged.connect(lambda _: self.refresh())
        self.active.currentIndexChanged.connect(lambda _: self.refresh())
        self.table.row_activated.connect(lambda _: self.edit())
        self.table.selection_changed.connect(lambda _: self._sync())
        self._rows: dict = {}

    def refresh(self) -> None:
        try:
            with session_scope() as s:
                rows = trainer_service.list_trainers(s, self.search.text(), self.active.currentData())
                self._rows = {t.id: (t.full_name, t.phone, t.email, t.specialization, t.joining_date, t.is_active)
                              for t in rows}
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        self.table.set_rows([(tid, list(v)) for tid, v in self._rows.items()])
        self._sync()

    def _sync(self) -> None:
        row = self._rows.get(self.table.selected_id())
        self.toggle_btn.setText("Activate" if row is not None and not row[5] else "Deactivate")

    def _fields(self, v: dict | None = None) -> list[Field]:
        v = v or {}
        return [Field("full_name", "Full name", required=True, value=v.get("full_name")),
                Field("phone", "Phone number", required=True, value=v.get("phone")),
                Field("email", "Email", value=v.get("email")),
                Field("specialization", "Specialization", value=v.get("specialization")),
                Field("joining_date", "Joining date", "date", value=v.get("joining_date") or date.today())]

    def add(self) -> None:
        def submit(v: dict) -> None:
            with session_scope() as s:
                trainer_service.register_trainer(s, **v)

        ok, _ = ask_form(self, "Add trainer", self._fields(), submit)
        if ok:
            notify("Trainer added.")
            self.refresh()

    def edit(self) -> None:
        tid = self.table.selected_id()
        if tid is None:
            return notify("Select a trainer first.", "info")
        name, phone, email, spec, joined, _ = self._rows[tid]

        def submit(v: dict) -> None:
            with session_scope() as s:
                trainer_service.update_trainer(s, tid, **v)

        ok, _ = ask_form(self, "Edit trainer", self._fields(
            {"full_name": name, "phone": phone, "email": email, "specialization": spec, "joining_date": joined}), submit)
        if ok:
            notify("Trainer updated.")
            self.refresh()

    def toggle(self) -> None:
        tid = self.table.selected_id()
        if tid is None:
            return notify("Select a trainer first.", "info")
        try:
            with session_scope() as s:
                trainer_service.set_trainer_active(s, tid, not self._rows[tid][5])
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        self.refresh()
