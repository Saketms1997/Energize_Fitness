from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class ModulePage(QWidget):
    """Page for a module that is not built yet. Says so plainly rather than faking data."""

    def __init__(self, title: str, phase: int, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        note = QLabel(f"This module is scheduled for Phase {phase} and is not available yet.")
        note.setObjectName("pageSubtitle")
        layout.addWidget(heading)
        layout.addWidget(note)
        layout.addStretch(1)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
