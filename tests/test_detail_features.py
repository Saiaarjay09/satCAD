"""Detail-level features: more profiles, shell, sweep, loft, edge fillets, ISO holes, fasteners."""
import math

import pytest
from build123d import Vector

from satcad import Design, DesignError

PI = math.pi


def vol_mm3(d, name):
    return d.parts[name].volume_mm3


# ---- more extrude profiles --------------------------------------------------------------------------------
def test_slot_rounded_rect_ellipse_ngon_areas():
    d = Design()
    d.add("extrude", "slot", profile="slot", width=100, height=30, depth=5)
    assert vol_mm3(d, "slot") == pytest.approx((PI * 15 ** 2 + (100 - 30) * 30) * 5, rel=1e-4)
    d.add("extrude", "rr", profile="rounded_rect", width=100, height=50, corner_radius=10, depth=5)
    assert vol_mm3(d, "rr") == pytest.approx((100 * 50 - (4 - PI) * 10 ** 2) * 5, rel=1e-4)
    d.add("extrude", "el", profile="ellipse", width=80, height=40, depth=5)
    assert vol_mm3(d, "el") == pytest.approx(PI * 40 * 20 * 5, rel=1e-4)
    d.add("extrude", "hex", profile="ngon", sides=6, radius=10, depth=5)
    assert vol_mm3(d, "hex") == pytest.approx(6 / 2 * 100 * math.sin(2 * PI / 6) * 5, rel=1e-4)


@pytest.mark.parametrize("kwargs, message", [
    (dict(profile="slot", width=20, height=30, depth=5), "larger than its height"),
    (dict(profile="slot", width=100, depth=5), "width and height"),
    (dict(profile="rounded_rect", width=100, height=50, depth=5), "corner_radius"),
    (dict(profile="rounded_rect", width=100, height=50, corner_radius=30, depth=5), "corner_radius"),
    (dict(profile="ngon", sides=2, radius=10, depth=5), "ngon"),
    (dict(profile="ngon", sides=6, depth=5), "ngon"),
    (dict(profile="ellipse", width=10, height=5, depth=5, wall=1), "Hollow"),
])
def test_new_profile_errors(kwargs, message):
    with pytest.raises(DesignError, match=message):
        Design().add("extrude", "e", **kwargs)


# ---- shell -----------------------------------------------------------------------------------------------------
def test_shell_leaves_the_chosen_face_open():
    d = Design()
    d.add("box", "case", length=100, width=80, height=60)
    d.add("shell", "hollow", target="case", open_faces=["+z"], thickness=5)
    assert vol_mm3(d, "case") == pytest.approx(100 * 80 * 60 - 90 * 70 * 55, rel=1e-6)
    assert d.describe("case")["size_mm"] == [100, 80, 60]                      # inward shell keeps the outer size
    assert d.parts["case"].solid.is_inside(Vector(47, 0, 0))                   # inside the +x wall
    assert not d.parts["case"].solid.is_inside(Vector(0, 0, 0))                # empty cavity


def test_shell_with_two_open_faces_and_outward_direction():
    d = Design()
    d.add("box", "tube", length=100, width=80, height=60)
    d.add("shell", "s", target="tube", open_faces=["+z", "-z"], thickness=4)
    assert vol_mm3(d, "tube") == pytest.approx(100 * 80 * 60 - 92 * 72 * 60, rel=1e-6)
    e = Design()
    e.add("box", "b", length=100, width=80, height=60)
    e.add("shell", "s", target="b", open_faces=["+z"], thickness=5, direction="outward")
    assert e.describe("b")["size_mm"][0] == 110                                 # outward shell grows the part


def test_shell_errors_and_parametric_follow():
    d = Design()
    d.add("box", "b", length=100, width=80, height=60)
    for bad, msg in [(dict(open_faces=[], thickness=5), "open_faces"), (dict(open_faces=["up"], thickness=5), "open_faces"),
                     (dict(open_faces=["+z"], thickness=5, direction="sideways"), "direction"),
                     (dict(open_faces=["+z"], thickness=-1), "greater than 0")]:
        with pytest.raises(DesignError, match=msg):
            d.add("shell", "s", target="b", **bad)
    with pytest.raises(DesignError):
        d.add("shell", "s", target="b", open_faces=["+z"], thickness=50)        # walls would meet: nothing sensible left
    d.add("shell", "s", target="b", open_faces=["+z"], thickness=5)
    d.edit("b", length=200)                                                      # the shell follows
    assert vol_mm3(d, "b") == pytest.approx(200 * 80 * 60 - 190 * 70 * 55, rel=1e-6)


