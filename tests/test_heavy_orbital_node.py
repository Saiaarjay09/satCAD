"""The heavy-class Orbital-Node-Alpha generator (a standalone script, not part of the app), plus the X-Ray / layers / assembly-folder features it needs."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_heavy_orbital_node as heavy  # noqa: E402
from satcad import Design, DesignError  # noqa: E402

SHELL_MM = 3500


@pytest.fixture(scope="module")
def node():
    return heavy.build()


def parts_in(d, assembly):
    return [p for p in d.parts.values() if p.assembly == assembly]


def bbox_of(p):
    b = p.solid.bounding_box()
    return (b.min.X, b.min.Y, b.min.Z), (b.max.X, b.max.Y, b.max.Z)


# ===== structure =====================================================================================================================
def test_the_three_schematics_and_other_assemblies_exist_and_are_colour_coded(node):
    tree = [a["name"] for a in node.assembly_tree()["assemblies"]]
    for name in ("Primary_Bus", "Compute_Core", "HV_Power_Schematic", "Optical_Data_Schematic", "MPFL_Fluid_Schematic", "Radiator_Wings", "OISL_Turrets"):
        assert name in tree
    orange = {tuple(p.color) for p in parts_in(node, "HV_Power_Schematic") if p.name.startswith(("busbar", "solar"))}
    assert orange and all(c[0] > 0.8 and 0.3 < c[1] < 0.7 and c[2] < 0.1 for c in orange)         # orange power (two shades)
    cyan = [p for p in parts_in(node, "Optical_Data_Schematic") if p.name.startswith(("fibre", "edfa", "optical_switch"))]
    assert cyan and all(p.color[0] < 0.1 and p.color[2] > 0.7 for p in cyan)                        # cyan optical
    hot, cold = node.parts["hot_manifold_A"].color, node.parts["cold_manifold_A"].color
    assert hot[0] > 0.8 > hot[2] and cold[2] > 0.8 > cold[0]                                        # red out, blue return


def test_hull_is_a_hollow_3500_by_3500_by_5500_bus(node):
    hull = node.parts["bus_hull"]
    (x0, y0, z0), (x1, y1, z1) = bbox_of(hull)
    assert (x1 - x0, y1 - y0, z1 - z0) == pytest.approx((3500, 3500, 5500), abs=0.5)
    solid_mm3 = 3500 * 3500 * 5500
    assert hull.volume_mm3 < 0.06 * solid_mm3                                                       # a 35 mm shell, not a block
    assert hull.volume_mm3 == pytest.approx(solid_mm3 - (3500 - 70) ** 2 * (5500 - 70), rel=0.01)


def test_sixteen_server_trays_at_the_requested_size_and_pitch(node):
    trays = sorted((p for p in node.parts.values() if p.name.startswith("server_tray")), key=lambda p: bbox_of(p)[0][2])
    assert len(trays) == 16
    for p in trays:
        lo, hi = bbox_of(p)
        assert (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) == pytest.approx((2200, 1800, 65), abs=0.5)
    zs = [(bbox_of(p)[0][2] + bbox_of(p)[1][2]) / 2 for p in trays]
    assert [b - a for a, b in zip(zs, zs[1:])] == pytest.approx([220] * 15, abs=0.5)
    assert sum(zs) / 16 == pytest.approx(0, abs=0.5)                                                # stack centred in the hull


def test_corridors_around_the_trays_are_reported_honestly():
    c = heavy.corridors()
    assert c["x_clear_mm"] == pytest.approx((3500 - 70 - 2200) / 2) and c["y_clear_mm"] == pytest.approx((3500 - 70 - 1800) / 2)
    assert c["x_from_outer_skin_mm"] == pytest.approx(650)                                          # the brief's 650 mm is measured from the outer skin
    assert c["x_clear_mm"] < 650                                                                    # ...so the clear gap inside is smaller, and we say so


def test_busbars_are_40_by_10_and_one_feeds_each_tray(node):
    feeders = [p for p in node.parts.values() if p.name.startswith("busbar_feeder")]
    assert len(feeders) == 16
    for p in feeders:
        lo, hi = bbox_of(p)
        dims = sorted((hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]))
        assert dims[0] == pytest.approx(10, abs=0.1) and dims[1] == pytest.approx(40, abs=0.1)


def test_four_battery_banks_sit_near_the_base_and_fibre_conduits_are_12_mm(node):
    banks = [p for p in node.parts.values() if p.name.startswith("battery_bank")]
    assert len(banks) == 4 and all(bbox_of(p)[1][2] < -1500 for p in banks)
    for n in ("fibre_trunk", "fibre_turret_1"):
        lo, hi = bbox_of(node.parts[n])
        dims = sorted((hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]))
        assert dims[0] == pytest.approx(12, abs=0.1) and dims[1] == pytest.approx(12, abs=0.1)     # a 12 mm diameter round conduit


def test_four_oisl_turrets_have_fibre_to_the_racks(node):
    assert len(parts_in(node, "OISL_Turrets")) == 12                                                # base + barrel + lens, four times
    assert all(f"fibre_turret_{i}" in node.parts for i in range(1, 5))
    assert "flight_shield" in node.parts and "flight_computer" in node.parts


# ===== radiators =====================================================================================================================
def test_each_radiator_wing_is_4_2_m_wide_and_spans_the_16_m_tip_to_tip(node):
    for side in ("pos_y", "neg_y"):
        skins = [p for p in node.parts.values() if p.name.startswith(f"fin_top_{side}")]
        xs = [v for p in skins for v in (bbox_of(p)[0][0], bbox_of(p)[1][0])]
        assert max(xs) - min(xs) == pytest.approx(4200, abs=1)
    overall = node.measure()["overall_size_mm"]
    assert overall[0] == pytest.approx(4200, abs=5) and overall[1] > 30000                          # two 16 m wings plus the bus


def test_wing_ridges_are_a_30_copy_pattern_at_140_mm_pitch(node):
    ridges = [p for p in node.parts.values() if p.name.startswith("fin_top_pos_y")]
    assert len(ridges) == 4200 // 140 == 30
    xs = sorted((bbox_of(p)[0][0] + bbox_of(p)[1][0]) / 2 for p in ridges)
    assert [b - a for a, b in zip(xs, xs[1:])] == pytest.approx([140] * 29, abs=0.01)


def test_ridge_shape_follows_the_opening_angle():
    wide = heavy.build(dict(FIN_RIDGE_ANGLE_DEG=120)).parts["fin_top_pos_y"]
    narrow = heavy.build(dict(FIN_RIDGE_ANGLE_DEG=60)).parts["fin_top_pos_y"]
    assert wide.volume_mm3 > narrow.volume_mm3
    assert bbox_of(wide)[1][2] - bbox_of(wide)[0][2] == pytest.approx(bbox_of(narrow)[1][2] - bbox_of(narrow)[0][2], abs=0.01)   # same 35 mm height


def test_main_tubes_are_18_mm_radius_with_a_rifled_bore(node):
    p = node.parts["tube_hot_pos_y"]
    lo, hi = bbox_of(p)
    assert sorted((hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]))[:2] == pytest.approx([36, 36], abs=0.1)
    length = max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
    plain = 3.14159265 * (18 ** 2 - 16 ** 2) * length
    assert p.volume_mm3 > plain                                                                     # the 8 micro-ridges add wall inside the bore
    assert p.volume_mm3 < 3.14159265 * 18 ** 2 * length                                             # and the bore really is hollow
    hot = [q for q in node.parts.values() if q.name.startswith("tube_hot_pos_y")]
    cold = [q for q in node.parts.values() if q.name.startswith("tube_cold_pos_y")]
    assert len(hot) == len(cold) == 6


def test_rifled_cavity_has_eight_ridges_and_is_counter_clockwise():
    pts = heavy.rifled_cavity()
    area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))
    assert area2 > 0
    radii = [(x * x + y * y) ** 0.5 for x, y in pts]
    assert min(radii) == pytest.approx(heavy.TUBE_INNER_R - 2.0, abs=0.01) and max(radii) == pytest.approx(heavy.TUBE_INNER_R, abs=0.01)
    flipped = heavy.ccw(pts[::-1])                                                                  # a clockwise list is turned round to counter-clockwise
    assert sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(flipped, flipped[1:] + flipped[:1])) > 0


# ===== quality ===========================================================================================================================
def test_no_overlaps_and_nothing_floats(node):
    chk = node.check()
    assert chk["overlaps"] == []
    assert chk["floating_parts"] == []


def test_every_part_is_registered_in_an_assembly_and_has_a_layer(node):
    assert all(p.assembly for p in node.parts.values())
    layers = {p.params.get("layer") for p in node.parts.values()}
    assert {"shell", "power", "optical", "fluid"} <= layers
    assert all(p.params.get("layer") == "shell" for p in node.parts.values() if p.name in ("bus_hull",) or p.name.startswith(("server_tray", "fin_")))
    assert all(p.params.get("layer") == "power" for p in node.parts.values() if p.name.startswith(("busbar", "battery")))


# ===== parameters ======================================================================================================================
def test_the_three_parameters_are_model_variables(node):
    for k in ("BUS_WIDTH_MM", "TRAY_PITCH_MM", "FIN_RIDGE_ANGLE_DEG"):
        assert k in node.variables and k.isupper()
    assert node.variables["BUS_WIDTH_MM"] == 3500 and node.variables["TRAY_PITCH_MM"] == 220 and node.variables["FIN_RIDGE_ANGLE_DEG"] == 120


def test_changing_the_pitch_rebuilds_the_stack_instantly():
    d = heavy.build()
    d.set_variable("TRAY_PITCH_MM", 200)
    zs = sorted((bbox_of(p)[0][2] + bbox_of(p)[1][2]) / 2 for p in d.parts.values() if p.name.startswith("server_tray"))
    assert [b - a for a, b in zip(zs, zs[1:])] == pytest.approx([200] * 15, abs=0.5)
    assert d.check()["overlaps"] == []


def test_changing_the_bus_width_resizes_the_hull_and_moves_the_wings():
    d = heavy.build(dict(BUS_WIDTH_MM=4000))
    lo, hi = bbox_of(d.parts["bus_hull"])
    assert (hi[0] - lo[0], hi[1] - lo[1]) == pytest.approx((4000, 4000), abs=0.5)
    assert d.measure()["overall_size_mm"][1] > heavy.build().measure()["overall_size_mm"][1]
    assert d.check()["overlaps"] == []


def test_changing_the_ridge_angle_live_rebuilds_ridges():
    d = heavy.build()
    before = d.parts["fin_top_pos_y"].volume_mm3
    d.set_variable("FIN_RIDGE_ANGLE_DEG", 80)
    assert d.parts["fin_top_pos_y"].volume_mm3 != pytest.approx(before)


@pytest.mark.parametrize("bad", [dict(BUS_WIDTH_MM=3000), dict(TRAY_PITCH_MM=50), dict(FIN_RIDGE_ANGLE_DEG=10), dict(NOPE=1)])
def test_out_of_range_or_unknown_parameters_are_refused_with_a_reason(bad):
    with pytest.raises(DesignError):
        heavy.build(bad)


# ===== files and CLI ================================================================================================================
def test_cli_saves_json_and_step_into_the_designs_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SATCAD_DESIGNS", str(tmp_path))
    assert heavy.main(["--name", "t_heavy"]) == 0
    out = capsys.readouterr().out
    assert "no overlapping bodies" in out and "Corridors beside the trays" in out
    assert (tmp_path / "t_heavy.json").exists() and (tmp_path / "t_heavy.step").stat().st_size > 100_000
    back = Design("x")
    back.load(tmp_path / "t_heavy.json")
    assert len(back.parts) == 343 and back.variables["TRAY_PITCH_MM"] == 220


def test_cli_set_overrides_and_no_save(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SATCAD_DESIGNS", str(tmp_path))
    assert heavy.main(["--set", "TRAY_PITCH_MM=200", "--no-save"]) == 0
    assert list(tmp_path.iterdir()) == []
    assert heavy.main(["--set", "BUS_WIDTH_MM=100", "--no-save"]) == 1
    assert "Can't build this design" in capsys.readouterr().err


def test_the_shipped_example_file_opens_and_matches_the_script():
    path = ROOT / "sattelite designs" / "heavy_orbital_node.json"
    assert path.exists() and (ROOT / "sattelite designs" / "heavy_orbital_node.step").exists()
    d = Design("x")
    d.load(path)
    fresh = heavy.build()
    assert len(d.parts) == len(fresh.parts) and d.variables["FIN_RIDGE_ANGLE_DEG"] == 120
    assert json.loads(path.read_text())["features"]


def test_the_script_does_not_touch_the_old_generators():
    assert "generate_orbital_node_assembly" not in (ROOT / "generate_heavy_orbital_node.py").read_text()


# ===== the layer setting ======================================================================================================================
def test_layer_is_a_common_setting_that_assemblies_do_not_need():
    d = Design("t")
    d.add("box", "a", length=10, width=10, height=10, layer="shell")
    d.add("box", "b", length=10, width=10, height=10, x=50)
    assert d.parts["a"].params["layer"] == "shell" and d.parts["b"].params["layer"] is None


# ===== X-Ray, hide/show and the navigator ======================================================================================================
qt = pytest.importorskip("PySide6")
pytest.importorskip("vtkmodules.vtkRenderingCore")
from PySide6.QtWidgets import QApplication  # noqa: E402

from satcad.gui import theme  # noqa: E402
from satcad.gui.navigator import PartNavigator  # noqa: E402
from satcad.gui.session import Session  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    theme.apply(a)
    return a


def small_design(session):
    session.design.add("assembly", "Shell_Group")
    session.design.add("assembly", "Loops")
    session.design.add("box", "hull", length=100, width=100, height=100, assembly="Shell_Group", layer="shell")
    session.design.add("box", "tray", length=20, width=20, height=20, assembly="Shell_Group", layer="shell")
    session.design.add("box", "pipe", length=10, width=10, height=10, x=300, assembly="Loops", layer="fluid")
    session.design.add("box", "loose", length=10, width=10, height=10, x=500)
    session.changed.emit()


def window(app):
    from satcad.gui.ui_layout import MainWindow
    win = MainWindow(Session())
    try:
        win.viewport.grab_image()
    except Exception as e:
        pytest.skip(f"off-screen rendering not available: {e}")
    return win


def test_xray_makes_shell_layer_parts_85_percent_transparent_and_leaves_the_rest_solid(app):
    win = window(app)
    small_design(win.session)
    win._refresh_all()
    assert win.xray_map() == {}                                                                      # off by default
    win.set_xray(True)
    assert win.xray_button.isChecked()
    m = win.xray_map()
    assert m == {"hull": 0.15, "tray": 0.15}                                                         # 85% transparent = 15% opaque
    assert "pipe" not in m and "loose" not in m
    assert win.viewport._opacity == m
    win.set_xray(False)
    assert win.xray_map() == {} and win.viewport._opacity == {} and not win.xray_button.isChecked()


def test_xray_survives_a_rebuild_and_warns_when_nothing_is_on_the_shell_layer(app):
    win = window(app)
    log = []
    win.session.message.connect(lambda lvl, txt: log.append((lvl, txt)))
    win.session.design.add("box", "plain", length=10, width=10, height=10)
    win._refresh_all()
    win.set_xray(True)
    assert any(lvl == "warn" and "shell" in txt for lvl, txt in log)
    win.session.design.add("box", "hull", length=100, width=100, height=100, x=200, layer="shell")
    win._refresh_all()
    assert win.viewport._opacity == {"hull": 0.15}                                                   # re-applied after the scene was rebuilt


def test_see_through_parts_cannot_be_clicked_but_solid_ones_can(app):
    win = window(app)
    small_design(win.session)
    win._refresh_all()
    win.set_xray(True)
    pick = {n: win.viewport._surfaces[n].GetPickable() for n in ("hull", "pipe")}
    assert not pick["hull"] and pick["pipe"]                                                         # click through the glass to the parts inside it


def test_navigator_assembly_rows_toggle_all_their_parts(app):
    from PySide6.QtCore import Qt
    win = window(app)
    small_design(win.session)
    win._refresh_all()
    nav = win.navigator
    seen = []
    nav.hidden_changed.connect(lambda s: seen.append(set(s)))
    assert sorted(nav._members("Shell_Group")) == ["hull", "tray"]
    row = nav._items["Shell_Group"]
    row.setCheckState(0, Qt.Unchecked)                                                               # untick the folder
    assert seen[-1] == {"hull", "tray"} and nav.hidden == {"hull", "tray"}
    assert nav._items["hull"].checkState(0) == Qt.Unchecked and nav._items["pipe"].checkState(0) == Qt.Checked
    assert win.viewport._surfaces["hull"].GetVisibility() == 0 and win.viewport._surfaces["pipe"].GetVisibility() == 1
    nav._items["hull"].setCheckState(0, Qt.Checked)                                                  # one part back: the folder shows a partial tick
    assert row.checkState(0) == Qt.PartiallyChecked
    win.show_all_parts()
    assert seen[-1] == set() and nav.hidden == set() and row.checkState(0) == Qt.Checked
    assert win.viewport._surfaces["tray"].GetVisibility() == 1


def test_assembly_rows_show_their_own_names_in_the_part_navigator(app):
    from satcad.gui.session import feature_label
    assert feature_label("assembly", {}, 3, "HV_Power_Schematic") == "HV_Power_Schematic"
    assert feature_label("box", {}, 1, "block_1").startswith("Block")


def test_the_app_ships_no_heavy_satellite_either():
    sources = "".join(p.read_text() for p in (ROOT / "satcad").rglob("*.py")) + (ROOT / "satcad_app.spec").read_text()
    assert "generate_heavy_orbital_node" not in sources and "heavy_orbital_node" not in sources
