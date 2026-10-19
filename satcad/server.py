"""
satcad MCP server: lets Claude build and inspect a spacecraft design.

Run it directly (stdio):      python -m satcad.server
Environment:                  SATCAD_OUT  folder for saved designs, pictures and exports
                                          (default: ./cad_output next to where the server is started)

Every tool works in MILLIMETRES and KILOGRAMS. Axes: X forward, Y left, Z up.
"""
from __future__ import annotations

import os
import webbrowser
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from .engine import NAME_RE, SCHEMAS, Design, DesignError
from .materials import MATERIALS, describe_all
from .render import render_png
from . import fem as _fem
from .viewer import write_mode_viewer, write_viewer
from .power import size_solar_array as _size_solar_array
from .thermal import size_radiator as _size_radiator

OUT_DIR = Path(os.environ.get("SATCAD_OUT", Path.cwd() / "cad_output")).resolve()
AUTOSAVE = OUT_DIR / "designs" / "current.json"

INSTRUCTIONS = """\
You are the user's spacecraft CAD assistant. The user is a beginner and has never used CAD: explain what you are
doing in plain language, avoid jargon (or define it), and never ask them to click anything.

Workflow: design_status -> add chassis -> add radiator panels / other parts -> check_design -> preview (ALWAYS show the
picture after changes) -> measure_design -> open_3d_viewer / export_design when asked.
Units: millimetres, kilograms, degrees. Axes: X forward, Y left, Z up.

PARAMETRIC DESIGN: parts are recipes, not frozen shapes. Prefer named variables and formulas over hard-coded numbers
when a dimension is shared or likely to change: set_variable("wingspan", 22000), then use "=wingspan" or a formula such as
"=(wingspan - chassis.width) / 2" in any numeric setting. A formula may read another part: chassis.width, chassis.xmax,
chassis.mass. Changing a variable (set_variable) or a part (edit_part) rebuilds everything that depends on it.
Trig functions use degrees.

Part types: chassis, box, cylinder, sphere, panel (attached flat panel), extrude (2D profile pushed into a solid: beams,
struts, tubes), revolve (profile spun around an axis: tanks, nozzles), sweep (pipes, harnesses along a path), loft
(transitions), shell (hollow out with open faces), fillet_edges, cutout, hole (ISO metric, bolt circles), fastener (ISO 4762
screws), pattern, mirror. ORGANISE real designs into ASSEMBLIES (subsystems: add_assembly, then assembly=<name> on parts): they
roll up mass/inertia per subsystem, nest, and a hinge assembly (joint_axis + angle_deg="=deploy") stows and deploys arrays. Use size_radiator / size_solar_array to turn watts into area before sizing panels.
measure_design returns the real mass, centre of mass and full inertia tensor (kg*m^2): quote these for attitude-control work.
analyze_modal / analyze_static run a BETA shell finite-element solver (plates, hollow chassis walls, lumped equipment): always
state its limits (flat plates only, no holes/fillets/joint flexibility, linear) and that results need cross-checking, and analyse
the right configuration (stowed for launch, deployed for orbit) by setting the hinge variables first.

Be honest about limits: masses come from simple densities; thermal numbers ignore sunlight and Earth heat; there is no
structural, vibration or orbital analysis here. Do not claim this matches commercial CAD or flight-qualified accuracy.
"""

mcp = FastMCP("satellite-cad", instructions=INSTRUCTIONS)
design = Design("satellite")


def _load_previous() -> None:
    try:
        if AUTOSAVE.exists():
            design.load(AUTOSAVE)
    except Exception:                                   # a corrupt autosave must never stop the server
        design.clear("satellite")


def _autosave() -> None:
    try:
        design.save(AUTOSAVE)
    except OSError:
        pass


def _changed(result):
    _autosave()
    return result


Num = float | str            # a number, or a formula such as "=chassis.width * 2" or "=wingspan / 2"


def _n(v):
    """Numbers pass through, '=formulas' stay text, and plain numeric text such as '500' becomes 500."""
    if isinstance(v, str) and not v.lstrip().startswith("="):
        try:
            return float(v)
        except ValueError:
            raise DesignError(f"'{v}' is not a number. To use a formula start it with '=' (for example '=2*width').") from None
    return v


def _add(assembly, kind, name, **kw):
    """design.add with an optional assembly (subsystem) the new part belongs to."""
    if assembly is not None:
        kw["assembly"] = assembly
    return design.add(kind, name, **kw)


def _pts(points):
    return [[_n(c) for c in pt] for pt in points]


_load_previous()


# --------------------------------------------------------------------------- #
# Looking at the design
# --------------------------------------------------------------------------- #
@mcp.tool()
def design_status() -> dict:
    """Summary of the current design: name, parts, total mass and overall size. Call this first in a session."""
    return dict(name=design.name, parts=[p["name"] for p in design.list_parts()], measurements=design.measure(),
                output_folder=str(OUT_DIR))