# ---- sweep -------------------------------------------------------------------------------------------------------
def test_straight_pipe_volume_and_direction():
    d = Design()
    d.add("sweep", "p", profile="circle", radius=20, path=[[0, 0, 0], [0, 0, 500]])
    assert vol_mm3(d, "p") == pytest.approx(PI * 400 * 500, rel=1e-4)
    assert d.describe("p")["bbox_max_mm"][2] == pytest.approx(500)


def test_bent_pipe_follows_pappus_theorem():
    d = Design()
    path = [[0, 0, 0], [500, 0, 0], [500, 400, 0], [500, 400, 300]]
    d.add("sweep", "p", profile="circle", radius=20, path=path, bend_radius=60)
    centreline = 1200 - 2 * (2 * 60 - PI / 2 * 60)                              # two 90-degree bends replace 2*60 of straight run
    assert vol_mm3(d, "p") == pytest.approx(PI * 400 * centreline, rel=2e-3)
    assert d.describe("p")["bbox_max_mm"][2] == pytest.approx(300, abs=0.1)


def test_hollow_and_rectangular_sweeps():
    d = Design()
    d.add("sweep", "hp", profile="circle", radius=20, wall=3, path=[[0, 0, 0], [800, 0, 0]])
    assert vol_mm3(d, "hp") == pytest.approx(PI * (400 - 289) * 800, rel=1e-4)
    d.add("sweep", "rp", profile="rect", width=30, height=10, path=[[0, 100, 0], [0, 100, 600]])
    assert vol_mm3(d, "rp") == pytest.approx(30 * 10 * 600, rel=1e-4)


def test_sweep_errors_are_explained():
    d = Design()
    with pytest.raises(DesignError, match="needs a 'bend_radius'"):
        d.add("sweep", "p", profile="circle", radius=5, path=[[0, 0, 0], [100, 0, 0], [100, 100, 0]])
    with pytest.raises(DesignError, match="too large for the corner"):
        d.add("sweep", "p", profile="circle", radius=5, path=[[0, 0, 0], [100, 0, 0], [100, 100, 0]], bend_radius=200)
    with pytest.raises(DesignError, match="same"):
        d.add("sweep", "p", profile="circle", radius=5, path=[[0, 0, 0], [0, 0, 0]])
    with pytest.raises(DesignError, match="doubles back"):
        d.add("sweep", "p", profile="circle", radius=5, path=[[0, 0, 0], [100, 0, 0], [0, 0, 0]], bend_radius=5)
    with pytest.raises(DesignError, match="at least 2 points"):
        d.add("sweep", "p", profile="circle", radius=5, path=[[0, 0, 0]])
    with pytest.raises(DesignError, match="radius"):
        d.add("sweep", "p", profile="circle", path=[[0, 0, 0], [1, 0, 0]])
    with pytest.raises(DesignError, match="wall"):
        d.add("sweep", "p", profile="circle", radius=5, wall=5, path=[[0, 0, 0], [100, 0, 0]])


def test_collinear_points_do_not_need_a_bend():
    d = Design()
    d.add("sweep", "p", profile="circle", radius=10, path=[[0, 0, 0], [100, 0, 0], [300, 0, 0]], bend_radius=30)
    assert vol_mm3(d, "p") == pytest.approx(PI * 100 * 300, rel=1e-3)


# ---- loft ----------------------------------------------------------------------------------------------------------
def test_loft_circle_to_circle_is_a_frustum():
    d = Design()
    d.add("loft", "cone", sections=[["circle", 0, 100], ["circle", 300, 40]])
    assert vol_mm3(d, "cone") == pytest.approx(PI * 300 * (100 ** 2 + 100 * 40 + 40 ** 2) / 3, rel=2e-3)


def test_loft_rect_to_rect_and_rect_to_circle():
    d = Design()
    d.add("loft", "pyr", sections=[["rect", 0, 200, 100], ["rect", 200, 100, 50]])
    a1, a2 = 200 * 100, 100 * 50
    assert vol_mm3(d, "pyr") == pytest.approx(200 / 3 * (a1 + a2 + math.sqrt(a1 * a2)), rel=2e-3)
    d.add("loft", "adapter", sections=[["rect", 0, 200, 200], ["circle", 300, 80]], plane="yz")
    assert 20000 * 300 < vol_mm3(d, "adapter") < 40000 * 300
    assert d.describe("adapter")["bbox_max_mm"][0] == pytest.approx(300, abs=0.5)         # stacked along +x for the yz plane


