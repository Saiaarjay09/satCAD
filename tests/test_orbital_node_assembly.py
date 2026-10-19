"""Orbital-Node-Alpha assembly generator: structure, parameters, geometry, colours and STEP export."""
import math
import subprocess
import sys
from pathlib import Path

import pytest

import generate_orbital_node_assembly as g
from satcad import Design, DesignError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def alpha():
    return g.build()


def test_six_sub_assemblies_with_the_specified_children(alpha):
    names = {n for n, f in alpha.features.items() if f.kind == "assembly"}
    assert {"bus", "compute_rack", "mpfl", "radiators", "solar_arrays", "oisl_turrets"} <= names
    tree = alpha.assembly_tree()
    assert [a["name"] for a in tree["assemblies"]] == ["bus", "compute_rack", "mpfl", "radiators", "solar_arrays", "oisl_turrets"]
    assert tree["ungrouped"] == []                                                      # nothing left outside a sub-assembly
    top = {a["name"]: a for a in tree["assemblies"]}
    assert {c["name"] for c in top["radiators"]["children"]} == {"radiator_pos_y", "radiator_neg_y"}
    assert {c["name"] for c in top["solar_arrays"]["children"]} == {"solar_pos_x", "solar_neg_x"}
    assert {c["name"] for c in top["oisl_turrets"]["children"]} == {f"turret_{t}" for t in ("fwd_a", "fwd_b", "aft_a", "aft_b")}


def test_no_overlaps_nothing_floating_and_a_sane_mass(alpha):
    result = alpha.check()
    assert result["ok"] and result["overlaps"] == [] and result["floating_parts"] == []
    m = alpha.measure()
    assert 8000 < m["total_mass_kg"] < 11000 and m["parts"] > 350
    # axes: Y is the radiator axis, X the solar axis, Z the (short) velocity axis
    sx, sy, sz = m["overall_size_mm"]
    assert sx == pytest.approx(2 * (1900 + 15000)) and sy == pytest.approx(2 * (1860 + 12000), abs=1) and 3500 < sz < 4500


def test_primary_bus_is_a_hollow_hull_with_windows_beams_and_ring(alpha):
    hull = alpha.parts["bus_hull"]
    outer = 2000 * 2000 * 3500
    inner = 1950 * 1950 * 3450
    windows = 2 * 1400 * 2400 * 25                                                       # two inspection windows ...
    ports = 2 * math.pi * 40 ** 2 * 25                                                   # ... and two 80 mm pipe ports are cut through the wall
    assert hull.volume_mm3 == pytest.approx(outer - inner - windows - ports, rel=1e-4)
    assert {f"ibeam_{i}" for i in range(1, 5)} <= set(alpha.parts)
    for i in range(1, 5):
        b = alpha.describe(f"ibeam_{i}")["size_mm"]
        assert b == pytest.approx([100, 100, 3450])
    ring = alpha.describe("adapter_ring")
    assert ring["size_mm"][:2] == pytest.approx([1575, 1575]) and ring["size_mm"][2] == pytest.approx(150)
    assert ring["bbox_min_mm"][2] == pytest.approx(-1900) and ring["bbox_max_mm"][2] == pytest.approx(-1750)
    assert ring["volume_cm3"] < math.pi * (787.5 ** 2 - 725 ** 2) * 150 / 1000          # the bottom-lip chamfer removed material


def test_compute_rack_blades_packages_and_cold_plates(alpha):
    blades = [n for n in alpha.parts if n == "blade" or n.startswith("blade_stack_")]
    assert len(blades) == 16
    zs = sorted(alpha.describe(n)["centre_mm"][2] for n in blades)
    assert zs[0] == pytest.approx(-1050) and zs[-1] == pytest.approx(1050)
    assert all(b - a == pytest.approx(140) for a, b in zip(zs, zs[1:]))                  # 60 mm blade + 80 mm gap
    assert alpha.describe("blade")["size_mm"] == pytest.approx([1600, 1200, 60])
    packages = [n for n in alpha.parts if n.startswith("pkg_")]
    assert len(packages) == 8 * 16
    assert alpha.describe("pkg_1")["size_mm"] == pytest.approx([100, 100, 5])
    plates = [n for n in alpha.parts if n == "coldplate" or n.startswith("coldplate_stack_")]
    assert len(plates) == 16 and alpha.describe("coldplate")["size_mm"] == pytest.approx([1400, 1000, 15])
    assert alpha.parts["coldplate"].volume_mm3 < 1400 * 1000 * 15                        # micro-channel grooves are cut into it