@mcp.tool()
def list_parts() -> list[dict]:
    """Every part with its size, position (bounding box), material, mass and the settings used to build it."""
    return design.list_parts()


@mcp.tool()
def measure_design() -> dict:
    """
    Total mass, centre of mass, overall dimensions, mass by role, AND the full inertia tensor about the centre of mass
    (kg*m^2) with principal moments and axes, computed from the real solids (equipment mass is spread through the chassis
    interior). This is what attitude-control analysis needs.
    """
    return design.measure()


@mcp.tool()
def check_design() -> dict:
    """Find parts that overlap each other (an error) and parts that float free, touching nothing (a warning)."""
    return design.check()


@mcp.tool()
def preview(views: list[str] | None = None) -> list:
    """
    Draw the design: isometric, top, front and side views in one picture. Call after every change and show the
    user. `views` can be any of: iso, top, front, side (default: all four).
    """
    views = views or ["iso", "top", "front", "side"]
    png = render_png(design, tuple(views))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{design.name}_preview.png"
    path.write_bytes(png)
    m = design.measure()
    return [f"{design.name}: {m['parts']} parts, {m['total_mass_kg']} kg, overall "
            f"{' x '.join(str(round(v)) for v in m['overall_size_mm'])} mm. Picture saved to {path}",
            Image(data=png, format="png")]


@mcp.tool()
def list_materials() -> dict:
    """
    Materials you can assign to parts: density, Young's modulus, Poisson ratio, yield and ultimate strength, thermal
    expansion, conductivity, specific heat, and a plain-English note. Typical handbook values for concept design, not
    certified allowables; say so when quoting them.
    """
    return describe_all()


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #
@mcp.tool()
def design_new(name: str = "satellite") -> str:
    """Throw away the current design (parts AND variables) and start an empty one."""
    if not NAME_RE.match(name):
        raise DesignError("Names may only use letters, digits, '_' and '-'.")
    design.clear(name)
    return _changed(f"Started a new empty design called '{name}'.")


@mcp.tool()
def add_chassis(name: str, length: Num, width: Num, height: Num, wall: Num = 0.0, internal_mass_kg: Num = 0.0,
                material: str = "aluminum_6061", fillet: Num = 0.0, chamfer: Num = 0.0,
                x: Num = 0.0, y: Num = 0.0, z: Num = 0.0,
                assembly: str | None = None) -> dict:
    """
    Add the main rectangular box (the 'server housing' / spacecraft bus), centred at (x, y, z).
    length = X size, width = Y size, height = Z size, all in mm. wall = metal thickness in mm (0 makes a solid block;
    use e.g. 5-10 for a hollow housing). internal_mass_kg = mass of the equipment inside (servers, batteries...), spread
    uniformly through the interior for the inertia calculation. fillet rounds all edges by that radius; chamfer bevels them.
    Any number may be a formula, e.g. length="=bay * 4".
    """
    return _changed(_add(assembly, "chassis", name, length=_n(length), width=_n(width), height=_n(height), wall=_n(wall),
                               internal_mass_kg=_n(internal_mass_kg), material=material, fillet=_n(fillet),
                               chamfer=_n(chamfer), x=_n(x), y=_n(y), z=_n(z)))


@mcp.tool()
def add_radiator_panel(name: str, attach_to: str, side: str, length: Num, width: Num, thickness: Num,
                       gap: Num = 0.0, offset_along_width: Num = 0.0, offset_along_thickness: Num = 0.0,
                       tilt_deg: Num = 0.0, thin_axis: str | None = None,
                       material: str = "aluminum_honeycomb", role: str = "radiator",
                       assembly: str | None = None) -> dict:
    """
    Attach a flat rectangular panel to one face of an existing part. It follows that part if it is resized or moved.
    side: which face, '+y' '-y' '+x' '-x' '+z' '-z'. The panel sticks OUT of that face by `length`; `width` runs along
    the face; `thickness` is the thin direction (all mm). By default the thin direction is Z for side faces, so the
    panel lies flat like a wing. gap = distance off the face (0 = touching). tilt_deg hinges the panel up/down about
    its attached edge. offset_along_width / offset_along_thickness slide it along the face.
    Use role='solar' for solar wings. Numbers may be formulas, e.g. length="=(wingspan - chassis.width) / 2".
    """
    return _changed(_add(assembly, "panel", name, attach_to=attach_to, side=side, length=_n(length), width=_n(width),
                               thickness=_n(thickness), gap=_n(gap), offset_span=_n(offset_along_width),
                               offset_thin=_n(offset_along_thickness), tilt_deg=_n(tilt_deg), thin_axis=thin_axis,
                               material=material, role=role))


