"""New feature types: extrude, revolve, fillet/chamfer, cutout, pattern, mirror."""
import math

import pytest

from satcad import Design, DesignError


# ---- extrude ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("plane, size", [("xy", [100, 40, 300]), ("yz", [300, 100, 40]), ("zx", [40, 300, 100])])
def test_extrude_rectangle_orientation_and_volume(plane, size):
    d = Design()
    r = d.add("extrude", "e", profile="rect", width=100, height=40, depth=300, plane=plane)
    assert r["size_mm"] == size
    assert r["volume_cm3"] == pytest.approx(100 * 40 * 300 / 1000)


def test_extrude_starts_on_the_plane_and_goes_along_its_normal():
    d = Design()
    assert d.add("extrude", "z", profile="rect", width=10, height=10, depth=50)["bbox_min_mm"][2] == 0
    assert d.add("extrude", "x", profile="rect", width=10, height=10, depth=50, plane="yz")["bbox_max_mm"][0] == 50
    assert d.add("extrude", "y", profile="rect", width=10, height=10, depth=50, plane="zx")["bbox_max_mm"][1] == 50


def test_extrude_circle_tube_and_square_tube():
    d = Design()
    tube = d.add("extrude", "tube", profile="circle", radius=50, wall=5, depth=1000, plane="yz")
    assert tube["volume_cm3"] == pytest.approx(math.pi * (50 ** 2 - 45 ** 2) * 1000 / 1000, rel=1e-4)
    sq = d.add("extrude", "sq", profile="rect", width=60, height=40, wall=4, depth=500)
    assert sq["volume_cm3"] == pytest.approx((60 * 40 - 52 * 32) * 500 / 1000)


def test_extrude_polygon_both_sides_and_taper():
    d = Design()
    tri = d.add("extrude", "tri", profile="polygon", points=[[0, 0], [100, 0], [0, 100]], depth=10)
    assert tri["volume_cm3"] == pytest.approx(50.0)
    both = d.add("extrude", "sym", profile="rect", width=10, height=10, depth=100, both_sides=True)
    assert both["bbox_min_mm"][2] == -100 and both["bbox_max_mm"][2] == 100          # depth in EACH direction
    straight = d.add("extrude", "s", profile="rect", width=100, height=100, depth=100)["volume_cm3"]
    tapered = d.add("extrude", "t", profile="rect", width=100, height=100, depth=100, taper_deg=10)["volume_cm3"]
    assert tapered < straight


def test_extrude_pose_moves_the_profile_plane():
    d = Design()
    r = d.add("extrude", "e", profile="circle", radius=10, depth=100, x=500, y=-200, z=50)
    assert r["bbox_min_mm"] == [490, -210, 50] and r["bbox_max_mm"] == [510, -190, 150]


@pytest.mark.parametrize("kwargs, message", [
    (dict(profile="blob", depth=10), "profile"),
    (dict(profile="rect", depth=10), "width and height"),
    (dict(profile="rect", width=10, height=10), "needs these settings"),
    (dict(profile="circle", depth=10), "radius"),
    (dict(profile="polygon", points=[[0, 0], [1, 1]], depth=10), "at least 3"),
    (dict(profile="polygon", points=[[0, 0], [1, 1], [2, 2]], depth=10), "no area"),
    (dict(profile="polygon", points=[[0, 0], [1, "a"], [2, 3]], depth=10), "2 numbers"),
    (dict(profile="rect", width=10, height=10, depth=10, wall=5), "too thick"),
    (dict(profile="polygon", points=[[0, 0], [9, 0], [0, 9]], depth=10, wall=1), "only available for rect and circle"),
    (dict(profile="rect", width=10, height=10, depth=10, plane="ab"), "plane"),
    (dict(profile="rect", width=10, height=10, depth=10, taper_deg=95), "taper"),
    (dict(profile="rect", width=20, height=20, depth=10, wall=2, taper_deg=5), "tapered"),
])
def test_extrude_errors(kwargs, message):
    with pytest.raises(DesignError, match=message):
        Design().add("extrude", "e", **kwargs)


# ---- revolve ---------------------------------------------------------------------------------------------
def test_revolve_tank_with_cone_dome():
    d = Design()
    r = d.add("revolve", "tank", points=[[0, 0], [300, 0], [300, 500], [0, 700]])
    assert r["volume_cm3"] == pytest.approx((math.pi * 300 ** 2 * 500 + math.pi * 300 ** 2 * 200 / 3) / 1000, rel=1e-4)
    assert r["size_mm"] == [600, 600, 700]


