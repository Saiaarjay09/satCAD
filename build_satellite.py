"""
ORBITAL AI DATACENTRE: parametric satellite model, built with SatCAD
====================================================================

Builds a 3D model of a space-based AI compute node out of four subsystems:

    1. CHASSIS        a large hollow rectangular core holding a row of AI compute blades
    2. RADIATORS      two big flat panels reaching out of the sides to dump heat into deep space
    3. OISL           two laser transceivers (inter-satellite optical links), one front and one rear
    4. SOLAR ARRAYS   two flat wings on the sun-facing axis, perpendicular to the radiators

HOW TO RUN (from this folder, with the virtual environment active):

    python build_satellite.py                 build it, write the files, print a design report
    python build_satellite.py --view          ...and open the interactive 3D viewer afterwards
    python build_satellite.py --set RADIATOR_WINGSPAN=30000 --name wide_radiators
                                              try a different size without editing the file

OUTPUT (in ./output/<name>/):
    <name>_viewer.html          interactive 3D model: orbit, zoom, click parts, hide parts, explode
    <name>.png                  4 views            <name>_core_detail.png   blades + lasers, shell hidden
    <name>.step .stl .glb       CAD / mesh files   <name>.json              the editable recipe (variables + formulas)
    <name>_bom.csv              bill of materials  <name>_report.txt        the design report

Every number in the block below becomes a NAMED VARIABLE inside the model (lower-case: chassis_length, radiator_wingspan ...)
and the shapes are built from formulas that use them. So the saved .json is fully parametric: load it with the Claude
connector and use set_variable("radiator_wingspan", 30000) and the whole satellite rebuilds.

All dimensions are MILLIMETRES, masses are KILOGRAMS. Axes:  X = front (+) / rear (-),  Y = left (+) / right (-),
Z = top (+) / bottom (-).  The sun is assumed to lie along +Y.
"""

# ==========================================================================================================
#  >>> CHANGE THESE NUMBERS <<<        (everything below this block is derived from them)
# ==========================================================================================================

# --- 1. CHASSIS: the central datacentre core --------------------------------------------------------------
CHASSIS_LENGTH = 6000        # mm, front to back (X)
CHASSIS_WIDTH = 2400         # mm, left to right (Y)
CHASSIS_HEIGHT = 2400        # mm, bottom to top (Z)
CHASSIS_WALL = 15            # mm, thickness of the structural wall (hollow inside)
CHASSIS_EDGE_RADIUS = 25     # mm, rounding of the chassis edges (0 = sharp edges)
PLUMBING_MASS_KG = 1500      # kg, pumps, coolant and pipework inside (spread through the interior; not drawn)

# --- 1b. COMPUTE BLADES inside the chassis (stand-in blocks, so the mass is counted) ----------------------
BLADE_COUNT = 12             # number of blades in the row
BLADE_THICKNESS = 120        # mm, thickness of one blade (along the row, X)
BLADE_PITCH = 180            # mm, centre-to-centre spacing along the row (must be >= thickness)
BLADE_HEIGHT = 1600          # mm, how tall each blade is (Z)
BLADE_SIDE_CLEARANCE = 60    # mm, gap between blade ends and the chassis walls (left and right)

# --- 2. THERMAL RADIATORS: two panels, one each side (+Y and -Y) ------------------------------------------
RADIATOR_WINGSPAN = 22000    # mm, TIP-TO-TIP length across both panels and the chassis between them
RADIATOR_CHORD = 6000        # mm, how wide each panel is along the satellite's length (X)
RADIATOR_THICKNESS = 40      # mm, panel thickness (the thin direction is Z, so they lie flat, edge-on to the sun)
RADIATOR_TILT_DEG = 0        # degrees, hinge both panels up (+) / down (-) about where they attach (0 = flat)

# --- 3. OPTICAL INTER-SATELLITE LINKS (OISL): laser transceivers on the front (+X) and rear (-X) ------------
TRANSCEIVER_RADIUS = 250     # mm, radius of each telescope barrel (the optics and fast steering mirror live here)
TRANSCEIVER_LENGTH = 800     # mm, length of each telescope barrel
GIMBAL_RADIUS_FACTOR = 1.4   # gimbal housing radius = this x telescope radius (holds the steering mechanism)
GIMBAL_LENGTH = 300          # mm, length of the gimbal housing between the chassis and the telescope
OISL_PORT_FACTOR = 0.8       # optical feed-through hole in the chassis wall = this x telescope diameter

