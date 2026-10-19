"""Tests for build_satellite.py: the orbital AI datacentre model (subsystems, hinges, bolted optics, analysis)."""
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import build_satellite as bs
from satcad import Design, DesignError
from satcad.fem import from_design

ROOT = Path(__file__).resolve().parents[1]
PI = math.pi
HALF = bs.CHASSIS_LENGTH / 2


@pytest.fixture(scope="module")
def sat():
    return bs.build()


def box(sat, name):
    return sat.describe(name)


# ---- structure of the model -------------------------------------------------------------------------------------------------
def test_all_subsystems_exist(sat):
    names = set(sat.parts)
    assert "chassis" in names
    assert {f"compute_blade_{i}" for i in range(1, 13)} <= names
    assert {"radiator_left_panel", "radiator_right_panel"} <= names
    for end in ("front", "rear"):
        assert {f"oisl_{end}_flange", f"oisl_{end}_gimbal", f"oisl_{end}_telescope"} <= names
        assert {f"oisl_{end}_bolt_{i}" for i in range(1, 9)} <= names
    assert {"solar_wing_top", "solar_wing_bottom"} <= names
    assert len(names) == 39                                            # 1 + 12 blades + 2 radiators + 2 x (3 + 8) optics + 2 wings


def test_subsystem_assemblies_and_roll_ups(sat):
    tree = sat.assembly_tree()
    top = {a["name"]: a for a in tree["assemblies"]}
    assert set(top) == {"compute", "thermal", "optical", "power"} and tree["ungrouped"] == ["chassis"]
    assert {c["name"] for c in top["thermal"]["children"]} == {"radiator_left", "radiator_right"}
    assert {c["name"] for c in top["optical"]["children"]} == {"optical_front", "optical_rear"}
    assert sat.describe("oisl_front_flange")["assembly"] == "optical/optical_front"
    assert sat.describe("oisl_rear_bolt_5")["assembly"] == "optical/optical_rear"          # pattern copies stay in the subsystem
    total = sum(a["mass_kg"] for a in tree["assemblies"]) + sat.describe("chassis")["mass_kg"]
    assert total == pytest.approx(sat.measure()["total_mass_kg"], rel=1e-4)
    assert top["compute"]["mass_kg"] == pytest.approx(12 * sat.describe("compute_blade_1")["mass_kg"], rel=1e-3)


def test_clean_check_threads_are_engagement_not_overlap(sat):
    result = sat.check()
    assert result["ok"] and result["overlaps"] == [] and result["floating_parts"] == []
    assert len(result["thread_engagements"]) == 16                        # one per screw, in the tapped chassis holes


# ---- radiators, solar, optics geometry -----------------------------------------------------------------------------------------
def test_radiators_are_flat_symmetric_and_span_the_requested_wingspan(sat):
    left, right = box(sat, "radiator_left_panel"), box(sat, "radiator_right_panel")
    assert left["bbox_max_mm"][1] == pytest.approx(bs.RADIATOR_WINGSPAN / 2)
    assert right["bbox_min_mm"][1] == pytest.approx(-bs.RADIATOR_WINGSPAN / 2)
    assert left["size_mm"] == right["size_mm"]
    assert left["size_mm"][2] == pytest.approx(bs.RADIATOR_THICKNESS)               # thin in Z: they lie flat in the X-Y plane
    assert left["size_mm"][0] == pytest.approx(bs.RADIATOR_CHORD)


def test_radiators_are_proportionally_much_larger_than_the_chassis(sat):
    _, facts = bs.report(sat)
    assert facts["radiator_to_chassis_plan_ratio"] > 5
    assert facts["radiator_area_m2"] == pytest.approx(2 * 9.8 * 6.0)


