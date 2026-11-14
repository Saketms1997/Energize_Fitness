from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QComboBox, QDateEdit, QHBoxLayout, QLabel

from database.connection import session_scope
from services import expense_service, settings_service
from ui.common import confirm, money, notify, show_error
from ui.widgets.forms import Field, ask_form
from ui.widgets.page import Page, button_row, make_button
from ui.widgets.table import DataTable


class ExpensesPage(Page):
    def __init__(self, parent=None):
        super().__init__("Expenses", "Record what the gym spends and review it month by month.", parent)
        self.add_action("Add expense", self.add)
        bar = QHBoxLayout()
        self.month = QDateEdit(QDate.currentDate())
        self.month.setDisplayFormat("MMMM yyyy")
        self.month.setCalendarPopup(True)
        self.category = QComboBox()
        bar.addWidget(QLabel("Month"))
        bar.addWidget(self.month)
        bar.addWidget(self.category)
        bar.addStretch(1)
        self.root.addLayout(bar)
        self.total = QLabel("")
        self.total.setStyleSheet("font-size: 13pt; font-weight: 600;")
        self.root.addWidget(self.total)
        self.table = DataTable(["Date", "Category", "Description", "Method", "Amount"], stretch=2)
        self.root.addWidget(self.table, 1)
        self.root.addLayout(button_row(make_button("Edit", self.edit), make_button("Delete", self.delete, "danger")))
        self.month.dateChanged.connect(lambda _: self.refresh())
        self.category.currentIndexChanged.connect(lambda _: self.refresh())
        self._rows: dict = {}
        self._loading = False

    def refresh(self) -> None:
        d = self.month.date().toPython()
        first, last = d.replace(day=1), d.replace(day=28)
        try:
            with session_scope() as s:
                report = expense_service.monthly_report(s, d.year, d.month)
                cats = settings_service.get(s, "expense.categories")
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        picked = self.category.currentData()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("All categories", None)
        for c in sorted(set(cats) | set(report.by_category)):
            self.category.addItem(c, c)
        idx = self.category.findData(picked)
        self.category.setCurrentIndex(max(idx, 0))
        self.category.blockSignals(False)
        chosen = self.category.currentData()
        rows = [e for e in report.expenses if chosen is None or e.category == chosen]
        self._rows = {e.id: e for e in rows}
        self.table.set_rows([(e.id, [e.expense_date, e.category, e.description, e.payment_method, e.amount])
                             for e in rows])
        breakdown = "   ".join(f"{c}: {money(t)}" for c, t in report.by_category.items())
        self.total.setText(f"Total {money(report.total)}   {breakdown}")

    def _fields(self, e=None) -> list[Field]:
        with session_scope() as s:
            cats = settings_service.get(s, "expense.categories")
            methods = settings_service.get(s, "payment.methods")
        return [Field("category", "Category", "choice", True, e.category if e else cats[0], cats, editable_choice=True),
                Field("amount", "Amount", required=True, value=f"{e.amount:.2f}" if e else ""),
                Field("expense_date", "Date", "date", value=e.expense_date if e else date.today()),
                Field("payment_method", "Paid by", "choice", True, e.payment_method if e else methods[0], methods),
                Field("description", "Description", "multiline", value=e.description if e else "")]

    def add(self) -> None:
        def submit(v: dict) -> None:
            with session_scope() as s:
                expense_service.add_expense(s, v["category"], v["amount"], v["expense_date"], v["payment_method"],
                                            v["description"])

        ok, _ = ask_form(self, "Add expense", self._fields(), submit)
        if ok:
            notify("Expense recorded.")
            self.refresh()

    def edit(self) -> None:
        e = self._rows.get(self.table.selected_id())
        if e is None:
            return notify("Select an expense first.", "info")

        def submit(v: dict) -> None:
            with session_scope() as s:
                expense_service.update_expense(s, e.id, **v)

        ok, _ = ask_form(self, "Edit expense", self._fields(e), submit)
        if ok:
            notify("Expense updated.")
            self.refresh()

    def delete(self) -> None:
        e = self._rows.get(self.table.selected_id())
        if e is None:
            return notify("Select an expense first.", "info")
        if not confirm(self, "Delete expense", f"Delete {e.category} expense of {money(e.amount)}?\n"
                                                "It is removed from totals but kept in the audit log.", "Delete"):
            return
        try:
            with session_scope() as s:
                expense_service.delete_expense(s, e.id)
        except Exception as exc:  # noqa: BLE001
            return show_error(self, exc)
        notify("Expense deleted.")
        self.refresh()