def test_revolve_angle_and_axis():
    d = Design()
    full = d.add("revolve", "f", points=[[0, 0], [300, 0], [300, 500], [0, 500]])["volume_cm3"]
    half = d.add("revolve", "h", points=[[0, 0], [300, 0], [300, 500], [0, 500]], angle=180)["volume_cm3"]
    assert half == pytest.approx(full / 2, rel=1e-4)
    along_x = d.add("revolve", "x", points=[[0, 0], [300, 0], [300, 500], [0, 700]], axis="x")
    assert along_x["size_mm"] == [700, 600, 600]


def test_revolve_errors():
    d = Design()
    with pytest.raises(DesignError, match="radius can't be negative"):
        d.add("revolve", "r", points=[[-1, 0], [5, 0], [5, 5]])
    with pytest.raises(DesignError, match="angle"):
        d.add("revolve", "r", points=[[0, 0], [5, 0], [5, 5]], angle=400)
    with pytest.raises(DesignError, match="axis"):
        d.add("revolve", "r", points=[[0, 0], [5, 0], [5, 5]], axis="q")


# ---- fillet / chamfer ------------------------------------------------------------------------------------
def test_fillet_and_chamfer_remove_material():
    d = Design()
    sharp = d.add("box", "a", length=100, width=60, height=40)["volume_cm3"]
    round_ = d.add("box", "b", length=100, width=60, height=40, fillet=5)["volume_cm3"]
    cut = d.add("box", "c", length=100, width=60, height=40, chamfer=5)["volume_cm3"]
    assert 0 < sharp - round_ < sharp * 0.05 and 0 < sharp - cut < sharp * 0.05
    assert cut < round_                                                              # a chamfer removes more than a fillet


def test_fillet_on_hollow_chassis_keeps_it_hollow():
    d = Design()
    sharp = d.add("chassis", "s", length=1000, width=800, height=600, wall=10)["volume_cm3"]
    rounded = d.add("chassis", "r", length=1000, width=800, height=600, wall=10, fillet=40)["volume_cm3"]
    assert 0.5 * sharp < rounded < 1.05 * sharp
    assert rounded != pytest.approx(sharp, rel=1e-3)


@pytest.mark.parametrize("kwargs, message", [
    (dict(fillet=5, chamfer=5), "either fillet"),
    (dict(fillet=30), "too big"),
    (dict(chamfer=-1), "0 or more"),
])
def test_fillet_errors(kwargs, message):
    with pytest.raises(DesignError, match=message):
        Design().add("box", "b", length=100, width=60, height=40, **kwargs)


# ---- cutouts ---------------------------------------------------------------------------------------------
def test_round_hole_through_removes_the_right_volume():
    d = Design()
    d.add("box", "b", length=1000, width=600, height=400)
    d.add("cutout", "h", target="b", side="+x", shape="circle", diameter=100)
    assert d.describe("b")["volume_cm3"] == pytest.approx((1000 * 600 * 400 - math.pi * 50 ** 2 * 1000) / 1000, rel=1e-4)


def test_pocket_depth_and_offsets():
    d = Design()
    d.add("box", "b", length=1000, width=600, height=400)
    d.add("cutout", "p", target="b", side="+z", shape="rect", width=100, height=60, depth=50, offset_u=200, offset_v=-100)
    assert d.describe("b")["volume_cm3"] == pytest.approx((1000 * 600 * 400 - 100 * 60 * 50) / 1000, rel=1e-6)
    # +z face: offset_u runs along x, offset_v along y. The pocket must sit at x=200, y=-100 on the top face.
    d2 = Design()
    d2.add("box", "b", length=1000, width=600, height=400)
    d2.add("cutout", "p", target="b", side="+z", shape="circle", diameter=40, depth=50, offset_u=200, offset_v=-100)
    from build123d import Vector
    assert not d2.parts["b"].solid.is_inside(Vector(200, -100, 190))                  # hole is there...
    assert d2.parts["b"].solid.is_inside(Vector(-200, 100, 190))                      # ...and not at the mirror position