# --- 4. SOLAR ARRAYS: two wings on the +Z / -Z faces, flat in the X-Z plane (perpendicular to the radiators) --
SOLAR_WING_LENGTH = 14000    # mm, how far each wing reaches out from the chassis (Z)
SOLAR_WING_WIDTH = 12000     # mm, how wide each wing is (X)
SOLAR_THICKNESS = 40         # mm, wing thickness (the thin direction is Y: the big faces look at the sun)

# --- Operating assumptions (used only for the design report, not for the shapes) --------------------------
RADIATOR_TEMP_K = 330        # K, radiator surface temperature (330 K = 57 C)
RADIATOR_EMISSIVITY = 0.85   # how well the surface radiates heat (0 to 1)
SOLAR_CELL_EFFICIENCY = 0.30     # modern triple-junction space cells
SOLAR_PACKING_FACTOR = 0.90      # fraction of the wing actually covered by cells
SOLAR_END_OF_LIFE_FACTOR = 0.85  # allowance for radiation damage and ageing

# ==========================================================================================================
#  End of the numbers you are meant to change.  Everything below builds the model.
# ==========================================================================================================

PARAMS = {k: v for k, v in dict(globals()).items() if k.isupper() and not k.startswith("_")}

import argparse
import platform
import subprocess
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                          # lets "from satcad import ..." work from any folder

from satcad import Design, DesignError                 # noqa: E402  (the design engine)
from satcad.power import size_solar_array               # noqa: E402
from satcad.render import render_png                    # noqa: E402
from satcad.thermal import STEFAN_BOLTZMANN             # noqa: E402
from satcad.viewer import write_viewer                  # noqa: E402


# ==========================================================================================================
#  CHECK THE NUMBERS BEFORE BUILDING
# ==========================================================================================================
def validate(p: dict) -> None:
    """Stop with a plain-English message if the numbers can't make a sensible satellite."""
    for key, value in p.items():
        if key == "RADIATOR_TILT_DEG" or not isinstance(value, (int, float)):
            continue
        zero_ok = key in ("BLADE_SIDE_CLEARANCE", "PLUMBING_MASS_KG", "CHASSIS_EDGE_RADIUS")   # may be 0, but not negative
        if value < 0 or (value == 0 and not zero_ok):
            raise DesignError(f"{key} must be {'0 or more' if zero_ok else 'greater than 0'} (you gave {value}).")
    if p["CHASSIS_WALL"] * 2 >= min(p["CHASSIS_LENGTH"], p["CHASSIS_WIDTH"], p["CHASSIS_HEIGHT"]):
        raise DesignError("CHASSIS_WALL is too thick: twice the wall must be smaller than every chassis dimension.")
    if p["CHASSIS_EDGE_RADIUS"] * 2 >= min(p["CHASSIS_LENGTH"], p["CHASSIS_WIDTH"], p["CHASSIS_HEIGHT"]):
        raise DesignError("CHASSIS_EDGE_RADIUS is too large for the chassis size.")
    if p["RADIATOR_WINGSPAN"] <= p["CHASSIS_WIDTH"]:
        raise DesignError(f"RADIATOR_WINGSPAN ({p['RADIATOR_WINGSPAN']}) is the tip-to-tip span, so it must be bigger "
                          f"than CHASSIS_WIDTH ({p['CHASSIS_WIDTH']}), otherwise the panels have no length.")
    inner_l = p["CHASSIS_LENGTH"] - 2 * p["CHASSIS_WALL"]
    inner_w = p["CHASSIS_WIDTH"] - 2 * p["CHASSIS_WALL"]
    inner_h = p["CHASSIS_HEIGHT"] - 2 * p["CHASSIS_WALL"]
    if p["BLADE_PITCH"] < p["BLADE_THICKNESS"]:
        raise DesignError("BLADE_PITCH must be at least BLADE_THICKNESS, otherwise neighbouring blades overlap.")
    row = (p["BLADE_COUNT"] - 1) * p["BLADE_PITCH"] + p["BLADE_THICKNESS"]
    if row > inner_l:
        raise DesignError(f"The {p['BLADE_COUNT']} blades need {row} mm but the chassis interior is only {inner_l} mm long. "
                          "Use fewer blades, a smaller pitch, or a longer chassis.")
    if p["BLADE_HEIGHT"] > inner_h:
        raise DesignError(f"BLADE_HEIGHT ({p['BLADE_HEIGHT']}) is taller than the chassis interior ({inner_h}).")
    if inner_w - 2 * p["BLADE_SIDE_CLEARANCE"] <= 0:
        raise DesignError("BLADE_SIDE_CLEARANCE is too large: no room left for the blades across the chassis.")
    if 2 * p["TRANSCEIVER_RADIUS"] * p["GIMBAL_RADIUS_FACTOR"] >= min(p["CHASSIS_WIDTH"], p["CHASSIS_HEIGHT"]):
        raise DesignError("The transceiver gimbal is wider than the chassis face it mounts on. Reduce TRANSCEIVER_RADIUS "
                          "or GIMBAL_RADIUS_FACTOR.")
    if p["GIMBAL_RADIUS_FACTOR"] < 1:
        raise DesignError("GIMBAL_RADIUS_FACTOR must be 1 or more (the gimbal housing is at least as wide as the telescope).")
    if p["OISL_PORT_FACTOR"] > p["GIMBAL_RADIUS_FACTOR"] * 2:
        raise DesignError("OISL_PORT_FACTOR is too large: the feed-through hole would be wider than the gimbal housing.")


