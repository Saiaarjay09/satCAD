#!/usr/bin/env python3
"""
HEAVY-CLASS ORBITAL NODE: "Orbital-Node-Alpha" AI-datacentre satellite for a heavy-lift fairing, built from scratch with the satCAD engine
(OpenCASCADE kernel through build123d; the app's interface is Qt).

    python generate_heavy_orbital_node.py                      # build, check, print a report, and save into 'sattelite designs'
    python generate_heavy_orbital_node.py --set TRAY_PITCH_MM=250 --set FIN_RIDGE_ANGLE_DEG=110
    python generate_heavy_orbital_node.py --no-save            # build and report only
    python generate_heavy_orbital_node.py --diagrams           # also write the wiring / cooling / optical diagrams and the 3D routing view

This is a stand-alone design script. It is NOT built into the satCAD app: it writes `heavy_orbital_node.json` (open it with the app's Open button) and
`heavy_orbital_node.step` (for other CAD programs) into the 'sattelite designs' folder. In the app, View > X-Ray makes the hull, server trays and
radiator skins 85% transparent so the three internal schematics show through at full opacity; untick an assembly in the Part Navigator to hide a layer.

COORDINATES: origin at the centre of the bus. +Z/-Z velocity vector (fore/aft), +Y/-Y deep space (radiator wings), +X/-X Sun side.
All dimensions are millimetres.

PARAMETERS (model variables, editable in the app's Property Editor when nothing is selected)
    BUS_WIDTH_MM          hull width in X and Y (3500 minimum)
    TRAY_PITCH_MM         centre-to-centre spacing of the 16 server trays (100 to 280)
    FIN_RIDGE_ANGLE_DEG   opening angle of the radiator ridges (60 to 122; larger values are clamped to keep the ridge valid)

ASSEMBLIES
    Primary_Bus            3500 x 3500 x 5500 hull, 35 mm wall, hollow, with ports (Boolean subtract)
    Compute_Core           16 horizontal server trays 2200 x 1800 x 65
    HV_Power_Schematic     (orange)   4 battery banks, 2 solar regulators, 48 V busbars with a feeder to every tray
    Optical_Data_Schematic (cyan)     2 core switches, 4 EDFAs, radiation-shielded flight computer, 12 mm fibre conduits to the 4 turrets and 16 trays
    MPFL_Fluid_Schematic   (red/blue) two redundant loops: accumulator, 2 pumps, hot (red) and cold (blue) manifolds, 32 feeders each, radiator lines
    Radiator_Wings         two wings 16000 x 4200: ridged trapezoidal skins, 12 main tubes each with 8 internal micro-ridges, headers, truss boom
    OISL_Turrets           four external laser terminals

SYSTEMS (wiring, tubing, fibre): the file also carries a netlist (satcad.systems) saying what connects to what: 48 V busbar taps and cables, the optical backbone,
and two redundant ammonia loops with pump skids. Cables and the pump pipes are routed automatically around the equipment and drawn as swept tubes in the model;
the app's Systems tab draws the diagrams and checks the design (current density, voltage drop, flow, pump head, radiator, fibre bends and loss, clashes).
Loads, pump heads and fluid properties are ASSUMED concept values.

HONEST NOTES
  * The 650 mm perimeter corridor is measured from the OUTER skin: with the 35 mm wall the clear corridor beside the trays is 615 mm in X (815 mm in Y).
    Set BUS_WIDTH_MM = 3570 for a full 650 mm clear. The report prints the actual corridors.
  * The 35 mm "isogrid" wall is modelled as a solid 35 mm wall (the triangular rib pattern is not modelled).
  * "30 mm main manifold pipes" are modelled as 30 mm RADIUS (60 mm outside diameter); the 12 mm fibre conduits are 12 mm in diameter.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from satcad import Design, DesignError  # noqa: E402
from satcad.paths import in_designs  # noqa: E402

# ==========================================================================================================
#  PARAMETERS
# ==========================================================================================================
PARAMETERS = dict(BUS_WIDTH_MM=3500, TRAY_PITCH_MM=220, FIN_RIDGE_ANGLE_DEG=120)

HULL_LENGTH = 5500           # Z
WALL = 35
TRAYS = 16
TRAY = (2200, 1800, 65)
CORRIDOR_NOMINAL = 650
FIN_SPAN, FIN_WIDTH, FIN_PITCH, FIN_HEIGHT, FIN_TOP, FIN_BASE = 16000, 4200, 140, 35, 10, 3     # ridge geometry (mm)
TUBE_R, TUBE_INNER_R, RIFLING = 18, 16, 8
BOOM = (900, 300, 25, 1000)  # width X, height Z, wall, length Y
COLORS = {                   # RGB 0..255
    "hull": (200, 204, 210), "tray": (20, 60, 30), "power": (255, 140, 0), "power_dark": (210, 105, 0), "optical": (0, 200, 255), "optical_dark": (0, 150, 200),
    "red": (225, 50, 40), "blue": (40, 110, 235), "white": (245, 245, 250), "black": (35, 35, 40), "silver": (192, 196, 204), "lens": (0, 200, 255),
}
RETURN_STOP = 1650                             # where each cold return line ends inside the hull (|Y|, mm)
FLOOR_Z = -(HULL_LENGTH / 2) + WALL            # inner floor
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


def rifled_cavity(radius=TUBE_INNER_R, ridges=RIFLING, ridge_h=2.0, ridge_half_deg=6.0, arc_steps=3) -> list:
    """Outline [z, x] of the inside of a tube: a circle with `ridges` small ridges pointing inward (the rifling)."""
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


NOMINAL = {"=fin_base_half": 65.6, "=-fin_base_half": -65.6}


def ccw(points: list) -> list:
    """Order a profile counter-clockwise: the engine extrudes a clockwise outline the other way (toward -Y), so every profile is normalised here."""
    num = [[NOMINAL.get(c, c) if isinstance(c, str) else c for c in p] for p in points]
    area = sum(num[i][0] * num[(i + 1) % len(num)][1] - num[(i + 1) % len(num)][0] * num[i][1] for i in range(len(num)))
    return [list(p) for p in (points if area > 0 else points[::-1])]


# ==========================================================================================================
#  1. PRIMARY BUS
# ==========================================================================================================
def primary_bus(b: B) -> None:
    b.asm("Primary_Bus")
    b.add("box", "bus_hull", "Primary_Bus", "hull", "shell", length="=BUS_WIDTH_MM", width="=BUS_WIDTH_MM", height=HULL_LENGTH, material="aluminum_6061", role="chassis")
    b.add("box", "bus_cavity", None, None, length=f"=BUS_WIDTH_MM - {2 * WALL}", width=f"=BUS_WIDTH_MM - {2 * WALL}", height=HULL_LENGTH - 2 * WALL)
    b.d.add("boolean", "bus_hollow", operation="subtract", target="bus_hull", tool="bus_cavity")
    for side in ("+y", "-y"):                                      # ports for the hot and cold radiator lines
        for tag, x in (("hot", 300), ("cold", -300)):
            b.d.add("cutout", f"port_{'pos' if side[0] == '+' else 'neg'}_y_{tag}", target="bus_hull", side=side, shape="circle", diameter=100, depth=WALL, offset_u=0,
                    offset_v=x)
    for side in ("+z", "-z"):                                      # ports for the fibre conduits to the turrets
        for tag, y in (("a", 1300), ("b", -1300)):
            b.d.add("cutout", f"port_{'fwd' if side[0] == '+' else 'aft'}_{tag}", target="bus_hull", side=side, shape="circle", diameter=40, depth=WALL, offset_u=-1420,
                    offset_v=y)


# ==========================================================================================================
#  Compute core
# ==========================================================================================================
def compute_core(b: B) -> None:
    b.asm("Compute_Core")
    b.add("box", "server_tray", "Compute_Core", "tray", "shell", length=TRAY[0], width=TRAY[1], height=TRAY[2], z="=tray_z0", material="generic_equipment", role="payload")
    b.row("server_tray_stack", "server_tray", "Compute_Core", TRAYS, dz="=TRAY_PITCH_MM")


# ==========================================================================================================
#  2a. HV power schematic (orange)
# ==========================================================================================================
def hv_power(b: B) -> None:
    A = "HV_Power_Schematic"
    b.asm(A)
    L = "power"
    # 4 Li-Ion battery banks on the floor (two stacks of two), either side of a 10 mm gap that holds the main busbar
    b.add("box", "battery_bank_1", A, "power", L, length=500, width=800, height=300, x=1400, y=405, z=FLOOR_Z + 150, material="generic_equipment", role="schematic")
    b.d.add("mirror", "battery_bank_2", source="battery_bank_1", plane="zx", assembly=A)
    b.add("box", "battery_bank_3", A, "power", L, length=500, width=800, height=300, x=1400, y=405, z=FLOOR_Z + 450, material="generic_equipment", role="schematic")
    b.d.add("mirror", "battery_bank_4", source="battery_bank_3", plane="zx", assembly=A)
    b.add("box", "solar_regulator_1", A, "power_dark", L, length=400, width=300, height=250, x=1450, y=1300, z=FLOOR_Z + 125, material="generic_equipment", role="schematic")
    b.d.add("mirror", "solar_regulator_2", source="solar_regulator_1", plane="zx", assembly=A)
    # 48 V DC busbars, 40 x 10 mm: one vertical main bar in the 10 mm gap between the banks, and a flat feeder bar to every tray
    b.add("box", "busbar_main", A, "power_dark", L, length=40, width=10, height="=bus_top - bus_bottom", x=1300, y=0, z="=(bus_top + bus_bottom) / 2",
          material="copper", role="schematic")
    b.add("box", "busbar_feeder", A, "power", L, length=180, width=40, height=10, x=1190, y=0, z="=tray_z0", material="copper", role="schematic")
    b.row("busbar_feeder_run", "busbar_feeder", A, TRAYS, dz="=TRAY_PITCH_MM")


# ==========================================================================================================
#  2b. Optical data backplane (cyan)
# ==========================================================================================================
def optical(b: B) -> None:
    A = "Optical_Data_Schematic"
    b.asm(A)
    L = "optical"
    b.add("box", "optical_switch_top", A, "optical", L, length=480, width=700, height=220, x=-1420, y=0, z=2300, material="generic_equipment", role="schematic")
    b.d.add("mirror", "optical_switch_bottom", source="optical_switch_top", plane="xy", assembly=A)
    b.add("box", "edfa_1", A, "optical_dark", L, length=300, width=250, height=120, x=-1420, y=1300, z=2250, material="generic_equipment", role="schematic")
    b.d.add("mirror", "edfa_2", source="edfa_1", plane="zx", assembly=A)
    b.d.add("mirror", "edfa_3", source="edfa_1", plane="xy", assembly=A)
    b.d.add("mirror", "edfa_4", source="edfa_2", plane="xy", assembly=A)
    # radiation-shielded flight computer: a core inside a 40 mm titanium shield
    b.add("box", "flight_shield", A, "optical_dark", L, length=520, width=520, height=420, x=-1420, y=1000, z=0, material="titanium_ti6al4v", role="schematic")
    b.add("box", "flight_shield_cavity", None, None, length=440, width=440, height=340, x=-1420, y=1000, z=0)
    b.d.add("boolean", "flight_shield_hollow", operation="subtract", target="flight_shield", tool="flight_shield_cavity")
    b.add("box", "flight_computer", A, "optical", L, length=440, width=440, height=340, x=-1420, y=1000, z=0, material="generic_equipment", role="schematic")
    # 12 mm fibre conduits (6 mm radius)
    R = 6
    b.add("cylinder", "fibre_trunk", A, "optical", L, radius=R, height=4380, axis="z", x=-1250, y=0, z=0, material="generic_equipment", role="schematic")
    b.add("cylinder", "fibre_branch", A, "optical", L, radius=R, height=144, axis="x", x=-1172, y=0, z="=tray_z0", material="generic_equipment", role="schematic")
    b.row("fibre_branch_run", "fibre_branch", A, TRAYS, dz="=TRAY_PITCH_MM")
    b.add("cylinder", "fibre_computer_link", A, "optical", L, radius=R, height=734, axis="y", x=-1250, y=373, z=0, material="generic_equipment", role="schematic")
    b.add("cylinder", "fibre_edfa_switch_1", A, "optical", L, radius=R, height=825, axis="y", x=-1420, y=762.5, z=2250, material="generic_equipment", role="schematic")
    b.d.add("mirror", "fibre_edfa_switch_2", source="fibre_edfa_switch_1", plane="zx", assembly=A)
    b.d.add("mirror", "fibre_edfa_switch_3", source="fibre_edfa_switch_1", plane="xy", assembly=A)
    b.d.add("mirror", "fibre_edfa_switch_4", source="fibre_edfa_switch_2", plane="xy", assembly=A)
    b.add("cylinder", "fibre_turret_1", A, "optical", L, radius=R, height=440, axis="z", x=-1420, y=1300, z=2530, material="generic_equipment", role="schematic")
    b.d.add("mirror", "fibre_turret_2", source="fibre_turret_1", plane="zx", assembly=A)
    b.d.add("mirror", "fibre_turret_3", source="fibre_turret_1", plane="xy", assembly=A)
    b.d.add("mirror", "fibre_turret_4", source="fibre_turret_2", plane="xy", assembly=A)


# ==========================================================================================================
#  2c. Closed-loop coolant circuit (red / blue), two redundant loops
# ==========================================================================================================
def fluid(b: B) -> None:
    A = "MPFL_Fluid_Schematic"
    b.asm(A)
    L = "fluid"
    for tag, s in (("A", 1), ("B", -1)):                        # loop A on the +Y side (feeds the +Y wing), loop B on the -Y side
        y0 = s * 1300
        b.add("extrude", f"hot_manifold_{tag}", A, "red", L, profile="circle", radius=30, wall=3, depth="=manifold_len", plane="xy", x=300, y=y0, z="=manifold_z0",
              material="stainless_316", role="schematic")
        b.add("extrude", f"cold_manifold_{tag}", A, "blue", L, profile="circle", radius=30, wall=3, depth="=manifold_len", plane="xy", x=-300, y=y0, z="=manifold_z0",
              material="stainless_316", role="schematic")
        start = 900 if s > 0 else -1270
        b.add("extrude", f"hot_feeder_{tag}", A, "red", L, profile="circle", radius=10, wall=1.5, depth=370, plane="zx", x=300, y=start, z="=tray_z0", material="stainless_316",
              role="schematic")
        b.row(f"hot_feeder_{tag}_run", f"hot_feeder_{tag}", A, TRAYS, dz="=TRAY_PITCH_MM")
        b.add("extrude", f"cold_feeder_{tag}", A, "blue", L, profile="circle", radius=10, wall=1.5, depth=370, plane="zx", x=-300, y=start, z="=tray_z0", material="stainless_316",
              role="schematic")
        b.row(f"cold_feeder_{tag}_run", f"cold_feeder_{tag}", A, TRAYS, dz="=TRAY_PITCH_MM")
        for n, x in ((1, 650), (2, 950)):                       # centrifugal pumps on saddles
            b.add("cylinder", f"pump_{tag}{n}", A, "blue", L, radius=150, height=400, axis="z", x=x, y=y0, z=FLOOR_Z + 115 + 200, material="generic_equipment", role="schematic")
            b.add("box", f"pump_saddle_{tag}{n}", A, "silver", L, length=200, width=300, height=115, x=x, y=y0, z=FLOOR_Z + 57.5, material="aluminum_6061", role="schematic")
        b.add("cylinder", f"accumulator_{tag}", A, "blue", L, radius=300, height=800, axis="z", x=-800, y=y0, z=FLOOR_Z + 400, material="generic_equipment", role="schematic")
        for kind, x, col in (("hot", 300, "red"), ("cold", -300, "blue")):                 # lines through the bus wall and boom to the wing root
            near = 1330 if kind == "hot" else RETURN_STOP                                       # the cold return stops at the pump skid, not at the manifold
            b.add("extrude", f"{kind}_line_{tag}", A, col, L, profile="circle", radius=30, wall=2, depth=f"=wing_root_y - 30 - {near}", plane="zx", x=x, z=0,
                  y=near if s > 0 else "=-(wing_root_y - 30)", material="stainless_316", role="schematic")


# ==========================================================================================================
#  3. Radiator wings with ridged skins and rifled main tubes
# ==========================================================================================================
def radiators(b: B) -> None:
    b.asm("Radiator_Wings")
    cell_top = [[18, -FIN_PITCH / 2], [18, FIN_PITCH / 2], [18 + FIN_BASE, FIN_PITCH / 2], [18 + FIN_BASE, "=fin_base_half"], [18 + FIN_BASE + FIN_HEIGHT, FIN_TOP / 2],
                [18 + FIN_BASE + FIN_HEIGHT, -FIN_TOP / 2], [18 + FIN_BASE, "=-fin_base_half"], [18 + FIN_BASE, -FIN_PITCH / 2]]
    cell_bottom = [[-z if isinstance(z, (int, float)) else z, x] for z, x in cell_top]
    for tag, s in (("pos_y", 1), ("neg_y", -1)):
        W, fixed = f"Radiator_Wing_{'PosY' if s > 0 else 'NegY'}", None
        b.asm(W, "Radiator_Wings")
        b.add("extrude", f"boom_{tag}", W, "silver", None, profile="rect", width=BOOM[1], height=BOOM[0], wall=BOOM[2], depth=BOOM[3], plane="zx", y="=hull_half" if s > 0 else "=-(hull_half + 1000)",
              material="aluminum_6061", role="structure")
        # ridged skins: one trapezoid unit cell, repeated across the 4200 mm width (so the ridge angle is a live formula)
        y_start = "=wing_root_y + 40" if s > 0 else "=-(wing_root_y + 40 + 16000)"
        for face, cell in (("top", cell_top), ("bottom", cell_bottom)):
            name = f"fin_{face}_{tag}"
            b.add("extrude", name, W, "white", "shell", profile="polygon", points=ccw(cell), depth=FIN_SPAN, plane="zx", x=-FIN_WIDTH / 2 + FIN_PITCH / 2, y=y_start,
                  material="aluminum_honeycomb", role="radiator")
            b.row(f"{name}_ridges", name, W, FIN_WIDTH // FIN_PITCH, dx=FIN_PITCH)
        # headers
        root_y = "=wing_root_y" if s > 0 else "=-wing_root_y"
        b.add("extrude", f"root_header_hot_{tag}", W, "red", "fluid", profile="circle", radius=30, wall=2, depth=2100, plane="yz", x=0, y=root_y, z=0, material="stainless_316", role="schematic")
        b.add("extrude", f"root_header_cold_{tag}", W, "blue", "fluid", profile="circle", radius=30, wall=2, depth=2100, plane="yz", x=-2100, y=root_y, z=0, material="stainless_316",
              role="schematic")
        tip_y = "=wing_root_y + 40 + 16000 + 40" if s > 0 else "=-(wing_root_y + 40 + 16000 + 40)"
        b.add("extrude", f"tip_header_{tag}", W, "red", "fluid", profile="circle", radius=30, wall=2, depth=4200, plane="yz", x=-2100, y=tip_y, z=0, material="stainless_316", role="schematic")
        # main tubes: radius 18 with 8 internal micro-ridges (outer tube minus a rifled cavity)
        tube_y = "=wing_root_y + 30" if s > 0 else "=-(wing_root_y + 30 + 16020)"
        for col, name, x0, dx in (("red", f"tube_hot_{tag}", 175, 350), ("blue", f"tube_cold_{tag}", -175, -350)):
            b.add("extrude", name, W, col, "fluid", profile="circle", radius=TUBE_R, depth=16020, plane="zx", x=x0, z=0, y=tube_y, material="aluminum_6061", role="radiator")
            b.add("extrude", f"{name}_bore", None, None, None, profile="polygon", points=rifled_cavity(), depth=16020, plane="zx", x=x0, y=tube_y)
            b.d.add("boolean", f"{name}_rifle", operation="subtract", target=name, tool=f"{name}_bore")
            b.row(f"{name}_row", name, W, 6, dx=dx)


# ==========================================================================================================
#  4. Four external OISL turrets
# ==========================================================================================================
def turrets(b: B) -> None:
    b.asm("OISL_Turrets")
    for tag, s, y in (("fwd_a", 1, 1300), ("fwd_b", 1, -1300), ("aft_a", -1, 1300), ("aft_b", -1, -1300)):
        zf = s * HULL_LENGTH / 2
        b.add("cylinder", f"turret_{tag}_base", "OISL_Turrets", "black", "optical", radius=180, height=120, x=-1420, y=y, z=zf + s * 60, material="aluminum_6061", role="antenna")
        zc = zf + s * (120 + 110)
        b.add("extrude", f"turret_{tag}_barrel", "OISL_Turrets", "black", "optical", profile="circle", radius=110, wall=15, depth=350, plane="zx", x=-1420, z=zc, y=y - 175,
              material="aluminum_6061", role="antenna")
        b.add("cylinder", f"turret_{tag}_lens", "OISL_Turrets", "lens", "optical", radius=95, height=6, axis="y", x=-1420, y=y + 163, z=zc, material="generic_equipment", role="antenna")


# ==========================================================================================================
#  SYSTEMS: the netlist behind the schematics (what connects to what, and what each run is made of)
# ==========================================================================================================
TRAY_LOAD_W = 1200            # assumed electrical load of one server tray; all of it ends up as heat in its cold plate
RADIATOR_AREA_M2 = 2 * FIN_SPAN * FIN_WIDTH / 1e6        # both faces of one wing, projected area (ridges not counted: conservative)


def _copy(src: str, pattern: str, k: int) -> str:
    """Name of copy k (1 to 16) of a linear pattern: copy 1 is the source part itself."""
    return src if k == 1 else f"{pattern}_{k}"


def netlist(d: Design):
    """Build the Systems netlist for the node: 48 V power, the optical backbone and the two coolant loops. Ports are anchored to parts of the model."""
    from satcad.systems import Systems
    S = Systems("Orbital-Node-Alpha (heavy) systems")
    S.assumptions.update(tray_load_w=TRAY_LOAD_W, note="Loads, pump heads and fluid properties are ASSUMED values for a concept study, not measured or flight data.")
    trays = range(1, TRAYS + 1)
    # ---- power ---------------------------------------------------------------------------------------------------------------
    for i in (1, 2):
        S.component(f"solar_regulator_{i}", "power", "regulator", part=f"solar_regulator_{i}", label=f"Solar regulator {i}", cols={"power": 0}, source_w=12000,
                    source_group="regulator", ports={"OUT": {"at": "+z"}})
    for i in (1, 2, 3, 4):
        S.component(f"battery_bank_{i}", "power", "battery", part=f"battery_bank_{i}", label=f"Li-Ion bank {i}", cols={"power": 0}, source_w=6000, source_group="battery",
                    ports={"BUS": {"at": "-y" if i in (1, 3) else "+y"}})
    S.component("busbar_main", "power", "busbar", part="busbar_main", label="48 V main busbar 40 x 10 mm", cols={"power": 1})
    S.bus("busbar_main", "power", axis=2, bar_mm=[40, 10])
    z_bank = {1: 50, 2: 50, 3: 235, 4: 235}
    for i in (1, 2, 3, 4):
        S.port("busbar_main", f"bank_{i}", {"part": "busbar_main", "at": "-z", "offset": [20, 0, z_bank[i]]})
        S.link(f"contact_bank_{i}", "power", f"battery_bank_{i}.BUS", f"busbar_main.bank_{i}", kind="contact", contact=True)
    for i, dz in ((1, 500), (2, 580)):
        S.port("busbar_main", f"reg_{i}", {"part": "busbar_main", "at": "-z", "offset": [20, 0, dz], "dir": [1, 0, 0]})
        S.link(f"cable_regulator_{i}", "power", f"solar_regulator_{i}.OUT", f"busbar_main.reg_{i}", auto=True, awg="4/0", color="red", assembly="HV_Power_Schematic",
               bundle="Solar regulator feeds")
    for k in trays:
        feeder = _copy("busbar_feeder", "busbar_feeder_run", k)
        tray = _copy("server_tray", "server_tray_stack", k)
        S.component(f"tray_{k}", ("power", "optical", "fluid"), "server tray", part=tray, label=f"Server tray {k}", cols={"power": 2, "optical": 4, "fluid": 4}, load_w=TRAY_LOAD_W,
                    ports={"P48": {"part": feeder, "at": "-x"}})
        S.port("busbar_main", f"t{k}", {"part": feeder, "at": "+x"})
        S.link(f"feeder_{k}", "power", f"busbar_main.t{k}", f"tray_{k}.P48", part=feeder, kind="bar", bar_mm=[40, 10])
    loads = [("pump_A1", 150, "14", "pump"), ("pump_A2", 0, "14", "pump"), ("pump_B1", 150, "14", "pump"), ("pump_B2", 0, "14", "pump"),
             ("edfa_1", 30, "18", "edfa"), ("edfa_2", 30, "18", "edfa"), ("edfa_3", 30, "18", "edfa"), ("edfa_4", 30, "18", "edfa"),
             ("optical_switch_top", 200, "14", "switch"), ("optical_switch_bottom", 200, "14", "switch"), ("flight_computer", 60, "16", "computer")]
    tap_from_bottom = {"pump_A1": 680, "pump_A2": 740, "pump_B1": 800, "pump_B2": 860, "edfa_3": 920, "edfa_4": 980, "optical_switch_bottom": 1040}    # taps 60 mm apart, near the floor kit
    tap_from_top = {"edfa_1": 30, "edfa_2": 90, "optical_switch_top": 150, "flight_computer": 210}                                                           # and near the ceiling
    for j, (name, watts, awg, kind) in enumerate(loads):
        label = {"pump": "Pump " + name[-2:], "edfa": "EDFA " + name[-1], "switch": "Core switch " + name.split("_")[-1], "computer": "Flight computer"}[kind]
        existing = S.components.get(name)
        if existing is None:
            S.component(name, "power", kind, part=name, label=label, cols={"power": 2}, load_w=watts)
        else:
            existing["load_w"] = watts
        S.port(name, "V", {"at": "+z" if kind == "pump" else "+x", **({"part": "flight_shield"} if kind == "computer" else {})})     # the computer's supply enters through its shield
        # each cable leaves the bar a little farther out than the cable above (or below) it, so their first legs nest instead of crossing
        if name in tap_from_bottom:
            rank = sorted(tap_from_bottom.values()).index(tap_from_bottom[name])
            tap = {"part": "busbar_main", "at": "-z", "offset": [20, 0, tap_from_bottom[name]], "dir": [1, 0, 0], "lead": 40 + 45 * (len(tap_from_bottom) - 1 - rank)}
        else:
            rank = sorted(tap_from_top.values()).index(tap_from_top[name])
            tap = {"part": "busbar_main", "at": "+z", "offset": [20, 0, -tap_from_top[name]], "dir": [1, 0, 0], "lead": 40 + 45 * rank}
        S.port("busbar_main", f"tap_{name}", tap)
        S.link(f"cable_{name}", "power", f"busbar_main.tap_{name}", f"{name}.V", auto=True, awg=awg, color="orange", assembly="HV_Power_Schematic",
               bundle="Power to pumps" if kind == "pump" else "Power to optical rack")
    # ---- optical -------------------------------------------------------------------------------------------------------------
    for i, tag in enumerate(("fwd_a", "fwd_b", "aft_a", "aft_b"), start=1):
        fwd = "fwd" in tag
        S.component(f"turret_{tag}", "optical", "OISL turret", part=f"turret_{tag}_base", label=f"OISL turret {tag.replace('_', ' ')}", cols={"optical": 0},
                    ports={"FIBRE": {"part": f"fibre_turret_{i}", "at": "+z" if fwd else "-z"}})
        S.component(f"edfa_{i}", "optical", "edfa", part=f"edfa_{i}", merge=True, cols={"optical": 1},
                    ports={"TO_TURRET": {"part": f"fibre_turret_{i}", "at": "-z" if fwd else "+z"}, "TO_SWITCH": {"part": f"fibre_edfa_switch_{i}", "at": "+y" if i in (1, 3) else "-y"}})
        S.link(f"fibre_turret_{i}", "optical", f"edfa_{i}.TO_TURRET", f"turret_{tag}.FIBRE", part=f"fibre_turret_{i}", fibre="SMF-28", conduit_od_mm=12)
    for name, edfas in (("optical_switch_top", (1, 2)), ("optical_switch_bottom", (3, 4))):
        S.component(name, "optical", "core switch", part=name, merge=True, cols={"optical": 2}, ports={"TRUNK": {"part": "fibre_trunk", "at": "+z" if name.endswith("top") else "-z"}})
        for e in edfas:
            S.port(name, f"E{e}", {"part": f"fibre_edfa_switch_{e}", "at": "-y" if e in (1, 3) else "+y"})
            S.link(f"fibre_edfa_switch_{e}", "optical", f"edfa_{e}.TO_SWITCH", f"{name}.E{e}", part=f"fibre_edfa_switch_{e}", fibre="SMF-28", conduit_od_mm=12)
    S.component("flight_computer", "optical", "computer", part="flight_computer", merge=True, cols={"optical": 4})
    S.component("optical_trunk", "optical", "fibre trunk", part="fibre_trunk", label="Optical backbone 12 mm", cols={"optical": 3})
    S.bus("optical_trunk", "optical", axis=2, fibre="SMF-28", conduit_od_mm=12)
    S.port("optical_trunk", "top", {"part": "fibre_trunk", "at": "+z"})
    S.port("optical_trunk", "bottom", {"part": "fibre_trunk", "at": "-z"})
    S.link("trunk_to_switch_top", "optical", "optical_switch_top.TRUNK", "optical_trunk.top", contact=True, fibre="SMF-28", length_mm=0)
    S.link("trunk_to_switch_bottom", "optical", "optical_switch_bottom.TRUNK", "optical_trunk.bottom", contact=True, fibre="SMF-28", length_mm=0)
    S.port("optical_trunk", "fc", {"part": "fibre_computer_link", "at": "-y"})
    S.port("flight_computer", "OPT", {"part": "fibre_computer_link", "at": "+y"})
    S.link("fibre_computer_link", "optical", "optical_trunk.fc", "flight_computer.OPT", part="fibre_computer_link", fibre="SMF-28", conduit_od_mm=12)
    for k in trays:
        branch = _copy("fibre_branch", "fibre_branch_run", k)
        S.port("optical_trunk", f"t{k}", {"part": branch, "at": "-x"})
        S.port(f"tray_{k}", "OPT", {"part": branch, "at": "+x"})
        S.link(f"fibre_branch_{k}", "optical", f"optical_trunk.t{k}", f"tray_{k}.OPT", part=branch, fibre="SMF-28", conduit_od_mm=12)
    # ---- coolant -------------------------------------------------------------------------------------------------------------
    for tag, s in (("A", 1), ("B", -1)):
        tray_end, man_end = ("-y", "+y") if s > 0 else ("+y", "-y")
        inner, outer = ("-y", "+y") if s > 0 else ("+y", "-y")                  # faces of the skid equipment toward the trays / toward the hull wall
        wing, wing_end, near_end = ("pos_y", "+y", "-y") if s > 0 else ("neg_y", "-y", "+y")
        asm = "MPFL_Fluid_Schematic"
        S.component(f"cold_manifold_{tag}", "fluid", "manifold", part=f"cold_manifold_{tag}", label=f"Cold manifold {tag}", cols={"fluid": 3}, od_mm=60, wall_mm=3, color="blue", loop=tag,
                    ports={"in1": {"at": "-z"}, "in2": {"at": "+z"}})
        S.component(f"hot_manifold_{tag}", "fluid", "manifold", part=f"hot_manifold_{tag}", label=f"Hot manifold {tag}", cols={"fluid": 5}, od_mm=60, wall_mm=3, color="red", loop=tag,
                    ports={"out": {"part": f"hot_line_{tag}", "at": near_end}})
        S.component(f"accumulator_{tag}", "fluid", "accumulator", part=f"accumulator_{tag}", label=f"Accumulator {tag}", cols={"fluid": 1}, loop=tag,
                    ports={"in": {"at": "+z"}, "out1": {"at": inner, "offset": [0, 0, -85]}, "out2": {"at": "+x", "offset": [0, 0, -85]}})
        for n in (1, 2):
            S.component(f"pump_{tag}{n}", "fluid", "pump", part=f"pump_{tag}{n}", merge=True, cols={"fluid": 2}, loop=tag, label=f"Pump {tag}{n}" + (" (duty)" if n == 1 else " (standby)"),
                        ports={"in": {"at": inner}, "out": {"at": outer}})
        S.component(f"wing_{tag}", "fluid", "radiator wing", label="Radiator wing " + ("+Y" if s > 0 else "-Y"), cols={"fluid": 6}, loop=tag,
                    ports={"hot_in": {"part": f"hot_line_{tag}", "at": wing_end}, "cold_out": {"part": f"cold_line_{tag}", "at": wing_end}}, area_m2=RADIATOR_AREA_M2)
        S.component(f"elbow_{tag}", "fluid", "fitting", label=f"Return fitting {tag}", cols={"fluid": 0}, loop=tag, ports={"in": {"part": f"cold_line_{tag}", "at": near_end},
                                                                                                                          "out": {"part": f"cold_line_{tag}", "at": near_end}})
        for k in trays:
            ch, hf = _copy(f"cold_feeder_{tag}", f"cold_feeder_{tag}_run", k), _copy(f"hot_feeder_{tag}", f"hot_feeder_{tag}_run", k)
            S.port(f"tray_{k}", f"{tag}_cold", {"part": ch, "at": tray_end})
            S.port(f"tray_{k}", f"{tag}_hot", {"part": hf, "at": tray_end})
            S.port(f"cold_manifold_{tag}", f"tap{k}", {"part": ch, "at": man_end})
            S.port(f"hot_manifold_{tag}", f"tap{k}", {"part": hf, "at": man_end})
            S.link(f"cold_feeder_{tag}_{k}", "fluid", f"cold_manifold_{tag}.tap{k}", f"tray_{k}.{tag}_cold", part=ch, od_mm=20, wall_mm=1.5, role="cold", loop=tag)
            S.link(f"hot_feeder_{tag}_{k}", "fluid", f"tray_{k}.{tag}_hot", f"hot_manifold_{tag}.tap{k}", part=hf, od_mm=20, wall_mm=1.5, role="hot", loop=tag)
        S.link(f"hot_line_{tag}", "fluid", f"hot_manifold_{tag}.out", f"wing_{tag}.hot_in", part=f"hot_line_{tag}", od_mm=60, wall_mm=2, role="hot", loop=tag)
        S.link(f"cold_line_{tag}", "fluid", f"wing_{tag}.cold_out", f"elbow_{tag}.in", part=f"cold_line_{tag}", od_mm=60, wall_mm=2, role="cold", loop=tag)
        pipe = dict(od_mm=40, wall_mm=2, auto=True, assembly=asm, loop=tag)
        S.link(f"return_{tag}", "fluid", f"elbow_{tag}.out", f"accumulator_{tag}.in", role="cold", **pipe)
        for n, port in ((1, "out1"), (2, "out2")):
            S.link(f"suction_{tag}{n}", "fluid", f"accumulator_{tag}.{port}", f"pump_{tag}{n}.in", role="cold", **pipe)
        S.link(f"discharge_{tag}1", "fluid", f"pump_{tag}1.out", f"cold_manifold_{tag}.in1", role="cold", **pipe)
        S.link(f"discharge_{tag}2", "fluid", f"pump_{tag}2.out", f"cold_manifold_{tag}.in2", role="cold", **pipe)
        bore = dict(od_mm=36, wall_mm=2)
        hot_tubes = [_copy(f"tube_hot_{wing}", f"tube_hot_{wing}_row", k) for k in range(1, 7)]
        cold_tubes = [_copy(f"tube_cold_{wing}", f"tube_cold_{wing}_row", k) for k in range(1, 7)]
        S.loop(f"Loop {tag}", fluid="ammonia", heat_w=TRAYS * TRAY_LOAD_W, delta_t_k=10.0, pump={"component": f"pump_{tag}1", "head_kpa": 250.0},
               radiator={"area_m2": RADIATOR_AREA_M2, "emissivity": 0.85, "fluid_temp_k": 293.0, "sink_temp_k": 200.0, "fin_drop_k": 5.0},
               stages=[dict(name="Pump discharge", elements=[f"discharge_{tag}1"]),
                       dict(name="Cold manifold", elements=[f"cold_manifold_{tag}"]),
                       dict(name="Cold feeders (16 in parallel)", elements=[f"cold_feeder_{tag}_{k}" for k in trays], parallel=True),
                       dict(name="Tray cold plates", elements=[dict(id=f"cold_plates_{tag}", od_mm=17, wall_mm=0, length_mm=0, dp_kpa=20.0, parts=[f"tray_{k}" for k in trays])]),
                       dict(name="Hot feeders (16 in parallel)", elements=[f"hot_feeder_{tag}_{k}" for k in trays], parallel=True),
                       dict(name="Hot manifold", elements=[f"hot_manifold_{tag}"]),
                       dict(name="Hot line to radiator", elements=[f"hot_line_{tag}"]),
                       dict(name="Root header (hot)", elements=[dict(id=f"root_hot_{tag}", part=f"root_header_hot_{wing}", od_mm=60, wall_mm=2)]),
                       dict(name="Hot radiator tubes (6 in parallel)", elements=[dict(id=f"tubes_hot_{tag}", parts=hot_tubes, **bore)]),
                       dict(name="Tip header", elements=[dict(id=f"tip_{tag}", part=f"tip_header_{wing}", od_mm=60, wall_mm=2)]),
                       dict(name="Cold radiator tubes (6 in parallel)", elements=[dict(id=f"tubes_cold_{tag}", parts=cold_tubes, **bore)]),
                       dict(name="Root header (cold)", elements=[dict(id=f"root_cold_{tag}", part=f"root_header_cold_{wing}", od_mm=60, wall_mm=2)]),
                       dict(name="Cold line", elements=[f"cold_line_{tag}"]),
                       dict(name="Return to accumulator", elements=[f"return_{tag}"]),
                       dict(name="Pump suction", elements=[f"suction_{tag}1"])])
    # ---- optical loss budgets ---------------------------------------------------------------------------------------------------
    S.sync(d)
    for k in trays:
        S.chain(f"Core switch (top) to tray {k}", links=_trunk_path(S, k) + [f"fibre_branch_{k}"], connectors=4, budget_db=6.0)
    for i in (1, 2, 3, 4):
        sw = "optical_switch_top" if i in (1, 2) else "optical_switch_bottom"
        far = 16 if i in (1, 2) else 1
        S.chain(f"Turret {i} to tray {far} (longest path)", links=[f"fibre_turret_{i}", f"fibre_edfa_switch_{i}"] + _trunk_path(S, far, top=(i in (1, 2))) + [f"fibre_branch_{far}"],
                connectors=7, budget_db=10.0)
    return S


def _trunk_path(S, k: int, top: bool = True) -> list[str]:
    """The trunk segments between the top (or bottom) switch and the tap of tray k."""
    segs = sorted((l for l in S.links.values() if l.get("generated") == "optical_trunk"), key=lambda l: min(l["span"][0][2], l["span"][1][2]))
    tap = S.port_pos(f"optical_trunk.t{k}")[2]
    out = []
    for l in segs:
        lo, hi = sorted((l["span"][0][2], l["span"][1][2]))
        if top and lo >= tap - 1e-6:
            out.append(l["id"])
        if not top and hi <= tap + 1e-6:
            out.append(l["id"])
    return out


# ==========================================================================================================
#  The whole satellite
# ==========================================================================================================
def build(overrides: dict | None = None, name: str = "Heavy_Orbital_Node", with_systems: bool = True) -> Design:
    p = {**PARAMETERS, **(overrides or {})}
    unknown = set(p) - set(PARAMETERS)
    if unknown:
        raise DesignError(f"Unknown parameter(s): {sorted(unknown)}. Choose from {list(PARAMETERS)}.")
    if not 3500 <= p["BUS_WIDTH_MM"] <= 6000:
        raise DesignError("BUS_WIDTH_MM must be between 3500 and 6000 (the internal schematics need the 3500 mm minimum).")
    if not 100 <= p["TRAY_PITCH_MM"] <= 280:
        raise DesignError("TRAY_PITCH_MM must be between 100 and 280 (trays are 65 mm thick, and the stack must stay between the end equipment).")
    if not 40 <= p["FIN_RIDGE_ANGLE_DEG"] <= 170:
        raise DesignError("FIN_RIDGE_ANGLE_DEG must be between 40 and 170 degrees (above about 122 the ridge is clamped to stay a valid shape).")
    d = Design(name)
    with d.batch():
        for k, v in p.items():
            d.set_variable(k, v)
        d.set_variable("hull_half", "=BUS_WIDTH_MM / 2")
        d.set_variable("wing_root_y", "=BUS_WIDTH_MM / 2 + 1050")
        d.set_variable("tray_z0", "=-7.5 * TRAY_PITCH_MM")
        d.set_variable("stack_half", "=7.5 * TRAY_PITCH_MM + 32.5")
        d.set_variable("bus_bottom", -2500)
        d.set_variable("bus_top", "=stack_half + 360")
        d.set_variable("manifold_z0", "=-stack_half - 150")
        d.set_variable("manifold_len", "=2 * stack_half + 300")
        d.set_variable("fin_base_half", f"=min({FIN_TOP / 2} + {FIN_HEIGHT} * tan(FIN_RIDGE_ANGLE_DEG / 2), {FIN_PITCH / 2 - 1})")
        b = B(d)
        for step in (primary_bus, compute_core, hv_power, optical, fluid, radiators, turrets):
            step(b)
    if with_systems:                                          # the netlist, the automatic cable and pipe routes, and their 3D tubes
        from satcad import routing
        routing.update(d, netlist(d))
    return d


def corridors(p: dict | None = None) -> dict:
    """Clear space between the server trays and the inside of the hull."""
    w = (p or PARAMETERS)["BUS_WIDTH_MM"]
    return dict(x_clear_mm=(w - 2 * WALL - TRAY[0]) / 2, y_clear_mm=(w - 2 * WALL - TRAY[1]) / 2,
                x_from_outer_skin_mm=(w - TRAY[0]) / 2, nominal_mm=CORRIDOR_NOMINAL)


def report(d: Design) -> str:
    m, check, tree = d.measure(), d.check(), d.assembly_tree()
    c = corridors({k: d.variables[k] for k in PARAMETERS})
    lines = [f"{d.name}: {m['parts']} bodies in {len(d.features)} features, total mass {m['total_mass_kg']:,.0f} kg",
             f"Overall size {m['overall_size_mm'][0] / 1000:.2f} x {m['overall_size_mm'][1] / 1000:.2f} x {m['overall_size_mm'][2] / 1000:.2f} m",
             f"Corridors beside the trays: X {c['x_clear_mm']:.0f} mm clear ({c['x_from_outer_skin_mm']:.0f} mm from the outer skin), Y {c['y_clear_mm']:.0f} mm clear "
             f"(target {c['nominal_mm']} mm)", "Assemblies (kg):"]
    for a in tree["assemblies"]:
        lines.append(f"  {a['name']:24s} {a['mass_kg']:>10,.1f}")
    lines.append("Interference check: " + ("no overlapping bodies" if not check["overlaps"] else f"{len(check['overlaps'])} overlaps"))
    for o in check["overlaps"][:8]:
        lines.append(f"  OVERLAP {o['a']} / {o['b']}: {o['overlap_cm3']:,.2f} cm3")
    if check["floating_parts"]:
        lines.append(f"Floating parts ({len(check['floating_parts'])}): " + ", ".join(f['part'] for f in check['floating_parts'][:8]))
    return "\n".join(lines)


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
    ap = argparse.ArgumentParser(description="Build the heavy-class Orbital-Node-Alpha satellite.")
    ap.add_argument("--set", action="append", metavar="NAME=VALUE", help="override BUS_WIDTH_MM, TRAY_PITCH_MM or FIN_RIDGE_ANGLE_DEG")
    ap.add_argument("--name", default="heavy_orbital_node", help="file name (without extension) for the saved design")
    ap.add_argument("--no-save", action="store_true", help="do not write files")
    ap.add_argument("--no-systems", action="store_true", help="geometry only: skip the wiring / tubing / fibre netlist and its routes")
    ap.add_argument("--diagrams", action="store_true", help="also write the wiring, cooling and optical diagrams, the 3D routing view and the cut lists")
    args = ap.parse_args(argv)
    t0 = time.time()
    try:
        d = build(_parse(args.set), with_systems=not args.no_systems)
    except DesignError as e:
        print(f"Can't build this design: {e}", file=sys.stderr)
        return 1
    print(report(d))
    if d.systems:
        from satcad.systems import Systems
        print(Systems.of(d).report(d))
    print(f"(built in {time.time() - t0:.1f} s)")
    if not args.no_save:
        js = in_designs(f"{args.name}.json")
        js.parent.mkdir(parents=True, exist_ok=True)
        d.save(js)
        info = d.export(js.parent, args.name, ["step"])
        print("Saved", js)
        print("Exported", info["step"]["path"])
        if args.diagrams and d.systems:
            from satcad import diagrams
            from satcad.systems import Systems
            paths = diagrams.write_all(Systems.of(d), d, js.parent / f"{args.name}_systems", include_check=False)
            print(f"Wrote {len(paths)} drawings and cut lists to", js.parent / f"{args.name}_systems")
    return 0


if __name__ == "__main__":
    sys.exit(main())