def test_cutout_follows_the_part_when_it_is_resized():
    d = Design()
    d.add("box", "b", length=1000, width=600, height=400)
    d.add("cutout", "h", target="b", side="+x", shape="circle", diameter=100, offset_u=100)
    d.edit("b", length=2000)
    assert d.describe("b")["volume_cm3"] == pytest.approx((2000 * 600 * 400 - math.pi * 50 ** 2 * 2000) / 1000, rel=1e-4)
    d.edit("b", width="=2 * 300 + 200")                                              # formulas work here too
    assert d.describe("b")["volume_cm3"] == pytest.approx((2000 * 800 * 400 - math.pi * 50 ** 2 * 2000) / 1000, rel=1e-4)


def test_cutout_on_a_hollow_chassis_cuts_one_wall_when_depth_matches():
    d = Design()
    d.add("chassis", "c", length=1000, width=800, height=600, wall=10)
    before = d.describe("c")["volume_cm3"]
    d.add("cutout", "port", target="c", side="+x", shape="rect", width=200, height=100, depth=10)
    assert d.describe("c")["volume_cm3"] == pytest.approx(before - 200 * 100 * 10 / 1000, rel=1e-4)


def test_cutout_errors():
    d = Design()
    d.add("box", "b", length=100, width=100, height=100)
    with pytest.raises(DesignError, match="missed"):
        d.add("cutout", "far", target="b", side="+x", shape="circle", diameter=10, offset_u=5000)
    with pytest.raises(DesignError, match="doesn't exist"):
        d.add("cutout", "h", target="ghost", side="+x", shape="circle", diameter=10)
    with pytest.raises(DesignError, match="diameter"):
        d.add("cutout", "h", target="b", side="+x", shape="circle")
    with pytest.raises(DesignError, match="width and height"):
        d.add("cutout", "h", target="b", side="+x", shape="rect", width=5)
    with pytest.raises(DesignError, match="shape"):
        d.add("cutout", "h", target="b", side="+x", shape="star")
    with pytest.raises(DesignError, match="side"):
        d.add("cutout", "h", target="b", side="up", shape="circle", diameter=5)
    assert list(d.features) == ["b"]                                                  # failures left nothing behind


# ---- patterns --------------------------------------------------------------------------------------------
def test_linear_pattern_makes_evenly_spaced_copies():
    d = Design()
    d.add("box", "blade", length=100, width=500, height=300)
    d.add("pattern", "row", source="blade", mode="linear", count=5, dx=250)
    assert {f"row_{i}" for i in range(2, 6)} <= set(d.parts) and len(d.parts) == 5
    xs = [d.describe(n)["centre_mm"][0] for n in ["blade"] + [f"row_{i}" for i in range(2, 6)]]
    assert xs == pytest.approx([0, 250, 500, 750, 1000])
    assert d.measure()["total_mass_kg"] == pytest.approx(5 * d.describe("blade")["mass_kg"])


def test_circular_pattern_places_copies_around_the_axis():
    d = Design()
    d.add("cylinder", "thruster", radius=50, height=200, x=1000)
    d.add("pattern", "ring", source="thruster", mode="circular", count=4, axis="z")
    centres = [d.describe(n)["centre_mm"] for n in ("thruster", "ring_2", "ring_3", "ring_4")]
    assert [round(c[0]) for c in centres] == [1000, 0, -1000, 0] and [round(c[1]) for c in centres] == [0, 1000, 0, -1000]


def test_partial_circular_pattern_spans_the_total_angle():
    d = Design()
    d.add("box", "b", length=10, width=10, height=10, x=500)
    d.add("pattern", "arc", source="b", mode="circular", count=3, axis="z", total_angle=90)
    last = d.describe("arc_3")["centre_mm"]
    assert last[0] == pytest.approx(0, abs=0.5) and last[1] == pytest.approx(500, abs=0.5)


def test_pattern_copies_keep_cutouts_and_follow_edits():
    d = Design()
    d.add("box", "plate", length=100, width=100, height=20)
    d.add("cutout", "hole", target="plate", side="+z", shape="circle", diameter=30)
    d.add("pattern", "row", source="plate", mode="linear", count=3, dx=300)
    vols = [d.describe(n)["volume_cm3"] for n in ("plate", "row_2", "row_3")]
    assert vols[0] == pytest.approx(vols[1]) == pytest.approx(vols[2])
    d.edit("row", count=6)
    assert len(d.parts) == 6
    d.edit("plate", length=200)
    assert d.describe("row_6")["size_mm"][0] == 200