@mcp.tool()
def add_radiator_pair(name_prefix: str, attach_to: str, axis: str, length: Num, width: Num, thickness: Num,
                      gap: Num = 0.0, tilt_deg: Num = 0.0, material: str = "aluminum_honeycomb",
                      assembly: str | None = None) -> list[dict]:
    """
    Add two identical radiator panels on opposite sides of a part (axis 'x', 'y' or 'z'), named
    <name_prefix>_pos and <name_prefix>_neg. This is the quick way to make symmetric radiator wings.
    """
    if axis not in ("x", "y", "z"):
        raise DesignError("axis must be 'x', 'y' or 'z'.")
    tilt = _n(tilt_deg)
    out = []
    for sign, suffix in (("+", "pos"), ("-", "neg")):
        out.append(_add(assembly, "panel", f"{name_prefix}_{suffix}", attach_to=attach_to, side=sign + axis,
                              length=_n(length), width=_n(width), thickness=_n(thickness), gap=_n(gap),
                              tilt_deg=tilt if sign == "+" else (-tilt if isinstance(tilt, (int, float)) else f"=-({tilt[1:]})"),
                              material=material))
    return _changed(out)


@mcp.tool()
def add_box(name: str, length: Num, width: Num, height: Num, x: Num = 0.0, y: Num = 0.0, z: Num = 0.0,
            material: str = "aluminum_6061", role: str = "structure", fillet: Num = 0.0, chamfer: Num = 0.0,
            rx: Num = 0.0, ry: Num = 0.0, rz: Num = 0.0,
            assembly: str | None = None) -> dict:
    """Add a plain rectangular block centred at (x, y, z), optionally rotated (rx, ry, rz in degrees) and with rounded
    (fillet) or bevelled (chamfer) edges. Sizes in mm. role is a label (structure, payload, solar...)."""
    return _changed(_add(assembly, "box", name, length=_n(length), width=_n(width), height=_n(height), x=_n(x), y=_n(y),
                               z=_n(z), material=material, role=role, fillet=_n(fillet), chamfer=_n(chamfer),
                               rx=_n(rx), ry=_n(ry), rz=_n(rz)))


@mcp.tool()
def add_cylinder(name: str, radius: Num, height: Num, axis: str = "z", x: Num = 0.0, y: Num = 0.0,
                 z: Num = 0.0, material: str = "aluminum_6061", role: str = "structure",
                 assembly: str | None = None) -> dict:
    """Add a solid cylinder (telescope barrel, pipe stub...) centred at (x, y, z), lying along `axis`. Sizes in mm."""
    return _changed(_add(assembly, "cylinder", name, radius=_n(radius), height=_n(height), axis=axis, x=_n(x), y=_n(y),
                               z=_n(z), material=material, role=role))


@mcp.tool()
def add_sphere(name: str, radius: Num, x: Num = 0.0, y: Num = 0.0, z: Num = 0.0,
               material: str = "aluminum_6061", role: str = "structure",
               assembly: str | None = None) -> dict:
    """Add a solid sphere centred at (x, y, z). Radius in mm."""
    return _changed(_add(assembly, "sphere", name, radius=_n(radius), x=_n(x), y=_n(y), z=_n(z), material=material, role=role))


@mcp.tool()
def add_extrusion(name: str, profile: str, depth: Num, width: Num | None = None, height: Num | None = None,
                  radius: Num | None = None, points: list[list[Num]] | None = None, wall: Num = 0.0,
                  corner_radius: Num | None = None, sides: Num | None = None,
                  plane: str = "xy", both_sides: bool = False, taper_deg: Num = 0.0,
                  x: Num = 0.0, y: Num = 0.0, z: Num = 0.0, rx: Num = 0.0, ry: Num = 0.0, rz: Num = 0.0,
                  material: str = "aluminum_6061", role: str = "structure",
                  assembly: str | None = None) -> dict:
    """
    Push a 2D profile out into a solid: struts, beams, tubes, brackets, wedge-shaped fittings.
    profile: 'rect' (give width, height), 'circle' (give radius), 'polygon' (give points as [[x, y], ...] in mm, in
    the profile plane), 'slot' (width = overall length, height = slot width), 'rounded_rect' (width, height, corner_radius),
    'ellipse' (width and height are the full axes) or 'ngon' (sides and radius to a corner: hex nuts, polygonal tubes). wall > 0 makes a hollow tube (rect or circle only). plane: 'xy' (extrudes along +z),
    'yz' (along +x, profile width runs along y, height along z) or 'zx' (along +y). The profile sits on the plane at
    (x, y, z) and extends `depth` mm along the normal; both_sides=True extends `depth` in BOTH directions.
    taper_deg narrows (positive) or widens (negative) the solid along its length.
    """
    return _changed(_add(assembly, "extrude", name, profile=profile, depth=_n(depth),
                               width=None if width is None else _n(width), height=None if height is None else _n(height),
                               radius=None if radius is None else _n(radius), points=None if points is None else _pts(points),
                               corner_radius=None if corner_radius is None else _n(corner_radius),
                               sides=None if sides is None else _n(sides),
                               wall=_n(wall), plane=plane, both_sides=both_sides, taper_deg=_n(taper_deg), x=_n(x),
                               y=_n(y), z=_n(z), rx=_n(rx), ry=_n(ry), rz=_n(rz), material=material, role=role))


