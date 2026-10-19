"""Tests for build_satellite.py: the orbital AI datacentre model."""
import subprocess
import sys
from pathlib import Path

import pytest

import build_satellite as bs
from satcad import DesignError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def sat():
    return bs.build()


def box(sat, name):
    return sat.describe(name)


def test_all_four_subsystems_exist(sat):
    names = set(sat.parts)
    assert "chassis" in names
    assert {f"compute_blade_{i}" for i in range(1, 13)} <= names
    assert {"radiator_left", "radiator_right"} <= names
    assert {"oisl_front_gimbal", "oisl_front_telescope", "oisl_rear_gimbal", "oisl_rear_telescope"} <= names
    assert {"solar_wing_top", "solar_wing_bottom"} <= names
    assert len(names) == 21


def test_clean_check_no_overlaps_no_floating_parts(sat):
    result = sat.check()
    assert result["ok"] and result["overlaps"] == [] and result["floating_parts"] == []


def test_radiators_are_flat_symmetric_and_span_the_requested_wingspan(sat):
    left, right = box(sat, "radiator_left"), box(sat, "radiator_right")
    assert left["bbox_max_mm"][1] == pytest.approx(bs.RADIATOR_WINGSPAN / 2)
    assert right["bbox_min_mm"][1] == pytest.approx(-bs.RADIATOR_WINGSPAN / 2)
    assert left["size_mm"] == right["size_mm"]
    # thin in Z: they lie flat in the X-Y plane
    assert left["size_mm"][2] == pytest.approx(bs.RADIATOR_THICKNESS)
    assert left["size_mm"][0] == pytest.approx(bs.RADIATOR_CHORD)


def test_radiators_are_proportionally_much_larger_than_the_chassis(sat):
    _, facts = bs.report(sat)
    assert facts["radiator_to_chassis_plan_ratio"] > 5
    chassis_footprint = bs.CHASSIS_LENGTH * bs.CHASSIS_WIDTH / 1e6
    assert facts["radiator_area_m2"] == pytest.approx(2 * 9.8 * 6.0)
    assert facts["radiator_area_m2"] > 5 * chassis_footprint


def test_solar_wings_are_perpendicular_to_the_radiators(sat):
    rad, sol = box(sat, "radiator_left"), box(sat, "solar_wing_top")
    # radiator plane normal is Z (thin in Z); solar plane normal is Y (thin in Y): the planes are at right angles
    assert rad["size_mm"].index(min(rad["size_mm"])) == 2
    assert sol["size_mm"].index(min(sol["size_mm"])) == 1
    assert sol["size_mm"][1] == pytest.approx(bs.SOLAR_THICKNESS)
    # and the wings reach out of the top / bottom faces
    assert sol["bbox_min_mm"][2] == pytest.approx(bs.CHASSIS_HEIGHT / 2)
    assert box(sat, "solar_wing_bottom")["bbox_max_mm"][2] == pytest.approx(-bs.CHASSIS_HEIGHT / 2)


def test_oisl_transceivers_sit_front_and_rear_on_the_axis(sat):
    half = bs.CHASSIS_LENGTH / 2
    tip = half + bs.GIMBAL_LENGTH + bs.TRANSCEIVER_LENGTH
    front, rear = box(sat, "oisl_front_telescope"), box(sat, "oisl_rear_telescope")
    assert front["bbox_max_mm"][0] == pytest.approx(tip) and rear["bbox_min_mm"][0] == pytest.approx(-tip)
    assert front["size_mm"][1] == pytest.approx(2 * bs.TRANSCEIVER_RADIUS)           # diameter in Y and Z
    assert front["size_mm"][2] == pytest.approx(2 * bs.TRANSCEIVER_RADIUS)
    assert box(sat, "oisl_front_gimbal")["bbox_min_mm"][0] == pytest.approx(half)     # gimbal sits on the chassis face


def test_blades_stand_on_the_floor_inside_the_chassis(sat):
    c = box(sat, "chassis")
    wall = bs.CHASSIS_WALL
    for i in range(1, bs.BLADE_COUNT + 1):
        b = box(sat, f"compute_blade_{i}")
        assert b["bbox_min_mm"][2] == pytest.approx(c["bbox_min_mm"][2] + wall)                  # on the inner floor
        assert b["bbox_max_mm"][0] < c["bbox_max_mm"][0] - wall and b["bbox_min_mm"][0] > c["bbox_min_mm"][0] + wall
        assert abs(b["bbox_max_mm"][1]) < c["bbox_max_mm"][1] - wall


