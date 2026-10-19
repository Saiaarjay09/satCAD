"""Property Editor (right panel): the parametric settings of whatever is selected in the Part Navigator, plus the Expressions list.

Every field accepts a number or a formula (text starting with '='). Press Enter or leave the field to apply; the part rebuilds
immediately and everything that depends on it follows."""
from __future__ import annotations

import json
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..engine import REQ, SCHEMAS
from ..expressions import is_expression
from ..materials import MATERIALS
from .session import Session

DIMENSIONS = ("length", "width", "height", "radius", "diameter", "thickness", "wall", "depth", "fillet", "chamfer", "gap", "offset", "taper_deg")
POSITION = ("x", "y", "z", "rx", "ry", "rz", "offset_u", "offset_v", "offset_span", "offset_thin", "dx", "dy", "dz", "cx", "cy", "cz",
            "tilt_deg", "angle", "total_angle", "angle_deg", "pivot_x", "pivot_y", "pivot_z", "angle_min", "angle_max")
REFERENCES = ("attach_to", "target", "tool", "source", "side", "plane", "axis", "mode", "operation", "edges", "open_faces", "shape",
              "profile", "standard", "hole_type", "size", "parent", "direction", "joint_axis", "count")
CHOICES = {
    "side": ["+x", "-x", "+y", "-y", "+z", "-z"], "axis": ["x", "y", "z"], "plane": ["xy", "yz", "zx"], "mode": ["linear", "circular"],
    "operation": ["unite", "subtract", "intersect"], "shape": ["circle", "rect"], "direction": ["inward", "outward"],
    "profile": ["rect", "circle", "slot", "rounded_rect", "ellipse", "ngon", "polygon"],
    "hole_type": ["clearance", "tapped", "counterbore", "countersink"], "standard": ["M3", "M4", "M5", "M6", "M8", "M10", "M12"],
    "size": ["M3", "M4", "M5", "M6", "M8", "M10", "M12"], "joint_axis": ["", "x", "y", "z"],
    "role": ["structure", "chassis", "radiator", "solar", "payload", "antenna"],
    "edges": ["all", "parallel:x", "parallel:y", "parallel:z", "face:+x", "face:-x", "face:+y", "face:-y", "face:+z", "face:-z"],
}
UNITS = {"rx": "°", "ry": "°", "rz": "°", "tilt_deg": "°", "taper_deg": "°", "angle": "°", "total_angle": "°", "angle_deg": "°",
         "angle_min": "°", "angle_max": "°"}
NUMERIC_TEXT = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$", re.I)


def parse_value(text: str):
    """Typed text to a setting value: number, formula string, boolean, list (JSON) or plain text. '' means 'use the default' (None)."""
    t = text.strip()
    if t == "":
        return None
    if NUMERIC_TEXT.match(t):
        return float(t) if any(c in t for c in ".eE") else int(t)
    if t.lower() in ("true", "false"):
        return t.lower() == "true"
    if t[0] in "[{":
        try:
            return json.loads(t)
        except ValueError:
            raise ValueError(f"Couldn't read '{t[:30]}' as a list. Use the form [[0, 0], [10, 0]].") from None
    return t


def show_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v == int(v) and abs(v) < 1e12:
        return str(int(v))
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return str(v)


class ExprEdit(QLineEdit):
    """A line edit that marks formulas (blue italics) and applies on Enter / focus-out only when the text actually changed."""

    def __init__(self, text: str, on_commit):
        super().__init__(text)
        self._orig, self._commit = text, on_commit
        self.setProperty("formula", is_expression(text))
        self.editingFinished.connect(self._done)
        self.textEdited.connect(self._typing)

    def _typing(self, text: str) -> None:
        self.setProperty("formula", text.strip().startswith("="))
        self.style().unpolish(self)
        self.style().polish(self)

    def _done(self) -> None:
        if self.text() != self._orig:
            self._commit(self.text())