@mcp.tool()
def add_revolve(name: str, points: list[list[Num]], angle: Num = 360.0, axis: str = "z", x: Num = 0.0, y: Num = 0.0,
                z: Num = 0.0, material: str = "aluminum_6061", role: str = "structure",
                assembly: str | None = None) -> dict:
    """
    Spin a 2D profile around an axis: propellant tanks, nozzles, cones, domes, nose fairings.
    points: the outline as [[radius, height], ...] in mm (radius >= 0, height along the axis), joined in order and closed
    automatically. Example tank with a conical end: [[0,0],[300,0],[300,500],[0,700]]. angle: degrees (360 = full revolution).
    axis: which direction the finished part points ('x', 'y' or 'z').
    """
    return _changed(_add(assembly, "revolve", name, points=_pts(points), angle=_n(angle), axis=axis, x=_n(x), y=_n(y),
                               z=_n(z), material=material, role=role))


@mcp.tool()
def add_cutout(name: str, target: str, side: str, shape: str, width: Num | None = None, height: Num | None = None,
               diameter: Num | None = None, depth: Num = 0.0, offset_u: Num = 0.0, offset_v: Num = 0.0) -> dict:
    """
    Cut a hole or pocket into one face of an existing part (access ports, cable holes, lightening holes, mounting holes).
    side: the face, '+x' '-x' '+y' '-y' '+z' '-z'. shape: 'circle' (give diameter) or 'rect' (give width and height).
    depth: how deep, in mm; 0 cuts all the way through. offset_u / offset_v slide the cut from the face centre along the
    face: for an x-face u=y, v=z; for a y-face u=z, v=x; for a z-face u=x, v=y. The cut follows the part if it is resized.
    On a hollow chassis, depth = the wall thickness cuts through just that wall.
    """
    return _changed(design.add("cutout", name, target=target, side=side, shape=shape,
                               width=None if width is None else _n(width), height=None if height is None else _n(height),
                               diameter=None if diameter is None else _n(diameter), depth=_n(depth),
                               offset_u=_n(offset_u), offset_v=_n(offset_v)))


@mcp.tool()
def add_pattern(name: str, source: str, mode: str, count: Num, dx: Num = 0.0, dy: Num = 0.0, dz: Num = 0.0,
                axis: str = "z", cx: Num = 0.0, cy: Num = 0.0, cz: Num = 0.0, total_angle: Num = 360.0,
                assembly: str | None = None) -> dict:
    """
    Repeat an existing part. count = total number of instances INCLUDING the original; the copies are named
    <name>_2, <name>_3 ... mode 'linear': copies are spaced by (dx, dy, dz) mm. mode 'circular': copies are spread around an
    axis ('x', 'y' or 'z') passing through (cx, cy, cz); total_angle 360 spreads them evenly all the way round, a smaller
    angle spans just that arc. Use for rows of compute blades, thruster rings, bolt circles, solar-array rows.
    """
    return _changed(_add(assembly, "pattern", name, source=source, mode=mode, count=_n(count), dx=_n(dx), dy=_n(dy), dz=_n(dz),
                               axis=axis, cx=_n(cx), cy=_n(cy), cz=_n(cz), total_angle=_n(total_angle)))


@mcp.tool()
def add_mirror(name: str, source: str, plane: str, offset: Num = 0.0,
               assembly: str | None = None) -> dict:
    """
    Make a mirror-image copy of a part across a plane: 'xy' (flips Z), 'yz' (flips X) or 'zx' (flips Y), optionally shifted
    `offset` mm along the plane's normal. Keeps symmetric designs symmetric: edit the original and the mirror follows.
    """
    return _changed(_add(assembly, "mirror", name, source=source, plane=plane, offset=_n(offset)))


@mcp.tool()
def add_assembly(name: str, parent: str | None = None, x: Num = 0.0, y: Num = 0.0, z: Num = 0.0,
                 rx: Num = 0.0, ry: Num = 0.0, rz: Num = 0.0, joint_axis: str | None = None, angle_deg: Num = 0.0,
                 pivot_x: Num = 0.0, pivot_y: Num = 0.0, pivot_z: Num = 0.0, angle_min: Num = -180.0,
                 angle_max: Num = 180.0) -> dict:
    """
    Create a named assembly (a subsystem such as 'thermal', 'power', 'optical'). Put parts in it by passing assembly=<name>
    to the add_* tools. Assemblies nest (parent=...), roll up mass, centre of mass and inertia, and group the 3D viewer's part
    tree. x, y, z, rx, ry, rz place the whole assembly (member parts' own positions are relative to it; panels that attach to other
    parts keep following those parts). joint_axis ('x', 'y' or 'z') makes it a HINGE: members swing by angle_deg about that
    axis through (pivot_x, pivot_y, pivot_z), measured in the assembly's own frame. Use angle_deg="=deploy" with a
    variable to stow and deploy solar arrays and radiators; the 3D viewer gets a slider within angle_min..angle_max.
    """
    return _changed(design.add("assembly", name, parent=parent, x=_n(x), y=_n(y), z=_n(z), rx=_n(rx), ry=_n(ry),
                               rz=_n(rz), joint_axis=joint_axis, angle_deg=_n(angle_deg), pivot_x=_n(pivot_x),
                               pivot_y=_n(pivot_y), pivot_z=_n(pivot_z), angle_min=_n(angle_min), angle_max=_n(angle_max)))


