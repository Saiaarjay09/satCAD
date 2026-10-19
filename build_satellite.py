"""
ORBITAL AI DATACENTRE: parametric satellite model, built with SatCAD
====================================================================

Builds a 3D model of a space-based AI compute node, organised as real SUBSYSTEMS (assemblies):

    structure   a large hollow rectangular chassis (the primary structure), with optical feed-through holes
    compute     a row of AI compute blades inside the chassis
    thermal     two big flat radiator panels on hinges, one on each side
    optical     two laser transceivers (inter-satellite optical links), front and rear: each a bolted flange,
                a gimbal housing and a telescope barrel, held on by M6 screws on a bolt circle
    power       two flat solar wings on hinges, perpendicular to the radiators

HOW TO RUN (from this folder, with the virtual environment active):

    python build_satellite.py                 build it, write the files, print a design report
    python build_satellite.py --view          ...and open the interactive 3D viewer afterwards
    python build_satellite.py --set RADIATOR_WINGSPAN=30000 --name wide_radiators
                                              try a different size without editing the file
    python build_satellite.py --set RADIATOR_DEPLOY_ANGLE_DEG=90 --name radiators_up
                                              swing the radiators about their hinges

OUTPUT (in ./output/<name>/):
    <name>_viewer.html          interactive 3D model: orbit, zoom, click parts, hide parts, explode, hinge sliders
    <name>.png                  4 views            <name>_core_detail.png   blades + lasers, shell hidden
    <name>.step .stl .glb       CAD / mesh files   <name>.json              the editable recipe (variables + formulas)
    <name>_bom.csv              bill of materials  <name>_report.txt        the design report (mass, inertia, modes)
    <name>_modes.html           animated vibration mode shapes (shell finite-element analysis, BETA)

Every number in the block below becomes a NAMED VARIABLE inside the model (lower-case: chassis_length, radiator_wingspan ...)
and the shapes are built from formulas that use them. So the saved .json is fully parametric: load it with the Claude
connector and use set_variable("radiator_wingspan", 30000) and the whole satellite rebuilds.

All dimensions are MILLIMETRES, masses are KILOGRAMS, angles DEGREES. Axes:  X = front (+) / rear (-),  Y = left (+) / right (-),
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
RADIATOR_TILT_DEG = 0        # degrees, fixed tilt of both panels up (+) / down (-) at the hinge (0 = flat)
RADIATOR_DEPLOY_ANGLE_DEG = 0    # degrees, HINGE angle: 0 = fully deployed, up to 90 = swung up beside the chassis

# --- 3. OPTICAL INTER-SATELLITE LINKS (OISL): laser transceivers on the front (+X) and rear (-X) ------------
TRANSCEIVER_RADIUS = 250     # mm, radius of each telescope barrel (the optics and fast steering mirror live here)
TRANSCEIVER_LENGTH = 800     # mm, length of each telescope barrel
GIMBAL_RADIUS_FACTOR = 1.4   # gimbal housing radius = this x telescope radius (holds the steering mechanism)
GIMBAL_LENGTH = 300          # mm, length of the gimbal housing between the flange and the telescope
OISL_PORT_FACTOR = 0.8       # optical feed-through hole in the chassis wall = this x telescope diameter
FLANGE_THICKNESS = 20        # mm, thickness of the mounting flange between the chassis and the gimbal
FLANGE_RADIUS_FACTOR = 1.15  # flange outer radius = this x gimbal radius (the bolts sit in the ring this leaves)
OISL_BOLT_COUNT = 8          # M6 screws per terminal on a bolt circle (0 = no bolts)

# --- 4. SOLAR ARRAYS: two wings on the +Z / -Z faces, flat in the X-Z plane (perpendicular to the radiators) --
SOLAR_WING_LENGTH = 14000    # mm, how far each wing reaches out from the chassis (Z)
SOLAR_WING_WIDTH = 12000     # mm, how wide each wing is (X)
SOLAR_THICKNESS = 40         # mm, wing thickness (the thin direction is Y: the big faces look at the sun)
SOLAR_DEPLOY_ANGLE_DEG = 0   # degrees, HINGE angle: 0 = fully deployed, up to 90 = swung over toward the radiators

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
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                          # lets "from satcad import ..." work from any folder

from satcad import Design, DesignError                 # noqa: E402  (the design engine)
from satcad.fem import from_design                      # noqa: E402  (shell finite-element analysis, beta)
from satcad.power import size_solar_array               # noqa: E402
from satcad.render import render_png                    # noqa: E402
from satcad.thermal import STEFAN_BOLTZMANN             # noqa: E402
from satcad.viewer import write_mode_viewer, write_viewer  # noqa: E402

BOLT = "M6"                                            # screw size for the optical flanges (ISO 4762)
TAP_ENGAGE = 3                                         # mm of the wall left untapped (screw bites wall - this)


# ==========================================================================================================
#  CHECK THE NUMBERS BEFORE BUILDING
# ==========================================================================================================
def validate(p: dict) -> None:
    """Stop with a plain-English message if the numbers can't make a sensible satellite."""
    for key, value in p.items():
        if key in ("RADIATOR_TILT_DEG", "RADIATOR_DEPLOY_ANGLE_DEG", "SOLAR_DEPLOY_ANGLE_DEG") or not isinstance(value, (int, float)):
            continue
        zero_ok = key in ("BLADE_SIDE_CLEARANCE", "PLUMBING_MASS_KG", "CHASSIS_EDGE_RADIUS", "OISL_BOLT_COUNT")   # may be 0
        if value < 0 or (value == 0 and not zero_ok):
            raise DesignError(f"{key} must be {'0 or more' if zero_ok else 'greater than 0'} (you gave {value}).")
    for key in ("RADIATOR_DEPLOY_ANGLE_DEG", "SOLAR_DEPLOY_ANGLE_DEG"):
        if not 0 <= p[key] <= 90:
            raise DesignError(f"{key} must be between 0 (deployed) and 90 degrees (you gave {p[key]}).")
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
    gimbal_r = p["TRANSCEIVER_RADIUS"] * p["GIMBAL_RADIUS_FACTOR"]
    if p["GIMBAL_RADIUS_FACTOR"] < 1:
        raise DesignError("GIMBAL_RADIUS_FACTOR must be 1 or more (the gimbal housing is at least as wide as the telescope).")
    if p["FLANGE_RADIUS_FACTOR"] < 1:
        raise DesignError("FLANGE_RADIUS_FACTOR must be 1 or more (the flange is at least as wide as the gimbal).")
    if 2 * gimbal_r * p["FLANGE_RADIUS_FACTOR"] >= min(p["CHASSIS_WIDTH"], p["CHASSIS_HEIGHT"]):
        raise DesignError("The transceiver flange is wider than the chassis face it mounts on. Reduce TRANSCEIVER_RADIUS, "
                          "GIMBAL_RADIUS_FACTOR or FLANGE_RADIUS_FACTOR.")
    if p["OISL_PORT_FACTOR"] * p["TRANSCEIVER_RADIUS"] > gimbal_r:
        raise DesignError("OISL_PORT_FACTOR is too large: the feed-through hole would be wider than the gimbal housing.")
    if p["OISL_BOLT_COUNT"]:
        n = p["OISL_BOLT_COUNT"]
        if int(n) != n or not 3 <= n <= 64:
            raise DesignError("OISL_BOLT_COUNT must be 0 (no bolts) or a whole number from 3 to 64.")
        ring = gimbal_r * (p["FLANGE_RADIUS_FACTOR"] - 1)                      # radial width of the flange ring outside the gimbal
        if ring < 16:
            raise DesignError(f"The flange ring outside the gimbal is only {ring:.1f} mm wide, too narrow for {BOLT} screw heads. "
                              "Raise FLANGE_RADIUS_FACTOR, or set OISL_BOLT_COUNT to 0.")
        pitch = 2 * 3.14159265 * (gimbal_r * (1 + p["FLANGE_RADIUS_FACTOR"]) / 2) / n
        if pitch < 20:
            raise DesignError(f"{int(n)} screws would be only {pitch:.1f} mm apart on the bolt circle. Use fewer screws.")
        if p["FLANGE_THICKNESS"] + p["CHASSIS_WALL"] - TAP_ENGAGE <= 0:
            raise DesignError("FLANGE_THICKNESS and CHASSIS_WALL leave no room for the screws.")


