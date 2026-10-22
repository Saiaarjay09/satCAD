"""
2D production drawings from the 3D model: hidden-line removal with OpenCASCADE, third-angle views, dimensions, a title block and a bill of materials,
exported as DXF, SVG and PDF.

PIPELINE
    1. compute_projections(design, ...)   picks the shape (the whole assembly, one assembly or one part, optionally in a Motion Studio pose) and runs OpenCASCADE's
                                          HLRBRep_Algo + HLRBRep_HLRToShape for the four standard views. The visible, outline and hidden edges are discretised into
                                          2D polylines (BRepAdaptor_Curve + GCPnts_UniformDeflection). Cylinder axes are found for centre lines.
                                          Very large assemblies are projected from their bounding boxes (FAST mode), which still removes hidden lines between bodies.
    2. compose_sheet(design, proj, opts)  lays the views out in third-angle positions on an A3 / A2 sheet at a standard scale, adds overall and parameter-linked
                                          dimensions, centre lines, the border with grid zones, the title block and the bill of materials. The result is a Sheet:
                                          plain Python primitives (polylines, text, filled triangles) with a layer and a line weight each.
    3. export_dxf / export_svg / export_pdf  write that Sheet. DXF (AutoCAD R2000 ASCII) and SVG are pure Python; PDF uses Qt's QPdfWriter (see satcad.gui.blueprint_view).

VIEW CONVENTIONS (satCAD axes: X forward, Y left, Z up)
    front  viewer on the -Y side, X to the right, Z up      top    viewer above (+Z), X to the right, Y up
    right  viewer on the +X side, Y to the right, Z up      iso    viewer in the (+1, +1, +1) octant, Z up
    Third angle: the top view sits above the front view and the right view to its right.

HONEST LIMITS
    * Dimensions are overall sizes plus the part sizes and radii that equal one of the design's variables. They are placed automatically and can overlap busy views.
    * Tolerances are the general tolerances of ISO 2768-m for sizes up to 4000 mm; larger sizes are shown as reference sizes. There are no feature control frames
      (GD&T), no section views, no hatching and no threads: this is a first-pass production sheet, not a drafting package.
    * The DXF has been read back and audited with the ezdxf library, not opened in AutoCAD.
"""
from __future__ import annotations

import datetime
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from OCP.BRep import BRep_Builder
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.Bnd import Bnd_Box
from OCP.GCPnts import GCPnts_UniformDeflection
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf

from .engine import Design, DesignError
from .materials import MATERIALS

SHEETS = {"A3": (420.0, 297.0), "A2": (594.0, 420.0)}
STANDARD_SCALES = (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000)       # 1:n

# layer key -> (DXF layer name, DXF colour index, DXF line type, line weight mm)
LAYERS = {
    "visible": ("0_VISIBLE", 7, "CONTINUOUS", 0.50),
    "hidden": ("1_HIDDEN", 8, "DASHED", 0.25),
    "centerlines": ("2_CENTERLINES", 1, "CENTER", 0.18),
    "dimensions": ("3_DIMENSIONS", 3, "CONTINUOUS", 0.25),
    "titleblock": ("4_TITLEBLOCK_BOM", 5, "CONTINUOUS", 0.35),
}
HIDDEN_DASH = (4.0, 2.0)                      # mm on the sheet
CENTER_DASH = (12.0, 2.0, 2.0, 2.0)           # long dash, gap, dot, gap
VIEW_ORDER = ("front", "top", "right", "iso")
VIEW_LABEL = {"front": "FRONT VIEW", "top": "TOP VIEW", "right": "RIGHT SIDE VIEW", "iso": "ISOMETRIC VIEW"}
# name -> (direction towards the viewer, direction of the screen's x axis)
VIEW_AXES = {"front": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)), "top": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)), "right": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
             "iso": ((1.0, 1.0, 1.0), (-1.0, 1.0, 0.0))}

MATERIAL_LABEL = {"aluminum_6061": "Al 6061-T6", "aluminum_7075": "Al 7075-T6", "titanium_ti6al4v": "Ti-6Al-4V", "stainless_316": "SS 316", "inconel_718": "Inconel 718",
                  "invar_36": "Invar 36", "copper": "Copper (Cu)", "cfrp": "CFRP", "aluminum_honeycomb": "Al honeycomb", "generic_equipment": "Equipment (generic)"}


class DrawingCancelled(Exception):
    pass


# ======================================================================================================================================
# options and the result containers
# ======================================================================================================================================
@dataclass
class DrawingOptions:
    sheet: str = "A3"
    show_hidden: bool = True
    title: str = ""                                   # default: the design name
    author: str = "Saiaarjay09 / satCAD"
    scale: int | None = None                          # 1:n, None = the largest standard scale that fits
    fast_body_limit: int = 300                        # more bodies than this: project the bounding boxes instead (FAST mode); 0 = never
    force_fast: bool = False
    tolerance_class: str = "m"
    bom_rows: int = 10
    dimension_limit: int = 8                          # parameter-linked dimensions per sheet
    centerline_limit: int = 80


@dataclass
class ViewLines:
    name: str
    visible: list = field(default_factory=list)       # polylines [(x, y), ...] in the view's own coordinates (model units, x right, y up)
    hidden: list = field(default_factory=list)
    axes: list = field(default_factory=list)          # centre lines: (p0, p1, radius, end_on) with 2D points, end_on = the axis points at the viewer
    bounds: tuple = (0.0, 0.0, 0.0, 0.0)              # xmin, ymin, xmax, ymax of the projected bounding box


@dataclass
class Projections:
    views: dict
    box3d: tuple                                      # (xmin, ymin, zmin, xmax, ymax, zmax) of the drawn shape
    parts: list
    mode: str                                         # "hlr" or "bbox"
    seconds: float = 0.0
    selection: str = ""
    notes: list = field(default_factory=list)
    boxes: dict = field(default_factory=dict)         # part name -> (xmin, ymin, zmin, xmax, ymax, zmax) as drawn (posed)
    posed: set = field(default_factory=set)           # parts that a Motion Studio pose has moved


@dataclass
class Prim:
    kind: str                                         # "line" (polyline), "text", "fill" (filled polygon)
    layer: str
    pts: list = field(default_factory=list)
    text: str = ""
    height: float = 3.5
    anchor: str = "ml"                                # horizontal l/c/r + vertical b/m/t
    style: str = "continuous"                         # continuous | dashed | center
    weight: float | None = None
    rot: float = 0.0
    group: str = ""                                   # the view or table it belongs to


@dataclass
class Sheet:
    size: tuple
    prims: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)          # title, scale, date, ... and the view placements
    dims: list = field(default_factory=list)          # the dimension records (text, value, parameter) for tests and the UI

    def layer(self, key):
        return [p for p in self.prims if p.layer == key]


# ======================================================================================================================================
# view maths
# ======================================================================================================================================
def view_basis(name: str):
    n, x = (np.array(v, float) for v in VIEW_AXES[name])
    n /= np.linalg.norm(n)
    x /= np.linalg.norm(x)
    y = np.cross(n, x)
    return n, x, y


def to_view(p, name: str):
    """A 3D point in model coordinates -> (x, y) in the named view, matching the coordinates OpenCASCADE's projector produces."""
    n, x, y = view_basis(name)
    p = np.asarray(p, float)
    return float(p @ x), float(p @ y)


def _corners(box):
    x0, y0, z0, x1, y1, z1 = box
    return [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


def projected_bounds(box3d, name: str):
    pts = [to_view(c, name) for c in _corners(box3d)]
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


# ======================================================================================================================================
# shapes
# ======================================================================================================================================
def _trsf(M):
    t = gp_Trsf()
    t.SetValues(*(float(M[i][j]) for i in range(3) for j in range(4)))
    return t


def part_shape(part, matrix=None):
    """The OCC shape of a part, optionally moved by a rigid 4x4 matrix (a Motion Studio pose)."""
    shape = part.solid.wrapped
    if matrix is not None and not np.allclose(matrix, np.eye(4), atol=1e-9):
        shape = shape.Moved(TopLoc_Location(_trsf(matrix)))
    return shape


def assembly_members(design: Design, name: str) -> list:
    """Parts inside an assembly (and its child assemblies)."""
    children = {name}
    changed = True
    while changed:
        changed = False
        for n, f in design.features.items():
            if f.kind == "assembly" and f.params.get("parent") in children and n not in children:
                children.add(n)
                changed = True
    return [n for n, p in design.parts.items() if p.assembly in children]


def resolve_selection(design: Design, selection: str | None) -> list:
    """'' / None -> every part; a part name; 'assembly:<name>' or the name of an assembly -> its parts."""
    if not selection or selection in ("all", "Full assembly"):
        return list(design.parts)
    if selection in design.parts:
        return [selection]
    name = selection.split(":", 1)[1] if selection.startswith("assembly:") else selection
    if name in design.features and design.features[name].kind == "assembly":
        members = assembly_members(design, name)
        if members:
            return members
        raise DesignError(f"The assembly '{name}' has no parts to draw.")
    raise DesignError(f"There is nothing called '{selection}' to draw. Use a part name, an assembly name or 'all'.")


def _bbox_of(shape):
    """(xmin, ymin, zmin, xmax, ymax, zmax) of an OCC shape (exact bounds, not the padded ones)."""
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, False)
    a, b = box.CornerMin(), box.CornerMax()
    return (a.X(), a.Y(), a.Z(), b.X(), b.Y(), b.Z())