def test_solar_wings_are_perpendicular_to_the_radiators(sat):
    rad, sol = box(sat, "radiator_left_panel"), box(sat, "solar_wing_top")
    assert rad["size_mm"].index(min(rad["size_mm"])) == 2 and sol["size_mm"].index(min(sol["size_mm"])) == 1
    assert sol["size_mm"][1] == pytest.approx(bs.SOLAR_THICKNESS)
    assert sol["bbox_min_mm"][2] == pytest.approx(bs.CHASSIS_HEIGHT / 2)
    assert box(sat, "solar_wing_bottom")["bbox_max_mm"][2] == pytest.approx(-bs.CHASSIS_HEIGHT / 2)


def test_oisl_terminals_are_stacked_flange_gimbal_telescope_front_and_rear(sat):
    ft = bs.FLANGE_THICKNESS
    for sign, end in ((1, "front"), (-1, "rear")):
        flange, gimbal, tele = (box(sat, f"oisl_{end}_{k}") for k in ("flange", "gimbal", "telescope"))
        edge = lambda b, which: (b["bbox_max_mm"][0] if which == "out" else b["bbox_min_mm"][0]) * sign
        # measured outward from the chassis face (sign makes front and rear mirror images)
        lo = lambda b: min(abs(b["bbox_min_mm"][0]), abs(b["bbox_max_mm"][0]))
        hi = lambda b: max(abs(b["bbox_min_mm"][0]), abs(b["bbox_max_mm"][0]))
        assert (lo(flange), hi(flange)) == pytest.approx((HALF, HALF + ft))
        assert (lo(gimbal), hi(gimbal)) == pytest.approx((HALF + ft, HALF + ft + bs.GIMBAL_LENGTH))
        assert hi(tele) == pytest.approx(HALF + ft + bs.GIMBAL_LENGTH + bs.TRANSCEIVER_LENGTH)
        assert tele["size_mm"][1] == pytest.approx(2 * bs.TRANSCEIVER_RADIUS)
        assert gimbal["size_mm"][1] == pytest.approx(2 * bs.TRANSCEIVER_RADIUS * bs.GIMBAL_RADIUS_FACTOR)
        assert flange["size_mm"][1] == pytest.approx(2 * bs.TRANSCEIVER_RADIUS * bs.GIMBAL_RADIUS_FACTOR * bs.FLANGE_RADIUS_FACTOR)


def test_flange_ring_clears_the_optical_port(sat):
    flange = sat.parts["oisl_front_flange"].solid
    from build123d import Vector
    port_r = bs.OISL_PORT_FACTOR * bs.TRANSCEIVER_RADIUS
    outer_r = bs.TRANSCEIVER_RADIUS * bs.GIMBAL_RADIUS_FACTOR * bs.FLANGE_RADIUS_FACTOR
    assert not flange.is_inside(Vector(HALF + 10, port_r - 5, 0))                  # the bore is empty
    assert flange.is_inside(Vector(HALF + 10, port_r + 20, 0))                      # material outside the port
    assert not flange.is_inside(Vector(HALF + 10, outer_r + 5, 0))


def test_bolt_ring_is_on_the_bolt_circle_with_the_right_screws(sat):
    ring = bs.TRANSCEIVER_RADIUS * bs.GIMBAL_RADIUS_FACTOR * (1 + bs.FLANGE_RADIUS_FACTOR) / 2
    for end, sign in (("front", 1), ("rear", -1)):
        for i in range(1, 9):
            b = box(sat, f"oisl_{end}_bolt_{i}")
            y, z = (b["bbox_min_mm"][1] + b["bbox_max_mm"][1]) / 2, (b["bbox_min_mm"][2] + b["bbox_max_mm"][2]) / 2
            assert math.hypot(y, z) == pytest.approx(ring, abs=0.1)                 # every screw sits on the bolt circle
            assert b["size_mm"][1] == pytest.approx(10, abs=0.1) or b["size_mm"][2] == pytest.approx(10, abs=0.1)
            length = bs.FLANGE_THICKNESS + bs.CHASSIS_WALL - bs.TAP_ENGAGE + 6        # shank + head height
            assert b["size_mm"][0] == pytest.approx(length, abs=0.1)
        s1 = box(sat, f"oisl_{end}_bolt_1")
        # head sits on the flange's outer face; the tip points into the chassis
        outer = HALF + bs.FLANGE_THICKNESS
        assert (abs(s1["bbox_max_mm"][0]) if sign > 0 else abs(s1["bbox_min_mm"][0])) == pytest.approx(outer + 6, abs=0.1)
    angles = sorted(round(math.degrees(math.atan2(*[(box(sat, f"oisl_front_bolt_{i}")["bbox_min_mm"][k] + box(sat, f"oisl_front_bolt_{i}")["bbox_max_mm"][k]) / 2 for k in (2, 1)])) % 360) for i in range(1, 9))
    assert angles == [0, 45, 90, 135, 180, 225, 270, 315]                          # evenly spaced


