"""The desktop application: session/undo, Part Navigator, Property Editor, viewport geometry and camera, and the assembled window.
Runs headless (Qt 'offscreen' platform; VTK renders off-screen)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("vtk")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from satcad import Design  # noqa: E402
from satcad.gui import theme  # noqa: E402
from satcad.gui.navigator import PartNavigator  # noqa: E402
from satcad.gui.properties import ExpressionsEditor, PropertyEditor, parse_value, show_value  # noqa: E402
from satcad.gui.session import Session, feature_label  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    theme.apply(a)
    return a


@pytest.fixture()
def session(app):
    return Session()


def messages(session):
    log = []
    session.message.connect(lambda lvl, txt: log.append((lvl, txt)))
    return log


# ---- session -------------------------------------------------------------------------------------------------------------------------
def test_session_add_edit_remove_with_undo_redo(session):
    name = session.add("box", length=100, width=100, height=100)
    assert name == "block_1" and session.selected == name and session.dirty
    assert session.edit(name, length=200) and session.design.describe(name)["size_mm"][0] == 200
    session.undo()
    assert session.design.describe(name)["size_mm"][0] == 100
    session.redo()
    assert session.design.describe(name)["size_mm"][0] == 200
    session.undo()
    session.undo()
    assert not session.design.features and not session.can_undo and session.can_redo
    session.redo()
    assert name in session.design.features


def test_session_failed_edit_changes_nothing_and_explains(session):
    log = messages(session)
    name = session.add("box", length=100, width=100, height=100)
    undo_depth = len(session._undo)
    assert session.edit(name, length=-5) is False
    assert session.design.describe(name)["size_mm"][0] == 100 and len(session._undo) == undo_depth
    assert log[-1][0] == "error" and "length" in log[-1][1]
    assert session.add("panel", attach_to="ghost", side="+y", length=5, width=5, thickness=1) is None
    assert log[-1][0] == "error" and "ghost" in log[-1][1]


def test_session_boolean_names_and_selection_cleanup(session):
    a = session.add("box", length=100, width=100, height=100)
    b = session.add("cylinder", radius=20, height=300)
    session.add("boolean", operation="subtract", target=a, tool=b)
    assert list(session.design.parts) == [a]
    assert list(session.labels().values()) == ["Block(1)", "Cylinder(2)", "Subtract(3)"]
    session.select(b)
    session.undo()
    assert set(session.design.parts) == {a, b}


def test_feature_labels_follow_nx_naming():
    assert feature_label("box", {}, 1) == "Block(1)"
    assert feature_label("fillet_edges", {"radius": 5}, 3) == "Edge Blend(3)"
    assert feature_label("fillet_edges", {"chamfer": 2, "radius": 0}, 3) == "Chamfer(3)"
    assert feature_label("boolean", {"operation": "unite"}, 4) == "Unite(4)"
    assert feature_label("hole", {"hole_type": "tapped"}, 5) == "Tapped Hole(5)"


def test_session_save_open_round_trip(session, tmp_path):
    session.add("box", length=100, width=100, height=100)
    session.set_variable("w", 40)
    assert session.save(tmp_path / "p.json") and not session.dirty
    other = Session()
    assert other.open(tmp_path / "p.json") and list(other.design.features) == ["block_1"] and other.design.variables == {"w": 40}
    log = messages(other)
    (tmp_path / "bad.json").write_text("not json")
    assert other.open(tmp_path / "bad.json") is False and log[-1][0] == "error"
    assert list(other.design.features) == ["block_1"]                    # the open design survived


# ---- navigator ------------------------------------------------------------------------------------------------------------------------
def test_navigator_lists_history_in_order_and_syncs_selection(session):
    nav = PartNavigator(session)
    a = session.add("box", length=100, width=100, height=100)
    b = session.add("cylinder", radius=20, height=300)
    session.add("boolean", operation="subtract", target=a, tool=b)
    root = nav.topLevelItem(0)
    texts = [root.child(i).text(0) for i in range(root.childCount())]
    assert texts == ["Datum CSYS(0)", "Block(1)", "Cylinder(2)", "Subtract(3)"]
    session.select(a)
    assert nav.selectedItems()[0].text(0) == "Block(1)"
    seen = []
    nav.selected.connect(seen.append)
    nav._items[b].setSelected(True) if b in nav._items else None
    nav.itemSelectionChanged.emit()
    assert seen and seen[-1] in (a, b, None)


def test_navigator_visibility_checkbox_and_assembly_nesting(session):
    nav = PartNavigator(session)
    session.add("assembly", "power")
    a = session.add("box", length=100, width=100, height=100, assembly="power")
    root = nav.topLevelItem(0)
    folder = [root.child(i) for i in range(root.childCount()) if root.child(i).data(0, Qt.UserRole) == "power"][0]
    assert folder.childCount() == 1 and folder.child(0).data(0, Qt.UserRole) == a
    got = []
    nav.visibility_changed.connect(lambda n, v: got.append((n, v)))
    folder.child(0).setCheckState(0, Qt.Unchecked)
    assert got == [(a, False)] and a in nav.hidden


# ---- property editor and expressions ---------------------------------------------------------------------------------------------------
def test_parse_and_show_values():
    assert parse_value("12") == 12 and parse_value("1.5") == 1.5 and parse_value("=a*2") == "=a*2" and parse_value("") is None
    assert parse_value("[[0, 0], [1, 2]]") == [[0, 0], [1, 2]] and parse_value("true") is True
    with pytest.raises(ValueError):
        parse_value("[1, 2")
    assert show_value(100.0) == "100" and show_value(None) == "" and show_value([1, 2]) == "[1, 2]"


def test_property_editor_edits_dimensions_and_formulas(session):
    editor = PropertyEditor(session)
    name = session.add("box", length=100, width=50, height=20)
    f = session.design.features[name]
    editor._commit(name, f, "length", "250")
    assert session.design.describe(name)["size_mm"][0] == 250
    session.set_variable("w", 80)
    editor._commit(name, session.design.features[name], "width", "=w * 2")
    assert session.design.describe(name)["size_mm"][1] == 160
    session.set_variable("w", 10)
    assert session.design.describe(name)["size_mm"][1] == 20                 # the formula follows the variable


def test_property_editor_rejects_bad_input_without_changing_the_part(session):
    editor = PropertyEditor(session)
    name = session.add("box", length=100, width=50, height=20)
    log = messages(session)
    editor._commit(name, session.design.features[name], "height", "-3")
    editor._commit(name, session.design.features[name], "height", "=nope + 1")
    assert session.design.describe(name)["size_mm"][2] == 20
    assert [lvl for lvl, _ in log[-2:]] == ["error", "error"]
    editor._commit(name, session.design.features[name], "length", "")        # required: cannot be cleared
    assert session.design.describe(name)["size_mm"][0] == 100


def test_property_editor_shows_summary_then_selected_feature(session):
    editor = PropertyEditor(session)
    assert editor._form_box.count() >= 1
    name = session.add("cylinder", radius=10, height=50)
    session.select(None)
    session.select(name)
    labels = [editor._form_box.itemAt(i).widget() for i in range(editor._form_box.count()) if editor._form_box.itemAt(i).widget()]
    assert any("Cylinder(1)" in getattr(w, "text", lambda: "")() for w in labels)


def test_expressions_editor_adds_edits_and_deletes(session):
    ex = ExpressionsEditor(session)
    ex.new_name.setText("span")
    ex.new_value.setText("2000")
    ex._add()
    assert session.design.variables == {"span": 2000} and ex.table.rowCount() == 1
    ex.table.item(0, 1).setText("=1500 + 500")
    assert session.design.variables["span"] == "=1500 + 500"
    ex.table.selectRow(0)
    ex._delete()
    assert not session.design.variables


# ---- viewport ---------------------------------------------------------------------------------------------------------------------------
@pytest.fixture()
def viewport(app):
    from satcad.gui.viewport import Viewport
    v = Viewport()
    v.resize(640, 480)
    try:
        v.grab_image()
    except Exception as e:                       # a machine with no usable off-screen GL
        pytest.skip(f"VTK off-screen rendering is not available here: {e}")
    return v


def test_geometry_conversion_matches_the_cad_solids(app):
    from satcad.gui.viewport import part_edges, part_polydata
    d = Design("t")
    d.add("box", "a", length=100, width=60, height=40, fillet=5)
    d.add("cylinder", "c", radius=20, height=50, x=300)
    for part in d.parts.values():
        poly, edges = part_polydata(part), part_edges(part)
        mn, mx = part.bbox()
        b = poly.GetBounds()
        assert [b[0], b[2], b[4]] == pytest.approx(list(mn), abs=0.05) and [b[1], b[3], b[5]] == pytest.approx(list(mx), abs=0.05)
        assert poly.GetNumberOfPolys() > 100 and edges.GetNumberOfLines() == len(part.solid.edges())
        assert poly.GetPointData().GetNormals() is not None


def test_viewport_renders_picks_and_selects(viewport):
    d = Design("t")
    d.add("box", "a", length=100, width=100, height=100)
    d.add("box", "b", x=400, length=100, width=100, height=100)
    viewport.set_design(d, fit=True)
    img = viewport.grab_image()
    assert img.width() > 100 and img.height() > 100
    px = np.array([[img.pixelColor(x, y).red() for x in range(0, img.width(), 40)] for y in range(0, img.height(), 40)])
    assert px.std() > 5                                                         # not a blank frame
    seen = set()
    for x in range(20, viewport.width() - 20, 30):
        for y in range(20, viewport.height() - 20, 30):
            seen.add(viewport.pick(x, y))
    assert {"a", "b"} <= seen and None in seen                                 # both bodies hit, plus background
    clicks = []
    viewport.body_clicked.connect(lambda n, c: clicks.append(n))
    viewport.set_selected("a")
    assert viewport._surfaces["a"].GetProperty().GetColor() == pytest.approx(theme.SELECT_COLOR)
    viewport.set_selected(None)
    assert viewport._surfaces["a"].GetProperty().GetColor() != pytest.approx(theme.SELECT_COLOR)


def test_viewport_camera_orbit_pan_zoom_and_views(viewport):
    d = Design("t")
    d.add("box", "a", length=100, width=100, height=100)
    viewport.set_design(d, fit=True)
    cam = viewport.ren.GetActiveCamera()
    dist0 = np.linalg.norm(np.array(cam.GetPosition()) - np.array(cam.GetFocalPoint()))
    viewport.rotate_by(200, -50)
    assert cam.GetViewUp() == pytest.approx((0, 0, 1))                         # turntable keeps Z up
    viewport.rotate_by(0, 100000)                                              # cannot flip over the pole
    pos, foc = np.array(cam.GetPosition()), np.array(cam.GetFocalPoint())
    assert abs(pos[2] - foc[2]) < np.linalg.norm(pos - foc) * 1.0001
    viewport.pan_by(50, 0)
    assert np.linalg.norm(np.array(cam.GetFocalPoint()) - foc) > 0
    viewport.set_view("iso")
    near = np.linalg.norm(np.array(cam.GetPosition()) - np.array(cam.GetFocalPoint()))
    viewport.zoom_by(1.5)
    assert np.linalg.norm(np.array(cam.GetPosition()) - np.array(cam.GetFocalPoint())) < near
    viewport.set_view("top")
    p = np.array(cam.GetPosition()) - np.array(cam.GetFocalPoint())
    assert p[2] > 0 and abs(p[0]) < 1e-6 and abs(p[1]) < 1e-6
    viewport.set_view("front")
    p = np.array(cam.GetPosition()) - np.array(cam.GetFocalPoint())
    assert p[1] < 0 and abs(p[0]) < 1e-6 and abs(p[2]) < 1e-6
    assert dist0 > 0


def test_viewport_display_modes_and_visibility(viewport):
    d = Design("t")
    d.add("box", "a", length=100, width=100, height=100)
    viewport.set_design(d, fit=True)
    surf, edges = viewport._surfaces["a"], viewport._edges["a"]
    viewport.set_mode("wireframe")
    assert not surf.GetVisibility() and edges.GetVisibility()
    viewport.set_mode("shaded")
    assert surf.GetVisibility() and not edges.GetVisibility()
    viewport.set_mode("shaded_edges")
    viewport.set_visible("a", False)
    assert not surf.GetVisibility() and not edges.GetVisibility()


# ---- the assembled window -------------------------------------------------------------------------------------------------------------
def test_main_window_wires_every_panel_together(viewport, tmp_path):
    from satcad.gui.main import build_demo
    from satcad.gui.ui_layout import MainWindow
    session = Session()
    win = MainWindow(session)
    build_demo(session)
    win.show()
    assert win.navigator.topLevelItem(0).childCount() == 6                       # datum + 5 features
    assert len(win.viewport._surfaces) == len(session.design.parts) == 1
    session.select("hole_1")                                                     # a modifier highlights the body it changed
    assert win.viewport._selected == "block_1" and "Hole(5)" in win.sel_label.text()
    session.add("box", length=10, width=10, height=10)
    assert len(win.viewport._surfaces) == 2 and session.dirty
    win.commands.delete_selected()
    assert len(win.viewport._surfaces) == 1
    session.undo()
    assert len(win.viewport._surfaces) == 2
    shot = tmp_path / "shot.png"
    win.save_screenshot(str(shot))
    assert shot.stat().st_size > 10_000
    session.message.emit("error", "boom")
    assert "boom" in win.info.toPlainText()
    ribbon_tabs = [win.ribbon.tabText(i) for i in range(win.ribbon.count())]
    assert ribbon_tabs == ["Home", "Assemblies", "Simulate", "View"]
    from PySide6.QtWidgets import QDockWidget
    assert {"Part Navigator", "Property Editor", "Information"} <= {d.windowTitle() for d in win.findChildren(QDockWidget)}


def _stub_viewport_class():
    from PySide6.QtWidgets import QWidget

    class StubViewport(QWidget):
        """Stands in for the 3D viewport so the window can be tested without any rendering."""
        def __init__(self):
            super().__init__()
            self.calls = []

        def set_design(self, *a, **k):
            self.calls.append("set_design")

        def set_selected(self, *a):
            self.calls.append("set_selected")

        def set_visible(self, *a):
            self.calls.append("set_visible")

        def set_view(self, *a):
            self.calls.append("set_view")

        def set_mode(self, *a):
            self.calls.append("set_mode")

        def fit(self):
            self.calls.append("fit")
    return StubViewport


def test_commands_boolean_and_blend_through_session(app):
    from satcad.gui.ui_layout import MainWindow
    session = Session()
    win = MainWindow(session, viewport_factory=_stub_viewport_class())
    a = session.add("box", length=100, width=100, height=100)
    session.add("cylinder", radius=20, height=300)
    win.commands.s.add("fillet_edges", target=a, edges="all", radius=5)
    assert session.labels()[list(session.design.features)[-1]] == "Edge Blend(3)"
    assert win.commands._default_body() == a


def test_demo_part_builds_clean(app):
    from satcad.gui.main import build_demo
    s = Session()
    build_demo(s)
    assert s.design.check()["ok"] and not s.dirty and list(s.design.parts) == ["block_1"]


def test_mouse_click_selects_and_middle_drag_rotates(viewport):
    from PySide6.QtCore import QEvent, QPoint, QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    d = Design("t")
    d.add("box", "a", length=100, width=100, height=100)
    viewport.set_design(d, fit=True)
    clicks = []
    viewport.body_clicked.connect(lambda n, c: clicks.append(n))
    QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, viewport.rect().center())            # on the body
    QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, QPoint(8, 8))
    assert clicks == ["a", None]

    def ev(kind, x, y, button, buttons):
        p = QPointF(x, y)
        return QMouseEvent(kind, p, viewport.mapToGlobal(p), button, buttons, Qt.NoModifier)
    cam = viewport.ren.GetActiveCamera()
    before = np.array(cam.GetPosition())
    viewport.mousePressEvent(ev(QEvent.MouseButtonPress, 300, 200, Qt.MiddleButton, Qt.MiddleButton))
    viewport.mouseMoveEvent(ev(QEvent.MouseMove, 380, 200, Qt.NoButton, Qt.MiddleButton))
    viewport.mouseReleaseEvent(ev(QEvent.MouseButtonRelease, 380, 200, Qt.MiddleButton, Qt.NoButton))
    assert np.linalg.norm(np.array(cam.GetPosition()) - before) > 1 and clicks == ["a", None]       # rotated, and a drag is not a click


def test_property_editor_leaves_no_stale_widgets_behind(session):
    from PySide6.QtWidgets import QGroupBox
    editor = PropertyEditor(session)
    name = session.add("box", length=100, width=50, height=20)
    session.select(None)
    assert any(g.title() == "Mass properties" for g in editor._body.findChildren(QGroupBox))
    session.select(name)                                                     # no event loop has run: old widgets must already be gone
    titles = [g.title() for g in editor._body.findChildren(QGroupBox)]
    assert "Mass properties" not in titles and "Dimensions" in titles
    session.select(None)
    assert [g.title() for g in editor._body.findChildren(QGroupBox)] == ["Mass properties"]


# ---- face picking --------------------------------------------------------------------------------------------------------------------
def test_face_data_identifies_flat_axis_aligned_faces(app):
    from satcad.gui.viewport import part_face_data, part_polydata
    d = Design("t")
    d.add("box", "a", length=100, width=60, height=40, fillet=5)
    d.add("cylinder", "c", radius=20, height=50)
    box = part_face_data(d.parts["a"])
    assert sorted(f["side"] for f in box if f["side"]) == ["+x", "+y", "+z", "-x", "-y", "-z"]       # 6 flat faces, the rest are fillets
    assert sum(1 for f in box if not f["planar"]) == len(box) - 6
    cyl = part_face_data(d.parts["c"])
    assert sorted(f["side"] for f in cyl if f["side"]) == ["+z", "-z"] and sum(1 for f in cyl if not f["planar"]) == 1
    poly = part_polydata(d.parts["a"])
    ids = poly.GetCellData().GetArray("face_id")
    assert ids.GetNumberOfTuples() == poly.GetNumberOfPolys() == sum(len(f["tris"]) for f in box)
    assert {int(ids.GetValue(i)) for i in range(ids.GetNumberOfTuples())} == set(range(len(box)))


def test_picked_face_offsets_place_a_hole_where_clicked(app):
    from build123d import Vector
    from satcad.gui.viewport import face_pick_info, part_face_data
    d = Design("t")
    d.add("box", "a", length=200, width=100, height=40)
    part = d.parts["a"]
    for side, point in (("+z", (30.0, -20.0, 20.0)), ("+y", (-40.0, 50.0, 5.0)), ("-x", (-100.0, 10.0, -12.0))):
        face = [f for f in part_face_data(part) if f["side"] == side][0]
        info = face_pick_info(part, face, point)
        assert info["side"] == side and info["at_outer_face"]
        e = Design("t")
        e.add("box", "a", length=200, width=100, height=40)
        e.add("hole", "h", target="a", side=side, standard="M6", offset_u=info["offset_u"], offset_v=info["offset_v"])
        p = Vector(*point)
        inward = {"+z": Vector(0, 0, -1), "+y": Vector(0, -1, 0), "-x": Vector(1, 0, 0)}[side]
        assert not e.parts["a"].solid.is_inside(p + inward * 3)                   # the drilled hole is exactly under the clicked point
        assert e.parts["a"].solid.is_inside(p + inward * 3 + Vector(*[(1 - abs(v)) * 8 for v in (inward.X, inward.Y, inward.Z)]))   # solid just beside it


def test_viewport_face_mode_picks_face_and_reports_it(viewport):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    d = Design("t")
    d.add("box", "a", length=100, width=100, height=100)
    viewport.set_design(d, fit=True)
    viewport.set_view("top")
    got, bodies = [], []
    viewport.face_clicked.connect(lambda n, info: got.append((n, info)))
    viewport.body_clicked.connect(lambda n, c: bodies.append(n))
    QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, viewport.rect().center())          # body filter: no face event
    assert got == [] and bodies == ["a"]
    viewport.set_filter("face")
    QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, viewport.rect().center())
    assert len(got) == 1 and got[0][0] == "a" and got[0][1]["side"] == "+z"
    assert abs(got[0][1]["offset_u"]) < 5 and abs(got[0][1]["offset_v"]) < 5                    # clicked the middle of the top face
    assert viewport._face_actor is not None
    QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, QPoint(6, 6))                      # background clears the overlay
    assert viewport._face_actor is None and bodies[-1] is None
    viewport.set_filter("body")


def test_window_uses_the_picked_face_for_commands(app):
    from satcad.gui.ui_layout import MainWindow
    session = Session()
    win = MainWindow(session, viewport_factory=_stub_viewport_class())
    a = session.add("box", length=100, width=100, height=100)
    assert win.commands._face() is None
    win._on_face(a, dict(side="+z", planar=True, offset_u=12.0, offset_v=-7.0, at_outer_face=True, point=(12, -7, 50), normal=(0, 0, 1)))
    assert win.commands._face()[1]["side"] == "+z" and "+z" in win.cue.text()
    session.add("cylinder", radius=5, height=5)                                              # any rebuild invalidates the pick
    assert win.picked_face is None and win.commands._face() is None
    win._on_face(a, dict(side=None, planar=False, offset_u=0, offset_v=0, at_outer_face=False, point=(0, 0, 0), normal=None))
    assert win.commands._face() is None and "curved" in win.cue.text()
    win.set_filter("face")
    assert win.picked_face is None and win._filter_buttons["face"].isChecked() and not win._filter_buttons["body"].isChecked()
