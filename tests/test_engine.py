import math

import pytest
from build123d import import_step

from satcad import Design, DesignError, size_radiator, size_solar_array
from satcad.render import render_png


@pytest.fixture
def sat():
    d = Design("t")
    d.add("chassis", "chassis", length=800, width=600, height=400, wall=8, internal_mass_kg=250)
    d.add("panel", "rad_l", attach_to="chassis", side="+y", length=1500, width=800, thickness=20)
    d.add("panel", "rad_r", attach_to="chassis", side="-y", length=1500, width=800, thickness=20)
    return d


def test_hollow_chassis_volume_and_mass(sat):
    outer, inner = 800 * 600 * 400, 784 * 584 * 384
    c = sat.describe("chassis")
    assert c["volume_cm3"] == pytest.approx((outer - inner) / 1000, rel=1e-4)
    assert c["mass_kg"] == pytest.approx((outer - inner) * 1e-9 * 2700 + 250, rel=1e-4)


def test_solid_chassis_has_full_volume():
    d = Design()
    d.add("chassis", "c", length=100, width=100, height=100)
    assert d.describe("c")["volume_cm3"] == pytest.approx(1000.0)


def test_panel_sits_on_face_and_extends_outward(sat):
    r = sat.describe("rad_l")
    assert r["bbox_min_mm"][1] == pytest.approx(300) and r["bbox_max_mm"][1] == pytest.approx(1800)
    assert r["size_mm"] == [800, 1500, 20]                   # width along X, length along Y, thin along Z
    n = sat.describe("rad_r")
    assert n["bbox_max_mm"][1] == pytest.approx(-300) and n["bbox_min_mm"][1] == pytest.approx(-1800)


def test_gap_and_offsets():
    d = Design()
    d.add("chassis", "c", length=100, width=100, height=100)
    d.add("panel", "p", attach_to="c", side="+x", length=50, width=40, thickness=2, gap=10, offset_span=5, offset_thin=-3)
    p = d.describe("p")
    assert p["bbox_min_mm"][0] == pytest.approx(60) and p["bbox_max_mm"][0] == pytest.approx(110)
    assert (p["bbox_min_mm"][1] + p["bbox_max_mm"][1]) / 2 == pytest.approx(5)       # span axis is Y for a +x face
    assert (p["bbox_min_mm"][2] + p["bbox_max_mm"][2]) / 2 == pytest.approx(-3)


def test_parametric_edit_moves_attached_panels(sat):
    sat.edit("chassis", width=1000)
    assert sat.describe("rad_l")["bbox_min_mm"][1] == pytest.approx(500)
    assert sat.describe("rad_r")["bbox_max_mm"][1] == pytest.approx(-500)


def test_tilt_hinges_about_attached_edge():
    d = Design()
    d.add("chassis", "c", length=100, width=100, height=100)
    d.add("panel", "p", attach_to="c", side="+y", length=1000, width=100, thickness=1, tilt_deg=30)
    p = d.describe("p")
    # hinge on the top edge for an upward tilt: the tip rises by L*sin(30) and the panel never digs into the chassis
    assert p["bbox_max_mm"][2] == pytest.approx(0.5 + 1000 * math.sin(math.radians(30)), abs=0.5)
    assert p["bbox_min_mm"][1] >= 50 - 1e-6


@pytest.mark.parametrize("side", ["+y", "-y", "+x", "-x", "+z", "-z"])
@pytest.mark.parametrize("tilt", [-70, -30, 30, 70])
def test_tilted_panels_never_dig_into_their_parent(side, tilt):
    d = Design()
    d.add("chassis", "c", length=400, width=300, height=200, wall=10)
    d.add("panel", "p", attach_to="c", side=side, length=600, width=150, thickness=20, tilt_deg=tilt)
    assert d.check()["ok"], d.check()["overlaps"]


def test_overlap_and_floating_detection(sat):
    assert sat.check()["ok"] and not sat.check()["floating_parts"]       # touching panels are fine
    sat.add("box", "rack", length=200, width=200, height=200, x=0, y=0, z=0)       # inside the hollow housing: legitimate
    assert sat.check()["ok"]
    sat.add("box", "intruder", length=200, width=200, height=200, x=400, y=0, z=0)  # pokes through the +x wall
    result = sat.check()
    assert not result["ok"] and any({"chassis", "intruder"} == {o["a"], o["b"]} for o in result["overlaps"])
    sat.add("box", "far", length=50, width=50, height=50, x=5000, y=5000, z=5000)
    assert any(f["part"] == "far" for f in sat.check()["floating_parts"])


def test_measure_total_and_centre_of_mass(sat):
    m = sat.measure()
    assert m["total_mass_kg"] == pytest.approx(sat.describe("chassis")["mass_kg"] + 2 * 1500 * 800 * 20 * 1e-9 * 100, rel=1e-4)
    assert m["centre_of_mass_mm"] == [pytest.approx(0, abs=0.1)] * 3
    assert m["overall_size_mm"] == [pytest.approx(800), pytest.approx(3600), pytest.approx(400)]