def build_compound(shapes):
    comp = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(comp)
    for s in shapes:
        b.Add(comp, s)
    return comp


def _proxy_box(lo, hi):
    dx, dy, dz = (max(h - l, 1e-3) for l, h in zip(lo, hi))
    return BRepPrimAPI_MakeBox(gp_Pnt(lo[0], lo[1], lo[2]), dx, dy, dz).Shape()


# ======================================================================================================================================
# hidden line removal
# ======================================================================================================================================
def _edges(shape):
    out = []
    ex = TopExp_Explorer(shape, TopAbs_EDGE)
    while ex.More():
        out.append(TopoDS.Edge_s(ex.Current()) if hasattr(TopoDS, "Edge_s") else TopoDS.Edge(ex.Current()))
        ex.Next()
    return out


def discretize_edge(edge, deflection: float) -> list:
    """An OCC edge as a 2D polyline (the HLR result lives in the view plane: x, y)."""
    try:
        c = BRepAdaptor_Curve(edge)
        first, last = c.FirstParameter(), c.LastParameter()
        d = GCPnts_UniformDeflection(c, deflection, first, last)
        if not d.IsDone() or d.NbPoints() < 2:
            a, b = c.Value(first), c.Value(last)
            return [(a.X(), a.Y()), (b.X(), b.Y())]
        return [(d.Value(i).X(), d.Value(i).Y()) for i in range(1, d.NbPoints() + 1)]
    except Exception:
        return []


def hlr_view(shape, name: str, deflection: float, want_hidden: bool = True):
    """Run OpenCASCADE hidden-line removal for one view. Returns (visible, hidden) polylines."""
    n, x, _ = view_basis(name)
    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*n), gp_Dir(*x))))
    algo.Update()
    algo.Hide()
    res = HLRBRep_HLRToShape(algo)
    visible, hidden = [], []
    for comp in (res.VCompound(), res.OutLineVCompound()):
        if comp is not None and not comp.IsNull():
            visible += [p for p in (discretize_edge(e, deflection) for e in _edges(comp)) if len(p) >= 2]
    if want_hidden:
        for comp in (res.HCompound(), res.OutLineHCompound()):
            if comp is not None and not comp.IsNull():
                hidden += [p for p in (discretize_edge(e, deflection) for e in _edges(comp)) if len(p) >= 2]
    return visible, hidden


def find_axes(shapes, diag: float, limit: int) -> list:
    """Cylinder axes worth a centre line: (p0, p1, radius) in 3D, largest first. Small cylinders are ignored (a screw would clutter an overall view)."""
    min_r = 0.0015 * diag
    found = {}
    for shape in shapes:
        ex = TopExp_Explorer(shape, TopAbs_FACE)
        while ex.More():
            face = ex.Current()
            ex.Next()
            try:
                surf = BRepAdaptor_Surface(TopoDS.Face_s(face) if hasattr(TopoDS, "Face_s") else TopoDS.Face(face))
                if surf.GetType() != GeomAbs_Cylinder:
                    continue
                cyl = surf.Cylinder()
                r = cyl.Radius()
                if r < min_r:
                    continue
                ax = cyl.Axis()
                loc, d = ax.Location(), ax.Direction()
                o, dv = np.array([loc.X(), loc.Y(), loc.Z()]), np.array([d.X(), d.Y(), d.Z()])
                bb = _bbox_of(face)
                lo = np.array(bb[:3])
                hi = np.array(bb[3:])
                ts = [float((np.array([x, y, z]) - o) @ dv) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
                t0, t1 = min(ts), max(ts)
                if t1 - t0 < 0.02 * r:
                    continue
                direction = dv if dv[int(np.argmax(abs(dv)))] > 0 else -dv
                key = (tuple(np.round(o + dv * ((t0 + t1) / 2), 0)), tuple(np.round(direction, 3)), round(r, 1))              # one centre line per cylinder (split faces collapse)
                p0, p1 = o + dv * t0, o + dv * t1
                if key not in found or (p1 - p0) @ (p1 - p0) > (found[key][1] - found[key][0]) @ (found[key][1] - found[key][0]):
                    found[key] = (p0, p1, r)
            except Exception:
                continue
    axes = sorted(found.values(), key=lambda a: -a[2])[:limit]
    return [(tuple(p0), tuple(p1), r) for p0, p1, r in axes]


@dataclass
class Prepared:
    """What a projection needs, taken from the design in one quick step so the slow part can run on a worker thread while the design keeps changing."""
    names: list
    shapes: list
    boxes: list
    box3d: tuple
    diag: float
    selection: str
    posed: set = field(default_factory=set)


def prepare_projection(design: Design, selection: str | None = None, matrices: dict | None = None) -> Prepared:
    names = resolve_selection(design, selection)
    if not names:
        raise DesignError("The design has no parts to draw.")
    shapes = [part_shape(design.parts[n], (matrices or {}).get(n)) for n in names]
    boxes = [_bbox_of(s) for s in shapes]
    lo = [min(b[i] for b in boxes) for i in range(3)]
    hi = [max(b[i + 3] for b in boxes) for i in range(3)]
    diag = float(np.linalg.norm(np.array(hi) - np.array(lo))) or 1.0
    posed = {n for n in names if n in (matrices or {}) and not np.allclose(matrices[n], np.eye(4), atol=1e-9)}
    return Prepared(names, shapes, boxes, (lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]), diag, selection or "", posed)


def project_prepared(prep: Prepared, opts: DrawingOptions | None = None, progress=None, cancelled=None) -> Projections:
    """The slow part: hidden-line removal for the four views and the centre lines. Safe to call from a worker thread."""
    opts = opts or DrawingOptions()
    progress = progress or (lambda f, m: None)
    cancelled = cancelled or (lambda: False)
    t0 = time.time()
    names, shapes, boxes, box3d, diag = prep.names, prep.shapes, prep.boxes, prep.box3d, prep.diag
    fast = opts.force_fast or bool(opts.fast_body_limit and len(names) > opts.fast_body_limit)
    notes = []
    if fast:
        notes.append(f"FAST mode: {len(names)} bodies are drawn as their bounding boxes (hidden lines between the boxes are still removed)")
        progress(0.05, f"Building {len(names)} bounding boxes")
        work = build_compound([_proxy_box(b[:3], b[3:]) for b in boxes])
    else:
        work = build_compound(shapes)
    deflection = max(0.02, diag / 6000.0)
    views = {}
    for i, name in enumerate(VIEW_ORDER):
        if cancelled():
            raise DrawingCancelled()
        progress(0.1 + 0.7 * i / len(VIEW_ORDER), f"Hidden-line removal: {VIEW_LABEL[name].lower()}")
        vis, hid = hlr_view(work, name, deflection, opts.show_hidden)
        views[name] = ViewLines(name, vis, hid, [], projected_bounds(box3d, name))
    if cancelled():
        raise DrawingCancelled()
    progress(0.85, "Finding centre lines")
    if True:                                                           # a cylinder search over every body is fast (about 0.1 s for 2,400 bodies)
        for p0, p1, r in find_axes(shapes, diag, opts.centerline_limit):
            for name in VIEW_ORDER:
                n, _, _ = view_basis(name)
                d = np.array(p1) - np.array(p0)
                on_end = abs(float(d @ n)) > 0.999 * np.linalg.norm(d)
                views[name].axes.append((to_view(p0, name), to_view(p1, name), r, on_end))
    progress(1.0, "Projection done")
    return Projections(views, box3d, names, "bbox" if fast else "hlr", time.time() - t0, prep.selection, notes, dict(zip(names, boxes)), set(prep.posed))