def new_design(name: str, p: dict) -> Design:
    """An empty design that already holds every number from the block above as a NAMED VARIABLE (lower-case)."""
    d = Design(name)
    for key, value in p.items():
        d.set_variable(key.lower(), value)
    return d


# ==========================================================================================================
#  SUBSYSTEM 1: CHASSIS  (hollow core + compute blades)
#  Shapes are written as FORMULAS ("=chassis_length") that read the variables, so editing a variable rebuilds them.
# ==========================================================================================================
def add_chassis(d: Design, p: dict) -> None:
    # The core: a hollow aluminium box with rounded edges. "wall" makes it a shell; internal_mass_kg is the pumps/coolant
    # mass, spread through the interior when the inertia is computed.
    d.add("chassis", "chassis", length="=chassis_length", width="=chassis_width", height="=chassis_height",
          wall="=chassis_wall", fillet="=chassis_edge_radius", internal_mass_kg="=plumbing_mass_kg", material="aluminum_6061")
    # Optical feed-throughs: a round hole through the wall under each laser terminal, so the beam path reaches the optics bench.
    for sign, end in (("+", "front"), ("-", "rear")):
        d.add("cutout", f"oisl_{end}_port", target="chassis", side=f"{sign}x", shape="circle",
              diameter="=oisl_port_factor * transceiver_radius * 2", depth="=chassis_wall")


def add_blades(d: Design, p: dict) -> None:
    """The compute blades: upright slabs standing on the inside floor, in a row centred along X. We model the first blade
    and repeat it with a PATTERN. (Because they rest on the floor they touch the chassis, so the 'floating parts' check
    stays quiet.)"""
    d.add("box", "compute_blade_1", length="=blade_thickness",
          width="=chassis_width - 2 * chassis_wall - 2 * blade_side_clearance", height="=blade_height",
          x="=-(blade_count - 1) / 2 * blade_pitch", y=0, z="=-chassis_height / 2 + chassis_wall + blade_height / 2",
          material="generic_equipment", role="payload")
    if p["BLADE_COUNT"] > 1:
        # copies are named compute_blade_2 ... compute_blade_<count>
        d.add("pattern", "compute_blade", source="compute_blade_1", mode="linear", count="=blade_count", dx="=blade_pitch")


# ==========================================================================================================
#  SUBSYSTEM 2: THERMAL RADIATORS  (two flat panels, symmetric about the chassis)
# ==========================================================================================================
def add_radiators(d: Design, p: dict) -> None:
    # WINGSPAN is tip to tip, so each panel only needs to cover the distance from the chassis wall to the tip.
    # side "+y": the panel sticks out of the left face. The thin direction defaults to Z, so the panel lies flat in the
    # X-Y plane. attach_to makes it follow the chassis if the chassis is resized.
    d.add("panel", "radiator_left", attach_to="chassis", side="+y", length="=(radiator_wingspan - chassis.width) / 2",
          width="=radiator_chord", thickness="=radiator_thickness", tilt_deg="=radiator_tilt_deg",
          material="aluminum_honeycomb", role="radiator")
    # The right radiator is a true MIRROR of the left one across the XZ plane, so the pair can never drift out of symmetry.
    d.add("mirror", "radiator_right", source="radiator_left", plane="zx")


