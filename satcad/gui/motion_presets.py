"""Ready-made motion for the Orbital-Node-Alpha model (see generate_orbital_node_assembly.py): four mechanisms, written as plain
Desmos-style equations you can edit. Pivots and axes are read from the model's own geometry, so they follow it when it is resized.

    SADA_Revolute          solar arrays turn 360 degrees per orbit about X (the Sun-facing axis)
    Radiator_Fold_Hinge    radiator wings unfold 0 to 90 degrees along an S-curve (0 = stowed along -Z, 90 = deployed along Y)
    Turret_Gimbal          each of the four OISL turrets: azimuth (about Z) and elevation (about X) micro-tracking, plus a
                           spherical 2-axis fast steering mirror (FSM) whose jitter loop partly cancels the gimbal motion
    Coolant pulse          a travelling colour wave along the 24 segments of the hot and cold manifolds
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .kinematics_solver import ColorPulse, Joint

EQUATIONS = [
    "# Orbit period T_orbit, in seconds, is a slider",
    "theta_solar(t) = 360 * (t / T_orbit)",
    "fold(t) = 90 * smoothstep((t - t_start) / t_deploy)",
    "az_laser(t) = A_az * sin(2 * pi * f_az * t)",
    "el_laser(t) = A_el * sin(2 * pi * f_el * t + phi_el)",
    "fsm_tip(t) = -k_fsm * el_laser(t) + A_jit * sin(2 * pi * f_jit * t)",
    "fsm_tilt(t) = -k_fsm * az_laser(t) + A_jit * cos(2 * pi * f_jit * t)",
    "coolant(t, k) = 0.5 + 0.5 * sin(2 * pi * (f_flow * t - k / n_seg))",
]
DEFAULTS = {"T_orbit": 5400.0, "t_start": 60.0, "t_deploy": 600.0, "A_az": 5.0, "f_az": 0.5, "A_el": 3.0, "f_el": 0.35, "phi_el": 0.8,
            "k_fsm": 0.5, "A_jit": 0.4, "f_jit": 7.0, "f_flow": 0.4, "n_seg": 24.0}
RANGES = {"T_orbit": (600, 12000), "t_start": (0, 1200), "t_deploy": (30, 1800), "A_az": (0, 20), "f_az": (0, 5), "A_el": (0, 20), "f_el": (0, 5),
          "phi_el": (0, 6.3), "k_fsm": (0, 1.5), "A_jit": (0, 3), "f_jit": (0, 20), "f_flow": (0, 3), "n_seg": (1, 48)}
TURRETS = ("fwd_a", "fwd_b", "aft_a", "aft_b")


@dataclass
class MotionPreset:
    equations: list = field(default_factory=lambda: list(EQUATIONS))
    params: dict = field(default_factory=lambda: dict(DEFAULTS))
    ranges: dict = field(default_factory=lambda: dict(RANGES))
    joints: list = field(default_factory=list)
    pulses: list = field(default_factory=list)
    t_max: float = 5400.0
    missing: list = field(default_factory=list)          # parts / assemblies the model does not have (so those mechanisms were skipped)


def _centre(design, part: str):
    return tuple(design.describe(part)["centre_mm"])


def alpha_presets(design) -> MotionPreset:
    """Build the four preset mechanisms for a design made by generate_orbital_node_assembly.py (missing pieces are skipped and reported)."""
    p = MotionPreset()
    asm = {n for n, f in design.features.items() if f.kind == "assembly"}
    parts = set(design.parts)

    def need(*names) -> bool:
        gone = [n for n in names if n not in asm and n not in parts]
        p.missing.extend(gone)
        return not gone

    # 1. solar array drives: continuous rotation about X through the bus centre line
    for tag in ("pos_x", "neg_x"):
        if need(f"solar_{tag}_wing", f"sada_{tag}"):
            c = _centre(design, f"sada_{tag}")
            p.joints.append(Joint(f"SADA_Revolute_{tag}", "revolute", f"solar_{tag}_wing", axis=(1, 0, 0), pivot=(0.0, c[1], c[2]), expr="theta_solar"))
    # 2. radiator fold hinges: the model is drawn deployed, so the driven value 0..90 is shifted by -90 (0 = stowed along -Z)
    for tag, sign in (("pos_y", 1), ("neg_y", -1)):
        if need(f"radiator_{tag}_wing", f"root_manifold_{tag}"):
            c = _centre(design, f"root_manifold_{tag}")
            p.joints.append(Joint(f"Radiator_Fold_Hinge_{tag}", "revolute", f"radiator_{tag}_wing", axis=(sign, 0, 0), pivot=(0.0, c[1], c[2]),
                                  expr="fold", offset=-90.0, minimum=-90.0, maximum=0.0))
    # 3. the four optical turrets: azimuth stage, elevation stage and the fast steering mirror
    for tag in TURRETS:
        top = f"turret_{tag}"
        if not need(f"{top}_az", f"{top}_el", f"{top}_fsm", f"{top}_base", f"{top}_barrel", f"{top}_fsm_mirror"):
            continue
        base, barrel, mirror = _centre(design, f"{top}_base"), _centre(design, f"{top}_barrel"), _centre(design, f"{top}_fsm_mirror")
        p.joints.append(Joint(f"Turret_Gimbal_Az_{tag}", "revolute", f"{top}_az", axis=(0, 0, 1), pivot=(base[0], base[1], base[2]), expr="az_laser"))
        p.joints.append(Joint(f"Turret_Gimbal_El_{tag}", "revolute", f"{top}_el", axis=(1, 0, 0), pivot=barrel, expr="el_laser"))
        p.joints.append(Joint(f"FSM_Jitter_{tag}", "spherical", f"{top}_fsm", axis=(1, 0, 0), pivot=mirror, expr="fsm_tip", axis2=(0, 1, 0),
                              pivot2=mirror, expr2="fsm_tilt"))
    # 4. coolant pulse along the manifold segments
    for tag, hot in (("hot", (1.0, 0.42, 0.12)), ("cold", (0.15, 0.62, 1.0))):
        run = [f"{tag}_manifold"] + [f"{tag}_manifold_run_{i}" for i in range(2, 100) if f"{tag}_manifold_run_{i}" in parts]
        if f"{tag}_manifold" in parts:
            p.pulses.append(ColorPulse(f"Coolant_Pulse_{tag}", run, "coolant", base=(0.75, 0.75, 0.78), hot=hot))
        else:
            p.missing.append(f"{tag}_manifold")
    return p