def test_changing_variables_rebuilds_the_model():
    wide = bs.build({"RADIATOR_WINGSPAN": 30000})
    assert wide.describe("radiator_left")["bbox_max_mm"][1] == pytest.approx(15000)
    long = bs.build({"CHASSIS_LENGTH": 8000})
    assert long.describe("oisl_front_telescope")["bbox_max_mm"][0] == pytest.approx(4000 + 300 + 800)
    few = bs.build({"BLADE_COUNT": 5})
    assert len([n for n in few.parts if n.startswith("compute_blade")]) == 5
    big = bs.build({"TRANSCEIVER_RADIUS": 400})
    assert big.describe("oisl_rear_telescope")["size_mm"][1] == pytest.approx(800)
    assert wide.check()["ok"] and long.check()["ok"] and few.check()["ok"] and big.check()["ok"]


def test_tilt_raises_both_radiator_tips_equally():
    import math
    d = bs.build({"RADIATOR_TILT_DEG": 20})
    left, right = d.describe("radiator_left"), d.describe("radiator_right")
    panel_length = (bs.RADIATOR_WINGSPAN - bs.CHASSIS_WIDTH) / 2
    expected = panel_length * math.sin(math.radians(20))
    assert left["bbox_max_mm"][2] == pytest.approx(expected, rel=0.02)
    assert right["bbox_max_mm"][2] == pytest.approx(left["bbox_max_mm"][2], abs=1)      # a symmetric V, not a twist
    assert d.check()["ok"]


@pytest.mark.parametrize("override, message", [
    ({"RADIATOR_WINGSPAN": 2000}, "tip-to-tip"),
    ({"BLADE_PITCH": 100}, "overlap"),
    ({"BLADE_COUNT": 100}, "interior"),
    ({"BLADE_HEIGHT": 3000}, "taller"),
    ({"TRANSCEIVER_RADIUS": 900}, "gimbal is wider"),
    ({"CHASSIS_LENGTH": -5}, "greater than 0"),
    ({"CHASSIS_WALL": 1300}, "too thick"),
    ({"GIMBAL_RADIUS_FACTOR": 0.5}, "at least as wide"),
])
def test_bad_numbers_give_friendly_errors(override, message):
    with pytest.raises(DesignError, match=message):
        bs.build(override)


def test_report_warns_when_radiators_cannot_reject_the_solar_power():
    d = bs.build({"RADIATOR_WINGSPAN": 6000})
    text, facts = bs.report(d, {"RADIATOR_WINGSPAN": 6000})
    assert facts["heat_balance"] < 1 and "WARNING" in text


def test_report_numbers_match_geometry(sat):
    text, facts = bs.report(sat)
    assert facts["solar_area_m2"] == pytest.approx(2 * 14.0 * 12.0)
    assert facts["mass_kg"] == pytest.approx(sat.measure()["total_mass_kg"])
    assert 100 < facts["heat_rejection_kw"] < 170 and facts["heat_balance"] > 1
    assert "No overlapping parts" in text


def test_command_line_writes_all_outputs(tmp_path):
    assert bs.main(["--out", str(tmp_path), "--name", "t", "--set", "BLADE_COUNT=6", "--set", "radiator_wingspan=26000.0"]) == 0
    folder = tmp_path / "t"
    for f in ("t.png", "t_core_detail.png", "t.step", "t.stl", "t.glb", "t.json", "t_report.txt", "t_bom.csv", "t_viewer.html"):
        assert (folder / f).stat().st_size > 0, f
    assert (folder / "t.png").read_bytes()[:4] == b"\x89PNG"
    assert "BLADE_COUNT" not in (folder / "t_report.txt").read_text()               # report is about the design, not the flags


def test_command_line_rejects_unknown_and_invalid_settings(tmp_path, capsys):
    with pytest.raises(SystemExit):
        bs.main(["--out", str(tmp_path), "--set", "NOT_A_SETTING=1"])
    with pytest.raises(SystemExit):
        bs.main(["--out", str(tmp_path), "--set", "BLADE_COUNT=lots"])
    with pytest.raises(SystemExit):
        bs.main(["--out", str(tmp_path), "--set", "BLADE_COUNT=6.5"])
    assert bs.main(["--out", str(tmp_path), "--set", "RADIATOR_WINGSPAN=100"]) == 1
    assert "Can't build this design" in capsys.readouterr().err