class PropertyEditor(QWidget):
    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._body = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self._body)
        self._layout.addWidget(scroll)
        self._form_box = QVBoxLayout(self._body)
        self._form_box.setAlignment(Qt.AlignTop)
        session.selection_changed.connect(lambda _=None: self.refresh())
        session.changed.connect(self.refresh)
        self.refresh()

    # ---- building the form --------------------------------------------------------------------------------------------------------
    def _clear(self) -> None:
        while self._form_box.count():
            item = self._form_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)                      # leave the panel at once; deleteLater alone keeps painting for one more event-loop turn
                w.deleteLater()

    def refresh(self) -> None:
        self._clear()
        d, name = self.session.design, self.session.selected
        if name is None or name not in d.features:
            self._summary()
            return
        f = d.features[name]
        label = self.session.labels()[name]
        head = QLabel(f"<b>{label}</b> &nbsp;<span style='color:#9aa2ad'>{name}</span>")
        self._form_box.addWidget(head)
        schema = SCHEMAS[f.kind]
        groups = {"Dimensions": [], "Position / Orientation": [], "References": [], "Material": [], "Other": []}
        for key in schema:
            if key in ("material", "role"):
                groups["Material"].append(key)
            elif key in DIMENSIONS:
                groups["Dimensions"].append(key)
            elif key in POSITION:
                groups["Position / Orientation"].append(key)
            elif key in REFERENCES:
                groups["References"].append(key)
            elif key not in ("color",):
                groups["Other"].append(key)
        # dimensions first, but for bodies with a pose show it second: matches the order engineers expect
        for title, keys in groups.items():
            if not keys:
                continue
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setLabelAlignment(Qt.AlignLeft)
            for key in keys:
                form.addRow(self._label(key), self._editor(name, f, key, schema[key]))
            self._form_box.addWidget(box)
            if title == "Other" or (title in ("Position / Orientation", "References", "Material") and False):
                pass
        part = d.parts.get(name)
        if part is not None:
            info = QLabel(f"Mass <b>{part.mass_kg:,.3f} kg</b> &nbsp; Volume <b>{part.volume_mm3 / 1e6:,.3f} L</b> &nbsp; "
                          f"Material {part.material}")
            info.setWordWrap(True)
            self._form_box.addWidget(info)
        self._form_box.addStretch(1)

    def _label(self, key: str) -> QLabel:
        text = key.replace("_", " ")
        unit = UNITS.get(key, "mm" if key in DIMENSIONS + POSITION and key not in UNITS and key not in ("count",) else "")
        lab = QLabel(f"{text} <span style='color:#9aa2ad'>{unit}</span>" if unit else text)
        lab.setToolTip(key)
        return lab

    def _editor(self, name: str, feature, key: str, default):
        raw = feature.raw.get(key)
        choices = list(CHOICES.get(key, ()))
        if key == "material":
            choices = list(MATERIALS)
        if key in ("target", "tool", "source", "attach_to") and key != "side":
            choices = list(self.session.design.parts)
        if key in ("assembly", "parent"):
            choices = [""] + [n for n, f in self.session.design.features.items() if f.kind == "assembly"]
        if choices and key not in ("open_faces",):
            box = QComboBox()
            box.setEditable(True)                       # a formula or an unlisted value can still be typed
            box.addItems([""] + [c for c in choices if c != ""] if "" not in choices else choices)
            current = show_value(raw if raw is not None else (None if default == REQ else default))
            box.setCurrentText(current)
            box.lineEdit().editingFinished.connect(lambda b=box: self._commit(name, feature, key, b.currentText()))
            box.activated.connect(lambda _i, b=box: self._commit(name, feature, key, b.currentText()))
            return box
        shown = show_value(raw if raw is not None else (None if default == REQ else default))
        edit = ExprEdit(shown, lambda text: self._commit(name, feature, key, text))
        if raw is None and default != REQ:
            edit.setPlaceholderText(show_value(default))
            edit.setText("")
        if default == REQ and raw is None:
            edit.setPlaceholderText("required")
        return edit

    def _commit(self, name: str, feature, key: str, text: str) -> None:
        old = show_value(feature.raw.get(key))
        if text.strip() == old:
            return
        try:
            value = parse_value(text)
        except ValueError as e:
            self.session.message.emit("error", str(e))
            self.refresh()
            return
        schema_default = SCHEMAS[feature.kind][key]
        if value is None:
            if schema_default == REQ:
                self.session.message.emit("error", f"'{key}' is required.")
                self.refresh()
                return
            value = schema_default
        if not self.session.edit(name, **{key: value}):
            self.refresh()                               # the engine refused: show the real values again

    def _summary(self) -> None:
        d = self.session.design
        m = d.measure()
        head = QLabel(f"<b>{d.name}</b> &nbsp;<span style='color:#9aa2ad'>nothing selected</span>")
        self._form_box.addWidget(head)
        if not m.get("parts"):
            self._form_box.addWidget(QLabel("The part is empty. Use the Home tab to create a Block, Cylinder or Sphere."))
            self._form_box.addStretch(1)
            return
        rows = [("Bodies", str(m["parts"])), ("Total mass", f"{m['total_mass_kg']:,.3f} kg"),
                ("Overall size", " × ".join(f"{v / 1000:,.3f}" for v in m["overall_size_mm"]) + " m")]
        if m.get("centre_of_mass_mm"):
            rows.append(("Centre of mass", ", ".join(f"{v / 1000:,.3f}" for v in m["centre_of_mass_mm"]) + " m"))
        if m.get("principal_moments_kg_m2"):
            rows.append(("Principal inertia", " / ".join(f"{v:,.4g}" for v in m["principal_moments_kg_m2"]) + " kg·m²"))
        box = QGroupBox("Mass properties")
        form = QFormLayout(box)
        for k, v in rows:
            form.addRow(k, QLabel(v))
        self._form_box.addWidget(box)
        self._model_parameters()
        self._form_box.addStretch(1)

    def _model_parameters(self) -> None:
        """The model's headline parameters (the UPPER_CASE variables): edit one and the whole assembly rebuilds."""
        d = self.session.design
        primary = [n for n in d.variables if n.isupper()]
        if not primary:
            return
        values = d.list_variables()
        box = QGroupBox("Model parameters")
        form = QFormLayout(box)
        for n in primary:
            raw = values[n]["definition"]
            form.addRow(QLabel(n.replace("_", " ").lower()), ExprEdit(show_value(raw), lambda text, n=n: self._set_parameter(n, text)))
        self._form_box.addWidget(box)
        tip = QLabel("Edit a number and the model rebuilds.")
        tip.setStyleSheet("color:#9aa2ad")
        self._form_box.addWidget(tip)

    def _set_parameter(self, name: str, text: str) -> None:
        try:
            value = parse_value(text)
        except ValueError as e:
            self.session.message.emit("error", str(e))
            self.refresh()
            return
        if value is None or not self.session.set_variable(name, value):
            self.refresh()


