#!/usr/bin/env python3
"""
ALPHA-SIGMA: a 1:1 production-scale orbital AI datacentre for an 8 m heavy-lift fairing, built from scratch with the satCAD engine
(OpenCASCADE kernel through build123d; the app's interface is Qt).

    python3 "sattelite designs/alpha_sigma_production_node.py"                 # build, check, report, save the .json and Alpha_Sigma_v1.step next to this script
    python3 "sattelite designs/alpha_sigma_production_node.py" --no-save       # build and report only
    python3 "sattelite designs/alpha_sigma_production_node.py" --deployment-ratio 0   # also write the STOWED (launch) STEP: Alpha_Sigma_v1_stowed.step

This is a stand-alone design script, NOT built into the app. Open `alpha_sigma_production_node.json` with satCAD's Open button; the file carries its own
Motion Studio setup, a DEPLOYMENT_RATIO slider (0 = stowed for launch, 1 = deployed in orbit), the wiring / coolant / optical netlist and the layer presets.

COORDINATES (mm): origin at the centre of the bus. Z = flight / axial vector (hull +-4000), Y = deep-space radiator axis, X = solar axis.

WHAT IS IN IT
    AlphaSigma_Bus                 4500 x 4500 x 8000 hull (45 mm wall, hollow), 8 ring frames, 2600 mm launch adapter ring at Z = -4000
    AlphaSigma_Compute_Core        32 server trays in two banks of 16 (2800 x 2400 x 70, 185 mm pitch), 8 CPUs + 24 accelerators on each (1024 chips, 768 accelerators),
                                   32 grooved copper cold plates (2600 x 2200 x 18), a 400 mm avionics bay at Z = 0 with the photonic switch matrix, EDFAs and 2 BCDUs
    Schematic_HV_Wiring (orange)   8 Li-Ion modules, 4 solid-copper busbars (60 x 15), insulated branch cables (radius 8) routed to every tray, pump and turret
    Schematic_Coolant_Lining       4 pumps, 2 accumulators, 2 bypass valves, 45 mm hot / cold risers, 12 mm quick-disconnect feeders to all 32 plates, radiator rotary unions
    Schematic_Optical_Fiber (cyan) photonic matrix, 4 EDFAs, 15 mm armoured trunk with a branch to every tray, fibres to the turrets
    Foldable_Radiator_Wings        two wings, each 4 hinged panels of 5000 x 6800 x 16 (20 m deployed), ridged skins, 24 micro-tubes, toroidal bellows across every hinge
    Foldable_Solar_Arrays          two wings, each 6 hinged panels of 4000 x 3800 x 18 (24 m deployed), SADA (r = 220, 850 long), scissor linkages, copper hinge straps
    OISL_Turrets / Beacons         4 gimbal turrets (azimuth base r = 220, U-yoke, 250 mm Cassegrain barrel, 45-degree FSM) and 2 laser receiver beacon units (emerald dome,
                                   60 x 60 mm InGaAs quadrant detector, 8-diode coarse emitter ring)

FOLDING (Motion Studio, parameter DEPLOYMENT_RATIO 0..1): the radiators Z-fold 90 degrees about a root hinge and then accordion 180 degrees at each panel joint so the four panels
stack flat on the +-Y walls; the solar arrays pre-roll 90 degrees on their SADA, fold about a root hinge and accordion into six layers flat on the +-X walls. Stowed, the whole
satellite is inside a 5200 mm square envelope (checked numerically in the tests).

HONEST NOTES
  * The 45 mm "orthogrid" wall is modelled as a plain 45 mm wall (the rib pattern is not modelled).
  * The 850 mm perimeter corridor is measured from the OUTER skin, as in the brief: the clear corridor beside the trays is 805 mm in X and 1005 mm in Y. The 8 ring frames
    take 120 mm of it.
  * Compute power is an ASSUMPTION (750 W per tray, 24 kW in total); the brief gives sizes, not power. Pump heads, fluid properties and the 100 V bus are assumptions too
    (see the netlist assumptions in the file). Nothing here is flight-qualified.
  * The brief names both "2 beacon domes" and "a beacon beside each turret"; this model has 2 beacon units, one at each end of the bus, beside the turret pair.
  * Radiator ridges are on one face of each panel (the 120-degree, 35 mm trapezoids), so the stowed stack fits the 5200 mm envelope; the other face is flat.
  * The PythonOCC / gp_Trsf wording of the brief does not apply: the kernel is OpenCASCADE through build123d, and folding is done with Motion Studio joints.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from satcad import Design, DesignError  # noqa: E402

# ==========================================================================================================
#  PARAMETERS AND CONSTANTS (mm)
# ==========================================================================================================
PARAMETERS = dict(TRAY_PITCH_MM=185, FIN_RIDGE_ANGLE_DEG=120)
DEFAULT_DEPLOYMENT = 1.0

BUS = 4500
HULL_LENGTH = 8000
WALL = 45
HALF = BUS / 2                      # 2250
CAV = BUS - 2 * WALL                # 4410
CAV_Z = HULL_LENGTH - 2 * WALL      # 7910
RING_Z = (-3300, -2300, -1300, -300, 300, 1300, 2300, 3300)       # ring frames every 1000 mm (none at Z = 0, where the SADA passes)
RING_DEPTH, RING_THICK = 120, 60
ADAPTER = dict(od=2600, id=2440, height=150)                      # heavy payload launch adapter ring at Z = -4000 (outside the hull)

TRAYS_PER_BANK, TRAY = 16, (2800, 2400, 70)
BANK_CENTRE = 1700                  # banks centred at Z = -1700 (A) and +1700 (B): Z -3200..-200 and 200..3200 at the default pitch
BANK_DZ = 2 * BANK_CENTRE
PLATE = (2600, 2200, 18)
GROOVES, GROOVE_W, GROOVE_D, GROOVE_PITCH = 12, 14, 6, 200
ACCEL = (90, 90, 14)                # 24 per tray, 6 x 4
CPU = (80, 80, 12)                  # 8 per tray, 4 x 2

FIN_PITCH, FIN_HEIGHT, FIN_TOP, FIN_BASE_CAP = 140, 35, 10, 69
MICRO_R, MICRO_WALL, RIFLING = 5.0, 0.8, 8
RAD_PANELS, RAD_PANEL_LEN, RAD_HEIGHT, RAD_CORE = 4, 5000, 6800, 16
SOL_PANELS, SOL_PANEL_LEN, SOL_WIDTH, SOL_THICK = 6, 4000, 3800, 18

COLORS = {                           # RGB 0..255
    "hull": (200, 204, 210), "ring": (170, 176, 186), "tray": (22, 62, 34), "plate": (200, 120, 70), "chip": (34, 36, 44), "cpu": (60, 64, 76),
    "wire": (255, 95, 20), "wire_dark": (205, 70, 10), "hot": (235, 35, 35), "cold": (35, 130, 255), "fibre": (0, 230, 255), "fibre_dark": (0, 170, 200),
    "white": (245, 245, 250), "black": (35, 35, 40), "silver": (192, 196, 204), "solar": (15, 35, 80), "gold": (214, 170, 60), "emerald": (0, 255, 140), "copper": (200, 118, 60),
    "lens": (0, 200, 255), "battery": (60, 70, 90), "steel": (150, 156, 166),
}
FLOOR_Z = -(HULL_LENGTH / 2) + WALL
CEIL_Z = HULL_LENGTH / 2 - WALL


def rgb(name: str) -> list:
    return [round(v / 255.0, 4) for v in COLORS[name]]


class B:
    """Adds features to a Design with an assembly, layer and colour."""

    def __init__(self, d: Design):
        self.d = d

    def asm(self, name, parent=None):
        self.d.add("assembly", name, **({"parent": parent} if parent else {}))

    def add(self, kind, name, asm, color, layer=None, **kw):
        if color:
            kw["color"] = rgb(color)
        if asm:
            kw["assembly"] = asm
        if layer:
            kw["layer"] = layer
        self.d.add(kind, name, **kw)

    def row(self, name, src, asm, count, dx=0, dy=0, dz=0):
        self.d.add("pattern", name, source=src, mode="linear", count=count, dx=dx, dy=dy, dz=dz, assembly=asm)

    def grid(self, name, src, asm, steps):
        self.d.add("pattern", name, source=src, mode="grid", count=2, grid=steps, assembly=asm)


def ccw(points: list) -> list:
    """Order a profile counter-clockwise: the engine extrudes a clockwise outline the other way, so every profile is normalised here."""
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))
    return points if area > 0 else points[::-1]


def rifled_cavity(radius: float, ridge_h: float = 0.4, ridges: int = RIFLING, ridge_half_deg: float = 7.0, arc_steps: int = 2) -> list:
    """Outline [u, v] of the inside of a micro-tube: a circle with small ridges pointing inward (the rifling)."""
    pts = []
    for k in range(ridges):
        c = 360.0 / ridges * k
        for ang, r in ((c - ridge_half_deg, radius), (c, radius - ridge_h), (c + ridge_half_deg, radius)):
            a = math.radians(ang)
            pts.append([round(r * math.cos(a), 4), round(r * math.sin(a), 4)])
        span = 360.0 / ridges - 2 * ridge_half_deg
        for j in range(1, arc_steps + 1):
            a = math.radians(c + ridge_half_deg + span * j / (arc_steps + 1))
            pts.append([round(radius * math.cos(a), 4), round(radius * math.sin(a), 4)])
    return pts


# ==========================================================================================================
#  1. PRODUCTION BUS
# ==========================================================================================================
def bus(b: B) -> None:
    A = "AlphaSigma_Bus"
    b.asm(A)
    b.add("box", "AlphaSigma_Bus_Hull", A, "hull", "shell", length=BUS, width=BUS, height=HULL_LENGTH, material="aluminum_6061", role="chassis")
    b.add("box", "bus_cavity", None, None, length=CAV, width=CAV, height=CAV_Z)
    b.d.add("boolean", "bus_hollow", operation="subtract", target="AlphaSigma_Bus_Hull", tool="bus_cavity")
    # ports through the wall (u, v follow the face: +-X faces use (Y, Z), +-Y faces use (Z, X), +-Z faces use (X, Y))
    for sx in ("+x", "-x"):                                                      # SADA openings
        b.d.add("cutout", f"port_sada_{'pos' if sx[0] == '+' else 'neg'}_x", target="AlphaSigma_Bus_Hull", side=sx, shape="circle", diameter=2 * 220 + 20, depth=WALL, offset_u=0, offset_v=0)
    for sy, tag in (("+y", "pos_y"), ("-y", "neg_y")):                           # radiator line ports (hot, cold) near the root hinge
        for kind, z in (("hot", 3500), ("cold", -3500)):
            b.d.add("cutout", f"port_{tag}_{kind}", target="AlphaSigma_Bus_Hull", side=sy, shape="circle", diameter=80, depth=WALL, offset_u=z, offset_v=1900)
    for sz, tag in (("+z", "fwd"), ("-z", "aft")):                               # fibre and cable ports to the turrets and beacons
        for name, x, y in (("a", -1500, 1400), ("b", -1500, -1400), ("beacon", 1500, 0)):
            b.d.add("cutout", f"port_{tag}_{name}", target="AlphaSigma_Bus_Hull", side=sz, shape="circle", diameter=140 if name != "beacon" else 70, depth=WALL, offset_u=x, offset_v=y)
    # ring frames: four bars per ring (so a run can pass through the open middle), patterned along Z every 1000 mm
    z0 = RING_Z[0]
    half = len(RING_Z) // 2                                  # four frames per bank; the 600 mm gap between the banks is the avionics bay
    for tag, size, pos in (("px", (RING_DEPTH, CAV - 2 * RING_DEPTH, RING_THICK), (HALF - WALL - RING_DEPTH / 2, 0)), ("nx", (RING_DEPTH, CAV - 2 * RING_DEPTH, RING_THICK), (-(HALF - WALL - RING_DEPTH / 2), 0)),
                           ("py", (CAV, RING_DEPTH, RING_THICK), (0, HALF - WALL - RING_DEPTH / 2)), ("ny", (CAV, RING_DEPTH, RING_THICK), (0, -(HALF - WALL - RING_DEPTH / 2)))):
        name = f"ring_frame_{tag}"
        b.add("box", name, A, "ring", "shell", length=size[0], width=size[1], height=size[2], x=pos[0], y=pos[1], z=z0, material="aluminum_7075")
        b.grid(f"{name}_n", name, A, [[0, 0, 1000, half], [0, 0, RING_Z[half] - RING_Z[0], 2]])
    # heavy payload launch adapter ring (outside the hull)
    b.add("extrude", "launch_adapter_ring", A, "silver", "shell", profile="circle", radius=ADAPTER["od"] / 2, wall=(ADAPTER["od"] - ADAPTER["id"]) / 2, depth=ADAPTER["height"],
          plane="xy", z=-HULL_LENGTH / 2 - ADAPTER["height"], material="titanium_ti6al4v")


# ==========================================================================================================
#  2. 32-BLADE DUAL-BANK COMPUTE CORE
# ==========================================================================================================
def compute_core(b: B) -> None:
    A = "AlphaSigma_Compute_Core"
    b.asm(A)
    b.add("box", "server_tray", A, "tray", "shell", length=TRAY[0], width=TRAY[1], height=TRAY[2], z="=tray_z0", material="generic_equipment", role="payload")
    b.grid("server_tray_grid", "server_tray", A, [[0, 0, "=TRAY_PITCH_MM", TRAYS_PER_BANK], [0, 0, BANK_DZ, 2]])
    # 24 AI accelerators (6 x 4) and 8 host CPUs (4 x 2) on every tray
    b.add("box", "ai_accelerator", A, "chip", "shell", length=ACCEL[0], width=ACCEL[1], height=ACCEL[2], x=-1000, y=-675, z=f"=tray_z0 + {TRAY[2] / 2 + ACCEL[2] / 2}",
          material="generic_equipment", role="payload")
    b.grid("ai_accelerator_grid", "ai_accelerator", A, [[400, 0, 0, 6], [0, 450, 0, 4], [0, 0, "=TRAY_PITCH_MM", TRAYS_PER_BANK], [0, 0, BANK_DZ, 2]])
    b.add("box", "host_cpu", A, "cpu", "shell", length=CPU[0], width=CPU[1], height=CPU[2], x=-1050, y=-1000, z=f"=tray_z0 + {TRAY[2] / 2 + CPU[2] / 2}",
          material="generic_equipment", role="payload")
    b.grid("host_cpu_grid", "host_cpu", A, [[700, 0, 0, 4], [0, 2000, 0, 2], [0, 0, "=TRAY_PITCH_MM", TRAYS_PER_BANK], [0, 0, BANK_DZ, 2]])
    # dual-loop grooved copper cold plate: a profile in (Z, X) with 12 grooves cut into its top face, extruded along Y
    half_t, half_w = PLATE[2] / 2, PLATE[0] / 2
    pts = [[-half_t, -half_w]]
    pts.append([-half_t, half_w])
    pts.append([half_t, half_w])
    for k in range(GROOVES - 1, -1, -1):                                         # the top face from +X back to -X, notching a groove every 200 mm
        cx = (k - (GROOVES - 1) / 2) * GROOVE_PITCH
        pts += [[half_t, cx + GROOVE_W / 2], [half_t - GROOVE_D, cx + GROOVE_W / 2], [half_t - GROOVE_D, cx - GROOVE_W / 2], [half_t, cx - GROOVE_W / 2]]
    pts.append([half_t, -half_w])
    b.add("extrude", "cold_plate", A, "plate", "shell", profile="polygon", points=ccw(pts), depth=PLATE[1], plane="zx", x=0, z=f"=tray_z0 + {TRAY[2] / 2 + ACCEL[2] + PLATE[2] / 2}",
          y=-PLATE[1] / 2, material="copper", role="payload")
    b.grid("cold_plate_grid", "cold_plate", A, [[0, 0, "=TRAY_PITCH_MM", TRAYS_PER_BANK], [0, 0, BANK_DZ, 2]])
    # the 400 mm avionics bay at Z = 0: photonic switch matrix, EDFAs, two BCDUs
    bay = "AlphaSigma_Avionics_Bay"
    b.asm(bay)
    top_a = f"=tray_z0 + 15 * TRAY_PITCH_MM + {TRAY[2] / 2}"                                  # the top face of the highest tray of bank A
    deck_z = -150 - 10                                                                       # the deck: 20 mm thick, its top face at Z = -150
    for i, (x, y) in enumerate(((-1330, -1150), (1330, -1150), (-1330, 1150), (1330, 1150)), start=1):
        b.add("cylinder", f"avionics_post_{i}", bay, "ring", "shell", radius=40, height=f"=({deck_z - 10}) - ({top_a[1:]})", x=x, y=y, z=f"=(({deck_z - 10}) + ({top_a[1:]})) / 2",
              material="aluminum_7075", role="structure")
    b.add("box", "avionics_deck", bay, "ring", "shell", length=2800, width=2700, height=20, z=deck_z, material="aluminum_7075", role="structure")
    b.add("box", "photonic_switch_matrix", bay, "fibre_dark", "optical", length=1400, width=900, height=300, material="generic_equipment", role="schematic")
    for i, (x, y) in enumerate(((1000, 0), (-1000, 0), (1000, 450), (-1000, -450)), start=1):
        b.add("box", f"edfa_{i}", bay, "fibre", "optical", length=300, width=250, height=120, x=x, y=y, z=-90, material="generic_equipment", role="schematic")
    for i, y in ((1, 900), (2, -900)):
        b.add("box", f"bcdu_{i}", bay, "wire_dark", "power", length=1200, width=700, height=300, y=y, material="generic_equipment", role="schematic")


# ==========================================================================================================
#  3. SCHEMATIC BODIES: power, coolant, optical
# ==========================================================================================================
def hv_wiring(b: B) -> None:
    A = "Schematic_HV_Wiring"
    b.asm(A)
    for i, (x, y) in enumerate(((-1650, -700), (-550, -700), (550, -700), (1650, -700), (-1650, 700), (-550, 700), (550, 700), (1650, 700)), start=1):
        b.add("box", f"battery_{i}", A, "battery", "power", length=900, width=600, height=450, x=x, y=y, z=FLOOR_Z + 225, material="generic_equipment", role="schematic")
    for i, (x, y) in enumerate(((-1800, -800), (-1800, 800), (1800, -800), (1800, 800)), start=1):          # 60 x 15 mm solid copper busbars, vertical
        b.add("box", f"busbar_{i}", A, "wire", "power", length=15, width=60, height=7005, x=x, y=y, z=(-3505 + 3500) / 2, material="copper", role="schematic")


def coolant(b: B) -> None:
    A = "Schematic_Coolant_Lining"
    b.asm(A)
    R_RISER, W_RISER, R_FEED, W_FEED = 22.5, 2.5, 6.0, 1.0
    zl, zh = -3300, 3300
    for tag, sy in (("A", 1), ("B", -1)):
        yr = sy * 1700
        for kind, x, col in (("hot", 300, "hot"), ("cold", -300, "cold")):
            b.add("extrude", f"{kind}_riser_{tag}", A, col, "fluid", profile="circle", radius=R_RISER, wall=W_RISER, depth=zh - zl, plane="xy", x=x, y=yr, z=zl,
                  material="stainless_316", role="schematic")
            # 12 mm quick-disconnect feeders, one per cold plate: from the plate edge (|y| = 1100) to the riser, 32 of them in two banks
            start = 1100 if sy > 0 else -(1700 - R_RISER)
            depth = (1700 - R_RISER) - 1100
            name = f"{kind}_feeder_{tag}"
            b.add("extrude", name, A, col, "fluid", profile="circle", radius=R_FEED, wall=W_FEED, depth=depth, plane="zx", x=x, y=start,
                  z=f"=tray_z0 + {TRAY[2] / 2 + ACCEL[2] + PLATE[2] / 2}", material="stainless_316", role="schematic")
            b.grid(f"{name}_grid", name, A, [[0, 0, "=TRAY_PITCH_MM", TRAYS_PER_BANK], [0, 0, BANK_DZ, 2]])
    # four redundant pumps (r 150, 450 long, horizontal), two bellows accumulators, two three-way bypass mixing valves, in the -Z bay corridors
    for tag, sy in (("A", 1), ("B", -1)):
        for n, x in ((1, 900), (2, 1500)):
            b.add("cylinder", f"pump_{tag}{n}", A, "steel", "fluid", radius=150, height=450, axis="y", x=x, y=sy * 1750, z=FLOOR_Z + 155 + 150, material="generic_equipment", role="schematic")
            b.add("box", f"pump_saddle_{tag}{n}", A, "ring", "fluid", length=300, width=300, height=155, x=x, y=sy * 1750, z=FLOOR_Z + 77.5, material="aluminum_6061", role="structure")
        b.add("cylinder", f"accumulator_{tag}", A, "cold", "fluid", radius=250, height=650, axis="z", x=-1100, y=sy * 1750, z=FLOOR_Z + 325, material="generic_equipment", role="schematic")
        b.add("box", f"bypass_valve_{tag}", A, "steel", "fluid", length=300, width=300, height=300, x=300, y=sy * 1750, z=FLOOR_Z + 150, material="generic_equipment", role="schematic")


def optical(b: B) -> None:
    A = "Schematic_Optical_Fiber"
    b.asm(A)
    R = 7.5                                                                       # 15 mm armoured trunk
    # the trunk sits outside the busbars (x = -1800) and clear of the SADA (y = 0)
    b.add("cylinder", "fibre_trunk", A, "fibre", "optical", radius=R, height=6800, axis="z", x=TRUNK_X, y=TRUNK_Y, z=0, material="generic_equipment", role="schematic")
    b.add("cylinder", "fibre_branch", A, "fibre", "optical", radius=R, height=abs(TRUNK_X) - R - 1400, axis="x", x=(TRUNK_X + R - 1400) / 2, y=TRUNK_Y,
          z="=tray_z0 + 0", material="generic_equipment", role="schematic")
    b.grid("fibre_branch_grid", "fibre_branch", A, [[0, 0, "=TRAY_PITCH_MM", TRAYS_PER_BANK], [0, 0, BANK_DZ, 2]])


# ==========================================================================================================
#  4. OISL TURRETS AND BEACON UNITS
# ==========================================================================================================
TURRET_SITES = (("fwd_a", 1, 1400), ("fwd_b", 1, -1400), ("aft_a", -1, 1400), ("aft_b", -1, -1400))
TURRET_X = -1500
TRUNK_X, TRUNK_Y = -1950, -400


def turrets(b: B) -> None:
    b.asm("OISL_Turrets")
    for tag, s, y0 in TURRET_SITES:
        top = f"turret_{tag}"
        az, el, fsm = f"{top}_az", f"{top}_el", f"{top}_fsm"
        b.asm(top, "OISL_Turrets")
        b.asm(az, top)
        b.asm(el, az)
        b.asm(fsm, el)
        z_face = s * HULL_LENGTH / 2
        z_base_top = z_face + s * 150
        b.add("cylinder", f"{top}_base", top, "black", "external", radius=220, height=150, x=TURRET_X, y=y0, z=z_face + s * 75, material="aluminum_6061", role="antenna")
        u = [[0, -230], [0, 230], [s * 420, 230], [s * 420, 170], [s * 60, 170], [s * 60, -170], [s * 420, -170], [s * 420, -230]]
        b.add("extrude", f"{top}_yoke", az, "black", "external", profile="polygon", points=ccw([[z_base_top + p[0], TURRET_X + p[1]] for p in u]), depth=420, plane="zx", y=y0 - 210,
              material="aluminum_6061", role="antenna")
        z_c = z_base_top + s * 240
        b.add("extrude", f"{top}_barrel", el, "black", "external", profile="circle", radius=150, wall=25, depth=520, plane="zx", x=TURRET_X, z=z_c, y=y0 - 260,
              material="aluminum_6061", role="antenna")                                                # hollowed 250 mm aperture Cassegrain barrel
        b.add("cylinder", f"{top}_lens", el, "lens", "external", radius=125, height=8, axis="y", x=TURRET_X, y=y0 + 264, z=z_c, material="generic_equipment", role="antenna")
        b.add("cylinder", f"{top}_pin_a", el, "silver", "external", radius=35, height=20, axis="x", x=TURRET_X + 160, y=y0, z=z_c, material="aluminum_6061", role="antenna")
        b.d.add("mirror", f"{top}_pin_b", source=f"{top}_pin_a", plane="yz", offset=TURRET_X, assembly=el)
        b.add("cylinder", f"{top}_fsm_mirror", fsm, "silver", "external", radius=50, height=5, rx=45, x=TURRET_X, y=y0, z=z_c, material="aluminum_6061", role="antenna")
        b.add("cylinder", f"{top}_fsm_mount", fsm, "silver", "external", radius=10, height=70, axis="x", x=TURRET_X + 85, y=y0, z=z_c, material="aluminum_6061", role="antenna")


def beacons(b: B) -> None:
    for tag, s in (("fwd", 1), ("aft", -1)):
        A = f"Laser_Receiver_Beacon_Unit_{tag}"
        b.asm(A)
        zf = s * HULL_LENGTH / 2
        x0, y0 = 1500, 0
        b.add("box", f"beacon_bench_{tag}", A, "black", "external", length=350, width=250, height=180, x=x0, y=y0, z=zf + s * 90, material="aluminum_6061", role="antenna")
        zb = zf + s * 180                                                         # the top of the bench
        b.add("sphere", f"beacon_sphere_{tag}", None, None, radius=90, x=x0, y=y0, z=zb)
        b.add("sphere", f"beacon_inner_{tag}", None, None, radius=86, x=x0, y=y0, z=zb)
        b.d.add("boolean", f"beacon_shell_{tag}", operation="subtract", target=f"beacon_sphere_{tag}", tool=f"beacon_inner_{tag}")              # a 4 mm hollow dome
        b.add("box", f"beacon_cut_{tag}", None, None, length=300, width=300, height=100, x=x0, y=y0, z=zb - s * 50)
        b.d.add("boolean", f"beacon_dome_{tag}", operation="subtract", target=f"beacon_sphere_{tag}", tool=f"beacon_cut_{tag}")
        d = b.d
        d.edit(f"beacon_sphere_{tag}", color=rgb("emerald"), assembly=A, layer="external", material="generic_equipment", role="antenna")
        qz = zb + s * 1.5
        for i, (qx, qy) in enumerate(((-16, -16), (16, -16), (-16, 16), (16, 16)), start=1):      # the four quadrants of the 60 x 60 mm InGaAs wafer, with a 4 mm cross-hair gap
            b.add("box", f"quad_{tag}_{i}", A, "gold", "external", length=28, width=28, height=3, x=x0 + qx, y=y0 + qy, z=qz, material="generic_equipment", role="antenna")
        b.add("cylinder", f"beacon_emitter_{tag}", A, "lens", "external", radius=8, height=22, x=x0 + 115, y=y0, z=zb + s * 11, material="generic_equipment", role="antenna")
        b.d.add("pattern", f"beacon_emitter_ring_{tag}", source=f"beacon_emitter_{tag}", mode="circular", count=8, axis="z", cx=x0, cy=y0, cz=0, assembly=A)




# ==========================================================================================================
#  5. FOLDABLE RADIATOR WINGS (4-panel Z-fold) AND FOLDABLE SOLAR ARRAYS (6-panel accordion)
# ==========================================================================================================
GAP_SERVICE = 70                    # clear space between the hull wall and the stowed stack (the plumbing jumpers run in it)
X_W = 2500                          # the radiator wings hinge on an outrigger 250 mm beyond the bus corner, so a stowed 5000 mm panel is centred on the 4500 mm wall
Y_R = HALF + GAP_SERVICE + RAD_CORE / 2                                # root hinge axis of the +Y wing (|y|)
RAD_HDR = 22.5                      # header / jumper radius (45 mm pipe)
RAD_GAP = 34                        # gap between radiator panels at a hinge
RAD_PL = RAD_PANEL_LEN - RAD_GAP    # 4966
RAD_PIN = 12
RAD_PITCH = (96, 26, 96)            # stowed layer pitches (ridge-to-ridge, flat-to-flat, ridge-to-ridge)
RAD_A = (RAD_PITCH[0] / 2, -RAD_PITCH[1] / 2, RAD_PITCH[2] / 2)        # hinge axis offsets from the panel mid-plane (derived from the stack)
TUBE_PITCH = RAD_HEIGHT / 24
SOL_GAP, SOL_PL = 22, SOL_PANEL_LEN - 22
X_S = HALF + GAP_SERVICE + 20       # root hinge axis of the solar wings (|x|)
SOL_PIN = 9
SOL_P = SOL_THICK + 14              # stowed layer pitch
SOL_A = tuple((1 if k % 2 == 0 else -1) * SOL_P / 2 for k in range(SOL_PANELS - 1))


def hinge_unit(b: B, tag: str, asm_a: str, asm_b: str, xa: float, yh: float, zc: float, pin_r: float, half_gap: float, x_a: tuple, x_b: tuple, sy: int, L: float = 420, col: str = "steel") -> None:
    """One knuckle of a hinge: a pin along Z on the hinge axis with a tab from each panel. The two tabs sit in different Z ranges so they never meet when the panels fold."""
    b.add("cylinder", f"hinge_pin_{tag}", asm_a, col, "shell", radius=pin_r, height=L, x=xa, y=yh, z=zc, material="titanium_ti6al4v", role="structure")
    t = half_gap - pin_r
    near = abs(yh)
    for side, asm, xr, z0, suffix in (("a", asm_a, x_a, zc - L / 4, "a"), ("b", asm_b, x_b, zc + L / 4, "b")):
        lo, hi = (near - half_gap, near - pin_r) if side == "a" else (near + pin_r, near + half_gap)
        ys = sorted((sy * lo, sy * hi))
        b.add("box", f"hinge_tab_{tag}_{suffix}", asm, col, "shell", length=xr[1] - xr[0], width=ys[1] - ys[0], height=L / 2, x=(xr[0] + xr[1]) / 2, y=(ys[0] + ys[1]) / 2, z=z0,
              material="titanium_ti6al4v", role="structure")


def radiator_wing(b: B, tag: str, sy: int) -> None:
    top, wing = "Foldable_Radiator_Wings", f"rad_{tag}_wing"
    b.asm(wing, top)
    panels = [f"rad_{tag}_p{k}" for k in range(1, RAD_PANELS + 1)]
    b.asm(panels[0], wing)
    for k in range(1, RAD_PANELS):
        b.asm(panels[k], panels[k - 1])
    y_r = sy * Y_R
    # fixed side: outrigger, root pin, rotary unions, jumpers from the hull ports
    b.add("box", f"outrigger_{tag}", wing, "silver", "shell", length=X_W + RAD_CORE / 2 - 2000, width=Y_R - RAD_PIN - HALF, height=200, x=(2000 + X_W + RAD_CORE / 2) / 2,
          y=sy * (HALF + (Y_R - RAD_PIN - HALF) / 2), z=0, material="aluminum_7075", role="structure")
    b.add("cylinder", f"root_pin_{tag}", panels[0], "steel", "shell", radius=RAD_PIN, height=200, x=X_W, y=y_r, z=0, material="titanium_ti6al4v", role="structure")
    b.add("box", f"root_tab_{tag}", panels[0], "steel", "shell", length=RAD_CORE, width=RAD_PIN * 0 + (RAD_HDR - RAD_PIN), height=100, x=X_W, y=sy * (Y_R + (RAD_PIN + RAD_HDR) / 2), z=50,
          material="titanium_ti6al4v", role="structure")
    for kind, col, zu in (("hot", "hot", 3500), ("cold", "cold", -3500)):
        b.add("cylinder", f"rotary_union_{kind}_{tag}", wing, "steel", "fluid", radius=40, height=200, x=X_W, y=y_r, z=zu, material="stainless_316", role="schematic")
        b.add("sweep", f"jumper_{kind}_{tag}", wing, col, "fluid", profile="circle", radius=RAD_HDR, wall=2.5, path=[[1900, sy * 2100, zu], [1900, y_r, zu], [X_W - 40, y_r, zu]],
              bend_radius=100, material="stainless_316", role="schematic")
    # panels
    for k in range(1, RAD_PANELS + 1):
        asm = panels[k - 1]
        y_s = Y_R + RAD_HDR + (k - 1) * RAD_PANEL_LEN
        y_lo = y_s if sy > 0 else -(y_s + RAD_PL)
        y_c = sy * (y_s + RAD_PL / 2)
        for side, dx in (("a", -6.5), ("b", 6.5)):
            b.add("box", f"radiator_panel_{tag}_p{k}_{side}", asm, "white", "shell", length=3, width=RAD_PL, height=RAD_HEIGHT, x=X_W + dx, y=y_c, z=0, material="aluminum_honeycomb",
                  role="radiator")
        # the ridged skin: 48 trapezoids (120 degrees by default, 35 mm high) along the span on the front face
        bh = "=fin_base_half"
        b.add("extrude", f"fin_top_{tag}_p{k}", asm, "white", "shell", profile="polygon", points=[[f"=-fin_base_half", 8], [bh, 8], [FIN_TOP / 2, 8 + FIN_HEIGHT], [-FIN_TOP / 2, 8 + FIN_HEIGHT]],
              depth=RAD_PL, plane="zx", x=X_W, z=-(47 / 2) * FIN_PITCH, y=y_lo, material="aluminum_honeycomb", role="radiator")
        b.grid(f"fin_top_{tag}_p{k}_ridges", f"fin_top_{tag}_p{k}", asm, [[0, 0, FIN_PITCH, 48]])
        # 24 micro-tubes between the skins: 12 hot (upper half) and 12 cold (lower half), each with 8 internal micro-ridges
        for kind, col, z0 in (("hot", "hot", TUBE_PITCH / 2), ("cold", "cold", -(TUBE_PITCH / 2) - 11 * TUBE_PITCH)):
            name = f"micro_tube_{tag}_p{k}_{kind}"
            b.add("extrude", name, asm, col, "external", profile="circle", radius=MICRO_R, depth=RAD_PL, plane="zx", x=X_W, z=z0, y=y_lo, material="aluminum_6061", role="radiator")
            b.add("extrude", f"{name}_bore", None, None, profile="polygon", points=ccw(rifled_cavity(MICRO_R - MICRO_WALL)), depth=RAD_PL, plane="zx", x=X_W, z=z0, y=y_lo)
            b.d.add("boolean", f"{name}_rifle", operation="subtract", target=name, tool=f"{name}_bore")
            b.grid(f"{name}_row", name, asm, [[0, 0, TUBE_PITCH, 12]])
    # headers: hot (upper half) and cold (lower half) at the root, one long header at the tip joining them
    b.add("extrude", f"root_header_hot_{tag}", panels[0], "hot", "external", profile="circle", radius=RAD_HDR, wall=2.5, depth=3300, plane="xy", x=X_W, y=y_r, z=100, material="stainless_316",
          role="schematic")
    b.add("extrude", f"root_header_cold_{tag}", panels[0], "cold", "external", profile="circle", radius=RAD_HDR, wall=2.5, depth=3300, plane="xy", x=X_W, y=y_r, z=-3400, material="stainless_316",
          role="schematic")
    y_tip = Y_R + RAD_HDR + 3 * RAD_PANEL_LEN + RAD_PL + RAD_HDR
    b.add("extrude", f"tip_header_{tag}", panels[-1], "hot", "external", profile="circle", radius=RAD_HDR, wall=2.5, depth=RAD_HEIGHT, plane="xy", x=X_W, y=sy * y_tip, z=-3400,
          material="stainless_316", role="schematic")
    # hinges between panels: three knuckles per joint, toroidal bellows (r 22) bridging every micro-tube across the gap
    for k in range(1, RAD_PANELS):
        yh = sy * (Y_R + RAD_HDR + k * RAD_PANEL_LEN - RAD_GAP / 2)
        xa = X_W + RAD_A[k - 1]
        span_a, span_b = (min(X_W - 8, xa - RAD_PIN), max(X_W + 8, xa + RAD_PIN)), (min(X_W - 8, xa - RAD_PIN), max(X_W + 8, xa + RAD_PIN))
        for j, zc in enumerate((-8 * TUBE_PITCH, 0, 8 * TUBE_PITCH)):                  # between two bellows, where the hinge has room
            hinge_unit(b, f"rad_{tag}_h{k}_{j}", panels[k - 1], panels[k], xa, yh, zc, RAD_PIN, RAD_GAP / 2, span_a, span_b, sy, L=200)
        for kind, col, z0 in (("hot", "hot", TUBE_PITCH / 2), ("cold", "cold", -(TUBE_PITCH / 2) - 11 * TUBE_PITCH)):
            name = f"bellows_{tag}_h{k}_{kind}"
            b.add("revolve", name, panels[k], col, "external", points=bellows_profile(), angle=360, x=X_W, y=yh, z=z0, rx=90, material="stainless_316", role="schematic")
            b.grid(f"{name}_row", name, panels[k], [[0, 0, TUBE_PITCH, 12]])


def bellows_profile(half: float = RAD_GAP / 2, inner: float = 7.0, base: float = 14.0, peak: float = 22.0, convolutions: int = 3) -> list:
    """Half-section [radius, axial position] of a braided-stainless toroidal bellows: three convolutions of outer radius 22 mm across the hinge gap."""
    period = 2 * half / convolutions
    pts = [[inner, -half], [base, -half]]
    for i in range(convolutions):
        z0 = -half + i * period
        pts += [[base, z0], [peak, z0 + period * 0.28], [peak, z0 + period * 0.72], [base, z0 + period]]
    pts += [[base, half], [inner, half]]
    clean = [pts[0]]
    for q in pts[1:]:
        if q != clean[-1]:
            clean.append(q)
    return clean


def solar_wing(b: B, tag: str, sx: int) -> None:
    top, parent, wing = "Foldable_Solar_Arrays", f"solar_{tag}", f"solar_{tag}_wing"
    b.asm(parent, top)
    b.asm(wing, parent)
    panels = [f"solar_{tag}_p{k}" for k in range(1, SOL_PANELS + 1)]
    b.asm(panels[0], wing)
    for k in range(1, SOL_PANELS):
        b.asm(panels[k], panels[k - 1])
    # the SADA: r 220, 850 long, from the corridor (x = 1400) through the wall to the outer skin
    b.add("cylinder", f"sada_{tag}", parent, "gold", "shell", radius=220, height=850, axis="x", x=sx * (1400 + 425), y=0, z=0, material="titanium_ti6al4v", role="structure")
    xs = sx * X_S
    # yoke from the SADA flange to the root hinge, turning with the SADA
    x0, x1 = HALF, X_S - SOL_PIN
    b.add("box", f"sada_yoke_{tag}", wing, "silver", "shell", length=x1 - x0, width=80, height=3000, x=sx * (x0 + x1) / 2, y=0, z=0, material="aluminum_7075", role="structure")
    for k in range(1, SOL_PANELS + 1):
        asm = panels[k - 1]
        xa_lo = X_S + SOL_GAP / 2 + (k - 1) * SOL_PANEL_LEN
        b.add("box", f"solar_panel_{tag}_p{k}", asm, "solar", "shell", length=SOL_PL, width=SOL_THICK, height=SOL_WIDTH, x=sx * (xa_lo + SOL_PL / 2), y=0, z=0, material="aluminum_honeycomb",
              role="solar")
    # root hinge (knuckles at Z = -1200, 0, 1200): the pin belongs to the yoke, the 2 mm tab to panel 1
    for j, zc in enumerate((-1200, 0, 1200)):
        b.add("cylinder", f"root_pin_sol_{tag}_{j}", wing, "steel", "shell", radius=SOL_PIN, height=420, x=xs, y=0, z=zc, material="titanium_ti6al4v", role="structure")
        b.add("box", f"root_tab_sol_{tag}_{j}", panels[0], "steel", "shell", length=SOL_GAP / 2 - SOL_PIN, width=SOL_THICK, height=420, x=sx * (X_S + (SOL_PIN + SOL_GAP / 2) / 2), y=0, z=zc,
              material="titanium_ti6al4v", role="structure")
    for k in range(1, SOL_PANELS):
        xh = sx * (X_S + k * SOL_PANEL_LEN)                                      # the centre of the 22 mm gap between panels k and k+1
        ya = SOL_A[k - 1]
        for j, zc in enumerate((-1200, 0, 1200)):
            b.add("cylinder", f"hinge_pin_sol_{tag}_h{k}_{j}", panels[k - 1], "steel", "shell", radius=SOL_PIN, height=420, x=xh, y=ya, z=zc, material="titanium_ti6al4v", role="structure")
            y_lo, y_hi = min(-SOL_THICK / 2, ya - SOL_PIN), max(SOL_THICK / 2, ya + SOL_PIN)
            for suffix, asm, x_from, z0 in (("a", panels[k - 1], xh - sx * SOL_GAP / 2, zc - 105), ("b", panels[k], xh + sx * SOL_GAP / 2, zc + 105)):
                b.add("box", f"hinge_tab_sol_{tag}_h{k}_{j}{suffix}", asm, "steel", "shell", length=SOL_GAP / 2 - SOL_PIN, width=y_hi - y_lo, height=210,
                      x=x_from + sx * (SOL_GAP / 2 - SOL_PIN) / 2 * (1 if suffix == "a" else -1), y=(y_lo + y_hi) / 2, z=z0, material="titanium_ti6al4v", role="structure")
        # scissor linkage: two crossed bars on the back of panel k+1 at Z = +-600 (starting just across the hinge), copper straps on the cell face at Z = +-1800
        for zc in (-600, 600):
            for sign, off in ((1, -SOL_THICK / 2 - 3), (-1, -SOL_THICK / 2 - 9)):
                b.add("box", f"scissor_{tag}_h{k}_{'p' if zc > 0 else 'n'}_{'a' if sign > 0 else 'b'}", panels[k], "silver", "shell", length=1200, width=6, height=40, x=xh + sx * 570, y=off, z=zc,
                      ry=sign * 8.0, material="aluminum_7075", role="structure")
        for zc in (-1800, 1800):
            b.add("box", f"copper_strap_{tag}_h{k}_{'p' if zc > 0 else 'n'}", panels[k], "copper", "shell", length=SOL_GAP + 58, width=1.5, height=120, x=xh, y=SOL_THICK / 2 + 0.75, z=zc,
                  material="copper", role="structure")


# ==========================================================================================================
#  6. FOLDING KINEMATICS (the Motion Studio joints behind DEPLOYMENT_RATIO)
# ==========================================================================================================
# Stage windows (DEPLOYMENT_RATIO from, to): each fold completes in turn, so the wings unfurl one panel after another instead of all at once.
RAD_WINDOWS = {"root": (0.00, 0.40), 1: (0.30, 0.60), 2: (0.50, 0.80), 3: (0.70, 1.00)}
SOL_WINDOWS = {"root": (0.00, 0.30), **{k: (0.20 + 0.12 * (k - 1), 0.52 + 0.12 * (k - 1)) for k in range(1, SOL_PANELS)}}


def smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 3 * x * x - 2 * x * x * x


def folded(r: float, window: tuple) -> float:
    """1 when fully folded (DEPLOYMENT_RATIO at or below the window's start), 0 when fully unfolded (at or above its end)."""
    a, b = window
    return 1.0 - smoothstep((r - a) / (b - a))


def joint_specs() -> list[dict]:
    """Every folding joint: name, assembly, kind, axis, pivot and a rule for its angle: angle = sign * full * folded(DEPLOYMENT_RATIO, window) (+ the sun angle for a SADA)."""
    out = []
    for tag, sy in (("pos_y", 1), ("neg_y", -1)):
        out.append(dict(name=f"Rad_Root_{tag}", assembly=f"rad_{tag}_p1", axis=(0, 0, 1), pivot=(X_W, sy * Y_R, 0), sign=sy, full=90.0, window=RAD_WINDOWS["root"]))
        for k in range(1, RAD_PANELS):
            yh = sy * (Y_R + RAD_HDR + k * RAD_PANEL_LEN - RAD_GAP / 2)
            out.append(dict(name=f"Rad_Fold{k}_{tag}", assembly=f"rad_{tag}_p{k + 1}", axis=(0, 0, 1), pivot=(X_W + RAD_A[k - 1], yh, 0), sign=sy * (-1 if k % 2 else 1), full=180.0,
                            window=RAD_WINDOWS[k]))
    for tag, sx in (("pos_x", 1), ("neg_x", -1)):
        out.append(dict(name=f"SADA_Roll_{tag}", assembly=f"solar_{tag}_wing", axis=(1, 0, 0), pivot=(0, 0, 0), sign=1, full=-90.0, window=SOL_WINDOWS["root"], sun=True))
        out.append(dict(name=f"Sol_Root_{tag}", assembly=f"solar_{tag}_p1", axis=(0, 0, 1), pivot=(sx * X_S, 0, 0), sign=sx, full=-90.0, window=SOL_WINDOWS["root"]))
        for k in range(1, SOL_PANELS):
            out.append(dict(name=f"Sol_Fold{k}_{tag}", assembly=f"solar_{tag}_p{k + 1}", axis=(0, 0, 1), pivot=(sx * (X_S + k * SOL_PANEL_LEN), SOL_A[k - 1], 0),
                            sign=sx * (1 if k % 2 else -1), full=180.0, window=SOL_WINDOWS[k]))
    return out


def joint_angles(r: float, sun_deg: float = 0.0) -> dict[str, float]:
    """The angle (degrees) of every folding joint at DEPLOYMENT_RATIO r (0 = stowed for launch, 1 = deployed in orbit)."""
    out = {}
    for j in joint_specs():
        out[j["name"]] = j["sign"] * j["full"] * folded(r, j["window"]) + (sun_deg if j.get("sun") else 0.0)
    return out


def folding_joints():
    from satcad.gui.kinematics_solver import Joint
    return [Joint(j["name"], "revolute", j["assembly"], axis=j["axis"], pivot=tuple(float(v) for v in j["pivot"]), expr=j["name"].lower()) for j in joint_specs()]


def pose_matrices(design: Design, r: float, sun_deg: float = 0.0) -> dict:
    """World 4x4 matrix of every part that the folding moves, at DEPLOYMENT_RATIO r."""
    from satcad.gui.kinematics_solver import KinematicsSolver
    solver = KinematicsSolver(folding_joints(), design)
    values = {n: (a,) for n, a in joint_angles(r, sun_deg).items()}
    return solver.part_matrices(values)


def rigid(solid, M):
    """The solid moved by a rigid 4x4 matrix (rotation + translation). A true placement, so the shape stays exact and small (unlike a general geometric transform).
    Placed at the OpenCASCADE level: build123d's own `moved()` deep-copies the whole parent compound, which takes minutes for 2,400 parts."""
    from OCP.gp import gp_Trsf
    from OCP.TopLoc import TopLoc_Location
    t = gp_Trsf()
    t.SetValues(*(float(M[i][j]) for i in range(3) for j in range(4)))
    from build123d import Shape
    return Shape.cast(solid.wrapped.Moved(TopLoc_Location(t)))


def posed_boxes(design: Design, r: float, sun_deg: float = 0.0):
    """(names, lo, hi, moved) arrays: every part's axis-aligned box with the folding applied at DEPLOYMENT_RATIO r (exact for the 90 / 180 degree poses)."""
    import numpy as np
    mats = pose_matrices(design, r, sun_deg)
    names, lo, hi, moved = [], [], [], []
    for n, p in design.parts.items():
        a, b = (np.array(v, float) for v in p.bbox())
        if n in mats:
            corners = np.array([[x, y, z, 1.0] for x in (a[0], b[0]) for y in (a[1], b[1]) for z in (a[2], b[2])]) @ mats[n].T
            a, b = corners[:, :3].min(0), corners[:, :3].max(0)
        names.append(n)
        lo.append(a)
        hi.append(b)
        moved.append(n in mats)
    return names, np.array(lo), np.array(hi), np.array(moved)


def pose_report(design: Design, r: float, sun_deg: float = 0.0, tol: float = 0.05, exact: bool = True) -> dict:
    """Extent of the satellite at DEPLOYMENT_RATIO r and every pair of bodies that overlap (the moving bodies against everything), using exact solids for the candidates."""
    import numpy as np
    from satcad.gui.kinematics_solver import KinematicsSolver
    names, lo, hi, moved = posed_boxes(design, r, sun_deg)
    hull_like = {i for i, n in enumerate(names) if n == "AlphaSigma_Bus_Hull"}
    idx_moved = np.nonzero(moved)[0]
    pairs = []
    for i in idx_moved:
        ov = (np.minimum(hi[i], hi) - np.maximum(lo[i], lo) > tol).all(axis=1)
        for j in np.nonzero(ov)[0]:
            if j == i or j in hull_like or (moved[j] and j < i):
                continue
            pairs.append((names[i], names[j]))
    hits = []
    if exact and pairs:
        mats = pose_matrices(design, r, sun_deg)
        cache = {}

        def solid(n):
            if n not in cache:
                sol = design.parts[n].solid
                cache[n] = rigid(sol, mats[n]) if n in mats else sol
            return cache[n]
        for a, b in pairs:
            try:
                res = solid(a).intersect(solid(b))
                vol = float(res.volume) if res is not None and hasattr(res, "volume") else float(sum(getattr(q, "volume", 0.0) for q in res)) if res else 0.0
            except Exception:
                vol = 0.0
            if vol > 1.0:                                              # more than 1 mm3
                hits.append((a, b, round(vol / 1000.0, 3)))
    hull = design.parts["AlphaSigma_Bus_Hull"]
    hull_hits = []
    h_lo, h_hi = (np.array(v) for v in hull.bbox())
    for i in idx_moved:                                               # a moving body must stay outside the hull box (the SADA and wing roots are fixed to it, not moving)
        inside = (np.minimum(hi[i], h_hi - 1.0) - np.maximum(lo[i], h_lo + 1.0) > 1.0).all()
        if inside:
            hull_hits.append(names[i])
    return dict(ratio=r, lo=lo.min(axis=0).round(1).tolist(), hi=hi.max(axis=0).round(1).tolist(), candidates=len(pairs), overlaps=hits, hull_hits=hull_hits)


def fold_collisions(design: Design, ratios=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9), sun_deg: float = 0.0) -> list:
    """Mid-fold check: the radiator skins and solar panels (the big moving plates) tested against each other at each DEPLOYMENT_RATIO in `ratios`.
    Returns [(ratio, a, b, cm3)] for every pair whose solids overlap; an empty list means the folds never swing a panel through another one."""
    import numpy as np
    plates = [n for n in design.parts if n.startswith(("radiator_panel_", "solar_panel_"))]
    out = []
    for r in ratios:
        mats = pose_matrices(design, r, sun_deg)
        boxes, solids = {}, {}
        for n in plates:
            a, b = (np.array(v, float) for v in design.parts[n].bbox())
            if n in mats:
                c = np.array([[x, y, z, 1.0] for x in (a[0], b[0]) for y in (a[1], b[1]) for z in (a[2], b[2])]) @ mats[n].T
                a, b = c[:, :3].min(0), c[:, :3].max(0)
            boxes[n] = (a, b)
        for i, n1 in enumerate(plates):
            for n2 in plates[i + 1:]:
                (a1, b1), (a2, b2) = boxes[n1], boxes[n2]
                if not (np.minimum(b1, b2) - np.maximum(a1, a2) > 0.05).all():
                    continue
                for n in (n1, n2):
                    if n not in solids:
                        solids[n] = rigid(design.parts[n].solid, mats[n]) if n in mats else design.parts[n].solid
                try:
                    res = solids[n1].intersect(solids[n2])
                    vol = float(res.volume) if res is not None and hasattr(res, "volume") else float(sum(q.volume for q in res)) if res else 0.0
                except Exception:
                    vol = 0.0
                if vol > 10.0:
                    out.append((r, n1, n2, round(vol / 1000.0, 3)))
    return out