def test_blades_stand_on_the_floor_inside_the_chassis(sat):
    c = box(sat, "chassis")
    wall = bs.CHASSIS_WALL
    for i in range(1, bs.BLADE_COUNT + 1):
        b = box(sat, f"compute_blade_{i}")
        assert b["bbox_min_mm"][2] == pytest.approx(c["bbox_min_mm"][2] + wall)
        assert b["bbox_max_mm"][0] < c["bbox_max_mm"][0] - wall and b["bbox_min_mm"][0] > c["bbox_min_mm"][0] + wall


def test_chassis_wall_has_the_ports_and_the_tapped_holes():
    plain = Design()
    plain.add("chassis", "c", length=bs.CHASSIS_LENGTH, width=bs.CHASSIS_WIDTH, height=bs.CHASSIS_HEIGHT, wall=bs.CHASSIS_WALL,
              fillet=bs.CHASSIS_EDGE_RADIUS)
    d = bs.build()
    removed = (plain.describe("c")["volume_cm3"] - d.describe("chassis")["volume_cm3"]) * 1000
    port = PI * (bs.OISL_PORT_FACTOR * bs.TRANSCEIVER_RADIUS) ** 2 * bs.CHASSIS_WALL
    tapped = 16 * PI * 2.5 ** 2 * (bs.CHASSIS_WALL - bs.TAP_ENGAGE)                  # M6 tap drill 5.0, 12 mm deep
    assert removed == pytest.approx(2 * port + tapped, rel=0.01)


# ---- the model is built from named variables and formulas ------------------------------------------------------------------------
def test_every_dimension_is_a_named_variable(sat):
    assert sat.variables["radiator_wingspan"] == bs.RADIATOR_WINGSPAN and sat.variables["blade_count"] == bs.BLADE_COUNT
    assert sat.variables["oisl_bolt_count"] == 8 and sat.variables["radiator_deploy_angle_deg"] == 0
    assert sat.features["chassis"].raw["length"] == "=chassis_length"
    assert sat.features["radiator_left_panel"].raw["length"] == "=(radiator_wingspan - chassis.width) / 2"


def test_changing_variables_rebuilds_the_whole_satellite():
    d = bs.build()
    d.set_variable("radiator_wingspan", 30000)
    assert d.describe("radiator_left_panel")["bbox_max_mm"][1] == pytest.approx(15000)
    assert d.describe("radiator_right_panel")["bbox_min_mm"][1] == pytest.approx(-15000)
    d.set_variable("chassis_length", 8000)
    assert d.describe("oisl_front_telescope")["bbox_max_mm"][0] == pytest.approx(4000 + 20 + 300 + 800)
    assert d.describe("oisl_rear_telescope")["bbox_min_mm"][0] == pytest.approx(-(4000 + 20 + 300 + 800))
    assert d.describe("oisl_front_bolt_1")["bbox_max_mm"][0] == pytest.approx(4020 + 6, abs=0.1)     # the screws moved with the flange
    d.set_variable("blade_count", 6)
    assert len([n for n in d.parts if n.startswith("compute_blade_")]) == 6
    d.set_variable("oisl_bolt_count", 6)
    assert len([n for n in d.parts if n.startswith("oisl_front_bolt_")]) == 6
    assert d.check()["ok"]