# ==========================================================================================================
#  SUBSYSTEM 3: OPTICAL INTER-SATELLITE LINKS (OISL)  (a gimbal plus a telescope barrel, front and rear)
# ==========================================================================================================
def add_oisl(d: Design, p: dict) -> None:
    # Gimbal housing: sits directly on the chassis front face (holds the pointing mechanism and Fast Steering Mirror).
    d.add("cylinder", "oisl_front_gimbal", radius="=transceiver_radius * gimbal_radius_factor", height="=gimbal_length",
          axis="x", x="=chassis_length / 2 + gimbal_length / 2", y=0, z=0, material="titanium_ti6al4v", role="structure")
    # Telescope barrel: the laser optics, pointing away from the satellite along +X.
    d.add("cylinder", "oisl_front_telescope", radius="=transceiver_radius", height="=transceiver_length", axis="x",
          x="=chassis_length / 2 + gimbal_length + transceiver_length / 2", y=0, z=0, material="cfrp", role="antenna")
    # The rear terminal is the front one mirrored across the YZ plane.
    d.add("mirror", "oisl_rear_gimbal", source="oisl_front_gimbal", plane="yz")
    d.add("mirror", "oisl_rear_telescope", source="oisl_front_telescope", plane="yz")


# ==========================================================================================================
#  SUBSYSTEM 4: SOLAR ARRAYS  (two wings on the +Z / -Z faces, perpendicular to the radiators)
# ==========================================================================================================
def add_solar_arrays(d: Design, p: dict) -> None:
    # Radiators lie in the X-Y plane (thin in Z). The solar wings lie in the X-Z plane (thin in Y, via thin_axis="y").
    # Planes at right angles = "perpendicular". The big solar faces look along Y toward the sun, and the radiators sit
    # edge-on to the sun, so they neither shade the arrays nor bake in direct sunlight.
    d.add("panel", "solar_wing_top", attach_to="chassis", side="+z", length="=solar_wing_length", width="=solar_wing_width",
          thickness="=solar_thickness", thin_axis="y", material="aluminum_honeycomb", role="solar")
    d.add("mirror", "solar_wing_bottom", source="solar_wing_top", plane="xy")          # the bottom wing mirrors the top one


# ==========================================================================================================
#  PUT IT TOGETHER
# ==========================================================================================================
def build_core_detail(overrides: dict | None = None, name: str = "orbital_datacentre_core") -> Design:
    """A cut-away view for the picture only: the compute blades and optical terminals with the chassis shell hidden."""
    p = {**PARAMS, **(overrides or {})}
    validate(p)
    d = new_design(name, p)
    add_blades(d, p)
    add_oisl(d, p)
    return d


def build(overrides: dict | None = None, name: str = "orbital_datacentre") -> Design:
    """Build the satellite. `overrides` replaces any of the variables at the top, e.g. {"RADIATOR_WINGSPAN": 30000}."""
    p = {**PARAMS, **(overrides or {})}
    validate(p)
    d = new_design(name, p)
    add_chassis(d, p)
    add_blades(d, p)
    add_radiators(d, p)
    add_oisl(d, p)
    add_solar_arrays(d, p)
    return d