def motion_setup() -> dict:
    """The Motion Studio setup saved in the file: equations of DEPLOYMENT_RATIO (a slider), one joint per fold, and the parameters shown in the Property Editor."""
    eqs = ["# Alpha-Sigma folding. DEPLOYMENT_RATIO: 0 = stowed for launch (inside the 5200 mm envelope), 1 = deployed in orbit.",
           "# Each fold finishes in turn; the windows below are the DEPLOYMENT_RATIO ranges. SUN_ANGLE_DEG turns the solar arrays about their SADA axis."]
    for j in joint_specs():
        a, b = j["window"]
        fn = j["name"].lower()
        term = f"{j['sign'] * j['full']:g} * (1 - smoothstep((DEPLOYMENT_RATIO - {a:g}) / {b - a:g}))"
        eqs.append(f"{fn}(t) = {term}" + (" + SUN_ANGLE_DEG" if j.get("sun") else ""))
    return dict(engine=dict(equations=eqs, params=dict(DEPLOYMENT_RATIO=DEFAULT_DEPLOYMENT, SUN_ANGLE_DEG=0.0), ranges=dict(DEPLOYMENT_RATIO=[0.0, 1.0], SUN_ANGLE_DEG=[0.0, 360.0])),
                joints=[j.to_dict() for j in folding_joints()], pulses=[], t_max=60.0, expose=["DEPLOYMENT_RATIO", "SUN_ANGLE_DEG"])