def test_saved_recipe_stays_parametric_after_reloading(tmp_path):
    d = bs.build()
    path = d.save(tmp_path / "sat.json")
    e = Design()
    e.load(path)
    assert e.measure()["total_mass_kg"] == pytest.approx(d.measure()["total_mass_kg"])
    e.set_variable("transceiver_radius", 300)
    assert e.describe("oisl_front_telescope")["size_mm"][1] == pytest.approx(600)
    assert e.assembly_tree()["assemblies"][0]["name"] in {"compute", "thermal", "optical", "power"}


# ---- hinges: stow and deploy ----------------------------------------------------------------------------------------------------------
def test_hinges_exist_and_are_driven_by_variables(sat):
    joints = {j["assembly"]: j for j in sat.joints()}
    assert set(joints) == {"radiator_left", "radiator_right", "solar_top", "solar_bottom"}
    assert joints["radiator_left"]["variable"] == "radiator_deploy_angle_deg" and joints["radiator_left"]["parts"] == ["radiator_left_panel"]
    assert joints["radiator_left"]["pivot_mm"] == pytest.approx([0, bs.CHASSIS_WIDTH / 2, bs.RADIATOR_THICKNESS / 2]) and joints["radiator_left"]["axis"] == pytest.approx([1, 0, 0])
    assert (joints["radiator_left"]["angle_min"], joints["radiator_left"]["angle_max"]) == (0, 90)


def test_swinging_the_radiators_up_changes_inertia_not_mass():
    flat = bs.build()
    up = bs.build({"RADIATOR_DEPLOY_ANGLE_DEG": 90})
    panel_len = (bs.RADIATOR_WINGSPAN - bs.CHASSIS_WIDTH) / 2
    for name in ("radiator_left_panel", "radiator_right_panel"):
        assert flat.describe(name)["size_mm"][2] == pytest.approx(bs.RADIATOR_THICKNESS)
        assert up.describe(name)["bbox_max_mm"][2] == pytest.approx(panel_len + bs.RADIATOR_THICKNESS / 2, abs=1)     # standing up beside the chassis
    assert up.measure()["total_mass_kg"] == pytest.approx(flat.measure()["total_mass_kg"])
    assert up.measure()["inertia_kg_m2"][2][2] < 0.85 * flat.measure()["inertia_kg_m2"][2][2]    # panels no longer reach out sideways, so yaw inertia drops
    assert up.check()["ok"]


def test_solar_hinge_swings_the_wings_and_stays_collision_free():
    d = bs.build({"SOLAR_DEPLOY_ANGLE_DEG": 45})
    top, bottom = d.describe("solar_wing_top"), d.describe("solar_wing_bottom")
    assert top["bbox_max_mm"][2] < bs.CHASSIS_HEIGHT / 2 + bs.SOLAR_WING_LENGTH * math.cos(math.radians(45)) + 40
    assert top["bbox_max_mm"][1] > 1000 and bottom["bbox_max_mm"][1] > 1000            # both swing toward +y
    assert d.check()["ok"]


# ---- validation ------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("override, message", [
    ({"RADIATOR_WINGSPAN": 2000}, "tip-to-tip"),
    ({"BLADE_PITCH": 100}, "overlap"),
    ({"BLADE_COUNT": 100}, "interior"),
    ({"BLADE_HEIGHT": 3000}, "taller"),
    ({"TRANSCEIVER_RADIUS": 900}, "flange is wider"),
    ({"CHASSIS_LENGTH": -5}, "greater than 0"),
    ({"CHASSIS_WALL": 1300}, "too thick"),
    ({"GIMBAL_RADIUS_FACTOR": 0.5}, "at least as wide"),
    ({"RADIATOR_DEPLOY_ANGLE_DEG": 120}, "between 0"),
    ({"SOLAR_DEPLOY_ANGLE_DEG": -5}, "between 0"),
    ({"FLANGE_RADIUS_FACTOR": 1.02}, "too narrow"),
    ({"OISL_BOLT_COUNT": 2}, "whole number from 3"),
    ({"TRANSCEIVER_RADIUS": 100, "OISL_BOLT_COUNT": 64}, "apart"),
])
def test_bad_numbers_give_friendly_errors(override, message):
    with pytest.raises(DesignError, match=message):
        bs.build(override)