def new_design(name: str, p: dict) -> Design:
    """An empty design that already holds every number from the block above as a NAMED VARIABLE (lower-case)."""
    d = Design(name)
    for key, value in p.items():
        d.set_variable(key.lower(), value)
    return d


# ==========================================================================================================
#  SUBSYSTEM: STRUCTURE (hollow chassis)
#  Shapes are written as FORMULAS ("=chassis_length") that read the variables, so editing a variable rebuilds them.
# ==========================================================================================================
def add_chassis(d: Design, p: dict) -> None:
    # The core: a hollow aluminium box with rounded edges. "wall" makes it a shell; internal_mass_kg is the pumps/coolant
    # mass, spread through the interior when the inertia and the vibration analysis are computed.
    d.add("chassis", "chassis", length="=chassis_length", width="=chassis_width", height="=chassis_height",
          wall="=chassis_wall", fillet="=chassis_edge_radius", internal_mass_kg="=plumbing_mass_kg", material="aluminum_6061")
    for sign, end in (("+", "front"), ("-", "rear")):
        # Optical feed-through: a round hole through the wall under each laser terminal.
        d.add("cutout", f"oisl_{end}_port", target="chassis", side=f"{sign}x", shape="circle",
              diameter="=oisl_port_factor * transceiver_radius * 2", depth="=chassis_wall")
        if p["OISL_BOLT_COUNT"]:
            # Tapped holes (ISO M6) on the bolt circle, for the screws that hold the flange.
            d.add("hole", f"oisl_{end}_tapped", target="chassis", side=f"{sign}x", standard=BOLT, hole_type="tapped",
                  depth=f"=chassis_wall - {TAP_ENGAGE}", circle_count="=oisl_bolt_count",
                  circle_diameter="=transceiver_radius * gimbal_radius_factor * (1 + flange_radius_factor)")


