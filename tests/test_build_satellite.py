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
    assert {f"compute_blade_{i:02d}" for i in range(1, 13)} <= names
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
        b = box(sat, f"compute_blade_{i:02d}")
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
    for f in ("t.png", "t_core_detail.png", "t.step", "t.stl", "t.glb", "t.json", "t_report.txt"):
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
