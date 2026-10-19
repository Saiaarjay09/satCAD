"""FE models built from SatCAD designs: plates, chassis walls, lumped equipment, ties and supports."""
import numpy as np
import pytest

from satcad import Design
from satcad.fem import G0, from_design


def bus(with_panel=True, **kw):
    d = Design("bus")
    d.add("chassis", "bus", length=2000, width=1200, height=1000, wall=10, internal_mass_kg=800, **kw)
    if with_panel:
        d.add("panel", "wing", attach_to="bus", side="+y", length=3000, width=1500, thickness=40, material="aluminum_honeycomb")
    return d


def test_fe_mass_equals_cad_mass():
    d = bus()
    d.add("box", "payload", length=400, width=400, height=300, z=-300, material="generic_equipment", role="payload")
    m = from_design(d, mesh_size=200)
    assert m.total_mass() * 1000 == pytest.approx(d.measure()["total_mass_kg"], rel=2e-3)


def test_chassis_becomes_six_walls_panels_become_plates_equipment_becomes_lumps():
    d = bus()
    d.add("box", "payload", length=400, width=400, height=300, z=-300, material="generic_equipment", role="payload")
    m = from_design(d, mesh_size=250)
    names = sorted(p.name for p in m.plates)
    assert names == ["bus+x", "bus+y", "bus+z", "bus-x", "bus-y", "bus-z", "wing"]
    assert sorted(l.name for l in m.lumps) == ["bus_equipment", "payload"]
    assert sum(1 for p in m.plates if p.part == "bus") == 6


def test_attached_panel_is_tied_to_the_chassis_wall_and_the_structure_is_one_piece():
    m = from_design(bus(), mesh_size=250)
    m.prepare()
    assert len(m.ties) > 0 and len(m.components()) == 1 and not [w for w in m.warnings if "separate pieces" in w]
    xyz = m.mesh.xyz
    # every tied node lies on the chassis +y wall plane (y = 600 - 5 offset to the midsurface) at the panel's inner edge
    assert all(abs(xyz[t[0]][1] - 605) < 1e-6 or abs(xyz[t[0]][1] - 600) < 1e-6 or abs(xyz[t[0]][1] - 595) < 10 for t in m.ties)


def test_fixed_base_modal_gives_positive_ordered_frequencies_and_modal_mass():
    m = from_design(bus(), mesh_size=250, fixed={"part": "bus", "side": "-z"})
    r = m.modal(8)
    f = r["frequencies_hz"]
    assert len(f) == 8 and all(x > 0 for x in f) and f == sorted(f)
    assert r["total_mass_tonne"] * 1000 == pytest.approx(bus().measure()["total_mass_kg"], rel=2e-3)
    assert all(0 <= v <= 1.0001 for k in "xyz" for v in r["effective_mass_fraction"][k])
    assert r["rigid_body_modes_removed"] == 0


def test_free_free_modal_removes_exactly_six_rigid_body_modes():
    m = from_design(bus(), mesh_size=300)
    r = m.modal(4)
    assert r["rigid_body_modes_removed"] == 6 and all(x > 0 for x in r["frequencies_hz"])


def test_stiffer_structure_has_higher_frequencies():
    soft = from_design(bus(), mesh_size=250, fixed={"part": "bus", "side": "-z"}).modal(1)["frequencies_hz"][0]
    d = bus()
    d.edit("wing", thickness=80)
    stiff = from_design(d, mesh_size=250, fixed={"part": "bus", "side": "-z"}).modal(1)["frequencies_hz"][0]
    assert stiff > soft * 1.05
    d2 = bus()
    d2.edit("bus", wall=20)
    assert from_design(d2, mesh_size=250, fixed={"part": "bus", "side": "-z"}).modal(1)["frequencies_hz"][0] != pytest.approx(soft, rel=0.01)


def test_static_g_load_reaction_equals_mass_times_acceleration():
    d = bus()
    m = from_design(d, mesh_size=250, fixed={"part": "bus", "side": "-z"}, constraint="face")
    r = m.static(accel_g=(0, 0, 8))
    mass_t = d.measure()["total_mass_kg"] / 1000
    assert r["reaction_force_n"][2] == pytest.approx(mass_t * 8 * G0, rel=2e-3)
    assert abs(r["reaction_force_n"][0]) < 1e-3 * r["reaction_force_n"][2]
    assert r["max_displacement_mm"] > 0 and r["max_von_mises_mpa"] > 0
    assert set(r["stress_by_plate"]) >= {"wing", "bus+z"}
    assert r["stress_by_plate"]["bus+z"]["margin_of_safety"] is not None


