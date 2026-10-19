"""Captures the pictures used in docs/HOW_TO_USE.md from the real satCAD window (headless), annotated with numbered callouts.

    python tools/make_howto_images.py            # writes docs/howto/*.png

Everything shown is produced by driving the actual application code, so the pictures cannot drift from the program.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["SATCAD_DESIGNS"] = tempfile.mkdtemp(prefix="satcad_howto_")           # keep the real designs folder untouched
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "howto"
OUT.mkdir(parents=True, exist_ok=True)

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QWidget  # noqa: E402

from satcad.gui import commands, theme  # noqa: E402
from satcad.gui.session import Session  # noqa: E402
from satcad.gui.ui_layout import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.apply(app)
MainWindow.confirm_discard = lambda self: True                                 # closing a window must never wait for a click
ACCENT = QColor("#ffb020")


def settle(n: int = 4) -> None:
    for _ in range(n):
        app.processEvents()


def rect_of(win: QWidget, widget: QWidget) -> QRect:
    return QRect(widget.mapTo(win, QPoint(0, 0)), widget.size())


def annotate(pix: QPixmap, items: list[tuple[QRect, str]], scale: float = 1.0) -> QPixmap:
    """Draw outlined rectangles with numbered badges (and small labels) on a screenshot."""
    out = QPixmap(pix)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    ratio = out.devicePixelRatio() or 1
    f = QFont("Helvetica Neue", 11)
    f.setBold(True)
    p.setFont(f)
    for i, item in enumerate(items, start=1):
        r, label = item[0], item[1]
        anchor = item[2] if len(item) > 2 else "tl"
        rr = QRect(int(r.x() * ratio), int(r.y() * ratio), int(r.width() * ratio), int(r.height() * ratio))
        p.setPen(QPen(ACCENT, 3 * ratio))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rr.adjusted(2, 2, -2, -2), 6, 6)
        d = int(26 * ratio)
        bx = rr.x() + 6 if "l" in anchor else rr.right() - d - 6
        by = rr.y() + 6 if "t" in anchor else rr.bottom() - d - 6
        badge = QRect(bx, by, d, d)
        p.setPen(Qt.NoPen)
        p.setBrush(ACCENT)
        p.drawEllipse(badge)
        p.setPen(QColor("#1b1b1b"))
        p.drawText(badge, Qt.AlignCenter, str(i))
    p.end()
    return out


def save(pix: QPixmap, name: str) -> None:
    pix.save(str(OUT / name))
    print("wrote", name, pix.width(), "x", pix.height())


def grab_window(win: MainWindow, name: str, callouts=None) -> None:
    settle()
    pix = win.grab()
    if callouts:
        pix = annotate(pix, callouts)
    save(pix, name)


def new_window(size=(1480, 900)) -> tuple[MainWindow, Session]:
    s = Session()
    w = MainWindow(s)
    w.resize(*size)
    w.show()
    settle()
    from PySide6.QtWidgets import QDockWidget
    docks = {d.objectName(): d for d in w.findChildren(QDockWidget)}
    w.resizeDocks([docks["infoDock"]], [96], Qt.Vertical)
    settle()
    return w, s


def capture_dialog(path: str):
    """Make the next ParamDialog / JointDialog screenshot itself instead of waiting for a click."""
    def fake_exec(self):
        self.show()
        settle()
        self.grab().save(str(OUT / path))
        print("wrote", path)
        return QDialog.Rejected
    for cls in (commands.ParamDialog,):
        cls.exec = fake_exec


def main() -> None:
    # ---- 1. an empty window, annotated ---------------------------------------------------------------------------------------------
    win, s = new_window()
    s.add("box", length=240, width=160, height=60)
    s.add("cylinder", radius=40, height=60, z=60)
    s.add("boolean", operation="unite", target="block_1", tool="cylinder_1")
    s.add("fillet_edges", target="block_1", edges="parallel:z", radius=12)
    s.add("hole", target="block_1", side="+z", standard="M10", hole_type="clearance", depth=0)
    s.select("block_1")
    win.viewport.set_view("iso")
    nav, prop, vp, rib, info = win.navigator, win.right, win.viewport, win.ribbon, win.info
    grab_window(win, "01_overview.png", [(rect_of(win, rib), "Ribbon", "tr"), (rect_of(win, nav), "Part Navigator", "br"), (rect_of(win, vp), "3D viewport"),
                                          (rect_of(win, prop), "Property Editor", "br"), (rect_of(win, info), "Information window", "tr")])

    # ---- 2. the ribbon tabs ------------------------------------------------------------------------------------------------------------
    for i, tab in enumerate(("home", "assemblies", "simulate", "motion", "view")):
        rib.setCurrentIndex(i)
        settle()
        save(rib.grab(), f"02_ribbon_{tab}.png")
    rib.setCurrentIndex(0)

    # ---- 3. first part: a block, then a formula --------------------------------------------------------------------------------------
    win.close()
    win, s = new_window()
    win.commands.block()
    win.viewport.set_view("iso")
    settle()
    grab_window(win, "03_first_block.png", [(rect_of(win, win.ribbon), "Block button", "tr"), (rect_of(win, win.navigator), "new feature in the tree", "br"),
                                             (rect_of(win, win.right), "dimensions to edit", "br")])
    s.set_variable("wing", 300)
    s.edit("block_1", length="=wing * 2", width=150, height=40)
    win.right.setCurrentIndex(0)
    settle()
    save(win.right.grab(), "04_property_editor_formula.png")
    win.right.setCurrentIndex(1)
    settle()
    save(win.right.grab(), "05_expressions_tab.png")
    win.right.setCurrentIndex(0)

    # ---- 4. boolean dialog and result --------------------------------------------------------------------------------------------------
    win.commands.cylinder()
    s.edit("cylinder_1", radius=60, height=200)
    win.viewport.set_view("iso")
    s.select("block_1")                                                              # the selected body becomes the Target in the dialog
    capture_dialog("06_boolean_dialog.png")
    win.commands.boolean("subtract")
    commands.ParamDialog.exec = lambda self: QDialog.Accepted                       # accept the dialog's defaults
    win.commands.boolean("subtract")
    win.viewport.set_view("iso")
    s.select(None)
    grab_window(win, "07_subtract_result.png", [(rect_of(win, win.navigator), "every step is recorded", "br"), (rect_of(win, win.viewport), "the result")])

    # ---- 5. face picking and a hole -----------------------------------------------------------------------------------------------------
    win.close()
    win, s = new_window()
    s.add("box", length=300, width=200, height=60)
    win.viewport.set_view("iso")
    win.set_filter("face")
    win.viewport._render_frame()
    hit = None
    for x in range(win.viewport.width() // 3, 2 * win.viewport.width() // 3, 20):
        for y in range(win.viewport.height() // 4, 3 * win.viewport.height() // 4, 20):
            h = win.viewport.pick_face(x, y)
            if h:
                face = win.viewport._faces[h[0]][h[1]]
                if face["side"] == "+z":
                    hit = (x, y)
                    break
        if hit:
            break
    from PySide6.QtTest import QTest
    if hit:
        QTest.mouseClick(win.viewport, Qt.LeftButton, Qt.NoModifier, QPoint(*hit))
    settle()
    grab_window(win, "08_face_picked.png", [(rect_of(win, win.ribbon), "Select: Face", "tr"),
                                             (rect_of(win, win.viewport), "the picked face is highlighted")])
    capture_dialog("09_hole_dialog.png")
    win.commands.hole()
    commands.ParamDialog.exec = lambda self: QDialog.Accepted
    win.commands.hole()
    win.set_filter("body")
    win.viewport.set_view("iso")
    s.select(None)
    grab_window(win, "10_hole_result.png")

    # ---- 6. importing the example satellite with Open ------------------------------------------------------------------------------------
    win.close()
    win, s = new_window((1600, 960))
    s.open(ROOT / "sattelite designs" / "orbital_node_alpha.json")                       # what the Open button does with that file
    win.viewport.set_view("iso")
    grab_window(win, "11_alpha_loaded.png", [(rect_of(win, win.ribbon), "Home > Open", "tr"), (rect_of(win, win.navigator), "6 sub-assemblies", "br")])
    item = win.navigator._items["bus_hull"]
    item.setCheckState(0, Qt.Unchecked)
    win.viewport.zoom_by(5.0)
    s.select(None)
    settle()
    grab_window(win, "12_alpha_hull_hidden.png", [(rect_of(win, win.navigator), "untick the bus hull", "br"), (rect_of(win, win.viewport), "blades, cold plates, pipes, turrets"),
                                                   (rect_of(win, win.right), "model parameters", "br")])
    save(win.right.grab(), "13_model_parameters.png")

    # ---- 7. Motion Studio --------------------------------------------------------------------------------------------------------------------
    s.open(ROOT / "sattelite designs" / "orbital_node_alpha_with_motion.json")          # the same satellite, with its motion setup saved in the file
    win.viewport.set_view("iso")
    item = win.navigator._items["bus_hull"]
    item.setCheckState(0, Qt.Unchecked)
    win.viewport.zoom_by(5.0)
    win.motion.seek(1800.0)
    win.show_motion()
    win.ribbon.setCurrentIndex(3)                                                  # the Motion tab
    win.timeline_dock.replot()
    settle(6)
    grab_window(win, "14_motion_studio.png", [(rect_of(win, win.ribbon), "Motion tab", "tr"), (rect_of(win, win.motion_dock), "equations and sliders", "br"),
                                               (rect_of(win, win.viewport), "3D view"), (rect_of(win, win.timeline_dock), "timeline and plots", "tr")])
    save(win.motion_dock.equations.grab(), "15_motion_equations.png")
    win.motion_dock.widget().setCurrentIndex(1)
    settle()
    save(win.motion_dock.joints.grab(), "16_motion_joints.png")
    win.motion_dock.widget().setCurrentIndex(0)
    win.timeline_dock.replot()
    settle()
    save(win.timeline_dock.grab(), "17_timeline.png")
    rib = win.ribbon
    rib.setCurrentIndex(3)
    settle()
    save(rib.grab(), "18_motion_ribbon.png")
    from satcad.gui.motion_expressions import JointDialog
    dlg = JointDialog(win, win.motion, "revolute")
    dlg.name.setText("My_hinge")
    dlg.assembly.setCurrentText("solar_pos_x_wing")
    dlg.axis.setText("1, 0, 0")
    dlg.expr.setCurrentText("theta_solar")
    dlg.show()
    settle()
    save(dlg.grab(), "19_joint_dialog.png")
    dlg.close()
    # a custom equation with its auto-created sliders
    win.motion.set_equations(["my_az(t) = A * sin(2 * pi * f * t)", "my_ramp(t) = 10 * t / T_end"])
    win.motion.engine.set_param("A", 12.0)
    win.motion.engine.set_param("f", 0.2)
    win.motion.set_parameter("T_end", 60.0)
    settle(6)
    save(win.motion_dock.equations.grab(), "20_custom_equation_sliders.png")
    win.close()
    print("done")


if __name__ == "__main__":
    main()