@mcp.tool()
def list_assemblies() -> dict:
    """The product structure: every assembly with its rolled-up mass, centre of mass and principal inertias, nested, plus parts not in any."""
    return design.assembly_tree()


@mcp.tool()
def list_joints() -> list[dict]:
    """Every hinge with its world-space pivot and axis, current angle and range, and the parts it moves."""
    return design.joints()


@mcp.tool()
def add_shell(name: str, target: str, open_faces: list[str], thickness: Num, direction: str = "inward") -> dict:
    """
    Hollow out an existing solid to a wall thickness, leaving some faces open: electronics boxes, covers, trays, housings.
    open_faces: the faces to remove, e.g. ['+z'] (open top). direction 'inward' keeps the outer size; 'outward' grows it.
    The wall can't be thicker than the corner radius of any rounded edge it follows.
    """
    return _changed(design.add("shell", name, target=target, open_faces=open_faces, thickness=_n(thickness), direction=direction))


@mcp.tool()
def add_sweep(name: str, path: list[list[Num]], profile: str = "circle", radius: Num | None = None,
              width: Num | None = None, height: Num | None = None, wall: Num = 0.0, bend_radius: Num = 0.0,
              x: Num = 0.0, y: Num = 0.0, z: Num = 0.0, material: str = "aluminum_6061", role: str = "structure",
              assembly: str | None = None) -> dict:
    """
    Drag a profile along a 3D path: coolant pipes, propellant lines, cable harnesses, bent struts.
    path: points [[x, y, z], ...] in mm (at least 2). profile 'circle' (radius) or 'rect' (width, height); wall > 0 makes
    a hollow pipe. Paths with corners need bend_radius > 0 (every corner is rounded with that radius; it must fit the
    straight lengths on each side). The profile starts at the first point, perpendicular to the first segment.
    """
    return _changed(_add(assembly, "sweep", name, path=[[_n(c) for c in pt] for pt in path], profile=profile,
                         radius=None if radius is None else _n(radius), width=None if width is None else _n(width),
                         height=None if height is None else _n(height), wall=_n(wall), bend_radius=_n(bend_radius),
                         x=_n(x), y=_n(y), z=_n(z), material=material, role=role))


@mcp.tool()
def add_loft(name: str, sections: list[list], plane: str = "xy", x: Num = 0.0, y: Num = 0.0, z: Num = 0.0,
             material: str = "aluminum_6061", role: str = "structure", assembly: str | None = None) -> dict:
    """
    A smooth solid through cross-sections: adapters, fairings, nozzles, transitions between a square box and a round tube.
    sections: [['rect', position, width, height], ['circle', position, radius], ...] with increasing positions (mm) along
    the stacking axis: plane 'xy' stacks along +z, 'yz' along +x, 'zx' along +y. Needs at least 2 sections.
    """
    return _changed(_add(assembly, "loft", name, sections=[[sec[0]] + [_n(c) for c in sec[1:]] for sec in sections],
                         plane=plane, x=_n(x), y=_n(y), z=_n(z), material=material, role=role))


@mcp.tool()
def add_fillet_edges(name: str, target: str, edges: str, radius: Num = 0.0, chamfer: Num = 0.0) -> dict:
    """
    Round (radius) or bevel (chamfer) chosen edges of an existing part. edges: 'all', 'parallel:x' (or y, z: every edge
    running along that axis), or 'face:+z' (every edge of that face). Give radius OR chamfer, not both.
    """
    return _changed(design.add("fillet_edges", name, target=target, edges=edges, radius=_n(radius), chamfer=_n(chamfer)))


@mcp.tool()
def add_hole(name: str, target: str, side: str, standard: str, hole_type: str = "clearance", depth: Num = 0.0,
             offset_u: Num = 0.0, offset_v: Num = 0.0, count_u: Num = 1, pitch_u: Num = 0.0, count_v: Num = 1,
             pitch_v: Num = 0.0, circle_count: Num = 0, circle_diameter: Num = 0.0, circle_start_deg: Num = 0.0) -> dict:
    """
    Standard ISO metric holes in one face of a part. standard: 'M3' 'M4' 'M5' 'M6' 'M8' 'M10' or 'M12'.
    hole_type: 'clearance' (a screw passes through), 'tapped' (threaded: modelled at the tap-drill size), 'counterbore'
    (clearance hole with a recess for a socket-head screw) or 'countersink' (conical recess for a flat-head screw; not M12).
    depth 0 = through all. One hole sits at (offset_u, offset_v) from the face centre (for an x-face u=y, v=z; y-face u=z,
    v=x; z-face u=x, v=y). count_u x count_v with pitch_u/pitch_v makes a grid centred there; circle_count > 0 instead makes a
    BOLT CIRCLE of circle_diameter starting at circle_start_deg. The holes follow the part if it is resized.
    """
    return _changed(design.add("hole", name, target=target, side=side, standard=standard, hole_type=hole_type,
                               depth=_n(depth), offset_u=_n(offset_u), offset_v=_n(offset_v), count_u=_n(count_u),
                               pitch_u=_n(pitch_u), count_v=_n(count_v), pitch_v=_n(pitch_v),
                               circle_count=_n(circle_count), circle_diameter=_n(circle_diameter),
                               circle_start_deg=_n(circle_start_deg)))


