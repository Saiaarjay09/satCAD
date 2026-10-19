"""Verification of the shell FE solver against closed-form plate and membrane solutions."""
import math

import numpy as np
import pytest

from satcad.fem import DOF, FEModel, Plate

E, NU, RHO = 70000.0, 0.3, 2.7e-9                   # MPa, -, tonne/mm^3  (aluminium)
A_SIDE, T = 1000.0, 10.0                           # square plate 1 m x 1 m x 10 mm
D = E * T ** 3 / (12 * (1 - NU ** 2))              # bending stiffness N mm


def plate(a=A_SIDE, b=A_SIDE, t=T, n=20, name="p", part=None, centre=(0, 0, 0), e1=(1, 0, 0), e2=(0, 1, 0)):
    p = Plate(name, np.array(centre, float), np.array(e1, float), np.array(e2, float), a, b, t, E, NU, RHO, 276.0, part or name)
    p.nx, p.ny = n, max(2, round(n * b / a))
    return p


def edge_nodes(model, axis, value, tol=1e-6):
    return model.nodes_where(lambda x: abs(x[axis] - value) < tol)


def simply_supported(model, bending_only=True):
    xyz = model.mesh.xyz
    half = A_SIDE / 2
    edges = model.nodes_where(lambda x: abs(abs(x[0]) - half) < 1e-6 or abs(abs(x[1]) - half) < 1e-6)
    model.fix_nodes(edges, dofs=(2,))
    model.fix_nodes(range(len(xyz)), dofs=(0, 1, 5))               # no in-plane motion: pure bending test


# ---- membrane ------------------------------------------------------------------------------------------------------------
def test_uniaxial_tension_matches_hooke():
    L, B, t, sigma = 1000.0, 200.0, 10.0, 100.0
    m = FEModel([plate(L, B, t, n=10)], tie=False)
    xyz = m.mesh.xyz
    left = m.nodes_where(lambda x: abs(x[0] + L / 2) < 1e-6)
    right = m.nodes_where(lambda x: abs(x[0] - L / 2) < 1e-6)
    m.fix_nodes(left, dofs=(0,))
    m.fix_nodes([n for n in left if abs(xyz[n][1]) < 1e-6][:1], dofs=(1,))
    m.fix_nodes(range(len(xyz)), dofs=(2, 3, 4, 5))
    force = sigma * B * t
    ys = sorted(xyz[n][1] for n in right)
    loads = {}
    for n in right:
        w = 0.5 if abs(abs(xyz[n][1]) - B / 2) < 1e-6 else 1.0
        loads[n] = (force * w / (len(right) - 1), 0, 0)
    res = m.static(point_loads=loads)
    ux = np.mean([res["displacements_mm"][n][0] for n in right])
    assert ux == pytest.approx(sigma * L / E, rel=1e-6)                 # constant-strain element is exact
    assert res["max_von_mises_mpa"] == pytest.approx(sigma, rel=1e-6)


# ---- static plate bending ------------------------------------------------------------------------------------------------
def test_simply_supported_plate_under_uniform_pressure():
    q = 0.001                                                           # MPa
    m = FEModel([plate(n=20)], tie=False)
    simply_supported(m)
    res = m.static(pressure={"p": q})
    centre = m.nodes_where(lambda x: abs(x[0]) < 1e-6 and abs(x[1]) < 1e-6)[0]
    w = abs(res["displacements_mm"][centre][2])
    assert w == pytest.approx(0.00406 * q * A_SIDE ** 4 / D, rel=0.02)   # Timoshenko: 0.00406 q a^4 / D


def test_clamped_plate_under_uniform_pressure():
    q = 0.001
    m = FEModel([plate(n=20)], tie=False)
    half = A_SIDE / 2
    edges = m.nodes_where(lambda x: abs(abs(x[0]) - half) < 1e-6 or abs(abs(x[1]) - half) < 1e-6)
    m.fix_nodes(edges, dofs=(2, 3, 4))
    m.fix_nodes(range(len(m.mesh.xyz)), dofs=(0, 1, 5))
    res = m.static(pressure={"p": q})
    centre = m.nodes_where(lambda x: abs(x[0]) < 1e-6 and abs(x[1]) < 1e-6)[0]
    assert abs(res["displacements_mm"][centre][2]) == pytest.approx(0.00126 * q * A_SIDE ** 4 / D, rel=0.03)


