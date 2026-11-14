from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractItemView, QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QScrollArea, QWidget


def _enclosing_scroll_area(widget: QWidget) -> QScrollArea | None:
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


def _at_edge(view: QAbstractScrollArea, delta_y: int) -> bool:
    bar = view.verticalScrollBar()
    if bar.maximum() <= bar.minimum():
        return True
    return (delta_y > 0 and bar.value() <= bar.minimum()) or (delta_y < 0 and bar.value() >= bar.maximum())


class WheelPassthrough(QObject):
    """Stops inputs and tables from swallowing the mouse wheel so the page keeps scrolling.

    Combo/spin/date boxes only react to the wheel while focused; tables and other scroll views pass the wheel
    on to the enclosing page once they have reached their own top or bottom.
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel or not isinstance(obj, QWidget):
            return False
        target = self._owner(obj)
        if target is None:
            return False
        area = _enclosing_scroll_area(target)
        if area is None:
            return False
        delta_y = event.angleDelta().y()
        if isinstance(target, (QComboBox, QAbstractSpinBox)):
            if target.hasFocus():
                return False
        elif isinstance(target, QAbstractScrollArea):
            if not _at_edge(target, delta_y):
                return False
        # re-deliver the wheel to the page's viewport and swallow the original
        forwarded = QWheelEvent(
            event.position(), event.globalPosition(), event.pixelDelta(), event.angleDelta(),
            event.buttons(), event.modifiers(), event.phase(), event.inverted(), event.source())
        QApplication.sendEvent(area.viewport(), forwarded)
        return True

    @staticmethod
    def _owner(obj: QWidget) -> QWidget | None:
        """The input/table the wheel landed on (tables deliver it via their viewport)."""
        if isinstance(obj, (QComboBox, QAbstractSpinBox)):
            return obj
        parent = obj.parentWidget()
        if isinstance(parent, QAbstractScrollArea) and parent.viewport() is obj:
            return parent if isinstance(parent, QAbstractItemView) else None
        return obj if isinstance(obj, QAbstractItemView) else None
