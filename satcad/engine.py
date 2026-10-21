"""
Parametric design engine.

How it works (the same idea professional CAD uses):

* A design is an ordered list of FEATURES. A feature is a *recipe*: "a 600 x 400 x 300 mm chassis",
  "a radiator panel attached to the +y face of the chassis", "a 40 mm hole through the front face".
* The 3D geometry is always REBUILT from those recipes, in order. Change one number and everything that depends on it
  follows. That is what "parametric" means.
* Any numeric setting may be a FORMULA ("=wingspan/2 - chassis.width"), and the design can hold named VARIABLES, like
  NX Expressions. See expressions.py.
* Everything is in MILLIMETRES, masses in KILOGRAMS, angles in DEGREES.

Coordinates: X points forward, Y points left, Z points up, in mm. The origin is the centre of the first thing you create
unless you move it.

Feature types
-------------
  chassis    hollow or solid box, optional rounded/chamfered edges, with equipment mass inside
  box, cylinder, sphere
  panel      flat panel attached to a face of another part (radiators, solar wings, shear panels), optional hinge tilt
  extrude    a 2D profile (rectangle, circle, polygon; optionally hollow) pushed out into a solid: beams, struts, brackets
  revolve    a 2D profile spun around an axis: propellant tanks, nozzles, domes, cones
  cutout     rectangular or round hole/pocket cut into a face of an existing part
  pattern    repeat a part along a line or around an axis (thruster pods, solar rows, bolt circles)
  mirror     a mirror-image copy of a part across a plane
  assembly   a named group of parts (a subsystem) with its own placement, nestable, optionally with a hinge/joint angle
  shell      hollow out a solid to a wall thickness, leaving chosen faces open (boxes, housings, covers)
  sweep      a profile dragged along a 3D path with rounded bends: pipes, cable harnesses, bent struts
  loft       a smooth transition between cross-sections: adapters, fairings, nozzles
  fillet_edges  round or bevel chosen edges of an existing part
  hole       standard ISO metric holes (clearance, tapped, counterbore, countersink) singly, in grids or on bolt circles
  fastener   an ISO 4762 socket-head cap screw
"""
from __future__ import annotations

import contextlib
import copy
import json
import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from build123d import (Axis, Box, Circle, Color, Compound, Cone, Cylinder, Edge, Ellipse, Location, Plane, Polygon, Pos,
                       Rectangle, RectangleRounded, RegularPolygon, Rot, SlotOverall, Sphere, Vector, Wire, export_gltf,
                       export_step, export_stl, extrude, loft, mirror, offset, revolve, sweep)
from build123d import chamfer as bd_chamfer
from build123d import fillet as bd_fillet

from . import hardware
from .expressions import RESERVED, ExpressionError, evaluate, is_expression
from .materials import MATERIALS, ROLE_COLORS, density

AXES = {"x": 0, "y": 1, "z": 2}
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
PLANES = {"xy": Plane.XY, "yz": Plane.YZ, "zx": Plane.ZX}            # profile planes; normals +z, +x, +y
GEOMETRY_ATTRS = ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax", "size_x", "size_y", "size_z", "mass")


class DesignError(ValueError):
    """A problem with the design that the user can fix. Messages are written for beginners."""


# kind -> {param: default}.  REQ marks a parameter you must always provide.
REQ = "__required__"
_POSE = dict(x=0.0, y=0.0, z=0.0, rx=0.0, ry=0.0, rz=0.0)
_COMMON = dict(material="aluminum_6061", role="structure", color=None, notes="", assembly=None, layer=None)   # layer: a label for X-ray display and grouping
SCHEMAS: dict[str, dict] = {
    "chassis": {**_COMMON, "role": "chassis", "length": REQ, "width": REQ, "height": REQ, "wall": 0.0,
                "internal_mass_kg": 0.0, "fillet": 0.0, "chamfer": 0.0, **_POSE},
    "box": {**_COMMON, "length": REQ, "width": REQ, "height": REQ, "fillet": 0.0, "chamfer": 0.0, **_POSE},
    "cylinder": {**_COMMON, "radius": REQ, "height": REQ, "axis": "z", **_POSE},
    "sphere": {**_COMMON, "radius": REQ, **_POSE},
    "panel": {**_COMMON, "material": "aluminum_honeycomb", "role": "radiator", "attach_to": REQ, "side": REQ,
              "length": REQ, "width": REQ, "thickness": REQ, "gap": 0.0, "offset_span": 0.0,
              "offset_thin": 0.0, "thin_axis": None, "tilt_deg": 0.0},
    "extrude": {**_COMMON, "profile": REQ, "depth": REQ, "width": None, "height": None, "radius": None, "points": None,
                "corner_radius": None, "sides": None, "wall": 0.0, "plane": "xy", "both_sides": False,
                "taper_deg": 0.0, **_POSE},
    "shell": {"target": REQ, "open_faces": REQ, "thickness": REQ, "direction": "inward"},
    "sweep": {**_COMMON, "profile": "circle", "radius": None, "width": None, "height": None, "wall": 0.0,
              "path": REQ, "bend_radius": 0.0, **_POSE},
    "loft": {**_COMMON, "sections": REQ, "plane": "xy", **_POSE},
    "fillet_edges": {"target": REQ, "edges": REQ, "radius": 0.0, "chamfer": 0.0},
    "hole": {"target": REQ, "side": REQ, "standard": REQ, "hole_type": "clearance", "depth": 0.0, "offset_u": 0.0,
             "offset_v": 0.0, "count_u": 1, "pitch_u": 0.0, "count_v": 1, "pitch_v": 0.0, "circle_count": 0,
             "circle_diameter": 0.0, "circle_start_deg": 0.0},
    "fastener": {**_COMMON, "material": "stainless_316", "size": REQ, "length": REQ, "axis": "-z", **_POSE},
    "revolve": {**_COMMON, "points": REQ, "angle": 360.0, "axis": "z", **_POSE},
    "cutout": {"target": REQ, "side": REQ, "shape": REQ, "width": None, "height": None, "diameter": None,
               "depth": 0.0, "offset_u": 0.0, "offset_v": 0.0},
    "pattern": {"source": REQ, "mode": REQ, "count": REQ, "dx": 0.0, "dy": 0.0, "dz": 0.0, "axis": "z",
                "cx": 0.0, "cy": 0.0, "cz": 0.0, "total_angle": 360.0, "assembly": None, "grid": None},
    "mirror": {"source": REQ, "plane": REQ, "offset": 0.0, "assembly": None},
    "boolean": {"operation": REQ, "target": REQ, "tool": REQ},
    "assembly": {"parent": None, "x": 0.0, "y": 0.0, "z": 0.0, "rx": 0.0, "ry": 0.0, "rz": 0.0,
                 "joint_axis": None, "angle_deg": 0.0, "pivot_x": 0.0, "pivot_y": 0.0, "pivot_z": 0.0,
                 "angle_min": -180.0, "angle_max": 180.0, "notes": ""},
}
BOOLEAN_OPS = ("unite", "subtract", "intersect")
_POSITIVE = {"length", "width", "height", "radius", "thickness", "depth", "diameter"}
_ZERO_OK = {("cutout", "depth"), ("hole", "depth"), ("fillet_edges", "radius")}    # 0 means "through" / "not used"


@dataclass
class Feature:
    name: str
    kind: str
    raw: dict                                       # settings as the user gave them (numbers, text, or "=formulas")
    params: dict = field(default_factory=dict)      # settings after formulas are evaluated and defaults filled in


@dataclass
class Part:
    name: str
    kind: str
    role: str
    material: str
    solid: object                                   # a build123d Shape
    extra_mass_kg: float
    color: tuple
    params: dict
    equipment: object = None                        # the volume the equipment mass is spread through (chassis interior)
    assembly: str | None = None                     # the assembly (subsystem) this part belongs to
    translated_from: str | None = None              # linear-pattern copies: the source part, and the offset (lets viewers reuse its mesh)
    translation: tuple | None = None

    @property
    def volume_mm3(self) -> float:
        return float(self.solid.volume)

    @property
    def structure_mass_kg(self) -> float:
        return self.volume_mm3 * 1e-9 * density(self.material)

    @property
    def mass_kg(self) -> float:
        return self.structure_mass_kg + self.extra_mass_kg

    def bbox(self):
        bb = self.solid.bounding_box()
        return [bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]


def _r(v, n=3):
    return round(float(v), n)


def _rm(v):
    """Round a mass in kg: millimetre-scale hardware weighs grams, so small masses keep 6 decimals."""
    return round(float(v), 6 if abs(v) < 10 else 3)


def _num_list(value, what: str, min_len: int, width: int = 2) -> list:
    """Validate a list of [a, b] points."""
    if not isinstance(value, (list, tuple)) or len(value) < min_len:
        raise DesignError(f"'{what}' needs a list of at least {min_len} points, e.g. [[0, 0], [100, 0], [50, 80]].")
    out = []
    for pt in value:
        if not isinstance(pt, (list, tuple)) or len(pt) != width or not all(
                isinstance(c, (int, float)) and not isinstance(c, bool) for c in pt):
            raise DesignError(f"Every point in '{what}' must be {width} numbers, like [10, 20]. Got {pt!r}.")
        out.append((float(pt[0]), float(pt[1])))
    return out