# ==========================================================================================================
#  7. SYSTEMS: the netlist behind the schematics (what connects to what, and what each run is made of)
# ==========================================================================================================
TRAY_LOAD_W = 750            # ASSUMED electrical (and heat) load of one tray: 24 accelerators + 8 CPUs; 24 kW for the 32 trays
RADIATOR_AREA_M2 = 2 * RAD_PANELS * RAD_PL * RAD_HEIGHT / 1e6          # both faces of one wing, projected (ridges not counted: conservative)
BARS = {1: (-1800, -800), 2: (-1800, 800), 3: (1800, -800), 4: (1800, 800)}


def copy_name(src: str, pattern: str, n: int) -> str:
    """Name of copy n (1 to 32) of a grid pattern: copy 1 is the source part itself."""
    return src if n == 1 else f"{pattern}_{n}"


def tray_z(n: int) -> float:
    """Nominal Z of tray n (1 to 32) at the default pitch: bank A is trays 1 to 16, bank B trays 17 to 32."""
    bank, k = divmod(n - 1, TRAYS_PER_BANK)
    return -BANK_CENTRE + bank * BANK_DZ - 7.5 * PARAMETERS["TRAY_PITCH_MM"] + k * PARAMETERS["TRAY_PITCH_MM"]


def netlist(d: Design):
    """Build the Systems netlist: 100 V power (assumed), the optical backbone and the two redundant ammonia loops. Ports are anchored to parts of the model."""
    from satcad.systems import Systems
    S = Systems("Alpha-Sigma systems")
    S.assumptions.update(bus_voltage_v=100.0, tray_load_w=TRAY_LOAD_W, max_voltage_drop_pct=3.0,
                         note="Loads, pump heads, the 100 V bus and fluid properties are ASSUMED values for a concept study, not measured or flight data.")
    trays = range(1, 2 * TRAYS_PER_BANK + 1)
    bar_of = lambda n: ((n - 1) % 4) + 1                                        # noqa: E731  (each busbar feeds every fourth tray)
    # ---- power -----------------------------------------------------------------------------------------------------------------
    for i in (1, 2):
        S.component(f"bcdu_{i}", "power", "regulator", part=f"bcdu_{i}", label=f"BCDU {i}", cols={"power": 0}, source_w=20000, source_group="regulator",
                    ports={"OUT_L": {"at": "-x"}, "OUT_R": {"at": "+x"}})
    bat_pos = {1: (-1650, -700), 2: (-550, -700), 3: (550, -700), 4: (1650, -700), 5: (-1650, 700), 6: (-550, 700), 7: (550, 700), 8: (1650, 700)}
    for i, (x, y) in bat_pos.items():
        S.component(f"battery_{i}", "power", "battery", part=f"battery_{i}", label=f"Li-Ion module {i}", cols={"power": 0}, source_w=4000, source_group="battery", ports={"BUS": {"at": "+z"}})
    for i, (bx, by) in BARS.items():
        S.component(f"busbar_{i}", "power", "busbar", part=f"busbar_{i}", label=f"Busbar {i} 60 x 15 mm", cols={"power": 1})
        S.bus(f"busbar_{i}", "power", axis=2, bar_mm=[60, 15])
    inner = lambda i: (BARS[i][0] + (7.5 if BARS[i][0] < 0 else -7.5), BARS[i][1], 1 if BARS[i][0] < 0 else -1)      # noqa: E731  (x of the inner face, y, outward normal sign)

    def tap(i: int, name: str, z: float) -> None:
        x, y, sgn = inner(i)
        S.port(f"busbar_{i}", name, {"point": [x, y, z], "dir": [sgn, 0, 0]})
    cable = dict(awg="8", od_mm=16, color="orange", assembly="Schematic_HV_Wiring")
    for k, (bx, by) in bat_pos.items():
        i = (1 if by < 0 else 2) if bx < 0 else (3 if by < 0 else 4)
        tap(i, f"bat{k}", -3300 if k % 2 else -3400)
        S.link(f"cable_battery_{k}", "power", f"battery_{k}.BUS", f"busbar_{i}.bat{k}", auto=True, **{**cable, "awg": "4", "od_mm": 16}, bundle="Battery feeds")
    for bcdu in ("bcdu_1", "bcdu_2"):                                           # both outputs of a BCDU are one conductor inside it
        S.link(f"{bcdu}_internal", "power", f"{bcdu}.OUT_L", f"{bcdu}.OUT_R", kind="contact", contact=True)
    for j, (bcdu, side, i) in enumerate((("bcdu_1", "L", 2), ("bcdu_1", "R", 4), ("bcdu_2", "L", 1), ("bcdu_2", "R", 3))):
        tap(i, f"{bcdu}_{side}", 40 * (j - 1.5))
        S.link(f"cable_{bcdu}_{side}", "power", f"{bcdu}.OUT_{side}", f"busbar_{i}.{bcdu}_{side}", auto=True, **{**cable, "awg": "2"}, bundle="BCDU feeds")
    for n in trays:
        tray = copy_name("server_tray", "server_tray_grid", n)
        i = bar_of(n)
        face = "-x" if BARS[i][0] < 0 else "+x"
        S.component(f"tray_{n}", ("power", "optical", "fluid"), "server tray", part=tray, label=f"Server tray {n}", cols={"power": 2, "optical": 4, "fluid": 4}, load_w=TRAY_LOAD_W,
                    ports={"P": {"at": face}})
        tap(i, f"t{n}", tray_z(n))
        S.link(f"cable_tray_{n}", "power", f"busbar_{i}.t{n}", f"tray_{n}.P", auto=True, **cable, bundle=f"Tray power, busbar {i}")
    loads = [("pump_A1", 150, "pump", "+z", 4, -3150), ("pump_A2", 0, "pump", "+z", 4, -3050), ("pump_B1", 150, "pump", "+z", 3, -3150), ("pump_B2", 0, "pump", "+z", 3, -3050)]
    for name, watts, kind, at, i, z in loads:
        S.component(name, "power", kind, part=name, label=f"Pump {name[-2:]}" + (" (duty)" if watts else " (standby)"), cols={"power": 2}, load_w=watts, ports={"V": {"at": at}})
        tap(i, f"tap_{name}", z)
        S.link(f"cable_{name}", "power", f"busbar_{i}.tap_{name}", f"{name}.V", auto=True, **{**cable, "awg": "14", "od_mm": 10}, bundle="Power to pumps")
    for n, (tag, s, y0) in enumerate(TURRET_SITES, start=1):
        wall = s * (HULL_LENGTH / 2 - WALL)
        S.component(f"turret_{tag}", ("power", "optical"), "OISL turret", part=f"turret_{tag}_base", label=f"OISL turret {tag.replace('_', ' ')}", cols={"power": 2, "optical": 0}, load_w=120,
                    ports={"V": {"point": [TURRET_X - 35, y0, wall], "dir": [0, 0, -s]}, "FIBRE": {"point": [TURRET_X + 35, y0, wall], "dir": [0, 0, -s]}})
        i = 2 if y0 > 0 else 1
        tap(i, f"tap_turret_{tag}", 3400 if s > 0 else -3200)
        S.link(f"cable_turret_{tag}", "power", f"busbar_{i}.tap_turret_{tag}", f"turret_{tag}.V", auto=True, **{**cable, "awg": "12", "od_mm": 10}, bundle="Power to turrets")
    for name, y, comps in (("matrix", 0, None),):
        pass
    # ---- optical -------------------------------------------------------------------------------------------------------------
    S.component("photonic_switch_matrix", "optical", "core switch", part="photonic_switch_matrix", label="Photonic switch matrix", cols={"optical": 2}, ports={"TRUNK": {"at": "-x"}})
    for i in (1, 2, 3, 4):
        S.component(f"edfa_{i}", "optical", "edfa", part=f"edfa_{i}", label=f"EDFA {i}", cols={"optical": 1}, ports={"TO_MATRIX": {"at": "+x" if i in (2, 4) else "-x"}, "TO_TURRET": {"at": "+z"}})
        S.port("photonic_switch_matrix", f"E{i}", {"at": "+x" if i in (1, 3) else "-x", "offset": [0, 200 if i in (1, 2) else -200, 0]})
        S.link(f"fibre_edfa_matrix_{i}", "optical", f"edfa_{i}.TO_MATRIX", f"photonic_switch_matrix.E{i}", auto=True, fibre="SMF-28", conduit_od_mm=15, assembly="Schematic_Optical_Fiber")
    for n, (tag, s, y0) in enumerate(TURRET_SITES, start=1):
        S.link(f"fibre_turret_{tag}", "optical", f"edfa_{n}.TO_TURRET", f"turret_{tag}.FIBRE", auto=True, fibre="SMF-28", conduit_od_mm=15, assembly="Schematic_Optical_Fiber")
    S.component("optical_trunk", "optical", "fibre trunk", part="fibre_trunk", label="Armoured trunk 15 mm", cols={"optical": 3})
    S.bus("optical_trunk", "optical", axis=2, fibre="SMF-28", conduit_od_mm=15)
    S.port("optical_trunk", "matrix", {"part": "fibre_trunk", "at": "+x"})
    S.link("fibre_matrix_trunk", "optical", "photonic_switch_matrix.TRUNK", "optical_trunk.matrix", auto=True, fibre="SMF-28", conduit_od_mm=15, assembly="Schematic_Optical_Fiber")
    for n in trays:
        branch = copy_name("fibre_branch", "fibre_branch_grid", n)
        S.port("optical_trunk", f"t{n}", {"part": branch, "at": "-x"})
        S.port(f"tray_{n}", "OPT", {"part": branch, "at": "+x"})
        S.link(f"fibre_branch_{n}", "optical", f"optical_trunk.t{n}", f"tray_{n}.OPT", part=branch, fibre="SMF-28", conduit_od_mm=15)
    # ---- coolant -------------------------------------------------------------------------------------------------------------
    for tag, sy, wing in (("A", 1, "pos_y"), ("B", -1, "neg_y")):
        asm = "Schematic_Coolant_Lining"
        yr = sy * 1700
        S.component(f"cold_riser_{tag}", "fluid", "manifold", part=f"cold_riser_{tag}", label=f"Cold riser {tag}", cols={"fluid": 3}, od_mm=45, wall_mm=2.5, loop=tag,
                    ports={"bottom": {"at": "-z"}, "top": {"at": "+z", "spare": True}})
        S.component(f"hot_riser_{tag}", "fluid", "manifold", part=f"hot_riser_{tag}", label=f"Hot riser {tag}", cols={"fluid": 5}, od_mm=45, wall_mm=2.5, loop=tag, ports={"top": {"at": "+z"}})
        S.component(f"accumulator_{tag}", "fluid", "accumulator", part=f"accumulator_{tag}", label=f"Accumulator {tag}", cols={"fluid": 1}, loop=tag,
                    ports={"in": {"at": "+z"}, "out1": {"at": "+x", "offset": [0, 0, -50]}, "out2": {"at": "-y" if sy > 0 else "+y"}})
        S.component(f"bypass_valve_{tag}", "fluid", "valve", part=f"bypass_valve_{tag}", label=f"3-way bypass valve {tag}", cols={"fluid": 2}, loop=tag, od_mm=45, wall_mm=2.5, dp_kpa=10.0,
                    ports={"in1": {"at": "+y" if sy > 0 else "-y"}, "in2": {"at": "+x"}, "out": {"at": "+z"}, "bypass": {"at": "-x", "spare": True}})
        for n, port_in, port_out in ((1, "-x", "+z"), (2, "-x", "+z")):
            S.component(f"pump_{tag}{n}", "fluid", "pump", part=f"pump_{tag}{n}", merge=True, cols={"fluid": 2}, loop=tag,
                        ports={"in": {"at": "-y" if sy > 0 else "+y"}, "out": {"at": "+y" if sy > 0 else "-y"}})
        # the hull ports and the root rotary unions (fixed in space): the inner ends of the jumper pipes are where the routed lines arrive
        for kind, zu in (("hot", 3500), ("cold", -3500)):
            S.component(f"{kind}_port_{tag}", "fluid", "fitting", part=f"jumper_{kind}_{wing}", label=f"{kind.capitalize()} radiator port {tag}", cols={"fluid": 5 if kind == "hot" else 0}, loop=tag,
                        ports={"in": {"point": [1900, sy * 2100, zu], "dir": [0, -sy, 0]}, "out": {"point": [1900, sy * 2100, zu], "spare": True}})
        S.component(f"wing_{tag}", "fluid", "radiator wing", label=f"Radiator wing {'+Y' if sy > 0 else '-Y'}", cols={"fluid": 6}, loop=tag, area_m2=RADIATOR_AREA_M2,
                    ports={"hot_in": {"point": [X_W - 40, sy * Y_R, 3500]}, "cold_out": {"point": [X_W - 40, sy * Y_R, -3500]}})
        for n in trays:
            ch = copy_name(f"cold_feeder_{tag}", f"cold_feeder_{tag}_grid", n)
            hf = copy_name(f"hot_feeder_{tag}", f"hot_feeder_{tag}_grid", n)
            tray_end, riser_end = ("-y", "+y") if sy > 0 else ("+y", "-y")
            S.port(f"tray_{n}", f"{tag}_cold", {"part": ch, "at": tray_end})
            S.port(f"tray_{n}", f"{tag}_hot", {"part": hf, "at": tray_end})
            S.port(f"cold_riser_{tag}", f"tap{n}", {"part": ch, "at": riser_end})
            S.port(f"hot_riser_{tag}", f"tap{n}", {"part": hf, "at": riser_end})
            S.link(f"cold_feeder_{tag}_{n}", "fluid", f"cold_riser_{tag}.tap{n}", f"tray_{n}.{tag}_cold", part=ch, od_mm=12, wall_mm=1.0, role="cold", loop=tag)
            S.link(f"hot_feeder_{tag}_{n}", "fluid", f"tray_{n}.{tag}_hot", f"hot_riser_{tag}.tap{n}", part=hf, od_mm=12, wall_mm=1.0, role="hot", loop=tag)
        pipe = dict(od_mm=45, wall_mm=2.5, auto=True, assembly=asm, loop=tag)
        for kind, zu, col in (("hot", 3500, "hot"), ("cold", -3500, "cold")):
            path = [[1900, sy * 2100, zu], [1900, sy * Y_R, zu], [X_W - 40, sy * Y_R, zu]]
            S.link(f"jumper_{kind}_{tag}", "fluid", f"{kind}_port_{tag}.out", f"wing_{tag}.{'hot_in' if kind == 'hot' else 'cold_out'}", route=path, od_mm=45, wall_mm=2.5, role=kind, loop=tag,
                   part=f"jumper_{kind}_{wing}")
        S.link(f"valve_out_{tag}", "fluid", f"bypass_valve_{tag}.out", f"cold_riser_{tag}.bottom", role="cold", **pipe)       # routed first: the tightest ports of the pump bay
        for n in (1, 2):
            S.link(f"discharge_{tag}{n}", "fluid", f"pump_{tag}{n}.out", f"bypass_valve_{tag}.in{n}", role="cold", **pipe)
        for n in (1, 2):
            S.link(f"suction_{tag}{n}", "fluid", f"accumulator_{tag}.out{n}", f"pump_{tag}{n}.in", role="cold", **pipe)
        S.link(f"hot_line_{tag}", "fluid", f"hot_riser_{tag}.top", f"hot_port_{tag}.in", role="hot", **pipe)
        S.link(f"return_{tag}", "fluid", f"cold_port_{tag}.in", f"accumulator_{tag}.in", role="cold", **pipe)
        hot_tubes = [f"micro_tube_{wing}_p{k}_hot" for k in range(1, RAD_PANELS + 1)]
        cold_tubes = [f"micro_tube_{wing}_p{k}_cold" for k in range(1, RAD_PANELS + 1)]
        tube_len = RAD_PANELS * RAD_PL + (RAD_PANELS - 1) * RAD_GAP
        S.loop(f"Loop {tag}", fluid="ammonia", heat_w=2 * TRAYS_PER_BANK * TRAY_LOAD_W, delta_t_k=10.0, pump={"component": f"pump_{tag}1", "head_kpa": 250.0},
               radiator={"area_m2": RADIATOR_AREA_M2, "emissivity": 0.85, "fluid_temp_k": 293.0, "sink_temp_k": 200.0, "fin_drop_k": 5.0},
               stages=[dict(name="Pump discharge", elements=[f"discharge_{tag}1"]),
                       dict(name="Bypass mixing valve", elements=[f"bypass_valve_{tag}"]),
                       dict(name="Valve to cold riser", elements=[f"valve_out_{tag}"]),
                       dict(name="Cold riser", elements=[f"cold_riser_{tag}"]),
                       dict(name="Cold feeders (32 in parallel)", elements=[f"cold_feeder_{tag}_{n}" for n in trays], parallel=True),
                       dict(name="Cold plates", elements=[dict(id=f"cold_plates_{tag}", od_mm=12, wall_mm=0, length_mm=0, dp_kpa=20.0, parts=[f"tray_{n}" for n in trays])]),
                       dict(name="Hot feeders (32 in parallel)", elements=[f"hot_feeder_{tag}_{n}" for n in trays], parallel=True),
                       dict(name="Hot riser", elements=[f"hot_riser_{tag}"]),
                       dict(name="Hot line to radiator", elements=[f"hot_line_{tag}", f"jumper_hot_{tag}"]),
                       dict(name="Hot radiator tubes (12 in parallel, 4 panels)", elements=[dict(id=f"tubes_hot_{tag}", parts=[f"{hot_tubes[0]}_{i}" if i else hot_tubes[0] for i in range(12)],
                                                                                            od_mm=2 * MICRO_R, wall_mm=MICRO_WALL, length_mm=tube_len, k_extra=1.5)]),
                       dict(name="Tip header", elements=[dict(id=f"tip_{tag}", part=f"tip_header_{wing}", od_mm=45, wall_mm=2.5)]),
                       dict(name="Cold radiator tubes (12 in parallel, 4 panels)", elements=[dict(id=f"tubes_cold_{tag}", parts=[f"{cold_tubes[0]}_{i}" if i else cold_tubes[0] for i in range(12)],
                                                                                              od_mm=2 * MICRO_R, wall_mm=MICRO_WALL, length_mm=tube_len, k_extra=1.5)]),
                       dict(name="Cold line", elements=[f"jumper_cold_{tag}"]),
                       dict(name="Return to accumulator", elements=[f"return_{tag}"]),
                       dict(name="Pump suction", elements=[f"suction_{tag}1"])])
    S.sync(d)
    for n in trays:
        segs = sorted((l for l in S.links.values() if l.get("generated") == "optical_trunk"), key=lambda l: min(l["span"][0][2], l["span"][1][2]))
        tap_z, mat_z = S.port_pos(f"optical_trunk.t{n}")[2], S.port_pos("optical_trunk.matrix")[2]
        lo, hi = sorted((tap_z, mat_z))
        path = [l["id"] for l in segs if min(l["span"][0][2], l["span"][1][2]) >= lo - 1e-6 and max(l["span"][0][2], l["span"][1][2]) <= hi + 1e-6]
        S.chain(f"Photonic matrix to tray {n}", links=["fibre_matrix_trunk"] + path + [f"fibre_branch_{n}"], connectors=4, budget_db=6.0)
    return S