@mcp.tool()
def add_fastener(name: str, size: str, length: Num, axis: str = "-z", x: Num = 0.0, y: Num = 0.0, z: Num = 0.0,
                 material: str = "stainless_316", assembly: str | None = None) -> dict:
    """
    An ISO 4762 socket-head cap screw (M3-M12) with its hex socket. (x, y, z) is the centre of the underside of the head
    (where it seats); `axis` is the direction the tip points, e.g. '-z' for a screw going down into a plate. length is the
    shank length under the head. Use add_pattern (mode 'circular') to put screws on a bolt circle. Mass is real.
    """
    return _changed(_add(assembly, "fastener", name, size=size, length=_n(length), axis=axis, x=_n(x), y=_n(y), z=_n(z),
                         material=material))


@mcp.tool()
def edit_part(name: str, changes: dict) -> dict:
    """
    Change settings of an existing part (or cutout, pattern, mirror) and rebuild. `changes` uses the same setting names the
    part was built with, e.g. {"length": 1000, "wall": 6} for a chassis, {"tilt_deg": 30} for a panel, {"count": 8} for a
    pattern. Values may be formulas: {"length": "=2*bay"}. Everything attached to the part follows automatically.
    Call list_features to see every part's current settings.
    """
    aliases = {"offset_along_width": "offset_span", "offset_along_thickness": "offset_thin"}   # same names as add_radiator_panel
    return _changed(design.edit(name, **{aliases.get(k, k): (_n(v) if not isinstance(v, (list, dict)) else v)
                                         for k, v in changes.items()}))


@mcp.tool()
def remove_part(name: str) -> str:
    """Delete a part. Fails if other parts (panels, cutouts, patterns, formulas) depend on it; remove those first."""
    design.remove(name)
    return _changed(f"Removed '{name}'.")


@mcp.tool()
def set_variable(name: str, value: Num) -> dict:
    """
    Create or change a named variable (like an NX 'expression'): a number, or a formula using other variables, e.g.
    set_variable("bay", 500) then set_variable("length", "=4 * bay"). Use a variable in any numeric setting by writing
    "=bay * 2". Changing it rebuilds every part that uses it, so one number can resize the whole spacecraft.
    """
    return _changed(design.set_variable(name, _n(value)))


@mcp.tool()
def delete_variable(name: str) -> dict:
    """Delete a variable (fails if a part or another variable still uses it)."""
    return _changed(design.delete_variable(name))


@mcp.tool()
def list_variables() -> dict:
    """All named variables with their definitions and current values."""
    design.rebuild()
    return design.list_variables()


@mcp.tool()
def list_features() -> list[dict]:
    """The design's recipe in build order: every feature with the settings (and formulas) it was built from."""
    return design.list_features()


# --------------------------------------------------------------------------- #
# Engineering helpers and files
# --------------------------------------------------------------------------- #
@mcp.tool()
def size_radiator(heat_load_w: float, panel_temp_k: float = 320.0, emissivity: float = 0.85, sides: int = 2,
                  sink_temp_k: float = 4.0, panel_count: int = 2, aspect_length_over_width: float = 2.0) -> dict:
    """
    How much radiator area is needed to reject `heat_load_w` watts into space (Stefan-Boltzmann law)?
    Returns the required area and suggested panel sizes in mm to feed into add_radiator_panel. sink_temp_k is what the
    panel 'sees' (about 4 K deep space; 200-250 K if it faces the Earth). A lower bound: sunlight and Earth heat are ignored.
    """
    return _size_radiator(heat_load_w, panel_temp_k, emissivity, sides, sink_temp_k, aspect_length_over_width, panel_count)


@mcp.tool()
def size_solar_array(power_w: float, cell_efficiency: float = 0.30, packing_factor: float = 0.90,
                     end_of_life_factor: float = 0.85, sun_angle_deg: float = 0.0, panel_count: int = 2,
                     aspect_length_over_width: float = 1.6) -> dict:
    """
    How much solar-cell area is needed to make `power_w` watts of electricity in Earth orbit? Returns the area and
    suggested panel sizes in mm (use them with add_radiator_panel(..., role='solar', material=...) or add_box).
    Sunlight-only estimate: eclipse and batteries are not included.
    """
    return _size_solar_array(power_w, cell_efficiency, packing_factor, end_of_life_factor, sun_angle_deg, panel_count,
                             aspect_length_over_width)