def compute_projections(design: Design, selection: str | None = None, matrices: dict | None = None, opts: DrawingOptions | None = None,
                        progress=None, cancelled=None) -> Projections:
    """Project the chosen shape into the four views. `matrices` = {part name: 4x4} poses (Motion Studio); `progress(fraction, text)` and `cancelled()` are optional."""
    opts = opts or DrawingOptions()
    (progress or (lambda f, m: None))(0.02, "Collecting bodies")
    return project_prepared(prepare_projection(design, selection, matrices), opts, progress, cancelled)


# ======================================================================================================================================
# tolerances, numbers, text
# ======================================================================================================================================
# ISO 2768-1 general tolerances, class m (medium): (upper limit of the size range in mm, +- tolerance in mm)
ISO2768_M = ((3.0, 0.1), (6.0, 0.1), (30.0, 0.2), (120.0, 0.3), (400.0, 0.5), (1000.0, 0.8), (2000.0, 1.2), (4000.0, 2.0))
ISO2768_F = ((3.0, 0.05), (6.0, 0.05), (30.0, 0.1), (120.0, 0.15), (400.0, 0.2), (1000.0, 0.3), (2000.0, 0.5))
ISO2768_C = ((3.0, 0.2), (6.0, 0.3), (30.0, 0.5), (120.0, 0.8), (400.0, 1.2), (1000.0, 2.0), (2000.0, 3.0), (4000.0, 4.0))


def general_tolerance(size_mm: float, cls: str = "m"):
    """The +- general tolerance for a linear size (ISO 2768-1), or None when the size is outside the table (then it is a reference size)."""
    table = {"m": ISO2768_M, "f": ISO2768_F, "c": ISO2768_C}[cls]
    for limit, tol in table:
        if size_mm <= limit:
            return tol
    return None


def fmt_mm(v: float) -> str:
    return f"{v:.1f}"


def dimension_text(value: float, cls: str = "m", name: str | None = None, diameter: bool = False) -> str:
    """'PLATE_L = 240.0 ±0.5' for a size tied to a variable, 'Ø80.0 ±0.3 (2 x BOSS_R)' for a diameter whose variable is the radius, 'REF' beyond ISO 2768's range."""
    tol = general_tolerance(value, cls)
    tail = f" ±{tol:g}" if tol is not None else " REF"
    if diameter:
        return f"Ø{fmt_mm(value)}{tail}" + (f" (2 x {name})" if name else "")
    return (f"{name} = " if name else "") + fmt_mm(value) + tail


# ======================================================================================================================================
# the bill of materials
# ======================================================================================================================================
def base_name(name: str) -> str:
    """server_tray_grid_17 -> server_tray, radiator_panel_pos_y_p2_a -> radiator_panel_pos_y_p#_a (digits make no difference to what a part is)."""
    n = re.sub(r"_grid_\d+$", "", name)
    n = re.sub(r"_(n|row|ridges|rows)?_?\d+$", "", n)
    return re.sub(r"\d+", "#", n) if re.search(r"\d", n) else n


def bom_rows(design, names: list) -> list:
    """[(item, part / sub-assembly, assembly, quantity, material, mass_kg)] grouped by assembly and base name, heaviest first."""
    groups: dict = {}
    for n in names:
        p = design.parts[n]
        key = (p.assembly or "-", base_name(n))
        g = groups.setdefault(key, dict(qty=0, mass=0.0, materials={}))
        g["qty"] += 1
        g["mass"] += p.mass_kg
        g["materials"][p.material] = g["materials"].get(p.material, 0) + 1
    rows = []
    for (assembly, name), g in groups.items():
        mat = max(g["materials"], key=lambda m: g["materials"][m])
        rows.append([0, name, assembly, g["qty"], MATERIAL_LABEL.get(mat, mat.replace("_", " ").title()), g["mass"]])
    rows.sort(key=lambda r: -r[5])
    for i, r in enumerate(rows, 1):
        r[0] = i
    return [tuple(r) for r in rows]


@dataclass
class Context:
    """What the sheet composer reads from the design, captured on the GUI thread so composing can run on a worker (parts are never changed after a rebuild)."""
    name: str
    parts: dict
    variables: dict                                   # length variables: name -> value in mm
    links: dict = field(default_factory=dict)         # feature name -> {setting: variable} for settings that are exactly "=VARIABLE"
    prims: dict = field(default_factory=dict)         # feature name -> {kind, params, final}: box / cylinder features and the part they end up in


_LENGTH_KEYS = {"length", "width", "height", "radius", "thickness", "depth", "diameter", "wall", "x", "y", "z", "dx", "dy", "dz", "cx", "cy", "cz", "offset", "offset_u",
                "offset_v", "pitch_u", "pitch_v", "fillet", "chamfer", "gap", "corner_radius", "pivot_x", "pivot_y", "pivot_z", "points", "size"}
_ANGLE_KEYS = {"rx", "ry", "rz", "angle", "angle_deg", "total_angle", "taper_deg", "circle_start_deg", "tilt_deg"}


def length_variables(design: Design) -> dict:
    """{name: value in mm} of the variables that are used as lengths (not angles, counts or ratios): the ones worth a dimension on a drawing."""
    from .parametric_transpiler import _names_in
    votes: dict = {}
    for f in design.features.values():
        for key, raw in f.raw.items():
            kind = "deg" if key in _ANGLE_KEYS else ("mm" if key in _LENGTH_KEYS else "other")
            items = raw if isinstance(raw, (list, tuple)) else [raw]
            for it in items:
                for sub in (it if isinstance(it, (list, tuple)) else [it]):
                    if isinstance(sub, str) and sub.lstrip().startswith("="):
                        for name in _names_in(sub.lstrip()[1:].strip()):
                            votes.setdefault(name, {"mm": 0, "deg": 0, "other": 0})[kind] += 1
    out = {}
    for name, raw in design.variables.items():
        up = name.upper()
        if up.endswith(("COUNT", "RATIO", "_DEG", "ANGLE")) or "ANGLE" in up:
            continue
        v = votes.get(name)
        if v is not None and v["deg"] + v["other"] > v["mm"]:
            continue
        try:
            value = float(design._var_cache.get(name, raw))
        except (TypeError, ValueError):
            continue
        if value > 0:
            out[name] = value
    return out


def variable_links(design: Design, variables: dict) -> dict:
    """{part: {setting: variable}} for the settings written as exactly '=NAME': the dimension on the sheet is then truly tied to that variable, not just equal to it."""
    out = {}
    for fname, f in design.features.items():
        for key in ("length", "width", "height", "radius", "depth", "diameter", "thickness"):
            raw = f.raw.get(key)
            if isinstance(raw, str) and raw.lstrip().startswith("="):
                name = raw.lstrip()[1:].strip()
                if name in variables:
                    out.setdefault(fname, {})[key] = name
    return out


def make_context(design: Design) -> Context:
    variables = length_variables(design)
    links = variable_links(design, variables)
    merged = {}
    for name, f in design.features.items():
        if f.kind == "boolean":
            merged[f.params["tool"]] = f.params["target"]

    def final(n):
        seen = 0
        while n in merged and seen < 1000:
            n, seen = merged[n], seen + 1
        return n
    prims = {}
    for name, f in design.features.items():
        if name in links and f.kind in ("box", "chassis", "cylinder"):
            prims[name] = dict(kind=f.kind, params=dict(f.params), final=final(name))
    return Context(design.name, dict(design.parts), variables, links, prims)