# ==========================================================================================================
#  SUBSYSTEM: COMPUTE (blades inside the chassis)
# ==========================================================================================================
def add_blades(d: Design, p: dict) -> None:
    """The compute blades: upright slabs standing on the inside floor, in a row centred along X. We model the first blade
    and repeat it with a PATTERN. (Because they rest on the floor they touch the chassis, so the 'floating parts' check
    stays quiet.)"""
    d.add("assembly", "compute")
    d.add("box", "compute_blade_1", length="=blade_thickness",
          width="=chassis_width - 2 * chassis_wall - 2 * blade_side_clearance", height="=blade_height",
          x="=-(blade_count - 1) / 2 * blade_pitch", y=0, z="=-chassis_height / 2 + chassis_wall + blade_height / 2",
          material="generic_equipment", role="payload", assembly="compute")
    if p["BLADE_COUNT"] > 1:
        # copies are named compute_blade_2 ... compute_blade_<count>
        d.add("pattern", "compute_blade", source="compute_blade_1", mode="linear", count="=blade_count", dx="=blade_pitch")


# ==========================================================================================================
#  SUBSYSTEM: THERMAL (two flat radiator panels on hinges, symmetric about the chassis)
# ==========================================================================================================
def add_radiators(d: Design, p: dict) -> None:
    d.add("assembly", "thermal")
    # Each radiator is its own sub-assembly with a HINGE along the edge where it meets the chassis (axis X). Angle 0 = deployed
    # flat. The pin sits on the panel's face that it swings toward, so the panel corner never swings into the wall.
    # WINGSPAN is tip to tip, so each panel only needs to cover the distance from the chassis wall to the tip.
    d.add("assembly", "radiator_left", parent="thermal", joint_axis="x", angle_deg="=radiator_deploy_angle_deg",
          pivot_y="=chassis_width / 2", pivot_z="=radiator_thickness / 2", angle_min=0, angle_max=90)
    d.add("panel", "radiator_left_panel", attach_to="chassis", side="+y", length="=(radiator_wingspan - chassis.width) / 2",
          width="=radiator_chord", thickness="=radiator_thickness", tilt_deg="=radiator_tilt_deg",
          material="aluminum_honeycomb", role="radiator", assembly="radiator_left")
    d.add("assembly", "radiator_right", parent="thermal", joint_axis="x", angle_deg="=-radiator_deploy_angle_deg",
          pivot_y="=-chassis_width / 2", pivot_z="=radiator_thickness / 2", angle_min=-90, angle_max=0)
    d.add("panel", "radiator_right_panel", attach_to="chassis", side="-y", length="=(radiator_wingspan - chassis.width) / 2",
          width="=radiator_chord", thickness="=radiator_thickness", tilt_deg="=-radiator_tilt_deg",
          material="aluminum_honeycomb", role="radiator", assembly="radiator_right")


