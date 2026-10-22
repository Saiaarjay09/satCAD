"""2D Production Drawing Studio: OpenCASCADE hidden-line removal, third-angle layout, dimensions, title block, bill of materials, DXF / SVG / PDF, and the blueprint tab."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math
import re
import sys
import xml.dom.minidom
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from satcad import Design, DesignError, drawing_studio as ds  # noqa: E402


# ---- designs ------------------------------------------------------------------------------------------------------------------------------
def plate_design() -> Design:
    """A 240 x 160 x 60 plate with a boss, a hole, variables and a cylinder along Z (a face-on circle in the top view)."""
    d = Design("plate")
    d.set_variable("PLATE_L", 240)
    d.set_variable("PLATE_W", 160)
    d.set_variable("PLATE_T", 60)
    d.set_variable("BOSS_R", 40)
    d.set_variable("COUNT", 3)
    d.set_variable("TILT_DEG", 30)
    d.add("box", "plate", length="=PLATE_L", width="=PLATE_W", height="=PLATE_T", material="aluminum_6061")
    d.add("cylinder", "boss", radius="=BOSS_R", height=40, z=50, material="titanium_ti6al4v")
    d.add("box", "block", length=60, width=40, height=30, x=-60, y=-20, z=-45, material="copper")
    d.add("hole", "drill", target="plate", side="+z", standard="M10", hole_type="clearance", depth=0, offset_u=70)
    return d


def sheet_of(d, **kw):
    opts = ds.DrawingOptions(**kw)
    return ds.build_sheet(d, kw.pop("selection", None) if False else None, opts)


def texts(sheet):
    return [p.text for p in sheet.prims if p.kind == "text"]


# ---- hidden-line removal --------------------------------------------------------------------------------------------------------------------
def test_projected_bounds_match_what_opencascade_draws_in_every_view():
    d = plate_design()
    proj = ds.compute_projections(d, None, None, ds.DrawingOptions())
    for name in ds.VIEW_ORDER:
        pts = [p for pl in proj.views[name].visible for p in pl]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        vb = proj.views[name].bounds
        if name == "iso":                                                      # the corners of the 3D box are not all occupied: the lines lie inside the projected box
            assert min(xs) >= vb[0] - 0.5 and min(ys) >= vb[1] - 0.5 and max(xs) <= vb[2] + 0.5 and max(ys) <= vb[3] + 0.5
        else:
            assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx(vb, abs=0.5), name
    assert proj.mode == "hlr" and proj.parts == ["plate", "boss", "block"]


def test_the_viewer_stands_on_the_documented_side():
    """The front view is seen from -Y: a block poking out of the -Y face is visible there; from +Y (the back) it is hidden behind the plate."""
    d = Design("x")
    d.add("box", "body", length=100, width=60, height=40)
    d.add("box", "nose", length=30, width=20, height=20, y=-40)
    shape = ds.prepare_projection(d).shapes
    work = ds.build_compound(shape)
    vis_front, hid_front = ds.hlr_view(work, "front", 0.1)
    # the nose (30 wide, 20 tall) outlines are visible in the front view
    assert any(abs(max(p[0] for p in pl) - min(p[0] for p in pl) - 30) < 0.1 for pl in vis_front)
    ds.VIEW_AXES["_back"] = ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0))
    try:
        vis_back, hid_back = ds.hlr_view(work, "_back", 0.1)
    finally:
        del ds.VIEW_AXES["_back"]
    assert not any(abs(max(p[0] for p in pl) - min(p[0] for p in pl) - 30) < 0.1 and abs(max(p[1] for p in pl) - min(p[1] for p in pl) - 20) < 0.1 for pl in vis_back)
    assert any(abs(max(p[0] for p in pl) - min(p[0] for p in pl) - 30) < 0.1 for pl in hid_back)         # but it is there, hidden


def test_hidden_edges_appear_dashed_and_can_be_switched_off():
    d = plate_design()
    on = ds.build_sheet(d, None, ds.DrawingOptions(show_hidden=True))
    off = ds.build_sheet(d, None, ds.DrawingOptions(show_hidden=False))
    assert on.layer("hidden") and all(p.style == "dashed" for p in on.layer("hidden"))
    assert off.layer("hidden") == []
    assert len(on.layer("visible")) == len(off.layer("visible"))
    assert ds.HIDDEN_DASH == (4.0, 2.0) and ds.LAYERS["hidden"][3] == 0.25 and ds.LAYERS["visible"][3] == 0.50 and ds.LAYERS["centerlines"][3] == 0.18


def test_centre_lines_go_through_cylinders_long_ways_and_cross_when_seen_end_on():
    d = plate_design()
    sheet = ds.build_sheet(d)
    centre = [p for p in sheet.layer("centerlines") if p.style == "center"]
    by_view = {}
    for p in centre:
        by_view.setdefault(p.group, []).append(p)
    assert len(by_view["top"]) >= 2 and len(by_view["front"]) >= 1                  # a cross end-on in the top view, one line along the boss in the front view
    assert {len(p.pts) for p in centre} == {2}


def test_a_selection_can_be_a_part_an_assembly_or_everything():
    d = plate_design()
    d.add("assembly", "arm", x=10)
    d.add("box", "arm_plate", assembly="arm", length=20, width=20, height=5, z=100)
    assert ds.resolve_selection(d, None) == list(d.parts)
    assert ds.resolve_selection(d, "boss") == ["boss"]
    assert ds.resolve_selection(d, "assembly:arm") == ["arm_plate"] == ds.resolve_selection(d, "arm")
    with pytest.raises(DesignError, match="nothing called"):
        ds.resolve_selection(d, "nope")
    one = ds.build_sheet(d, "boss", ds.DrawingOptions())
    assert one.meta["bodies"] == 1 and "BOSS" in one.meta["title"].upper() and one.meta["bom"]["bodies"] == 1


def test_a_motion_pose_moves_the_drawn_model():
    d = plate_design()
    rot = np.eye(4)
    rot[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]                                # 90 degrees about Z
    flat = ds.compute_projections(d, "plate", None)
    turned = ds.compute_projections(d, "plate", {"plate": rot})
    f, t = flat.box3d, turned.box3d
    assert (f[3] - f[0], f[4] - f[1]) == pytest.approx((t[4] - t[1], t[3] - t[0]), abs=1e-6)


def test_fast_mode_projects_bounding_boxes_for_large_assemblies():
    d = plate_design()
    full = ds.compute_projections(d, None, None, ds.DrawingOptions())
    fast = ds.compute_projections(d, None, None, ds.DrawingOptions(fast_body_limit=2))
    assert full.mode == "hlr" and fast.mode == "bbox" and any("FAST" in n for n in fast.notes)
    assert fast.box3d == pytest.approx(full.box3d)
    assert fast.views["front"].visible and ds.build_sheet(d, None, ds.DrawingOptions(fast_body_limit=2)).meta["mode"] == "bbox"


# ---- the sheet ----------------------------------------------------------------------------------------------------------------------------
def test_third_angle_layout_on_a_standard_scale_inside_the_border():
    d = plate_design()
    sheet = ds.build_sheet(d)
    v = sheet.meta["views"]
    assert int(sheet.meta["scale"].split(":")[1]) in ds.STANDARD_SCALES
    fx, fy = v["front"]["origin"]
    tx, ty = v["top"]["origin"]
    rx, ry = v["right"]["origin"]
    assert tx == pytest.approx(fx) and ty > fy and rx > fx and ry == pytest.approx(fy)             # top above the front, right beside it
    w, h = sheet.size
    for p in sheet.prims:
        for x, y in p.pts:
            assert -0.01 <= x <= w + 0.01 and -0.01 <= y <= h + 0.01, (p.layer, p.group, x, y)
    assert ds.build_sheet(d, None, ds.DrawingOptions(scale=2)).meta["scale"] == "1:2"
    assert ds.build_sheet(d, None, ds.DrawingOptions(sheet="A2")).size == (594.0, 420.0)
    with pytest.raises(DesignError):
        ds.compose_sheet(d, ds.compute_projections(d), ds.DrawingOptions(sheet="A0"))


def test_border_has_grid_zones_a_to_f_and_1_to_8():
    t = texts(ds.build_sheet(plate_design()))
    for ch in "ABCDEF":
        assert t.count(ch) == 2                                                          # left and right margin
    for n in "12345678":
        assert t.count(n) >= 2                                                           # top and bottom margin


def test_title_block_is_filled_in():
    sheet = ds.build_sheet(plate_design(), None, ds.DrawingOptions(author="Saiaarjay09 / satCAD"))
    t = texts(sheet)
    for needle in ("PLATE", "MILLIMETERS", "ISO 2768-mK", "THIRD ANGLE", "Saiaarjay09 / satCAD", "1 OF 1", sheet.meta["drawing_no"], sheet.meta["scale"]):
        assert needle in t, needle
    assert any(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", x) for x in t)
    assert sheet.meta["tolerance"] == "ISO 2768-mK" and sheet.meta["projection"] == "THIRD ANGLE" and sheet.meta["units"] == "MILLIMETERS"


def test_bom_groups_parts_and_adds_up_to_the_design_mass():
    d = plate_design()
    for i in range(3):
        d.add("box", f"tray_grid_{i + 1}", length=10, width=10, height=5, x=20 * i, y=70, material="aluminum_7075")
    names = list(d.parts)
    rows = ds.bom_rows(d, names)
    assert [r[0] for r in rows] == list(range(1, len(rows) + 1))
    tray = next(r for r in rows if r[1] == "tray")
    assert tray[3] == 3 and tray[4] == "Al 7075-T6"
    assert sum(r[5] for r in rows) == pytest.approx(sum(d.parts[n].mass_kg for n in names))
    assert any(r[4] == "Ti-6Al-4V" for r in rows) and any(r[4].startswith("Copper") for r in rows)
    assert [r[5] for r in rows] == sorted((r[5] for r in rows), reverse=True)
    sheet = ds.build_sheet(d, None, ds.DrawingOptions(bom_rows=2))
    assert sheet.meta["bom"]["shown"] == 2 and any("more groups" in x for x in texts(sheet))
    assert sheet.meta["bom"]["total_mass_kg"] == pytest.approx(sum(d.parts[n].mass_kg for n in names))


def test_bom_csv_has_every_group(tmp_path):
    d = plate_design()
    path = ds.export_bom_csv(d, list(d.parts), tmp_path / "bom.csv")
    lines = path.read_text().strip().splitlines()
    assert lines[0] == "item,part,sub-assembly,quantity,material,mass_kg" and len(lines) == 1 + len(ds.bom_rows(d, list(d.parts)))


# ---- dimensions ----------------------------------------------------------------------------------------------------------------------------
def test_iso_2768_general_tolerances():
    assert ds.general_tolerance(2.0) == 0.1 and ds.general_tolerance(25) == 0.2 and ds.general_tolerance(100) == 0.3 and ds.general_tolerance(300) == 0.5
    assert ds.general_tolerance(700) == 0.8 and ds.general_tolerance(1500) == 1.2 and ds.general_tolerance(3000) == 2.0 and ds.general_tolerance(4500) is None
    assert ds.general_tolerance(100, "f") == 0.15 and ds.general_tolerance(100, "c") == 0.8
    assert ds.dimension_text(240.0, "m", "PLATE_L") == "PLATE_L = 240.0 ±0.5" and ds.dimension_text(4500.0) == "4500.0 REF"
    assert ds.dimension_text(80.0, "m", "BOSS_R", diameter=True) == "Ø80.0 ±0.3 (2 x BOSS_R)"


def test_overall_and_parameter_linked_dimensions():
    d = plate_design()
    sheet = ds.build_sheet(d)
    by = {(x.get("axis"), x.get("parameter")): x for x in sheet.dims}
    overall = {x["axis"]: x["value"] for x in sheet.dims if x.get("overall")}
    assert overall == {"x": pytest.approx(240.0), "y": pytest.approx(160.0), "z": pytest.approx(130.0)}        # plate + boss + block extents
    assert any(x.get("parameter") == "PLATE_L" and x.get("overall") for x in sheet.dims)                      # an overall size that is a variable is named
    assert any(x.get("parameter") == "PLATE_W" and x.get("overall") for x in sheet.dims)
    dia = next(x for x in sheet.dims if x.get("diameter"))
    assert dia["parameter"] == "BOSS_R" and dia["value"] == pytest.approx(80.0) and dia["text"] == "Ø80.0 ±0.3 (2 x BOSS_R)"
    assert not any(x.get("parameter") in ("COUNT", "TILT_DEG") for x in sheet.dims)                            # a count and an angle are not drawn as lengths
    assert by[("x", "PLATE_L")]["text"].startswith("PLATE_L = 240.0")


def test_dimensions_have_extension_lines_filled_arrows_and_centred_text():
    sheet = ds.build_sheet(plate_design())
    dim_prims = sheet.layer("dimensions")
    assert any(p.kind == "fill" and len(p.pts) == 3 for p in dim_prims)                        # arrow heads
    assert sum(1 for p in dim_prims if p.kind == "fill") % 2 == 0
    assert any(p.kind == "line" and (p.weight or 0) == 0.18 for p in dim_prims)                # thin extension lines
    assert any(p.kind == "text" and p.anchor == "cb" for p in dim_prims)
    assert any(p.kind == "text" and p.rot == 90.0 for p in dim_prims)                           # vertical dimensions read from the right


def test_changing_a_variable_updates_the_drawing_dimensions():
    d = plate_design()
    first = ds.build_sheet(d)
    d.set_variable("PLATE_L", 300)
    second = ds.build_sheet(d)
    v1 = next(x["value"] for x in first.dims if x.get("overall") and x["axis"] == "x")
    v2 = next(x for x in second.dims if x.get("overall") and x["axis"] == "x")
    assert (v1, v2["value"]) == pytest.approx((240.0, 300.0)) and v2["text"].startswith("PLATE_L = 300.0")


# ---- DXF -------------------------------------------------------------------------------------------------------------------------------------
def test_dxf_has_the_five_layers_with_colours_and_line_types(tmp_path):
    sheet = ds.build_sheet(plate_design())
    path = ds.export_dxf(sheet, tmp_path / "x.dxf")
    raw = path.read_text().splitlines()
    pairs = list(zip(raw[0::2], raw[1::2]))
    assert ("9", "$ACADVER") in pairs and ("1", "AC1015") in pairs and ("70", "4") in pairs
    layer_defs = []
    for i, (code, val) in enumerate(pairs):
        if (code, val) == ("0", "LAYER"):
            rec = {}
            for c, v in pairs[i + 1:i + 14]:
                if c == "0":
                    break
                rec[c] = v
            layer_defs.append(rec)
    wanted = {"0_VISIBLE": ("7", "CONTINUOUS"), "1_HIDDEN": ("8", "DASHED"), "2_CENTERLINES": ("1", "CENTER"), "3_DIMENSIONS": ("3", "CONTINUOUS"), "4_TITLEBLOCK_BOM": ("5", "CONTINUOUS")}
    got = {r["2"]: (r["62"], r["6"]) for r in layer_defs if r["2"] in wanted}
    assert got == wanted
    handles = [v for c, v in pairs if c == "5"]
    assert len(handles) == len(set(handles))


def test_dxf_reads_back_clean_with_ezdxf(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    sheet = ds.build_sheet(plate_design())
    path = ds.export_dxf(sheet, tmp_path / "x.dxf")
    doc = ezdxf.readfile(str(path))
    assert doc.dxfversion == "AC1015"
    audit = doc.audit()
    assert not audit.errors and not audit.fixes
    msp = doc.modelspace()
    layers = {e.dxf.layer for e in msp}
    assert {"0_VISIBLE", "1_HIDDEN", "2_CENTERLINES", "3_DIMENSIONS", "4_TITLEBLOCK_BOM"} <= layers
    assert len(list(msp.query("LWPOLYLINE"))) == sum(1 for p in sheet.prims if p.kind == "line")
    assert len(list(msp.query("SOLID"))) == sum(1 for p in sheet.prims if p.kind == "fill")
    assert {e.dxf.linetype for e in msp.query("LWPOLYLINE[layer=='1_HIDDEN']")} == {"DASHED"}
    hidden = [e for e in msp.query("LWPOLYLINE") if e.dxf.layer == "1_HIDDEN"]
    assert hidden and all(e.dxf.lineweight == 25 for e in hidden)
    dims = [e for e in msp.query("TEXT") if e.dxf.layer == "3_DIMENSIONS"]
    assert any("PLATE_L" in e.dxf.text for e in dims)


# ---- SVG -----------------------------------------------------------------------------------------------------------------------------------------
def test_svg_is_grouped_and_styled(tmp_path):
    sheet = ds.build_sheet(plate_design())
    path = ds.export_svg(sheet, tmp_path / "x.svg")
    dom = xml.dom.minidom.parse(str(path))
    ids = [g.getAttribute("id") for g in dom.getElementsByTagName("g")]
    assert ids == ["visible_edges", "hidden_edges", "centerlines", "dimensions", "titleblock"]
    groups = {g.getAttribute("id"): g for g in dom.getElementsByTagName("g")}
    hidden = groups["hidden_edges"].getElementsByTagName("path")
    assert len(hidden) == len(sheet.layer("hidden")) and all(p.getAttribute("stroke-dasharray") == "4 2" for p in hidden)
    assert all(p.getAttribute("stroke-width") == "0.5" for p in groups["visible_edges"].getElementsByTagName("path"))
    assert any(p.getAttribute("stroke-dasharray") == "12 2 2 2" for p in groups["centerlines"].getElementsByTagName("path"))
    assert "PLATE_L" in path.read_text()
    root = dom.documentElement
    assert root.getAttribute("width") == "420mm" and root.getAttribute("viewBox") == "0 0 420 297"
    dark = ds.export_svg(sheet, tmp_path / "d.svg", "dark").read_text()
    assert "#0B132B" in dark and "#0B132B" not in path.read_text()


# ---- PDF -----------------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from satcad.gui import theme
    a = QApplication.instance() or QApplication([])
    theme.apply(a)
    return a


def test_pdf_is_a_landscape_a3_vector_sheet(app, tmp_path):
    sheet = ds.build_sheet(plate_design())
    path = ds.export_pdf(sheet, tmp_path / "x.pdf")
    raw = path.read_bytes()
    assert raw.startswith(b"%PDF") and len(raw) > 5000
    box = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", raw)
    assert box and float(box.group(1)) == pytest.approx(1190.55, abs=1.0) and float(box.group(2)) == pytest.approx(841.89, abs=1.0)
    a2 = ds.export_pdf(ds.build_sheet(plate_design(), None, ds.DrawingOptions(sheet="A2")), tmp_path / "y.pdf").read_bytes()
    box = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", a2)
    assert float(box.group(1)) == pytest.approx(1683.78, abs=1.0)


# ---- the Qt scene, the worker and the tab --------------------------------------------------------------------------------------------------------
def test_scene_draws_every_layer_and_switches_theme(app):
    from satcad.gui.blueprint_view import BlueprintScene
    sheet = ds.build_sheet(plate_design())
    scene = BlueprintScene()
    scene.load(sheet, "dark")
    assert scene.backgroundBrush().color().name().lower() == "#060b1a" and scene._paper.brush().color().name().lower() == "#0b132b"
    assert set(scene._layer_items) >= {"visible", "hidden", "centerlines", "dimensions", "titleblock", "text"}
    visible = scene._layer_items["visible"][0].pen()
    assert visible.widthF() == pytest.approx(0.5) and visible.color().name().lower() == ds.THEMES["dark"]["visible"]
    hidden = scene._layer_items["hidden"][0].pen()
    assert hidden.widthF() == pytest.approx(0.25) and list(hidden.dashPattern()) == pytest.approx([16.0, 8.0])         # 4 mm dash, 2 mm gap, in pen widths
    scene.set_theme("print")
    assert scene._paper.brush().color().name().lower() == "#ffffff" and scene._layer_items["visible"][0].pen().color().name().lower() == "#000000"
    assert scene.items() and len(scene._layer_items["visible"]) >= 1


def test_blueprint_studio_runs_the_projection_on_a_thread_and_reports_progress(app):
    from satcad.gui.blueprint_view import BlueprintStudio
    studio = BlueprintStudio()
    seen = []
    studio.sheet_ready.connect(lambda s: seen.append(s))
    d = plate_design()
    assert studio.generate(d, "")
    assert studio.worker is not None
    studio.wait()
    assert len(seen) == 1 and studio.sheet is seen[0] and studio.live
    assert "scale" in studio.label.text() and not studio.progress.isVisible()
    assert studio.generate(Design("empty")) is False and "Can't draw" in studio.label.text()
    studio.set_theme("print")
    assert studio.theme == "print"


def test_hidden_toggle_regenerates_without_dashed_lines(app):
    from satcad.gui.blueprint_view import BlueprintStudio
    studio = BlueprintStudio()
    studio.generate(plate_design())
    studio.wait()
    assert studio.sheet.layer("hidden")
    studio.set_hidden(False)
    studio.wait()
    assert studio.sheet.layer("hidden") == []


def test_stale_results_are_ignored(app):
    from satcad.gui.blueprint_view import BlueprintStudio
    studio = BlueprintStudio()
    studio.design = plate_design()
    studio._generation = 5
    studio._on_done(object(), 4)                                              # an older projection finishing late
    assert studio.sheet is None


def _window(design):
    from satcad.gui.session import Session
    from satcad.gui.ui_layout import MainWindow
    win = MainWindow(Session(design))
    try:
        win.show()
        win.viewport.grab_image()
    except Exception as e:
        pytest.skip(f"off-screen rendering is not available here: {e}")
    return win


def test_drawing_tab_buttons_generate_switch_and_export(app, tmp_path):
    from PySide6.QtWidgets import QToolButton
    win = _window(plate_design())
    tabs = [win.ribbon.tabText(i) for i in range(win.ribbon.count())]
    assert "Drawing" in tabs
    texts_ = {b.text() for b in win.ribbon.findChildren(QToolButton)}
    assert {"Generate 2D Drawing Sheet", "Hidden Lines", "Export .DXF", "Export .SVG", "Export .PDF"} <= texts_
    assert [win.centre_tabs.tabText(i) for i in range(win.centre_tabs.count())] == ["3D Model", "2D Blueprint Studio"]
    items = [win.draw_selector.itemText(i) for i in range(win.draw_selector.count())]
    assert items[:2] == ["Full assembly", "Selected part (feature tree)"] and {"plate", "boss", "block"} <= set(items)
    assert win.export_drawing("dxf", str(tmp_path / "a.dxf")) is False                    # nothing generated yet
    win.generate_drawing()
    win.blueprint.wait()
    assert win.centre_tabs.currentWidget() is win.blueprint and win.blueprint.sheet is not None
    for fmt in ("dxf", "svg", "pdf"):
        assert win.export_drawing(fmt, str(tmp_path / f"a.{fmt}")) and (tmp_path / f"a.{fmt}").stat().st_size > 1000
    win.draw_selector.setCurrentText("boss")
    win.generate_drawing()
    win.blueprint.wait()
    assert win.blueprint.sheet.meta["bodies"] == 1
    win.theme_button.setChecked(True)
    win.toggle_blueprint_theme()
    assert win.blueprint.theme == "print"


def test_the_sheet_follows_the_design_and_the_motion_pose_live(app):
    win = _window(plate_design())
    win.generate_drawing()
    win.blueprint.wait()
    first = next(x["value"] for x in win.blueprint.sheet.dims if x.get("overall") and x["axis"] == "x")
    win.session.set_variable("PLATE_L", 300) if hasattr(win.session, "set_variable") else win.session.design.set_variable("PLATE_L", 300)
    win.session.changed.emit()
    win.blueprint.timer.stop()
    win.blueprint._refresh_now()
    win.blueprint.wait()
    second = next(x["value"] for x in win.blueprint.sheet.dims if x.get("overall") and x["axis"] == "x")
    assert (first, second) == pytest.approx((240.0, 300.0))


def test_empty_design_warns_instead_of_drawing(app):
    from satcad.gui.session import Session
    from satcad.gui.ui_layout import MainWindow
    log = []
    s = Session()
    s.message.connect(lambda lvl, txt: log.append((lvl, txt)))
    win = MainWindow(s)
    win.generate_drawing()
    assert log and log[-1][0] == "warn"


def test_cli(tmp_path, app):
    d = plate_design()
    src = tmp_path / "plate.json"
    d.save(src)
    assert ds.main([str(src), "--out", str(tmp_path / "o"), "--formats", "dxf", "svg"]) == 0
    assert {p.name for p in (tmp_path / "o").iterdir()} == {"plate.dxf", "plate.svg"}
    assert ds.main([str(src), "--part", "boss", "--out", str(tmp_path / "p"), "--formats", "svg"]) == 0 and (tmp_path / "p" / "plate_boss.svg").exists()
    assert ds.main([str(src), "--part", "nope", "--out", str(tmp_path / "q")]) == 1
    assert ds.main([str(tmp_path / "missing.json")]) == 1


def test_the_deployment_ratio_slider_re_projects_the_sheet(app):
    """Moving a Motion Studio parameter poses the model, re-runs the projection and updates the overall dimensions."""
    d = Design("hinged")
    d.add("assembly", "arm")
    d.add("box", "hub", length=100, width=100, height=100)
    d.add("box", "boom", length=400, width=40, height=40, x=250, assembly="arm")
    win = _window(d)
    win.motion.load_dict({"engine": {"equations": ["arm_a(t) = 90 * (1 - DEPLOYMENT_RATIO)"], "params": {"DEPLOYMENT_RATIO": 1.0}, "ranges": {"DEPLOYMENT_RATIO": [0, 1]}},
                          "joints": [{"name": "Arm_A", "kind": "revolute", "assembly": "arm", "axis": [0, 0, 1], "pivot": [50, 0, 0], "expr": "arm_a"}], "expose": ["DEPLOYMENT_RATIO"]})
    win.generate_drawing()
    win.blueprint.wait()

    def overall():
        return {x["axis"]: x["value"] for x in win.blueprint.sheet.dims if x.get("overall")}
    deployed = overall()
    assert deployed["x"] == pytest.approx(500.0) and deployed["y"] == pytest.approx(100.0)
    win.motion.set_parameter("DEPLOYMENT_RATIO", 0.0)                                  # fold the boom 90 degrees
    win.blueprint.timer.stop()
    win.blueprint._refresh_now()
    win.blueprint.wait()
    folded = overall()
    assert folded["x"] == pytest.approx(120.0, abs=1.0) and folded["y"] == pytest.approx(450.0, abs=1.0)         # the 40 mm wide boom now sticks out of the hub side
    assert win.blueprint.matrices is not None
