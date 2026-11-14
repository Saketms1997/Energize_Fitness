from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget


class Page(QWidget):
    """Standard page: title/subtitle on the left, action buttons on the right, content below."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(28, 24, 28, 20)
        self.root.setSpacing(14)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        self.title_label = QLabel(title)
        self.title_label.setObjectName("pageTitle")
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("pageSubtitle")
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        header.addLayout(titles, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        header.addLayout(self.actions)
        self.root.addLayout(header)

    def add_action(self, text: str, handler: Callable[[], None], kind: str = "primary") -> QPushButton:
        button = QPushButton(text)
        if kind != "primary":
            button.setObjectName(kind)
        button.clicked.connect(lambda _=False: handler())
        self.actions.addWidget(button)
        return button

    def refresh(self) -> None:  # called whenever the page is shown
        pass


def scrollable(widget: QWidget, transparent: bool = True) -> QScrollArea:
    """Wrap ``widget`` so it scrolls when space is short instead of being squeezed (which makes rows overlap)."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(widget)
    if transparent:
        area.viewport().setAutoFillBackground(False)
        widget.setAutoFillBackground(False)
    return area


def button_row(*buttons: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    for b in buttons:
        row.addWidget(b)
    row.addStretch(1)
    return row


def make_button(text: str, handler: Callable[[], None], kind: str = "secondary") -> QPushButton:
    button = QPushButton(text)
    if kind != "primary":
        button.setObjectName(kind)
    button.clicked.connect(lambda _=False: handler())
    return button
