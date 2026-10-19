"""The tabbed ribbon across the top: groups of icon buttons (Home / Assemblies / Simulate / View)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QTabWidget, QToolButton, QVBoxLayout, QWidget

from .icons import icon


class RibbonGroup(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("ribbonGroup")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 2)
        outer.setSpacing(1)
        self.row = QHBoxLayout()
        self.row.setSpacing(2)
        outer.addLayout(self.row, 1)
        label = QLabel(title)
        label.setObjectName("groupTitle")
        label.setAlignment(Qt.AlignCenter)
        outer.addWidget(label)
        self._stack: QVBoxLayout | None = None

    def add_button(self, text: str, icon_name: str, callback, tip: str = "", large: bool = True, checkable: bool = False) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip or text)
        b.setCheckable(checkable)
        b.clicked.connect(lambda _=False: callback())
        if large:
            self._stack = None
            b.setIcon(icon(icon_name, 32))
            b.setIconSize(QSize(32, 32))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setMinimumWidth(58)
            b.setFixedHeight(62)
            self.row.addWidget(b)
        else:
            if self._stack is None or self._stack.count() >= 3:
                self._stack = QVBoxLayout()
                self._stack.setSpacing(0)
                holder = QWidget()
                holder.setLayout(self._stack)
                self.row.addWidget(holder)
            b.setIcon(icon(icon_name, 16))
            b.setIconSize(QSize(16, 16))
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setFixedHeight(20)
            self._stack.addWidget(b)
        return b


class RibbonTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(0)
        self.lay = lay
        lay.addStretch(1)

    def add_group(self, title: str) -> RibbonGroup:
        g = RibbonGroup(title)
        self.lay.insertWidget(self.lay.count() - 1, g)
        return g


class Ribbon(QTabWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("ribbon")
        self.setFixedHeight(104)
        self.setDocumentMode(True)

    def add_tab(self, title: str) -> RibbonTab:
        tab = RibbonTab()
        self.addTab(tab, title)
        return tab