# ======================================================================================================================================
# composing the sheet
# ======================================================================================================================================
class _Composer:
    def __init__(self, design, proj: Projections, opts: DrawingOptions):
        self.d = design if isinstance(design, Context) else make_context(design)
        self.proj, self.opts = proj, opts
        self.W, self.H = SHEETS[opts.sheet]
        self.sheet = Sheet((self.W, self.H))
        self.l, self.b, self.r, self.t = 10.0, 10.0, self.W - 10.0, self.H - 10.0
        self.bom = bom_rows(self.d, proj.parts)
        self.bom_rows_shown = min(len(self.bom), opts.bom_rows)
        self.below: dict = {}                                 # view -> how far below its lowest edge the lowest dimension line sits (mm on the sheet)
        self.bom_h = (self.bom_rows_shown + 3) * 6.0
        self.tb_w, self.tb_h = 180.0, 42.0

    # ---- primitives ----------------------------------------------------------------------------------------------------------------------
    def line(self, layer, pts, style="continuous", weight=None, group=""):
        self.sheet.prims.append(Prim("line", layer, [tuple(map(float, p)) for p in pts], style=style, weight=weight, group=group))

    def text(self, layer, p, s, height=3.5, anchor="ml", rot=0.0, group=""):
        self.sheet.prims.append(Prim("text", layer, [tuple(map(float, p))], text=s, height=height, anchor=anchor, rot=rot, group=group))

    def fill(self, layer, pts, group=""):
        self.sheet.prims.append(Prim("fill", layer, [tuple(map(float, p)) for p in pts], group=group))

    def rect(self, layer, x0, y0, x1, y1, weight=None, group=""):
        self.line(layer, [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], weight=weight, group=group)

    # ---- the sheet --------------------------------------------------------------------------------------------------------------------------
    def border(self):
        self.rect("titleblock", 0, 0, self.W, self.H, 0.18, "border")
        self.rect("titleblock", self.l, self.b, self.r, self.t, 0.7, "border")
        cols, rows = 8, 6
        cw, rh = (self.r - self.l) / cols, (self.t - self.b) / rows
        for i in range(cols):
            x = self.l + i * cw
            if i:
                self.line("titleblock", [(x, 0), (x, self.b)], weight=0.25, group="border")
                self.line("titleblock", [(x, self.t), (x, self.H)], weight=0.25, group="border")
            for y in (self.b / 2, self.t + self.b / 2):
                self.text("titleblock", (x + cw / 2, y), str(i + 1), 3.5, "cm", group="border")
        for j in range(rows):
            y = self.t - (j + 1) * rh
            if j < rows - 1:
                self.line("titleblock", [(0, y), (self.l, y)], weight=0.25, group="border")
                self.line("titleblock", [(self.r, y), (self.W, y)], weight=0.25, group="border")
            for x in (self.l / 2, self.r + self.l / 2):
                self.text("titleblock", (x, y + rh / 2), "ABCDEF"[j], 3.5, "cm", group="border")

    # ---- layout and scale ------------------------------------------------------------------------------------------------------------------
    def choose_layout(self):
        box = self.proj.box3d
        ex, ey, ez = box[3] - box[0], box[4] - box[1], box[5] - box[2]
        self.ext = (ex, ey, ez)
        left_w = (self.r - self.l) * 0.56
        gx = gy = 34.0
        need = []
        for n in VIEW_ORDER:
            vb = self.proj.views[n].bounds
            need.append((vb[2] - vb[0], vb[3] - vb[1]))
        wf, hf = need[0]
        wt, ht = need[1]
        wr, hr = need[2]
        self.plan = self.plan_parameter_dims()
        n_x = sum(1 for it in self.plan if it["kind"] == "linear" and it["axis"] == "x")
        n_v = 1.4 * max(sum(1 for it in self.plan if it["kind"] == "linear" and it["axis"] == "z"), sum(1 for it in self.plan if it["kind"] == "linear" and it["axis"] == "y"))
        self.m_left, self.m_bottom = 26.0 + 9.0 * n_v, 34.0 + 13.0 * n_x
        top_free = (self.t - self.b) - self.m_bottom - 12.0
        scale_n = self.opts.scale
        if not scale_n:
            for n in STANDARD_SCALES:
                s = 1.0 / n
                if (wf + wr) * s + gx + self.m_left <= left_w and (hf + ht) * s + gy + self.m_bottom <= top_free:
                    scale_n = n
                    break
            else:
                scale_n = STANDARD_SCALES[-1]
        self.scale_n, self.s = scale_n, 1.0 / scale_n
        s = self.s
        self.gx, self.gy = gx, gy
        # origins (sheet position of each view's lower-left projected-bounds corner)
        self.org = {}
        fx, fy = self.l + self.m_left, self.b + self.m_bottom
        self.org["front"] = (fx, fy)
        self.org["top"] = (fx, fy + hf * s + gy)
        self.org["right"] = (fx + wf * s + gx, fy)
        # the isometric view: top right, above the bill of materials, at its own scale if it does not fit at the common scale
        ix0 = self.l + left_w + 10.0
        iy0 = self.b + self.tb_h + self.bom_h + 14.0
        avail_w, avail_h = self.r - 10.0 - ix0, self.t - 18.0 - iy0
        wi, hi = need[3]
        self.iso_scale_n = scale_n
        for n in STANDARD_SCALES:
            if n >= scale_n and wi / n <= avail_w and hi / n <= avail_h:
                self.iso_scale_n = n
                break
        else:
            self.iso_scale_n = STANDARD_SCALES[-1]
        si = 1.0 / self.iso_scale_n
        self.org["iso"] = (ix0 + (avail_w - wi * si) / 2, iy0 + (avail_h - hi * si) / 2)
        self.view_scale = {"front": s, "top": s, "right": s, "iso": si}

    def sx(self, name, p):
        vb = self.proj.views[name].bounds
        ox, oy = self.org[name]
        k = self.view_scale[name]
        return (ox + (p[0] - vb[0]) * k, oy + (p[1] - vb[1]) * k)

    # ---- the views ----------------------------------------------------------------------------------------------------------------------------
    def views(self):
        for name in VIEW_ORDER:
            v = self.proj.views[name]
            for pl in v.visible:
                if len(pl) >= 2:
                    self.line("visible", [self.sx(name, p) for p in pl], group=name)
            if self.opts.show_hidden:
                for pl in v.hidden:
                    if len(pl) >= 2:
                        self.line("hidden", [self.sx(name, p) for p in pl], "dashed", group=name)
            self.centerlines(name)
            vb = v.bounds
            k = self.view_scale[name]
            self.sheet.meta.setdefault("views", {})[name] = dict(scale_n=(self.scale_n if name != "iso" else self.iso_scale_n), origin=self.org[name], bounds=vb, k=k,
                                                                 lines=len(v.visible) + len(v.hidden))

    def view_labels(self):
        """Under each view, below its lowest dimension line."""
        for name in VIEW_ORDER:
            vb = self.proj.views[name].bounds
            cx, cy = self.sx(name, ((vb[0] + vb[2]) / 2, vb[1]))
            gap = self.below.get(name, 0.0) + 8.0 if name != "iso" else 8.0
            scale_txt = f"SCALE 1:{self.scale_n if name != 'iso' else self.iso_scale_n}"
            self.text("titleblock", (cx, cy - gap), f"{VIEW_LABEL[name]}   {scale_txt}", 3.5, "cm", group=name)

    def centerlines(self, name):
        k = self.view_scale[name]
        over = 3.0 / k                                      # 3 mm on the sheet, in model units
        for p0, p1, r, end_on in self.proj.views[name].axes:
            if end_on:
                c = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
                ext = r + over
                self.line("centerlines", [self.sx(name, (c[0] - ext, c[1])), self.sx(name, (c[0] + ext, c[1]))], "center", group=name)
                self.line("centerlines", [self.sx(name, (c[0], c[1] - ext)), self.sx(name, (c[0], c[1] + ext))], "center", group=name)
            else:
                d = np.array(p1) - np.array(p0)
                n = float(np.linalg.norm(d))
                if n < 1e-9:
                    continue
                u = d / n
                a, b = np.array(p0) - u * over, np.array(p1) + u * over
                self.line("centerlines", [self.sx(name, tuple(a)), self.sx(name, tuple(b))], "center", group=name)

    # ---- dimensions ------------------------------------------------------------------------------------------------------------------------------
    def dim_linear(self, a, b, offset, text, orientation, group, record=None):
        """A dimension between two sheet points a and b, with extension lines, a dimension line, filled arrow heads and centred text.
        `offset` (mm, positive) pushes the dimension line below (h) or to the left of (v) the measured points."""
        ARROW, GAP, OVER = 3.0, 1.0, 2.0
        if orientation == "h":
            self.below[group] = max(self.below.get(group, 0.0), offset)
            y = min(a[1], b[1]) - offset
            x0, x1 = sorted((a[0], b[0]))
            for p in (a, b):
                self.line("dimensions", [(p[0], p[1] - GAP), (p[0], y - OVER)], weight=0.18, group=group)
            self.line("dimensions", [(x0, y), (x1, y)], weight=0.25, group=group)
            self.fill("dimensions", [(x0, y), (x0 + ARROW, y + 0.5), (x0 + ARROW, y - 0.5)], group)
            self.fill("dimensions", [(x1, y), (x1 - ARROW, y + 0.5), (x1 - ARROW, y - 0.5)], group)
            self.text("dimensions", ((x0 + x1) / 2, y + 1.2), text, 3.0, "cb", group=group)
            where = ((x0 + x1) / 2, y)
        else:
            x = min(a[0], b[0]) - offset
            y0, y1 = sorted((a[1], b[1]))
            for p in (a, b):
                self.line("dimensions", [(p[0] - GAP, p[1]), (x - OVER, p[1])], weight=0.18, group=group)
            self.line("dimensions", [(x, y0), (x, y1)], weight=0.25, group=group)
            self.fill("dimensions", [(x, y0), (x + 0.5, y0 + ARROW), (x - 0.5, y0 + ARROW)], group)
            self.fill("dimensions", [(x, y1), (x + 0.5, y1 - ARROW), (x - 0.5, y1 - ARROW)], group)
            self.sheet.prims.append(Prim("text", "dimensions", [(x - 1.2, (y0 + y1) / 2)], text=text, height=3.0, anchor="cb", rot=90.0, group=group))
            where = (x, (y0 + y1) / 2)
        rec = dict(text=text, group=group, at=where)
        rec.update(record or {})
        self.sheet.dims.append(rec)

    def dim_diameter(self, centre, radius_sheet, text, group, record=None, top_y=None):
        """A diameter across a face-on circle. The text is carried on a leader line up to just above the view, where nothing else is drawn."""
        a, b = (centre[0] - radius_sheet, centre[1]), (centre[0] + radius_sheet, centre[1])
        ARROW = min(3.0, radius_sheet * 0.8)
        self.line("dimensions", [a, b], weight=0.25, group=group)
        self.fill("dimensions", [a, (a[0] + ARROW, a[1] + 0.5), (a[0] + ARROW, a[1] - 0.5)], group)
        self.fill("dimensions", [b, (b[0] - ARROW, b[1] + 0.5), (b[0] - ARROW, b[1] - 0.5)], group)
        ty = (top_y if top_y is not None else centre[1] + radius_sheet) + 7.0
        self.line("dimensions", [(centre[0], centre[1] + radius_sheet), (centre[0], ty)], weight=0.18, group=group)
        self.text("dimensions", (centre[0], ty + 1.2), text, 3.0, "cb", group=group)
        rec = dict(text=text, group=group, at=centre)
        rec.update(record or {})
        self.sheet.dims.append(rec)

    def dimensions(self):
        box = self.proj.box3d
        ex, ey, ez = self.ext
        cls = self.opts.tolerance_class
        names = self.var_names()
        fb = self.proj.views["front"].bounds
        tb = self.proj.views["top"].bounds
        rb = self.proj.views["right"].bounds
        # overall sizes
        a, b = self.sx("front", (fb[0], fb[1])), self.sx("front", (fb[2], fb[1]))
        self.dim_linear(a, b, 12.0, dimension_text(ex, cls, names.get(round(ex, 1))), "h", "front", dict(axis="x", value=ex, parameter=names.get(round(ex, 1)), overall=True))
        a, b = self.sx("front", (fb[0], fb[1])), self.sx("front", (fb[0], fb[3]))
        self.dim_linear(a, b, 12.0, dimension_text(ez, cls, names.get(round(ez, 1))), "v", "front", dict(axis="z", value=ez, parameter=names.get(round(ez, 1)), overall=True))
        a, b = self.sx("top", (tb[0], tb[1])), self.sx("top", (tb[0], tb[3]))
        self.dim_linear(a, b, 12.0, dimension_text(ey, cls, names.get(round(ey, 1))), "v", "top", dict(axis="y", value=ey, parameter=names.get(round(ey, 1)), overall=True))
        a, b = self.sx("right", (rb[0], rb[1])), self.sx("right", (rb[2], rb[1]))
        self.dim_linear(a, b, 12.0, dimension_text(ey, cls, names.get(round(ey, 1))), "h", "right", dict(axis="y", value=ey, parameter=names.get(round(ey, 1)), overall=True))
        self.parameter_dimensions()

    def var_names(self) -> dict:
        """{value rounded to 0.1: variable name} for the length variables of the design (for labelling overall sizes)."""
        out = {}
        for name, v in self.d.variables.items():
            out.setdefault(round(v, 1), name)
        return out

    def plan_parameter_dims(self) -> list:
        """Dimensions for settings that are tied to a variable ('=PLATE_L'): a box's length / width / height, a cylinder's height, a cylinder's radius seen face-on.
        They are read from the feature itself, so they survive a later boolean (a boss united into a plate keeps its own dimension). Only unrotated features outside
        an assembly and not moved by a pose are dimensioned (anywhere else the view would not show the setting undistorted)."""
        d = self.d
        plan, used = [], set()
        ext = (self.proj.box3d[3] - self.proj.box3d[0], self.proj.box3d[4] - self.proj.box3d[1], self.proj.box3d[5] - self.proj.box3d[2])
        overall = {round(x, 1) for x in ext}
        small = 0.04 * max(ext)                                   # a dimension on something tinier than this would be a blob on the sheet
        shown = set(self.proj.parts)
        for fname, prim in d.prims.items():
            if len(plan) >= self.opts.dimension_limit:
                break
            p = prim["params"]
            if prim["final"] not in shown or prim["final"] in self.proj.posed or p.get("assembly") or any(abs(p.get(k, 0.0)) > 1e-9 for k in ("rx", "ry", "rz")):
                continue
            centre = (p["x"], p["y"], p["z"])
            for key, vname in d.links[fname].items():
                v = d.variables[vname]
                if vname in used or v < small:
                    continue
                if key == "radius":
                    if prim["kind"] != "cylinder":
                        continue
                    axis = p.get("axis", "z")
                    plan.append(dict(kind="dia", axis=axis, view={"z": "top", "y": "front", "x": "right"}[axis], value=2 * v, radius=v, vname=vname, part=fname, centre=centre))
                    used.add(vname)
                    continue
                if prim["kind"] in ("box", "chassis"):
                    axis_i = {"length": 0, "width": 1, "height": 2}.get(key)
                else:
                    axis_i = {"x": 0, "y": 1, "z": 2}[p.get("axis", "z")] if key == "height" else None
                if axis_i is None or round(v, 1) in overall:
                    continue                                       # overall sizes are dimensioned already
                plan.append(dict(kind="linear", axis="xyz"[axis_i], view={0: "front", 1: "top", 2: "front"}[axis_i], orient="h" if axis_i == 0 else "v", value=v, vname=vname,
                                 part=fname, lo=centre[axis_i] - v / 2, hi=centre[axis_i] + v / 2))
                used.add(vname)
        return plan

    def parameter_dimensions(self):
        cls = self.opts.tolerance_class
        rank = {"x": 0, "y": 0, "z": 0}
        for item in self.plan:
            view = item["view"]
            vb = self.proj.views[view].bounds
            if item["kind"] == "linear":
                if item["value"] * self.view_scale[view] < 6.0:
                    continue                                     # too small on the sheet for arrows and text
                if item["axis"] == "x":
                    a, b = self.sx("front", (item["lo"], vb[1])), self.sx("front", (item["hi"], vb[1]))
                elif item["axis"] == "z":
                    a, b = self.sx("front", (vb[0], item["lo"])), self.sx("front", (vb[0], item["hi"]))
                else:
                    a, b = self.sx("top", (vb[0], item["lo"])), self.sx("top", (vb[0], item["hi"]))
                rank[item["axis"]] += 1
                self.dim_linear(a, b, 12.0 + 13.0 * rank[item["axis"]], dimension_text(item["value"], cls, item["vname"]), item["orient"], view,
                                dict(axis=item["axis"], value=item["value"], parameter=item["vname"], part=item["part"]))
            else:
                c = self.sx(view, to_view(item["centre"], view))
                top_y = self.sx(view, (vb[0], vb[3]))[1]
                self.dim_diameter(c, item["radius"] * self.view_scale[view], dimension_text(item["value"], cls, item["vname"], diameter=True), view,
                                  dict(axis=item["axis"], value=item["value"], parameter=item["vname"], part=item["part"], diameter=True), top_y)

    # ---- title block and BOM ------------------------------------------------------------------------------------------------------------
    def title_block(self):
        x1, y0 = self.r, self.b
        x0, y1 = x1 - self.tb_w, y0 + self.tb_h
        self.rect("titleblock", x0, y0, x1, y1, 0.7, "titleblock")
        title = self.opts.title or self.d.name.replace("_", " ").upper()
        if self.proj.selection and self.proj.selection not in ("all", "Full assembly"):
            title += f" - {self.proj.selection}"
        # rows: title (18) / scale, units, tolerance, projection (12) / author, date, sheet, drawing number (12)
        ys = [y1 - 18.0, y1 - 30.0]
        self.line("titleblock", [(x0, ys[0]), (x1, ys[0])], weight=0.35, group="titleblock")
        self.line("titleblock", [(x0, ys[1]), (x1, ys[1])], weight=0.35, group="titleblock")
        self.text("titleblock", (x0 + 3, y1 - 3.5), "TITLE", 1.8, "lt", group="titleblock")
        self.text("titleblock", (x0 + 3, y1 - 12.5), title[:48], 6.0, "lm", group="titleblock")
        scale_txt = f"1:{self.scale_n}"
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        cells = [("SCALE", scale_txt), ("UNITS", "MILLIMETERS"), ("TOLERANCE", f"ISO 2768-m{'K'}"), ("PROJECTION", "THIRD ANGLE")]
        widths = [30.0, 40.0, 40.0, 70.0]
        x = x0
        for (label, value), w in zip(cells, widths):
            self.text("titleblock", (x + 2, ys[0] - 2.5), label, 1.8, "lt", group="titleblock")
            self.text("titleblock", (x + 2, ys[0] - 8.5), value, 3.2, "lm", group="titleblock")
            x += w
            if x < x1 - 0.1:
                self.line("titleblock", [(x, ys[1]), (x, ys[0])], weight=0.25, group="titleblock")
        pn = re.sub(r"[^A-Za-z0-9]+", "-", self.d.name).strip("-").upper()[:24]
        cells2 = [("AUTHOR", self.opts.author[:26]), ("DATE", today), ("SHEET", "1 OF 1"), ("DRAWING NO.", f"SC-{pn}-001")]
        small_no = len(cells2[3][1]) > 18
        widths2 = [52.0, 50.0, 22.0, 56.0]
        x = x0
        for (label, value), w in zip(cells2, widths2):
            self.text("titleblock", (x + 2, ys[1] - 2.5), label, 1.8, "lt", group="titleblock")
            self.text("titleblock", (x + 2, ys[1] - 8.5), value, 2.2 if (label == "DRAWING NO." and small_no) else 3.0, "lm", group="titleblock")
            x += w
            if x < x1 - 0.1:
                self.line("titleblock", [(x, y0), (x, ys[1])], weight=0.25, group="titleblock")
        # the third-angle projection symbol (circles on the left, the frustum on the right) in the projection cell
        sx0 = x0 + 30 + 40 + 40 + 44
        cy = ys[0] - 7.5
        for rad in (3.4, 1.8):
            pts = [(sx0 + rad * math.cos(2 * math.pi * k / 40), cy + rad * math.sin(2 * math.pi * k / 40)) for k in range(41)]
            self.line("titleblock", pts, weight=0.25, group="titleblock")
        self.line("titleblock", [(sx0 + 6, cy + 3.4), (sx0 + 15, cy + 1.8), (sx0 + 15, cy - 1.8), (sx0 + 6, cy - 3.4), (sx0 + 6, cy + 3.4)], weight=0.25, group="titleblock")
        self.line("titleblock", [(sx0 - 5, cy), (sx0 + 17, cy)], "center", weight=0.18, group="titleblock")
        self.sheet.meta.update(title=title, scale=scale_txt, date=today, units="MILLIMETERS", tolerance="ISO 2768-mK", projection="THIRD ANGLE", author=self.opts.author,
                               drawing_no=f"SC-{pn}-001", sheet=self.opts.sheet)

    def bom_table(self):
        x1 = self.r
        x0 = x1 - self.tb_w
        y0 = self.b + self.tb_h
        row_h = 6.0
        cols = [("ITEM", 12.0), ("PART / SUB-ASSEMBLY", 60.0), ("QTY", 14.0), ("MATERIAL", 48.0), ("MASS kg", 46.0)]
        shown = self.bom[: self.bom_rows_shown]
        extra = len(self.bom) - len(shown)
        n_rows = len(shown) + 1 + 1 + (1 if extra else 0)
        top = y0 + n_rows * row_h
        self.rect("titleblock", x0, y0, x1, top, 0.5, "bom")
        # header at the TOP (ISO: list reads upward from the title block, header on top); rows are drawn below it
        y = top - row_h
        x = x0
        for label, w in cols:
            self.text("titleblock", (x + 1.5, y + row_h / 2), label, 2.6, "lm", group="bom")
            x += w
            if x < x1 - 0.1:
                self.line("titleblock", [(x, y0), (x, top)], weight=0.25, group="bom")
        self.line("titleblock", [(x0, y), (x1, y)], weight=0.35, group="bom")
        for i, (item, name, assembly, qty, mat, mass) in enumerate(shown):
            y -= row_h
            self.line("titleblock", [(x0, y), (x1, y)], weight=0.12, group="bom")
            vals = [str(item), (name if assembly == "-" else f"{name} [{assembly}]")[:34], str(qty), mat[:24], f"{mass:,.2f}"]
            x = x0
            for (label, w), v in zip(cols, vals):
                anchor = "rm" if label in ("MASS kg",) else "lm"
                self.text("titleblock", (x + (w - 1.5 if anchor == "rm" else 1.5), y + row_h / 2), v, 2.6, anchor, group="bom")
                x += w
        if extra:
            y -= row_h
            self.line("titleblock", [(x0, y), (x1, y)], weight=0.12, group="bom")
            self.text("titleblock", (x0 + 13.5, y + row_h / 2), f"... and {extra} more groups (full list: Export BOM .csv)", 2.6, "lm", group="bom")
        y -= row_h
        self.line("titleblock", [(x0, y), (x1, y)], weight=0.35, group="bom")
        total = sum(self.d.parts[n].mass_kg for n in self.proj.parts)
        self.text("titleblock", (x0 + 1.5, y + row_h / 2), f"TOTAL  {len(self.proj.parts)} bodies in {len(self.bom)} groups", 2.6, "lm", group="bom")
        self.text("titleblock", (x1 - 1.5, y + row_h / 2), f"{total:,.2f} kg", 2.6, "rm", group="bom")
        self.sheet.meta["bom"] = dict(rows=len(self.bom), shown=len(shown), total_mass_kg=total, bodies=len(self.proj.parts))

    def build(self) -> Sheet:
        self.border()
        self.choose_layout()
        self.views()
        self.dimensions()
        self.view_labels()
        self.title_block()
        self.bom_table()
        self.sheet.meta.update(mode=self.proj.mode, notes=list(self.proj.notes), seconds=self.proj.seconds, bodies=len(self.proj.parts), selection=self.proj.selection)
        return self.sheet


