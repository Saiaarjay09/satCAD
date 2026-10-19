"""
satcad MCP server: lets Claude build and inspect a spacecraft design.

Run it directly (stdio):      python -m satcad.server
Environment:                  SATCAD_OUT  folder for saved designs, pictures and exports
                                          (default: ./cad_output next to where the server is started)

Every tool works in MILLIMETRES and KILOGRAMS. Axes: X forward, Y left, Z up.
"""
from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from .engine import NAME_RE, SCHEMAS, Design, DesignError
from .materials import MATERIALS
from .render import render_png
from .power import size_solar_array as _size_solar_array
from .thermal import size_radiator as _size_radiator

OUT_DIR = Path(os.environ.get("SATCAD_OUT", Path.cwd() / "cad_output")).resolve()
AUTOSAVE = OUT_DIR / "designs" / "current.json"

INSTRUCTIONS = """\
You are the user's spacecraft CAD assistant. The user is a beginner and has never used CAD: explain what you are
doing in plain language, avoid jargon (or define it), and never ask them to click anything.

Workflow: design_status -> add chassis -> add radiator panels / other parts -> check_design -> preview (ALWAYS show the
picture after changes) -> measure_design -> export_design when asked.
Units: millimetres and kilograms. Axes: X forward, Y left, Z up. Parts are PARAMETRIC: use edit_part to change a size and
everything attached follows. Use size_radiator to turn a heat load (watts) into panel area before sizing radiators.
Be honest about limits: masses are estimates from simple densities; thermal numbers ignore sunlight and Earth heat.
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
    """Total mass, centre of mass, overall dimensions and mass broken down by role (chassis, radiator...)."""
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
    """Materials you can assign to parts, with density (kg/m^3) and a plain-English note."""
    return {k: dict(density_kg_m3=v[0], note=v[2]) for k, v in MATERIALS.items()}


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #
@mcp.tool()
def design_new(name: str = "satellite") -> str:
    """Throw away the current design and start an empty one."""
    if not NAME_RE.match(name):
        raise DesignError("Names may only use letters, digits, '_' and '-'.")
    design.clear(name)
    return _changed(f"Started a new empty design called '{name}'.")


@mcp.tool()
def add_chassis(name: str, length: float, width: float, height: float, wall: float = 0.0,
                internal_mass_kg: float = 0.0, material: str = "aluminum_6061",
                x: float = 0.0, y: float = 0.0, z: float = 0.0) -> dict:
    """
    Add the main rectangular box (the 'server housing' / spacecraft bus), centred at (x, y, z).
    length = X size, width = Y size, height = Z size, all in mm. wall = metal thickness in mm (0 makes a solid block;
    use e.g. 5-10 for a hollow housing). internal_mass_kg = mass of the equipment inside (servers, batteries...),
    counted in the total mass at the chassis centre.
    """
    return _changed(design.add("chassis", name, length=length, width=width, height=height, wall=wall,
                               internal_mass_kg=internal_mass_kg, material=material, x=x, y=y, z=z))


@mcp.tool()
def add_radiator_panel(name: str, attach_to: str, side: str, length: float, width: float, thickness: float,
                       gap: float = 0.0, offset_along_width: float = 0.0, offset_along_thickness: float = 0.0,
                       tilt_deg: float = 0.0, thin_axis: str | None = None,
                       material: str = "aluminum_honeycomb", role: str = "radiator") -> dict:
    """
    Attach a flat rectangular panel to one face of an existing part. It follows that part if it is resized or moved.
    side: which face, '+y' '-y' '+x' '-x' '+z' '-z'. The panel sticks OUT of that face by `length`; `width` runs along
    the face; `thickness` is the thin direction (all mm). By default the thin direction is Z for side faces, so the
    panel lies flat like a wing. gap = distance off the face (0 = touching). tilt_deg hinges the panel up/down about
    its attached edge. offset_along_width / offset_along_thickness slide it along the face.
    """
    return _changed(design.add("panel", name, attach_to=attach_to, side=side, length=length, width=width,
                               thickness=thickness, gap=gap, offset_span=offset_along_width,
                               offset_thin=offset_along_thickness, tilt_deg=tilt_deg, thin_axis=thin_axis,
                               material=material, role=role))


@mcp.tool()
def add_radiator_pair(name_prefix: str, attach_to: str, axis: str, length: float, width: float, thickness: float,
                      gap: float = 0.0, tilt_deg: float = 0.0, material: str = "aluminum_honeycomb") -> list[dict]:
    """
    Add two identical radiator panels on opposite sides of a part (axis 'x', 'y' or 'z'), named
    <name_prefix>_pos and <name_prefix>_neg. This is the quick way to make symmetric radiator wings.
    """
    if axis not in ("x", "y", "z"):
        raise DesignError("axis must be 'x', 'y' or 'z'.")
    out = []
    for sign, suffix in (("+", "pos"), ("-", "neg")):
        out.append(design.add("panel", f"{name_prefix}_{suffix}", attach_to=attach_to, side=sign + axis, length=length,
                              width=width, thickness=thickness, gap=gap,
                              tilt_deg=tilt_deg * (1 if sign == "+" else -1), material=material))
    return _changed(out)


@mcp.tool()
def add_box(name: str, length: float, width: float, height: float, x: float = 0.0, y: float = 0.0, z: float = 0.0,
            material: str = "aluminum_6061", role: str = "structure") -> dict:
    """Add a plain rectangular block centred at (x, y, z). Sizes in mm. role is a label (structure, payload, solar...)."""
    return _changed(design.add("box", name, length=length, width=width, height=height, x=x, y=y, z=z,
                               material=material, role=role))


@mcp.tool()
def add_cylinder(name: str, radius: float, height: float, axis: str = "z", x: float = 0.0, y: float = 0.0,
                 z: float = 0.0, material: str = "aluminum_6061", role: str = "structure") -> dict:
    """Add a solid cylinder (fuel tank, telescope barrel, pipe...) centred at (x, y, z), lying along `axis`. Sizes in mm."""
    return _changed(design.add("cylinder", name, radius=radius, height=height, axis=axis, x=x, y=y, z=z,
                               material=material, role=role))


@mcp.tool()
def add_sphere(name: str, radius: float, x: float = 0.0, y: float = 0.0, z: float = 0.0,
               material: str = "aluminum_6061", role: str = "structure") -> dict:
    """Add a solid sphere centred at (x, y, z). Radius in mm."""
    return _changed(design.add("sphere", name, radius=radius, x=x, y=y, z=z, material=material, role=role))


@mcp.tool()
def edit_part(name: str, changes: dict) -> dict:
    """
    Change settings of an existing part and rebuild. `changes` uses the same setting names the part was built
    with, e.g. {"length": 1000, "wall": 6} for a chassis or {"tilt_deg": 30} for a panel. Everything attached
    to the part follows automatically. Call list_parts to see a part's current settings.
    """
    aliases = {"offset_along_width": "offset_span", "offset_along_thickness": "offset_thin"}   # same names as add_radiator_panel
    return _changed(design.edit(name, **{aliases.get(k, k): v for k, v in changes.items()}))


@mcp.tool()
def remove_part(name: str) -> str:
    """Delete a part. Fails if other parts (e.g. radiator panels) are attached to it; remove those first."""
    design.remove(name)
    return _changed(f"Removed '{name}'.")


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
