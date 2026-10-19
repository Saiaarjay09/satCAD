"""Assemblies (subsystems): grouping, nesting, placement, roll-ups and hinge joints."""
import math

import numpy as np
import pytest

from satcad import Design, DesignError

RHO = 2700.0


def thermal_design():
    d = Design("sat")
    d.add("chassis", "bus", length=2000, width=1000, height=800, wall=10)
    d.add("assembly", "thermal")
    d.add("assembly", "radiators", parent="thermal")
    d.add("panel", "rad_l", attach_to="bus", side="+y", length=3000, width=1000, thickness=20, assembly="radiators")
    d.add("mirror", "rad_r", source="rad_l", plane="zx")
    d.add("box", "pump", length=200, width=200, height=200, x=0, y=0, z=-100, assembly="thermal", material="stainless_316")
    return d


def test_membership_paths_and_inheritance():
    d = thermal_design()
    assert d.describe("rad_l")["assembly"] == "thermal/radiators"
    assert d.describe("rad_r")["assembly"] == "thermal/radiators"                  # a mirror joins its source's assembly
    assert d.describe("pump")["assembly"] == "thermal" and d.describe("bus")["assembly"] is None


def test_roll_up_mass_includes_nested_assemblies_and_matches_parts():
    d = thermal_design()
    tree = d.assembly_tree()
    top = {a["name"]: a for a in tree["assemblies"]}["thermal"]
    rad_mass = d.describe("rad_l")["mass_kg"] + d.describe("rad_r")["mass_kg"]
    assert top["children"][0]["name"] == "radiators" and top["children"][0]["mass_kg"] == pytest.approx(rad_mass, abs=0.002)
    assert top["mass_kg"] == pytest.approx(rad_mass + d.describe("pump")["mass_kg"], abs=0.002)
    assert top["part_count"] == 3 and tree["ungrouped"] == ["bus"]
    all_mass = sum(d.describe(n)["mass_kg"] for n in d.parts)
    assert d.measure()["total_mass_kg"] == pytest.approx(all_mass, abs=0.01)
    assert {a["path"] for a in d.measure()["assemblies"]} == {"thermal", "thermal/radiators"}


def test_assembly_inertia_matches_a_design_of_only_those_parts():
    d = thermal_design()
    sub = d.mass_properties(d._members("thermal"))
    ref = Design()
    ref.add("chassis", "bus", length=2000, width=1000, height=800, wall=10)
    ref.add("panel", "rad_l", attach_to="bus", side="+y", length=3000, width=1000, thickness=20)
    ref.add("mirror", "rad_r", source="rad_l", plane="zx")
    ref.add("box", "pump", length=200, width=200, height=200, z=-100, material="stainless_316")
    parts = [ref.parts[n] for n in ("rad_l", "rad_r", "pump")]
    expect = ref.mass_properties(parts)
    assert sub["total_mass_kg"] == pytest.approx(expect["total_mass_kg"], rel=1e-9)
    assert np.allclose(sub["inertia_kg_m2"], expect["inertia_kg_m2"], rtol=1e-6)


def test_assembly_placement_moves_member_poses():
    d = Design()
    d.add("assembly", "pod", x=1000, y=500, z=-200)
    d.add("box", "b", length=100, width=100, height=100, x=10, assembly="pod")
    assert d.describe("b")["centre_mm"] == pytest.approx([1010, 500, -200])
    d.edit("pod", x=3000)                                                           # move the whole subsystem
    assert d.describe("b")["centre_mm"] == pytest.approx([3010, 500, -200])


def test_nested_assemblies_compose_and_rotate():
    d = Design()
    d.add("assembly", "outer", x=1000)
    d.add("assembly", "inner", parent="outer", y=2000, rz=90)
    d.add("box", "b", length=100, width=100, height=100, x=300, assembly="inner")      # 300 mm along the inner frame's x
    c = d.describe("b")["centre_mm"]
    assert c == pytest.approx([1000, 2300, 0], abs=1e-3)                            # rotated 90 deg about z: x becomes y