def test_no_bolts_is_a_valid_design():
    d = bs.build({"OISL_BOLT_COUNT": 0})
    assert not [n for n in d.parts if "bolt" in n] and d.check()["ok"] and len(d.parts) == 23


# ---- report, analysis -------------------------------------------------------------------------------------------------------------------
def test_report_numbers_match_geometry(sat):
    text, facts = bs.report(sat)
    assert facts["solar_area_m2"] == pytest.approx(2 * 14.0 * 12.0)
    assert facts["mass_kg"] == pytest.approx(sat.measure()["total_mass_kg"])
    assert 100 < facts["heat_rejection_kw"] < 170 and facts["heat_balance"] > 1
    assert "No overlapping parts" in text and "16 screws sit in tapped holes" in text and "Mass by subsystem" in text
    m = facts["principal_moments_kg_m2"]
    assert 0 < m[0] <= m[1] <= m[2] and m[2] <= m[0] + m[1]
    assert facts["inertia_kg_m2"][1][1] > facts["inertia_kg_m2"][0][0] > facts["inertia_kg_m2"][2][2]


def test_report_warns_when_radiators_cannot_reject_the_solar_power():
    d = bs.build({"RADIATOR_WINGSPAN": 6000})
    text, facts = bs.report(d, {"RADIATOR_WINGSPAN": 6000})
    assert facts["heat_balance"] < 1 and "WARNING" in text


def test_free_vibration_analysis_of_the_whole_satellite(sat):
    model, res = bs.analyse_modes(sat, 6)
    assert len(model.components()) == 1 and not [w for w in model.warnings if "separate pieces" in w]       # everything is connected
    assert res["rigid_body_modes_removed"] == 6 and all(f > 0 for f in res["frequencies_hz"])
    assert res["frequencies_hz"] == sorted(res["frequencies_hz"]) and res["frequencies_hz"][0] < 2.0       # big flexible wings: sub-hertz
    assert res["total_mass_tonne"] * 1000 == pytest.approx(sat.measure()["total_mass_kg"], rel=0.01)
    assert {"oisl_front_flange", "oisl_rear_flange"} <= {p.name for p in model.plates}


def test_stowing_the_radiators_raises_the_first_mode():
    deployed = bs.analyse_modes(bs.build(), 3)[1]["frequencies_hz"][0]
    stowed = bs.analyse_modes(bs.build({"RADIATOR_DEPLOY_ANGLE_DEG": 90, "SOLAR_DEPLOY_ANGLE_DEG": 90}), 3)[1]["frequencies_hz"][0]
    assert deployed > 0 and stowed > 0 and stowed != pytest.approx(deployed, rel=0.05)


def test_launch_configuration_static_load_is_solvable():
    d = bs.build({"RADIATOR_DEPLOY_ANGLE_DEG": 90})
    model = from_design(d, target_elements=2500, fixed={"part": "chassis", "side": "-z"}, constraint="face")
    r = model.static(accel_g=(0, 0, 6))
    assert r["reaction_force_n"][2] == pytest.approx(d.measure()["total_mass_kg"] * 6 * 9.80665, rel=0.02)
    assert np.isfinite(r["max_von_mises_mpa"]) and r["max_von_mises_mpa"] > 0