def compose_sheet(design, proj: Projections, opts: DrawingOptions | None = None) -> Sheet:
    """`design` is a Design or a Context captured with make_context()."""
    opts = opts or DrawingOptions()
    if opts.sheet not in SHEETS:
        raise DesignError(f"Sheet size must be one of {', '.join(SHEETS)}.")
    return _Composer(design, proj, opts).build()


def build_sheet(design: Design, selection: str | None = None, opts: DrawingOptions | None = None, matrices: dict | None = None, progress=None) -> Sheet:
    """Everything in one call: project, then compose."""
    opts = opts or DrawingOptions()
    return compose_sheet(design, compute_projections(design, selection, matrices, opts, progress), opts)


# ======================================================================================================================================
# DXF (AutoCAD R2000, ASCII)
# ======================================================================================================================================
DXF_LINEWEIGHTS = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)       # the values DXF allows (1/100 mm)


def _dxf_lw(weight_mm: float) -> int:
    return min(DXF_LINEWEIGHTS, key=lambda v: abs(v - weight_mm * 100))


class _Dxf:
    def __init__(self):
        self.out: list = []
        self._h = 0xFF

    def pair(self, code, value):
        self.out.append(f"{code}\n{value}")

    def handle(self) -> str:
        self._h += 1
        return f"{self._h:X}"

    def text(self) -> str:
        return "\n".join(self.out) + "\n"