def test_friendly_errors(sat):
    with pytest.raises(DesignError, match="already exists"):
        sat.add("chassis", "chassis", length=1, width=1, height=1)
    with pytest.raises(DesignError, match="needs these settings"):
        sat.add("box", "b", length=10, width=10)
    with pytest.raises(DesignError, match="no setting called"):
        sat.add("box", "b", length=10, width=10, height=10, colour="red")
    with pytest.raises(DesignError, match="greater than 0"):
        sat.add("box", "b", length=-1, width=10, height=10)
    with pytest.raises(DesignError, match="too thick"):
        sat.add("chassis", "thick", length=100, width=100, height=100, wall=60)
    with pytest.raises(DesignError, match="side"):
        sat.add("panel", "bad", attach_to="chassis", side="up", length=1, width=1, thickness=1)
    with pytest.raises(DesignError, match="no such part"):
        sat.add("panel", "bad", attach_to="ghost", side="+y", length=1, width=1, thickness=1)
    with pytest.raises(DesignError, match="Unknown material"):
        sat.add("box", "b", length=1, width=1, height=1, material="unobtainium")
    with pytest.raises(DesignError, match="Names may only"):
        sat.add("box", "bad name!", length=1, width=1, height=1)
    assert list(sat.parts) == ["chassis", "rad_l", "rad_r"]              # failed adds left nothing behind


def test_cannot_remove_part_with_attachments(sat):
    with pytest.raises(DesignError, match="attached to it"):
        sat.remove("chassis")
    sat.remove("rad_l")
    assert "rad_l" not in sat.parts


def test_failed_edit_rolls_back(sat):
    with pytest.raises(DesignError):
        sat.edit("chassis", wall=999)
    assert sat.describe("chassis")["settings"]["wall"] == 8


def test_save_load_roundtrip(sat, tmp_path):
    before = sat.measure()
    path = sat.save(tmp_path / "d.json")
    other = Design()
    other.load(path)
    assert other.measure() == before and list(other.parts) == list(sat.parts)


def test_step_export_reimports_with_same_volume(sat, tmp_path):
    files = sat.export(tmp_path, "sat")
    assert {"step", "stl", "glb"} == set(files) and all(f["kilobytes"] > 0 for f in files.values())
    back = import_step(files["step"]["path"])
    assert back.volume == pytest.approx(sum(p.volume_mm3 for p in sat.parts.values()), rel=1e-4)


def test_cylinder_and_sphere_orientation():
    d = Design()
    d.add("cylinder", "cx", radius=10, height=100, axis="x")
    d.add("sphere", "s", radius=10, x=500)
    assert d.describe("cx")["size_mm"] == [pytest.approx(100), pytest.approx(20), pytest.approx(20)]
    assert d.describe("s")["volume_cm3"] == pytest.approx(4 / 3 * math.pi * 10 ** 3 / 1000, rel=1e-3)


def test_radiator_sizing_matches_stefan_boltzmann():
    r = size_radiator(1000, panel_temp_k=300, emissivity=1.0, sides=2, sink_temp_k=0.001, panel_count=1)
    assert r["flux_w_per_m2_per_side"] == pytest.approx(5.670374419e-8 * 300 ** 4, rel=1e-3)
    assert r["required_panel_area_m2"] == pytest.approx(1000 / (2 * 5.670374419e-8 * 300 ** 4), abs=0.01)
    hot = size_radiator(100_000, panel_temp_k=360)["required_panel_area_m2"]
    cool = size_radiator(100_000, panel_temp_k=300)["required_panel_area_m2"]
    assert hot < cool                                                     # hotter panels need less area
    with pytest.raises(ValueError):
        size_radiator(1000, panel_temp_k=100, sink_temp_k=200)


def test_render_produces_png(sat):
    png = render_png(sat)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 20_000
    assert render_png(sat, ("iso",))[:4] == b"\x89PNG"
    with pytest.raises(DesignError):
        render_png(Design())


def test_solar_sizing_matches_hand_calculation():
    r = size_solar_array(100_000, cell_efficiency=0.30, packing_factor=0.9, end_of_life_factor=0.85)
    assert r["usable_w_per_m2"] == pytest.approx(1361 * 0.30 * 0.9 * 0.85, abs=0.1)         # 312.4 W/m2
    assert r["required_array_area_m2"] == pytest.approx(100_000 / 312.4, abs=0.2)
    assert size_solar_array(1000, sun_angle_deg=60)["usable_w_per_m2"] == pytest.approx(
        size_solar_array(1000)["usable_w_per_m2"] / 2, abs=0.1)                               # cos(60) = 0.5
    with pytest.raises(ValueError):
        size_solar_array(-5)


# ---- batch mode and the faster neighbour search -------------------------------------------------------------------------------------
def test_batch_rebuilds_once_and_rolls_back_on_failure():
    d = Design("b")
    rebuilds = []
    original = d.rebuild
    d.rebuild = lambda: (rebuilds.append(1), original())[1]
    with d.batch():
        for i in range(5):
            d.add("box", f"b{i}", length=10, width=10, height=10, x=i * 20)
        d.set_variable("k", 3)
    assert len(rebuilds) == 1 and len(d.parts) == 5 and d.variables == {"k": 3}
    with pytest.raises(DesignError):
        with d.batch():
            d.add("box", "ok", length=10, width=10, height=10)
            d.add("box", "bad", length=-1, width=10, height=10)                  # only discovered when the batch rebuilds
    assert "ok" not in d.features and "bad" not in d.features and len(d.parts) == 5


def test_floating_part_distance_matches_the_exhaustive_answer():
    d = Design("f")
    d.add("box", "a", length=10, width=10, height=10)
    d.add("box", "b", x=100, length=10, width=10, height=10)
    d.add("box", "c", x=300, length=10, width=10, height=10)
    floating = {f["part"]: f["nearest_gap_mm"] for f in d.check()["floating_parts"]}
    assert floating == {"a": 90.0, "b": 90.0, "c": 190.0}