@mcp.tool()
def export_design(basename: str | None = None, formats: list[str] | None = None) -> dict:
    """
    Write the model to files in the output folder. formats: 'step' (standard CAD exchange file that Siemens NX,
    Fusion, SolidWorks, FreeCAD... can import), 'stl' (3D-printing / mesh viewers), 'glb' (web and AR viewers).
    Default: all three.
    """
    base = basename or design.name
    if not NAME_RE.match(base):
        raise DesignError("basename may only use letters, digits, '_' and '-'.")
    return design.export(OUT_DIR / "exports", base, tuple(formats or ("step", "stl", "glb")))


_FE_NOTE = ("BETA shell solver for concept-stage checks. Flat plates only: holes, fillets, ribs, joint flexibility, curved shells and "
            "sandwich-panel layup are not modelled, equipment is lumped, and the model is linear. Verify with an established FE tool "
            "before relying on any number for flight hardware.")


def _fe_model(fixed_part, fixed_side, constraint, mesh_size):
    if (fixed_part is None) != (fixed_side is None):
        raise DesignError("Give both fixed_part and fixed_side (for example 'chassis' and '-z'), or neither for a free structure.")
    if constraint not in ("perimeter", "face"):
        raise DesignError("constraint must be 'perimeter' (clamp the edge of that wall, like a launch-adapter ring) or 'face' (clamp the whole wall).")
    try:
        return _fem.from_design(design, mesh_size=mesh_size, fixed=None if fixed_part is None else {"part": fixed_part, "side": fixed_side},
                                constraint=constraint)
    except ValueError as e:
        raise DesignError(str(e)) from None


@mcp.tool()
def analyze_modal(n_modes: int = 8, fixed_part: str | None = None, fixed_side: str | None = None,
                  constraint: str = "perimeter", mesh_size: float | None = None, show_modes: bool = True) -> dict:
    """
    Natural frequencies and mode shapes of the structure (BETA shell finite-element solver). Launch vehicles require the
    first modes to be above a minimum frequency, and attitude control needs to know where flexible modes sit.
    fixed_part + fixed_side clamp one wall of a part to represent the launch-adapter interface, e.g. fixed_part='chassis',
    fixed_side='-z' (constraint 'perimeter' clamps the wall's edge, 'face' the whole wall). Leave both out for a FREE structure
    (in orbit): the six rigid-body modes are removed. Analyse the configuration you care about: set the hinge variables first
    (stowed for launch, deployed for orbit). Returns frequencies (Hz), the fraction of mass each mode moves in X/Y/Z
    (effective modal mass: modes with large fractions drive launch loads), modelling notes, and a path to an animated
    mode-shape viewer. Thin plate-like parts and a hollow chassis are modelled; everything else is a lumped mass.
    """
    model = _fe_model(fixed_part, fixed_side, constraint, mesh_size)
    res = model.modal(n_modes)
    out = dict(frequencies_hz=[round(f, 4) for f in res["frequencies_hz"]],
               effective_mass_percent={a: [round(100 * v, 2) for v in res["effective_mass_fraction"][a]] for a in "xyz"},
               cumulative_effective_mass_percent={a: round(100 * v, 1) for a, v in res["cumulative_effective_mass"].items()},
               boundary=("free (in orbit)" if fixed_part is None else f"{fixed_part} wall {fixed_side}, {constraint} clamped"),
               rigid_body_modes_removed=res["rigid_body_modes_removed"],
               model=dict(plates=len(model.plates), lumped_masses=len(model.lumps), nodes=res["nodes"],
                          elements=res["elements"], mass_kg=round(res["total_mass_tonne"] * 1000, 1),
                          cad_mass_kg=design.measure()["total_mass_kg"]),
               warnings=model.warnings, note=_FE_NOTE)
    if show_modes:
        out["mode_shape_viewer"] = write_mode_viewer(model, res, OUT_DIR / f"{design.name}_modes.html", design.name)
    return out