def test_thick_plate_is_softer_than_kirchhoff_by_the_shear_correction():
    q = 0.001
    thick = FEModel([plate(t=100.0, n=20)], tie=False)                  # a/t = 10: shear deformation matters
    simply_supported(thick)
    d_thick = E * 100.0 ** 3 / (12 * (1 - NU ** 2))
    w = abs(thick.static(pressure={"p": q})["displacements_mm"][thick.nodes_where(lambda x: abs(x[0]) < 1e-6 and abs(x[1]) < 1e-6)[0]][2])
    kirchhoff = 0.00406 * q * A_SIDE ** 4 / d_thick
    assert 1.02 * kirchhoff < w < 1.25 * kirchhoff                      # Mindlin theory adds shear deflection (a few to ~15 %)


def test_mesh_refinement_converges_towards_the_analytic_value():
    q, errs = 0.001, []
    for n in (6, 12, 24):
        m = FEModel([plate(n=n)], tie=False)
        simply_supported(m)
        res = m.static(pressure={"p": q})
        c = m.nodes_where(lambda x: abs(x[0]) < 1e-6 and abs(x[1]) < 1e-6)[0]
        errs.append(abs(abs(res["displacements_mm"][c][2]) / (0.00406 * q * A_SIDE ** 4 / D) - 1))
    assert errs[2] < errs[1] < errs[0] and errs[2] < 0.01


def test_bending_stress_matches_plate_theory():
    """SS square plate under uniform pressure (nu = 0.3): centre moment 0.0479 q a^2 (stress 6M/t^2, equibiaxial so von Mises
    = that stress); corner twisting moment 0.0325 q a^2, pure shear, von Mises = sqrt(3) * 6 M / t^2 (the larger value)."""
    q = 0.001
    m = FEModel([plate(n=24)], tie=False)
    simply_supported(m)
    res = m.static(pressure={"p": q})
    centre_expected = 6 * 0.0479 * q * A_SIDE ** 2 / T ** 2
    corner_expected = math.sqrt(3) * 6 * 0.0325 * q * A_SIDE ** 2 / T ** 2
    c = res["element_centroids_mm"]
    near_centre = np.argmin(np.linalg.norm(c[:, :2], axis=1))
    assert res["von_mises_by_element_mpa"][near_centre] == pytest.approx(centre_expected, rel=0.06)
    assert res["max_von_mises_mpa"] == pytest.approx(corner_expected, rel=0.06)
    worst = np.argmax(res["von_mises_by_element_mpa"])
    assert abs(abs(c[worst][0]) - A_SIDE / 2) < 60 and abs(abs(c[worst][1]) - A_SIDE / 2) < 60        # it is at a corner


# ---- modal -------------------------------------------------------------------------------------------------------------------
W0 = math.sqrt(D / (RHO * T))                                            # sqrt(D / (rho t))


def test_simply_supported_plate_fundamental_frequency():
    m = FEModel([plate(n=20)], tie=False)
    simply_supported(m)
    res = m.modal(4)
    expected = (2 * math.pi ** 2 / A_SIDE ** 2) * W0 / (2 * math.pi)
    assert res["frequencies_hz"][0] == pytest.approx(expected, rel=0.02)
    assert res["frequencies_hz"][1] / res["frequencies_hz"][0] == pytest.approx(2.5, rel=0.03)      # (1,2) and (2,1) modes: 5/2


def test_cantilever_square_plate_frequency():
    m = FEModel([plate(n=20)], tie=False)
    m.fix_nodes(edge_nodes(m, 0, -A_SIDE / 2))                         # clamp one edge
    res = m.modal(3)
    assert res["frequencies_hz"][0] == pytest.approx(3.4917 / (2 * math.pi * A_SIDE ** 2) * W0, rel=0.03)    # Leissa


def test_free_free_plate_has_six_rigid_body_modes_and_the_known_first_elastic_mode():
    m = FEModel([plate(n=20)], tie=False)
    res = m.modal(3)
    assert res["rigid_body_modes_removed"] == 6
    assert res["frequencies_hz"][0] == pytest.approx(13.468 / (2 * math.pi * A_SIDE ** 2) * W0, rel=0.04)    # Leissa free square plate


def test_frequency_scales_with_thickness_and_stiffness():
    base = FEModel([plate(n=12)], tie=False)
    simply_supported(base)
    f0 = base.modal(1)["frequencies_hz"][0]
    thick = FEModel([plate(t=2 * T, n=12)], tie=False)
    simply_supported(thick)
    assert thick.modal(1)["frequencies_hz"][0] == pytest.approx(2 * f0, rel=0.03)                  # f ~ t for a thin plate


