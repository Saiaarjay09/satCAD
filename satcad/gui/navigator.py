"""Part Navigator: the feature tree on the left. Lists every operation in the order it was performed, NX style:
Datum CSYS(0), Block(1), Cylinder(2), Edge Blend(3), Subtract(4) ... Assemblies appear as folders holding their parts."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QMenu, QTreeWidget, QTreeWidgetItem

from . import theme
from .icons import icon
from .session import MODIFIERS, Session

KIND_ICON = {"box": "block", "chassis": "block", "cylinder": "cylinder", "sphere": "sphere", "extrude": "extrude", "revolve": "revolve",
             "sweep": "extrude", "loft": "extrude", "shell": "shell", "fillet_edges": "blend", "hole": "hole", "fastener": "hole",
             "cutout": "hole", "assembly": "assembly", "panel": "block", "pattern": "block", "mirror": "block", "boolean": "unite"}


class PartNavigator(QTreeWidget):
    selected = Signal(object)                # feature name or None
    visibility_changed = Signal(str, bool)   # part name, visible
    delete_requested = Signal(str)

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self.setHeaderLabels(["Name", "Part"])
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemSelectionChanged.connect(self._on_select)
        self.itemChanged.connect(self._on_check)
        self.header().setStretchLastSection(True)
        self._items: dict[str, QTreeWidgetItem] = {}
        self._building = False
        self.hidden: set[str] = set()
        session.changed.connect(self.rebuild)
        session.selection_changed.connect(self.select)
        self.rebuild()

    # ---- building the tree --------------------------------------------------------------------------------------------------------
    def rebuild(self) -> None:
        self._building = True
        expanded = {n for n, it in self._items.items() if it.isExpanded()}
        self.clear()
        self._items = {}
        d = self.session.design
        labels = self.session.labels()
        history = QTreeWidgetItem(self, ["Model History"])
        history.setFlags(Qt.ItemIsEnabled)
        history.setExpanded(True)
        datum = QTreeWidgetItem(history, ["Datum CSYS(0)", "X forward, Y left, Z up"])
        datum.setIcon(0, icon("datum", 16))
        datum.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        datum.setForeground(1, Qt.gray)
        for name, f in d.features.items():
            parent = history
            owner = f.raw.get("parent") if f.kind == "assembly" else f.raw.get("assembly")
            if isinstance(owner, str) and owner in self._items:
                parent = self._items[owner]
            item = QTreeWidgetItem(parent, [labels[name], name])
            item.setIcon(0, icon(KIND_ICON.get(f.kind, "block"), 16))
            item.setData(0, Qt.UserRole, name)
            item.setForeground(1, Qt.gray)
            flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
            if f.kind not in MODIFIERS and f.kind != "assembly" and name in d.parts:
                flags |= Qt.ItemIsUserCheckable
                item.setCheckState(0, Qt.Unchecked if name in self.hidden else Qt.Checked)
            elif f.kind == "pattern":
                flags |= Qt.ItemIsUserCheckable
                item.setCheckState(0, Qt.Checked)
            item.setFlags(flags)
            if f.kind == "boolean" and f.raw.get("tool"):
                item.setToolTip(0, f"{f.raw.get('operation')}: target {f.raw.get('target')}, tool {f.raw.get('tool')}")
            self._items[name] = item
            if name in expanded or f.kind == "assembly":
                item.setExpanded(True)
        self.resizeColumnToContents(0)
        self._building = False
        self.select(self.session.selected)

    # ---- selection and visibility --------------------------------------------------------------------------------------------------
    def _on_select(self) -> None:
        if self._building:
            return
        items = self.selectedItems()
        name = items[0].data(0, Qt.UserRole) if items else None
        self.selected.emit(name)

    def select(self, name) -> None:
        item = self._items.get(name) if name else None
        self._building = True
        self.clearSelection()
        if item is not None:
            item.setSelected(True)
            self.scrollToItem(item)
        self._building = False

    def _on_check(self, item: QTreeWidgetItem, column: int) -> None:
        if self._building or column != 0:
            return
        name = item.data(0, Qt.UserRole)
        if not name:
            return
        visible = item.checkState(0) == Qt.Checked
        (self.hidden.discard if visible else self.hidden.add)(name)
        self.visibility_changed.emit(name, visible)

    def _menu(self, pos) -> None:
        item = self.itemAt(pos)
        name = item.data(0, Qt.UserRole) if item else None
        if not name:
            return
        menu = QMenu(self)
        menu.addAction("Edit Parameters", lambda: self.session.select(name))
        menu.addSeparator()
        menu.addAction("Delete", lambda: self.delete_requested.emit(name))
        menu.exec(self.viewport().mapToGlobal(pos))