# ==========================================================================================================
#  The whole satellite
# ==========================================================================================================
def wings_step(b: B) -> None:
    b.asm("Foldable_Radiator_Wings")
    b.asm("Foldable_Solar_Arrays")
    for tag, sy in (("pos_y", 1), ("neg_y", -1)):
        radiator_wing(b, tag, sy)
    for tag, sx in (("pos_x", 1), ("neg_x", -1)):
        solar_wing(b, tag, sx)


def build(overrides: dict | None = None, name: str = "Alpha-Sigma", stage: str = "all", with_systems: bool = True) -> Design:
    p = {**PARAMETERS, **(overrides or {})}
    unknown = set(p) - set(PARAMETERS)
    if unknown:
        raise DesignError(f"Unknown parameter(s): {sorted(unknown)}. Choose from {list(PARAMETERS)}.")
    if not 120 <= p["TRAY_PITCH_MM"] <= 195:
        raise DesignError("TRAY_PITCH_MM must be between 120 and 195 (a bank of 16 trays must fit its 3000 mm space, and a cold plate needs room above each tray).")
    if not 40 <= p["FIN_RIDGE_ANGLE_DEG"] <= 170:
        raise DesignError("FIN_RIDGE_ANGLE_DEG must be between 40 and 170 degrees.")
    d = Design(name)
    with d.batch():
        for k, v in p.items():
            d.set_variable(k, v)
        d.set_variable("tray_z0", f"=-{BANK_CENTRE} - 7.5 * TRAY_PITCH_MM")
        d.set_variable("fin_base_half", f"=min({FIN_TOP / 2} + {FIN_HEIGHT} * tan(FIN_RIDGE_ANGLE_DEG / 2), {FIN_BASE_CAP})")
        b = B(d)
        steps = [bus, compute_core, hv_wiring, coolant, optical, turrets, beacons]
        if stage in ("all", "wings"):
            steps += [wings_step]
        for step in steps:
            step(b)
    if with_systems and stage == "all":                         # the netlist, the automatic cable and pipe routes, and their 3D tubes
        from satcad import routing
        routing.update(d, netlist(d))
    return d


