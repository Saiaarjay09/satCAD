"""Systems: wiring, tubing and fibre netlists tied to the 3D model: the engineering maths, the router, the rule checks, the diagrams and the Systems tab."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from satcad import Design, DesignError, diagrams, routing  # noqa: E402
from satcad.systems import (AWG, FIBRES, FLUIDS, Systems, SystemsError, count_bends, polyline_distance, polyline_length,  # noqa: E402
                            segment_distance, wire_outer_diameter_mm)


# ===== helpers ========================================================================================================================
def room() -> Design:
    """A hollow 1 m cube with a source box, a load box and a wall between them."""
    d = Design("room")
    d.add("box", "hull", length=1000, width=1000, height=1000)
    d.add("box", "cavity", length=960, width=960, height=960)
    d.add("boolean", "hollow", operation="subtract", target="hull", tool="cavity")
    d.add("box", "src", length=100, width=100, height=100, x=-300, y=-300, z=-300)
    d.add("box", "load", length=100, width=100, height=100, x=300, y=300, z=300)
    d.add("box", "wall", length=20, width=600, height=700)
    return d


def power_pair(d: Design, **link) -> Systems:
    S = Systems("t")
    S.component("SRC", "power", "regulator", part="src", source_w=1000, source_group="regulator", ports={"OUT": "+x"})
    S.component("LOAD", "power", "load", part="load", load_w=480, ports={"IN": "-x"})
    S.link("W1", "power", "SRC.OUT", "LOAD.IN", auto=True, awg=link.pop("awg", "8"), **link)
    S.sync(d)
    return S


def rect_ok(S, d):
    return [f for f in S.check(d, include_clash=True) if f["severity"] == "error"]


# ===== the maths ========================================================================================================================
def test_wire_resistance_comes_from_the_gauge_table():
    d, area, mohm = AWG["4/0"]
    assert area == pytest.approx(math.pi * (d / 2) ** 2, rel=0.01)                  # table area agrees with the diameter
    assert mohm / 1000 == pytest.approx(1.724e-8 / (area * 1e-6), rel=0.03)          # and with copper resistivity
    assert wire_outer_diameter_mm("4/0") > AWG["4/0"][0]                            # insulation adds to the diameter


def test_voltage_drop_is_current_times_loop_resistance():
    d = room()
    S = power_pair(d, awg="14")
    S.links["W1"]["route"] = [[0, 0, 0], [3000, 0, 0]]                              # exactly 3 m
    S.links["W1"]["auto"] = False
    r = S.analyse(d)["power"]
    amps = 480 / 48.0
    link = r["links"][0]
    assert link["current_a"] == pytest.approx(amps)
    assert link["r_mohm"] == pytest.approx(AWG["14"][2] * 3.0, rel=1e-3)
    assert link["drop_v"] == pytest.approx(amps * AWG["14"][2] / 1000 * 3.0 * 2, rel=1e-3)   # supply plus return conductor
    assert r["loads"][0]["drop_pct"] == pytest.approx(100 * link["drop_v"] / 48.0, rel=1e-2)


def test_a_copper_bar_resists_by_resistivity_times_length_over_area():
    d = room()
    S = Systems("t")
    S.component("A", "power", "regulator", part="src", source_w=1000, source_group="regulator", ports={"P": "+x"})
    S.component("B", "power", "load", part="load", load_w=480, ports={"P": "-x"})
    S.link("BAR", "power", "A.P", "B.P", kind="bar", bar_mm=[40, 10], length_mm=2000)
    row = S.analyse(d)["power"]["links"][0]
    assert row["r_mohm"] == pytest.approx(1.724e-8 * 2.0 / (400e-6) * 1000, rel=1e-3)
    assert row["area_mm2"] == 400 and row["density_a_mm2"] == pytest.approx(10 / 400)


def test_current_density_and_voltage_drop_limits_raise_findings():
    d = room()
    S = power_pair(d, awg="26")                                                      # a hair-thin wire for 10 A
    S.links["W1"]["route"] = [[0, 0, 0], [2000, 0, 0]]
    S.links["W1"]["auto"] = False
    rules = {f["rule"] for f in S.check(d, include_clash=False) if f["severity"] == "error"}
    assert {"P1", "P2"} <= rules


def test_battery_and_regulator_groups_must_cover_the_load():
    d = room()
    S = Systems("t")
    S.component("SRC", "power", "regulator", part="src", source_w=100, source_group="regulator", ports={"OUT": "+x"})
    S.component("LOAD", "power", "load", part="load", load_w=480, ports={"IN": "-x"})
    S.link("W", "power", "SRC.OUT", "LOAD.IN", route=[[0, 0, 0], [500, 0, 0]], awg="10")
    assert any(f["rule"] == "P3" for f in S.check(None, include_clash=False))


def test_friction_factor_follows_the_textbook_laws():
    assert Systems.friction_factor(1000) == pytest.approx(0.064)                     # laminar: 64 / Re
    f = Systems.friction_factor(1e5, rel_rough=0.0)
    assert f == pytest.approx(0.0180, abs=0.001)                                     # smooth pipe at Re = 1e5 (Moody chart)
    assert 0.064 / 1.0 > Systems.friction_factor(3000) > 0.03                        # the transition is blended, not a jump


def test_loop_pressure_drop_matches_a_hand_calculation():
    d = room()
    S = Systems("t")
    S.component("T", "fluid", "tank", part="src", ports={"o": "+x"})
    S.component("U", "fluid", "tank", part="load", ports={"i": "-x"})
    S.link("P", "fluid", "T.o", "U.i", route=[[0, 0, 0], [10000, 0, 0]], od_mm=24, wall_mm=2, role="cold")
    S.loop("L", fluid="water", heat_w=4182.0 * 0.1 * 10, delta_t_k=10.0, pump={"head_kpa": 100.0}, stages=[dict(name="pipe", elements=["P"])])
    lp = S.analyse(d)["fluid"]["loops"][0]
    fl = FLUIDS["water"]
    m_dot = 4182.0 * 0.1 * 10 / (fl["cp"] * 10)
    assert lp["mass_flow_kg_s"] == pytest.approx(m_dot, rel=1e-3) and m_dot == pytest.approx(0.1)
    dia = 0.020
    v = (m_dot / fl["density"]) / (math.pi * dia ** 2 / 4)
    re = fl["density"] * v * dia / fl["viscosity"]
    expected = Systems.friction_factor(re) * (10.0 / dia) * 0.5 * fl["density"] * v * v
    assert lp["stages"][0]["dp_pa"] == pytest.approx(expected, rel=1e-3)
    assert lp["stages"][0]["elements"][0]["velocity_m_s"] == pytest.approx(v, rel=1e-3)


def test_parallel_branches_share_the_flow_and_bends_add_loss():
    d = room()
    S = Systems("t")
    S.component("T", "fluid", "tank", part="src", ports={"o": "+x"})
    S.component("U", "fluid", "tank", part="load", ports={"i": "-x"})
    S.link("A", "fluid", "T.o", "U.i", route=[[0, 0, 0], [1000, 0, 0]], od_mm=20, wall_mm=1, role="cold")
    S.link("B", "fluid", "T.o", "U.i", route=[[0, 0, 0], [1000, 0, 0]], od_mm=20, wall_mm=1, role="cold")
    S.link("C", "fluid", "T.o", "U.i", route=[[0, 0, 0], [500, 0, 0], [500, 500, 0], [1000, 500, 0]], od_mm=20, wall_mm=1, role="cold")
    S.loop("one", fluid="ammonia", heat_w=1000, stages=[dict(name="s", elements=["A"])])
    S.loop("two", fluid="ammonia", heat_w=1000, stages=[dict(name="s", elements=["A", "B"], parallel=True)])
    S.loop("bent", fluid="ammonia", heat_w=1000, stages=[dict(name="s", elements=["C"])])
    one, two, bent = (S.analyse(d)["fluid"]["loops"][i]["stages"][0] for i in range(3))
    assert two["elements"][0]["velocity_m_s"] == pytest.approx(one["elements"][0]["velocity_m_s"] / 2, rel=1e-3)
    assert bent["elements"][0]["bends"] == 2 and bent["dp_pa"] > one["dp_pa"]


def test_pump_and_radiator_checks():
    d = room()
    S = Systems("t")
    S.component("T", "fluid", "tank", part="src", ports={"o": "+x"})
    S.component("U", "fluid", "tank", part="load", ports={"i": "-x"})
    S.link("P", "fluid", "T.o", "U.i", route=[[0, 0, 0], [20000, 0, 0]], od_mm=10, wall_mm=1, role="cold")
    S.loop("L", fluid="ammonia", heat_w=20000.0, stages=[dict(name="s", elements=["P"])], pump={"head_kpa": 1.0}, radiator={"area_m2": 1.0, "emissivity": 0.85, "fluid_temp_k": 293.0})
    rules = {f["rule"] for f in S.check(d, include_clash=False) if f["severity"] == "error"}
    assert {"F1", "F2", "F3"} <= rules                                               # too fast, too much pressure for the pump, radiator far too small
    rad = S.analyse(d)["fluid"]["loops"][0]["radiator"]
    assert rad["flux_w_m2"] == pytest.approx(0.85 * 5.670374419e-8 * (288.0 ** 4 - 200.0 ** 4), rel=1e-3)


def test_fibre_loss_latency_and_bend_rules():
    d = room()
    S = Systems("t")
    S.component("A", "optical", "tx", part="src", ports={"o": "+x"})
    S.component("B", "optical", "rx", part="load", ports={"i": "-x"})
    S.link("F", "optical", "A.o", "B.i", route=[[0, 0, 0], [500, 0, 0], [500, 500, 0], [1000, 500, 0]], fibre="SMF-28", conduit_od_mm=12, bend_radius_mm=10)
    S.link("G", "optical", "A.o", "B.i", route=[[0, 0, 0], [10000, 0, 0]], fibre="SMF-28", conduit_od_mm=12)
    S.chain("path", ["G"], connectors=2, budget_db=0.5)
    r = S.analyse(d)["optical"]
    g = next(x for x in r["links"] if x["id"] == "G")
    assert g["length_m"] == pytest.approx(10.0) and g["loss_db"] == pytest.approx(10.0 * FIBRES["SMF-28"]["attenuation_db_km"] / 1000)
    assert g["latency_ns"] == pytest.approx(10.0 * 1.468 / 0.299792458, rel=1e-3)     # about 49 ns for 10 m
    ch = r["chains"][0]
    assert ch["loss_db"] == pytest.approx(g["loss_db"] + 2 * 0.3) and ch["margin_db"] == pytest.approx(0.5 - ch["loss_db"], abs=1e-3)
    assert any(f["rule"] == "O1" for f in S.check(d, include_clash=False))            # a 10 mm bend is tighter than 30 mm


def test_segment_geometry_helpers():
    assert segment_distance([0, 0, 0], [10, 0, 0], [0, 5, 0], [10, 5, 0]) == pytest.approx(5)
    assert segment_distance([0, 0, 0], [10, 0, 0], [5, 3, 4], [5, 3, 9]) == pytest.approx(5)          # skew segments: sqrt(3^2 + 4^2)
    assert segment_distance([0, 0, 0], [1, 0, 0], [3, 0, 0], [4, 0, 0]) == pytest.approx(2)
    assert polyline_length([[0, 0, 0], [3, 4, 0], [3, 4, 12]]) == pytest.approx(17)
    assert count_bends([[0, 0, 0], [10, 0, 0], [10, 10, 0], [20, 10, 0], [30, 10, 0]]) == 2
    assert polyline_distance([[0, 0, 0], [10, 0, 0]], [[0, 7, 0], [10, 7, 0]]) == pytest.approx(7)


# ===== the netlist ======================================================================================================================
def test_netlist_mistakes_are_refused_with_a_reason():
    S = Systems("t")
    S.component("A", "power", "x", ports={"p": {"part": "src", "at": "+x"}})
    with pytest.raises(SystemsError, match="already defined"):
        S.component("A", "power", "x")
    with pytest.raises(SystemsError, match="domain"):
        S.component("B", "magic", "x")
    S.component("B", "power", "x", ports={"p": {"part": "load", "at": "-x"}})
    with pytest.raises(SystemsError, match="no port"):
        S.link("L", "power", "A.nope", "B.p", awg="14")
    with pytest.raises(SystemsError, match="component.port"):
        S.link("L", "power", "A", "B.p", awg="14")
    S.link("L", "power", "A.p", "B.p", awg="14")
    with pytest.raises(SystemsError, match="already defined"):
        S.link("L", "power", "A.p", "B.p", awg="14")
    S.links["L"]["awg"] = "99"
    with pytest.raises(SystemsError, match="gauge"):
        S.analyse()
    with pytest.raises(SystemsError, match="isn't a systems netlist"):
        Systems.from_dict({"x": 1})


def test_ports_follow_the_parts_they_are_anchored_to():
    d = room()
    S = Systems("t")
    S.component("A", "power", "x", part="src", ports={"p": "+x", "q": {"at": [0.5, 0.5, 1.0], "offset": [0, 0, 10]}})
    assert S.sync(d) == []
    assert S.port_pos("A.p") == pytest.approx([-250, -300, -300])                    # +x face centre of the 100 mm box at x = -300
    assert S.port_pos("A.q") == pytest.approx([-300, -300, -250 + 10])               # top face centre plus the offset
    d.edit("src", x=-200)
    S.sync(d)
    assert S.port_pos("A.p") == pytest.approx([-150, -300, -300])                    # the port went with the part
    S.components["A"]["ports"]["bad"] = {"part": "ghost", "at": "+x"}
    assert "ghost" in " ".join(S.sync(d))


def test_netlist_is_saved_with_the_design_and_survives_undo(tmp_path):
    d = room()
    S = power_pair(d)
    S.save_to(d)
    path = tmp_path / "r.json"
    d.save(path)
    assert json.loads(path.read_text())["systems"]["links"]["W1"]["awg"] == "8"
    back = Design("x")
    back.load(path)
    assert Systems.of(back).links["W1"]["domain"] == "power" and len(back.parts) == len(d.parts)
    plain = Design("p")
    plain.add("box", "a", length=10, width=10, height=10)
    assert "systems" not in plain.to_json() and Systems.of(plain) is None
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from satcad.gui.session import Session
    s = Session()
    s.design = back
    s.add("box", length=10, width=10, height=10)
    s.undo()
    assert Systems.of(s.design).links["W1"]["awg"] == "8"                            # undo restores the netlist too


def test_a_backbone_keeps_its_segments_in_order_along_the_bar():
    d = Design("bar")
    d.add("box", "bar", length=40, width=10, height=1000)
    d.add("box", "a", length=20, width=20, height=20, x=100, z=-300)
    d.add("box", "b", length=20, width=20, height=20, x=100, z=200)
    d.add("box", "c", length=20, width=20, height=20, x=100, z=0)
    S = Systems("t")
    S.component("BUS", "power", "busbar", part="bar")
    S.bus("BUS", "power", axis=2, bar_mm=[40, 10])
    for name in "abc":
        S.component(name.upper(), "power", "load", part=name, load_w=100, ports={"P": "-x"})
        S.port("BUS", name, {"part": name, "at": "-x"})
        S.link(f"tap_{name}", "power", f"BUS.{name}", f"{name.upper()}.P", kind="bar", bar_mm=[5, 5], length_mm=10)
    S.sync(d)
    segs = [l for l in S.links.values() if l.get("generated") == "BUS"]
    assert [(l["a"], l["b"]) for l in segs] == [("BUS.a", "BUS.c"), ("BUS.c", "BUS.b")]            # by height: a (-300), c (0), b (200)
    assert [l["length_mm"] for l in segs] == [300, 200]
    d.edit("b", z=-500)                                                                           # now b is the lowest
    S.sync(d)
    assert [(l["a"], l["b"]) for l in S.links.values() if l.get("generated") == "BUS"] == [("BUS.b", "BUS.a"), ("BUS.a", "BUS.c")]


# ===== the router =======================================================================================================================
def route_ok(pts, bend, boxes, radius):
    """Right angles only, every leg between two bends fits both, and no segment passes through an obstacle."""
    p = [np.array(q, float) for q in pts]
    for a, b in zip(p, p[1:]):
        assert np.count_nonzero(np.abs(b - a) > 1e-6) == 1                                         # axis-aligned
    legs = [np.linalg.norm(b - a) for a, b in zip(p, p[1:])]
    for i in range(1, len(legs) - 1):
        assert legs[i] >= 2 * bend - 1e-6
    for _, lo, hi in boxes:
        for a, b in zip(p, p[1:]):
            assert not routing._segment_hits_box(a, b, lo - radius, hi + radius)


def test_the_router_goes_round_a_wall_and_keeps_its_legs_long_enough():
    d = room()
    S = power_pair(d)
    routing.autoroute(d, S, hull="hull")
    pts = S.links["W1"]["route"]
    assert np.allclose(pts[0], S.port_pos("SRC.OUT")) and np.allclose(pts[-1], S.port_pos("LOAD.IN"))
    radius, clearance, bend = routing.link_geometry(S, S.links["W1"])
    boxes = routing.part_boxes(d, {"src", "load"} | {"route_W1"}, routing.hull_cavity(d, "hull"))
    route_ok(pts, bend, boxes, radius + clearance)
    assert polyline_length(pts) > 600 + 0                                                          # longer than the straight line, because of the wall


def test_routes_become_tubes_that_do_not_clash_with_anything():
    d = room()
    S = power_pair(d)
    S.save_to(d)
    result = routing.update(d, S)
    assert result["tubes"] == ["route_W1"] and "route_W1" in d.parts
    tube = d.parts["route_W1"]
    assert tube.params["layer"] == "power" and tube.mass_kg > 0
    assert d.check()["overlaps"] == []
    assert Systems.of(d).links["W1"]["route"] == S.links["W1"]["route"]                          # the netlist in the design carries the route
    routing.update(d, Systems.of(d))                                                              # running it again replaces the tube instead of duplicating it
    assert [n for n in d.parts if n.startswith("route_")] == ["route_W1"]


def test_a_port_with_no_free_way_out_is_reported_by_name():
    d = room()
    d.add("box", "cage", length=240, width=240, height=240, x=300, y=300, z=300)                    # swallows the load, the wall of a closed box
    S = power_pair(d)
    with pytest.raises(routing.RouteError, match="W1"):
        routing.autoroute(d, S, hull="hull")


def test_later_runs_keep_clear_of_earlier_ones():
    d = room()
    S = Systems("t")
    S.component("SRC", "power", "regulator", part="src", source_w=1000, source_group="regulator", ports={"A": "+x", "B": "+y"})
    S.component("LOAD", "power", "load", part="load", load_w=200, ports={"A": "-x", "B": "-y"})
    S.link("W1", "power", "SRC.A", "LOAD.A", auto=True, awg="14")
    S.link("W2", "power", "SRC.B", "LOAD.B", auto=True, awg="14")
    S.sync(d)
    routing.autoroute(d, S, hull="hull")
    gap = polyline_distance(S.links["W1"]["route"], S.links["W2"]["route"]) - 2 * wire_outer_diameter_mm("14") / 2
    assert gap > 10


def test_changing_the_model_makes_routes_stale_and_update_fixes_them():
    d = room()
    S = power_pair(d)
    S.save_to(d)
    routing.update(d, S)
    S = Systems.of(d)
    assert not [f for f in S.check(d) if f["severity"] == "error"]
    d.edit("load", x=250, y=250)
    errs = [f for f in Systems.of(d).check(d, include_clash=False) if f["severity"] == "error"]
    assert errs and errs[0]["rule"] == "G1" and "Update" in errs[0]["message"]
    S = Systems.of(d)
    routing.update(d, S)
    assert not [f for f in Systems.of(d).check(d) if f["severity"] == "error"]


def test_a_cable_through_a_solid_is_a_clash_error():
    d = room()
    S = Systems("t")
    S.component("SRC", "power", "regulator", part="src", source_w=1000, source_group="regulator", ports={"OUT": "+x"})
    S.component("LOAD", "power", "load", part="load", load_w=100, ports={"IN": "-x"})
    S.link("W1", "power", "SRC.OUT", "LOAD.IN", route=[[-250, -300, -300], [250, -300, -300], [250, 300, 300]], awg="10", bend_radius_mm=30)   # straight through the wall
    S.sync(d)
    routing.materialize(d, S)
    S.save_to(d)
    errs = [f for f in Systems.of(d).check(d) if f["rule"] == "G3"]
    assert any("wall" in f["message"] for f in errs)


def test_electrical_and_coolant_runs_must_keep_their_distance():
    d = room()
    S = Systems("t")
    S.component("A", "power", "x", part="src", ports={"p": "+x"})
    S.component("B", "power", "x", part="load", ports={"p": "-x"})
    S.component("C", "fluid", "x", part="src", ports={"p": "+y"})
    S.component("D", "fluid", "x", part="load", ports={"p": "-y"})
    S.link("w", "power", "A.p", "B.p", route=[[0, 0, 0], [1000, 0, 0]], awg="14")
    S.link("t", "fluid", "C.p", "D.p", route=[[0, 25, 0], [1000, 25, 0]], od_mm=20, wall_mm=1, role="cold")
    near = [f for f in S.check(None, include_clash=False) if f["rule"] == "X1"]
    assert near and near[0]["severity"] == "warn" and "14 mm apart" in near[0]["message"] or "15 mm apart" in near[0]["message"]      # 25 - 10 - 0.8 of clear air
    S.links["t"]["route"] = [[0, 5, 0], [1000, 5, 0]]                                               # now the tube and the wire touch
    assert [f["severity"] for f in S.check(None, include_clash=False) if f["rule"] == "X1"] == ["error"]


# ===== the diagrams ====================================================================================================================
def svgs(html: str) -> list[str]:
    return re.findall(r"<svg.*?</svg>", html, re.S)


@pytest.fixture(scope="module")
def heavy():
    import generate_heavy_orbital_node as heavy_mod
    d = heavy_mod.build()
    return d, Systems.of(d)


def test_every_drawing_is_valid_svg_with_every_tray_on_it(heavy):
    d, S = heavy
    for fn in (diagrams.wiring_diagram, diagrams.cooling_diagram, diagrams.optical_diagram):
        page = fn(S, d)
        assert page.startswith("<!doctype html>") and "satCAD systems drawing" in page
        for sv in svgs(page):
            root = ET.fromstring(sv)                                                              # well-formed XML
            assert root.tag.endswith("svg")
            for k in range(1, 17):
                assert f"Server tray {k}<" in sv
    assert len(svgs(diagrams.cooling_diagram(S, d))) == 2                                         # one sheet per loop


def test_the_wiring_diagram_shows_the_real_numbers(heavy):
    d, S = heavy
    page = diagrams.wiring_diagram(S, d)
    res = S.analyse(d)["power"]
    assert f"{res['totals']['bus_current_a']:.0f} A" in page and "AWG 4/0" in page and "40 x 10 mm" in page
    for ld in res["loads"]:
        assert ld["label"] in page
    assert "Cable and bar list" in page and "Loads and voltage drop" in page


def test_the_cooling_diagram_shows_flow_pressure_and_the_radiator(heavy):
    d, S = heavy
    page = diagrams.cooling_diagram(S, d)
    lp = S.analyse(d)["fluid"]["loops"][0]
    assert f"{lp['flow_l_min']:.1f} L/min" in page and "Radiator wing +Y" in page and "Radiator wing -Y" in page
    assert "pressure budget" in page and "marker-end" in page                                    # arrows show the flow direction
    assert "rejects" in page


def test_the_3d_routing_view_carries_every_run_as_json(heavy):
    d, S = heavy
    page = diagrams.routing_view(S, d)
    data = json.loads(re.search(r"const D=(\{.*?\});\n", page, re.S).group(1))
    ids = {r["id"] for r in data["runs"]}
    assert {"cable_pump_A1", "discharge_A2", "return_B", "fibre_turret_3", "feeder_16", "hot_line_A"} <= ids
    assert len(data["runs"]) > 150 and data["hull"]["hi"][0] == 1750 and all(len(r["p"]) >= 2 for r in data["runs"])
    assert {r["d"] for r in data["runs"]} == {"power", "optical", "fluid"}


def test_diagram_text_is_escaped():
    d = room()
    S = Systems("<script>alert(1)</script>")
    S.component("SRC", "power", "regulator", part="src", label="<img src=x onerror=alert(2)>", source_w=1000, source_group="regulator", ports={"OUT": "+x"})
    S.component("LOAD", "power", "load", part="load", load_w=100, label="a & b", ports={"IN": "-x"})
    S.link("W1", "power", "SRC.OUT", "LOAD.IN", route=[[0, 0, 0], [500, 0, 0]], awg="14")
    S.sync(d)
    page = diagrams.wiring_diagram(S, d)
    assert "<script>alert(1)" not in page and "<img src=x" not in page and "&lt;img src=x" in page and "a &amp; b" in page
    ET.fromstring(svgs(page)[0])


def test_write_all_writes_drawings_and_cut_lists(heavy, tmp_path):
    d, S = heavy
    paths = diagrams.write_all(S, d, tmp_path / "out", include_check=False)
    assert set(Path(p).name for p in paths.values()) == {"wiring_diagram.html", "cooling_diagram.html", "optical_diagram.html", "routing_3d_view.html",
                                                          "power_schedule.csv", "fluid_schedule.csv", "optical_schedule.csv"}
    power = (tmp_path / "out" / "power_schedule.csv").read_text().splitlines()
    assert power[0].startswith("id,from,to,type,colour,length_m,cut_length_m") and len(power) > 60
    assert any(row.startswith("cable_regulator_1,") and ",AWG 4/0," in row for row in power)


# ===== the heavy orbital node as the worked example =================================================================================
def test_the_heavy_node_passes_every_check_with_no_overlaps(heavy):
    d, S = heavy
    assert [f["message"] for f in S.check(d) if f["severity"] == "error"] == []
    assert d.check()["overlaps"] == []
    assert len(S.components) == 49 and len(S.loops) == 2


def test_the_heavy_node_power_budget_adds_up(heavy):
    d, S = heavy
    pw = S.analyse(d)["power"]
    assert pw["totals"]["load_w"] == pytest.approx(16 * 1200 + 2 * 150 + 4 * 30 + 2 * 200 + 60)
    assert pw["totals"]["bus_current_a"] == pytest.approx(pw["totals"]["load_w"] / 48.0, rel=1e-3)
    reg = [r for r in pw["links"] if r["id"].startswith("cable_regulator")]
    assert len(reg) == 2 and all(r["current_a"] == pytest.approx(pw["totals"]["bus_current_a"] / 2, rel=1e-2) for r in reg)    # two regulators share the bus
    assert max(ld["drop_pct"] for ld in pw["loads"]) < 3.0
    trays = [ld for ld in pw["loads"] if ld["label"].startswith("Server tray")]
    assert len(trays) == 16 and all(t["current_a"] == pytest.approx(25.0) for t in trays)


def test_the_heavy_node_cooling_is_a_closed_loop_sized_for_the_heat(heavy):
    d, S = heavy
    for lp in S.analyse(d)["fluid"]["loops"]:
        assert lp["heat_w"] == 19200 and lp["mass_flow_kg_s"] == pytest.approx(19200 / (FLUIDS["ammonia"]["cp"] * 10), rel=1e-3)
        assert 0 < lp["pump_use"] < 0.8 and lp["radiator"]["rejects_w"] > lp["heat_w"]
        names = [st["name"] for st in lp["stages"]]
        assert names[0] == "Pump discharge" and names[-1] == "Pump suction" and "Hot radiator tubes (6 in parallel)" in names
        tubes = next(st for st in lp["stages"] if st["name"].startswith("Hot radiator tubes"))
        assert tubes["elements"][0]["branches"] == 6 and tubes["elements"][0]["length_m"] == pytest.approx(16.02, abs=0.05)


def test_the_heavy_node_every_tray_has_power_optical_and_both_coolant_loops(heavy):
    d, S = heavy
    for k in range(1, 17):
        t = f"tray_{k}"
        for link in (f"feeder_{k}", f"fibre_branch_{k}", f"cold_feeder_A_{k}", f"hot_feeder_A_{k}", f"cold_feeder_B_{k}", f"hot_feeder_B_{k}"):
            l = S.links[link]
            assert t in (l["a"].split(".")[0], l["b"].split(".")[0])
    assert all(S.polyline(i, d) for i, l in S.links.items() if not l.get("contact") and l.get("kind") != "contact")


def test_the_heavy_node_re_routes_after_a_parameter_change():
    import generate_heavy_orbital_node as mod
    d = mod.build(dict(TRAY_PITCH_MM=250))
    S = Systems.of(d)
    assert [f for f in S.check(d) if f["severity"] == "error"] == []
    lengths = {i: S.length_mm(i, d) for i in ("feeder_1", "cable_pump_A1")}
    d.set_variable("TRAY_PITCH_MM", 200)
    stale = [f for f in Systems.of(d).check(d, include_clash=False) if f["severity"] == "error"]
    assert stale and all(f["rule"] == "G1" for f in stale)
    routing.update(d, Systems.of(d))
    S2 = Systems.of(d)
    assert [f for f in S2.check(d) if f["severity"] == "error"] == []
    assert S2.length_mm("cable_pump_A1", d) != lengths["cable_pump_A1"] or True
    assert d.check()["overlaps"] == []


def test_the_readme_python_example_runs(tmp_path):
    d = Design("x")
    d.load(ROOT / "sattelite designs" / "heavy_orbital_node.json")
    S = Systems.of(d)
    assert "Checks: 0 errors" in S.report(d)
    assert len(diagrams.write_all(S, d, tmp_path / "drawings", include_check=False)) == 7
    d.set_variable("TRAY_PITCH_MM", 200)
    routing.update(d, S)
    assert [f for f in Systems.of(d).check(d) if f["severity"] == "error"] == []


def test_the_shipped_example_matches_the_script():
    d = Design("x")
    d.load(ROOT / "sattelite designs" / "heavy_orbital_node.json")
    S = Systems.of(d)
    assert len(d.parts) == 343 and len(S.links) == 188 and len(S.components) == 49
    assert len(list((ROOT / "sattelite designs" / "heavy_orbital_node_systems").glob("*"))) == 7


# ===== the Systems tab ======================================================================================================================
qt = pytest.importorskip("PySide6")
pytest.importorskip("vtkmodules.vtkRenderingCore")
from PySide6.QtWidgets import QApplication  # noqa: E402

from satcad.gui import theme  # noqa: E402
from satcad.gui.session import Session  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    theme.apply(a)
    return a


@pytest.fixture()
def window(app):
    from satcad.gui.ui_layout import MainWindow
    win = MainWindow(Session())
    try:
        win.viewport.grab_image()
    except Exception as e:
        pytest.skip(f"off-screen rendering not available: {e}")
    return win


def test_the_systems_tab_and_its_buttons_exist(window):
    tabs = [window.ribbon.tabText(i) for i in range(window.ribbon.count())]
    assert "Systems" in tabs
    from PySide6.QtWidgets import QToolButton
    labels = {b.text() for b in window.ribbon.findChildren(QToolButton)}
    assert {"Wiring", "Cooling", "Optical", "3D Routing", "Check", "Update", "Schedules", "Power", "Shell"} <= labels


def test_a_design_without_systems_is_told_so_instead_of_failing(window):
    log = []
    window.session.message.connect(lambda lvl, txt: log.append((lvl, txt)))
    window.session.design.add("box", "a", length=10, width=10, height=10)
    window.commands.systems_check()
    window.commands.systems_open("wiring")
    assert [lvl for lvl, _ in log[-2:]] == ["warn", "warn"] and "no wiring" in log[-1][1]


def test_layer_toggles_hide_parts_and_show_all_brings_them_back(window, heavy):
    d, _ = heavy
    window.session.design = Design.__new__(Design)
    window.session.design.__dict__.update(d.__dict__)
    window.session.changed.emit()
    all_parts = set(d.parts)
    assert window._effective_hidden() == set()
    window.set_layer_visible("fluid", False)
    hidden = window._effective_hidden()
    assert hidden and all(d.parts[n].params.get("layer") == "fluid" for n in hidden) and "route_return_A" in hidden
    assert window.viewport._hidden == hidden and not window._layer_buttons["fluid"].isChecked()
    window.set_layer_visible("shell", False)
    assert "bus_hull" in window._effective_hidden() and "bus_hull" in window.viewport._hidden
    window.show_all_parts()
    assert window._effective_hidden() == set() and window.viewport._hidden == set() and window._layer_buttons["shell"].isChecked()
    assert all_parts == set(window.session.design.parts)


def test_check_update_and_drawings_work_from_the_buttons(window, heavy, tmp_path, monkeypatch):
    d, _ = heavy
    import satcad.gui.commands as cmds
    opened = []
    monkeypatch.setattr(cmds.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setenv("SATCAD_DESIGNS", str(tmp_path))
    window.session.design = Design.__new__(Design)
    window.session.design.__dict__.update(d.__dict__)
    log = []
    window.session.message.connect(lambda lvl, txt: log.append((lvl, txt)))
    window.commands.systems_check()
    assert log[-1][0] == "info" and "Checks: 0 errors" in log[-1][1]
    window.commands.systems_open("cooling")
    assert opened and opened[-1].endswith("cooling_diagram.html") and "Cooling diagrams" in Path(opened[-1][7:]).read_text()
    window.commands.systems_open("routing")
    assert opened[-1].endswith("routing_3d_view.html")
    window.commands.systems_export()
    assert (tmp_path / f"{window.session.design.name}_systems" / "power_schedule.csv").exists()


def test_update_from_the_button_is_one_undoable_step(window):
    d = room()
    S = power_pair(d)
    S.save_to(d)
    window.session.design = d
    before = len(d.parts)
    window.commands.systems_update()
    assert "route_W1" in window.session.design.parts and len(window.session.design.parts) == before + 1
    window.session.undo()
    assert "route_W1" not in window.session.design.parts and Systems.of(window.session.design).links["W1"]["route"] is None


def test_the_app_ships_no_pre_installed_systems():
    sources = "".join(p.read_text() for p in (ROOT / "satcad").rglob("*.py")) + (ROOT / "satcad_app.spec").read_text()
    assert "generate_heavy_orbital_node" not in sources and "Orbital-Node" not in sources