def test_loft_with_three_sections_and_errors():
    d = Design()
    d.add("loft", "fair", sections=[["circle", 0, 50], ["circle", 100, 80], ["circle", 200, 30]])
    assert d.describe("fair")["size_mm"][2] == pytest.approx(200, abs=0.5)
    with pytest.raises(DesignError, match="at least 2 sections"):
        d.add("loft", "x", sections=[["circle", 0, 5]])
    with pytest.raises(DesignError, match="must increase"):
        d.add("loft", "x", sections=[["circle", 10, 5], ["circle", 5, 5]])
    with pytest.raises(DesignError, match="Bad section"):
        d.add("loft", "x", sections=[["circle", 0, -5], ["circle", 5, 5]])
    with pytest.raises(DesignError, match="Bad section"):
        d.add("loft", "x", sections=[["star", 0, 5], ["circle", 5, 5]])


# ---- fillet / chamfer chosen edges ------------------------------------------------------------------------------------
def test_fillet_chosen_edges():
    d = Design()
    d.add("box", "b", length=100, width=60, height=40)
    d.add("fillet_edges", "f", target="b", edges="parallel:z", radius=5)
    assert 100 * 60 * 40 - vol_mm3(d, "b") == pytest.approx(4 * (1 - PI / 4) * 25 * 40, rel=1e-3)
    e = Design()
    e.add("box", "b", length=100, width=60, height=40)
    e.add("fillet_edges", "f", target="b", edges="face:+z", radius=4)
    removed = 100 * 60 * 40 - vol_mm3(e, "b")
    assert removed == pytest.approx((1 - PI / 4) * 16 * 320, rel=0.03)


def test_chamfer_chosen_edges_and_errors():
    d = Design()
    d.add("box", "b", length=100, width=60, height=40)
    d.add("fillet_edges", "c", target="b", edges="parallel:z", chamfer=5)
    assert 100 * 60 * 40 - vol_mm3(d, "b") == pytest.approx(4 * 12.5 * 40, rel=1e-3)
    for kwargs, msg in [(dict(edges="parallel:z"), "either"), (dict(edges="parallel:z", radius=2, chamfer=2), "either"),
                        (dict(edges="nothing", radius=2), "'edges' must be"), (dict(edges="parallel:q", radius=2), "'edges' must be")]:
        with pytest.raises(DesignError, match=msg):
            d.add("fillet_edges", "x", target="b", **kwargs)
    with pytest.raises(DesignError, match="Couldn't round"):
        d.add("fillet_edges", "x", target="b", edges="all", radius=500)


# ---- ISO holes ---------------------------------------------------------------------------------------------------------
def plate():
    d = Design()
    d.add("box", "plate", length=100, width=100, height=20)
    return d


def test_clearance_and_tapped_holes_use_the_standard_sizes():
    d = plate()
    d.add("hole", "h1", target="plate", side="+z", standard="M6")
    assert 100 * 100 * 20 - vol_mm3(d, "plate") == pytest.approx(PI * 3.3 ** 2 * 20, rel=1e-4)
    e = plate()
    e.add("hole", "h", target="plate", side="+z", standard="M6", hole_type="tapped", depth=12)
    assert 100 * 100 * 20 - vol_mm3(e, "plate") == pytest.approx(PI * 2.5 ** 2 * 12, rel=1e-4)       # M6 tap drill 5.0


def test_counterbore_and_countersink_volumes():
    d = plate()
    d.add("hole", "h", target="plate", side="+z", standard="M6", hole_type="counterbore")
    expected = PI * 3.3 ** 2 * (20 - 6.5) + PI * 5.5 ** 2 * 6.5
    assert 100 * 100 * 20 - vol_mm3(d, "plate") == pytest.approx(expected, rel=1e-4)
    e = plate()
    e.add("hole", "h", target="plate", side="+z", standard="M6", hole_type="countersink")
    r, big, h = 3.3, 11.3 / 2, (11.3 - 6.6) / 2
    frustum = PI * h / 3 * (big ** 2 + big * r + r ** 2)
    assert 100 * 100 * 20 - vol_mm3(e, "plate") == pytest.approx(PI * r ** 2 * 20 + frustum - PI * r ** 2 * h, rel=2e-3)