# ==========================================================================================================
#  SUBSYSTEM: OPTICAL (laser terminals: flange + gimbal + telescope, bolted to the chassis)
# ==========================================================================================================
def add_oisl(d: Design, p: dict) -> None:
    d.add("assembly", "optical")
    for sign, end in ((+1, "front"), (-1, "rear")):
        asm = f"optical_{end}"
        d.add("assembly", asm, parent="optical")
        s = "+" if sign > 0 else "-"
        # Mounting flange: a ring on the chassis face. plane "yz" extrudes along +x, so the rear flange starts a thickness
        # further back and grows toward the chassis. wall = ring width = outer radius - port radius (so it clears the port).
        flange_r = "transceiver_radius * gimbal_radius_factor * flange_radius_factor"
        start = "chassis_length / 2" if sign > 0 else "-(chassis_length / 2 + flange_thickness)"
        d.add("extrude", f"oisl_{end}_flange", profile="circle", radius=f"={flange_r}",
              wall=f"={flange_r} - oisl_port_factor * transceiver_radius", depth="=flange_thickness", plane="yz",
              x=f"={start}", material="aluminum_7075", role="structure", assembly=asm)
        if p["OISL_BOLT_COUNT"]:
            d.add("hole", f"oisl_{end}_flange_holes", target=f"oisl_{end}_flange", side=f"{s}x", standard=BOLT,
                  hole_type="clearance", circle_count="=oisl_bolt_count",
                  circle_diameter="=transceiver_radius * gimbal_radius_factor * (1 + flange_radius_factor)")
        base = f"chassis_length / 2 + flange_thickness"
        # Gimbal housing: sits on the flange (holds the pointing mechanism and the Fast Steering Mirror).
        d.add("cylinder", f"oisl_{end}_gimbal", radius="=transceiver_radius * gimbal_radius_factor", height="=gimbal_length",
              axis="x", x=f"={s}({base} + gimbal_length / 2)", y=0, z=0, material="titanium_ti6al4v", role="structure",
              assembly=asm)
        # Telescope barrel: the laser optics, pointing away from the satellite along +X (front) or -X (rear).
        d.add("cylinder", f"oisl_{end}_telescope", radius="=transceiver_radius", height="=transceiver_length", axis="x",
              x=f"={s}({base} + gimbal_length + transceiver_length / 2)", y=0, z=0, material="cfrp", role="antenna",
              assembly=asm)
        if p["OISL_BOLT_COUNT"]:
            # One screw, repeated around the axis by a PATTERN. Head seats on the flange's outer face; the tip points into the chassis.
            d.add("fastener", f"oisl_{end}_bolt_1", size=BOLT, length=f"=flange_thickness + chassis_wall - {TAP_ENGAGE}",
                  axis=f"{'-' if sign > 0 else '+'}x", x=f"={s}({base})",
                  y="=transceiver_radius * gimbal_radius_factor * (1 + flange_radius_factor) / 2", z=0, assembly=asm)
            d.add("pattern", f"oisl_{end}_bolt", source=f"oisl_{end}_bolt_1", mode="circular", count="=oisl_bolt_count", axis="x")