def test_fluid_loop_pumps_manifolds_feeders_and_valve(alpha):
    for pump in ("pump_a", "pump_b"):
        size = alpha.describe(pump)["size_mm"]
        assert size == pytest.approx([240, 350, 240]) and alpha.describe(pump)["centre_mm"][2] == pytest.approx(-1500)
    assert alpha.describe("bypass_valve")["size_mm"] == pytest.approx([150, 150, 150])
    for tag, sign in (("hot", 1), ("cold", -1)):
        segs = [n for n in alpha.parts if n == f"{tag}_manifold" or n.startswith(f"{tag}_manifold_run_")]
        assert len(segs) == 24
        assert alpha.describe(f"{tag}_manifold")["centre_mm"][1] == pytest.approx(sign * 900)
        assert alpha.describe(f"{tag}_manifold")["size_mm"][0] == pytest.approx(60)           # 30 mm radius
        feeders = [n for n in alpha.parts if n == f"{tag}_feeder" or n.startswith(f"{tag}_feeder_run_")]
        assert len(feeders) == 16 and alpha.describe(f"{tag}_feeder")["size_mm"][0] == pytest.approx(20)   # 10 mm radius


def test_radiator_wings_boom_micro_tubes_and_root_manifold(alpha):
    for tag, sign in (("pos_y", 1), ("neg_y", -1)):
        panel = alpha.describe(f"radiator_panel_{tag}")
        assert panel["size_mm"] == pytest.approx([3000, 12000, 12])
        assert alpha.describe(f"boom_{tag}")["size_mm"][1] == pytest.approx(800)
        tubes = [n for n in alpha.parts if n.startswith(f"micro_tube_{tag}_")]
        assert len(tubes) == 2 * 24                                                       # 24 on each face
        t = alpha.describe(f"micro_tube_{tag}_top")
        assert t["size_mm"][1] == pytest.approx(11800) and t["size_mm"][0] == pytest.approx(20)
        assert t["centre_mm"][2] == pytest.approx(16) and alpha.describe(f"micro_tube_{tag}_bottom")["centre_mm"][2] == pytest.approx(-16)
        assert alpha.describe(f"root_manifold_{tag}")["size_mm"][0] == pytest.approx(3000)
        assert sign * panel["centre_mm"][1] > 0


def test_solar_wings_have_sada_yoke_and_three_segments(alpha):
    for tag, sign in (("pos_x", 1), ("neg_x", -1)):
        sada = alpha.describe(f"sada_{tag}")
        assert sada["size_mm"] == pytest.approx([600, 300, 300])
        frames = [n for n in alpha.parts if n.startswith(f"solar_frame_{tag}")]
        sheets = [n for n in alpha.parts if n.startswith(f"solar_sheet_{tag}")]
        assert len(frames) == len(sheets) == 3
        lo = min(alpha.describe(n)["bbox_min_mm"][0] for n in frames)
        hi = max(alpha.describe(n)["bbox_max_mm"][0] for n in frames)
        assert (hi - lo) == pytest.approx(15000, abs=1)                                   # total span, including two 20 mm hinge gaps
        xs = sorted(alpha.describe(n)["centre_mm"][0] for n in frames)
        seg = (15000 - 40) / 3
        assert xs[1] - xs[0] == pytest.approx(seg + 20, abs=0.01) and xs[2] - xs[1] == pytest.approx(seg + 20, abs=0.01)
        assert alpha.describe(frames[0])["size_mm"][1] == pytest.approx(15) and alpha.describe(frames[0])["size_mm"][2] == pytest.approx(2500)
        assert sign * (lo + hi) / 2 > 0


