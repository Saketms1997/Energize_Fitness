"""Premium dark theme: design tokens plus one application-wide Qt stylesheet."""
from __future__ import annotations

COLORS = {
    "bg": "#0f1117",
    "surface": "#171a23",
    "surface_alt": "#1e2230",
    "border": "#2a2f42",
    "text": "#e8eaf2",
    "muted": "#8b93ab",
    "accent": "#ff6a3d",
    "accent_hover": "#ff8460",
    "ok": "#2ecc8f",
    "warn": "#f5b83d",
    "error": "#ff5470",
}
FONT_FAMILY = "Segoe UI"


def stylesheet() -> str:
    from config.settings import resource_dir

    c = COLORS
    chevron = (resource_dir() / "assets" / "icons" / "chevron_down.png").as_posix()
    return f"""
* {{ font-family: "{FONT_FAMILY}"; font-size: 10pt; color: {c['text']}; }}
QMainWindow, QWidget#central {{ background: {c['bg']}; }}
QWidget#content {{ background: {c['bg']}; }}
QScrollArea {{ background: transparent; border: none; }}

QFrame#sidebar {{ background: {c['surface']}; border-right: 1px solid {c['border']}; }}
QLabel#brand {{ font-size: 15pt; font-weight: 700; color: {c['text']}; padding: 4px 0; }}
QLabel#brandAccent {{ font-size: 15pt; font-weight: 700; color: {c['accent']}; }}
QLabel#brandSub {{ color: {c['muted']}; font-size: 8pt; letter-spacing: 1px; }}

QPushButton#nav {{
    text-align: left; padding: 8px 16px; border: none; border-radius: 10px;
    color: {c['muted']}; background: transparent; font-size: 10.5pt;
}}
QPushButton#nav:hover {{ background: {c['surface_alt']}; color: {c['text']}; }}
QPushButton#nav:checked {{ background: {c['surface_alt']}; color: {c['accent']}; font-weight: 600; }}

QLabel#pageTitle {{ font-size: 20pt; font-weight: 700; }}
QLabel#pageSubtitle {{ color: {c['muted']}; }}
QLabel#muted {{ color: {c['muted']}; }}

QFrame#card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
QLabel#cardTitle {{ color: {c['muted']}; font-size: 9pt; font-weight: 600; letter-spacing: 1px; }}
QLabel#cardValue {{ font-size: 24pt; font-weight: 700; }}
QLabel#cardCaption {{ color: {c['muted']}; font-size: 8.5pt; }}
QLabel#cardKey {{ color: {c['muted']}; }}
QLabel#cardVal {{ font-weight: 600; }}

QPushButton {{
    background: {c['accent']}; color: #fff; border: none; border-radius: 8px; padding: 8px 16px; font-weight: 600;
    min-height: 20px;
}}
QPushButton:hover {{ background: {c['accent_hover']}; }}
QPushButton#secondary {{ background: {c['surface_alt']}; border: 1px solid {c['border']}; font-weight: 500; }}
QPushButton#secondary:hover {{ border-color: {c['accent']}; }}
QPushButton#danger {{ background: transparent; border: 1px solid {c['error']}; color: {c['error']}; font-weight: 500; }}
QPushButton#danger:hover {{ background: {c['error']}; color: #fff; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 9px 14px; border: none; }}
QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; font-weight: 600; }}
QDialog {{ background: {c['bg']}; }}
QComboBox QAbstractItemView {{ background: {c['surface_alt']}; selection-background-color: {c['accent']}; }}
QProgressBar {{ background: {c['surface_alt']}; border: none; border-radius: 6px; height: 10px; text-align: center; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 6px; }}
QPushButton:disabled {{ background: {c['surface_alt']}; color: {c['muted']}; }}

QLineEdit, QComboBox, QDateEdit, QSpinBox, QTextEdit, QPlainTextEdit {{
    background: {c['surface_alt']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 6px 10px;
    min-height: 22px; selection-background-color: {c['accent']};
}}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus
    {{ border-color: {c['accent']}; }}
QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled {{ color: {c['muted']}; }}
QComboBox, QDateEdit {{ padding-right: 34px; }}
QComboBox::drop-down, QDateEdit::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right; width: 30px; border: none; background: transparent;
}}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url({chevron}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {c['surface_alt']}; border: 1px solid {c['border']}; padding: 4px; outline: 0;
    selection-background-color: {c['accent']};
}}
QCheckBox {{ spacing: 8px; }}
QCalendarWidget QWidget {{ background: {c['surface_alt']}; }}

QTableView, QTableWidget {{
    background: {c['surface']}; alternate-background-color: {c['surface_alt']};
    border: 1px solid {c['border']}; border-radius: 10px; gridline-color: transparent;
    selection-background-color: {c['accent']};
}}
QHeaderView::section {{
    background: {c['surface_alt']}; color: {c['muted']}; border: none; padding: 8px 10px; font-weight: 600;
}}
QTableWidget::item {{ padding: 4px 10px; }}
QDateEdit {{ min-width: 150px; }}

QStatusBar {{ background: {c['surface']}; border-top: 1px solid {c['border']}; color: {c['muted']}; }}
QStatusBar::item {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QToolTip {{ background: {c['surface_alt']}; color: {c['text']}; border: 1px solid {c['border']}; }}
"""