def _bend_points(pts: list, radius: float) -> list:
    """For every interior corner of a polyline return (tangent point in, arc midpoint, tangent point out), or raise a clear error."""
    out = []
    for i in range(1, len(pts) - 1):
        p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
        d_in, d_out = p1 - p0, p2 - p1
        l_in, l_out = np.linalg.norm(d_in), np.linalg.norm(d_out)
        u, w = d_in / l_in, d_out / l_out
        cos_phi = float(np.clip(np.dot(u, w), -1, 1))
        phi = math.acos(cos_phi)                                              # turning angle at the corner
        if phi < 1e-6:                                                        # straight through: no bend needed
            out.append(None)
            continue
        if phi > math.pi - 1e-3:
            raise DesignError(f"The path doubles back on itself at point {i + 1}.")
        t = radius * math.tan(phi / 2)
        room_in = l_in / (2 if i > 1 else 1)
        room_out = l_out / (2 if i < len(pts) - 2 else 1)
        if t > min(room_in, room_out) + 1e-9:
            raise DesignError(f"The bend radius {radius:g} mm is too large for the corner at path point {i + 1}: it needs "
                              f"{t:.1f} mm of straight length on each side but only {min(room_in, room_out):.1f} mm is available. "
                              "Use a smaller bend_radius or longer segments.")
        a, b = p1 - u * t, p1 + w * t
        n = w - np.dot(w, u) * u
        n = n / np.linalg.norm(n)
        centre = a + n * radius
        mid = centre + (p1 - centre) / np.linalg.norm(p1 - centre) * radius
        out.append((a, mid, b))
    return out


def _rounded_path(pts: list, radius: float):
    """A wire through the points with every interior corner rounded by a tangent arc of the given radius."""
    bends = _bend_points(pts, radius)
    edges, cursor = [], pts[0]
    for i, bend in enumerate(bends, start=1):
        if bend is None:
            continue
        a, mid, b = bend
        edges.append(Edge.make_line(Vector(*cursor), Vector(*a)))
        edges.append(Edge.make_three_point_arc(Vector(*a), Vector(*mid), Vector(*b)))
        cursor = b
    edges.append(Edge.make_line(Vector(*cursor), Vector(*pts[-1])))
    return Wire(edges)


