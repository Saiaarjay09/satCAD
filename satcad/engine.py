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
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from build123d import (Axis, Box, Circle, Color, Compound, Cylinder, Location, Plane, Polygon, Pos, Rectangle, Rot,
                       Sphere, Vector, export_gltf, export_step, export_stl, extrude, mirror, revolve)
from build123d import chamfer as bd_chamfer
from build123d import fillet as bd_fillet

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
_COMMON = dict(material="aluminum_6061", role="structure", color=None, notes="")
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
                "wall": 0.0, "plane": "xy", "both_sides": False, "taper_deg": 0.0, **_POSE},
    "revolve": {**_COMMON, "points": REQ, "angle": 360.0, "axis": "z", **_POSE},
    "cutout": {"target": REQ, "side": REQ, "shape": REQ, "width": None, "height": None, "diameter": None,
               "depth": 0.0, "offset_u": 0.0, "offset_v": 0.0},
    "pattern": {"source": REQ, "mode": REQ, "count": REQ, "dx": 0.0, "dy": 0.0, "dz": 0.0, "axis": "z",
                "cx": 0.0, "cy": 0.0, "cz": 0.0, "total_angle": 360.0},
    "mirror": {"source": REQ, "plane": REQ, "offset": 0.0},
}
_POSITIVE = {"length", "width", "height", "radius", "thickness", "depth", "diameter"}


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