def test_four_turrets_with_base_yoke_barrel_fsm_and_lens(alpha):
    for tag, sign, y0 in g.TURRET_SITES:
        base = alpha.describe(f"turret_{tag}_base")
        assert base["size_mm"] == pytest.approx([360, 360, 120])
        assert base["bbox_min_mm"][2] * sign >= 1750 - 0.01 or base["bbox_max_mm"][2] * sign >= 1750                # sits on its face
        barrel = alpha.describe(f"turret_{tag}_barrel")
        assert barrel["size_mm"][0] == pytest.approx(220) and barrel["size_mm"][1] == pytest.approx(350)
        wall = (110 ** 2 - 95 ** 2) * math.pi * 350 / 1000
        assert alpha.describe(f"turret_{tag}_barrel")["volume_cm3"] == pytest.approx(wall, rel=1e-3)   # outer radius 110, inner 95, hollow
        yoke = alpha.describe(f"turret_{tag}_yoke")
        assert yoke["size_mm"][2] == pytest.approx(300)                                       # yoke height 300
        fsm = alpha.parts[f"turret_{tag}_fsm_mirror"]
        assert fsm.params["rx"] == 45 and fsm.params["radius"] == 40                           # 45-degree disc, radius 40
        assert alpha.describe(f"turret_{tag}_lens")["assembly"].endswith("_el")
    forward = [t for t in g.TURRET_SITES if t[1] > 0]
    assert len(forward) == 2 and len(g.TURRET_SITES) == 4


def test_colours_follow_the_specification(alpha):
    def colour(name):
        return tuple(round(c * 255) for c in alpha.parts[name].color)
    assert colour("adapter_ring") == (212, 175, 55)
    assert colour("blade") == (20, 60, 30) and colour("coldplate") == (184, 115, 51)
    assert colour("hot_manifold") == (192, 192, 200) and colour("radiator_panel_pos_y") == (245, 245, 250)
    assert colour("solar_sheet_pos_x") == (15, 35, 80) and colour("turret_fwd_a_base") == (35, 35, 40)
    assert colour("turret_fwd_a_lens") == (0, 200, 255)


def test_parameters_rebuild_the_whole_assembly():
    d = g.build({"NUM_SERVER_BLADES": 10, "RADIATOR_SPAN_MM": 8000, "SOLAR_SPAN_MM": 9000, "BUS_WALL_THICKNESS": 40})
    assert len([n for n in d.parts if n == "blade" or n.startswith("blade_stack_")]) == 10
    assert d.describe("radiator_panel_pos_y")["size_mm"][1] == 8000
    assert d.describe("micro_tube_pos_y_top")["size_mm"][1] == 7800
    frames = [n for n in d.parts if n.startswith("solar_frame_pos_x")]
    assert max(d.describe(n)["bbox_max_mm"][0] for n in frames) - min(d.describe(n)["bbox_min_mm"][0] for n in frames) == pytest.approx(9000, abs=1)
    assert d.check()["ok"]
    # changing a variable on a built design rebuilds it too
    d.set_variable("NUM_SERVER_BLADES", 6)
    assert len([n for n in d.parts if n == "blade" or n.startswith("blade_stack_")]) == 6 and d.check()["ok"]


@pytest.mark.parametrize("override, text", [({"NUM_SERVER_BLADES": 0}, "whole number from 1 to 18"), ({"NUM_SERVER_BLADES": 25}, "whole number from 1 to 18"),
                                            ({"NUM_SERVER_BLADES": 2.5}, "whole number"), ({"BUS_WALL_THICKNESS": 2}, "between 5 and 60"),
                                            ({"RADIATOR_SPAN_MM": 100}, "at least 1000"), ({"NOPE": 1}, "Unknown parameter")])
def test_bad_parameters_give_plain_messages(override, text):
    with pytest.raises(DesignError, match=text):
        g.build(override)


def test_step_export_and_command_line(tmp_path):
    out = tmp_path / "alpha.step"
    result = subprocess.run([sys.executable, str(ROOT / "generate_orbital_node_assembly.py"), "--set", "NUM_SERVER_BLADES=4", "--step", str(out),
                             "--json", str(tmp_path / "alpha.json")], capture_output=True, text=True, timeout=600, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "no overlapping bodies" in result.stdout and out.stat().st_size > 100_000
    head = out.read_bytes()[:20000].decode("latin-1")
    assert head.startswith("ISO-10303-21") and "AP214" in head.upper() or "AUTOMOTIVE" in head.upper() or "CONFIG_CONTROL" in head.upper()
    assert b"COLOUR_RGB" in out.read_bytes()                                             # colours are carried in the STEP file
    d = Design("x")
    d.load(tmp_path / "alpha.json")
    assert len([n for n in d.parts if n == "blade" or n.startswith("blade_stack_")]) == 4
    assert subprocess.run([sys.executable, str(ROOT / "generate_orbital_node_assembly.py"), "--set", "NUM_SERVER_BLADES=99"],
                          capture_output=True, text=True, cwd=tmp_path).returncode == 1