# --------------------------------------------------------------------------- #
class Design:
    def __init__(self, name: str = "satellite"):
        self.name = name
        self._batch_depth = 0
        self.features: dict[str, Feature] = {}
        self.parts: dict[str, Part] = {}
        self.variables: dict[str, object] = {}
        self.systems: dict | None = None                                   # wiring / tubing / fibre netlist (see satcad.systems), saved with the design
        self._var_cache: dict[str, float] = {}
        self._var_stack: list[str] = []
        self._built: set[str] = set()
        self._in_variable = False
        self._asm_loc: dict[str, Location] = {}
        self._asm_base: dict[str, Location] = {}
        self._asm_static: dict[str, Location] = {}                         # placement with every joint at 0 degrees
        self._asm_artic: dict[str, bool] = {}                              # does this assembly (or an ancestor) have a joint turned?

    # ---- formulas -------------------------------------------------------------
    def _var(self, name: str):
        if name in self._var_cache:
            return self._var_cache[name]
        if name in self.variables:
            if name in self._var_stack:
                raise ExpressionError(f"Variable '{name}' refers to itself ({' -> '.join(self._var_stack + [name])}).")
            self._var_stack.append(name)
            prev, self._in_variable = self._in_variable, True
            try:
                raw = self.variables[name]
                value = evaluate(raw, self._var, self._attr) if is_expression(raw) else raw
            except ExpressionError as e:
                raise ExpressionError(f"Variable '{name}': {e}") from None
            finally:
                self._var_stack.pop()
                self._in_variable = prev
            self._var_cache[name] = value
            return value
        if name in self.features:
            raise ExpressionError(f"'{name}' is a part. To use its size write {name}.width, {name}.xmax, {name}.mass ...")
        known = list(self.variables) or "none yet"
        raise ExpressionError(f"Unknown name '{name}'. Variables defined: {known}. Use set_variable to create one.")

    def _attr(self, owner: str, attr: str):
        if self._in_variable:
            raise ExpressionError("Variables can only use other variables, not part settings.")
        if owner not in self.features:
            raise ExpressionError(f"There is no part called '{owner}' (in '{owner}.{attr}').")
        if owner not in self._built:
            raise ExpressionError(f"'{owner}' is defined after this setting, so '{owner}.{attr}' isn't available yet. "
                                  "Define the part you refer to first.")
        params = self.features[owner].params
        if attr in params and isinstance(params[attr], (int, float)) and not isinstance(params[attr], bool):
            return params[attr]
        part = self.parts.get(owner)
        if part is not None and attr in GEOMETRY_ATTRS:
            mn, mx = part.bbox()
            return {"xmin": mn[0], "xmax": mx[0], "ymin": mn[1], "ymax": mx[1], "zmin": mn[2], "zmax": mx[2],
                    "size_x": mx[0] - mn[0], "size_y": mx[1] - mn[1], "size_z": mx[2] - mn[2], "mass": part.mass_kg}[attr]
        numeric = [k for k, v in params.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        raise ExpressionError(f"'{owner}' has no number called '{attr}'. Available: {numeric + (list(GEOMETRY_ATTRS) if part else [])}.")

    def _evaluate(self, f: Feature) -> dict:
        def walk(v, setting):
            if is_expression(v):
                try:
                    return evaluate(v, self._var, self._attr)
                except ExpressionError as e:
                    raise DesignError(f"In '{f.name}', setting '{setting}': {e}") from None
            if isinstance(v, (list, tuple)):
                return [walk(x, setting) for x in v]
            return v
        return {k: walk(v, k) for k, v in f.raw.items()}

    def set_variable(self, name: str, value) -> dict:
        """Create or change a named variable: a number, or a formula such as "=2*bay + 100"."""
        if not VAR_RE.match(name) or name in RESERVED:
            raise DesignError("Variable names must start with a letter or '_', use only letters, digits and '_', "
                              f"and not be a maths word ({', '.join(sorted(RESERVED))}).")
        if name in self.features:
            raise DesignError(f"'{name}' is already a part name. Choose a different variable name.")
        if isinstance(value, bool) or not (isinstance(value, (int, float)) or is_expression(value)):
            raise DesignError("A variable is a number, or a formula starting with '=' (e.g. \"=2*wingspan\").")
        old = self.variables.get(name, None)
        had = name in self.variables
        self.variables[name] = value
        if self._batch_depth:
            return {}
        try:
            self.rebuild()
        except Exception:
            if had:
                self.variables[name] = old
            else:
                del self.variables[name]
            self.rebuild()
            raise
        return self.list_variables()

    def delete_variable(self, name: str) -> dict:
        if name not in self.variables:
            raise DesignError(f"No variable called '{name}'. Variables: {list(self.variables) or 'none'}.")
        pattern = re.compile(rf"\b{re.escape(name)}\b(?!\.)")
        users = [f.name for f in self.features.values() if any(self._mentions(v, pattern) for v in f.raw.values())]
        users += [v for v, raw in self.variables.items() if v != name and is_expression(raw) and pattern.search(raw)]
        if users:
            raise DesignError(f"Can't delete '{name}': it is used by {users}.")
        old = self.variables.pop(name)
        try:
            self.rebuild()
        except Exception:
            self.variables[name] = old
            self.rebuild()
            raise
        return self.list_variables()

    @staticmethod
    def _mentions(value, pattern) -> bool:
        if is_expression(value):
            return bool(pattern.search(value))
        if isinstance(value, (list, tuple)):
            return any(Design._mentions(v, pattern) for v in value)
        return False

    def list_variables(self) -> dict:
        out = {}
        for name, raw in self.variables.items():
            out[name] = dict(definition=raw, value=self._var_cache.get(name, raw if not is_expression(raw) else None))
        return out

    # ---- validation of evaluated settings --------------------------------------
    @staticmethod
    def _resolve(kind: str, params: dict) -> dict:
        if kind not in SCHEMAS:
            raise DesignError(f"Unknown part type '{kind}'. Choose one of: {', '.join(SCHEMAS)}.")
        schema = SCHEMAS[kind]
        unknown = set(params) - set(schema)
        if unknown:
            raise DesignError(f"'{kind}' has no setting called {sorted(unknown)}. Allowed settings: {sorted(schema)}.")
        full = {k: (params[k] if k in params else v) for k, v in schema.items()}
        missing = [k for k, v in full.items() if isinstance(v, str) and v == REQ]
        if missing:
            raise DesignError(f"A '{kind}' needs these settings: {missing}.")
        for k in _POSITIVE & set(full):
            if (kind, k) in _ZERO_OK:                                  # validated by the feature's own rules below
                continue
            if full[k] is not None and not (isinstance(full[k], (int, float)) and full[k] > 0):
                raise DesignError(f"'{k}' must be a number greater than 0 (you gave {full[k]}).")
        if "material" in full:
            try:
                density(full["material"])
            except ValueError as e:                                    # keep every user-facing problem a DesignError
                raise DesignError(str(e)) from None
        for k in ("fillet", "chamfer"):
            if k in full and (not isinstance(full[k], (int, float)) or full[k] < 0):
                raise DesignError(f"'{k}' must be 0 or more (you gave {full[k]}).")
        if kind in ("chassis", "box"):
            smallest = min(full["length"], full["width"], full["height"])
            if full["fillet"] and full["chamfer"]:
                raise DesignError("Use either fillet (rounded edges) or chamfer (angled edges), not both.")
            if max(full["fillet"], full["chamfer"]) * 2 >= smallest:
                raise DesignError("The fillet/chamfer is too big: twice its size must be smaller than every dimension.")
        if kind == "cylinder" and full["axis"] not in AXES:
            raise DesignError("A cylinder's axis must be 'x', 'y' or 'z'.")
        if kind == "chassis" and full["wall"] < 0:
            raise DesignError("Wall thickness cannot be negative. Use 0 for a solid block.")
        if kind == "chassis" and full["wall"] > 0 and full["wall"] * 2 >= min(full["length"], full["width"], full["height"]):
            raise DesignError("The wall is too thick: twice the wall must be smaller than every chassis dimension.")
        if kind == "panel":
            side = str(full["side"]).lower()
            if len(side) != 2 or side[0] not in "+-" or side[1] not in AXES:
                raise DesignError("A panel's 'side' must look like '+y', '-y', '+x', '-x', '+z' or '-z'.")
            if full["thin_axis"] is not None and full["thin_axis"] not in AXES:
                raise DesignError("'thin_axis' must be 'x', 'y' or 'z' (or leave it out).")
        if kind == "extrude":
            Design._validate_extrude(full)
        if kind == "revolve":
            pts = _num_list(full["points"], "points", 3)
            if any(r < 0 for r, _ in pts):
                raise DesignError("In a revolve profile every point is [radius, height], and the radius can't be negative.")
            if not (isinstance(full["angle"], (int, float)) and 0 < full["angle"] <= 360):
                raise DesignError("'angle' must be greater than 0 and at most 360 degrees.")
            if full["axis"] not in AXES:
                raise DesignError("A revolve's axis must be 'x', 'y' or 'z'.")
        if kind == "boolean":
            if str(full["operation"]).lower() not in BOOLEAN_OPS:
                raise DesignError(f"A boolean operation must be one of: {', '.join(BOOLEAN_OPS)}.")
            if full["target"] == full["tool"]:
                raise DesignError("A boolean needs two different bodies: the target and the tool.")
        if kind == "cutout":
            side = str(full["side"]).lower()
            if len(side) != 2 or side[0] not in "+-" or side[1] not in AXES:
                raise DesignError("'side' must look like '+y', '-y', '+x', '-x', '+z' or '-z'.")
            if full["shape"] == "rect":
                if not (full["width"] and full["height"] and full["width"] > 0 and full["height"] > 0):
                    raise DesignError("A rectangular cutout needs width and height greater than 0.")
            elif full["shape"] == "circle":
                if not full["diameter"]:
                    raise DesignError("A round cutout (hole) needs a diameter greater than 0.")
            else:
                raise DesignError("A cutout's shape must be 'rect' or 'circle'.")
            if not isinstance(full["depth"], (int, float)) or full["depth"] < 0:
                raise DesignError("'depth' must be 0 (cut all the way through) or a positive number.")
        if kind == "pattern" and full["mode"] == "grid":
            g = full["grid"]
            if not isinstance(g, (list, tuple)) or not 1 <= len(g) <= 4 or any(
                    not isinstance(a, (list, tuple)) or len(a) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in a) for a in g):
                raise DesignError("A grid pattern needs 'grid': one to four steps, each [dx, dy, dz, count], e.g. [[400, 0, 0, 6], [0, 450, 0, 4]] for 6 by 4 copies.")
            counts = [int(a[3]) for a in g]
            if any(c < 1 or c != a[3] for c, a in zip(counts, g)) or any(not any(a[:3]) for a, c in zip(g, counts) if c > 1):
                raise DesignError("Each grid step needs a whole-number count of at least 1 and, when the count is above 1, a spacing in dx, dy and/or dz.")
            total = int(np.prod(counts))
            if not 2 <= total <= 5000:
                raise DesignError(f"A grid pattern makes {total} copies; it must be between 2 and 5000.")
            full["count"] = total
        elif kind == "pattern":
            n = full["count"]
            if isinstance(n, float) and n.is_integer():
                n = full["count"] = int(n)
            if not isinstance(n, int) or isinstance(n, bool) or not 2 <= n <= 500:
                raise DesignError("A pattern's 'count' must be a whole number from 2 to 500.")
            if full["mode"] not in ("linear", "circular", "grid"):
                raise DesignError("A pattern's mode must be 'linear', 'circular' or 'grid'.")
            if full["mode"] == "linear" and not any(full[k] for k in ("dx", "dy", "dz")):
                raise DesignError("A linear pattern needs a spacing: set dx, dy and/or dz (mm between copies).")
            if full["axis"] not in AXES:
                raise DesignError("A circular pattern's axis must be 'x', 'y' or 'z'.")
            if not (isinstance(full["total_angle"], (int, float)) and 0 < full["total_angle"] <= 360):
                raise DesignError("'total_angle' must be greater than 0 and at most 360 degrees.")
        if kind == "mirror" and full["plane"] not in PLANES:
            raise DesignError("A mirror plane must be 'xy', 'yz' or 'zx'.")
        if kind == "shell":
            faces = full["open_faces"]
            if not isinstance(faces, (list, tuple)) or not faces or any(not Design._valid_side(f) for f in faces):
                raise DesignError("'open_faces' must be a list of faces left open, like ['+z'] or ['+z', '-x'].")
            if full["direction"] not in ("inward", "outward"):
                raise DesignError("'direction' must be 'inward' (keeps the outer size) or 'outward'.")
        if kind == "sweep":
            Design._validate_sweep(full)
        if kind == "loft":
            Design._validate_loft(full)
        if kind == "fillet_edges":
            sel = str(full["edges"])
            if not (sel == "all" or (sel.startswith("parallel:") and sel[9:] in AXES) or
                    (sel.startswith("face:") and Design._valid_side(sel[5:]))):
                raise DesignError("'edges' must be 'all', 'parallel:x' (or y, z), or 'face:+z' (any face side).")
            r, c = full["radius"], full["chamfer"]
            if not all(isinstance(v, (int, float)) and v >= 0 for v in (r, c)) or (r > 0) == (c > 0):
                raise DesignError("Give either 'radius' (rounded) or 'chamfer' (bevelled) greater than 0, not both and not neither.")
        if kind == "hole":
            Design._validate_hole(full)
        if kind == "fastener":
            try:
                hardware.spec(full["size"])
            except ValueError as e:
                raise DesignError(str(e)) from None
            if not Design._valid_side(full["axis"]):
                raise DesignError("A fastener's 'axis' is the direction its tip points: '+x' '-x' '+y' '-y' '+z' or '-z'.")
        if kind == "assembly":
            if full["joint_axis"] is not None and full["joint_axis"] not in AXES:
                raise DesignError("An assembly's 'joint_axis' must be 'x', 'y' or 'z' (or leave it out for a fixed assembly).")
            for k in ("x", "y", "z", "rx", "ry", "rz", "angle_deg", "pivot_x", "pivot_y", "pivot_z", "angle_min", "angle_max"):
                if not isinstance(full[k], (int, float)) or isinstance(full[k], bool):
                    raise DesignError(f"'{k}' must be a number (you gave {full[k]!r}).")
            if full["angle_min"] > full["angle_max"]:
                raise DesignError("'angle_min' must not be larger than 'angle_max'.")
        return full

    @staticmethod
    def _valid_side(side) -> bool:
        side = str(side).lower()
        return len(side) == 2 and side[0] in "+-" and side[1] in AXES

    @staticmethod
    def _validate_hole(full: dict) -> None:
        if not Design._valid_side(full["side"]):
            raise DesignError("'side' must look like '+y', '-y', '+x', '-x', '+z' or '-z'.")
        try:
            h = hardware.spec(full["standard"])
        except ValueError as e:
            raise DesignError(str(e)) from None
        if full["hole_type"] not in hardware.HOLE_KINDS:
            raise DesignError(f"A hole's 'hole_type' must be one of: {', '.join(hardware.HOLE_KINDS)}.")
        if full["hole_type"] == "countersink" and not h["csk_dia"]:
            raise DesignError(f"No countersink data for {str(full['standard']).upper()}; use a counterbore or a smaller size.")
        if not isinstance(full["depth"], (int, float)) or full["depth"] < 0:
            raise DesignError("'depth' must be 0 (all the way through) or a positive number.")
        for k in ("count_u", "count_v", "circle_count"):
            v = full[k]
            if isinstance(v, float) and v.is_integer():
                full[k] = v = int(v)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0 or v > 500:
                raise DesignError(f"'{k}' must be a whole number from 0 to 500.")
        if full["count_u"] < 1 or full["count_v"] < 1:
            raise DesignError("'count_u' and 'count_v' must be at least 1.")
        if (full["count_u"] > 1 and not full["pitch_u"]) or (full["count_v"] > 1 and not full["pitch_v"]):
            raise DesignError("A hole grid needs a pitch (spacing in mm) for each direction that has more than one hole.")
        if full["circle_count"] > 0 and not full["circle_diameter"] > 0:
            raise DesignError("A bolt circle needs 'circle_diameter' greater than 0.")

    @staticmethod
    def _validate_sweep(full: dict) -> None:
        pts = full["path"]
        if not isinstance(pts, (list, tuple)) or len(pts) < 2 or any(
                not isinstance(q, (list, tuple)) or len(q) != 3 or not all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in q)
                for q in pts):
            raise DesignError("'path' needs at least 2 points, each [x, y, z] in mm, e.g. [[0,0,0],[500,0,0],[500,400,0]].")
        v = [np.array(q, dtype=float) for q in pts]
        seg = [np.linalg.norm(b - a) for a, b in zip(v, v[1:])]
        if min(seg) < 1e-6:
            raise DesignError("Two consecutive path points are the same.")
        prof = full["profile"]
        if prof not in ("circle", "rect"):
            raise DesignError("A sweep 'profile' must be 'circle' or 'rect'.")
        if prof == "circle" and not (full["radius"] and full["radius"] > 0):
            raise DesignError("A circular sweep needs a radius greater than 0.")
        if prof == "rect" and not (full["width"] and full["height"] and full["width"] > 0 and full["height"] > 0):
            raise DesignError("A rectangular sweep needs width and height greater than 0.")
        wall = full["wall"]
        if not isinstance(wall, (int, float)) or wall < 0:
            raise DesignError("'wall' must be 0 (solid) or a positive thickness for a hollow pipe.")
        if wall > 0 and wall * 2 >= (full["radius"] * 2 if prof == "circle" else min(full["width"], full["height"])):
            raise DesignError("The pipe wall is too thick for the profile size.")
        r = full["bend_radius"]
        if not isinstance(r, (int, float)) or r < 0:
            raise DesignError("'bend_radius' must be 0 (straight path) or a positive radius.")
        if len(pts) > 2 and r == 0:
            raise DesignError("A path with corners needs a 'bend_radius' greater than 0: real pipes and harnesses can't turn "
                              "through a sharp corner, and the geometry kernel can't sweep one reliably.")
        if r > 0:
            _bend_points(v, r)                                   # raises a clear error if a bend doesn't fit

    @staticmethod
    def _validate_loft(full: dict) -> None:
        secs = full["sections"]
        if not isinstance(secs, (list, tuple)) or len(secs) < 2:
            raise DesignError("A loft needs at least 2 sections, e.g. [['rect', 0, 200, 200], ['circle', 300, 80]].")
        if full["plane"] not in PLANES:
            raise DesignError("'plane' must be 'xy', 'yz' or 'zx' (sections are stacked along +z, +x or +y).")
        last = None
        for sec in secs:
            ok = isinstance(sec, (list, tuple)) and len(sec) >= 3 and sec[0] in ("rect", "circle") and all(
                isinstance(c, (int, float)) and not isinstance(c, bool) for c in sec[1:])
            if not ok or (sec[0] == "circle" and (len(sec) != 3 or sec[2] <= 0)) or (
                    sec[0] == "rect" and (len(sec) != 4 or sec[2] <= 0 or sec[3] <= 0)):
                raise DesignError(f"Bad section {sec!r}. Use ['circle', position, radius] or ['rect', position, width, height].")
            if last is not None and sec[1] <= last:
                raise DesignError("Loft section positions must increase (each one further along the axis than the last).")
            last = sec[1]

    @staticmethod
    def _validate_extrude(full: dict) -> None:
        prof = full["profile"]
        if prof not in ("rect", "circle", "polygon", "slot", "rounded_rect", "ellipse", "ngon"):
            raise DesignError("An extrude 'profile' must be 'rect', 'circle', 'polygon', 'slot', 'rounded_rect', 'ellipse' or 'ngon'.")
        if prof in ("slot", "rounded_rect", "ellipse") and not (full["width"] and full["height"] and full["width"] > 0 and full["height"] > 0):
            raise DesignError(f"A '{prof}' profile needs width and height greater than 0"
                              + (" (width = overall slot length, height = slot width)." if prof == "slot" else "."))
        if prof == "slot" and full["width"] <= full["height"]:
            raise DesignError("A slot's width (overall length) must be larger than its height (slot width).")
        if prof == "rounded_rect":
            cr = full["corner_radius"]
            if not (isinstance(cr, (int, float)) and cr > 0) or cr * 2 >= min(full["width"], full["height"]):
                raise DesignError("A 'rounded_rect' needs a corner_radius greater than 0 and under half its smaller side.")
        if prof == "ngon":
            sd = full["sides"]
            if isinstance(sd, float) and sd.is_integer():
                sd = full["sides"] = int(sd)
            if not (isinstance(sd, int) and 3 <= sd <= 64) or not (full["radius"] and full["radius"] > 0):
                raise DesignError("An 'ngon' needs sides (3 to 64) and a radius (distance from centre to a corner) greater than 0.")
        if prof in ("slot", "rounded_rect", "ellipse", "ngon") and full["wall"]:
            raise DesignError("Hollow ('wall') profiles are only available for rect and circle.")
        if full["plane"] not in PLANES:
            raise DesignError("'plane' must be 'xy', 'yz' or 'zx' (the extrusion goes along +z, +x or +y respectively).")
        if prof == "rect" and not (full["width"] and full["height"] and full["width"] > 0 and full["height"] > 0):
            raise DesignError("A rectangle profile needs width and height greater than 0.")
        if prof == "circle" and not (full["radius"] and full["radius"] > 0):
            raise DesignError("A circle profile needs a radius greater than 0.")
        if prof == "polygon":
            pts = _num_list(full["points"], "points", 3)
            area = 0.5 * abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1])))
            if area <= 1e-9:
                raise DesignError("The polygon points enclose no area (they are in a straight line).")
        wall = full["wall"]
        if not isinstance(wall, (int, float)) or wall < 0:
            raise DesignError("'wall' must be 0 (solid) or a positive thickness for a hollow tube.")
        if wall > 0:
            if prof == "polygon":
                raise DesignError("Hollow ('wall') profiles are only available for rect and circle.")
            if wall * 2 >= (min(full["width"], full["height"]) if prof == "rect" else full["radius"] * 2):
                raise DesignError("The tube wall is too thick for the profile size.")
            if full["taper_deg"]:
                raise DesignError("A hollow profile can't also be tapered.")
        if not isinstance(full["taper_deg"], (int, float)) or abs(full["taper_deg"]) >= 89:
            raise DesignError("'taper_deg' must be between -89 and 89 degrees.")

    # ---- building geometry --------------------------------------------------
    def _color(self, p: dict) -> tuple:
        return tuple(p["color"]) if p.get("color") else ROLE_COLORS.get(p["role"], MATERIALS[p["material"]].color)

    def _place(self, p: dict, solid):
        """Apply the part's own pose, then its assembly's placement (so poses are relative to the assembly)."""
        placed = Pos(p["x"], p["y"], p["z"]) * Rot(p["rx"], p["ry"], p["rz"]) * solid
        loc = self._asm_loc.get(p.get("assembly")) if p.get("assembly") else None
        return loc * placed if loc is not None else placed

    @staticmethod
    def _edge_treatment(solid, p: dict):
        if p.get("fillet"):
            return bd_fillet(solid.edges(), radius=p["fillet"])
        if p.get("chamfer"):
            return bd_chamfer(solid.edges(), length=p["chamfer"])
        return solid

    def _make_part(self, f: Feature, solid, extra: float = 0.0, equipment=None, name: str | None = None) -> Part:
        p = f.params
        solid.label = name or f.name
        solid.color = Color(*self._color(p))
        return Part(name or f.name, f.kind, p["role"], p["material"], solid, float(extra), self._color(p), dict(p), equipment,
                    p.get("assembly"))

    def _build_into(self, f: Feature) -> None:
        p = f.params
        kind = f.kind
        if kind in ("chassis", "box"):
            solid = self._edge_treatment(Box(p["length"], p["width"], p["height"]), p)
            equipment = None
            extra = 0.0
            if kind == "chassis":
                extra = p["internal_mass_kg"]
                w = p["wall"]
                if w > 0:
                    inner = Box(p["length"] - 2 * w, p["width"] - 2 * w, p["height"] - 2 * w)
                    r = max(p["fillet"] - w, 0)
                    if r > 0 and 2 * r < min(p["length"], p["width"], p["height"]) - 2 * w:
                        inner = bd_fillet(inner.edges(), radius=r)
                    solid = solid - inner
                    equipment = self._place(p, Box(p["length"] - 2 * w, p["width"] - 2 * w, p["height"] - 2 * w))
                else:
                    equipment = self._place(p, Box(p["length"], p["width"], p["height"]))
            solid = self._place(p, solid)
            self.parts[f.name] = self._make_part(f, solid, extra, equipment if extra else None)
        elif kind == "cylinder":
            solid = self._axis_to(p["axis"], Cylinder(p["radius"], p["height"]))
            self.parts[f.name] = self._make_part(f, self._place(p, solid))
        elif kind == "sphere":
            self.parts[f.name] = self._make_part(f, self._place(p, Sphere(p["radius"])))
        elif kind == "panel":
            self.parts[f.name] = self._make_part(f, self._articulate(p, self._build_panel(p)))
        elif kind == "extrude":
            self.parts[f.name] = self._make_part(f, self._place(p, self._build_extrude(p)))
        elif kind == "revolve":
            solid = self._axis_to(p["axis"], self._build_revolve(p))
            self.parts[f.name] = self._make_part(f, self._place(p, solid))
        elif kind == "sweep":
            self.parts[f.name] = self._make_part(f, self._place(p, self._build_sweep(p)))
        elif kind == "loft":
            self.parts[f.name] = self._make_part(f, self._place(p, self._build_loft(p)))
        elif kind == "fastener":
            self.parts[f.name] = self._make_part(f, self._place(p, self._build_fastener(p)))
        elif kind == "shell":
            self._apply_shell(f)
        elif kind == "fillet_edges":
            self._apply_fillet_edges(f)
        elif kind == "hole":
            self._apply_hole(f)
        elif kind == "assembly":
            self._build_assembly(f)
        elif kind == "cutout":
            self._apply_cutout(f)
        elif kind == "boolean":
            self._apply_boolean(f)
        elif kind == "pattern":
            self._build_pattern(f)
        elif kind == "mirror":
            self._build_mirror(f)

    def _articulate(self, p: dict, solid):
        """A panel is built from its parent's geometry, so it ignores its assembly's PLACEMENT, but it must still swing with
        the assembly's HINGES: apply only the articulation (current placement relative to the all-joints-at-zero placement)."""
        name = p.get("assembly")
        if name and self._asm_artic.get(name):
            return self._asm_loc[name] * self._asm_static[name].inverse() * solid
        return solid

    def _build_assembly(self, f: Feature) -> None:
        """An assembly is a placement (and optional hinge rotation) that its member parts are built inside."""
        p = f.params
        parent = p["parent"]
        if parent is not None and parent not in self._asm_loc:
            raise DesignError(f"Assembly '{f.name}' is inside '{parent}', which isn't an assembly defined before it. "
                              f"Assemblies so far: {list(self._asm_loc) or 'none'}.")
        loc = (self._asm_loc[parent] if parent else Location()) * (Pos(p["x"], p["y"], p["z"]) * Rot(p["rx"], p["ry"], p["rz"]))
        self._asm_base[f.name] = loc                                       # placement before the joint rotation
        pose = Pos(p["x"], p["y"], p["z"]) * Rot(p["rx"], p["ry"], p["rz"])
        self._asm_static[f.name] = (self._asm_static[parent] if parent else Location()) * pose
        self._asm_artic[f.name] = bool(parent and self._asm_artic[parent]) or bool(p["joint_axis"] and p["angle_deg"])
        if p["joint_axis"] and p["angle_deg"]:
            ang = [p["angle_deg"] if i == AXES[p["joint_axis"]] else 0 for i in range(3)]
            pivot = (p["pivot_x"], p["pivot_y"], p["pivot_z"])
            loc = loc * (Pos(*pivot) * Rot(*ang) * Pos(-pivot[0], -pivot[1], -pivot[2]))
        self._asm_loc[f.name] = loc

    @staticmethod
    def _axis_to(axis: str, solid):
        """Solids are built along +Z; turn them to lie along the requested axis."""
        if axis == "x":
            return Rot(0, 90, 0) * solid
        if axis == "y":
            return Rot(-90, 0, 0) * solid
        return solid

    @staticmethod
    def _build_extrude(p: dict):
        prof = p["profile"]
        if prof == "rect":
            sketch = Rectangle(p["width"], p["height"])
            if p["wall"] > 0:
                sketch = sketch - Rectangle(p["width"] - 2 * p["wall"], p["height"] - 2 * p["wall"])
        elif prof == "circle":
            sketch = Circle(p["radius"])
            if p["wall"] > 0:
                sketch = sketch - Circle(p["radius"] - p["wall"])
        elif prof == "slot":
            sketch = SlotOverall(p["width"], p["height"])
        elif prof == "rounded_rect":
            sketch = RectangleRounded(p["width"], p["height"], p["corner_radius"])
        elif prof == "ellipse":
            sketch = Ellipse(p["width"] / 2, p["height"] / 2)
        elif prof == "ngon":
            sketch = RegularPolygon(p["radius"], p["sides"])
        else:
            sketch = Polygon(*_num_list(p["points"], "points", 3), align=None)
        sketch = PLANES[p["plane"]] * sketch
        return extrude(sketch, amount=p["depth"], both=p["both_sides"], taper=p["taper_deg"])

    @staticmethod
    def _build_revolve(p: dict):
        face = Plane.XZ * Polygon(*_num_list(p["points"], "points", 3), align=None)     # points are [radius, height]
        return revolve(face, axis=Axis.Z, revolution_arc=p["angle"])

    def _build_panel(self, p: dict):
        if p["attach_to"] not in self.parts:
            raise DesignError(f"Panel can't attach to '{p['attach_to']}': there is no such part yet. "
                              f"Existing parts: {list(self.parts) or 'none'}.")
        ref = self.parts[p["attach_to"]]
        side = str(p["side"]).lower()
        sign = 1 if side[0] == "+" else -1
        a = AXES[side[1]]                                           # axis the panel extends along
        thin = AXES[p["thin_axis"]] if p["thin_axis"] else (2 if a in (0, 1) else 0)
        if thin == a:
            raise DesignError("The panel's thin direction can't be the same as the direction it sticks out in.")
        span = ({0, 1, 2} - {a, thin}).pop()                        # the remaining axis: the panel's width
        mn, mx = ref.bbox()
        centre = [(mn[i] + mx[i]) / 2 for i in range(3)]
        dims = [0.0, 0.0, 0.0]
        dims[a], dims[span], dims[thin] = p["length"], p["width"], p["thickness"]
        c = list(centre)
        face = mx[a] if sign > 0 else mn[a]
        c[a] = face + sign * (p["gap"] + p["length"] / 2)
        c[span] = centre[span] + p["offset_span"]
        c[thin] = centre[thin] + p["offset_thin"]
        solid = Pos(*c) * Box(*dims)
        if p["tilt_deg"]:
            # The hinge sits on the attached edge. Which of the panel's two edges (top or bottom of its thickness)
            # is the pivot depends on the tilt direction: pivoting about the wrong one swings the inner corner
            # INTO the part it is attached to. Try both and keep the one that digs in least.
            direction = [0.0, 0.0, 0.0]
            direction[span] = 1.0
            plane = face + sign * p["gap"]                                  # where the panel meets the parent part
            best = None
            for edge in (+1, -1):
                hinge = list(c)
                hinge[a] = plane
                hinge[thin] = c[thin] + edge * p["thickness"] / 2
                tilted = solid.rotate(Axis(Vector(*hinge), Vector(*direction)), p["tilt_deg"])
                bb = tilted.bounding_box()
                lo, hi = [bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]
                dug_in = (plane - lo[a]) if sign > 0 else (hi[a] - plane)
                if best is None or dug_in < best[0] - 1e-9:
                    best = (dug_in, tilted)
            solid = best[1]
        return solid

    @staticmethod
    def _build_sweep(p: dict):
        pts = [np.array(q, dtype=float) for q in p["path"]]
        if len(pts) == 2 or p["bend_radius"] == 0:
            path = Wire([Edge.make_line(Vector(*pts[0]), Vector(*pts[1]))])
        else:
            path = _rounded_path(pts, p["bend_radius"])
        d0 = pts[1] - pts[0]
        d0 = d0 / np.linalg.norm(d0)
        plane = Plane(origin=tuple(pts[0]), z_dir=tuple(d0))
        if p["profile"] == "circle":
            sketch = Circle(p["radius"])
            if p["wall"] > 0:
                sketch = sketch - Circle(p["radius"] - p["wall"])
        else:
            sketch = Rectangle(p["width"], p["height"])
            if p["wall"] > 0:
                sketch = sketch - Rectangle(p["width"] - 2 * p["wall"], p["height"] - 2 * p["wall"])
        return sweep(plane * sketch, path=path)

    @staticmethod
    def _build_loft(p: dict):
        faces = []
        for sec in p["sections"]:
            shape = Circle(sec[2]) if sec[0] == "circle" else Rectangle(sec[2], sec[3])
            faces.append(PLANES[p["plane"]] * (Pos(0, 0, sec[1]) * shape))
        return loft(faces)

    @staticmethod
    def _build_fastener(p: dict):
        h = hardware.spec(p["size"])
        hh, sd = h["head_height"], h["socket_depth"]
        solid = Pos(0, 0, hh / 2) * Cylinder(h["head_dia"] / 2, hh) + Pos(0, 0, -p["length"] / 2) * Cylinder(h["d"] / 2, p["length"])
        socket = Pos(0, 0, hh - sd) * extrude(RegularPolygon(h["socket_af"] / math.sqrt(3), 6), amount=sd + 0.5)
        solid = solid - socket
        tip = str(p["axis"]).lower()
        turn = {"-z": (0, 0, 0), "+z": (180, 0, 0), "-x": (0, 90, 0), "+x": (0, -90, 0), "+y": (90, 0, 0), "-y": (-90, 0, 0)}[tip]
        return Rot(*turn) * solid

    @staticmethod
    def _faces_on_side(solid, side: str) -> list:
        """The flat faces that point toward `side` and lie furthest in that direction."""
        a, sign = AXES[side[1]], (1 if side[0] == "+" else -1)
        found = []
        for face in solid.faces():
            try:
                n = face.normal_at()
            except Exception:
                continue
            if (n.X, n.Y, n.Z)[a] * sign > 0.99:
                c = face.center()
                found.append(((c.X, c.Y, c.Z)[a] * sign, face))
        if not found:
            raise DesignError(f"There is no flat face pointing toward {side}.")
        best = max(v for v, _ in found)
        return [face for v, face in found if abs(v - best) < 1e-6]

    def _frame(self, part: Part, side: str) -> dict:
        """The coordinate frame of one face of a part: which axis it points along, the two sliding axes, and the face centre."""
        a = AXES[side[1]]
        sign = 1 if side[0] == "+" else -1
        mn, mx = part.bbox()
        return dict(a=a, sign=sign, u=(a + 1) % 3, v=(a + 2) % 3, face=mx[a] if sign > 0 else mn[a],
                    size=mx[a] - mn[a], centre=[(mn[i] + mx[i]) / 2 for i in range(3)])

    def _finish_edit(self, part: Part, new, what: str) -> None:
        if new.volume <= 0:
            raise DesignError(f"{what} would leave nothing of '{part.name}'.")
        new.label, new.color = part.name, Color(*part.color)
        part.solid = new

    def _apply_shell(self, f: Feature) -> None:
        p = f.params
        part = self._target(p["target"], "target part")
        faces = []
        for side in p["open_faces"]:
            faces += self._faces_on_side(part.solid, str(side).lower())
        before = part.solid.volume
        amount = -p["thickness"] if p["direction"] == "inward" else p["thickness"]
        try:
            new = offset(part.solid, amount=amount, openings=faces)
        except Exception as e:
            raise DesignError(f"Couldn't shell '{p['target']}' with thickness {p['thickness']}: {e}. "
                              "Try a smaller thickness.") from None
        if abs(new.volume - before) < 1e-6:
            fillet = part.params.get("fillet") or 0
            hint = (f" The part has rounded edges (fillet {fillet:g} mm): a wall thicker than the corner radius can't follow "
                    "them. Use a thinner wall or a larger fillet." if fillet and p["thickness"] > fillet else
                    " Try a thinner wall.")
            raise DesignError(f"The shell failed on '{p['target']}': the geometry kernel couldn't offset the faces.{hint}")
        self._finish_edit(part, new, "The shell")

    def _apply_fillet_edges(self, f: Feature) -> None:
        p = f.params
        part = self._target(p["target"], "target part")
        sel = str(p["edges"])
        solid = part.solid
        if sel == "all":
            edges = solid.edges()
        elif sel.startswith("parallel:"):
            edges = solid.edges().filter_by({"x": Axis.X, "y": Axis.Y, "z": Axis.Z}[sel[9:]])
        else:
            edges = [e for face in self._faces_on_side(solid, sel[5:].lower()) for e in face.edges()]
        if not len(edges):
            raise DesignError(f"No edges matched '{sel}' on '{p['target']}'.")
        before = solid.volume
        try:
            new = bd_fillet(edges, radius=p["radius"]) if p["radius"] > 0 else bd_chamfer(edges, length=p["chamfer"])
        except Exception as e:
            raise DesignError(f"Couldn't {'round' if p['radius'] > 0 else 'bevel'} those edges of '{p['target']}': {e}. "
                              "The size may be too large for the geometry.") from None
        if abs(new.volume - before) < 1e-6:
            raise DesignError("The edge treatment changed nothing.")
        self._finish_edit(part, new, "The edge treatment")

    def _apply_hole(self, f: Feature) -> None:
        p = f.params
        part = self._target(p["target"], "target part")
        side = str(p["side"]).lower()
        fr = self._frame(part, side)
        a, sign, u, v = fr["a"], fr["sign"], fr["u"], fr["v"]
        h = hardware.spec(p["standard"])
        kind = p["hole_type"]
        through = p["depth"] == 0
        depth = fr["size"] if through else p["depth"]
        dia = h["tap_drill"] if kind == "tapped" else h["clearance"]

        if p["circle_count"] > 0:                                          # bolt circle
            r = p["circle_diameter"] / 2
            spots = [(p["offset_u"] + r * math.cos(math.radians(p["circle_start_deg"] + 360.0 * k / p["circle_count"])),
                      p["offset_v"] + r * math.sin(math.radians(p["circle_start_deg"] + 360.0 * k / p["circle_count"])))
                     for k in range(p["circle_count"])]
        else:                                                              # grid centred on the offset
            spots = [(p["offset_u"] + (i - (p["count_u"] - 1) / 2) * p["pitch_u"],
                      p["offset_v"] + (j - (p["count_v"] - 1) / 2) * p["pitch_v"])
                     for i in range(p["count_u"]) for j in range(p["count_v"])]

        def tool_at(du, dv):
            at = [0.0, 0.0, 0.0]
            at[u], at[v] = fr["centre"][u] + du, fr["centre"][v] + dv
            def cyl(d, length, start):                                      # a cylinder starting `start` mm inside the face
                c = list(at)
                c[a] = fr["face"] - sign * (start + length / 2) + sign * 0.5
                return Pos(*c) * self._axis_to("xyz"[a], Cylinder(d / 2, length + 1.0))
            t = cyl(dia, depth, 0.0)
            if kind == "counterbore":
                t = t + cyl(h["cbore_dia"], h["cbore_depth"], 0.0)
            elif kind == "countersink":
                cone_h = (h["csk_dia"] - dia) / 2                            # 90 degree included angle
                over = 0.5
                r_out, r_in = h["csk_dia"] / 2 + over, dia / 2
                c = list(at)
                c[a] = fr["face"] - sign * (cone_h / 2) + sign * over / 2
                wide_up = sign > 0
                cone = Cone(r_in if wide_up else r_out, r_out if wide_up else r_in, cone_h + over)
                t = t + Pos(*c) * self._axis_to("xyz"[a], cone)
            return t

        before = part.solid.volume
        new = part.solid
        for du, dv in spots:
            new = new - tool_at(du, dv)
        if abs(new.volume - before) < 1e-6:
            raise DesignError(f"The holes missed '{p['target']}': nothing was removed. Check the face, offsets and size.")
        self._finish_edit(part, new, "The holes")

    def _target(self, name: str, what: str) -> Part:
        if name not in self.parts:
            raise DesignError(f"The {what} '{name}' doesn't exist (yet). Existing parts: {list(self.parts) or 'none'}.")
        return self.parts[name]

    def _apply_cutout(self, f: Feature) -> None:
        p = f.params
        part = self._target(p["target"], "target part")
        side = str(p["side"]).lower()
        sign = 1 if side[0] == "+" else -1
        a = AXES[side[1]]
        u, v = (a + 1) % 3, (a + 2) % 3                                    # offset_u / offset_v run along these axes
        mn, mx = part.bbox()
        centre = [(mn[i] + mx[i]) / 2 for i in range(3)]
        size_a = mx[a] - mn[a]
        depth = size_a if p["depth"] == 0 else p["depth"]
        face = mx[a] if sign > 0 else mn[a]
        length = depth + 1.0                                               # poke 1 mm out so the cut is clean
        at = [0.0, 0.0, 0.0]
        at[a] = face - sign * depth / 2 + sign * 0.5
        at[u] = centre[u] + p["offset_u"]
        at[v] = centre[v] + p["offset_v"]
        if p["shape"] == "rect":
            dims = [0.0, 0.0, 0.0]
            dims[a], dims[u], dims[v] = length, p["width"], p["height"]
            tool = Pos(*at) * Box(*dims)
        else:
            tool = Pos(*at) * self._axis_to("xyz"[a], Cylinder(p["diameter"] / 2, length))
        before = part.solid.volume
        cut = part.solid - tool
        if abs(cut.volume - before) < 1e-6:
            raise DesignError(f"The cutout missed '{p['target']}': nothing was removed. Check the face, offsets and size.")
        cut.label, cut.color = part.name, Color(*part.color)
        part.solid = cut

    def _apply_boolean(self, f: Feature) -> None:
        """Unite / subtract / intersect two bodies. The result keeps the target's name; the tool body is consumed (as in NX)."""
        p = f.params
        op = str(p["operation"]).lower()
        target, tool = self._target(p["target"], "target body"), self._target(p["tool"], "tool body")
        before = target.solid.volume
        if op == "unite":
            result = target.solid + tool.solid
        elif op == "subtract":
            result = target.solid - tool.solid
        else:
            result = target.solid & tool.solid
        try:
            volume = float(result.volume)
            pieces = len(result.solids())
        except Exception:
            volume, pieces = 0.0, 0
        if op == "intersect" and volume <= 1e-6:
            raise DesignError(f"The intersection of '{target.name}' and '{tool.name}' is empty: the bodies do not overlap.")
        if op == "subtract" and abs(volume - before) < 1e-6:
            raise DesignError(f"Subtracting '{tool.name}' removed nothing from '{target.name}': the bodies do not overlap.")
        if op == "unite" and pieces != 1:
            raise DesignError(f"'{target.name}' and '{tool.name}' do not touch or overlap, so they cannot be united into one body.")
        if pieces == 0 or volume <= 1e-6:
            raise DesignError(f"The result of the {op} would be empty.")
        result.label, result.color = target.name, Color(*target.color)
        target.solid = result
        del self.parts[tool.name]

    def _derive(self, src: Part, name: str, fn) -> Part:
        solid = fn(src.solid)
        solid.label, solid.color = name, Color(*src.color)
        return replace(src, name=name, solid=solid, equipment=fn(src.equipment) if src.equipment is not None else None,
                       params=dict(src.params))

    def _build_grid_pattern(self, f: Feature, src: Part) -> None:
        """A grid pattern: up to four steps [dx, dy, dz, count]; the first step varies fastest. Copy k (2 and up) is named <pattern>_<k>."""
        steps = [(float(a[0]), float(a[1]), float(a[2]), int(a[3])) for a in f.params["grid"]]
        k = 0
        for index in np.ndindex(*[c for *_, c in reversed(steps)]):
            idx = tuple(reversed(index))                                         # first step fastest
            off = tuple(sum(i * st[axis] for i, st in zip(idx, steps)) for axis in range(3))
            k += 1
            if k == 1:
                continue                                                         # the source itself is copy 1
            name = f"{f.name}_{k}"
            if name in self.features or name in self.parts:
                raise DesignError(f"The pattern would create a part called '{name}', which already exists. Rename the pattern.")
            self.parts[name] = self._derive(src, name, lambda s, off=off: s.moved(Location(off)))
            self.parts[name].assembly = f.params.get("assembly") or src.assembly
            self.parts[name].translated_from = src.name
            self.parts[name].translation = off

    def _build_pattern(self, f: Feature) -> None:
        p = f.params
        src = self._target(p["source"], "source part")
        if p["mode"] == "grid":
            self._build_grid_pattern(f, src)
            return
        n = p["count"]
        direction = [0.0, 0.0, 0.0]
        direction[AXES[p["axis"]]] = 1.0
        step = p["total_angle"] / n if p["total_angle"] == 360 else p["total_angle"] / (n - 1)
        for i in range(1, n):
            name = f"{f.name}_{i + 1}"
            if name in self.features or name in self.parts:
                raise DesignError(f"The pattern would create a part called '{name}', which already exists. Rename the pattern.")
            if p["mode"] == "linear":
                fn = lambda s, i=i: s.moved(Location((i * p["dx"], i * p["dy"], i * p["dz"])))
            else:
                fn = lambda s, i=i: s.rotate(Axis(Vector(p["cx"], p["cy"], p["cz"]), Vector(*direction)), step * i)
            self.parts[name] = self._derive(src, name, fn)
            self.parts[name].assembly = p.get("assembly") or src.assembly
            if p["mode"] == "linear":
                self.parts[name].translated_from = src.name
                self.parts[name].translation = (i * p["dx"], i * p["dy"], i * p["dz"])

    def _build_mirror(self, f: Feature) -> None:
        p = f.params
        src = self._target(p["source"], "source part")
        plane = PLANES[p["plane"]].offset(p["offset"])
        self.parts[f.name] = self._derive(src, f.name, lambda s: mirror(s, about=plane))
        self.parts[f.name].assembly = p.get("assembly") or src.assembly

    def rebuild(self) -> None:
        """Regenerate every part from its recipe, in order, re-evaluating all formulas."""
        self.parts = {}
        self._asm_loc, self._asm_base, self._asm_static, self._asm_artic = {}, {}, {}, {}
        self._var_cache, self._var_stack, self._built, self._in_variable = {}, [], set(), False
        for name in self.variables:
            try:
                self._var(name)
            except ExpressionError as e:
                raise DesignError(str(e)) from None
        for f in self.features.values():
            f.params = self._resolve(f.kind, self._evaluate(f))
            member_of = f.params.get("assembly")
            if member_of is not None and member_of not in self._asm_loc:
                raise DesignError(f"'{f.name}' is in assembly '{member_of}', which isn't an assembly defined before it. "
                                  f"Assemblies so far: {list(self._asm_loc) or 'none'}. Create it first with add_assembly.")
            self._build_into(f)
            self._built.add(f.name)

    # ---- editing ------------------------------------------------------------
    def _name_taken(self, name: str) -> bool:
        return name in self.features or name in self.parts or name in self.variables

    @contextlib.contextmanager
    def batch(self):
        """Add or edit many features while rebuilding only once, at the end (a design with hundreds of features is otherwise rebuilt after
        every single add). If the final rebuild fails, the design returns to exactly how it was before the batch and the error is raised."""
        if self._batch_depth:                         # nested: the outermost batch does the work
            yield
            return
        snapshot = copy.deepcopy(self.to_json())
        self._batch_depth = 1
        try:
            yield
        except BaseException:
            self._batch_depth = 0
            self.load_data(snapshot)
            raise
        self._batch_depth = 0
        try:
            self.rebuild()
        except BaseException:
            self.load_data(snapshot)
            raise

    def add(self, kind: str, name: str, **params) -> dict:
        if not NAME_RE.match(name):
            raise DesignError("Names may only use letters, digits, '_' and '-' (max 40 characters).")
        if self._name_taken(name):
            raise DesignError(f"A part or variable called '{name}' already exists. Pick another name, or use edit to change it.")
        if kind not in SCHEMAS:
            raise DesignError(f"Unknown part type '{kind}'. Choose one of: {', '.join(SCHEMAS)}.")
        self.features[name] = Feature(name, kind, dict(params))
        if self._batch_depth:
            return dict(name=name, type=kind, deferred=True)
        try:
            self.rebuild()
        except Exception:
            del self.features[name]
            self.rebuild()
            raise
        return self.describe(name) if name in self.parts else self.describe_feature(name)

    def edit(self, name: str, **changes) -> dict:
        if name not in self.features:
            raise DesignError(f"No part called '{name}'. Existing parts: {list(self.features)}.")
        old = self.features[name]
        raw = {**old.raw, **changes}
        for k in ("kind", "name"):
            raw.pop(k, None)
        self.features[name] = Feature(name, old.kind, raw)
        if self._batch_depth:
            return dict(name=name, deferred=True)
        try:
            self.rebuild()
        except Exception:
            self.features[name] = old
            self.rebuild()
            raise
        return self.describe(name) if name in self.parts else self.describe_feature(name)

    def _dependants(self, name: str) -> list[str]:
        attr = re.compile(rf"\b{re.escape(name)}\.")
        out = []
        for f in self.features.values():
            if f.name == name:
                continue
            refs = [f.raw.get(k) for k in ("attach_to", "target", "tool", "source", "assembly", "parent")]
            if name in refs or any(self._mentions(v, attr) for v in f.raw.values()):
                out.append(f.name)
        out += [v for v, raw in self.variables.items() if is_expression(raw) and attr.search(raw)]
        return out

    def remove(self, name: str) -> None:
        if name not in self.features:
            raise DesignError(f"No part called '{name}'. Existing parts: {list(self.features)}.")
        dependants = self._dependants(name)
        if dependants:
            raise DesignError(f"Can't remove '{name}': {dependants} are attached to it or use it. Remove those first.")
        old = self.features.pop(name)
        try:
            self.rebuild()
        except Exception:
            self.features[name] = old
            self.rebuild()
            raise

    def clear(self, name: str | None = None) -> None:
        self.features, self.parts, self.variables, self.systems = {}, {}, {}, None
        if name:
            self.name = name

    # ---- reading ------------------------------------------------------------
    def describe_feature(self, name: str) -> dict:
        f = self.features[name]
        if f.kind == "assembly":
            return dict(name=name, type="assembly", path=self.assembly_path(name),
                        settings={k: v for k, v in f.params.items() if v is not None},
                        expressions={k: v for k, v in f.raw.items() if is_expression(v)},
                        parts=[n for n, p in self.parts.items() if p.assembly == name])
        if f.kind == "pattern":
            made = [n for n in self.parts if re.fullmatch(rf"{re.escape(name)}_\d+", n)]
        else:
            made = [name] if name in self.parts else []
        return dict(name=f.name, type=f.kind, settings={k: v for k, v in f.params.items() if v is not None},
                    expressions={k: v for k, v in f.raw.items() if is_expression(v)}, creates_parts=made)

    def describe(self, name: str) -> dict:
        p = self.parts[name]
        mn, mx = p.bbox()
        feat = self.features.get(name)
        c = p.solid.center()
        return dict(name=p.name, type=p.kind, role=p.role, material=p.material, assembly=self.assembly_path(p.assembly),
                    size_mm=[_r(mx[i] - mn[i], 2) for i in range(3)],
                    bbox_min_mm=[_r(v, 2) for v in mn], bbox_max_mm=[_r(v, 2) for v in mx],
                    volume_cm3=_r(p.volume_mm3 / 1000, 4 if p.volume_mm3 < 1e5 else 2), mass_kg=_rm(p.mass_kg),
                    centre_mm=[_r(c.X, 2), _r(c.Y, 2), _r(c.Z, 2)],
                    settings={k: v for k, v in p.params.items() if v not in (None, "") and k not in ("color",)},
                    expressions={k: v for k, v in (feat.raw.items() if feat else []) if is_expression(v)})

    def list_parts(self) -> list[dict]:
        return [self.describe(n) for n in self.parts]

    def list_features(self) -> list[dict]:
        return [dict(name=f.name, type=f.kind, settings=f.raw) for f in self.features.values()]

    # ---- mass properties ------------------------------------------------------
    def assembly_path(self, name: str | None) -> str | None:
        """'thermal/radiators' for an assembly called radiators inside thermal."""
        chain = []
        while name:
            chain.append(name)
            name = self.features[name].params.get("parent") if name in self.features else None
        return "/".join(reversed(chain)) if chain else None

    def joints(self) -> list[dict]:
        """Every hinge in the design, with its pivot point and axis in WORLD coordinates (at any current angle), the parts it
        moves (including parts in nested assemblies), and its allowed range. Used by the 3D viewer's joint sliders."""
        out = []
        for name, f in self.features.items():
            if f.kind != "assembly" or not f.params.get("joint_axis"):
                continue
            p = f.params
            base = self._asm_base[name]
            pivot = Vector(p["pivot_x"], p["pivot_y"], p["pivot_z"])
            axis = Vector(*[1.0 if i == AXES[p["joint_axis"]] else 0.0 for i in range(3)])
            line = base * Edge.make_line(pivot, pivot + axis)                 # carry the pivot and axis through the placement
            a, b = line.start_point(), line.end_point()
            direction = np.array([b.X - a.X, b.Y - a.Y, b.Z - a.Z])
            ancestors, parent = [], p.get("parent")
            while parent:
                if self.features[parent].params.get("joint_axis"):
                    ancestors.append(parent)
                parent = self.features[parent].params.get("parent")
            out.append(dict(assembly=name, path=self.assembly_path(name), angle_deg=p["angle_deg"],
                            angle_min=p["angle_min"], angle_max=p["angle_max"],
                            pivot_mm=[_r(a.X, 3), _r(a.Y, 3), _r(a.Z, 3)],
                            axis=[_r(v, 6) for v in direction / np.linalg.norm(direction)],
                            variable=(f.raw.get("angle_deg")[1:].strip() if is_expression(f.raw.get("angle_deg"))
                                      and re.fullmatch(r"=\s*[A-Za-z_]\w*\s*", f.raw["angle_deg"]) else None),
                            parts=[q.name for q in self._members(name)], inside_joints=ancestors))
        return out

    def _members(self, assembly: str) -> list[Part]:
        """Every part in an assembly, including those in assemblies nested inside it."""
        def inside(a):
            while a:
                if a == assembly:
                    return True
                a = self.features[a].params.get("parent") if a in self.features else None
            return False
        return [p for p in self.parts.values() if inside(p.assembly)]

    def assembly_tree(self) -> dict:
        """The product structure: each assembly with rolled-up mass, centre of mass and inertia, nested."""
        asm = {n: f for n, f in self.features.items() if f.kind == "assembly"}

        def node(name):
            members = self._members(name)
            mp = self.mass_properties(members) if members else {}
            direct = [p.name for p in self.parts.values() if p.assembly == name]
            out = dict(name=name, path=self.assembly_path(name), part_count=len(members), parts=direct,
                       mass_kg=_rm(mp.get("total_mass_kg", 0.0)))
            if members and mp.get("total_mass_kg", 0) > 0:
                out["centre_of_mass_mm"] = [_r(v, 1) for v in mp["centre_of_mass_mm"]]
                out["principal_moments_kg_m2"] = [_r(v, 3) for v in mp["principal_moments_kg_m2"]]
            if asm[name].params.get("joint_axis"):
                out["joint"] = dict(axis=asm[name].params["joint_axis"], angle_deg=asm[name].params["angle_deg"],
                                    pivot_mm=[asm[name].params[k] for k in ("pivot_x", "pivot_y", "pivot_z")])
            out["children"] = [node(n) for n, f in asm.items() if f.params.get("parent") == name]
            return out

        return dict(assemblies=[node(n) for n, f in asm.items() if not f.params.get("parent")],
                    ungrouped=[p.name for p in self.parts.values() if not p.assembly])

    def _mass_items(self, parts=None):
        """Yield (mass kg, centre of mass mm, inertia kg*mm^2 about that centre, role) for every mass in the design."""
        for p in (self.parts.values() if parts is None else parts):
            rho = density(p.material) * 1e-9                                     # kg per mm^3
            yield (p.structure_mass_kg, np.array(list(p.solid.center())), np.array(p.solid.matrix_of_inertia) * rho, p.role)
            if p.extra_mass_kg > 0:
                if p.equipment is not None and p.equipment.volume > 0:
                    rho_e = p.extra_mass_kg / p.equipment.volume
                    yield (p.extra_mass_kg, np.array(list(p.equipment.center())),
                           np.array(p.equipment.matrix_of_inertia) * rho_e, p.role)
                else:                                                            # no volume to spread it through: a point mass
                    yield (p.extra_mass_kg, np.array(list(p.solid.center())), np.zeros((3, 3)), p.role)

    def mass_properties(self, parts=None) -> dict:
        """Total mass, centre of mass and the inertia tensor about it (kg*m^2), from the real solids.
        Pass `parts` (a list of Part) to get the same for just those parts, for example one assembly."""
        items = list(self._mass_items(parts))
        total = sum(m for m, *_ in items)
        if total <= 0:
            return dict(total_mass_kg=0.0)
        com = sum(m * c for m, c, _, _ in items) / total
        inertia = np.zeros((3, 3))
        for m, c, i_own, _ in items:
            d = c - com
            inertia += i_own + m * (np.dot(d, d) * np.eye(3) - np.outer(d, d))      # parallel-axis theorem
        inertia *= 1e-6                                                              # kg*mm^2 -> kg*m^2
        inertia = (inertia + inertia.T) / 2
        moments, axes = np.linalg.eigh(inertia)
        return dict(total_mass_kg=total, centre_of_mass_mm=com, inertia_kg_m2=inertia,
                    principal_moments_kg_m2=moments, principal_axes=axes.T)

    def measure(self) -> dict:
        if not self.parts:
            return dict(parts=0, note="The design is empty.")
        mp = self.mass_properties()
        by_role: dict[str, float] = {}
        for p in self.parts.values():
            by_role[p.role] = by_role.get(p.role, 0.0) + p.mass_kg
        lo, hi = [math.inf] * 3, [-math.inf] * 3
        for p in self.parts.values():
            mn, mx = p.bbox()
            lo, hi = [min(a, b) for a, b in zip(lo, mn)], [max(a, b) for a, b in zip(hi, mx)]
        total = mp["total_mass_kg"]
        out = dict(parts=len(self.parts), total_mass_kg=_rm(total),
                   centre_of_mass_mm=[_r(v, 1) for v in mp["centre_of_mass_mm"]] if total > 0 else None,
                   overall_size_mm=[_r(hi[i] - lo[i], 1) for i in range(3)],
                   overall_bbox_min_mm=[_r(v, 1) for v in lo], overall_bbox_max_mm=[_r(v, 1) for v in hi],
                   mass_by_role_kg={k: _r(v, 3) for k, v in sorted(by_role.items())})
        if any(f.kind == "assembly" for f in self.features.values()):
            def flat(n, acc):
                acc.append(dict(path=n["path"], mass_kg=n["mass_kg"], parts=n["part_count"]))
                for c in n["children"]:
                    flat(c, acc)
                return acc
            tree = self.assembly_tree()
            summary = []
            for a in tree["assemblies"]:
                flat(a, summary)
            out["assemblies"] = summary
        if total > 0:
            i = mp["inertia_kg_m2"]
            out.update(
                inertia_kg_m2=[[_r(i[r][c], 3) for c in range(3)] for r in range(3)],
                principal_moments_kg_m2=[_r(v, 3) for v in mp["principal_moments_kg_m2"]],
                principal_axes=[[_r(v, 4) for v in row] for row in mp["principal_axes"]])
        return out

    def bom(self) -> list[dict]:
        """Bill of materials: one row per part with material, quantity, mass and size."""
        rows = []
        for p in self.parts.values():
            mn, mx = p.bbox()
            rows.append(dict(part=p.name, assembly=self.assembly_path(p.assembly) or "", type=p.kind, role=p.role,
                             material=p.material, quantity=1,
                             mass_kg=_rm(p.mass_kg), structure_mass_kg=_rm(p.structure_mass_kg),
                             equipment_mass_kg=_rm(p.extra_mass_kg), volume_cm3=_r(p.volume_mm3 / 1000, 4 if p.volume_mm3 < 1e5 else 2),
                             size_x_mm=_r(mx[0] - mn[0], 1), size_y_mm=_r(mx[1] - mn[1], 1), size_z_mm=_r(mx[2] - mn[2], 1)))
        return rows

    def export_bom(self, path) -> str:
        import csv
        rows = self.bom()
        if not rows:
            raise DesignError("Nothing to list: the design is empty.")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
            w.writerow({**{k: "" for k in rows[0]}, "part": "TOTAL", "mass_kg": _r(sum(r["mass_kg"] for r in rows), 3),
                        "quantity": len(rows), "volume_cm3": _r(sum(r["volume_cm3"] for r in rows), 2)})
        return str(path)

    def check(self, touch_tolerance_mm: float = 0.05) -> dict:
        """Find parts that overlap each other (a real design error) and parts floating free of the rest."""
        names = list(self.parts)
        boxes = {n: self.parts[n].bbox() for n in names}

        def near(a, b, margin=0.0):
            """Do the bounding boxes of two parts come within `margin` mm of each other? A cheap test that skips most pairs."""
            (alo, ahi), (blo, bhi) = boxes[a], boxes[b]
            return all(alo[k] <= bhi[k] + margin and blo[k] <= ahi[k] + margin for k in range(3))

        overlaps, threads = [], []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                if not near(names[i], names[j]):
                    continue
                pa, pb = self.parts[names[i]], self.parts[names[j]]
                vol = _intersection_volume(pa.solid, pb.solid)
                if vol > 1.0:                                       # more than 1 mm^3
                    screw = pa if pa.kind == "fastener" else (pb if pb.kind == "fastener" else None)
                    if screw is not None and vol <= self._thread_allowance(screw) * 1.1:
                        threads.append(dict(fastener=screw.name, part=(pb if screw is pa else pa).name,
                                            engagement_mm3=_r(vol, 1)))          # a screw in a tapped hole overlaps by design
                    else:
                        overlaps.append(dict(a=names[i], b=names[j], overlap_cm3=_r(vol / 1000, 3)))
        floating = []
        if len(names) > 1:
            for n in names:
                candidates = [m for m in names if m != n and near(n, m, touch_tolerance_mm)]
                if any(self.parts[n].solid.distance_to(self.parts[m].solid) <= touch_tolerance_mm for m in candidates):
                    continue                                        # touches a neighbour: not floating
                nearest = self._nearest_gap(n, names, boxes)
                if nearest > touch_tolerance_mm:
                    floating.append(dict(part=n, nearest_gap_mm=_r(nearest, 2)))
        ok = not overlaps
        return dict(ok=ok, overlaps=overlaps, thread_engagements=threads, floating_parts=floating,
                    advice=("No overlapping parts." if ok else "Parts overlap: move them apart or shrink one.")
                    + (" Floating parts don't touch anything: add a gap=0 attachment or a connecting part."
                       if floating else ""))

    def _nearest_gap(self, name: str, names: list, boxes: dict) -> float:
        """Distance from one part to its nearest neighbour. Candidates are tried in order of bounding-box distance and the search stops as
        soon as no remaining part can be closer, so a big assembly does not need a solid-to-solid distance for every pair."""
        def box_gap(m):
            (alo, ahi), (blo, bhi) = boxes[name], boxes[m]
            return math.sqrt(sum(max(0.0, alo[k] - bhi[k], blo[k] - ahi[k]) ** 2 for k in range(3)))
        order = sorted((m for m in names if m != name), key=box_gap)
        best = math.inf
        for m in order:
            if box_gap(m) >= best:
                break
            best = min(best, self.parts[name].solid.distance_to(self.parts[m].solid))
        return best

    @staticmethod
    def _thread_allowance(screw: "Part") -> float:
        """Most a screw can overlap a tapped hole by design: the ring between its nominal and tap-drill diameters over its length."""
        h = hardware.spec(screw.params["size"])
        return math.pi / 4 * (h["d"] ** 2 - h["tap_drill"] ** 2) * screw.params["length"]

    # ---- files ----------------------------------------------------------------
    def compound(self) -> Compound:
        return Compound(children=[p.solid for p in self.parts.values()], label=self.name)

    def export(self, folder, basename: str, formats=("step", "stl", "glb")) -> dict:
        if not self.parts:
            raise DesignError("Nothing to export: the design is empty.")
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        out, comp = {}, self.compound()
        for fmt in formats:
            path = folder / f"{basename}.{'step' if fmt == 'step' else fmt}"
            if fmt == "step":
                export_step(comp, str(path))
            elif fmt == "stl":
                export_stl(comp, str(path))
            elif fmt == "glb":
                export_gltf(comp, str(path), binary=True)
            else:
                raise DesignError(f"Unknown export format '{fmt}'. Use step, stl or glb.")
            out[fmt] = dict(path=str(path), kilobytes=round(path.stat().st_size / 1024, 1))
        return out

    def to_json(self) -> dict:
        data = dict(format="satcad-2", name=self.name, units="mm", variables=self.variables,
                    features=[dict(name=f.name, kind=f.kind, params=f.raw) for f in self.features.values()])
        if self.systems:
            data["systems"] = self.systems
        return data

    def save(self, path) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_json(), indent=2))
        return str(path)

    def load(self, path) -> None:
        self.load_data(json.loads(Path(path).read_text()))

    def load_data(self, data: dict) -> None:
        """Replace this design with one described by a to_json() dictionary (what load() reads from a file)."""
        if not isinstance(data, dict) or data.get("format") not in ("satcad-1", "satcad-2"):
            raise DesignError("That file isn't a satcad design.")
        self.clear(data.get("name", "satellite"))
        self.variables = dict(data.get("variables", {}))
        for f in data["features"]:
            self.features[f["name"]] = Feature(f["name"], f["kind"], dict(f["params"]))
        self.systems = data.get("systems") or None
        self.rebuild()


def _intersection_volume(a, b) -> float:
    try:
        res = a.intersect(b)
    except Exception:
        return 0.0
    if res is None:
        return 0.0
    try:
        return float(res.volume)
    except Exception:
        return float(sum(getattr(s, "volume", 0.0) for s in res))