def corridors() -> dict:
    """Clear space between the server trays and the inside of the hull."""
    return dict(x_clear_mm=(CAV - TRAY[0]) / 2, y_clear_mm=(CAV - TRAY[1]) / 2, x_from_outer_skin_mm=(BUS - TRAY[0]) / 2, y_from_outer_skin_mm=(BUS - TRAY[1]) / 2, nominal_mm=850)


def report(d: Design, check: bool = True) -> str:
    m = d.measure()
    c = corridors()
    chips = sum(1 for n in d.parts if n.startswith("ai_accelerator")), sum(1 for n in d.parts if n.startswith("host_cpu"))
    lines = [f"{d.name}: {m['parts']} bodies in {len(d.features)} features, total mass {m['total_mass_kg']:,.0f} kg",
             f"Deployed size {m['overall_size_mm'][0] / 1000:.2f} x {m['overall_size_mm'][1] / 1000:.2f} x {m['overall_size_mm'][2] / 1000:.2f} m",
             f"Compute: {sum(1 for n in d.parts if n.startswith('server_tray'))} trays, {chips[0]} AI accelerators, {chips[1]} host CPUs, {sum(1 for n in d.parts if n.startswith('cold_plate'))} cold plates",
             f"Corridors beside the trays: X {c['x_clear_mm']:.0f} mm clear ({c['x_from_outer_skin_mm']:.0f} mm from the outer skin), Y {c['y_clear_mm']:.0f} mm clear "
             f"({c['y_from_outer_skin_mm']:.0f} from the skin); target {c['nominal_mm']} mm"]
    rep = pose_report(d, 0.0, exact=False)
    lines.append(f"Stowed (DEPLOYMENT_RATIO 0): X {rep['lo'][0]:.0f} to {rep['hi'][0]:.0f} mm ({rep['hi'][0] - rep['lo'][0]:.0f} wide), Y {rep['lo'][1]:.0f} to {rep['hi'][1]:.0f} mm "
                 f"({rep['hi'][1] - rep['lo'][1]:.0f} wide), Z {rep['lo'][2]:.0f} to {rep['hi'][2]:.0f}: "
                 + ("fits the 5200 mm envelope" if max(rep['hi'][0] - rep['lo'][0], rep['hi'][1] - rep['lo'][1]) <= 5200 else "DOES NOT fit the 5200 mm envelope")
                 + f"; {rep['candidates']} overlapping boxes, {len(rep['hull_hits'])} bodies inside the hull")
    if check:
        folds = fold_collisions(d)
        lines.append("Mid-fold check (radiator and solar panels, DEPLOYMENT_RATIO 0.1 to 0.9): " + ("no panel passes through another" if not folds else f"{len(folds)} panel collisions"))
        chk = d.check()
        lines.append("Interference check: " + ("no overlapping bodies" if not chk["overlaps"] else f"{len(chk['overlaps'])} overlaps"))
        for o in chk["overlaps"][:8]:
            lines.append(f"  OVERLAP {o['a']} / {o['b']}: {o['overlap_cm3']:,.2f} cm3")
        if chk["floating_parts"]:
            lines.append(f"Floating parts ({len(chk['floating_parts'])}): " + ", ".join(f["part"] for f in chk["floating_parts"][:8]))
    return "\n".join(lines)


