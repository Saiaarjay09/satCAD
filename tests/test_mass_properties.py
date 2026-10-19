"""Mass properties checked against closed-form textbook formulas. If these pass, the inertia tensors are trustworthy."""
import math

import numpy as np
import pytest

from satcad import Design

RHO = 2700.0                       # aluminum_6061, kg/m^3


def mm3_to_m3(v):
    return v * 1e-9


def box_inertia(m, a, b, c):
    """Principal moments of a solid cuboid with edge lengths a (x), b (y), c (z) in metres."""
    return np.diag([m / 12 * (b ** 2 + c ** 2), m / 12 * (a ** 2 + c ** 2), m / 12 * (a ** 2 + b ** 2)])


def inertia(d):
    return np.array(d.measure()["inertia_kg_m2"])


def test_solid_box_matches_formula():
    d = Design()
    d.add("box", "b", length=2000, width=1000, height=500)
    m = RHO * mm3_to_m3(2000 * 1000 * 500)
    assert d.measure()["total_mass_kg"] == pytest.approx(m, rel=1e-6)
    assert np.allclose(inertia(d), box_inertia(m, 2.0, 1.0, 0.5), rtol=1e-4)


def test_hollow_chassis_is_outer_minus_inner():
    d = Design()
    d.add("chassis", "c", length=2000, width=1000, height=800, wall=20)
    mo, mi = (RHO * mm3_to_m3(2000 * 1000 * 800), RHO * mm3_to_m3(1960 * 960 * 760))
    expected = box_inertia(mo, 2.0, 1.0, 0.8) - box_inertia(mi, 1.96, 0.96, 0.76)
    assert d.measure()["total_mass_kg"] == pytest.approx(mo - mi, rel=1e-6)
    assert np.allclose(inertia(d), expected, rtol=1e-4)


def test_sphere_and_cylinder_formulas():
    s = Design()
    s.add("sphere", "s", radius=500)
    m = RHO * mm3_to_m3(4 / 3 * math.pi * 500 ** 3)
    assert np.allclose(inertia(s), np.eye(3) * 0.4 * m * 0.25, rtol=1e-3)               # 2/5 m r^2

    for axis, idx in (("z", 2), ("x", 0), ("y", 1)):
        c = Design()
        c.add("cylinder", "c", radius=300, height=1200, axis=axis)
        m = RHO * mm3_to_m3(math.pi * 300 ** 2 * 1200)
        i = np.diag(inertia(c))
        axial = m * 0.3 ** 2 / 2
        transverse = m / 12 * (3 * 0.3 ** 2 + 1.2 ** 2)
        assert i[idx] == pytest.approx(axial, rel=1e-3)
        assert sorted(np.delete(i, idx)) == pytest.approx([transverse, transverse], rel=1e-3)


def test_parallel_axis_theorem_for_two_separated_spheres():
    d = Design()
    d.add("sphere", "a", radius=200, x=-1500)
    d.add("sphere", "b", radius=200, x=1500)
    m = RHO * mm3_to_m3(4 / 3 * math.pi * 200 ** 3)
    i = inertia(d)
    self_term = 0.4 * m * 0.2 ** 2
    assert i[0][0] == pytest.approx(2 * self_term, rel=1e-3)                            # about the line joining them
    assert i[1][1] == pytest.approx(2 * (self_term + m * 1.5 ** 2), rel=1e-3)           # parallel axis: + m d^2
    assert d.measure()["centre_of_mass_mm"] == [pytest.approx(0, abs=0.01)] * 3


def test_centre_of_mass_is_mass_weighted():
    d = Design()
    d.add("box", "heavy", length=1000, width=1000, height=1000, material="stainless_316")
    d.add("box", "light", length=1000, width=1000, height=1000, x=2000, material="aluminum_honeycomb")
    m1, m2 = 8000.0, 100.0
    assert d.measure()["centre_of_mass_mm"][0] == pytest.approx(2000 * m2 / (m1 + m2), abs=0.2)