@mcp.tool()
def analyze_static(accel_x_g: float = 0.0, accel_y_g: float = 0.0, accel_z_g: float = 0.0,
                   fixed_part: str | None = None, fixed_side: str | None = None, constraint: str = "perimeter",
                   mesh_size: float | None = None, safety_factor: float = 1.25) -> dict:
    """
    Static stress and deflection under an acceleration (quasi-static launch load), BETA shell finite-element solver. The structure
    is clamped at fixed_part / fixed_side (required; see analyze_modal) and every mass feels the acceleration given in g along
    X, Y, Z. Returns the largest displacement, the largest von Mises stress, and for each plate the stress, yield strength and
    margin of safety MS = yield / (safety_factor x stress) - 1 (negative means it yields). Linear small-displacement analysis:
    displacements above a few plate thicknesses are not trustworthy. Use the STOWED configuration for launch loads.
    """
    if fixed_part is None:
        raise DesignError("A static analysis needs a support: give fixed_part and fixed_side (for example 'chassis' and '-z').")
    model = _fe_model(fixed_part, fixed_side, constraint, mesh_size)
    try:
        res = model.static(accel_g=(accel_x_g, accel_y_g, accel_z_g), safety_factor=safety_factor)
    except ValueError as e:
        raise DesignError(str(e)) from None
    ranked = sorted(res["stress_by_plate"].items(), key=lambda kv: -kv[1]["max_von_mises_mpa"])
    table = {k: dict(max_von_mises_mpa=round(v["max_von_mises_mpa"], 2), yield_mpa=v["yield_mpa"],
                     margin_of_safety=None if v["margin_of_safety"] is None else round(v["margin_of_safety"], 3))
             for k, v in ranked[:10]}
    return dict(acceleration_g=[accel_x_g, accel_y_g, accel_z_g], max_displacement_mm=round(res["max_displacement_mm"], 3),
                max_von_mises_mpa=round(res["max_von_mises_mpa"], 2), worst_plate=res["worst_plate"],
                stress_by_plate_highest_first=table, safety_factor=safety_factor,
                reaction_force_n=[round(v, 1) for v in res["reaction_force_n"]],
                model=dict(plates=len(model.plates), lumped_masses=len(model.lumps), nodes=res["nodes"], elements=res["elements"],
                           mass_kg=round(res["total_mass_tonne"] * 1000, 1), cad_mass_kg=design.measure()["total_mass_kg"]),
                warnings=model.warnings, note=_FE_NOTE)


@mcp.tool()
def export_bom(filename: str | None = None) -> dict:
    """Write a bill of materials (CSV: part, type, role, material, mass, volume, size, with a TOTAL row) and return it."""
    base = filename or f"{design.name}_bom"
    if not NAME_RE.match(base):
        raise DesignError("filename may only use letters, digits, '_' and '-'.")
    path = design.export_bom(OUT_DIR / "exports" / f"{base}.csv")
    rows = design.bom()
    return dict(path=path, parts=len(rows), total_mass_kg=round(sum(r["mass_kg"] for r in rows), 3), rows=rows)


@mcp.tool()
def open_3d_viewer(open_in_browser: bool = True) -> dict:
    """
    Write an interactive 3D viewer (one .html file) and open it in the user's browser. In it the user can orbit, zoom, click
    parts for mass and material, hide parts, explode the assembly, see the centre of mass and save a PNG. Needs internet on
    first open (it loads the three.js library). Offer this after building anything non-trivial.
    """
    path = write_viewer(design, OUT_DIR / f"{design.name}_viewer.html")
    opened = False
    if open_in_browser:
        try:
            opened = bool(webbrowser.open(Path(path).resolve().as_uri()))
        except Exception:
            opened = False
    return dict(path=path, opened_in_browser=opened,
                note="Open this file in any browser to rotate and inspect the model." if not opened else "Opened in the browser.")


@mcp.tool()
def save_design(filename: str | None = None) -> str:
    """Save the design recipe (not just the shape) so it can be reloaded and edited later."""
    base = filename or design.name
    if not NAME_RE.match(base):
        raise DesignError("filename may only use letters, digits, '_' and '-'.")
    return design.save(OUT_DIR / "designs" / f"{base}.json")


@mcp.tool()
def load_design(filename: str) -> dict:
    """Load a previously saved design by name (see save_design)."""
    if not NAME_RE.match(filename):
        raise DesignError("filename may only use letters, digits, '_' and '-'.")
    path = OUT_DIR / "designs" / f"{filename}.json"
    if not path.exists():
        found = sorted(p.stem for p in (OUT_DIR / "designs").glob("*.json")) if (OUT_DIR / "designs").exists() else []
        raise DesignError(f"No saved design '{filename}'. Saved designs: {found or 'none'}.")
    design.load(path)
    _autosave()
    return design_status()


@mcp.resource("satcad://guide")
def guide() -> str:
    """Plain-English glossary for CAD beginners."""
    return (
        "CAD GLOSSARY FOR BEGINNERS\n"
        "- Part: one solid object (the chassis, a radiator panel).\n"
        "- Parametric: a part is stored as a recipe (e.g. length=800) not a frozen shape, so changing the number reshapes it.\n"
        "- Assembly: several parts positioned together. Here, panels are attached to the chassis and follow it.\n"
        "- Units: millimetres. 1000 mm = 1 m.\n"
        "- Axes: X forward, Y left, Z up. '+y' means the face pointing toward positive Y.\n"
        "- STEP file: the standard CAD exchange format. Siemens NX, Fusion 360, SolidWorks and FreeCAD can import it.\n"
        "- STL file: a mesh of triangles, for 3D printing and quick viewers.\n"
        "- Bounding box: the smallest box that contains a part.\n"
        "- Interference: two parts occupying the same space, a design error.\n"
        f"- Part types you can build: {', '.join(SCHEMAS)}.\n")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