def test_hole_grid_and_bolt_circle_positions():
    d = plate()
    d.add("hole", "grid", target="plate", side="+z", standard="M4", count_u=3, pitch_u=20, count_v=2, pitch_v=30)
    assert 100 * 100 * 20 - vol_mm3(d, "plate") == pytest.approx(6 * PI * 2.25 ** 2 * 20, rel=1e-4)
    s = d.parts["plate"].solid
    assert not s.is_inside(Vector(-20, -15, 0)) and not s.is_inside(Vector(20, 15, 0)) and s.is_inside(Vector(0, 0, 0))
    e = plate()
    e.add("hole", "ring", target="plate", side="+z", standard="M5", circle_count=6, circle_diameter=60, circle_start_deg=0)
    s = e.parts["plate"].solid
    assert 100 * 100 * 20 - vol_mm3(e, "plate") == pytest.approx(6 * PI * 2.75 ** 2 * 20, rel=1e-4)
    for k in range(6):
        a = math.radians(60 * k)
        assert not s.is_inside(Vector(30 * math.cos(a), 30 * math.sin(a), 0))
    assert s.is_inside(Vector(0, 0, 0))


def test_holes_from_the_negative_side_and_other_faces():
    d = plate()
    d.add("hole", "h", target="plate", side="-x", standard="M8", hole_type="tapped", depth=15)
    assert 100 * 100 * 20 - vol_mm3(d, "plate") == pytest.approx(PI * 3.4 ** 2 * 15, rel=1e-4)
    assert not d.parts["plate"].solid.is_inside(Vector(-45, 0, 0)) and d.parts["plate"].solid.is_inside(Vector(-30, 0, 0))


def test_hole_errors():
    d = plate()
    for kwargs, msg in [(dict(standard="M7"), "Unknown size"), (dict(standard="M12", hole_type="countersink"), "No countersink"),
                        (dict(standard="M6", hole_type="drilled"), "hole_type"), (dict(standard="M6", count_u=3), "pitch"),
                        (dict(standard="M6", circle_count=4), "circle_diameter"), (dict(standard="M6", depth=-1), "depth"),
                        (dict(standard="M6", offset_u=5000), "missed"), (dict(standard="M6", count_u=0), "at least 1")]:
        with pytest.raises(DesignError, match=msg):
            d.add("hole", "h", target="plate", side="+z", **kwargs)


def test_holes_follow_the_part():
    d = plate()
    d.add("hole", "h", target="plate", side="+z", standard="M6", offset_u=10)
    d.edit("plate", height=40)                                                    # through-hole: now cuts the whole 40 mm
    assert 100 * 100 * 40 - vol_mm3(d, "plate") == pytest.approx(PI * 3.3 ** 2 * 40, rel=1e-4)


# ---- fasteners --------------------------------------------------------------------------------------------------------
def test_m6_screw_volume_mass_and_size():
    d = Design()
    d.add("fastener", "s", size="M6", length=20)
    hex_area = math.sqrt(3) / 2 * 5 ** 2
    expected = PI * 5 ** 2 * 6 + PI * 3 ** 2 * 20 - hex_area * 3
    assert vol_mm3(d, "s") == pytest.approx(expected, rel=2e-3)
    assert d.describe("s")["mass_kg"] == pytest.approx(expected * 8000e-9, rel=2e-3)
    assert d.describe("s")["size_mm"] == pytest.approx([10, 10, 26], abs=0.01)


@pytest.mark.parametrize("axis, check", [
    ("-z", lambda b: b["bbox_min_mm"][2] == pytest.approx(-20) and b["bbox_max_mm"][2] == pytest.approx(6)),
    ("+z", lambda b: b["bbox_max_mm"][2] == pytest.approx(20) and b["bbox_min_mm"][2] == pytest.approx(-6)),
    ("+x", lambda b: b["bbox_max_mm"][0] == pytest.approx(20) and b["bbox_min_mm"][0] == pytest.approx(-6)),
    ("-x", lambda b: b["bbox_min_mm"][0] == pytest.approx(-20) and b["bbox_max_mm"][0] == pytest.approx(6)),
    ("+y", lambda b: b["bbox_max_mm"][1] == pytest.approx(20) and b["bbox_min_mm"][1] == pytest.approx(-6)),
    ("-y", lambda b: b["bbox_min_mm"][1] == pytest.approx(-20) and b["bbox_max_mm"][1] == pytest.approx(6)),
])
def test_fastener_tip_points_the_requested_way(axis, check):
    d = Design()
    d.add("fastener", "s", size="M6", length=20, axis=axis)
    assert check(d.describe("s"))