def export_dxf(sheet: Sheet, path, theme_unused=None) -> Path:
    """AutoCAD R2000 ASCII DXF with the five standard layers. Polylines are LWPOLYLINE, arrow heads SOLID, text TEXT; units are millimetres, y up."""
    path = Path(path)
    w, h = sheet.size
    d = _Dxf()
    P = d.pair
    # fixed handles
    H = dict(ltype_table="5", layer_table="2", style_table="3", appid_table="4", dimstyle_table="A", blockrec_table="1", vport_table="8", view_table="9", ucs_table="B",
             model_rec="1F", paper_rec="1B", model_blk="20", paper_blk="21", model_end="22", paper_end="23", root_dict="C", acad_group="D", acad_mlstyle="E",
             std_style="11", acad_appid="12", ltype_bylayer="13", ltype_byblock="14", ltype_cont="15", ltype_dashed="16", ltype_center="17", dimstyle_std="18")
    layer_handles = {key: f"{0x40 + i:X}" for i, key in enumerate(LAYERS)}
    # ---- header
    P(0, "SECTION"); P(2, "HEADER")
    P(9, "$ACADVER"); P(1, "AC1015")
    P(9, "$HANDSEED"); P(5, "FFFFF")
    P(9, "$INSUNITS"); P(70, 4)
    P(9, "$MEASUREMENT"); P(70, 1)
    P(9, "$LTSCALE"); P(40, 1.0)
    P(9, "$EXTMIN"); P(10, 0.0); P(20, 0.0); P(30, 0.0)
    P(9, "$EXTMAX"); P(10, w); P(20, h); P(30, 0.0)
    P(9, "$LIMMIN"); P(10, 0.0); P(20, 0.0)
    P(9, "$LIMMAX"); P(10, w); P(20, h)
    P(0, "ENDSEC")
    # ---- tables
    P(0, "SECTION"); P(2, "TABLES")
    P(0, "TABLE"); P(2, "VPORT"); P(5, H["vport_table"]); P(330, 0); P(100, "AcDbSymbolTable"); P(70, 1)
    P(0, "VPORT"); P(5, "30"); P(330, H["vport_table"]); P(100, "AcDbSymbolTableRecord"); P(100, "AcDbViewportTableRecord"); P(2, "*Active"); P(70, 0)
    P(10, 0.0); P(20, 0.0); P(11, 1.0); P(21, 1.0); P(12, w / 2); P(22, h / 2); P(13, 0.0); P(23, 0.0); P(14, 10.0); P(24, 10.0); P(15, 10.0); P(25, 10.0)
    P(16, 0.0); P(26, 0.0); P(36, 1.0); P(17, 0.0); P(27, 0.0); P(37, 0.0); P(40, h * 1.1); P(41, w / h); P(42, 50.0); P(43, 0.0); P(44, 0.0); P(50, 0.0); P(51, 0.0)
    P(71, 0); P(72, 100); P(73, 1); P(74, 3); P(75, 0); P(76, 0); P(77, 0); P(78, 0)
    P(0, "ENDTAB")
    P(0, "TABLE"); P(2, "LTYPE"); P(5, H["ltype_table"]); P(330, 0); P(100, "AcDbSymbolTable"); P(70, 5)

    def ltype(handle, name, desc, pattern):
        P(0, "LTYPE"); P(5, handle); P(330, H["ltype_table"]); P(100, "AcDbSymbolTableRecord"); P(100, "AcDbLinetypeTableRecord"); P(2, name); P(70, 0); P(3, desc)
        P(72, 65); P(73, len(pattern)); P(40, sum(abs(v) for v in pattern))
        for v in pattern:
            P(49, v); P(74, 0)
    ltype(H["ltype_byblock"], "ByBlock", "", [])
    ltype(H["ltype_bylayer"], "ByLayer", "", [])
    ltype(H["ltype_cont"], "CONTINUOUS", "Solid line", [])
    ltype(H["ltype_dashed"], "DASHED", "Dashed __ __ __", [HIDDEN_DASH[0], -HIDDEN_DASH[1]])
    ltype(H["ltype_center"], "CENTER", "Center ____ _ ____ _", [CENTER_DASH[0], -CENTER_DASH[1], CENTER_DASH[2], -CENTER_DASH[3]])
    P(0, "ENDTAB")
    P(0, "TABLE"); P(2, "LAYER"); P(5, H["layer_table"]); P(330, 0); P(100, "AcDbSymbolTable"); P(70, len(LAYERS) + 1)
    P(0, "LAYER"); P(5, "50"); P(330, H["layer_table"]); P(100, "AcDbSymbolTableRecord"); P(100, "AcDbLayerTableRecord"); P(2, "0"); P(70, 0); P(62, 7); P(6, "CONTINUOUS")
    for key, (name, color, ltname, weight) in LAYERS.items():
        P(0, "LAYER"); P(5, layer_handles[key]); P(330, H["layer_table"]); P(100, "AcDbSymbolTableRecord"); P(100, "AcDbLayerTableRecord")
        P(2, name); P(70, 0); P(62, color); P(6, ltname); P(370, _dxf_lw(weight))
    P(0, "ENDTAB")
    P(0, "TABLE"); P(2, "STYLE"); P(5, H["style_table"]); P(330, 0); P(100, "AcDbSymbolTable"); P(70, 1)
    P(0, "STYLE"); P(5, H["std_style"]); P(330, H["style_table"]); P(100, "AcDbSymbolTableRecord"); P(100, "AcDbTextStyleTableRecord"); P(2, "STANDARD"); P(70, 0)
    P(40, 0.0); P(41, 1.0); P(50, 0.0); P(71, 0); P(42, 2.5); P(3, "txt"); P(4, "")
    P(0, "ENDTAB")
    P(0, "TABLE"); P(2, "VIEW"); P(5, H["view_table"]); P(330, 0); P(100, "AcDbSymbolTable"); P(70, 0); P(0, "ENDTAB")
    P(0, "TABLE"); P(2, "UCS"); P(5, H["ucs_table"]); P(330, 0); P(100, "AcDbSymbolTable"); P(70, 0); P(0, "ENDTAB")
    P(0, "TABLE"); P(2, "APPID"); P(5, H["appid_table"]); P(330, 0); P(100, "AcDbSymbolTable"); P(70, 1)
    P(0, "APPID"); P(5, H["acad_appid"]); P(330, H["appid_table"]); P(100, "AcDbSymbolTableRecord"); P(100, "AcDbRegAppTableRecord"); P(2, "ACAD"); P(70, 0)
    P(0, "ENDTAB")
    P(0, "TABLE"); P(2, "DIMSTYLE"); P(5, H["dimstyle_table"]); P(330, 0); P(100, "AcDbSymbolTable"); P(70, 1); P(100, "AcDbDimStyleTable")
    P(0, "DIMSTYLE"); P(105, H["dimstyle_std"]); P(330, H["dimstyle_table"]); P(100, "AcDbSymbolTableRecord"); P(100, "AcDbDimStyleTableRecord"); P(2, "Standard"); P(70, 0)
    P(0, "ENDTAB")
    P(0, "TABLE"); P(2, "BLOCK_RECORD"); P(5, H["blockrec_table"]); P(330, 0); P(100, "AcDbSymbolTable"); P(70, 2)
    P(0, "BLOCK_RECORD"); P(5, H["model_rec"]); P(330, H["blockrec_table"]); P(100, "AcDbSymbolTableRecord"); P(100, "AcDbBlockTableRecord"); P(2, "*Model_Space")
    P(0, "BLOCK_RECORD"); P(5, H["paper_rec"]); P(330, H["blockrec_table"]); P(100, "AcDbSymbolTableRecord"); P(100, "AcDbBlockTableRecord"); P(2, "*Paper_Space")
    P(0, "ENDTAB")
    P(0, "ENDSEC")
    # ---- blocks
    P(0, "SECTION"); P(2, "BLOCKS")
    for rec, blk, end, name in ((H["model_rec"], H["model_blk"], H["model_end"], "*Model_Space"), (H["paper_rec"], H["paper_blk"], H["paper_end"], "*Paper_Space")):
        P(0, "BLOCK"); P(5, blk); P(330, rec); P(100, "AcDbEntity"); P(8, "0"); P(100, "AcDbBlockBegin"); P(2, name); P(70, 0); P(10, 0.0); P(20, 0.0); P(30, 0.0); P(3, name); P(1, "")
        P(0, "ENDBLK"); P(5, end); P(330, rec); P(100, "AcDbEntity"); P(8, "0"); P(100, "AcDbBlockEnd")
    P(0, "ENDSEC")
    # ---- entities
    P(0, "SECTION"); P(2, "ENTITIES")
    STYLE = {"continuous": "BYLAYER", "dashed": "DASHED", "center": "CENTER"}
    ALIGN = {"l": 0, "c": 1, "r": 2}
    VALIGN = {"b": 1, "m": 2, "t": 3}
    for p in sheet.prims:
        layer = LAYERS[p.layer][0]
        if p.kind == "line" and len(p.pts) >= 2:
            P(0, "LWPOLYLINE"); P(5, d.handle()); P(330, H["model_rec"]); P(100, "AcDbEntity"); P(8, layer)
            if p.style != "continuous":
                P(6, STYLE[p.style])
            P(370, _dxf_lw(p.weight or LAYERS[p.layer][3]))
            P(100, "AcDbPolyline"); P(90, len(p.pts)); P(70, 0)
            for x, y in p.pts:
                P(10, f"{x:.4f}"); P(20, f"{y:.4f}")
        elif p.kind == "fill" and len(p.pts) >= 3:
            a, b, c = p.pts[0], p.pts[1], p.pts[2]
            P(0, "SOLID"); P(5, d.handle()); P(330, H["model_rec"]); P(100, "AcDbEntity"); P(8, layer); P(100, "AcDbTrace")
            for k, pt in enumerate((a, b, c, c)):
                P(10 + k, f"{pt[0]:.4f}"); P(20 + k, f"{pt[1]:.4f}"); P(30 + k, 0.0)
            # DXF stores a SOLID's corners in the order 1, 2, 4, 3
        elif p.kind == "text" and p.text:
            x, y = p.pts[0]
            ha, va = ALIGN[p.anchor[0]], VALIGN[p.anchor[1]]
            P(0, "TEXT"); P(5, d.handle()); P(330, H["model_rec"]); P(100, "AcDbEntity"); P(8, layer); P(100, "AcDbText")
            P(10, f"{x:.4f}"); P(20, f"{y:.4f}"); P(30, 0.0); P(40, f"{p.height:.3f}"); P(1, p.text)
            if p.rot:
                P(50, f"{p.rot:.2f}")
            P(7, "STANDARD")
            if ha or va != 0:
                P(72, ha); P(11, f"{x:.4f}"); P(21, f"{y:.4f}"); P(31, 0.0)
            P(100, "AcDbText"); P(73, va)
    P(0, "ENDSEC")
    P(0, "SECTION"); P(2, "OBJECTS")
    P(0, "DICTIONARY"); P(5, H["root_dict"]); P(330, 0); P(100, "AcDbDictionary"); P(281, 1); P(3, "ACAD_GROUP"); P(350, H["acad_group"])
    P(0, "DICTIONARY"); P(5, H["acad_group"]); P(330, H["root_dict"]); P(100, "AcDbDictionary"); P(281, 1)
    P(0, "ENDSEC")
    P(0, "EOF")
    path.write_text(d.text(), encoding="ascii", errors="replace")
    return path


