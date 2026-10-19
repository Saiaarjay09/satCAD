"""
Parametric design engine.

How it works (the same idea professional CAD uses):

* A design is an ordered list of FEATURES. A feature is a *recipe*: "a 600 x 400 x 300 mm chassis",
  "a radiator panel attached to the +y face of the chassis".
* The 3D geometry is always REBUILT from those recipes. So if you change the chassis length, every
  radiator attached to it moves with it automatically. That is what "parametric" means.
* Everything is in MILLIMETRES, and masses are in kilograms.

Coordinates: X points forward, Y points left, Z points up, in mm. The origin is the centre of the
first thing you create unless you move it.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from build123d import Axis, Box, Color, Compound, Cylinder, Pos, Rot, Sphere, Vector, export_gltf, export_step, export_stl

from .materials import MATERIALS, ROLE_COLORS, density

AXES = {"x": 0, "y": 1, "z": 2}
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class DesignError(ValueError):
    """A problem with the design that the user can fix. Messages are written for beginners."""


# kind -> {param: default}.  REQ marks a parameter you must always provide.
REQ = "__required__"
_POSE = dict(x=0.0, y=0.0, z=0.0, rx=0.0, ry=0.0, rz=0.0)
_COMMON = dict(material="aluminum_6061", role="structure", color=None, notes="")
SCHEMAS: dict[str, dict] = {
    "chassis": {**_COMMON, "role": "chassis", "length": REQ, "width": REQ, "height": REQ, "wall": 0.0,
                "internal_mass_kg": 0.0, **_POSE},
    "box": {**_COMMON, "length": REQ, "width": REQ, "height": REQ, **_POSE},
    "cylinder": {**_COMMON, "radius": REQ, "height": REQ, "axis": "z", **_POSE},
    "sphere": {**_COMMON, "radius": REQ, **_POSE},
    "panel": {**_COMMON, "material": "aluminum_honeycomb", "role": "radiator", "attach_to": REQ, "side": REQ,
              "length": REQ, "width": REQ, "thickness": REQ, "gap": 0.0, "offset_span": 0.0,
              "offset_thin": 0.0, "thin_axis": None, "tilt_deg": 0.0},
}
_POSITIVE = {"length", "width", "height", "radius", "thickness"}


@dataclass
class Feature:
    name: str
    kind: str
    params: dict


@dataclass
class Part:
    name: str
    kind: str
    role: str
    material: str
    solid: object              # a build123d Shape
    extra_mass_kg: float
    color: tuple
    params: dict

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


# --------------------------------------------------------------------------- #
class Design:
    def __init__(self, name: str = "satellite"):
        self.name = name
        self.features: dict[str, Feature] = {}
        self.parts: dict[str, Part] = {}

    # ---- validation ---------------------------------------------------------
    @staticmethod
    def _resolve(kind: str, params: dict) -> dict:
        if kind not in SCHEMAS:
            raise DesignError(f"Unknown part type '{kind}'. Choose one of: {', '.join(SCHEMAS)}.")
        schema = SCHEMAS[kind]
        unknown = set(params) - set(schema)
        if unknown:
            raise DesignError(f"'{kind}' has no setting called {sorted(unknown)}. Allowed settings: {sorted(schema)}.")
        full = {k: (params[k] if k in params else v) for k, v in schema.items()}
        missing = [k for k, v in full.items() if v == REQ]
        if missing:
            raise DesignError(f"A '{kind}' needs these settings: {missing}.")
        for k in _POSITIVE & set(full):
            if full[k] != REQ and not full[k] > 0:
                raise DesignError(f"'{k}' must be greater than 0 (you gave {full[k]}).")
        try:
            density(full["material"])
        except ValueError as e:                                    # keep every user-facing problem a DesignError
            raise DesignError(str(e)) from None
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
        return full

    # ---- building geometry --------------------------------------------------
    def _build(self, f: Feature) -> Part:
        p = f.params
        color = tuple(p["color"]) if p.get("color") else ROLE_COLORS.get(p["role"], MATERIALS[p["material"]][1])
        extra = 0.0
        if f.kind == "panel":
            solid = self._build_panel(p)
        else:
            if f.kind in ("chassis", "box"):
                solid = Box(p["length"], p["width"], p["height"])
                if f.kind == "chassis" and p["wall"] > 0:
                    w = p["wall"]
                    solid = solid - Box(p["length"] - 2 * w, p["width"] - 2 * w, p["height"] - 2 * w)
                    extra = p["internal_mass_kg"]
                elif f.kind == "chassis":
                    extra = p["internal_mass_kg"]
            elif f.kind == "cylinder":
                solid = Cylinder(p["radius"], p["height"])
                if p["axis"] == "x":
                    solid = Rot(0, 90, 0) * solid
                elif p["axis"] == "y":
                    solid = Rot(-90, 0, 0) * solid
            else:
                solid = Sphere(p["radius"])
            solid = Pos(p["x"], p["y"], p["z"]) * Rot(p["rx"], p["ry"], p["rz"]) * solid
        solid.label = f.name
        solid.color = Color(*color)
        return Part(f.name, f.kind, p["role"], p["material"], solid, float(extra), color, dict(p))

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
            hinge = list(c)
            hinge[a] = face + sign * p["gap"]
            direction = [0.0, 0.0, 0.0]
            direction[span] = 1.0
            solid = solid.rotate(Axis(Vector(*hinge), Vector(*direction)), p["tilt_deg"])
        return solid

    def rebuild(self) -> None:
        """Regenerate every part from its recipe, in order."""
        self.parts = {}
        for f in self.features.values():
            self.parts[f.name] = self._build(f)

    # ---- editing ------------------------------------------------------------
    def add(self, kind: str, name: str, **params) -> dict:
        if not NAME_RE.match(name):
            raise DesignError("Names may only use letters, digits, '_' and '-' (max 40 characters).")
        if name in self.features:
            raise DesignError(f"A part called '{name}' already exists. Pick another name, or use edit to change it.")
        feature = Feature(name, kind, self._resolve(kind, params))
        self.features[name] = feature
        try:
            self.rebuild()
        except Exception:
            del self.features[name]
            self.rebuild()
            raise
        return self.describe(name)

    def edit(self, name: str, **changes) -> dict:
        if name not in self.features:
            raise DesignError(f"No part called '{name}'. Existing parts: {list(self.features)}.")
        old = self.features[name]
        merged = {**old.params, **changes}
        for k in ("kind", "name"):
            merged.pop(k, None)
        self.features[name] = Feature(name, old.kind, self._resolve(old.kind, merged))
        try:
            self.rebuild()
        except Exception:
            self.features[name] = old
            self.rebuild()
            raise
        return self.describe(name)

    def remove(self, name: str) -> None:
        if name not in self.features:
            raise DesignError(f"No part called '{name}'. Existing parts: {list(self.features)}.")
        dependants = [f.name for f in self.features.values() if f.params.get("attach_to") == name]
        if dependants:
            raise DesignError(f"Can't remove '{name}': {dependants} are attached to it. Remove those first.")
        del self.features[name]
        self.rebuild()

    def clear(self, name: str | None = None) -> None:
        self.features, self.parts = {}, {}
        if name:
            self.name = name

    # ---- reading ------------------------------------------------------------
    def describe(self, name: str) -> dict:
        p = self.parts[name]
        mn, mx = p.bbox()
        return dict(name=p.name, type=p.kind, role=p.role, material=p.material,
                    size_mm=[_r(mx[i] - mn[i], 2) for i in range(3)],
                    bbox_min_mm=[_r(v, 2) for v in mn], bbox_max_mm=[_r(v, 2) for v in mx],
                    volume_cm3=_r(p.volume_mm3 / 1000, 2), mass_kg=_r(p.mass_kg, 3),
                    settings={k: v for k, v in p.params.items() if v not in (None, "") and k not in ("color",)})

    def list_parts(self) -> list[dict]:
        return [self.describe(n) for n in self.parts]

    def measure(self) -> dict:
        if not self.parts:
            return dict(parts=0, note="The design is empty.")
        total = sum(p.mass_kg for p in self.parts.values())
        com = [0.0, 0.0, 0.0]
        by_role: dict[str, float] = {}
        lo, hi = [math.inf] * 3, [-math.inf] * 3
        for p in self.parts.values():
            c = p.solid.bounding_box().center() if p.kind == "chassis" else p.solid.center()
            for i, v in enumerate((c.X, c.Y, c.Z)):
                com[i] += p.mass_kg * v
            by_role[p.role] = by_role.get(p.role, 0.0) + p.mass_kg
            mn, mx = p.bbox()
            lo, hi = [min(a, b) for a, b in zip(lo, mn)], [max(a, b) for a, b in zip(hi, mx)]
        return dict(parts=len(self.parts), total_mass_kg=_r(total, 3),
                    centre_of_mass_mm=[_r(v / total, 1) for v in com] if total > 0 else None,
                    overall_size_mm=[_r(hi[i] - lo[i], 1) for i in range(3)],
                    overall_bbox_min_mm=[_r(v, 1) for v in lo], overall_bbox_max_mm=[_r(v, 1) for v in hi],
                    mass_by_role_kg={k: _r(v, 3) for k, v in sorted(by_role.items())})

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
        return dict(format="satcad-1", name=self.name, units="mm",
                    features=[dict(name=f.name, kind=f.kind, params=f.params) for f in self.features.values()])

    def save(self, path) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_json(), indent=2))
        return str(path)

    def load(self, path) -> None:
        data = json.loads(Path(path).read_text())
        if data.get("format") != "satcad-1":
            raise DesignError("That file isn't a satcad design.")
        self.clear(data.get("name", "satellite"))
        for f in data["features"]:
            self.features[f["name"]] = Feature(f["name"], f["kind"], self._resolve(f["kind"], f["params"]))
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