# ==========================================================================================================
#  DESIGN REPORT: numbers that tell you whether the proportions make physical sense
# ==========================================================================================================
def report(d: Design, overrides: dict | None = None) -> tuple[str, dict]:
    p = {**PARAMS, **(overrides or {})}
    L, W, H = p["CHASSIS_LENGTH"], p["CHASSIS_WIDTH"], p["CHASSIS_HEIGHT"]
    m2 = 1e-6                                              # mm^2 -> m^2

    chassis_surface = 2 * (L * W + L * H + W * H) * m2
    chassis_plan = L * W * m2
    panel_len = (p["RADIATOR_WINGSPAN"] - W) / 2
    rad_area_one_face = 2 * panel_len * p["RADIATOR_CHORD"] * m2          # both panels, one face each
    flux = p["RADIATOR_EMISSIVITY"] * STEFAN_BOLTZMANN * (p["RADIATOR_TEMP_K"] ** 4 - 4.0 ** 4)
    reject_kw = flux * 2 * rad_area_one_face / 1000                       # both big faces radiate to space

    solar_area = 2 * p["SOLAR_WING_LENGTH"] * p["SOLAR_WING_WIDTH"] * m2
    w_per_m2 = size_solar_array(1000, p["SOLAR_CELL_EFFICIENCY"], p["SOLAR_PACKING_FACTOR"],
                                p["SOLAR_END_OF_LIFE_FACTOR"])["usable_w_per_m2"]
    power_kw = solar_area * w_per_m2 / 1000

    meas, check = d.measure(), d.check()
    area_ratio = rad_area_one_face / chassis_plan
    balance = reject_kw / power_kw if power_kw else 0
    mom = meas["principal_moments_kg_m2"]
    facts = dict(radiator_area_m2=rad_area_one_face, chassis_plan_m2=chassis_plan, chassis_surface_m2=chassis_surface,
                 radiator_to_chassis_plan_ratio=area_ratio, heat_rejection_kw=reject_kw, solar_area_m2=solar_area,
                 solar_power_kw=power_kw, heat_balance=balance, mass_kg=meas["total_mass_kg"], check_ok=check["ok"],
                 principal_moments_kg_m2=mom, centre_of_mass_mm=meas["centre_of_mass_mm"],
                 inertia_kg_m2=meas["inertia_kg_m2"])

    i = meas["inertia_kg_m2"]
    lines = [f"DESIGN REPORT: {d.name}", "=" * 64,
             "Parts: " + str(meas["parts"]) + "   Total mass: " + f"{meas['total_mass_kg']:,.0f} kg",
             "Overall size: " + " x ".join(f"{v / 1000:.1f}" for v in meas["overall_size_mm"]) + " m  (X x Y x Z)",
             "Mass by role (kg): " + ", ".join(f"{k} {v:,.0f}" for k, v in meas["mass_by_role_kg"].items()),
             "",
             "MASS PROPERTIES (about the centre of mass; from the real solids)",
             f"  Centre of mass: ({meas['centre_of_mass_mm'][0] / 1000:.3f}, {meas['centre_of_mass_mm'][1] / 1000:.3f}, "
             f"{meas['centre_of_mass_mm'][2] / 1000:.3f}) m from the origin",
             f"  Inertia tensor (kg m2): Ixx {i[0][0]:,.0f}  Iyy {i[1][1]:,.0f}  Izz {i[2][2]:,.0f}   "
             f"Ixy {i[0][1]:,.0f}  Ixz {i[0][2]:,.0f}  Iyz {i[1][2]:,.0f}",
             f"  Principal moments (kg m2): {mom[0]:,.0f} / {mom[1]:,.0f} / {mom[2]:,.0f}   "
             f"(largest / smallest = {mom[2] / mom[0]:.1f})",
             "",
             "RADIATORS",
             f"  Each panel: {panel_len / 1000:.2f} m out x {p['RADIATOR_CHORD'] / 1000:.2f} m wide; "
             f"total {rad_area_one_face:.1f} m2 (one face of both panels)",
             f"  vs chassis: {area_ratio:.1f}x the chassis's top-down footprint ({chassis_plan:.1f} m2), "
             f"{rad_area_one_face * 2 / chassis_surface:.1f}x its whole outer surface counting both radiator faces",
             f"  At {p['RADIATOR_TEMP_K']} K, emissivity {p['RADIATOR_EMISSIVITY']}: can reject about {reject_kw:,.0f} kW "
             "(lower bound; ignores sunlight and Earth heat)",
             "",
             "SOLAR ARRAYS",
             f"  Total {solar_area:.0f} m2 -> about {power_kw:,.0f} kW electrical at end of life ({w_per_m2:.0f} W/m2)",
             "",
             "HEAT BALANCE (all electrical power ends up as heat)",
             f"  radiators can reject {reject_kw:,.0f} kW vs {power_kw:,.0f} kW generated: ratio {balance:.2f}"]
    if balance < 1:
        lines.append("  WARNING: the radiators are too small for this much solar power. Widen RADIATOR_WINGSPAN or RADIATOR_CHORD, "
                     "raise RADIATOR_TEMP_K, or shrink the solar wings.")
    if area_ratio < 3:
        lines.append("  NOTE: the radiators are less than 3x the chassis footprint; real orbital datacentres need them much larger.")
    lines += ["", "OPTICAL LINKS",
              f"  Telescope radius {p['TRANSCEIVER_RADIUS']} mm (barrel diameter {2 * p['TRANSCEIVER_RADIUS']} mm). "
              "The usable optical aperture is a bit smaller than the barrel; it is the `--tx-aperture-m` value in the "
              "Orbital-Datacentre link simulator.",
              "", "CHECKS", "  " + check["advice"]]
    for f in check["floating_parts"]:
        lines.append(f"  Floating part: {f['part']} ({f['nearest_gap_mm']} mm from anything)")
    for o in check["overlaps"]:
        lines.append(f"  OVERLAP: {o['a']} and {o['b']} ({o['overlap_cm3']} cm3)")
    lines += ["", "NOT INCLUDED: structural, vibration (launch loads), thermal-network and orbital analysis. Mass and inertia come "
              "from simple material densities and idealised blocks, so treat them as concept-stage estimates."]
    return "\n".join(lines), facts


