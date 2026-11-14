"""Dark-themed interactive Matplotlib charts (hover for exact values)."""
from __future__ import annotations

from typing import Sequence

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea, QSizePolicy  # noqa: E402

from ui.theme import COLORS  # noqa: E402

PALETTE = ["#ff6a3d", "#2ecc8f", "#f5b83d", "#5b8cff", "#b06bff", "#ff5470", "#8b93ab"]


class ChartCanvas(FigureCanvasQTAgg):
    def __init__(self, title: str, parent=None):
        self.figure = Figure(figsize=(4.2, 2.8), facecolor=COLORS["surface"])
        super().__init__(self.figure)
        self.setParent(parent)
        self.setMinimumHeight(240)
        self.setStyleSheet(f"background: {COLORS['surface']};")  # no light seam between neighbouring charts
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._title = title
        self._targets: list[tuple[object, str]] = []   # (matplotlib artist, tooltip text)
        self._note = None
        self.mpl_connect("motion_notify_event", self._hover)
        self.mpl_connect("figure_leave_event", lambda _e: self._hide_note())

    def wheelEvent(self, event) -> None:  # Matplotlib swallows the wheel; hand it to the page so it keeps scrolling
        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if parent is None:
            event.ignore()
            return
        QApplication.sendEvent(parent.viewport(), QWheelEvent(
            event.position(), event.globalPosition(), event.pixelDelta(), event.angleDelta(), event.buttons(),
            event.modifiers(), event.phase(), event.inverted(), event.source()))
        event.accept()

    # ---- drawing ---------------------------------------------------------------------------
    def _axes(self):
        self.figure.clear()
        self._targets, self._note = [], None
        ax = self.figure.add_subplot(111, facecolor=COLORS["surface"])
        ax.set_title(self._title, color=COLORS["text"], fontsize=10, loc="left", fontweight="bold", pad=10)
        for spine in ax.spines.values():
            spine.set_color(COLORS["border"])
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(colors=COLORS["muted"], labelsize=8)
        return ax

    def _finish(self, ax) -> None:
        self._note = ax.annotate("", xy=(0, 0), xytext=(12, 12), textcoords="offset points", color=COLORS["text"],
                                 fontsize=8, bbox=dict(boxstyle="round,pad=0.4", fc=COLORS["surface_alt"],
                                                       ec=COLORS["accent"]), visible=False, zorder=10)
        self.figure.tight_layout()
        self.draw_idle()

    def bar(self, labels: Sequence[str], values: Sequence[float], money: bool = False) -> None:
        ax = self._axes()
        bars = ax.bar(range(len(values)), values, color=COLORS["accent"], width=0.6)
        ax.set_xticks(range(len(labels)), labels, rotation=0)
        ax.set_ylim(0, max(max(values, default=0) * 1.1, 1))  # an all-zero chart still gets a sane axis
        if not money:
            ax.yaxis.get_major_locator().set_params(integer=True)
        ax.grid(axis="y", color=COLORS["border"], linewidth=0.5)
        ax.set_axisbelow(True)
        for rect, label, value in zip(bars, labels, values):
            text = f"{label}: ₹{value:,.2f}" if money else f"{label}: {value:g}"
            self._targets.append((rect, text))
        self._finish(ax)

    def line(self, labels: Sequence[str], values: Sequence[float]) -> None:
        ax = self._axes()
        xs = list(range(len(values)))
        ax.plot(xs, values, color=COLORS["accent"], linewidth=2, marker="o", markersize=4)
        ax.fill_between(xs, values, color=COLORS["accent"], alpha=0.12)
        step = max(1, len(labels) // 7)
        ax.set_xticks(xs[::step], [labels[i] for i in xs[::step]])
        ax.set_ylim(bottom=0)
        ax.yaxis.get_major_locator().set_params(integer=True)
        ax.grid(axis="y", color=COLORS["border"], linewidth=0.5)
        ax.set_axisbelow(True)
        for x, label, value in zip(xs, labels, values):
            self._targets.append(((x, value), f"{label}: {value:g}"))
        self._finish(ax)

    def donut(self, data: dict[str, int]) -> None:
        ax = self._axes()
        ax.spines["left"].set_visible(False)
        ax.spines["bottom"].set_visible(False)
        total = sum(data.values())
        if total == 0:
            ax.text(0.5, 0.5, "No data yet", ha="center", va="center", color=COLORS["muted"],
                    transform=ax.transAxes)
            ax.set_xticks([]), ax.set_yticks([])
            self._finish(ax)
            return
        wedges, _ = ax.pie(list(data.values()), colors=PALETTE[: len(data)], startangle=90, counterclock=False,
                           wedgeprops=dict(width=0.38, edgecolor=COLORS["surface"], linewidth=2))
        ax.legend(wedges, [f"{k} ({v})" for k, v in data.items()], loc="center left", bbox_to_anchor=(1.0, 0.5),
                  frameon=False, labelcolor=COLORS["text"], fontsize=8)
        ax.text(0, 0, str(total), ha="center", va="center", color=COLORS["text"], fontsize=18, fontweight="bold")
        for wedge, (name, value) in zip(wedges, data.items()):
            self._targets.append((wedge, f"{name}: {value} ({value / total:.0%})"))
        self._finish(ax)

    # ---- hover -----------------------------------------------------------------------------
    def _hide_note(self) -> None:
        if self._note is not None and self._note.get_visible():
            self._note.set_visible(False)
            self.draw_idle()

    def _hover(self, event) -> None:
        if self._note is None or event.inaxes is None:
            return self._hide_note()
        for artist, text in self._targets:
            hit = False
            if isinstance(artist, tuple):
                x, y = artist
                px, py = event.inaxes.transData.transform((x, y))
                hit = abs(px - event.x) < 12 and abs(py - event.y) < 30
                anchor = (x, y)
            else:
                hit = artist.contains(event)[0]
                anchor = (event.xdata, event.ydata)
            if hit:
                self._note.xy = anchor
                self._note.set_text(text)
                self._note.set_visible(True)
                self.draw_idle()
                return
        self._hide_note()