def test_pattern_errors_and_dependencies():
    d = Design()
    d.add("box", "b", length=10, width=10, height=10)
    for kwargs, message in [(dict(mode="linear", count=1, dx=5), "whole number"), (dict(mode="linear", count=3), "spacing"),
                            (dict(mode="spiral", count=3, dx=5), "mode"), (dict(mode="linear", count=3.5, dx=5), "whole number"),
                            (dict(mode="circular", count=3, axis="q"), "axis"), (dict(mode="linear", count=999, dx=5), "2 to 500")]:
        with pytest.raises(DesignError, match=message):
            d.add("pattern", "p", source="b", **kwargs)
    d.add("pattern", "row", source="b", mode="linear", count=3, dx=50)
    with pytest.raises(DesignError, match="attached to it or use it"):
        d.remove("b")
    d.remove("row")
    d.remove("b")
    with pytest.raises(DesignError, match="already exists"):
        d.add("box", "row_2", length=1, width=1, height=1)
        d.add("box", "q", length=1, width=1, height=1)
        d.add("pattern", "row", source="q", mode="linear", count=3, dx=5)


def test_pattern_with_a_formula_count():
    d = Design()
    d.set_variable("n", 4)
    d.add("box", "b", length=10, width=10, height=10)
    d.add("pattern", "p", source="b", mode="linear", count="=n", dx=100)
    assert len(d.parts) == 4
    d.set_variable("n", 7)
    assert len(d.parts) == 7


# ---- mirror ----------------------------------------------------------------------------------------------
def test_mirror_makes_a_reflected_copy():
    d = Design()
    d.add("box", "wing", length=100, width=100, height=10, y=500, z=30)
    d.add("mirror", "wing_other", source="wing", plane="zx")
    assert d.describe("wing_other")["centre_mm"] == pytest.approx([0, -500, 30])
    d.add("mirror", "wing_down", source="wing", plane="xy", offset=10)
    assert d.describe("wing_down")["centre_mm"][2] == pytest.approx(-10)               # reflected about the plane z = 10


def test_mirror_follows_its_source():
    d = Design()
    d.add("box", "w", length=100, width=100, height=10, y=500)
    d.add("mirror", "m", source="w", plane="zx")
    d.edit("w", y=800, length=300)
    assert d.describe("m")["centre_mm"][1] == pytest.approx(-800) and d.describe("m")["size_mm"][0] == 300
    assert d.measure()["centre_of_mass_mm"][1] == pytest.approx(0, abs=0.01)


def test_mirror_errors():
    d = Design()
    d.add("box", "w", length=1, width=1, height=1)
    with pytest.raises(DesignError, match="plane"):
        d.add("mirror", "m", source="w", plane="q")
    with pytest.raises(DesignError, match="doesn't exist"):
        d.add("mirror", "m", source="nope", plane="xy")


# ---- whole designs -----------------------------------------------------------------------------------------
def test_a_realistic_spacecraft_bus_mixes_every_feature(tmp_path):
    d = Design("bus")
    d.set_variable("bus_size", 1200)
    d.add("chassis", "bus", length="=bus_size", width="=bus_size", height=1000, wall=6, internal_mass_kg=300, fillet=20)
    d.add("revolve", "tank", points=[[0, 0], [250, 0], [250, 400], [0, 520]], z="=bus.zmax", material="titanium_ti6al4v")
    d.add("extrude", "strut", profile="circle", radius=15, wall=2, depth=700, plane="yz", x="=bus.xmax", z=-300,       # below the wing, which tilts upward
          material="cfrp")
    d.add("pattern", "struts", source="strut", mode="circular", count=4, axis="z")
    d.add("cutout", "port", target="bus", side="+x", shape="rect", width=200, height=150, depth=6)
    d.add("panel", "wing", attach_to="bus", side="+y", length=2000, width=1000, thickness=20, tilt_deg=15)
    d.add("mirror", "wing_2", source="wing", plane="zx")
    m = d.measure()
    assert m["parts"] == len(d.parts) and m["total_mass_kg"] > 300
    assert d.check()["overlaps"] == []
    d.set_variable("bus_size", 1500)                                                  # one number rebuilds the whole craft
    assert d.describe("bus")["size_mm"][0] == 1500
    d.export(tmp_path, "bus")
    d.save(tmp_path / "bus.json")
    e = Design()
    e.load(tmp_path / "bus.json")
    assert e.measure()["total_mass_kg"] == pytest.approx(d.measure()["total_mass_kg"])