# ==========================================================================================================
#  COMMAND LINE
# ==========================================================================================================
WHOLE_NUMBERS = {"BLADE_COUNT"}                          # settings that cannot be fractional


def _parse_overrides(pairs: list[str]) -> dict:
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--set expects KEY=VALUE, got '{pair}'.")
        key, raw = pair.split("=", 1)
        key = key.strip().upper()
        if key not in PARAMS:
            raise SystemExit(f"Unknown setting '{key}'. Settings: {', '.join(sorted(PARAMS))}")
        try:
            value = float(raw)
        except ValueError:
            raise SystemExit(f"{key} must be a number, got '{raw}'.")
        if key in WHOLE_NUMBERS and not value.is_integer():
            raise SystemExit(f"{key} must be a whole number, got '{raw}'.")
        out[key] = int(value) if value.is_integer() else value        # "22000" and "22000.0" both work
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the orbital AI datacentre model with SatCAD.")
    ap.add_argument("--name", default="orbital_datacentre", help="name for the design and its output files")
    ap.add_argument("--out", default=str(HERE / "output"), help="output folder (default: ./output)")
    ap.add_argument("--set", action="append", metavar="KEY=VALUE", help="override a variable, e.g. RADIATOR_WINGSPAN=30000")
    ap.add_argument("--view", action="store_true", help="open the interactive 3D viewer when done")
    args = ap.parse_args(argv)

    overrides = _parse_overrides(args.set)
    try:
        design = build(overrides, args.name)
    except DesignError as e:
        print(f"Can't build this design: {e}", file=sys.stderr)
        return 1

    folder = Path(args.out) / args.name
    folder.mkdir(parents=True, exist_ok=True)
    text, _ = report(design, overrides)
    picture = folder / f"{args.name}.png"
    picture.write_bytes(render_png(design))
    detail = folder / f"{args.name}_core_detail.png"
    detail.write_bytes(render_png(build_core_detail(overrides, f"{args.name}_core_detail (chassis shell hidden)")))
    files = design.export(folder, args.name)
    design.save(folder / f"{args.name}.json")
    bom = design.export_bom(folder / f"{args.name}_bom.csv")
    viewer = write_viewer(design, folder / f"{args.name}_viewer.html")
    (folder / f"{args.name}_report.txt").write_text(text + "\n")

    print(text)
    print("\nFILES WRITTEN to", folder)
    print(f"  viewer  : {Path(viewer).name}  (interactive 3D: orbit, zoom, click parts)")
    print("  picture :", picture.name, "  (4 views of the whole satellite)")
    print("  detail  :", detail.name, "  (compute blades and laser terminals, chassis shell hidden)")
    for fmt, info in files.items():
        print(f"  {fmt:8}: {Path(info['path']).name}  ({info['kilobytes']} KB)")
    print(f"  recipe  : {args.name}.json  (editable: variables + formulas; reload with Design().load(...))")
    print(f"  bom     : {Path(bom).name}  (bill of materials)")
    print(f"  report  : {args.name}_report.txt")
    if args.view:
        try:
            opened = webbrowser.open(Path(viewer).resolve().as_uri())
        except Exception:
            opened = False
        if not opened:
            print("Open the viewer yourself:", viewer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