# ==========================================================================================================
#  SUBSYSTEM: POWER (two solar wings on hinges, perpendicular to the radiators)
# ==========================================================================================================
def add_solar_arrays(d: Design, p: dict) -> None:
    d.add("assembly", "power")
    # Radiators lie in the X-Y plane (thin in Z). The solar wings lie in the X-Z plane (thin in Y, via thin_axis="y").
    # Planes at right angles = "perpendicular". The big solar faces look along Y toward the sun, and the radiators sit
    # edge-on to the sun, so they neither shade the arrays nor bake in direct sunlight. Each wing hinges about the edge
    # where it meets the chassis (axis X); angle 0 = deployed.
    d.add("assembly", "solar_top", parent="power", joint_axis="x", angle_deg="=-solar_deploy_angle_deg",
          pivot_z="=chassis_height / 2", pivot_y="=solar_thickness / 2", angle_min=-90, angle_max=0)
    d.add("panel", "solar_wing_top", attach_to="chassis", side="+z", length="=solar_wing_length", width="=solar_wing_width",
          thickness="=solar_thickness", thin_axis="y", material="aluminum_honeycomb", role="solar", assembly="solar_top")
    d.add("assembly", "solar_bottom", parent="power", joint_axis="x", angle_deg="=solar_deploy_angle_deg",
          pivot_z="=-chassis_height / 2", pivot_y="=solar_thickness / 2", angle_min=0, angle_max=90)
    d.add("panel", "solar_wing_bottom", attach_to="chassis", side="-z", length="=solar_wing_length", width="=solar_wing_width",
          thickness="=solar_thickness", thin_axis="y", material="aluminum_honeycomb", role="solar", assembly="solar_bottom")


# ==========================================================================================================
#  PUT IT TOGETHER
# ==========================================================================================================
def build_core_detail(overrides: dict | None = None, name: str = "orbital_datacentre_core") -> Design:
    """A cut-away view for the picture only: the compute blades and optical terminals with the chassis shell hidden."""
    p = {**PARAMS, **(overrides or {})}
    validate(p)
    d = new_design(name, p)
    add_blades(d, p)
    add_oisl_core(d, p)
    return d