def test_pattern_of_member_stays_in_the_assembly_and_attachments_are_not_double_moved():
    d = Design()
    d.add("assembly", "pod", x=500)
    d.add("box", "base", length=100, width=100, height=100, assembly="pod")
    d.add("panel", "fin", attach_to="base", side="+x", length=200, width=50, thickness=5, assembly="pod")
    assert d.describe("fin")["bbox_min_mm"][0] == pytest.approx(500 + 50)           # sits on the base's moved face, not moved twice
    d.add("pattern", "copies", source="base", mode="linear", count=3, dy=300)
    assert d.describe("copies_3")["assembly"] == "pod" and d.describe("copies_3")["centre_mm"][1] == pytest.approx(600)


def test_hinge_joint_swings_the_assembly_about_its_pivot():
    d = Design()
    d.add("assembly", "wing", joint_axis="y", angle_deg=90, pivot_x=0, pivot_y=0, pivot_z=0)
    d.add("box", "panel", length=1000, width=100, height=10, x=500, assembly="wing")   # stowed along +x
    c = d.describe("panel")["centre_mm"]
    assert abs(c[0]) < 1e-6 and abs(abs(c[2]) - 500) < 1e-6                            # rotated 90 deg about y: now along z
    d.edit("wing", angle_deg=0)
    assert d.describe("panel")["centre_mm"] == pytest.approx([500, 0, 0], abs=1e-6)
    d.set_variable("deploy", 45)
    d.edit("wing", angle_deg="=deploy")
    c = d.describe("panel")["centre_mm"]
    assert math.hypot(c[0], c[2]) == pytest.approx(500, abs=0.01) and c[0] == pytest.approx(500 * math.cos(math.radians(45)), abs=0.01)
    assert d.assembly_tree()["assemblies"][0]["joint"]["angle_deg"] == 45


def test_joint_pivot_not_at_origin():
    d = Design()
    d.add("assembly", "arm", joint_axis="z", angle_deg=180, pivot_x=1000)
    d.add("box", "tip", length=10, width=10, height=10, x=1500, assembly="arm")
    assert d.describe("tip")["centre_mm"] == pytest.approx([500, 0, 0], abs=1e-6)       # mirrored through the pivot at x=1000


def test_errors():
    d = Design()
    with pytest.raises(DesignError, match="isn't an assembly defined before"):
        d.add("box", "b", length=1, width=1, height=1, assembly="ghost")
    with pytest.raises(DesignError, match="isn't an assembly defined before"):
        d.add("assembly", "a", parent="ghost")
    with pytest.raises(DesignError, match="joint_axis"):
        d.add("assembly", "a", joint_axis="q")
    d.add("assembly", "ok")
    d.add("box", "b", length=1, width=1, height=1, assembly="ok")
    with pytest.raises(DesignError, match="attached to it or use it"):
        d.remove("ok")
    d.remove("b")
    d.remove("ok")
    assert "ok" not in d.features


def test_save_load_roundtrip_keeps_assemblies(tmp_path):
    d = thermal_design()
    d.add("assembly", "wing", joint_axis="y", angle_deg=30)
    path = d.save(tmp_path / "a.json")
    e = Design()
    e.load(path)
    assert e.describe("rad_l")["assembly"] == "thermal/radiators"
    assert e.assembly_tree()["assemblies"][1]["joint"]["angle_deg"] == 30
    assert e.measure()["total_mass_kg"] == pytest.approx(d.measure()["total_mass_kg"])


def test_bom_has_the_assembly_column(tmp_path):
    d = thermal_design()
    rows = {r["part"]: r for r in d.bom()}
    assert rows["rad_l"]["assembly"] == "thermal/radiators" and rows["bus"]["assembly"] == ""