# ---- equilibrium and mass ----------------------------------------------------------------------------------------------------
def test_total_mass_and_reaction_equal_inertia_load():
    m = FEModel([plate(n=10)], tie=False)
    m.fix_nodes(edge_nodes(m, 0, -A_SIDE / 2))
    assert m.total_mass() == pytest.approx(RHO * A_SIDE * A_SIDE * T, rel=1e-9)
    res = m.static(accel_g=(0, 0, 5))
    assert res["reaction_force_n"][2] == pytest.approx(m.total_mass() * 5 * 9806.65, rel=1e-6)       # support carries m*a
    assert abs(res["reaction_force_n"][0]) < 1e-6 * res["reaction_force_n"][2]


def test_static_needs_a_support_and_modal_effective_mass_adds_up():
    free = FEModel([plate(n=8)], tie=False)
    with pytest.raises(ValueError, match="needs at least one support"):
        free.static(accel_g=(0, 0, 1))
    fixed = FEModel([plate(n=12)], tie=False)
    fixed.fix_nodes(edge_nodes(fixed, 0, -A_SIDE / 2))
    res = fixed.modal(30)
    assert 0.5 < res["cumulative_effective_mass"]["z"] <= 1.0001         # bending modes carry most of the out-of-plane mass
    assert all(f > 0 for f in res["frequencies_hz"]) and res["frequencies_hz"] == sorted(res["frequencies_hz"])


# ---- ties ----------------------------------------------------------------------------------------------------------------------
def test_two_coplanar_plates_tied_together_behave_like_one_plate():
    half = A_SIDE / 2
    left = plate(a=half, b=A_SIDE, n=10, name="L", part="L", centre=(-half / 2, 0, 0))
    right = plate(a=half, b=A_SIDE, n=10, name="R", part="R", centre=(half / 2, 0, 0))
    split = FEModel([left, right], tie=True)
    xyz = split.mesh.xyz
    edges = split.nodes_where(lambda x: abs(abs(x[0]) - A_SIDE / 2) < 1e-6 or abs(abs(x[1]) - A_SIDE / 2) < 1e-6)
    split.fix_nodes(edges, dofs=(2,))
    split.fix_nodes(range(len(xyz)), dofs=(0, 1, 5))
    mono = FEModel([plate(n=20)], tie=False)
    simply_supported(mono)
    f_split, f_mono = split.modal(1)["frequencies_hz"][0], mono.modal(1)["frequencies_hz"][0]
    assert len(split.ties) == 21 and f_split == pytest.approx(f_mono, rel=0.01)


def test_panel_tied_to_a_clamped_wall_acts_as_a_clamped_cantilever():
    wall = plate(a=500, b=500, t=20, n=10, name="wall", part="wall", centre=(0, 0, 0), e1=(0, 1, 0), e2=(0, 0, 1))   # in the y-z plane at x=0
    panel = plate(a=1000, b=500, t=10, n=10, name="panel", part="panel", centre=(500, 0, 0), e1=(1, 0, 0), e2=(0, 1, 0))
    tied = FEModel([wall, panel], tie=True)
    tied.fix_nodes([n for n in range(len(tied.mesh.xyz)) if tied.mesh.xyz[n][0] == 0.0 or abs(tied.mesh.xyz[n][0]) < 1e-9])
    alone = FEModel([plate(a=1000, b=500, t=10, n=10, name="panel", part="panel", centre=(500, 0, 0))], tie=False)
    alone.fix_nodes(alone.nodes_where(lambda x: abs(x[0]) < 1e-6))
    f_t, f_a = tied.modal(2)["frequencies_hz"], alone.modal(2)["frequencies_hz"]
    assert len(tied.ties) > 0
    assert f_t[0] == pytest.approx(f_a[0], rel=0.02)


def test_tie_offset_transfers_moment_through_an_eccentric_connection():
    """A panel tied to a wall at an offset: wall rotation must move the panel tip (slave follows master + theta x r)."""
    wall = plate(a=400, b=400, t=20, n=8, name="wall", part="wall", centre=(0, 0, 0), e1=(0, 1, 0), e2=(0, 0, 1))
    panel = plate(a=800, b=200, t=10, n=8, name="panel", part="panel", centre=(410, 0, 0), e1=(1, 0, 0), e2=(0, 1, 0))
    m = FEModel([wall, panel], tie=True)
    xyz = m.mesh.xyz
    m.fix_nodes([n for n in range(len(xyz)) if abs(xyz[n][2] + 200) < 1e-6 and abs(xyz[n][0]) < 1e-6])    # clamp the wall bottom edge only
    res = m.static(accel_g=(0, 0, 1))
    assert res["max_displacement_mm"] > 0 and np.isfinite(res["max_von_mises_mpa"])
    tip = [n for n in range(len(xyz)) if abs(xyz[n][0] - 810) < 1e-6]
    assert np.mean([res["displacements_mm"][n][2] for n in tip]) < 0                  # droops under +z support acceleration