def add_oisl_core(d: Design, p: dict) -> None:
    """Telescope + gimbal + flange only (no chassis to bolt to), for the cut-away picture."""
    p = {**p, "OISL_BOLT_COUNT": 0}
    add_oisl(d, p)


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
def report(d: Design, overrides: dict | None = None, analysis: dict | None = None) -> tuple[str, dict]:
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
                 inertia_kg_m2=meas["inertia_kg_m2"], assemblies=meas.get("assemblies", []))

    i = meas["inertia_kg_m2"]
    lines = [f"DESIGN REPORT: {d.name}", "=" * 64,
             "Parts: " + str(meas["parts"]) + "   Total mass: " + f"{meas['total_mass_kg']:,.0f} kg",
             "Overall size: " + " x ".join(f"{v / 1000:.1f}" for v in meas["overall_size_mm"]) + " m  (X x Y x Z)",
             "Mass by role (kg): " + ", ".join(f"{k} {v:,.0f}" for k, v in meas["mass_by_role_kg"].items()),
             "Mass by subsystem (kg): " + ", ".join(f"{a['path']} {a['mass_kg']:,.0f}" for a in meas.get("assemblies", [])
                                                     if "/" not in a["path"]),
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
              "Orbital-Datacentre link simulator."]
    if p["OISL_BOLT_COUNT"]:
        lines.append(f"  Each terminal is held by {int(p['OISL_BOLT_COUNT'])} x {BOLT} socket-head screws through a "
                     f"{p['FLANGE_THICKNESS']} mm flange into tapped holes in the chassis wall.")
    if analysis:
        f = analysis["frequencies_hz"]
        lines += ["", "VIBRATION MODES (shell finite-element analysis, BETA; free structure as in orbit)",
                  "  First modes (Hz): " + ", ".join(f"{v:.2f}" for v in f[:6]),
                  f"  Model: {analysis['model']['elements']} elements; FE mass {analysis['model']['mass_kg']:,.0f} kg vs CAD "
                  f"{analysis['model']['cad_mass_kg']:,.0f} kg",
                  "  Flat plates only, linear, equipment lumped, sandwich panels idealised: cross-check before relying on it.",
                  "  For LAUNCH, analyse the STOWED state (set the deploy angles) with the adapter ring fixed."]
    lines += ["", "CHECKS", "  " + check["advice"]]
    for f in check["floating_parts"]:
        lines.append(f"  Floating part: {f['part']} ({f['nearest_gap_mm']} mm from anything)")
    for o in check["overlaps"]:
        lines.append(f"  OVERLAP: {o['a']} and {o['b']} ({o['overlap_cm3']} cm3)")
    if check.get("thread_engagements"):
        lines.append(f"  {len(check['thread_engagements'])} screws sit in tapped holes (thread engagement, expected).")
    lines += ["", "NOT INCLUDED: launch-load stress cases, thermal-network and orbital analysis. Mass and inertia come "
              "from simple material densities and idealised blocks, so treat them as concept-stage estimates."]
    return "\n".join(lines), facts


# ==========================================================================================================
#  COMMAND LINE
# ==========================================================================================================
WHOLE_NUMBERS = {"BLADE_COUNT", "OISL_BOLT_COUNT"}      # settings that cannot be fractional


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


def analyse_modes(design: Design, n_modes: int = 6):
    """Free-free vibration modes of the structure as currently configured (deployed or stowed)."""
    model = from_design(design, target_elements=3500)
    return model, model.modal(n_modes)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the orbital AI datacentre model with SatCAD.")
    ap.add_argument("--name", default="orbital_datacentre", help="name for the design and its output files")
    ap.add_argument("--out", default=str(HERE / "output"), help="output folder (default: ./output)")
    ap.add_argument("--set", action="append", metavar="KEY=VALUE", help="override a variable, e.g. RADIATOR_WINGSPAN=30000")
    ap.add_argument("--no-modes", action="store_true", help="skip the vibration-mode analysis")
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
    analysis, modes_html = None, None
    if not args.no_modes:
        try:
            fe, res = analyse_modes(design)
            analysis = dict(frequencies_hz=res["frequencies_hz"],
                            model=dict(elements=res["elements"], mass_kg=res["total_mass_tonne"] * 1000,
                                       cad_mass_kg=design.measure()["total_mass_kg"]))
            modes_html = write_mode_viewer(fe, res, folder / f"{args.name}_modes.html", args.name)
        except ValueError as e:                                           # e.g. no plates (never for this design)
            print(f"(vibration analysis skipped: {e})", file=sys.stderr)
    text, _ = report(design, overrides, analysis)
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
    print(f"  viewer  : {Path(viewer).name}  (interactive 3D: orbit, zoom, click parts, hinge sliders)")
    if modes_html:
        print(f"  modes   : {Path(modes_html).name}  (animated vibration mode shapes)")
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
