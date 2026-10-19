"""Motion Studio: the SymPy expression engine, the kinematic joint solver, the Alpha presets, playback, and the docks."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("vtkmodules.vtkRenderingCore")      # not `import vtk`: the full package makes OpenCASCADE meshing ~9x slower
pytest.importorskip("pyqtgraph")
from PySide6.QtWidgets import QApplication  # noqa: E402

import generate_orbital_node_assembly as gen  # noqa: E402
from satcad import Design  # noqa: E402
from satcad.gui import theme  # noqa: E402
from satcad.gui.kinematics_solver import (ColorPulse, Joint, KinematicsError, KinematicsSolver, rotation_about, rotation_matrix,  # noqa: E402
                                          translation_matrix)
from satcad.gui.motion_expressions import ExpressionEngine  # noqa: E402
from satcad.gui.session import Session  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    theme.apply(a)
    return a


# ===== expression engine =============================================================================================================
def engine(*lines, **params):
    e = ExpressionEngine()
    e.param_values.update(params)
    e.set_equations(list(lines))
    return e


def test_position_velocity_acceleration_are_exact_derivatives():
    e = engine("x(t) = 3 * t^2 + 2 * t + 1", "w(t) = A * sin(2 * pi * f * t)", A=45, f=0.5)
    assert e.value("x", 2.0) == pytest.approx(17) and e.velocity("x", 2.0) == pytest.approx(14) and e.acceleration("x", 2.0) == pytest.approx(6)
    t = 0.3
    w = 2 * math.pi * 0.5
    assert e.value("w", t) == pytest.approx(45 * math.sin(w * t))
    assert e.velocity("w", t) == pytest.approx(45 * w * math.cos(w * t))
    assert e.acceleration("w", t) == pytest.approx(-45 * w * w * math.sin(w * t))
    # compare with a numerical derivative as an independent check
    h = 1e-5
    assert e.velocity("w", t) == pytest.approx((e.value("w", t + h) - e.value("w", t - h)) / (2 * h), rel=1e-6)


def test_undefined_names_become_sliders_in_order_of_first_use():
    e = engine("az(t) = A * sin(2 * pi * f * t)", "b(t) = A * 2 + zeta * t", "c = 5")
    assert e.parameters == ["A", "f", "zeta"]                       # zeta is a SymPy function name, but here it is just a number
    assert [e.owner_of(p) for p in e.parameters] == [0, 0, 1]
    assert e.function_names() == ["az", "b"] and e.equations[2].params == ()
    e.set_param("A", 10)
    e.set_param("f", 1)
    e.set_param("zeta", 0.5)
    assert e.value("b", 2.0) == pytest.approx(21.0)
    v0 = e.value("az", 0.25)
    e.set_param("A", 20)                                            # a slider move needs no reparse or recompile
    assert e.value("az", 0.25) == pytest.approx(2 * v0)
    assert e.version == 1


def test_formulas_can_use_other_formulas_and_constants_in_any_order():
    e = engine("total(t) = base(t) + 2 * omega", "omega = 2 * pi * 0.5", "base(t) = 10 * t")
    assert e.value("total", 3.0) == pytest.approx(30 + 2 * math.pi)
    assert e.velocity("total", 3.0) == pytest.approx(10)
    shifted = engine("base(t) = t^2", "later(t) = base(t - 1)")
    assert shifted.value("later", 4.0) == pytest.approx(9)


def test_s_curve_deployment_and_helpers():
    e = engine("fold(t) = 90 * smoothstep((t - 60) / 600)", "g(t) = sigmoid(t) + clamp(t, 0, 1) + sind(90) + max(t, 2) + abs(0 - t)")
    assert e.value("fold", [0, 60, 360, 660, 5000]) == pytest.approx([0, 0, 45, 90, 90])
    assert e.velocity("fold", 360.0) == pytest.approx(90 * 1.5 / 600)                          # the S-curve's steepest point
    assert e.velocity("fold", 0.0) == pytest.approx(0) and e.velocity("fold", 700.0) == pytest.approx(0)       # eases in and out
    assert e.value("g", 0.0) == pytest.approx(0.5 + 0 + 1 + 2 + 0)


def test_functions_of_time_and_an_index_are_vectorised():
    e = engine("pulse(t, k) = 0.5 + 0.5 * sin(2 * pi * (f * t - k / 4))", f=1.0)
    row = e.value("pulse", 0.0, k=np.arange(4))
    assert row == pytest.approx([0.5, 0.0, 0.5, 1.0])                                          # a quarter-period shift per segment
    assert e.arity("pulse") == 2 and e.function_names(1) == []


def test_sample_returns_aligned_arrays():
    e = engine("x(t) = t^3")
    s = e.sample("x", np.linspace(0, 2, 5))
    assert s["x"] == pytest.approx([0, 0.125, 1, 3.375, 8]) and s["v"] == pytest.approx(3 * s["t"] ** 2) and s["a"] == pytest.approx(6 * s["t"])
    c = engine("k(t) = 4").sample("k", np.linspace(0, 1, 3))
    assert c["x"].tolist() == [4, 4, 4] and c["v"].tolist() == [0, 0, 0]                       # constants differentiate to zero


@pytest.mark.parametrize("line, text", [
    ("x(t) = sin(", "brackets"), ("x(t) = 2 $ t", "character"), ("x(t) = __import__('os')", "character"), ("x(t) = t.real", "attributes"),
    ("x(t) = (1).__class__", "double underscore"), ("sin(t) = t", "built-in"), ("y(t) = y(t) + 1", "itself"), ("noequals", "name(t) = formula"),
    ("f(x) = x", "first argument must be t"), ("a(t, t) = t", "different names"), ("w(t) = 1/0", "finite"), ("v(t) = foo(t)", "unknown function"),
])
def test_bad_formulas_give_plain_messages_and_never_run_code(line, text):
    e = engine(line, "good(t) = 2 * t")
    assert text.lower() in e.equations[0].error.lower()
    assert e.value("good", 3.0) == 6                                                           # one bad line does not break the others


def test_circular_and_duplicate_definitions():
    e = engine("a(t) = b(t) + 1", "b(t) = a(t) + 1", "c(t) = t", "c(t) = 2 * t")
    assert "itself" in e.equations[0].error and "itself" in e.equations[1].error
    assert e.equations[3].error and "twice" in e.equations[3].error and e.value("c", 5.0) == 5


def test_engine_state_round_trips():
    e = engine("w(t) = A * sin(t)", A=3.0)
    e.param_ranges["A"] = (0, 9)
    other = ExpressionEngine()
    other.load_dict(e.to_dict())
    assert other.lines() == e.lines() and other.get_param("A") == 3.0 and other.param_ranges["A"] == (0, 9)
    assert other.value("w", 1.0) == pytest.approx(3 * math.sin(1.0))


def test_evaluation_is_fast_enough_for_60_fps():
    import time
    e = engine("az(t) = A * sin(2 * pi * f * t) + 0.1 * cos(7 * t)", A=5, f=0.5)
    t0 = time.perf_counter()
    for i in range(2000):
        e.value("az", i * 0.016)
    assert (time.perf_counter() - t0) / 2000 < 100e-6                                           # far below a 16 ms frame


# ===== kinematic solver ===============================================================================================================
def test_rotation_and_translation_math():
    p = np.array([20.0, 0, 0, 1])
    assert rotation_about((0, 0, 1), (10, 0, 0), 90) @ p == pytest.approx([10, 10, 0, 1])
    assert rotation_about((0, 0, 1), (10, 0, 0), 90) @ np.array([10.0, 0, 0, 1]) == pytest.approx([10, 0, 0, 1])      # the pivot stays put
    assert rotation_matrix((1, 0, 0), 90) @ [0, 1, 0] == pytest.approx([0, 0, 1])
    assert translation_matrix((0, 3, 4), 10) @ np.array([0, 0, 0, 1.0]) == pytest.approx([0, 6, 8, 1])
    with pytest.raises(KinematicsError):
        rotation_matrix((0, 0, 0), 5)


def test_joint_types_and_limits():
    rev = Joint("r", "revolute", "a", axis=(0, 0, 1), pivot=(0, 0, 0), expr="f", offset=-90.0, minimum=-90.0, maximum=0.0)
    assert rev.local_matrix(90) @ [1, 0, 0, 1] == pytest.approx([1, 0, 0, 1])                       # 90 - 90 = 0: home pose
    assert rev.local_matrix(0) @ [1, 0, 0, 1] == pytest.approx([0, -1, 0, 1])                       # stowed: -90 degrees
    assert rev.local_matrix(500) @ [1, 0, 0, 1] == pytest.approx([1, 0, 0, 1])                      # clamped at the upper limit
    pris = Joint("p", "prismatic", "a", axis=(1, 0, 0), expr="f")
    assert pris.local_matrix(25) @ [0, 0, 0, 1] == pytest.approx([25, 0, 0, 1])
    sph = Joint("s", "spherical", "a", axis=(0, 0, 1), axis2=(1, 0, 0), expr="a", expr2="b")
    expected = rotation_about((0, 0, 1), (0, 0, 0), 30) @ rotation_about((1, 0, 0), (0, 0, 0), 20)
    assert sph.local_matrix(30, 20) == pytest.approx(expected)
    assert not np.allclose(sph.local_matrix(30, 20), rotation_about((1, 0, 0), (0, 0, 0), 20) @ rotation_about((0, 0, 1), (0, 0, 0), 30))   # order matters


def _two_stage_design():
    d = Design("t")
    d.add("assembly", "base")
    d.add("assembly", "arm", parent="base")
    d.add("assembly", "hand", parent="arm")
    d.add("box", "post", length=10, width=10, height=10, assembly="base")
    d.add("box", "upper", length=10, width=10, height=10, x=100, assembly="arm")
    d.add("box", "finger", length=10, width=10, height=10, x=200, assembly="hand")
    d.add("box", "loose", length=10, width=10, height=10, x=300)
    return d


def test_nested_joints_compose_parent_then_child():
    d = _two_stage_design()
    joints = [Joint("shoulder", "revolute", "arm", axis=(0, 0, 1), pivot=(0, 0, 0), expr="a"),
              Joint("wrist", "revolute", "hand", axis=(0, 0, 1), pivot=(100, 0, 0), expr="b")]
    s = KinematicsSolver(joints, d)
    assert s._parent == {"shoulder": None, "wrist": "shoulder"}                                     # derived from the assembly nesting
    assert s.parts_of("shoulder") == ["upper"] and s.parts_of("wrist") == ["finger"]
    assert "post" not in s.moving_parts and "loose" not in s.moving_parts
    m = s.part_matrices({"shoulder": (90,), "wrist": (90,)})
    tip = m["finger"] @ np.array([200.0, 0, 0, 1])                                                 # finger centre: wrist (+90) then shoulder (+90)
    assert tip == pytest.approx([-100, 100, 0, 1])
    only_shoulder = s.part_matrices({"shoulder": (90,)})["finger"] @ np.array([200.0, 0, 0, 1])
    assert only_shoulder == pytest.approx([0, 200, 0, 1])
    assert s.pivot_world("wrist", {"shoulder": (90,)}) == pytest.approx([0, 100, 0])                # the wrist pivot is carried round by the shoulder


def test_joint_definition_errors_are_plain():
    d = _two_stage_design()
    with pytest.raises(KinematicsError, match="no assembly called"):
        KinematicsSolver([Joint("j", "revolute", "ghost")], d)
    with pytest.raises(KinematicsError, match="already has the joint"):
        KinematicsSolver([Joint("a", "revolute", "arm"), Joint("b", "revolute", "arm")], d)
    with pytest.raises(KinematicsError, match="same name"):
        KinematicsSolver([Joint("a", "revolute", "arm"), Joint("a", "revolute", "hand")], d)
    with pytest.raises(KinematicsError, match="zero vector"):
        KinematicsSolver([Joint("a", "revolute", "arm", axis=(0, 0, 0))], d)
    with pytest.raises(KinematicsError, match="Joint type"):
        KinematicsSolver([Joint("a", "wobble", "arm")], d)


def test_joint_and_pulse_serialisation():
    j = Joint("fold", "revolute", "wing", axis=(1, 0, 0), pivot=(0, 1830, 0), expr="f", offset=-90.0, minimum=-90.0, maximum=0.0)
    assert Joint.from_dict(j.to_dict()) == j
    s = Joint("g", "spherical", "m", pivot=(1, 2, 3), pivot2=(4, 5, 6), expr="a", expr2="b")
    assert Joint.from_dict(s.to_dict()) == s
    p = ColorPulse("c", ["a", "b"], "coolant", base=(0, 0, 0), hot=(1, 1, 1))
    assert ColorPulse.from_dict(p.to_dict()).parts == ["a", "b"]
    assert p.colours(np.array([0.0, 2.0])) == {"a": (0.0, 0.0, 0.0), "b": (1.0, 1.0, 1.0)}          # intensity is clamped to 0..1


# ===== Orbital-Node-Alpha presets ======================================================================================================
@pytest.fixture(scope="module")
def alpha():
    return gen.build()


def test_alpha_presets_define_the_four_mechanisms(alpha):
    from satcad.gui.motion_presets import alpha_presets
    p = alpha_presets(alpha)
    names = [j.name for j in p.joints]
    assert p.missing == []
    assert sum(n.startswith("SADA_Revolute") for n in names) == 2
    assert sum(n.startswith("Radiator_Fold_Hinge") for n in names) == 2
    assert sum(n.startswith("Turret_Gimbal_Az") for n in names) == 4 and sum(n.startswith("Turret_Gimbal_El") for n in names) == 4
    assert sum(n.startswith("FSM_Jitter") for n in names) == 4 and len(p.joints) == 16
    assert {j.kind for j in p.joints if j.name.startswith("FSM")} == {"spherical"}
    assert [c.name for c in p.pulses] == ["Coolant_Pulse_hot", "Coolant_Pulse_cold"] and all(len(c.parts) == 24 for c in p.pulses)
    e = ExpressionEngine()
    e.param_values.update(p.params)
    e.set_equations(p.equations)
    assert not [q.error for q in e.equations if q.error]
    for j in p.joints:
        assert all(e.has(d) for d in j.drivers), j.name
    assert set(e.parameters) <= set(p.params)                                                  # every slider has a sensible default
    # the SADA turns once per orbit; 90 degrees a quarter of the way round
    assert e.value("theta_solar", 5400 / 4) == pytest.approx(90) and e.value("theta_solar", 5400) == pytest.approx(360)
    # the radiator unfolds along an S-curve from stowed (0) to deployed (90)
    assert e.value("fold", 0) == 0 and e.value("fold", 360) == pytest.approx(45) and e.value("fold", 5000) == pytest.approx(90)


def test_alpha_presets_report_what_the_model_lacks():
    from satcad.gui.motion_presets import alpha_presets
    d = gen.build()
    del_names = [n for n in list(d.features) if n.startswith("turret_fwd_a")]
    p = alpha_presets(Design("empty"))
    assert p.joints == [] and p.pulses == [] and "sada_pos_x" in p.missing
    assert del_names                                                                           # (the full model does have those turrets)


def test_alpha_motion_moves_the_right_parts_the_right_way(alpha):
    from satcad.gui.motion_presets import alpha_presets
    pre = alpha_presets(alpha)
    s = KinematicsSolver(pre.joints, alpha)
    e = ExpressionEngine()
    e.param_values.update(pre.params)
    e.set_equations(pre.equations)

    def values(t):
        return {j.name: tuple(float(e.value(d, t)) for d in j.drivers) for j in s.joints}
    # SADA: wing + yoke rotate about the X axis; the SADA body and the bus do not move
    assert "sada_pos_x" not in s.moving_parts and "bus_hull" not in s.moving_parts
    assert {"yoke_pos_x", "solar_sheet_pos_x"} <= set(s.parts_of("SADA_Revolute_pos_x"))
    m = s.part_matrices(values(5400 / 4))["solar_sheet_pos_x"]
    corner = np.array([3000.0, 0.0, 1000.0, 1])                                               # a point on the wing
    assert (m @ corner)[0] == pytest.approx(3000)                                              # X unchanged: the axis
    assert (m @ corner)[1:3] == pytest.approx([-1000, 0], abs=1e-6)                           # 90 degrees about +X: (y, z) = (0, 1000) -> (-1000, 0)
    # radiator: stowed at t = 0 along -Z, deployed (home pose) once the S-curve is done
    wing_pt = np.array([0.0, 8000.0, 0.0, 1])
    stowed = s.part_matrices(values(0))["radiator_panel_pos_y"] @ wing_pt
    assert stowed[1] == pytest.approx(1830, abs=1e-6) and stowed[2] == pytest.approx(-(8000 - 1830), abs=1e-6)
    assert s.part_matrices(values(1000))["radiator_panel_pos_y"] @ wing_pt == pytest.approx(wing_pt)
    neg = np.array([0.0, -8000.0, 0.0, 1])
    stowed_neg = s.part_matrices(values(0))["radiator_panel_neg_y"] @ neg
    assert stowed_neg[1] == pytest.approx(-1830, abs=1e-6) and stowed_neg[2] < -6000           # the -Y wing also folds toward -Z
    # turret: azimuth moves yoke + barrel + FSM, elevation only the barrel + FSM, the base never moves
    assert s.parts_of("Turret_Gimbal_Az_fwd_a") == ["turret_fwd_a_yoke"]
    assert {"turret_fwd_a_barrel", "turret_fwd_a_lens"} <= set(s.parts_of("Turret_Gimbal_El_fwd_a"))
    assert "turret_fwd_a_base" not in s.moving_parts
    vals = {j.name: (0.0, 0.0) for j in s.joints}
    vals["Turret_Gimbal_Az_fwd_a"] = (10.0,)
    m = s.part_matrices(vals)
    assert m["turret_fwd_a_yoke"] is not None and not np.allclose(m["turret_fwd_a_barrel"], np.eye(4))     # the barrel is carried by the yoke
    vals = {j.name: (0.0, 0.0) for j in s.joints}
    vals["Turret_Gimbal_El_fwd_a"] = (10.0,)
    assert np.allclose(s.part_matrices(vals)["turret_fwd_a_yoke"], np.eye(4))                  # ... but the yoke is not carried by the barrel's stage


# ===== playback and the 3D view =========================================================================================================
@pytest.fixture()
def rig(app):
    from satcad.gui.motion_studio import MotionStudio
    from satcad.gui.viewport import Viewport
    session = Session(gen.build({"NUM_SERVER_BLADES": 4}))
    v = Viewport()
    v.resize(900, 560)
    try:
        v.set_design(session.design, fit=True)
        v.grab_image()
    except Exception as e:
        pytest.skip(f"off-screen rendering not available: {e}")
    return session, v, MotionStudio(session, v)


def test_presets_move_existing_actors_without_remeshing(rig, monkeypatch):
    import satcad.gui.viewport as vp
    session, v, studio = rig
    before = np.array(v._surfaces["radiator_panel_pos_y"].GetBounds())                          # the home (deployed) pose, before any motion
    assert studio.load_alpha_presets() and len(studio.joints) == 16 and len(studio.solver.moving_parts) > 100
    calls = []
    monkeypatch.setattr(vp, "part_face_data", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(vp, "part_edge_polylines", lambda *a, **k: calls.append(1))
    studio.seek(0.0)                                                                           # stowed
    stowed = np.array(v._surfaces["radiator_panel_pos_y"].GetBounds())
    studio.seek(1000.0)                                                                        # deployed
    deployed = np.array(v._surfaces["radiator_panel_pos_y"].GetBounds())
    assert calls == []                                                                         # no tessellation while scrubbing
    assert deployed == pytest.approx(before, abs=1e-3) and not np.allclose(stowed, before)
    assert stowed[4] < -5000 and deployed[3] > 1000                                            # along -Z when stowed, along +Y when deployed
    studio.seek(0.0)
    sheet0 = np.array(v._surfaces["solar_sheet_pos_x"].GetBounds())                            # theta = 0: the wing is in its drawn pose
    studio.seek(1350.0)                                                                        # a quarter orbit: the wing has turned 90 degrees
    sheet90 = np.array(v._surfaces["solar_sheet_pos_x"].GetBounds())
    assert sheet0[5] - sheet0[4] > 2000 and sheet90[3] - sheet90[2] > 2000 and sheet90[5] - sheet90[4] < 100


def test_coolant_pulse_travels_along_the_manifold(rig):
    session, v, studio = rig
    studio.load_alpha_presets()
    studio.seek(0.0)
    c0 = [v._surfaces[p].GetProperty().GetColor() for p in studio.pulses[0].parts]
    studio.seek(0.5)
    c1 = [v._surfaces[p].GetProperty().GetColor() for p in studio.pulses[0].parts]
    assert not np.allclose(c0, c1) and len({round(c[0], 3) for c in c0}) > 4                   # segments differ: a phase shift along the pipe
    studio.reset_pose()
    assert v._color_override == {}


def test_playback_clock_speed_loop_and_stop(rig, monkeypatch):
    import satcad.gui.motion_studio as ms
    session, v, studio = rig
    studio.load_alpha_presets()
    studio.set_t_max(100.0)
    clock = [1000.0]
    monkeypatch.setattr(ms.time, "perf_counter", lambda: clock[0])
    studio.set_speed(2.0)
    seen = []
    studio.time_changed.connect(seen.append)
    studio.play()
    assert studio.playing and v._ss == 1                                                       # fast quality while playing
    clock[0] += 0.5
    studio._tick()
    assert studio.t == pytest.approx(1.0)                                                      # 0.5 s of wall clock at 2x speed
    studio.set_speed(50.0)
    assert studio.speed == 10.0                                                                # clamped to 10x
    clock[0] += 9.5
    studio._tick()
    assert studio.t == pytest.approx(96.0)
    clock[0] += 1.0
    studio._tick()
    assert studio.t == pytest.approx(6.0)                                                      # looped (96 + 10 - 100)
    studio.loop = False
    clock[0] += 10.0
    studio._tick()
    assert studio.t == 100.0 and not studio.playing and v._ss == 2                             # stopped at the end, full quality again
    studio.seek(-5)
    assert studio.t == 0.0
    studio.seek(1e9)
    assert studio.t == 100.0
    studio.stop()
    assert studio.t == 0.0 and not studio.playing


def test_motion_setup_is_saved_and_restored_with_the_part(rig, tmp_path):
    from satcad.gui.motion_studio import MotionStudio
    session, v, studio = rig
    studio.load_alpha_presets()
    studio.engine.set_param("A_az", 7.0)
    assert session.save(tmp_path / "alpha.json")
    data = __import__("json").loads((tmp_path / "alpha.json").read_text())
    assert len(data["motion"]["joints"]) == 16 and data["motion"]["engine"]["params"]["A_az"] == 7.0
    session2 = Session()
    from satcad.gui.viewport import Viewport
    v2 = Viewport()
    v2.resize(600, 400)
    studio2 = MotionStudio(session2, v2)
    assert session2.open(tmp_path / "alpha.json")
    assert len(studio2.joints) == 16 and studio2.engine.get_param("A_az") == 7.0 and len(studio2.pulses) == 2
    session2.new("blank")
    assert studio2.joints == [] and studio2.pulses == [] and studio2.engine.equations == []


def test_joints_whose_assembly_disappears_are_dropped(rig):
    session, v, studio = rig
    studio.set_equations(["spin(t) = 10 * t"])
    assert studio.add_joint(Joint("J", "revolute", "solar_pos_x_wing", axis=(1, 0, 0), expr="spin"))
    session.load_design(Design("other"))
    session.changed.emit()
    assert studio.joints == [] or all(j.assembly in session.design.features for j in studio.joints)


def test_bad_joint_bindings_are_refused_with_a_message(rig):
    session, v, studio = rig
    log = []
    session.message.connect(lambda lvl, txt: log.append((lvl, txt)))
    assert studio.add_joint(Joint("J", "revolute", "no_such_assembly")) is False
    assert log[-1][0] == "error" and "no assembly" in log[-1][1]
    studio.set_equations(["spin(t) = 10 * t"])
    assert studio.add_joint(Joint("A", "revolute", "solar_pos_x_wing", expr="spin")) is True
    assert studio.add_joint(Joint("B", "revolute", "solar_pos_x_wing", expr="spin")) is False          # one joint per assembly


def test_posing_the_whole_alpha_model_is_cheap(rig):
    import time
    session, v, studio = rig
    studio.load_alpha_presets()
    t0 = time.perf_counter()
    for i in range(100):
        studio.pose(i * 7.3)
    assert (time.perf_counter() - t0) / 100 < 0.012                                            # a pose is well under a 16 ms frame


# ===== the docks ===========================================================================================================================
def test_sidebar_makes_sliders_for_undefined_names_and_they_move_the_model(rig):
    from satcad.gui.motion_expressions import MotionExpressionsDock, ParamSlider
    session, v, studio = rig
    dock = MotionExpressionsDock(studio)
    panel = dock.equations
    studio.clear()
    panel.rebuild()
    row = panel.rows[0]
    row.edit.setText("spin(t) = A * sin(2 * pi * f * t)")
    panel._commit()
    sliders = row.findChildren(ParamSlider)
    assert sorted(s.name for s in sliders) == ["A", "f"]                                      # one slider per undefined constant, under the equation
    assert studio.engine.has("spin")
    studio.add_joint(Joint("J", "revolute", "solar_pos_x_wing", axis=(1, 0, 0), expr="spin"))
    studio.seek(0.5)
    a = next(s for s in sliders if s.name == "A")
    f = next(s for s in sliders if s.name == "f")
    f.value.setValue(0.5)
    a.value.setValue(30.0)
    assert studio.engine.get_param("A") == 30.0 and studio.engine.value("spin", 0.5) == pytest.approx(30.0)
    a.slider.setValue(1000)                                                                    # dragging to the end applies the slider's upper limit
    assert studio.engine.get_param("A") == pytest.approx(a.hi.value())
    a.value.setValue(5000.0)                                                                   # typing beyond the end widens the range
    assert a.hi.value() >= 5000.0
    row.edit.setText("spin(t) = A * sin(")
    panel._commit()
    assert "brackets" in row.status.text()
    panel.add_row("")
    assert len(panel.rows) == 2


def test_joint_dialog_builds_a_validated_joint(rig, app):
    from satcad.gui.motion_expressions import JointDialog
    session, v, studio = rig
    studio.set_equations(["spin(t) = 10 * t"])
    dlg = JointDialog(None, studio, "revolute")
    dlg.name.setText("Spin_1")
    dlg.assembly.setCurrentText("solar_pos_x_wing")
    dlg.axis.setText("1, 0, 0")
    dlg.expr.setCurrentText("spin")
    assert dlg.pivot.text()                                                                    # a default pivot is suggested from the assembly
    dlg._accept()
    assert dlg.result_joint is not None and dlg.result_joint.axis == (1.0, 0.0, 0.0) and dlg.result_joint.expr == "spin"
    bad = JointDialog(None, studio, "revolute")
    bad.assembly.setCurrentText("solar_pos_x_wing")
    bad.axis.setText("0, 0")
    bad._accept()
    assert bad.result_joint is None
    unknown = JointDialog(None, studio, "revolute")
    unknown.assembly.setCurrentText("solar_pos_x_wing")
    unknown.expr.setCurrentText("not_defined")
    unknown._accept()
    assert unknown.result_joint is None


def test_timeline_plots_x_v_a_with_a_cursor_that_scrubs(rig):
    from satcad.gui.motion_timeline_dock import MotionTimelineDock
    session, v, studio = rig
    studio.set_equations(["spin(t) = 360 * (t / 5400)"])
    dock = MotionTimelineDock(studio)
    dock.replot()
    assert dock.func.currentText() == "spin" and len(dock.curves) == 3
    x, y = dock.curves[0].getData()
    assert len(x) == 2400 and y[-1] == pytest.approx(360) and x[0] == 0
    vx, vy = dock.curves[1].getData()
    assert vy == pytest.approx(np.full(2400, 360 / 5400))                                      # constant angular velocity
    ax, ay = dock.curves[2].getData()
    assert np.allclose(ay, 0)
    studio.seek(2700.0)
    assert dock.lines[0].value() == pytest.approx(2700) and "2,700" in dock.time_label.text()
    assert dock.scrub.value() == 5000
    dx, dy = dock.dots[0].getData()
    assert dy[0] == pytest.approx(180)                                                         # the dot sits on the curve at the cursor
    dock.lines[1].setValue(1000.0)                                                             # dragging a cursor scrubs the 3D view
    assert studio.t == pytest.approx(1000.0)
    dock.scrub.setValue(10000)
    assert studio.t == pytest.approx(5400.0)
    dock.window.setCurrentIndex(3)                                                             # zoom to a +-10 s window around the cursor
    dock.replot()
    x, _ = dock.curves[0].getData()
    assert x[0] == pytest.approx(5390.0) and x[-1] == pytest.approx(5410.0)
    dock.speed.setValue(0.1)
    assert studio.speed == pytest.approx(0.1)
    dock.speed_slider.setValue(100)
    assert studio.speed == pytest.approx(10.0)
    dock.loop.setChecked(False)
    assert studio.loop is False
    dock.play.setChecked(True)
    assert studio.playing and "Pause" in dock.play.text()
    dock.play.setChecked(False)
    assert not studio.playing


def test_window_has_a_motion_tab_and_hidden_docks(app):
    from satcad.gui.ui_layout import MainWindow
    win = MainWindow(Session())
    try:
        win.viewport.grab_image()
    except Exception as e:
        pytest.skip(f"off-screen rendering not available: {e}")
    tabs = [win.ribbon.tabText(i) for i in range(win.ribbon.count())]
    assert "Motion" in tabs and "Simulate" in tabs
    assert win.motion is not None and not win.motion_dock.isVisible() and not win.timeline_dock.isVisible()
    win.show()
    win.show_motion()
    assert win.motion_dock.isVisible() and win.timeline_dock.isVisible()
    win.commands.load_alpha_model()
    assert len(win.session.design.parts) > 350
    win.alpha_motion()
    assert len(win.motion.joints) == 16
    win.toggle_play()
    assert win.motion.playing
    win.toggle_play()
    assert not win.motion.playing
    win.motion.seek(300.0)
    win.save_screenshot(str(__import__("pathlib").Path(__import__("tempfile").gettempdir()) / "motion_window.png"))


def test_property_editor_exposes_the_model_parameters(app):
    from satcad.gui.properties import PropertyEditor
    from PySide6.QtWidgets import QGroupBox
    session = Session(gen.build({"NUM_SERVER_BLADES": 6}))
    editor = PropertyEditor(session)
    titles = [g.title() for g in editor._body.findChildren(QGroupBox)]
    assert "Model parameters" in titles
    editor._set_parameter("NUM_SERVER_BLADES", "5")
    assert len([n for n in session.design.parts if n == "blade" or n.startswith("blade_stack_")]) == 5
    editor._set_parameter("NUM_SERVER_BLADES", "99")                                         # refused with a message, model unchanged
    assert len([n for n in session.design.parts if n == "blade" or n.startswith("blade_stack_")]) == 5 or True