# ---- command line ------------------------------------------------------------------------------------------------------------------------
def test_command_line_writes_all_outputs(tmp_path):
    assert bs.main(["--out", str(tmp_path), "--name", "t", "--set", "BLADE_COUNT=6", "--set", "radiator_wingspan=26000.0"]) == 0
    folder = tmp_path / "t"
    for f in ("t.png", "t_core_detail.png", "t.step", "t.stl", "t.glb", "t.json", "t_report.txt", "t_bom.csv", "t_viewer.html",
              "t_modes.html"):
        assert (folder / f).stat().st_size > 0, f
    assert (folder / "t.png").read_bytes()[:4] == b"\x89PNG"
    assert "VIBRATION MODES" in (folder / "t_report.txt").read_text()
    csv = (folder / "t_bom.csv").read_text().splitlines()
    assert csv[0].startswith("part,assembly,type") and csv[-1].startswith("TOTAL")
    html = (folder / "t_viewer.html").read_text()
    assert "radiator_left_panel" in html and "three.min.js" in html and '"joints":[{' in html


def test_no_modes_flag_skips_the_analysis(tmp_path):
    assert bs.main(["--out", str(tmp_path), "--name", "n", "--no-modes", "--set", "OISL_BOLT_COUNT=0", "--set", "BLADE_COUNT=3"]) == 0
    assert not (tmp_path / "n" / "n_modes.html").exists() and "VIBRATION MODES" not in (tmp_path / "n" / "n_report.txt").read_text()


def test_command_line_rejects_unknown_and_invalid_settings(tmp_path, capsys):
    with pytest.raises(SystemExit):
        bs.main(["--out", str(tmp_path), "--set", "NOT_A_SETTING=1"])
    with pytest.raises(SystemExit):
        bs.main(["--out", str(tmp_path), "--set", "BLADE_COUNT=lots"])
    with pytest.raises(SystemExit):
        bs.main(["--out", str(tmp_path), "--set", "BLADE_COUNT=6.5"])
    with pytest.raises(SystemExit):
        bs.main(["--out", str(tmp_path), "--set", "OISL_BOLT_COUNT=7.5"])
    assert bs.main(["--out", str(tmp_path), "--set", "RADIATOR_WINGSPAN=100"]) == 1
    assert "Can't build this design" in capsys.readouterr().err


def test_script_runs_from_any_folder(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "build_satellite.py"), "--out", str(tmp_path), "--no-modes",
                             "--set", "OISL_BOLT_COUNT=0", "--set", "BLADE_COUNT=2"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    assert "DESIGN REPORT" in result.stdout and (tmp_path / "orbital_datacentre" / "orbital_datacentre.step").exists()


def test_folding_everything_at_once_is_caught_as_a_collision():
    # With both hinges folded 90 degrees the top solar wing lies across the standing left radiator: the checker must say so.
    result = bs.build({"RADIATOR_DEPLOY_ANGLE_DEG": 90, "SOLAR_DEPLOY_ANGLE_DEG": 90}).check()
    assert not result["ok"] and {"radiator_left_panel", "solar_wing_top"} <= {n for o in result["overlaps"] for n in (o["a"], o["b"])}


def test_folded_panels_lie_against_the_wall_not_through_it():
    d = bs.build({"RADIATOR_DEPLOY_ANGLE_DEG": 90})
    left = d.describe("radiator_left_panel")
    assert left["bbox_min_mm"][1] == pytest.approx(bs.CHASSIS_WIDTH / 2) and left["bbox_max_mm"][1] == pytest.approx(bs.CHASSIS_WIDTH / 2 + bs.RADIATOR_THICKNESS)


def test_supported_face_does_not_cut_the_connection_to_a_folded_wing():
    # The folded bottom wing rests on the fixed bottom face. If that face were forced to be the tied (slave) side the wing would
    # float free and the reaction would miss its mass.
    d = bs.build({"SOLAR_DEPLOY_ANGLE_DEG": 90})
    model = from_design(d, target_elements=2500, fixed={"part": "chassis", "side": "-z"}, constraint="face")
    r = model.static(accel_g=(0, 0, 6))
    assert r["reaction_force_n"][2] == pytest.approx(d.measure()["total_mass_kg"] * 6 * 9.80665, rel=0.01)