# --------------------------------------------------------------------------- #
class Design:
    def __init__(self, name: str = "satellite"):
        self.name = name
        self.features: dict[str, Feature] = {}
        self.parts: dict[str, Part] = {}
        self.variables: dict[str, object] = {}
        self._var_cache: dict[str, float] = {}
        self._var_stack: list[str] = []
        self._built: set[str] = set()
        self._in_variable = False

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
            if k == "depth" and kind == "cutout":                      # 0 means "all the way through"; checked below
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
        if kind == "pattern":
            n = full["count"]
            if isinstance(n, float) and n.is_integer():
                n = full["count"] = int(n)
            if not isinstance(n, int) or isinstance(n, bool) or not 2 <= n <= 500:
                raise DesignError("A pattern's 'count' must be a whole number from 2 to 500.")
            if full["mode"] not in ("linear", "circular"):
                raise DesignError("A pattern's mode must be 'linear' or 'circular'.")
            if full["mode"] == "linear" and not any(full[k] for k in ("dx", "dy", "dz")):
                raise DesignError("A linear pattern needs a spacing: set dx, dy and/or dz (mm between copies).")
            if full["axis"] not in AXES:
                raise DesignError("A circular pattern's axis must be 'x', 'y' or 'z'.")
            if not (isinstance(full["total_angle"], (int, float)) and 0 < full["total_angle"] <= 360):
                raise DesignError("'total_angle' must be greater than 0 and at most 360 degrees.")
        if kind == "mirror" and full["plane"] not in PLANES:
            raise DesignError("A mirror plane must be 'xy', 'yz' or 'zx'.")
        return full

    @staticmethod
    def _validate_extrude(full: dict) -> None:
        prof = full["profile"]
        if prof not in ("rect", "circle", "polygon"):
            raise DesignError("An extrude 'profile' must be 'rect', 'circle' or 'polygon'.")
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
        return tuple(p["color"]) if p.get("color") else ROLE_COLORS.get(p["role"], MATERIALS[p["material"]][1])

    @staticmethod
    def _orient(p: dict, solid):
        return Pos(p["x"], p["y"], p["z"]) * Rot(p["rx"], p["ry"], p["rz"]) * solid

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
        return Part(name or f.name, f.kind, p["role"], p["material"], solid, float(extra), self._color(p), dict(p), equipment)

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
                    equipment = self._orient(p, Box(p["length"] - 2 * w, p["width"] - 2 * w, p["height"] - 2 * w))
                else:
                    equipment = self._orient(p, Box(p["length"], p["width"], p["height"]))
            solid = self._orient(p, solid)
            self.parts[f.name] = self._make_part(f, solid, extra, equipment if extra else None)
        elif kind == "cylinder":
            solid = self._axis_to(p["axis"], Cylinder(p["radius"], p["height"]))
            self.parts[f.name] = self._make_part(f, self._orient(p, solid))
        elif kind == "sphere":
            self.parts[f.name] = self._make_part(f, self._orient(p, Sphere(p["radius"])))
        elif kind == "panel":
            self.parts[f.name] = self._make_part(f, self._build_panel(p))
        elif kind == "extrude":
            self.parts[f.name] = self._make_part(f, self._orient(p, self._build_extrude(p)))
        elif kind == "revolve":
            solid = self._axis_to(p["axis"], self._build_revolve(p))
            self.parts[f.name] = self._make_part(f, self._orient(p, solid))
        elif kind == "cutout":
            self._apply_cutout(f)
        elif kind == "pattern":
            self._build_pattern(f)
        elif kind == "mirror":
            self._build_mirror(f)

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

    def _derive(self, src: Part, name: str, fn) -> Part:
        solid = fn(src.solid)
        solid.label, solid.color = name, Color(*src.color)
        return replace(src, name=name, solid=solid, equipment=fn(src.equipment) if src.equipment is not None else None,
                       params=dict(src.params))

    def _build_pattern(self, f: Feature) -> None:
        p = f.params
        src = self._target(p["source"], "source part")
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

    def _build_mirror(self, f: Feature) -> None:
        p = f.params
        src = self._target(p["source"], "source part")
        plane = PLANES[p["plane"]].offset(p["offset"])
        self.parts[f.name] = self._derive(src, f.name, lambda s: mirror(s, about=plane))

    def rebuild(self) -> None:
        """Regenerate every part from its recipe, in order, re-evaluating all formulas."""
        self.parts = {}
        self._var_cache, self._var_stack, self._built, self._in_variable = {}, [], set(), False
        for name in self.variables:
            try:
                self._var(name)
            except ExpressionError as e:
                raise DesignError(str(e)) from None
        for f in self.features.values():
            f.params = self._resolve(f.kind, self._evaluate(f))
            self._build_into(f)
            self._built.add(f.name)

    # ---- editing ------------------------------------------------------------
    def _name_taken(self, name: str) -> bool:
        return name in self.features or name in self.parts or name in self.variables

    def add(self, kind: str, name: str, **params) -> dict:
        if not NAME_RE.match(name):
            raise DesignError("Names may only use letters, digits, '_' and '-' (max 40 characters).")
        if self._name_taken(name):
            raise DesignError(f"A part or variable called '{name}' already exists. Pick another name, or use edit to change it.")
        if kind not in SCHEMAS:
            raise DesignError(f"Unknown part type '{kind}'. Choose one of: {', '.join(SCHEMAS)}.")
        self.features[name] = Feature(name, kind, dict(params))
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
            refs = [f.raw.get(k) for k in ("attach_to", "target", "source")]
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
        self.features, self.parts, self.variables = {}, {}, {}
        if name:
            self.name = name

    # ---- reading ------------------------------------------------------------
    def describe_feature(self, name: str) -> dict:
        f = self.features[name]
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
        return dict(name=p.name, type=p.kind, role=p.role, material=p.material,
                    size_mm=[_r(mx[i] - mn[i], 2) for i in range(3)],
                    bbox_min_mm=[_r(v, 2) for v in mn], bbox_max_mm=[_r(v, 2) for v in mx],
                    volume_cm3=_r(p.volume_mm3 / 1000, 2), mass_kg=_r(p.mass_kg, 3),
                    centre_mm=[_r(c.X, 2), _r(c.Y, 2), _r(c.Z, 2)],
                    settings={k: v for k, v in p.params.items() if v not in (None, "") and k not in ("color",)},
                    expressions={k: v for k, v in (feat.raw.items() if feat else []) if is_expression(v)})

    def list_parts(self) -> list[dict]:
        return [self.describe(n) for n in self.parts]

    def list_features(self) -> list[dict]:
        return [dict(name=f.name, type=f.kind, settings=f.raw) for f in self.features.values()]

    # ---- mass properties ------------------------------------------------------
    def _mass_items(self):
        """Yield (mass kg, centre of mass mm, inertia kg*mm^2 about that centre, role) for every mass in the design."""
        for p in self.parts.values():
            rho = density(p.material) * 1e-9                                     # kg per mm^3
            yield (p.structure_mass_kg, np.array(list(p.solid.center())), np.array(p.solid.matrix_of_inertia) * rho, p.role)
            if p.extra_mass_kg > 0:
                if p.equipment is not None and p.equipment.volume > 0:
                    rho_e = p.extra_mass_kg / p.equipment.volume
                    yield (p.extra_mass_kg, np.array(list(p.equipment.center())),
                           np.array(p.equipment.matrix_of_inertia) * rho_e, p.role)
                else:                                                            # no volume to spread it through: a point mass
                    yield (p.extra_mass_kg, np.array(list(p.solid.center())), np.zeros((3, 3)), p.role)

    def mass_properties(self) -> dict:
        """Total mass, centre of mass and the inertia tensor about it (kg*m^2), from the real solids."""
        items = list(self._mass_items())
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
        out = dict(parts=len(self.parts), total_mass_kg=_r(total, 3),
                   centre_of_mass_mm=[_r(v, 1) for v in mp["centre_of_mass_mm"]] if total > 0 else None,
                   overall_size_mm=[_r(hi[i] - lo[i], 1) for i in range(3)],
                   overall_bbox_min_mm=[_r(v, 1) for v in lo], overall_bbox_max_mm=[_r(v, 1) for v in hi],
                   mass_by_role_kg={k: _r(v, 3) for k, v in sorted(by_role.items())})
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
            rows.append(dict(part=p.name, type=p.kind, role=p.role, material=p.material, quantity=1,
                             mass_kg=_r(p.mass_kg, 3), structure_mass_kg=_r(p.structure_mass_kg, 3),
                             equipment_mass_kg=_r(p.extra_mass_kg, 3), volume_cm3=_r(p.volume_mm3 / 1000, 2),
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
        overlaps = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                vol = _intersection_volume(self.parts[names[i]].solid, self.parts[names[j]].solid)
                if vol > 1.0:                                       # more than 1 mm^3
                    overlaps.append(dict(a=names[i], b=names[j], overlap_cm3=_r(vol / 1000, 3)))
        floating = []
        if len(names) > 1:
            for n in names:
                nearest = min(self.parts[n].solid.distance_to(self.parts[m].solid) for m in names if m != n)
                if nearest > touch_tolerance_mm:
                    floating.append(dict(part=n, nearest_gap_mm=_r(nearest, 2)))
        ok = not overlaps
        return dict(ok=ok, overlaps=overlaps, floating_parts=floating,
                    advice=("No overlapping parts." if ok else "Parts overlap: move them apart or shrink one.")
                    + (" Floating parts don't touch anything: add a gap=0 attachment or a connecting part."
                       if floating else ""))

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
        return dict(format="satcad-2", name=self.name, units="mm", variables=self.variables,
                    features=[dict(name=f.name, kind=f.kind, params=f.raw) for f in self.features.values()])

    def save(self, path) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_json(), indent=2))
        return str(path)

    def load(self, path) -> None:
        data = json.loads(Path(path).read_text())
        if data.get("format") not in ("satcad-1", "satcad-2"):
            raise DesignError("That file isn't a satcad design.")
        self.clear(data.get("name", "satellite"))
        self.variables = dict(data.get("variables", {}))
        for f in data["features"]:
            self.features[f["name"]] = Feature(f["name"], f["kind"], dict(f["params"]))
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