def test_rotation_swaps_axes():
    a = Design()
    a.add("box", "b", length=2000, width=1000, height=500)
    b = Design()
    b.add("box", "b", length=2000, width=1000, height=500, rz=90)
    ia, ib = np.diag(inertia(a)), np.diag(inertia(b))
    assert ib[0] == pytest.approx(ia[1], rel=1e-4) and ib[1] == pytest.approx(ia[0], rel=1e-4)
    assert ib[2] == pytest.approx(ia[2], rel=1e-4)


def test_equipment_mass_is_spread_through_the_interior():
    """A solid chassis plus equipment mass is a uniform cuboid of the combined mass."""
    d = Design()
    d.add("chassis", "c", length=3000, width=2000, height=1000, internal_mass_kg=5000)
    m = RHO * mm3_to_m3(3000 * 2000 * 1000) + 5000
    assert np.allclose(inertia(d), box_inertia(m, 3.0, 2.0, 1.0), rtol=1e-4)


def test_hollow_chassis_equipment_fills_the_cavity():
    d = Design()
    d.add("chassis", "c", length=2000, width=1000, height=800, wall=20, internal_mass_kg=1000)
    outer = RHO * mm3_to_m3(2000 * 1000 * 800)
    inner = RHO * mm3_to_m3(1960 * 960 * 760)
    shell = box_inertia(outer, 2.0, 1.0, 0.8) - box_inertia(inner, 1.96, 0.96, 0.76)
    equipment = box_inertia(1000, 1.96, 0.96, 0.76)                       # uniform block filling the cavity
    assert np.allclose(inertia(d), shell + equipment, rtol=1e-4)


def test_principal_moments_and_axes_are_consistent():
    d = Design()
    d.add("box", "b", length=2000, width=1000, height=500)
    d.add("sphere", "s", radius=300, x=1500, y=400, z=200)
    m = d.measure()
    i = np.array(m["inertia_kg_m2"])
    assert np.allclose(i, i.T, atol=1e-3)                                               # symmetric
    moments = np.array(m["principal_moments_kg_m2"])
    assert np.allclose(sorted(np.linalg.eigvalsh(i)), moments, rtol=1e-4)
    axes = np.array(m["principal_axes"])
    assert np.allclose(axes @ axes.T, np.eye(3), atol=1e-3)                              # orthonormal
    assert moments[0] <= moments[1] <= moments[2]
    # triangle inequality for a rigid body: no moment exceeds the sum of the other two
    assert moments[2] <= moments[0] + moments[1] + 1e-3


def test_holes_reduce_mass_and_change_inertia():
    d = Design()
    d.add("box", "b", length=1000, width=1000, height=200)
    before = d.measure()
    d.add("cutout", "h", target="b", side="+z", shape="circle", diameter=400)
    after = d.measure()
    assert after["total_mass_kg"] == pytest.approx(before["total_mass_kg"] - RHO * mm3_to_m3(math.pi * 200 ** 2 * 200), rel=1e-4)
    assert after["inertia_kg_m2"][2][2] < before["inertia_kg_m2"][2][2]


def test_bom_totals_match_measured_mass(tmp_path):
    d = Design()
    d.add("chassis", "bus", length=2000, width=1000, height=800, wall=10, internal_mass_kg=500)
    d.add("panel", "rad", attach_to="bus", side="+y", length=3000, width=1000, thickness=20)
    rows = d.bom()
    assert sum(r["mass_kg"] for r in rows) == pytest.approx(d.measure()["total_mass_kg"], abs=0.01)
    path = d.export_bom(tmp_path / "bom.csv")
    lines = open(path).read().strip().splitlines()
    assert lines[0].startswith("part,type,role,material") and lines[-1].startswith("TOTAL")
    assert len(lines) == 2 + len(rows)
