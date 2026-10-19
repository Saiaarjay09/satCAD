"""The main window and its layout, arranged like a professional CAD package:

    +------------------------------------------------------------------------------+
    |  menu bar                                                                    |
    |  RIBBON:  Home | Assemblies | Simulate | View     (icon groups)               |
    +---------------+------------------------------------------+-------------------+
    | PART          |                                          |  PROPERTY EDITOR  |
    | NAVIGATOR     |             3D VIEWPORT                  |  (+ Expressions)  |
    | (feature tree)|                                          |                   |
    +---------------+------------------------------------------+-------------------+
    |  Information window (messages, results)                                      |
    |  status bar: cue line | selection | units                                    |
    +------------------------------------------------------------------------------+
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QDockWidget, QLabel, QMainWindow, QMessageBox, QTextEdit, QVBoxLayout, QWidget)

from .commands import Commands
from .icons import icon
from .navigator import PartNavigator
from .properties import RightDock
from .ribbon import Ribbon
from .session import MODIFIERS, Session

APP_NAME = "satCAD"
MOUSE_HELP = """<b>Mouse</b><br>
Left click: select a body &nbsp;&nbsp; Middle drag: rotate &nbsp;&nbsp; Shift + middle drag: pan<br>
Wheel / trackpad scroll: zoom &nbsp;&nbsp; Right drag: zoom &nbsp;&nbsp; Right click: context menu<br>
No middle button? Alt + left drag rotates, Alt + Shift + left drag pans.<br><br>
<b>Keys</b><br>
Ctrl+Z undo, Ctrl+Y redo, Delete removes the selected feature, F fits the view, Home gives the isometric view."""


class InfoWindow(QTextEdit):
    """Messages and results, newest at the bottom (the NX 'Information window')."""
    COLORS = {"info": "#e4e7eb", "warn": "#f0a030", "error": "#ff6b6b"}

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.document().setMaximumBlockCount(500)

    def log(self, level: str, text: str) -> None:
        color = self.COLORS.get(level, self.COLORS["info"])
        prefix = {"warn": "Warning: ", "error": "Error: "}.get(level, "")
        body = (prefix + text).replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>").replace("  ", "&nbsp;&nbsp;")
        self.append(f"<span style='color:{color}'>{body}</span>")


class MainWindow(QMainWindow):
    def __init__(self, session: Session | None = None, viewport_factory=None):
        super().__init__()
        self.session = session or Session()
        self.commands = Commands(self.session, self)
        self.resize(1480, 900)
        self.setDockNestingEnabled(True)

        # ---- centre: the 3D viewport -------------------------------------------------------------------------------------------------
        if viewport_factory is None:
            from .viewport import Viewport as viewport_factory
        self.viewport = viewport_factory()
        centre = QWidget()
        centre.setObjectName("root")
        QVBoxLayout(centre).setContentsMargins(0, 0, 0, 0)
        centre.layout().addWidget(self.viewport)
        self.setCentralWidget(centre)

        # ---- ribbon (top) ---------------------------------------------------------------------------------------------------------------
        self.ribbon = Ribbon()
        self._build_ribbon()
        ribbon_holder = QWidget()
        QVBoxLayout(ribbon_holder).setContentsMargins(0, 0, 0, 0)
        ribbon_holder.layout().addWidget(self.ribbon)
        top = QDockWidget("", self)
        top.setObjectName("ribbonDock")
        top.setTitleBarWidget(QWidget())                       # no title bar: the ribbon is fixed furniture, not a floating panel
        top.setFeatures(QDockWidget.NoDockWidgetFeatures)
        top.setWidget(ribbon_holder)
        self.addDockWidget(Qt.TopDockWidgetArea, top)

        # ---- left: Part Navigator ---------------------------------------------------------------------------------------------------------
        self.navigator = PartNavigator(self.session)
        left = QDockWidget("Part Navigator", self)
        left.setObjectName("navigatorDock")
        left.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        left.setWidget(self.navigator)
        left.setMinimumWidth(250)
        self.addDockWidget(Qt.LeftDockWidgetArea, left)

        # ---- right: Property Editor (+ Expressions) ---------------------------------------------------------------------------------------
        self.right = RightDock(self.session)
        right = QDockWidget("Property Editor", self)
        right.setObjectName("propertyDock")
        right.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        right.setWidget(self.right)
        right.setMinimumWidth(310)
        self.addDockWidget(Qt.RightDockWidgetArea, right)

        # ---- bottom: Information window ----------------------------------------------------------------------------------------------------
        self.info = InfoWindow()
        bottom = QDockWidget("Information", self)
        bottom.setObjectName("infoDock")
        bottom.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetClosable)
        bottom.setWidget(self.info)
        self.addDockWidget(Qt.BottomDockWidgetArea, bottom)
        self.resizeDocks([bottom], [150], Qt.Vertical)
        self.resizeDocks([left, right], [270, 340], Qt.Horizontal)

        # ---- status bar ------------------------------------------------------------------------------------------------------------------
        self.cue = QLabel("Select a body, or pick a command from the ribbon")
        self.sel_label = QLabel("")
        self.units = QLabel("Millimetres · kg · degrees")
        sb = self.statusBar()
        sb.addWidget(self.cue, 1)
        sb.addPermanentWidget(self.sel_label)
        sb.addPermanentWidget(self.units)

        self._build_menus()
        self._wire()
        self._fit_next = True
        self.picked_face = None                                # (body name, face info) after a click in Face mode
        self._refresh_all()
        self._update_title()

    # ================================================================================================================================
    #  Ribbon contents
    # ================================================================================================================================
    def _build_ribbon(self) -> None:
        c, r = self.commands, self.ribbon
        home = r.add_tab("Home")
        g = home.add_group("Part")
        g.add_button("New", "new", c.new, "New part (Ctrl+N)")
        g.add_button("Open", "open", c.open, "Open a saved part (Ctrl+O)")
        g.add_button("Save", "save", c.save, "Save (Ctrl+S)")
        g = home.add_group("Select")
        self._filter_buttons = {
            "body": g.add_button("Body", "block", lambda: self.set_filter("body"), "Click selects a whole body", large=False, checkable=True),
            "face": g.add_button("Face", "shell", lambda: self.set_filter("face"),
                                 "Click picks a flat face: Hole, Shell and Edge Blend then start from it", large=False, checkable=True)}
        self._filter_buttons["body"].setChecked(True)
        g = home.add_group("Primitives")
        g.add_button("Block", "block", c.block, "Create a 100 mm block; set its size in the Property Editor")
        g.add_button("Cylinder", "cylinder", c.cylinder, "Create a cylinder")
        g.add_button("Sphere", "sphere", c.sphere, "Create a sphere")
        g = home.add_group("Boolean")
        g.add_button("Unite", "unite", lambda: c.boolean("unite"), "Join two bodies into one")
        g.add_button("Subtract", "subtract", lambda: c.boolean("subtract"), "Cut the tool body out of the target")
        g.add_button("Intersect", "intersect", lambda: c.boolean("intersect"), "Keep only the shared volume")
        g = home.add_group("Detail Feature")
        g.add_button("Edge Blend", "blend", c.blend, "Round chosen edges")
        g.add_button("Chamfer", "chamfer", lambda: c.blend(True), "Bevel chosen edges")
        g.add_button("Hole", "hole", c.hole, "ISO clearance / tapped / counterbore / countersink hole")
        g.add_button("Shell", "shell", c.shell, "Hollow out a body")
        g = home.add_group("Edit")
        g.add_button("Undo", "undo", self.session.undo, "Undo (Ctrl+Z)", large=False)
        g.add_button("Redo", "redo", self.session.redo, "Redo (Ctrl+Y)", large=False)
        g.add_button("Delete", "delete", c.delete_selected, "Delete the selected feature (Delete)", large=False)

        asm = r.add_tab("Assemblies")
        g = asm.add_group("Structure")
        g.add_button("New Assembly", "assembly", c.assembly, "A group of parts, optionally on a hinge")
        g = asm.add_group("Analysis")
        g.add_button("Interference", "check", c.check, "Find overlapping or floating bodies")
        g.add_button("Mass Properties", "mass", c.mass_properties, "Mass, centre of mass and inertia of the whole part")

        sim = r.add_tab("Simulate")
        g = sim.add_group("Structural (BETA)")
        g.add_button("Modal", "modal", c.modal, "Vibration modes with animated shapes")
        g.add_button("Static", "static", c.static, "Stress and deflection under g-loads")
        g = sim.add_group("Report")
        g.add_button("Mass Properties", "mass", c.mass_properties)
        g.add_button("Interference", "check", c.check)
        g = sim.add_group("Export")
        g.add_button("STEP", "export", lambda: c.export("step"), "Export for other CAD programs")
        g.add_button("STL", "export", lambda: c.export("stl"), "Mesh for 3D printing")
        g.add_button("GLB", "export", lambda: c.export("glb"), "Web / AR viewers")

        view = r.add_tab("View")
        g = view.add_group("Orient")
        for label, key, ic in (("Isometric", "iso", "view_iso"), ("Top", "top", "view_top"), ("Front", "front", "view_front"),
                               ("Right", "right", "view_right"), ("Left", "left", "view_left")):
            g.add_button(label, ic, lambda k=key: self.viewport.set_view(k), f"{label} view")
        g.add_button("Fit", "fit", lambda: self.viewport.fit(), "Zoom to fit (F)")
        g = view.add_group("Display")
        g.add_button("Shaded + Edges", "shaded", lambda: self.viewport.set_mode("shaded_edges"))
        g.add_button("Shaded", "shaded", lambda: self.viewport.set_mode("shaded"))
        g.add_button("Wireframe", "wire", lambda: self.viewport.set_mode("wireframe"))

    # ================================================================================================================================
    #  Menus and shortcuts
    # ================================================================================================================================
    def _action(self, menu, text, callback, shortcut=None, icon_name=None) -> QAction:
        act = QAction(text, self)
        if icon_name:
            act.setIcon(icon(icon_name, 16))
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(lambda _=False: callback())
        menu.addAction(act)
        return act

    def _build_menus(self) -> None:
        c, s = self.commands, self.session
        m = self.menuBar().addMenu("&File")
        self._action(m, "New", c.new, "Ctrl+N", "new")
        self._action(m, "Open...", c.open, "Ctrl+O", "open")
        self._action(m, "Save", c.save, "Ctrl+S", "save")
        self._action(m, "Save As...", c.save_as, "Ctrl+Shift+S")
        m.addSeparator()
        for fmt, label in (("step", "Export STEP..."), ("stl", "Export STL..."), ("glb", "Export GLB...")):
            self._action(m, label, lambda f=fmt: c.export(f))
        m.addSeparator()
        self._action(m, "Quit", self.close, "Ctrl+Q")
        m = self.menuBar().addMenu("&Edit")
        self.undo_action = self._action(m, "Undo", s.undo, "Ctrl+Z", "undo")
        self.redo_action = self._action(m, "Redo", s.redo, "Ctrl+Y", "redo")
        m.addSeparator()
        self._action(m, "Delete", c.delete_selected, "Delete", "delete")
        m = self.menuBar().addMenu("&View")
        for label, key in (("Isometric", "iso"), ("Top", "top"), ("Front", "front"), ("Right", "right"), ("Left", "left")):
            self._action(m, label, lambda k=key: self.viewport.set_view(k), "Home" if key == "iso" else None)
        self._action(m, "Fit", lambda: self.viewport.fit(), "F")
        m = self.menuBar().addMenu("&Help")
        self._action(m, "Mouse and keys", lambda: QMessageBox.information(self, "Mouse and keys", MOUSE_HELP))
        self._action(m, "About satCAD", lambda: QMessageBox.about(
            self, "About satCAD", "<b>satCAD</b><br>Open-source spacecraft CAD on OpenCASCADE, with a Qt interface and a VTK viewport.<br><br>"
                                  "A concept-design tool: not certified, not a substitute for professional CAD and analysis."))

    # ================================================================================================================================
    #  Wiring between the model and the panels
    # ================================================================================================================================
    def _part_for(self, feature: str | None) -> str | None:
        """The body to highlight for a selected feature (a Boolean or Blend highlights the body it changed)."""
        d = self.session.design
        if feature is None or feature not in d.features:
            return None
        f = d.features[feature]
        if feature in d.parts:
            return feature
        if f.kind in MODIFIERS:
            t = f.raw.get("target")
            return t if t in d.parts else None
        return None

    def _feature_for(self, part: str | None) -> str | None:
        d = self.session.design
        if part is None:
            return None
        if part in d.features:
            return part
        for name, f in d.features.items():                         # a copy made by a pattern ("rib_3" belongs to feature "rib")
            if f.kind == "pattern" and part.startswith(name + "_"):
                return name
        return None

    def _wire(self) -> None:
        s, v, nav = self.session, self.viewport, self.navigator
        s.changed.connect(self._refresh_all)
        s.loaded.connect(lambda: setattr(self, "_fit_next", True))
        s.message.connect(self._on_message)
        s.selection_changed.connect(self._on_selection)
        s.dirty_changed.connect(lambda _d: self._update_title())
        nav.selected.connect(s.select)
        nav.visibility_changed.connect(v.set_visible)
        nav.delete_requested.connect(s.remove)
        if hasattr(v, "body_clicked"):
            v.body_clicked.connect(lambda part, _ctrl: s.select(self._feature_for(part)))
            v.context_requested.connect(self._viewport_menu)
        if hasattr(v, "face_clicked"):
            v.face_clicked.connect(self._on_face)

    def set_filter(self, mode: str) -> None:
        """Selection filter: 'body' or 'face'."""
        self.picked_face = None
        if hasattr(self.viewport, "set_filter"):
            self.viewport.set_filter(mode)
        for key, b in getattr(self, "_filter_buttons", {}).items():
            b.setChecked(key == mode)
        self.cue.setText("Click a flat face: Hole, Shell and Edge Blend will start from it" if mode == "face"
                         else "Select a body, or pick a command from the ribbon")

    def _on_face(self, body: str, info: dict) -> None:
        self.picked_face = (body, info)
        side = info.get("side")
        if side:
            self.cue.setText(f"Face {side} of {body} picked at offset ({info['offset_u']:g}, {info['offset_v']:g}) mm: now use Hole, Shell or Edge Blend")
        else:
            self.cue.setText(f"{body}: that face is curved or not square to an axis. Pick a flat axis-aligned face for Hole / Shell")

    def _refresh_all(self) -> None:
        self.picked_face = None
        fit, self._fit_next = self._fit_next, False
        self.viewport.set_design(self.session.design, hidden=self.navigator.hidden, selected=self._part_for(self.session.selected), fit=fit)
        self.undo_action.setEnabled(self.session.can_undo)
        self.redo_action.setEnabled(self.session.can_redo)
        self._update_title()

    def _on_selection(self, feature) -> None:
        self.viewport.set_selected(self._part_for(feature))
        if feature:
            label = self.session.labels().get(feature, feature)
            self.sel_label.setText(f"{label}  ({feature})")
            self.cue.setText("Edit values in the Property Editor, or press Delete to remove the feature")
        else:
            self.sel_label.setText("")
            self.cue.setText("Select a body, or pick a command from the ribbon")

    def _on_message(self, level: str, text: str) -> None:
        self.info.log(level, text)
        first = text.splitlines()[0] if text else ""
        self.cue.setText(first[:200])

    def _viewport_menu(self, part, global_pos) -> None:
        from PySide6.QtWidgets import QMenu
        feature = self._feature_for(part)
        menu = QMenu(self)
        if feature:
            self.session.select(feature)
            menu.addAction("Delete", lambda: self.session.remove(feature))
            menu.addSeparator()
        for label, key in (("Isometric", "iso"), ("Top", "top"), ("Front", "front"), ("Right", "right")):
            menu.addAction(label + " view", lambda k=key: self.viewport.set_view(k))
        menu.addAction("Fit", lambda: self.viewport.fit())
        menu.exec(global_pos)

    def _update_title(self) -> None:
        d = self.session.design
        self.setWindowTitle(f"{APP_NAME}  -  {d.name}{' *' if self.session.dirty else ''}")

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_F and not ev.modifiers():
            self.viewport.fit()
        else:
            super().keyPressEvent(ev)

    # ================================================================================================================================
    #  Misc
    # ================================================================================================================================
    def confirm_discard(self) -> bool:
        if not self.session.dirty:
            return True
        box = QMessageBox.question(self, "Unsaved changes", "Discard your unsaved changes?", QMessageBox.Discard | QMessageBox.Cancel,
                                   QMessageBox.Cancel)
        return box == QMessageBox.Discard

    def closeEvent(self, ev):
        if self.confirm_discard():
            ev.accept()
        else:
            ev.ignore()

    def save_screenshot(self, path: str) -> None:
        """The whole window as a PNG (the viewport paints its rendered frame like any other widget, so a plain grab includes it)."""
        self.grab().save(path)