def stowed_compound(d: Design, r: float = 0.0):
    """A compound of every body posed at DEPLOYMENT_RATIO r (for the launch-configuration STEP)."""
    import numpy as np
    from build123d import Compound
    mats = pose_matrices(d, r)
    kids = []
    for n, p in d.parts.items():
        sol = rigid(p.solid, mats[n] if n in mats else np.eye(4))                  # always a fresh copy: solids already inside another compound make a second one very slow
        sol.label, sol.color = n, p.solid.color
        kids.append(sol)
    return Compound(children=kids, label=f"{d.name}_stowed")


def _parse(items) -> dict:
    out = {}
    for it in items or []:
        key, _, val = it.partition("=")
        try:
            out[key.strip().upper()] = float(val) if "." in val else int(val)
        except ValueError:
            raise SystemExit(f"--set {it}: the value must be a number")
    return out


def main(argv=None) -> int:
    from satcad.paths import in_designs
    ap = argparse.ArgumentParser(description="Build the Alpha-Sigma production satellite.")
    ap.add_argument("--set", action="append", metavar="NAME=VALUE", help="override TRAY_PITCH_MM or FIN_RIDGE_ANGLE_DEG")
    ap.add_argument("--name", default="alpha_sigma_production_node", help="file name (without extension) for the saved design")
    ap.add_argument("--no-save", action="store_true", help="do not write files")
    ap.add_argument("--no-systems", action="store_true", help="geometry only: skip the wiring / tubing / fibre netlist and its routes")
    ap.add_argument("--no-check", action="store_true", help="skip the 25 s interference check in the report")
    ap.add_argument("--diagrams", action="store_true", help="also write the wiring, cooling and optical diagrams, the 3D routing view and the cut lists")
    ap.add_argument("--deployment-ratio", type=float, metavar="R", help="also write a STEP of the satellite posed at DEPLOYMENT_RATIO R (0 = stowed for launch)")
    args = ap.parse_args(argv)
    t0 = time.time()
    try:
        d = build(_parse(args.set), with_systems=not args.no_systems)
    except DesignError as e:
        print(f"Can't build this design: {e}", file=sys.stderr)
        return 1
    print(report(d, check=not args.no_check))
    if d.systems:
        from satcad.systems import Systems
        S = Systems.of(d)
        print(S.report(d) if not args.no_check else "\n".join(S.report(d).splitlines()[:-1]))
    print(f"(built in {time.time() - t0:.1f} s)")
    if not args.no_save:
        js = in_designs(f"{args.name}.json")
        js.parent.mkdir(parents=True, exist_ok=True)
        data = d.to_json()
        data["motion"] = motion_setup()
        js.write_text(json.dumps(data, indent=2))
        print("Saved", js)
        from build123d import export_step
        step = js.parent / "Alpha_Sigma_v1.step"
        export_step(d.compound(), str(step))
        print("Exported", step, f"({step.stat().st_size / 1e6:.1f} MB)")
        if args.deployment_ratio is not None:
            r = min(max(args.deployment_ratio, 0.0), 1.0)
            out = js.parent / ("Alpha_Sigma_v1_stowed.step" if r == 0 else f"Alpha_Sigma_v1_ratio_{r:g}.step")
            export_step(stowed_compound(d, r), str(out))
            print("Exported", out, f"(posed at DEPLOYMENT_RATIO {r:g})")
        if args.diagrams and d.systems:
            from satcad import diagrams
            from satcad.systems import Systems
            paths = diagrams.write_all(Systems.of(d), d, js.parent / f"{args.name}_systems", include_check=False)
            print(f"Wrote {len(paths)} drawings and cut lists to", js.parent / f"{args.name}_systems")
    return 0


if __name__ == "__main__":
    sys.exit(main())