# ---- joints() : world pivot and axis for the viewer ----------------------------------------------------------------------
def test_joint_world_pivot_and_axis_with_nested_placement():
    d = Design()
    d.add("assembly", "arm", x=1000, y=200, rz=90)                              # placed and turned 90 deg about z
    d.add("assembly", "wrist", parent="arm", x=300, joint_axis="y", pivot_x=50, angle_deg=20, angle_min=-10, angle_max=95)
    d.add("box", "hand", length=10, width=10, height=10, assembly="wrist")
    (j,) = d.joints()
    # local pivot (50,0,0) inside wrist at x=300 inside arm: arm frame (350,0,0) -> world: rotate 90 about z -> (0,350) + (1000,200)
    assert j["pivot_mm"] == pytest.approx([1000, 550, 0], abs=1e-3)
    assert j["axis"] == pytest.approx([-1, 0, 0], abs=1e-6)                      # local y becomes world -x after the 90 degree turn
    assert (j["angle_deg"], j["angle_min"], j["angle_max"], j["parts"]) == (20, -10, 95, ["hand"])


def test_joint_lists_nested_parts_ancestors_and_variable_names():
    d = Design()
    d.set_variable("deploy", 30)
    d.add("assembly", "wing", joint_axis="y", angle_deg="=deploy")
    d.add("assembly", "flap", parent="wing", joint_axis="x", angle_deg=5)
    d.add("box", "skin", length=100, width=100, height=10, assembly="wing")
    d.add("box", "tab", length=10, width=10, height=10, x=200, assembly="flap")
    by = {j["assembly"]: j for j in d.joints()}
    assert by["wing"]["variable"] == "deploy" and sorted(by["wing"]["parts"]) == ["skin", "tab"]     # wing moves its flap's parts too
    assert by["flap"]["parts"] == ["tab"] and by["flap"]["inside_joints"] == ["wing"] and by["flap"]["variable"] is None


def test_assemblies_without_a_joint_are_not_joints_and_range_is_validated():
    d = Design()
    d.add("assembly", "fixed")
    assert d.joints() == []
    with pytest.raises(DesignError, match="angle_min"):
        d.add("assembly", "bad", joint_axis="x", angle_min=50, angle_max=10)


# ---- attached panels swing with their assembly's hinge --------------------------------------------------------------------
def deployable():
    d = Design()
    d.set_variable("deploy", 0)
    d.add("chassis", "bus", length=2000, width=1000, height=800, wall=10)
    d.add("assembly", "wing", joint_axis="x", angle_deg="=deploy", pivot_y=500, angle_min=0, angle_max=90)
    d.add("panel", "array", attach_to="bus", side="+y", length=2000, width=1500, thickness=20, assembly="wing")
    return d


def test_attached_panel_stows_deploys_and_follows_the_variable():
    d = deployable()
    flat = d.describe("array")
    assert flat["bbox_max_mm"][1] == pytest.approx(2500) and flat["size_mm"][2] == pytest.approx(20)       # deployed flat, +y
    d.set_variable("deploy", 90)                                                                           # swing up 90 degrees
    up = d.describe("array")
    assert up["bbox_max_mm"][2] == pytest.approx(2000, abs=1) and up["size_mm"][1] == pytest.approx(20, abs=1)
    assert up["bbox_min_mm"][1] == pytest.approx(490, abs=1)                                               # hinge stays on the chassis face
    d.set_variable("deploy", 0)
    assert d.describe("array")["bbox_max_mm"][1] == pytest.approx(2500)
    assert d.mass_properties()["total_mass_kg"] == pytest.approx(deployable().mass_properties()["total_mass_kg"])   # same mass, new inertia


def test_hinging_changes_inertia_but_not_mass():
    d = deployable()
    flat = d.measure()["inertia_kg_m2"]
    d.set_variable("deploy", 90)
    up = d.measure()["inertia_kg_m2"]
    assert d.measure()["total_mass_kg"] == pytest.approx(deployable().measure()["total_mass_kg"])
    assert up[2][2] < flat[2][2] and up[1][1] > flat[1][1]                    # panel stands up: smaller Izz, larger Iyy


def test_placement_still_does_not_move_attached_panels_when_there_is_no_joint():
    d = Design()
    d.add("box", "base", length=100, width=100, height=100)
    d.add("assembly", "pod", x=999)
    d.add("panel", "fin", attach_to="base", side="+x", length=200, width=50, thickness=5, assembly="pod")
    assert d.describe("fin")["bbox_min_mm"][0] == pytest.approx(50)