def test_script_runs_from_any_folder(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "build_satellite.py"), "--out", str(tmp_path)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr
    assert "DESIGN REPORT" in result.stdout and (tmp_path / "orbital_datacentre" / "orbital_datacentre.step").exists()


# ---- the model is now built from named variables and formulas, mirrors, patterns and cutouts -------------------------
def test_every_dimension_is_a_named_variable(sat):
    assert sat.variables["radiator_wingspan"] == bs.RADIATOR_WINGSPAN and sat.variables["blade_count"] == bs.BLADE_COUNT
    assert sat.features["chassis"].raw["length"] == "=chassis_length"
    assert sat.features["radiator_left"].raw["length"] == "=(radiator_wingspan - chassis.width) / 2"


def test_saved_recipe_stays_parametric_after_reloading(tmp_path):
    d = bs.build()
    path = d.save(tmp_path / "sat.json")
    from satcad import Design
    e = Design()
    e.load(path)
    assert e.measure()["total_mass_kg"] == pytest.approx(d.measure()["total_mass_kg"])
    e.set_variable("radiator_wingspan", 30000)                                 # what Claude's set_variable tool does
    assert e.describe("radiator_left")["bbox_max_mm"][1] == pytest.approx(15000)
    assert e.describe("radiator_right")["bbox_min_mm"][1] == pytest.approx(-15000)
    e.set_variable("chassis_length", 8000)
    assert e.describe("oisl_front_telescope")["bbox_max_mm"][0] == pytest.approx(4000 + 300 + 800)
    assert e.describe("oisl_rear_telescope")["bbox_min_mm"][0] == pytest.approx(-(4000 + 300 + 800))
    e.set_variable("blade_count", 6)
    assert len([n for n in e.parts if n.startswith("compute_blade_")]) == 6
    assert e.check()["ok"]


def test_paired_parts_are_true_mirrors_and_cannot_drift_apart(sat):
    for src, mirrored in (("radiator_left", "radiator_right"), ("oisl_front_telescope", "oisl_rear_telescope"),
                          ("solar_wing_top", "solar_wing_bottom")):
        a, b = sat.describe(src), sat.describe(mirrored)
        assert a["size_mm"] == b["size_mm"] and a["mass_kg"] == b["mass_kg"]
    d = bs.build()
    d.edit("radiator_left", width=3000)                                        # edit only the left one...
    assert d.describe("radiator_right")["size_mm"] == d.describe("radiator_left")["size_mm"]     # ...the right follows


def test_optical_feedthrough_holes_are_cut_in_the_chassis_wall():
    from satcad import Design
    plain = Design()
    plain.add("chassis", "c", length=bs.CHASSIS_LENGTH, width=bs.CHASSIS_WIDTH, height=bs.CHASSIS_HEIGHT,
              wall=bs.CHASSIS_WALL, fillet=bs.CHASSIS_EDGE_RADIUS)
    d = bs.build()
    removed = plain.describe("c")["volume_cm3"] - d.describe("chassis")["volume_cm3"]
    port = 3.141592653589793 * (bs.OISL_PORT_FACTOR * bs.TRANSCEIVER_RADIUS) ** 2 * bs.CHASSIS_WALL / 1000
    assert removed == pytest.approx(2 * port, rel=0.02)                          # one hole under each terminal


def test_mass_properties_are_physically_sensible(sat):
    text, facts = bs.report(sat)
    m = facts["principal_moments_kg_m2"]
    assert 0 < m[0] <= m[1] <= m[2] and m[2] <= m[0] + m[1]                       # a real rigid body obeys the triangle inequality
    assert facts["centre_of_mass_mm"][0] == pytest.approx(0, abs=1) and facts["centre_of_mass_mm"][1] == pytest.approx(0, abs=1)
    assert "MASS PROPERTIES" in text and "Principal moments" in text and "NOT INCLUDED" in text
    # the wings in the X-Z plane make the satellite hardest to turn about Y; the long radiators dominate about Z
    i = facts["inertia_kg_m2"]
    assert i[1][1] > i[0][0] > i[2][2]


def test_bom_and_viewer_are_written(tmp_path):
    assert bs.main(["--out", str(tmp_path), "--name", "b"]) == 0
    csv = (tmp_path / "b" / "b_bom.csv").read_text().splitlines()
    assert csv[0].startswith("part,type") and csv[-1].startswith("TOTAL") and len(csv) == 23     # header + 21 parts + total
    html = (tmp_path / "b" / "b_viewer.html").read_text()
    assert "radiator_left" in html and "three.min.js" in html
