#!/usr/bin/env python3
"""
ORBITAL-NODE-ALPHA: detailed multi-body assembly of the AI datacentre satellite, built with the satCAD engine (OpenCASCADE kernel).

    python generate_orbital_node_assembly.py                       # build, check, print a report
    python generate_orbital_node_assembly.py --step alpha.step     # ...and export STEP into the 'sattelite designs' folder
    python generate_orbital_node_assembly.py --json alpha.json     # ...and save the editable parametric recipe there too
    python generate_orbital_node_assembly.py --set NUM_SERVER_BLADES=12 --set RADIATOR_SPAN_MM=10000

In the desktop app:  Assemblies tab -> "Orbital-Node-Alpha" builds this model into the open part, every sub-assembly appearing in the Part Navigator.

COORDINATE SYSTEM (origin = centre of the primary bus)
    +Z / -Z   velocity vector: fore / aft         (turrets, launch adapter)
    +Y / -Y   anti-Sun / deep-space axis          (radiator wings)
    +X / -X   Sun-facing axis                     (solar-array wings, bus inspection windows)
All dimensions are millimetres. Every part is a named part inside a named assembly, and the four numbers below are model variables: change
one (here, with --set, or in the desktop app's Property Editor) and the whole assembly rebuilds.

SUB-ASSEMBLIES
    1 bus            outer hull minus cavity (Boolean), inspection windows, four I-beams, launch adapter ring
    2 compute_rack   server blades, 8 silicon packages each, copper cold plate with micro-channel grooves
    3 mpfl           two pumps, hot / cold manifolds (24 segments each, so the coolant pulse can be animated), feeder tubes, bypass valve
    4 radiators      two deployable wings: truss boom, root manifold, panel, 24 micro-tubes on each face
    5 solar_arrays   SADA + Y-yoke + three framed solar segments per side
    6 oisl_turrets   four optical terminals: azimuth base, U-yoke, telescope barrel, 45-degree fast steering mirror, lens
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from satcad import Design, DesignError  # noqa: E402
from satcad.paths import designs_dir, in_designs  # noqa: E402

# ==========================================================================================================
#  >>> THE FOUR HEADLINE PARAMETERS (also model variables: editable in the app) <<<
# ==========================================================================================================
PARAMETERS = dict(
    NUM_SERVER_BLADES=16,       # horizontal AI server blades stacked along Z (1 to 18 fit the bus)
    RADIATOR_SPAN_MM=12000,     # length of each radiator wing along Y
    SOLAR_SPAN_MM=15000,        # total span of each solar wing along X (three segments)
    BUS_WALL_THICKNESS=25,      # wall of the hollow primary bus
    BLADE_GAP_MM=80,            # clear gap between neighbouring blades
)

# ---- fixed design data ------------------------------------------------------------------------------------
HULL = (2000, 2000, 3500)
BLADE = (1600, 1200, 60)
PACKAGE = (100, 100, 5)
PLATE = (1400, 1000, 15)
MANIFOLD_R, FEEDER_R, MICRO_R = 30, 10, 10
BYPASS_Z = -1290
MANIFOLD_Y = 900
MANIFOLD_SEGMENTS = 24
BOOM_LENGTH = 800
PANEL_ROOT_Y = 1860
HINGE_GAP = 20

COLORS = {                       # RGB 0..255 as specified, converted to 0..1 for the engine
    "gold": (212, 175, 55), "pcb": (20, 60, 30), "copper": (184, 115, 51), "steel": (192, 192, 200), "white": (245, 245, 250),
    "solar": (15, 35, 80), "silver": (192, 196, 204), "black": (35, 35, 40), "lens": (0, 200, 255), "silicon": (48, 50, 58),
    "hull": (200, 204, 210), "beam": (140, 148, 158),
}


def rgb(name: str) -> list:
    return [round(v / 255.0, 4) for v in COLORS[name]]


class Builder:
    """Thin helper: adds features to a Design, keeping track of the assembly each belongs to."""

    def __init__(self, design: Design):
        self.d = design

    def asm(self, name, parent=None):
        self.d.add("assembly", name, **({"parent": parent} if parent else {}))

    def add(self, kind, name, assembly, color=None, **kw):
        if color:
            kw["color"] = rgb(color)
        if assembly:
            kw["assembly"] = assembly
        self.d.add(kind, name, **kw)

    def pattern(self, name, source, assembly, count, dx=0, dy=0, dz=0):
        self.d.add("pattern", name, source=source, mode="linear", count=count, dx=dx, dy=dy, dz=dz, assembly=assembly)


# ==========================================================================================================
#  1. PRIMARY BUS & PAYLOAD ADAPTER
# ==========================================================================================================
def bus(b: Builder) -> None:
    b.asm("bus")
    b.add("box", "bus_hull", "bus", "hull", length=HULL[0], width=HULL[1], height=HULL[2], material="aluminum_6061", role="chassis")
    b.add("box", "bus_cavity", None, None, length="=2000 - 2 * BUS_WALL_THICKNESS", width="=2000 - 2 * BUS_WALL_THICKNESS",
          height="=3500 - 2 * BUS_WALL_THICKNESS")
    b.d.add("boolean", "bus_hollow", operation="subtract", target="bus_hull", tool="bus_cavity")          # Boolean cut: the hollow hull
    for side in ("+x", "-x"):                                                                             # inspection windows
        b.d.add("cutout", f"window_{'pos' if side[0] == '+' else 'neg'}_x", target="bus_hull", side=side, shape="rect", width=1400, height=2400, depth="=BUS_WALL_THICKNESS")
    for side in ("+y", "-y"):                                                                             # pass-through for the radiator lines
        b.d.add("cutout", f"line_port_{'pos' if side[0] == '+' else 'neg'}_y", target="bus_hull", side=side, shape="circle", diameter=80, depth="=BUS_WALL_THICKNESS")
    pts = [[-50, -50], [50, -50], [50, -38], [6, -38], [6, 38], [50, 38], [50, 50], [-50, 50], [-50, 38], [-6, 38], [-6, -38], [-50, -38]]
    for i, (sx, sy) in enumerate(((1, 1), (-1, 1), (-1, -1), (1, -1)), start=1):
        b.add("extrude", f"ibeam_{i}", "bus", "beam", profile="polygon", points=pts, depth="=3500 - 2 * BUS_WALL_THICKNESS", plane="xy",
              x=f"={sx} * (1000 - BUS_WALL_THICKNESS - 50)", y=f"={sy} * (1000 - BUS_WALL_THICKNESS - 50)", z="=-1750 + BUS_WALL_THICKNESS",
              material="aluminum_6061", role="structure")                                                  # beams touch the inner wall
    b.add("extrude", "adapter_ring", "bus", "gold", profile="circle", radius=787.5, wall=62.5, depth=150, plane="xy", z=-1900,
          material="aluminum_6061", role="structure")
    b.d.add("fillet_edges", "adapter_chamfer", target="adapter_ring", edges="face:-z", chamfer=5)       # 5 mm chamfer on the bottom lip


# ==========================================================================================================
#  2. AI COMPUTE RACK & COLD PLATES
# ==========================================================================================================
def compute(b: Builder) -> None:
    b.asm("compute_rack")
    n, z0 = "NUM_SERVER_BLADES", "blade_z0"
    b.add("box", "blade", "compute_rack", "pcb", length=BLADE[0], width=BLADE[1], height=BLADE[2], z=f"={z0}", material="generic_equipment",
          role="payload")
    b.pattern("blade_stack", "blade", "compute_rack", f"={n}", dz="=60 + BLADE_GAP_MM")
    k = 0
    for ix in (-450, -150, 150, 450):                                                                      # 8 silicon packages per blade
        for iy in (-250, 250):
            k += 1
            b.add("box", f"pkg_{k}", "compute_rack", "silicon", length=PACKAGE[0], width=PACKAGE[1], height=PACKAGE[2], x=ix, y=iy,
                  z=f"={z0} + 30 + 2.5", material="generic_equipment", role="payload")
            b.pattern(f"pkg_{k}_stack", f"pkg_{k}", "compute_rack", f"={n}", dz="=60 + BLADE_GAP_MM")
    b.add("box", "coldplate", "compute_rack", "copper", length=PLATE[0], width=PLATE[1], height=PLATE[2], z=f"={z0} + 30 + 5 + 7.5",
          material="copper", role="payload")
    for i, gx in enumerate((-300, -100, 100, 300), start=1):                                               # micro-channel grooves
        b.d.add("cutout", f"channel_{i}", target="coldplate", side="+z", shape="rect", width=14, height=900, depth=6, offset_u=gx)
    b.pattern("coldplate_stack", "coldplate", "compute_rack", f"={n}", dz="=60 + BLADE_GAP_MM")


# ==========================================================================================================
#  3. MECHANICALLY PUMPED FLUID LOOP
# ==========================================================================================================
def mpfl(b: Builder) -> None:
    b.asm("mpfl")
    n = "NUM_SERVER_BLADES"
    for name, x in (("pump_a", -400), ("pump_b", 400)):
        b.add("cylinder", name, "mpfl", "steel", radius=120, height=350, axis="y", x=x, y=0, z=-1500, material="stainless_316", role="payload")
    for name, x in (("pump_saddle_a", -400), ("pump_saddle_b", 400)):                                   # mounts from the pump to the bus floor
        b.add("box", name, "mpfl", "steel", length=200, width=300, height="=130 - BUS_WALL_THICKNESS", x=x, y=0,
              z="=-1750 + BUS_WALL_THICKNESS + (130 - BUS_WALL_THICKNESS) / 2", material="aluminum_6061", role="structure")
    b.add("box", "bypass_valve", "mpfl", "steel", length=150, width=150, height=150, z=BYPASS_Z, material="stainless_316", role="payload")
    b.add("extrude", "bypass_a", "mpfl", "steel", profile="circle", radius=MANIFOLD_R, wall=2, depth=795, plane="zx", z=BYPASS_Z, y=-870,
          material="stainless_316", role="payload")
    b.add("extrude", "bypass_b", "mpfl", "steel", profile="circle", radius=MANIFOLD_R, wall=2, depth=795, plane="zx", z=BYPASS_Z, y=75,
          material="stainless_316", role="payload")
    for tag, y in (("hot", MANIFOLD_Y), ("cold", -MANIFOLD_Y)):                                            # vertical manifolds in 24 segments
        b.add("extrude", f"{tag}_manifold", "mpfl", "steel", profile="circle", radius=MANIFOLD_R, wall=2, depth="=manifold_segment",
              plane="xy", y=y, z="=manifold_bottom", material="stainless_316", role="payload")
        b.pattern(f"{tag}_manifold_run", f"{tag}_manifold", "mpfl", MANIFOLD_SEGMENTS, dz="=manifold_segment")
    for tag, y0, depth in (("hot", 500, 370), ("cold", -870, 370)):                                        # 10 mm feeder tubes, one per cold plate
        b.add("extrude", f"{tag}_feeder", "mpfl", "steel", profile="circle", radius=FEEDER_R, wall=1.5, depth=depth, plane="zx", y=y0,
              z="=blade_z0 + 42.5", material="stainless_316", role="payload")
        b.pattern(f"{tag}_feeder_run", f"{tag}_feeder", "mpfl", f"={n}", dz="=60 + BLADE_GAP_MM")


# ==========================================================================================================
#  4. DEPLOYABLE RADIATOR WINGS
# ==========================================================================================================
def radiators(b: Builder) -> None:
    b.asm("radiators")
    for tag, s in (("pos_y", 1), ("neg_y", -1)):
        fixed, wing = f"radiator_{tag}", f"radiator_{tag}_wing"
        b.asm(fixed, "radiators")
        b.asm(wing, fixed)
        # fixed side: truss boom and the feed line from the bus manifold (through the bus wall port)
        b.add("extrude", f"boom_{tag}", fixed, "silver", profile="rect", width=240, height=240, wall=20, depth=BOOM_LENGTH, plane="zx",
              y=1000 if s > 0 else -1800, material="aluminum_6061", role="structure")
        b.add("extrude", f"feed_line_{tag}", fixed, "steel", profile="circle", radius=MANIFOLD_R, wall=2, depth=870, plane="zx",
              y=930 if s > 0 else -1800, material="stainless_316", role="payload")
        # wing side: root manifold, panel, micro-tubes on both faces
        b.add("extrude", f"root_manifold_{tag}", wing, "steel", profile="circle", radius=MANIFOLD_R, wall=2, depth=3000, plane="yz",
              x=-1500, y=s * 1830, material="stainless_316", role="payload")
        b.add("box", f"radiator_panel_{tag}", wing, "white", length=3000, width="=RADIATOR_SPAN_MM", height=12,
              y=f"={s * PANEL_ROOT_Y} + {s} * RADIATOR_SPAN_MM / 2", material="aluminum_honeycomb", role="radiator")
        for face, zc in (("top", 16), ("bottom", -16)):
            name = f"micro_tube_{tag}_{face}"
            b.add("extrude", name, wing, "white", profile="circle", radius=MICRO_R, wall=1.5, depth="=RADIATOR_SPAN_MM - 200", plane="zx",
                  x=-1380, z=zc, y=1900 if s > 0 else "=-1900 - (RADIATOR_SPAN_MM - 200)", material="aluminum_6061", role="radiator")
            b.pattern(f"{name}_row", name, wing, 24, dx=120)


# ==========================================================================================================
#  5. SUN-TRACKING SOLAR ARRAYS
# ==========================================================================================================
YOKE = [[-60, 1600], [60, 1600], [60, 1690], [1100, 1900], [900, 1900], [0, 1760], [-900, 1900], [-1100, 1900], [-60, 1690]]   # (z, x) outline


def solar(b: Builder) -> None:
    b.asm("solar_arrays")
    seg = "(SOLAR_SPAN_MM - 2 * 20) / 3"
    b.asm("solar_pos_x", "solar_arrays")
    b.asm("solar_pos_x_wing", "solar_pos_x")
    b.add("cylinder", "sada_pos_x", "solar_pos_x", "silver", radius=150, height=600, axis="x", x=1300, material="aluminum_6061", role="structure")
    b.add("extrude", "yoke_pos_x", "solar_pos_x_wing", "silver", profile="polygon", points=YOKE, depth=40, plane="zx", y=-20,
          material="aluminum_6061", role="structure")
    b.add("box", "solar_sheet_pos_x", "solar_pos_x_wing", "solar", length=f"={seg} - 80", width=15, height=2420, x=f"=1900 + {seg} / 2",
          material="aluminum_honeycomb", role="solar")
    b.add("extrude", "solar_frame_pos_x", "solar_pos_x_wing", "silver", profile="rect", width=2500, height=f"={seg}", wall=40, depth=15, plane="zx",
          x=f"=1900 + {seg} / 2", y=-7.5, material="aluminum_6061", role="structure")
    for part in ("solar_sheet_pos_x", "solar_frame_pos_x"):
        b.pattern(f"{part}_run", part, "solar_pos_x_wing", 3, dx=f"={seg} + 20")
    # the -X side is the mirror image of the +X side
    b.asm("solar_neg_x", "solar_arrays")
    b.asm("solar_neg_x_wing", "solar_neg_x")
    b.d.add("mirror", "sada_neg_x", source="sada_pos_x", plane="yz", assembly="solar_neg_x")
    b.d.add("mirror", "yoke_neg_x", source="yoke_pos_x", plane="yz", assembly="solar_neg_x_wing")
    b.add("box", "solar_sheet_neg_x", "solar_neg_x_wing", "solar", length=f"={seg} - 80", width=15, height=2420, x=f"=-(1900 + {seg} / 2)",
          material="aluminum_honeycomb", role="solar")
    b.add("extrude", "solar_frame_neg_x", "solar_neg_x_wing", "silver", profile="rect", width=2500, height=f"={seg}", wall=40, depth=15, plane="zx",
          x=f"=-(1900 + {seg} / 2)", y=-7.5, material="aluminum_6061", role="structure")
    for part in ("solar_sheet_neg_x", "solar_frame_neg_x"):
        b.pattern(f"{part}_run", part, "solar_neg_x_wing", 3, dx=f"=-({seg} + 20)")


# ==========================================================================================================
#  6. OISL LASER COMM TURRETS (PAT TERMINALS)
# ==========================================================================================================
TURRET_SITES = (("fwd_a", 1, 500), ("fwd_b", 1, -500), ("aft_a", -1, 500), ("aft_b", -1, -500))      # (name, +Z fore / -Z aft, y position)


def turrets(b: Builder) -> None:
    b.asm("oisl_turrets")
    for tag, s, y0 in TURRET_SITES:
        top = f"turret_{tag}"
        az, el, fsm = f"{top}_az", f"{top}_el", f"{top}_fsm"
        b.asm(top, "oisl_turrets")
        b.asm(az, top)
        b.asm(el, az)
        b.asm(fsm, el)
        z_face = s * 1750
        z_base_top = z_face + s * 120
        b.add("cylinder", f"{top}_base", top, "black", radius=180, height=120, y=y0, z=z_face + s * 60, material="aluminum_6061", role="antenna")
        u = [[s * 0, -160], [s * 0, 160], [s * 300, 160], [s * 300, 120], [s * 40, 120], [s * 40, -120], [s * 300, -120], [s * 300, -160]]
        b.add("extrude", f"{top}_yoke", az, "black", profile="polygon", points=[[z_base_top + p[0], p[1]] for p in u], depth=300, plane="zx",
              y=y0 - 150, material="aluminum_6061", role="antenna")
        z_c = z_base_top + s * 170
        b.add("extrude", f"{top}_barrel", el, "black", profile="circle", radius=110, wall=15, depth=350, plane="zx", z=z_c, y=y0 - 175,
              material="aluminum_6061", role="antenna")
        b.add("cylinder", f"{top}_lens", el, "lens", radius=95, height=6, axis="y", y=y0 + 163, z=z_c, material="generic_equipment", role="antenna")
        b.add("cylinder", f"{top}_pin_a", el, "silver", radius=25, height=10, axis="x", x=115, y=y0, z=z_c, material="aluminum_6061", role="antenna")
        b.d.add("mirror", f"{top}_pin_b", source=f"{top}_pin_a", plane="yz", assembly=el)             # trunnion pins carry the barrel in the yoke
        b.add("cylinder", f"{top}_fsm_mirror", fsm, "silver", radius=40, height=4, rx=45, y=y0, z=z_c, material="aluminum_6061", role="antenna")
        b.add("cylinder", f"{top}_fsm_mount", fsm, "silver", radius=8, height=54, axis="x", x=67, y=y0, z=z_c, material="aluminum_6061", role="antenna")


# ==========================================================================================================
#  The whole satellite
# ==========================================================================================================
def build(overrides: dict | None = None, name: str = "Orbital-Node-Alpha") -> Design:
    p = {**PARAMETERS, **(overrides or {})}
    unknown = set(p) - set(PARAMETERS)
    if unknown:
        raise DesignError(f"Unknown parameter(s): {sorted(unknown)}. Choose from {list(PARAMETERS)}.")
    n = p["NUM_SERVER_BLADES"]
    if int(n) != n or not 1 <= n <= 18:
        raise DesignError("NUM_SERVER_BLADES must be a whole number from 1 to 18 (more blades would not fit inside the bus).")
    if not 5 <= p["BUS_WALL_THICKNESS"] <= 60:
        raise DesignError("BUS_WALL_THICKNESS must be between 5 and 60 mm (the coolant manifolds sit 60 mm inside the wall).")
    if p["RADIATOR_SPAN_MM"] < 1000 or p["SOLAR_SPAN_MM"] < 3 * 500:
        raise DesignError("RADIATOR_SPAN_MM must be at least 1000 and SOLAR_SPAN_MM at least 1500.")
    d = Design(name.replace(" ", "_"))
    with d.batch():                                   # one rebuild at the end instead of one per feature
        for key, value in p.items():
            d.set_variable(key, value)
        d.set_variable("blade_z0", "=-(NUM_SERVER_BLADES - 1) * (60 + BLADE_GAP_MM) / 2")
        d.set_variable("manifold_bottom", BYPASS_Z + MANIFOLD_R)
        d.set_variable("manifold_top", "=(NUM_SERVER_BLADES - 1) * (60 + BLADE_GAP_MM) / 2 + 42.5 + 40")
        d.set_variable("manifold_segment", f"=(manifold_top - manifold_bottom) / {MANIFOLD_SEGMENTS}")
        b = Builder(d)
        for build_one in (bus, compute, mpfl, radiators, solar, turrets):
            build_one(b)
    return d


def report(d: Design) -> str:
    m = d.measure()
    check = d.check()
    tree = d.assembly_tree()
    lines = [f"{d.name}: {m['parts']} bodies in {len(d.features)} features, total mass {m['total_mass_kg']:,.0f} kg",
             f"Overall size {m['overall_size_mm'][0] / 1000:.2f} x {m['overall_size_mm'][1] / 1000:.2f} x {m['overall_size_mm'][2] / 1000:.2f} m",
             "Sub-assemblies (kg):"]
    for a in tree["assemblies"]:
        lines.append(f"  {a['name']:15s} {a['mass_kg']:>10,.1f}")
    lines.append("Interference check: " + ("no overlapping bodies" if not check["overlaps"] else f"{len(check['overlaps'])} overlaps"))
    for o in check["overlaps"][:8]:
        lines.append(f"  OVERLAP {o['a']} / {o['b']}: {o['overlap_cm3']:,.2f} cm3")
    return "\n".join(lines)


def _parse(items) -> dict:
    out = {}
    for it in items or []:
        key, _, val = it.partition("=")
        key = key.strip().upper()
        try:
            out[key] = float(val) if "." in val else int(val)
        except ValueError:
            raise SystemExit(f"--set {it}: the value must be a number")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the Orbital-Node-Alpha assembly.")
    ap.add_argument("--set", action="append", metavar="NAME=VALUE", help="override NUM_SERVER_BLADES, RADIATOR_SPAN_MM, SOLAR_SPAN_MM, BUS_WALL_THICKNESS or BLADE_GAP_MM")
    ap.add_argument("--step", metavar="FILE", help="export a STEP file (a bare file name is saved in the 'sattelite designs' folder)")
    ap.add_argument("--json", metavar="FILE", help="save the parametric recipe (a bare file name goes in the 'sattelite designs' folder)")
    args = ap.parse_args(argv)
    t0 = time.time()
    try:
        d = build(_parse(args.set))
    except DesignError as e:
        print(f"Can't build this design: {e}", file=sys.stderr)
        return 1
    print(report(d))
    print(f"(built in {time.time() - t0:.1f} s)")
    if args.json:
        out = in_designs(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        d.save(out)
        print("Saved", out)
    if args.step:
        out = in_designs(args.step)
        out.parent.mkdir(parents=True, exist_ok=True)
        info = d.export(out.parent, out.stem, ["step"])
        Path(info["step"]["path"]).replace(out) if Path(info["step"]["path"]) != out else None
        print("Exported", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
