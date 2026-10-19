"""Dark engineering theme: charcoal panels, a blue-grey viewport gradient and a single blue accent, in the spirit of professional CAD
packages. Colours are defined once here and used by the Qt style sheet and by the 3D viewport."""
from __future__ import annotations

PALETTE = dict(
    window="#2b2e34", panel="#33373e", panel_alt="#3a3f47", ribbon="#3a3f47", ribbon_tab="#2b2e34", field="#23262b",
    border="#4a505a", text="#e4e7eb", muted="#9aa2ad", accent="#3d8bfd", accent_dark="#2a62b8", warn="#f0a030", error="#ff6b6b",
    ok="#5fd08a", select="#ffa31a", hover="#454b55",
)
VIEWPORT_TOP = (0.20, 0.25, 0.32)           # background gradient (RGB 0..1): lighter at the bottom, like a studio floor
VIEWPORT_BOTTOM = (0.50, 0.57, 0.66)
SELECT_COLOR = (1.0, 0.64, 0.10)            # selected body highlight
EDGE_COLOR = (0.05, 0.06, 0.08)

_P = PALETTE
STYLESHEET = f"""
* {{ font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif; font-size: 12px; color: {_P['text']}; }}
QMainWindow, QDialog, QWidget#root {{ background: {_P['window']}; }}
QToolTip {{ background: {_P['panel_alt']}; color: {_P['text']}; border: 1px solid {_P['border']}; padding: 3px; }}

QMenuBar {{ background: {_P['window']}; }}
QMenuBar::item:selected {{ background: {_P['hover']}; }}
QMenu {{ background: {_P['panel']}; border: 1px solid {_P['border']}; }}
QMenu::item:selected {{ background: {_P['accent_dark']}; }}
QMenu::separator {{ height: 1px; background: {_P['border']}; margin: 3px 6px; }}

QTabWidget#ribbon::pane {{ border: 0; border-top: 1px solid {_P['border']}; background: {_P['ribbon']}; }}
QTabWidget#ribbon > QTabBar::tab {{ background: {_P['ribbon_tab']}; padding: 6px 18px; margin-right: 1px; color: {_P['muted']}; }}
QTabWidget#ribbon > QTabBar::tab:selected {{ background: {_P['ribbon']}; color: {_P['text']}; border-top: 2px solid {_P['accent']}; }}
QTabWidget#ribbon > QTabBar::tab:hover:!selected {{ background: {_P['hover']}; }}
QFrame#ribbonGroup {{ border-right: 1px solid {_P['border']}; }}
QLabel#groupTitle {{ color: {_P['muted']}; font-size: 10px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 3px; padding: 3px 4px; }}
QToolButton:hover {{ background: {_P['hover']}; border-color: {_P['border']}; }}
QToolButton:pressed, QToolButton:checked {{ background: {_P['accent_dark']}; border-color: {_P['accent']}; }}
QToolButton:disabled {{ color: #666c76; }}

QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {_P['panel_alt']}; padding: 5px 8px; border-bottom: 1px solid {_P['border']}; font-weight: 600; }}
QDockWidget > QWidget {{ background: {_P['panel']}; }}

QTreeWidget, QTableWidget, QListWidget, QPlainTextEdit, QTextEdit {{
    background: {_P['field']}; border: 1px solid {_P['border']}; alternate-background-color: #282b31;
    selection-background-color: {_P['accent_dark']}; selection-color: white; outline: 0; }}
QTreeWidget::item {{ padding: 2px 0; }}
QTreeWidget::item:hover {{ background: {_P['hover']}; }}
QHeaderView::section {{ background: {_P['panel_alt']}; border: 0; border-right: 1px solid {_P['border']}; padding: 4px 6px; color: {_P['muted']}; }}

QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{ background: {_P['field']}; border: 1px solid {_P['border']}; border-radius: 3px; padding: 3px 6px;
    selection-background-color: {_P['accent_dark']}; min-height: 18px; }}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {{ border-color: {_P['accent']}; }}
QLineEdit[formula="true"] {{ color: {_P['accent']}; font-style: italic; }}
QLineEdit[bad="true"] {{ border-color: {_P['error']}; }}
QComboBox QAbstractItemView {{ background: {_P['panel']}; selection-background-color: {_P['accent_dark']}; border: 1px solid {_P['border']}; }}
QGroupBox {{ border: 1px solid {_P['border']}; border-radius: 4px; margin-top: 12px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {_P['muted']}; }}

QPushButton {{ background: {_P['panel_alt']}; border: 1px solid {_P['border']}; border-radius: 3px; padding: 5px 14px; }}
QPushButton:hover {{ border-color: {_P['accent']}; }}
QPushButton:default {{ background: {_P['accent_dark']}; border-color: {_P['accent']}; }}

QStatusBar {{ background: {_P['panel_alt']}; border-top: 1px solid {_P['border']}; }}
QStatusBar QLabel {{ padding: 0 8px; }}
QScrollBar:vertical {{ background: {_P['window']}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #555b66; border-radius: 4px; min-height: 24px; }}
QScrollBar:horizontal {{ background: {_P['window']}; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #555b66; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSplitter::handle {{ background: {_P['border']}; }}
QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid {_P['border']}; border-radius: 2px; background: {_P['field']}; }}
QCheckBox::indicator:checked {{ background: {_P['accent']}; }}
QTreeView::indicator {{ width: 13px; height: 13px; }}
"""


def apply(app) -> None:
    """Apply the dark theme to a QApplication (Fusion style underneath, so every widget is themed consistently)."""
    from PySide6.QtGui import QColor, QPalette
    app.setStyle("Fusion")
    pal = QPalette()
    for role, key in ((QPalette.Window, "window"), (QPalette.Base, "field"), (QPalette.AlternateBase, "panel"),
                      (QPalette.Button, "panel_alt"), (QPalette.ToolTipBase, "panel_alt")):
        pal.setColor(role, QColor(_P[key]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.ToolTipText):
        pal.setColor(role, QColor(_P["text"]))
    pal.setColor(QPalette.Highlight, QColor(_P["accent_dark"]))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    pal.setColor(QPalette.PlaceholderText, QColor(_P["muted"]))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)