# ======================================================================================================================================
# SVG
# ======================================================================================================================================
THEMES = {
    "print": dict(bg="#ffffff", visible="#000000", hidden="#444444", centerlines="#cc0000", dimensions="#006400", titleblock="#00008b", text="#000000"),
    "dark": dict(bg="#0B132B", visible="#e8fbff", hidden="#5b7a99", centerlines="#ff6b6b", dimensions="#3ddc97", titleblock="#00e5ff", text="#e8fbff"),
}


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def export_svg(sheet: Sheet, path, theme: str = "print") -> Path:
    """Grouped SVG (millimetres): <g id="visible_edges">, <g id="hidden_edges">, <g id="centerlines">, <g id="dimensions">, <g id="titleblock">."""
    path = Path(path)
    w, h = sheet.size
    colors = THEMES[theme]
    groups = {"visible": "visible_edges", "hidden": "hidden_edges", "centerlines": "centerlines", "dimensions": "dimensions", "titleblock": "titleblock"}
    dash = {"dashed": " ".join(f"{v:g}" for v in HIDDEN_DASH), "center": " ".join(f"{v:g}" for v in CENTER_DASH)}
    anchors = {"l": "start", "c": "middle", "r": "end"}
    base = {"b": "alphabetic", "m": "central", "t": "hanging"}
    out = [f'<?xml version="1.0" encoding="UTF-8"?>', f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:g}mm" height="{h:g}mm" viewBox="0 0 {w:g} {h:g}">',
           f'<title>{_esc(str(sheet.meta.get("title", "satCAD drawing")))}</title>', f'<rect x="0" y="0" width="{w:g}" height="{h:g}" fill="{colors["bg"]}"/>']
    for key, gid in groups.items():
        col = colors[key]
        out.append(f'<g id="{gid}" fill="none" stroke="{col}" stroke-linecap="round" stroke-linejoin="round">')
        for p in sheet.layer(key):
            if p.kind == "line" and len(p.pts) >= 2:
                weight = p.weight or LAYERS[key][3]
                d = "M" + " L".join(f"{x:.3f},{h - y:.3f}" for x, y in p.pts)
                da = f' stroke-dasharray="{dash[p.style]}"' if p.style in dash else ""
                out.append(f'<path d="{d}" stroke-width="{weight:g}"{da}/>')
            elif p.kind == "fill" and len(p.pts) >= 3:
                d = "M" + " L".join(f"{x:.3f},{h - y:.3f}" for x, y in p.pts) + " Z"
                out.append(f'<path d="{d}" fill="{col}" stroke="none"/>')
            elif p.kind == "text" and p.text:
                x, y = p.pts[0]
                rot = f' transform="rotate({-p.rot:g} {x:.3f} {h - y:.3f})"' if p.rot else ""
                out.append(f'<text x="{x:.3f}" y="{h - y:.3f}" font-size="{p.height:g}" font-family="Helvetica, Arial, sans-serif" text-anchor="{anchors[p.anchor[0]]}" '
                           f'dominant-baseline="{base[p.anchor[1]]}" fill="{colors["text"]}" stroke="none"{rot}>{_esc(p.text)}</text>')
        out.append("</g>")
    out.append("</svg>")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