def test_unequal_and_equal_area_plates_both_tie_in_whichever_direction_touches():
    """Regression: a panel and wall of exactly equal area used to be skipped, leaving the panel floating."""
    wall = plate(a=400, b=400, t=20, n=8, name="wall", part="wall", centre=(0, 0, 0), e1=(0, 1, 0), e2=(0, 0, 1))
    panel = plate(a=800, b=200, t=10, n=8, name="panel", part="panel", centre=(410, 0, 0), e1=(1, 0, 0), e2=(0, 1, 0))
    assert wall.area == panel.area
    m = FEModel([wall, panel], tie=True)
    m.prepare()
    assert len(m.ties) > 0 and len(m.components()) == 1 and m.warnings == []


def test_disconnected_pieces_are_reported_not_silently_wrong():
    a = plate(a=300, b=300, t=10, n=4, name="a", part="a", centre=(0, 0, 0))
    b = plate(a=300, b=300, t=10, n=4, name="b", part="b", centre=(5000, 0, 0))
    m = FEModel([a, b], tie=True)
    m.fix_nodes(m.nodes_where(lambda x: abs(x[0] + 150) < 1e-6, plate_names=["a"]))
    m.prepare()
    assert len(m.components()) == 2 and any("separate pieces" in w for w in m.warnings)
    with pytest.raises(ValueError, match="not connected to any support"):
        m.static(accel_g=(0, 0, 1))


def test_face_to_face_bonded_plates_act_as_a_composite_plate():
    """Two identical plates bonded face to face (rigid offset links) form a plate of double thickness: D x 8, mass x 2, so f x 2."""
    single = FEModel([plate(n=12)], tie=False)
    simply_supported(single)
    f1 = single.modal(1)["frequencies_hz"][0]
    lower = plate(n=12, name="lower", part="lower", centre=(0, 0, 0))
    upper = plate(n=12, name="upper", part="upper", centre=(0, 0, T))                 # mid-surfaces T apart: faces just touch
    stack = FEModel([lower, upper], tie=True)
    stack.prepare()
    assert len(stack.components()) == 1 and len(stack.ties) == len(stack.mesh.plate_nodes[1])    # every node of one plate is bonded
    half = A_SIDE / 2
    edges = stack.nodes_where(lambda x: abs(abs(x[0]) - half) < 1e-6 or abs(abs(x[1]) - half) < 1e-6, plate_names=["lower", "upper"])
    stack.fix_nodes(edges, dofs=(2,))                                      # simply supported: uz = 0 on the edges
    xyz = stack.mesh.xyz                                                    # remove only the in-plane RIGID-BODY motion, so the
    centre = [n for n in stack.mesh.plate_nodes[1] if abs(xyz[n][0]) < 1e-6 and abs(xyz[n][1]) < 1e-6][0]       # membrane offset action
    on_axis = [n for n in stack.mesh.plate_nodes[1] if abs(xyz[n][1]) < 1e-6 and abs(xyz[n][0] - half) < 1e-6][0]  # that gives composite stiffness stays
    stack.fix_nodes([centre], dofs=(0, 1, 5))
    stack.fix_nodes([on_axis], dofs=(1,))
    assert stack.modal(1)["frequencies_hz"][0] == pytest.approx(2 * f1, rel=0.08)


def test_modal_reports_loose_parts_instead_of_hanging():
    a = plate(a=300, b=300, t=10, n=4, name="a", part="a", centre=(0, 0, 0))
    b = plate(a=300, b=300, t=10, n=4, name="b", part="b", centre=(5000, 0, 0))
    c = plate(a=300, b=300, t=10, n=4, name="c", part="c", centre=(0, 5000, 0))
    m = FEModel([a, b, c], tie=True)
    r = m.modal(3)                                                                         # three separate pieces: 18 rigid modes
    assert r["rigid_body_modes_removed"] == 18 and all(f > 0 for f in r["frequencies_hz"])
    assert any("3 separate pieces" in w for w in m.warnings)