class ExpressionsEditor(QWidget):
    """The named variables and formulas of the part (NX 'Expressions'): edit a definition and the whole model rebuilds."""

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Formula", "Value"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        self.new_name, self.new_value = QLineEdit(), QLineEdit()
        self.new_name.setPlaceholderText("new name")
        self.new_value.setPlaceholderText("number or =formula")
        add = QPushButton("Add")
        add.clicked.connect(self._add)
        self.new_value.returnPressed.connect(self._add)
        delete = QPushButton("Delete selected")
        delete.clicked.connect(self._delete)
        row.addWidget(self.new_name)
        row.addWidget(self.new_value)
        row.addWidget(add)
        lay.addLayout(row)
        lay.addWidget(delete)
        session.changed.connect(self.refresh)
        self._busy = False
        self.refresh()

    def refresh(self) -> None:
        self._busy = True
        variables = self.session.design.list_variables()
        self.table.setRowCount(len(variables))
        for r, (name, v) in enumerate(variables.items()):
            n = QTableWidgetItem(name)
            n.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            f = QTableWidgetItem(show_value(v["definition"]))
            val = QTableWidgetItem(show_value(v["value"]) if v["value"] is not None else "")
            val.setFlags(Qt.ItemIsEnabled)
            val.setForeground(Qt.gray)
            self.table.setItem(r, 0, n)
            self.table.setItem(r, 1, f)
            self.table.setItem(r, 2, val)
        self.table.resizeColumnsToContents()
        self._busy = False

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._busy or item.column() != 1:
            return
        name = self.table.item(item.row(), 0).text()
        try:
            value = parse_value(item.text())
        except ValueError as e:
            self.session.message.emit("error", str(e))
            return
        if value is None or not self.session.set_variable(name, value):
            self.refresh()

    def _add(self) -> None:
        name, text = self.new_name.text().strip(), self.new_value.text()
        try:
            value = parse_value(text)
        except ValueError as e:
            self.session.message.emit("error", str(e))
            return
        if not name or value is None:
            self.session.message.emit("error", "Give the new expression a name and a value.")
            return
        if self.session.set_variable(name, value):
            self.new_name.clear()
            self.new_value.clear()

    def _delete(self) -> None:
        rows = {i.row() for i in self.table.selectedItems()}
        for r in rows:
            self.session.delete_variable(self.table.item(r, 0).text())


class RightDock(QTabWidget):
    """The tabbed content of the right-hand dock: Properties and Expressions."""

    def __init__(self, session: Session):
        super().__init__()
        self.properties = PropertyEditor(session)
        self.expressions = ExpressionsEditor(session)
        self.addTab(self.properties, "Properties")
        self.addTab(self.expressions, "Expressions")