def test_lateral_load_bends_the_wing_more_than_axial_load():
    m = from_design(bus(), mesh_size=250, fixed={"part": "bus", "side": "-z"}, constraint="face")
    lateral = m.static(accel_g=(0, 0, 5))                    # along z: wing is flat, so this is out-of-plane bending
    axial = from_design(bus(), mesh_size=250, fixed={"part": "bus", "side": "-z"}, constraint="face").static(accel_g=(1, 0, 0))
    assert lateral["stress_by_plate"]["wing"]["max_von_mises_mpa"] > 5 * axial["stress_by_plate"]["wing"]["max_von_mises_mpa"]


def test_notes_explain_simplifications():
    d = bus(fillet=30)
    d.add("hole", "port", target="bus", side="+x", standard="M12", depth=10)
    m = from_design(d, mesh_size=300)
    text = " ".join(m.warnings)
    assert "rounded/bevelled edges are ignored" in text and "sandwich" in text.lower()


def test_cut_outs_in_a_panel_are_reported():
    d = bus()
    d.add("hole", "vent", target="wing", side="+z", standard="M12", hole_type="clearance", circle_count=6, circle_diameter=400)
    m = from_design(d, mesh_size=300)
    assert any("cut-outs / holes are ignored" in w and "wing" in w for w in m.warnings)


def test_errors_are_clear():
    with pytest.raises(ValueError, match="thin-walled plate or shell"):
        d = Design()
        d.add("sphere", "s", radius=100)
        from_design(d)
    d = bus()
    with pytest.raises(ValueError, match="has no plate facing"):
        from_design(d, fixed={"part": "wing", "side": "+x"})
    with pytest.raises(ValueError, match="not a plate"):
        from_design(d, fixed={"part": "nothing", "side": "-z"})


def test_hinged_array_changes_the_modes_when_deployed():
    def sat(angle):
        d = Design()
        d.set_variable("deploy", angle)
        d.add("chassis", "bus", length=2000, width=1200, height=1000, wall=10, internal_mass_kg=800)
        d.add("assembly", "wing_asm", joint_axis="x", angle_deg="=deploy", pivot_y=600)
        d.add("panel", "wing", attach_to="bus", side="+y", length=3000, width=1500, thickness=40,
              material="aluminum_honeycomb", assembly="wing_asm")
        return from_design(d, mesh_size=250, fixed={"part": "bus", "side": "-z"}).modal(1)["frequencies_hz"][0]
    flat, up = sat(0), sat(90)
    assert flat > 0 and up > 0 and flat != pytest.approx(up, rel=0.02)      # orientation of the wing matters to the first mode


def test_assemblies_and_mirrors_flow_into_the_fe_model():
    d = Design()
    d.add("chassis", "bus", length=2000, width=1200, height=1000, wall=10, internal_mass_kg=500)
    d.add("assembly", "thermal")
    d.add("panel", "rad_l", attach_to="bus", side="+y", length=2500, width=1200, thickness=30, material="aluminum_honeycomb", assembly="thermal")
    d.add("mirror", "rad_r", source="rad_l", plane="zx")
    m = from_design(d, mesh_size=250, fixed={"part": "bus", "side": "-z"})
    assert {"rad_l", "rad_r"} <= {p.name for p in m.plates}
    r = m.modal(3)
    assert r["total_mass_tonne"] * 1000 == pytest.approx(d.measure()["total_mass_kg"], rel=2e-3)
    # the wings bend vertically (z) in the first modes, so z carries far more of the mass than the lateral direction y
    assert r["cumulative_effective_mass"]["z"] > 5 * r["cumulative_effective_mass"]["y"]


def test_a_flange_bolted_face_to_face_to_the_wall_is_connected():
    d = bus(with_panel=False)
    d.add("extrude", "flange", profile="circle", radius=300, wall=150, depth=20, plane="yz", x=1000, material="aluminum_7075")
    m = from_design(d, mesh_size=200)
    m.prepare()
    assert len(m.components()) == 1 and len(m.ties) > 0
    flange = next(p for p in m.plates if p.name == "flange")
    assert flange.rho < 2810e-12 * 0.7                                      # a ring: density reduced to keep its mass
    assert any("rectangular plate with its density reduced" in w for w in m.warnings)
    assert m.total_mass() * 1000 == pytest.approx(d.measure()["total_mass_kg"], rel=2e-3)


def test_reaction_includes_loads_carried_by_tied_nodes():
    # a plate attached to a supported plate through ties: its weight must show up in the support reaction
    d = Design()
    d.add("chassis", "box", length=1000, width=1000, height=1000, wall=10)
    d.add("panel", "arm", attach_to="box", side="+y", length=2000, width=800, thickness=20, material="aluminum_6061")
    model = from_design(d, target_elements=800, fixed={"part": "box", "side": "-z"}, constraint="face")
    r = model.static(accel_g=(0, 0, 1))
    assert r["reaction_force_n"][2] == pytest.approx(d.measure()["total_mass_kg"] * 9.80665, rel=0.005)