def test_screw_in_a_clearance_hole_is_a_clean_joint():
    d = Design()
    d.add("box", "plate", length=100, width=100, height=20, z=-10)               # top face at z = 0
    d.add("hole", "h", target="plate", side="+z", standard="M6", hole_type="counterbore")
    d.add("fastener", "s", size="M6", length=20, axis="-z", z=-6.5)               # head seats at the counterbore floor
    check = d.check()
    assert check["ok"] and check["overlaps"] == []
    assert not check["floating_parts"]
    d.add("pattern", "ring", source="s", mode="circular", count=4, axis="z")      # screws on a ring: copies are clones
    assert len([n for n in d.parts if n.startswith("ring_")]) == 3


def test_fastener_errors_and_materials():
    d = Design()
    for kwargs, msg in [(dict(size="M7", length=10), "Unknown size"), (dict(size="M6", length=-1), "greater than 0"),
                        (dict(size="M6", length=10, axis="up"), "axis"), (dict(size="M6", length=10, material="x"), "Unknown material")]:
        with pytest.raises(DesignError, match=msg):
            d.add("fastener", "s", **kwargs)
    d.add("fastener", "ti", size="M8", length=30, material="titanium_ti6al4v")
    d.add("fastener", "ss", size="M8", length=30)
    assert d.describe("ti")["mass_kg"] == pytest.approx(d.describe("ss")["mass_kg"] * 4430 / 8000, rel=1e-4)


def test_detail_features_survive_save_and_load(tmp_path):
    d = Design("detail")
    d.set_variable("t", 4)
    d.add("box", "case", length=120, width=80, height=50, fillet=6)
    d.add("shell", "s", target="case", open_faces=["+z"], thickness="=t")
    d.add("hole", "mount", target="case", side="-z", standard="M4", hole_type="counterbore", count_u=2, pitch_u=60)
    d.add("sweep", "harness", profile="circle", radius=3, path=[[-70, 0, 0], [-70, 0, 60], [0, 0, 60]], bend_radius=15)
    d.add("loft", "adapter", sections=[["rect", 0, 40, 40], ["circle", 30, 15]], x=100)
    path = d.save(tmp_path / "d.json")
    e = Design()
    e.load(path)
    assert e.measure()["total_mass_kg"] == pytest.approx(d.measure()["total_mass_kg"])
    e.set_variable("t", 3)                                                         # formula-driven shell thickness
    assert e.describe("case")["mass_kg"] < d.describe("case")["mass_kg"]


def test_shell_thicker_than_the_fillet_gives_a_helpful_error():
    d = Design()
    d.add("box", "b", length=120, width=80, height=50, fillet=3)
    with pytest.raises(DesignError, match="rounded edges"):
        d.add("shell", "s", target="b", open_faces=["+z"], thickness=4)
    d.add("shell", "ok", target="b", open_faces=["+z"], thickness=2)                 # thinner than the fillet: fine


def test_small_masses_keep_their_precision():
    d = Design()
    d.add("fastener", "tiny", size="M3", length=8)
    assert 0 < d.describe("tiny")["mass_kg"] < 0.002 and len(str(d.describe("tiny")["mass_kg"])) > 6
    assert d.measure()["total_mass_kg"] == pytest.approx(d.describe("tiny")["mass_kg"])


def test_screw_in_a_tapped_hole_is_thread_engagement_not_an_overlap():
    d = Design()
    d.add("box", "block", length=60, width=60, height=30, z=-15)                   # top face at z = 0
    d.add("hole", "tapped", target="block", side="+z", standard="M6", hole_type="tapped", depth=12)
    d.add("fastener", "s", size="M6", length=12, z=0)                              # seated on the top face, tip 12 mm down
    result = d.check()
    assert result["ok"] and result["overlaps"] == []
    assert len(result["thread_engagements"]) == 1 and result["thread_engagements"][0]["part"] == "block"
    assert result["thread_engagements"][0]["engagement_mm3"] == pytest.approx(PI / 4 * (36 - 25) * 12, rel=0.02)


def test_a_screw_through_solid_metal_is_still_an_overlap():
    d = Design()
    d.add("box", "block", length=60, width=60, height=30, z=-15)                   # no hole at all
    d.add("fastener", "s", size="M6", length=12, z=0)
    result = d.check()
    assert not result["ok"] and result["overlaps"] and not result["thread_engagements"]