# ======================================================================================================================================
# PDF (Qt)
# ======================================================================================================================================
def export_pdf(sheet: Sheet, path, theme: str = "print") -> Path:
    """A print-ready vector PDF: the sheet is drawn into a QGraphicsScene and rendered with QPdfWriter (A3 or A2, landscape, 600 dpi)."""
    from PySide6.QtCore import QMarginsF, QRectF
    from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
    from PySide6.QtWidgets import QApplication
    from .gui.blueprint_view import BlueprintScene
    QApplication.instance() or QApplication([])
    path = Path(path)
    w, h = sheet.size
    writer = QPdfWriter(str(path))
    writer.setResolution(600)
    writer.setPageSize(QPageSize({"A3": QPageSize.A3, "A2": QPageSize.A2}.get(sheet.meta.get("sheet", "A3"), QPageSize.A3)))
    writer.setPageOrientation(QPageLayout.Landscape)
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    writer.setTitle(str(sheet.meta.get("title", "satCAD drawing")))
    writer.setCreator("satCAD")
    scene = BlueprintScene()
    scene.load(sheet, theme)
    painter = QPainter(writer)
    painter.setRenderHint(QPainter.Antialiasing)
    scene.render(painter, QRectF(0, 0, writer.width(), writer.height()), QRectF(0, 0, w, h))
    painter.end()
    return path


def export_bom_csv(design: Design, names: list, path) -> Path:
    """The full bill of materials (the sheet shows only the heaviest groups)."""
    import csv
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["item", "part", "sub-assembly", "quantity", "material", "mass_kg"])
        for item, name, assembly, qty, mat, mass in bom_rows(design, names):
            wr.writerow([item, name, assembly, qty, mat, f"{mass:.4f}"])
    return path


def main(argv=None) -> int:
    """python -m satcad.drawing_studio design.json [--part NAME] [--sheet A3|A2] [--out folder] [--formats dxf svg pdf]"""
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="satcad.drawing_studio", description="Make a dimensioned third-angle drawing sheet from a satCAD design.")
    ap.add_argument("design")
    ap.add_argument("--part", default=None, help="a part name, an assembly name or 'all' (default: everything)")
    ap.add_argument("--sheet", default="A3", choices=list(SHEETS))
    ap.add_argument("--out", default=".")
    ap.add_argument("--formats", nargs="+", default=["dxf", "svg", "pdf"], choices=["dxf", "svg", "pdf"])
    ap.add_argument("--no-hidden", action="store_true")
    args = ap.parse_args(argv)
    try:
        d = Design("x")
        d.load(args.design)
        opts = DrawingOptions(sheet=args.sheet, show_hidden=not args.no_hidden)
        sheet = build_sheet(d, args.part, opts)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        stem = d.name + (f"_{args.part}" if args.part and args.part != "all" else "")
        for fmt in args.formats:
            f = {"dxf": export_dxf, "svg": export_svg, "pdf": export_pdf}[fmt](sheet, out / f"{stem}.{fmt}")
            print(f"{fmt}: {f}")
        print(f"scale {sheet.meta['scale']}, {sheet.meta['mode']} projection, {sheet.meta['bodies']} bodies, {len(sheet.dims)} dimensions")
        return 0
    except (DesignError, ValueError, OSError, KeyError) as e:
        print(f"Can't draw: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
