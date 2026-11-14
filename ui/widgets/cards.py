from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ui.theme import COLORS


class StatCard(QFrame):
    """Headline-number card used on the dashboard."""

    def __init__(self, title: str, value: str = "-", caption: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumHeight(118)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._title = QLabel(title.upper())
        self._title.setObjectName("cardTitle")
        self._value = QLabel(value)
        self._value.setObjectName("cardValue")
        self._caption = QLabel(caption)
        self._caption.setObjectName("cardCaption")
        self._caption.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(4)
        layout.addWidget(self._title)
        layout.addWidget(self._value)
        layout.addWidget(self._caption)
        layout.addStretch(1)

    def set_value(self, value: str, caption: str | None = None) -> None:
        self._value.setText(value)
        if caption is not None:
            self._caption.setText(caption)


class StatusDot(QWidget):
    """Small coloured circle: ok / error / pending."""

    _STATE = {"ok": COLORS["ok"], "error": COLORS["error"], "pending": COLORS["warn"]}

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._state = "pending"

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    @property
    def state(self) -> str:
        return self._state

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._STATE[self._state]))
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


class KeyValueCard(QFrame):
    """Card listing label/value rows (used for the database status panel)."""

    def __init__(self, title: str, rows: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        header = QHBoxLayout()
        self.dot = StatusDot()
        heading = QLabel(title.upper())
        heading.setObjectName("cardTitle")
        header.addWidget(self.dot)
        header.addWidget(heading)
        header.addStretch(1)
        outer.addLayout(header)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        self._values: dict[str, QLabel] = {}
        for i, name in enumerate(rows):
            key = QLabel(name)
            key.setObjectName("cardKey")
            val = QLabel("-")
            val.setObjectName("cardVal")
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            val.setWordWrap(True)
            grid.addWidget(key, i, 0)
            grid.addWidget(val, i, 1)
            self._values[name] = val
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)

    def set_value(self, name: str, value: str) -> None:
        self._values[name].setText(value)
