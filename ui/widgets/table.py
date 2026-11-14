from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from ui.common import fmt

ID_ROLE = Qt.ItemDataRole.UserRole
SORT_ROLE = Qt.ItemDataRole.UserRole + 1


class _Item(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:  # numeric/date aware sorting
        a, b = self.data(SORT_ROLE), other.data(SORT_ROLE)
        try:
            return a < b
        except TypeError:
            return self.text() < other.text()


class DataTable(QTableWidget):
    """Read-only, sortable table whose rows carry an id (any object) for the owner's selection."""

    row_activated = Signal(object)   # double-click / Enter: row id
    selection_changed = Signal(object)  # row id or None

    def __init__(self, headers: Sequence[str], stretch: int | None = None, parent=None):
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(list(headers))
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(36)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(stretch is None)
        if stretch is not None:
            header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
        self.setSortingEnabled(True)
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)  # keep the service's order until a header is clicked
        self.itemDoubleClicked.connect(lambda item: self.row_activated.emit(self._id_of(item.row())))
        self.itemSelectionChanged.connect(lambda: self.selection_changed.emit(self.selected_id()))

    def _id_of(self, row: int) -> Any:
        item = self.item(row, 0)
        return item.data(ID_ROLE) if item else None

    def set_rows(self, rows: Sequence[tuple[Any, Sequence[Any]]]) -> None:
        """``rows`` = [(row_id, [cell values...])]. Selection is kept when the id still exists."""
        keep = self.selected_id()
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        for r, (row_id, cells) in enumerate(rows):
            for c, value in enumerate(cells):
                item = _Item(fmt(value))
                item.setData(ID_ROLE, row_id)
                item.setData(SORT_ROLE, value if value is not None else "")
                if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(r, c, item)
        self.setSortingEnabled(True)
        # Header text lines up with its data: right for number columns, left otherwise.
        for c in range(self.columnCount()):
            numeric = any(isinstance(cells[c], (int, float, Decimal)) and not isinstance(cells[c], bool)
                          for _, cells in rows if c < len(cells))
            head = self.horizontalHeaderItem(c)
            if head is not None:
                head.setTextAlignment((Qt.AlignmentFlag.AlignRight if numeric else Qt.AlignmentFlag.AlignLeft)
                                      | Qt.AlignmentFlag.AlignVCenter)
        if keep is not None:
            self.select_id(keep)

    def selected_id(self) -> Any:
        rows = self.selectionModel().selectedRows() if self.selectionModel() else []
        return self._id_of(rows[0].row()) if rows else None

    def select_id(self, row_id: Any) -> None:
        for r in range(self.rowCount()):
            if self._id_of(r) == row_id:
                self.selectRow(r)
                return

    def row_count(self) -> int:
        return self.rowCount()

    def cell(self, row: int, col: int) -> str:
        item = self.item(row, col)
        return item.text() if item else ""
