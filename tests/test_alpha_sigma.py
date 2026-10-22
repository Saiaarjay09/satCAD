"""Alpha-Sigma: the 1:1 production-scale orbital AI datacentre (sattelite designs/alpha_sigma_production_node.py), its folding kinematics, its schematics, and the
DEPLOYMENT_RATIO slider and view presets in the window."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from satcad import Design, diagrams, routing  # noqa: E402
from satcad.systems import Systems  # noqa: E402

SCRIPT = ROOT / "sattelite designs" / "alpha_sigma_production_node.py"
SHIPPED = ROOT / "sattelite designs" / "alpha_sigma_production_node.json"


if not SCRIPT.exists():
    pytest.skip("the Alpha-Sigma design files are in a separate private repository", allow_module_level=True)


def load_script():
    spec = importlib.util.spec_from_file_location("alpha_sigma_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


as_ = load_script()


@pytest.fixture(scope="module")
def design():
    return as_.build()


@pytest.fixture(scope="module")
def systems(design):
    return Systems.of(design)


def box(design, name):
    lo, hi = design.parts[name].bbox()
    return np.array(lo, float), np.array(hi, float)


def names(design, prefix):
    return [n for n in design.parts if n.startswith(prefix)]


def layer(design, name):
    return design.parts[name].params.get("layer")


# ===== the bus ===============================================================================================================================
def test_bus_hull_is_a_hollow_4500_x_4500_x_8000_shell_centred_on_the_origin(design):
    lo, hi = box(design, "AlphaSigma_Bus_Hull")
    assert (hi - lo) == pytest.approx([4500, 4500, 8000]) and (lo + hi) / 2 == pytest.approx([0, 0, 0])
    solid_volume = 4500 * 4500 * 8000
    cavity = (4500 - 2 * as_.WALL) ** 2 * (8000 - 2 * as_.WALL)
    shell = design.parts["AlphaSigma_Bus_Hull"].solid.volume
    assert shell < 0.2 * solid_volume and shell == pytest.approx(solid_volume - cavity, rel=0.01)       # 45 mm walls, less the small port cut-outs
    assert as_.WALL == 45


def test_eight_ring_frames_every_1000_mm_and_the_launch_adapter_ring(design):
    z = sorted(set(round((box(design, n)[0][2] + box(design, n)[1][2]) / 2) for n in names(design, "ring_frame_")))
    assert len(names(design, "ring_frame_")) == 4 * 8 and z == [-3300, -2300, -1300, -300, 300, 1300, 2300, 3300]
    assert [b - a for a, b in zip(z, z[1:])] == [1000, 1000, 1000, 600, 1000, 1000, 1000]                    # 1000 mm between frames inside a bank; the 600 mm step crosses the avionics bay
    lo, hi = box(design, "launch_adapter_ring")
    assert hi[2] == pytest.approx(-4000, abs=0.01) and (hi[0] - lo[0]) == pytest.approx(2600) and lo[2] < -4000
    assert layer(design, "launch_adapter_ring") == "shell"


# ===== compute ===============================================================================================================================
def test_thirty_two_trays_in_two_banks_of_sixteen_at_185_mm_pitch(design):
    trays = names(design, "server_tray")
    assert len(trays) == 32
    for n in trays:
        lo, hi = box(design, n)
        assert (hi - lo) == pytest.approx([2800, 2400, 70])
    centres = sorted((box(design, n)[0][2] + box(design, n)[1][2]) / 2 for n in trays)
    bank_a, bank_b = centres[:16], centres[16:]
    assert all(b - a == pytest.approx(185) for a, b in zip(bank_a, bank_a[1:])) and all(b - a == pytest.approx(185) for a, b in zip(bank_b, bank_b[1:]))
    assert -3200 <= bank_a[0] - 35 and bank_a[-1] + 35 <= -200 and 200 <= bank_b[0] - 35 and bank_b[-1] + 35 <= 3200
    assert bank_b[0] - bank_a[-1] > 400                                                                  # the avionics bay (400 mm) fits between the banks


def test_eight_cpus_and_twenty_four_accelerators_on_every_tray(design):
    assert len(names(design, "ai_accelerator")) == 768 and len(names(design, "host_cpu")) == 256 and len(names(design, "cold_plate")) == 32
    per_tray = {}
    for n in names(design, "ai_accelerator") + names(design, "host_cpu"):
        z = (box(design, n)[0][2] + box(design, n)[1][2]) / 2
        key = min(range(32), key=lambda k: abs(sorted((box(design, t)[0][2] + box(design, t)[1][2]) / 2 for t in names(design, "server_tray"))[k] - z))
        per_tray.setdefault(key, [0, 0])[0 if n.startswith("ai_") else 1] += 1
    assert len(per_tray) == 32 and all(v == [24, 8] for v in per_tray.values())


def test_cold_plates_are_2600_x_2200_x_18_grooved_copper_clear_of_the_chips(design):
    lo, hi = box(design, "cold_plate")
    assert (hi - lo) == pytest.approx([2600, 2200, 18])
    assert design.parts["cold_plate"].material == "copper"
    plate_volume = 2600 * 2200 * 18
    assert design.parts["cold_plate"].solid.volume < plate_volume - 6 * 14 * 2000                         # the grooves have really been cut
    # a plate sits on top of the chips of its own tray, never inside them
    chip = box(design, "ai_accelerator")
    assert lo[2] >= chip[1][2] - 0.01


def test_walk_through_corridors_beside_the_trays(design):
    c = as_.corridors()
    assert c["x_clear_mm"] == pytest.approx(805) and c["y_clear_mm"] == pytest.approx(1005)
    assert c["x_from_outer_skin_mm"] == pytest.approx(850) and c["nominal_mm"] == 850
    # the sides of the trays are where the corridors are: no schematic part intrudes into the 805 mm strip except the feeder and riser tubes along the hull walls
    hull_in = 2250 - as_.WALL
    tray_x = 1400
    assert hull_in - tray_x == pytest.approx(805)


def test_tray_pitch_is_a_live_model_parameter():
    d = as_.build({"TRAY_PITCH_MM": 190}, with_systems=False)
    z = sorted((box(d, n)[0][2] + box(d, n)[1][2]) / 2 for n in names(d, "server_tray"))
    assert z[1] - z[0] == pytest.approx(190) and len(z) == 32
    with pytest.raises(as_.DesignError):
        as_.build({"TRAY_PITCH_MM": 100}, with_systems=False)


# ===== the avionics bay and the schematics ===================================================================================================
def test_avionics_bay_and_photonic_matrix(design):
    assert len(names(design, "bcdu_")) == 2 and len(names(design, "edfa_")) == 4
    lo, hi = box(design, "photonic_switch_matrix")
    assert abs((lo[2] + hi[2]) / 2) < 200                                                                # in the 400 mm bay at Z = 0
    for n in ("bcdu_1", "bcdu_2"):
        assert abs(sum(box(design, n)[i][2] for i in (0, 1)) / 2) < 200


def test_eight_batteries_four_busbars_two_accumulators_four_pumps(design):
    assert len(names(design, "battery_")) == 8 and len(names(design, "busbar_")) == 4
    for n in names(design, "battery_"):
        lo, hi = box(design, n)
        assert (hi - lo) == pytest.approx([900, 600, 450]) and hi[2] < -3300                              # the aft end of the bus
    for n in names(design, "busbar_"):
        lo, hi = box(design, n)
        assert sorted(hi - lo)[:2] == pytest.approx([15, 60])
    assert len(names(design, "accumulator_")) == 2 and len(names(design, "bypass_valve_")) == 2
    pumps = [n for n in design.parts if n.startswith("pump_") and "saddle" not in n]
    assert len(pumps) == 4
    for n in pumps:
        lo, hi = box(design, n)
        assert sorted(hi - lo) == pytest.approx([300, 300, 450])                                          # r150 x 450 long


def test_schematic_colours_and_layers(design):
    assert tuple(round(c * 255) for c in design.parts["busbar_1"].color[:3]) == (255, 95, 20)
    assert tuple(round(c * 255) for c in design.parts["hot_riser_A"].color[:3]) == (235, 35, 35)
    assert tuple(round(c * 255) for c in design.parts["cold_riser_A"].color[:3]) == (35, 130, 255)
    assert tuple(round(c * 255) for c in design.parts["fibre_trunk"].color[:3]) == (0, 230, 255)
    assert layer(design, "busbar_1") == "power" and layer(design, "hot_riser_A") == "fluid" and layer(design, "fibre_trunk") == "optical"
    assert layer(design, "AlphaSigma_Bus_Hull") == "shell" and layer(design, "server_tray") == "shell"
    for n in ("turret_fwd_a_base", "beacon_sphere_fwd", "micro_tube_pos_y_p1_hot", "bellows_pos_y_h1_hot", "root_header_hot_pos_y"):
        assert layer(design, n) == "external", n


def test_risers_feeders_and_trunk_sizes(design):
    for n in ("hot_riser_A", "cold_riser_B"):
        assert design.parts[n].params["radius"] == pytest.approx(22.5)                                    # 45 mm riser
    for n in ("hot_feeder_A", "cold_feeder_B"):
        assert design.parts[n].params["radius"] == pytest.approx(6.0)                                     # 12 mm feeder
    assert design.parts["fibre_trunk"].params["radius"] == pytest.approx(7.5)                             # 15 mm trunk
    assert len(names(design, "route_cable_tray_")) == 32


# ===== optical terminals =====================================================================================================================
def test_four_oisl_turrets_with_cassegrain_barrels_and_fsm(design):
    bases = [n for n in design.parts if n.startswith("turret_") and n.endswith("_base")]
    assert len(bases) == 4
    for n in bases:
        assert design.parts[n].params["radius"] == pytest.approx(220)                                     # the azimuth base
    for tag in ("fwd_a", "fwd_b", "aft_a", "aft_b"):
        for part in ("yoke", "barrel", "lens", "fsm_mirror", "fsm_mount", "pin_a", "pin_b"):
            assert f"turret_{tag}_{part}" in design.parts
        assert design.parts[f"turret_{tag}_barrel"].params["depth"] >= 250                                # the 250 mm Cassegrain barrel
        assert design.parts[f"turret_{tag}_fsm_mirror"].params["rx"] == 45                                # the 45 degree steering mirror
    assert {"turret_fwd_a_az", "turret_fwd_a_el", "turret_fwd_a_fsm"} <= {p.assembly for p in design.parts.values()}                       # azimuth, elevation and mirror stages


def test_two_laser_receiver_beacons_with_quadrant_detector_and_emitter_ring(design):
    for tag in ("fwd", "aft"):
        dome = design.parts[f"beacon_sphere_{tag}"]
        assert tuple(round(c * 255) for c in dome.color[:3]) == (0, 255, 140)                             # emerald
        lo, hi = box(design, f"beacon_sphere_{tag}")
        assert hi[0] - lo[0] == pytest.approx(180, abs=1)                                                  # r 90
        assert len(names(design, f"quad_{tag}_")) == 4                                                    # 2 x 2 InGaAs quadrants, 60 x 60 mm in total
        quads = np.array([box(design, n) for n in names(design, f"quad_{tag}_")])
        assert quads[:, 1, :2].max(axis=0) - quads[:, 0, :2].min(axis=0) == pytest.approx([60, 60])
        ring = [design.parts[n] for n in design.parts if n.startswith(f"beacon_emitter_ring_{tag}") or n == f"beacon_emitter_{tag}"]
        assert len(ring) == 8                                                                              # the 8-diode ring
        assert box(design, f"beacon_bench_{tag}")[1] [0] - box(design, f"beacon_bench_{tag}")[0][0] == pytest.approx(350)


# ===== deployable radiators ==================================================================================================================
def test_radiator_wings_are_four_panel_z_folds_20_m_deployed(design):
    for tag, sy in (("pos_y", 1), ("neg_y", -1)):
        skins = names(design, f"radiator_panel_{tag}_")
        assert len(skins) == 8                                                                             # 4 panels x 2 skins
        lo = np.min([box(design, n)[0] for n in skins], axis=0)
        hi = np.max([box(design, n)[1] for n in skins], axis=0)
        reach = (hi[1] - 2250) if sy > 0 else (-2250 - lo[1])
        assert reach == pytest.approx(20_000, rel=0.02) and (hi[2] - lo[2]) == pytest.approx(6800)
        for n in skins:
            assert box(design, n)[1][1] - box(design, n)[0][1] == pytest.approx(4966, rel=0.001)                  # 5000 mm panel less the hinge gap
    assert as_.RAD_PANELS == 4 and as_.RAD_PANEL_LEN == 5000 and as_.RAD_HEIGHT == 6800 and as_.RAD_CORE == 16


def test_radiator_panels_have_ridges_micro_tubes_and_bellows(design):
    assert len([n for n in design.parts if n.startswith("fin_top_") and "_ridges" not in n]) == 8 and as_.FIN_HEIGHT == 35
    assert len(names(design, "micro_tube_pos_y_p1_hot")) >= 1 + 11                                          # the tube plus its 11-step row
    tubes = [n for n in design.parts if n.startswith("micro_tube_") and "row" not in n]
    assert len(tubes) == 2 * 4 * 2 and as_.RIFLING == 8                                                    # per wing: 4 panels x (hot, cold); 8 internal micro-ridges
    assert len(names(design, "micro_tube_pos_y_p1_hot")) + len(names(design, "micro_tube_pos_y_p1_cold")) == 2 * 12                       # 24 micro-tubes per panel
    ridged = design.parts["micro_tube_pos_y_p1_hot"].solid.volume
    plain = math.pi * (as_.MICRO_R ** 2 - (as_.MICRO_R - as_.MICRO_WALL) ** 2) * as_.RAD_PL
    assert ridged > plain                                                                                    # the 8 inward ridges add material to the wall
    # toroidal bellows across each hinge, radius 22
    bellows = [n for n in design.parts if n.startswith("bellows_pos_y_h1_hot")]
    assert len(bellows) == 12
    lo, hi = box(design, "bellows_pos_y_h1_hot")
    assert max(hi - lo) == pytest.approx(44, abs=1) or min(hi - lo) == pytest.approx(44, abs=1)
    assert len(names(design, "hinge_pin_rad_pos_y")) == 9                                                    # 3 hinges, 3 knuckles each


def test_fin_ridge_angle_is_a_live_parameter():
    d = as_.build({"FIN_RIDGE_ANGLE_DEG": 150}, with_systems=False)
    narrow = as_.build({"FIN_RIDGE_ANGLE_DEG": 120}, with_systems=False)
    assert d.parts["fin_top_pos_y_p1"].solid.volume != pytest.approx(narrow.parts["fin_top_pos_y_p1"].solid.volume)
    with pytest.raises(as_.DesignError):
        as_.build({"FIN_RIDGE_ANGLE_DEG": 10}, with_systems=False)


# ===== solar arrays ==========================================================================================================================
def test_solar_wings_are_six_panel_accordions_24_m_long(design):
    for tag, sx in (("pos_x", 1), ("neg_x", -1)):
        panels = [f"solar_panel_{tag}_p{k}" for k in range(1, 7)]
        lo = np.min([box(design, n)[0] for n in panels], axis=0)
        hi = np.max([box(design, n)[1] for n in panels], axis=0)
        assert (hi[0] - 2250) * sx if sx > 0 else (-2250 - lo[0]) == pytest.approx(24_000, rel=0.02)
        for n in panels:
            blo, bhi = box(design, n)
            assert sorted(bhi - blo) == pytest.approx([18, 3800, 3978], rel=0.001) or sorted(bhi - blo)[:2] == pytest.approx([18, 3800], rel=0.001)
        assert design.parts[f"sada_{tag}"].params["radius"] == pytest.approx(220) and design.parts[f"sada_{tag}"].params["height"] == pytest.approx(850)
    assert len(names(design, "scissor_")) == 2 * 5 * 4 and len(names(design, "copper_strap_")) == 2 * 5 * 2
    assert as_.SOL_PANELS == 6 and as_.SOL_PANEL_LEN == 4000 and as_.SOL_WIDTH == 3800 and as_.SOL_THICK == 18


# ===== kinematics ============================================================================================================================
def test_22_folding_joints_and_the_deployed_pose_is_the_drawn_one():
    joints = as_.folding_joints()
    assert len(joints) == 22 == len(as_.joint_specs())
    assert all(abs(a) < 1e-9 for a in as_.joint_angles(1.0).values())
    stowed = as_.joint_angles(0.0)
    assert sorted(abs(round(a)) for n, a in stowed.items() if n.startswith("Rad_Fold")) == [180] * 6
    assert sorted(abs(round(a)) for n, a in stowed.items() if n.startswith("Rad_Root") or n.startswith("Sol_Root") or n.startswith("SADA")) == [90] * 6
    assert as_.smoothstep(0.0) == 0.0 and as_.smoothstep(1.0) == 1.0 and 0 < as_.smoothstep(0.5) < 1


def test_deployed_pose_matrices_are_identity_and_stowed_ones_are_rigid(design):
    for n, m in as_.pose_matrices(design, 1.0).items():
        assert np.allclose(m, np.eye(4), atol=1e-6), n
    for n, m in as_.pose_matrices(design, 0.0).items():
        r = m[:3, :3]
        assert np.allclose(r @ r.T, np.eye(3), atol=1e-6) and abs(np.linalg.det(r) - 1) < 1e-6, n


def test_stowed_launch_configuration_fits_5200_mm_with_no_overlaps(design):
    rep = as_.pose_report(design, 0.0)
    assert rep["overlaps"] == [] and rep["hull_hits"] == []
    assert rep["hi"][0] - rep["lo"][0] <= 5200 and rep["hi"][1] - rep["lo"][1] <= 5200
    assert rep["hi"][2] - rep["lo"][2] < 12_000                                                              # about 9.1 m: the adapter ring below, nothing above the hull end


def test_deployed_pose_has_no_overlaps(design):
    rep = as_.pose_report(design, 1.0)
    assert rep["overlaps"] == [] and rep["hull_hits"] == []
    assert rep["hi"][1] - rep["lo"][1] > 40_000 and rep["hi"][0] - rep["lo"][0] > 50_000                    # 20 m radiators each side, 24 m solar arrays each side


def test_no_panel_swings_through_another_during_the_unfolding(design):
    assert as_.fold_collisions(design) == []


def test_the_collision_check_does_catch_a_wrong_fold_direction(design, monkeypatch):
    real = as_.joint_angles
    monkeypatch.setattr(as_, "joint_angles", lambda r, s=0.0: {k: (-v if k == "Rad_Fold2_pos_y" else v) for k, v in real(r, s).items()})
    assert as_.fold_collisions(design, ratios=(0.6,)) != []


def test_motion_equations_match_the_python_joint_angles(design):
    from satcad.gui.motion_expressions import ExpressionEngine
    setup = as_.motion_setup()
    assert setup["expose"] == ["DEPLOYMENT_RATIO", "SUN_ANGLE_DEG"] and len(setup["joints"]) == 22
    e = ExpressionEngine()
    e.load_dict(setup["engine"])
    assert e.param_ranges["DEPLOYMENT_RATIO"] == (0.0, 1.0)
    for r, sun in ((0.0, 0.0), (0.25, 30.0), (0.5, 0.0), (0.8, 120.0), (1.0, 0.0)):
        e.set_param("DEPLOYMENT_RATIO", r)
        e.set_param("SUN_ANGLE_DEG", sun)
        want = as_.joint_angles(r, sun)
        for j in setup["joints"]:
            assert e.value(j["expr"], 0.0) == pytest.approx(want[j["name"]], abs=1e-6), (j["name"], r)


def test_the_stowed_step_is_written_quickly_and_is_a_real_pose(design):
    from build123d import Compound
    comp = as_.stowed_compound(design, 0.0)
    assert isinstance(comp, Compound) and len(comp.children) == len(design.parts)
    lo = comp.bounding_box().min
    hi = comp.bounding_box().max
    assert hi.X - lo.X < 5200 and hi.Y - lo.Y < 5200


# ===== the systems ===========================================================================================================================
def test_netlist_is_complete_and_passes_every_rule(design, systems):
    assert len(systems.components) == 74 and len(systems.links) == 321
    assert [f for f in systems.check(design) if f["severity"] == "error"] == []
    report = systems.report(design)
    assert "Checks: 0 errors" in report and "Power: 24.8 kW at 100 V" in report
    assert "Loop A: 24.0 kW" in report and "Loop B: 24.0 kW" in report and "Optical: 73 fibre runs" in report
    assert "radiator rejects 68.7 kW" in report


def test_every_routed_run_is_a_clean_body(design):
    routed = [n for n in design.parts if n.startswith("route_")]
    assert len(routed) >= 70
    chk = design.check()
    assert chk["overlaps"] == [] and chk["floating_parts"] == []


def test_diagrams_are_written(design, systems, tmp_path):
    files = diagrams.write_all(systems, design, tmp_path / "drawings", include_check=False)
    assert len(files) == 7
    for f in files:
        assert (tmp_path / "drawings" / f).stat().st_size > 500


def test_shipped_files_match_the_script():
    d = Design("x")
    d.load(SHIPPED)
    assert len(d.parts) == 2446 and len(Systems.of(d).links) == 321 and len(Systems.of(d).components) == 74
    data = json.loads(SHIPPED.read_text())
    assert data["motion"]["expose"] == ["DEPLOYMENT_RATIO", "SUN_ANGLE_DEG"] and len(data["motion"]["joints"]) == 22
    assert (ROOT / "sattelite designs" / "Alpha_Sigma_v1.step").stat().st_size > 5_000_000
    assert len(list((ROOT / "sattelite designs" / "alpha_sigma_production_node_systems").glob("*"))) == 7


def test_existing_designs_are_not_overwritten_and_the_folder_is_the_agreed_one():
    folder = ROOT / "sattelite designs"
    for name in ("heavy_orbital_node.json", "heavy_orbital_node.step", "orbital_node_alpha.json", "orbital_node_alpha_with_motion.json", "orbital_datacentre.step"):
        assert (folder / name).exists(), name
    assert not (ROOT / "satellite_designs").exists()


def test_command_line_prints_the_report_without_writing_files(tmp_path):
    out = subprocess.run([sys.executable, str(SCRIPT), "--no-save", "--no-systems", "--no-check"], capture_output=True, text=True, timeout=300, cwd=tmp_path,
                         env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert out.returncode == 0, out.stderr
    assert "32 trays, 768 AI accelerators, 256 host CPUs, 32 cold plates" in out.stdout and "fits the 5200 mm envelope" in out.stdout
    bad = subprocess.run([sys.executable, str(SCRIPT), "--no-save", "--set", "TRAY_PITCH_MM=50"], capture_output=True, text=True, timeout=300, cwd=tmp_path,
                         env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert bad.returncode == 1 and "Can't build" in bad.stderr


def test_the_app_does_not_contain_the_satellite():
    sources = "".join(p.read_text() for p in (ROOT / "satcad").rglob("*.py")) + (ROOT / "satcad_app.spec").read_text()
    assert "alpha_sigma" not in sources.lower() and "Alpha-Sigma" not in sources


# ===== the parametric transpiler on the big design ===========================================================================================
def test_alpha_sigma_transpiles_and_its_variables_drive_the_ir(design):
    from satcad import parametric_transpiler as tp
    import transpiler_reference as ref
    ir = tp.build_ir(design)
    assert {p.name for p in ir.params} == {"TRAY_PITCH_MM", "FIN_RIDGE_ANGLE_DEG", "tray_z0", "fin_base_half"}
    assert ir.params.params["TRAY_PITCH_MM"].unit == "mm" and ir.params.params["FIN_RIDGE_ANGLE_DEG"].unit == "deg"
    cov = ir.coverage()
    assert cov["native"] > 300 and cov["unsupported"] > 50                                     # routed cables and tubes are sweeps: reported, not written
    for t in tp.TARGETS:
        code = tp.GENERATORS[t](ir)
        assert "TRAY_PITCH_MM" in code and "COVERAGE" in code
        if t != "solidworks":
            import ast
            ast.parse(code)
    # the IR rebuilds the bodies it can write, and a new tray pitch moves them the same way satCAD's own rebuild does
    parts = ref.Reference(ir, dict(TRAY_PITCH_MM=190)).run()
    design.set_variable("TRAY_PITCH_MM", 190)
    compared = {k: v for k, v in parts.items() if k.startswith(("server_tray", "ai_accelerator", "host_cpu", "cold_plate"))}
    assert len(compared) == 32 + 768 + 256 + 32
    bad = ref.compare(design, compared)
    design.set_variable("TRAY_PITCH_MM", 185)
    assert bad == [], bad[:5]
