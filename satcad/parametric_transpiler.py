"""
Cross-CAD parametric transpiler: turn a satCAD design (its variables, formulas and feature history) into rebuild scripts for other CAD programs.

WHY
    A STEP file is a "dumb solid": when you open it in SolidWorks, Fusion or NX the feature tree and the equations are gone. satCAD keeps the recipe
    (named variables, formulas, a feature history), so this module writes that recipe out as a script each program can run to rebuild the model with
    its own native features and its own equation list:

        Siemens NX       NXOpen journal (Python)      -> export_nxopen_journal()      expressions (Ctrl+E), Block / Cylinder / Boolean / Pattern features
        Autodesk Fusion  Fusion API script (Python)   -> export_fusion_script()       User Parameters (Change Parameters), sketches, extrudes, patterns
        SolidWorks       VBA macro                    -> export_solidworks_macro()    Equation Manager, sketches, FeatureExtrusion3, patterns
        FreeCAD          FCMacro (Python)             -> export_freecad_macro()       a Params spreadsheet with aliases, Part features bound with expressions

HOW IT WORKS (three layers)
    1. build_ir(design)    walks the design's variables and features and produces a neutral intermediate representation (IR):
                           a ParameterTable (every variable, its formula text and its value) and a list of FeatureNode objects (kind, arguments as
                           expressions, operation, the transforms that place it inside its assemblies).
    2. Expression printers rewrite each formula for the target program's syntax (power, trigonometry in degrees, how a variable is named).
    3. Backends (one per program) print a standalone script: a small prelude of helper functions that call that program's API, the parameter registry,
       then one helper call per feature, in the order of the original history.

HONEST LIMITS (read before trusting a script)
    * The IR and the expression translation are tested here (the IR is rebuilt with build123d and compared with satCAD, formulas are re-evaluated).
    * The scripts that call SIEMENS NX, AUTODESK FUSION and SOLIDWORKS APIs have NOT been run against those programs (no licence here). They are written
      from the public API shapes and may need small adjustments for your version. They are checked for valid syntax and structure only.
    * What is bound to a parameter and what is baked in differs per program and is listed at the top of every generated script ("COVERAGE"): sizes, radii,
      counts and pitches are bound where the target API allows it; positions are often written as evaluated numbers. Features the target cannot express
      (sweeps, lofts, screws, imported solids) are reported as UNSUPPORTED and left out; use the STEP file from the same bundle for those bodies.
    * Only formulas that survive translation stay formulas. A formula that reads another part's size (chassis.width) or uses a function the target lacks
      becomes its evaluated number, and the script says so.
"""
from __future__ import annotations

import ast
import datetime
import json
import math
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .expressions import is_expression

TARGETS = ("nx", "fusion", "solidworks", "freecad")
TARGET_LABEL = {"nx": "Siemens NX (NXOpen .py)", "fusion": "Autodesk Fusion (.py)", "solidworks": "SolidWorks (.vba)", "freecad": "FreeCAD (.FCMacro)"}
TARGET_FILE = {"nx": "export_nxopen_journal.py", "fusion": "export_fusion_script.py", "solidworks": "export_solidworks_macro.vba", "freecad": "export_freecad_macro.FCMacro"}
COMMENT = {"nx": "#", "fusion": "#", "solidworks": "'", "freecad": "#"}

LENGTH_KEYS = {"length", "width", "height", "radius", "thickness", "depth", "diameter", "wall", "x", "y", "z", "dx", "dy", "dz", "cx", "cy", "cz", "offset",
               "offset_u", "offset_v", "pitch_u", "pitch_v", "fillet", "chamfer", "gap", "corner_radius", "pivot_x", "pivot_y", "pivot_z", "points",
               "bend_radius", "size"}
ANGLE_KEYS = {"rx", "ry", "rz", "angle", "angle_deg", "total_angle", "taper_deg", "circle_start_deg", "tilt_deg", "angle_min", "angle_max"}
COUNT_KEYS = {"count", "sides", "count_u", "count_v", "circle_count"}


# ======================================================================================================================================
# Expressions: neutral form (satCAD syntax without the "="), and printers for each target
# ======================================================================================================================================
def fmt_num(v: float) -> str:
    """A number as short text: 4500.0 -> '4500', 0.35 -> '0.35'."""
    v = float(v)
    if not math.isfinite(v):
        raise ValueError("cannot write a non-finite number")
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return repr(round(v, 10))


@dataclass
class Expr:
    """A number that may be a formula. `src` is the formula in satCAD syntax (no leading '='), or None for a plain number; `value` is its value in mm / deg."""
    src: str | None
    value: float
    note: str = ""                      # why a formula was replaced by its number ("reads chassis.width"), for the coverage report

    @property
    def is_formula(self) -> bool:
        return self.src is not None

    def neutral(self) -> str:
        return self.src if self.src is not None else fmt_num(self.value)

    def names(self) -> set:
        return _names_in(self.src) if self.src else set()


def lit(v: float) -> Expr:
    return Expr(None, float(v))


def combine(op: str, a: Expr, b: Expr) -> Expr:
    """a op b as a new Expr (keeps formulas as formulas)."""
    value = {"+": a.value + b.value, "-": a.value - b.value, "*": a.value * b.value, "/": a.value / b.value if b.value else 0.0}[op]
    if not (a.is_formula or b.is_formula):
        return Expr(None, value)
    return Expr(f"({a.neutral()}) {op} ({b.neutral()})", value)


def _names_in(src: str | None) -> set:
    if not src:
        return set()
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        return set()
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}         # min, tan ... are functions, not variables
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - set(_CONSTANTS) - called


_CONSTANTS = {"pi": "pi", "e": "e"}


def _additive_names(src: str) -> set:
    """Variables a formula adds, subtracts or multiplies (not the ones it divides by or puts in a power or a function), for unit inference."""
    try:
        tree = ast.parse(src, mode="eval").body
    except SyntaxError:
        return set()
    out: set = set()

    def walk(n):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.BinOp):
            if isinstance(n.op, (ast.Add, ast.Sub, ast.Mult)):
                walk(n.left)
                walk(n.right)
            elif isinstance(n.op, ast.Div):
                walk(n.left)
        elif isinstance(n, ast.UnaryOp):
            walk(n.operand)
    walk(tree)
    return out - set(_CONSTANTS)

# function -> (kind, target spelling). kinds: 'trig' (satCAD takes degrees), 'atrig' (returns degrees), 'plain', or None = not available in that program
_FUNCS = {
    "nx": {"sin": ("trig", "sin"), "cos": ("trig", "cos"), "tan": ("trig", "tan"), "asin": ("plain", "asin"), "acos": ("plain", "acos"), "atan": ("plain", "atan"),
           "atan2": ("plain", "atan2"), "sqrt": ("plain", "sqrt"), "abs": ("plain", "abs"), "min": ("plain", "min"), "max": ("plain", "max"),
           "floor": ("plain", "floor"), "ceil": ("plain", "ceiling"), "round": ("plain", "round"), "log": ("plain", "ln"), "log10": ("plain", "log"),
           "exp": ("plain", "exp"), "radians": None, "degrees": None},
    "fusion": {"sin": ("trig", "sin"), "cos": ("trig", "cos"), "tan": ("trig", "tan"), "asin": ("atrig", "asin"), "acos": ("atrig", "acos"),
               "atan": ("atrig", "atan"), "atan2": ("atrig", "atan2"), "sqrt": ("plain", "sqrt"), "abs": ("plain", "abs"), "min": ("plain", "min"),
               "max": ("plain", "max"), "floor": ("plain", "floor"), "ceil": ("plain", "ceil"), "round": ("plain", "round"), "log": ("plain", "log"),
               "log10": ("plain", "log10"), "exp": ("plain", "exp"), "radians": None, "degrees": None},
    "solidworks": {"sin": ("trig", "sin"), "cos": ("trig", "cos"), "tan": ("trig", "tan"), "asin": None, "acos": None, "atan": ("atrig", "atn"),
                   "atan2": None, "sqrt": ("plain", "sqr"), "abs": ("plain", "abs"), "min": None, "max": None, "floor": ("plain", "int"), "ceil": None,
                   "round": None, "log": ("plain", "log"), "log10": None, "exp": ("plain", "exp"), "radians": None, "degrees": None},
    "freecad": {"sin": ("trig", "sin"), "cos": ("trig", "cos"), "tan": ("trig", "tan"), "asin": ("atrig", "asin"), "acos": ("atrig", "acos"),
                "atan": ("atrig", "atan"), "atan2": ("atrig", "atan2"), "sqrt": ("plain", "sqrt"), "abs": ("plain", "abs"), "min": ("plain", "min"),
                "max": ("plain", "max"), "floor": ("plain", "floor"), "ceil": ("plain", "ceil"), "round": ("plain", "round"), "log": ("plain", "ln"),
                "log10": ("plain", "log10"), "exp": ("plain", "exp"), "radians": None, "degrees": None},
}


class _Unsupported(Exception):
    pass


def _sanitize_alias(name: str) -> str:
    """FreeCAD spreadsheet aliases must not look like a cell address (A1, AB12)."""
    return name + "_" if re.fullmatch(r"[A-Za-z]{1,2}[0-9]+", name) else name


def to_dialect(src: str, target: str, name_fmt) -> str:
    """Rewrite one neutral formula for `target`. `name_fmt(var)` spells a variable. Raises _Unsupported if the target has no way to say it."""
    tree = ast.parse(src, mode="eval").body
    funcs = _FUNCS[target]

    def p(node, parent_prec=0) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return fmt_num(node.value)
        if isinstance(node, ast.Name):
            return "pi" if node.id == "pi" else ("2.718281828459045" if node.id == "e" else name_fmt(node.id))
        if isinstance(node, ast.UnaryOp):
            inner = p(node.operand, 3)
            if isinstance(node.op, ast.USub):
                return f"-{inner}" if isinstance(node.operand, (ast.Name, ast.Constant)) else f"-({inner})"
            if isinstance(node.op, ast.UAdd):
                return inner
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, (ast.FloorDiv, ast.Mod)):
                raise _Unsupported("// or %")
            sym, prec = {ast.Add: ("+", 1), ast.Sub: ("-", 1), ast.Mult: ("*", 2), ast.Div: ("/", 2), ast.Pow: ("^", 4)}[type(node.op)]
            left, right = p(node.left, prec), p(node.right, prec + 1 if sym != "^" else prec)
            text = f"{left} {sym} {right}"
            return f"({text})" if prec < parent_prec or (sym == "^" and parent_prec >= 4) else text
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            spec = funcs.get(node.func.id, "missing")
            if spec is None or spec == "missing":
                raise _Unsupported(f"function {node.func.id}()")
            kind, spelled = spec
            args = [p(a) for a in node.args]
            if kind == "trig":
                if target == "nx":
                    return f"{spelled}({args[0]})"
                if target == "solidworks":
                    return f"{spelled}(({args[0]}) * pi / 180)"
                return f"{spelled}(({args[0]}) * 1 deg)"
            if kind == "atrig":
                if target == "solidworks":
                    return f"({spelled}({', '.join(args)}) * 180 / pi)"
                return f"({spelled}({', '.join(args)}) / 1 deg)"
            return f"{spelled}({', '.join(args)})"
        raise _Unsupported(type(node).__name__)

    return p(tree)


def render(expr: Expr, target: str, unit: str, name_fmt) -> str:
    """The text a target needs for a dimension: a number, or a formula bound to the parameters. `unit` is 'mm', 'deg' or '' (a count / ratio)."""
    if not expr.is_formula:
        return _lit_for(expr.value, target, unit)
    try:
        text = to_dialect(expr.src, target, name_fmt)
    except _Unsupported:
        return _lit_for(expr.value, target, unit)
    if target in ("fusion", "freecad") and unit in ("mm", "deg"):
        return f"({text}) * 1 {unit}"
    return f"({text})" if re.search(r"[ +\-*/^]", text) else text


def render_param(expr: Expr, target: str, name_fmt) -> str:
    """A parameter's definition as the registry of `target` wants it: the formula with variable names, or its number. No units, no outer brackets."""
    if not expr.is_formula:
        return fmt_num(expr.value)
    try:
        return to_dialect(expr.src, target, name_fmt)
    except _Unsupported:
        return fmt_num(expr.value)


def is_renderable(expr: Expr, target: str) -> bool:
    if not expr.is_formula:
        return True
    try:
        to_dialect(expr.src, target, lambda n: n)
        return True
    except _Unsupported:
        return False


def _lit_for(value: float, target: str, unit: str) -> str:
    text = fmt_num(value)
    if target == "fusion" and unit in ("mm", "deg"):
        return f"{text} {unit}"
    if target == "freecad" and unit in ("mm", "deg"):
        return f"{text} {unit}"
    return text


# ======================================================================================================================================
# Intermediate representation
# ======================================================================================================================================
@dataclass
class Param:
    name: str
    expr: Expr                           # formula (or number) in satCAD syntax
    unit: str = ""                       # 'mm', 'deg' or '' (counts, ratios)
    comment: str = ""

    @property
    def value(self) -> float:
        return self.expr.value


class ParameterTable:
    """Every named variable of the design, in definition order, with its formula text and its value in mm / deg."""

    def __init__(self):
        self.params: dict[str, Param] = {}

    def add(self, p: Param) -> None:
        self.params[p.name] = p

    def __iter__(self):
        return iter(self.params.values())

    def __len__(self):
        return len(self.params)

    def __contains__(self, name):
        return name in self.params

    def values(self) -> dict:
        return {p.name: p.value for p in self.params.values()}

    def formulas(self) -> dict:
        return {p.name: (p.expr.src if p.expr.is_formula else None) for p in self.params.values()}


@dataclass
class FeatureNode:
    id: str                              # the satCAD feature / part name this node produces or edits
    kind: str                            # CSYS_Datum | Assembly_Group | Primitive_Box | Primitive_Cylinder | Primitive_Sphere | Sketch_Extrude | Revolve |
    #                                      Hollow_Shell | Boolean_Op | Linear_Pattern | Circular_Pattern | Mirror | Fillet_Chamfer | Cut_Box | Cut_Cylinder |
    #                                      Component_Transform | Unsupported
    args: dict = field(default_factory=dict)
    op: str = "NewBody"                  # NewBody | Join | Cut | Intersect
    fidelity: str = "native"             # native: every number that was a formula stays a formula; evaluated: some formula became a number; unsupported
    notes: list = field(default_factory=list)
    source_kind: str = ""                # the satCAD feature kind this came from
    assembly: str | None = None

    def exprs(self):
        """Every Expr inside the arguments (for the usage analysis and the tests)."""
        def walk(v):
            if isinstance(v, Expr):
                yield v
            elif isinstance(v, (list, tuple)):
                for x in v:
                    yield from walk(x)
            elif isinstance(v, dict):
                for x in v.values():
                    yield from walk(x)
        yield from walk(self.args)


@dataclass
class IR:
    name: str
    params: ParameterTable
    nodes: list
    warnings: list = field(default_factory=list)
    generated: str = ""

    def coverage(self) -> dict:
        out = {"native": 0, "evaluated": 0, "unsupported": 0}
        for n in self.nodes:
            if n.kind in ("CSYS_Datum", "Assembly_Group"):
                continue
            out[n.fidelity] += 1
        return out

    def to_dict(self) -> dict:
        def enc(v):
            if isinstance(v, Expr):
                return {"formula": v.src, "value": v.value}
            if isinstance(v, (list, tuple)):
                return [enc(x) for x in v]
            if isinstance(v, dict):
                return {k: enc(x) for k, x in v.items()}
            return v
        return dict(name=self.name, parameters=[dict(name=p.name, formula=p.expr.src, value=p.value, unit=p.unit, comment=p.comment) for p in self.params],
                    nodes=[dict(id=n.id, kind=n.kind, op=n.op, fidelity=n.fidelity, source_kind=n.source_kind, assembly=n.assembly, args=enc(n.args), notes=n.notes)
                           for n in self.nodes], warnings=self.warnings)


# ======================================================================================================================================
# Design -> IR
# ======================================================================================================================================
class _Builder:
    def __init__(self, design):
        self.d = design
        self.variables = dict(design.variables)
        self.nodes: list[FeatureNode] = []
        self.warnings: list[str] = []
        self.votes: dict[str, dict] = {v: {"mm": 0, "deg": 0, "": 0} for v in self.variables}
        self.assembly_raw: dict[str, dict] = {}
        self.assembly_parent: dict[str, str | None] = {}

    # ---- numbers and formulas ------------------------------------------------------------------------------------------------------------
    def num(self, raw, value, key: str, node_notes: list | None = None) -> Expr:
        """A setting as an Expr. Formulas stay formulas when they only use variables and functions the targets understand."""
        kind = "deg" if key in ANGLE_KEYS else ("" if key in COUNT_KEYS else "mm")
        if isinstance(value, bool) or value is None:
            return Expr(None, float(value or 0))
        if not is_expression(raw):
            return Expr(None, float(value))
        src = raw.lstrip()[1:].strip()
        try:
            tree = ast.parse(src, mode="eval")
        except SyntaxError:
            return Expr(None, float(value), "unreadable formula")
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute):
                return Expr(None, float(value), f"reads another part ({ast.unparse(n)})")
            if isinstance(n, ast.Name) and n.id not in self.variables and n.id not in _CONSTANTS and n.id not in _ALL_FUNC_NAMES:
                return Expr(None, float(value), f"unknown name {n.id}")
        for name in _names_in(src):
            if name in self.votes:
                self.votes[name][kind] += 1
        return Expr(src, float(value))

    def seq(self, raw, value, key: str) -> list:
        raw = raw if isinstance(raw, (list, tuple)) else [raw] * len(value)
        return [self.seq(r, v, key) if isinstance(v, (list, tuple)) else self.num(r, v, key) for r, v in zip(raw, value)]

    def settings(self, f, keys, rename: dict | None = None) -> dict:
        rename = rename or {}
        out = {}
        for k in keys:
            if k in f.params:
                out[rename.get(k, k)] = self.num(f.raw.get(k, f.params[k]), f.params[k], k)
        return out

    def pose(self, f) -> dict:
        return {k: self.num(f.raw.get(k, f.params.get(k, 0.0)), f.params.get(k, 0.0), k) for k in ("x", "y", "z", "rx", "ry", "rz")}

    # ---- nodes ---------------------------------------------------------------------------------------------------------------------------
    def add(self, id_: str, kind: str, args: dict, f=None, op: str = "NewBody", notes=None, fidelity: str | None = None) -> FeatureNode:
        node = FeatureNode(id_, kind, args, op=op, source_kind=f.kind if f is not None else "", assembly=(f.params.get("assembly") if f is not None else None),
                           notes=list(notes or []))
        if fidelity is None:
            fidelity = "native"
            for e in node.exprs():
                if e.note:
                    fidelity = "evaluated"
                    node.notes.append(e.note)
        node.fidelity = fidelity
        self.nodes.append(node)
        return node

    def placement(self, f) -> None:
        """After a body is created inside an assembly: the Component_Transform that moves it through its assemblies (innermost first)."""
        name = f.params.get("assembly")
        if not name:
            return
        steps = []
        while name:
            a = self.assembly_raw[name]
            axis, angle = a["joint_axis"], a["angle_deg"]
            if axis and (angle.is_formula or angle.value):
                steps.append(dict(type="hinge", axis=axis, angle=angle, pivot=[a["pivot_x"], a["pivot_y"], a["pivot_z"]], assembly=name))
            if any(a[k].is_formula or a[k].value for k in ("rx", "ry", "rz")):
                steps.append(dict(type="rotate", rx=a["rx"], ry=a["ry"], rz=a["rz"], assembly=name))
            if any(a[k].is_formula or a[k].value for k in ("x", "y", "z")):
                steps.append(dict(type="translate", x=a["x"], y=a["y"], z=a["z"], assembly=name))
            name = self.assembly_parent[name]
        if steps:
            self.add(f.name, "Component_Transform", dict(body=f.name, steps=steps), f, op="Move")

    # ---- the walk ------------------------------------------------------------------------------------------------------------------------
    def run(self) -> IR:
        d = self.d
        self.add("CSYS", "CSYS_Datum", dict(origin=[lit(0), lit(0), lit(0)], x=[1, 0, 0], y=[0, 1, 0], z=[0, 0, 1]))
        for f in d.features.values():
            handler = getattr(self, "k_" + f.kind, None)
            if handler is None:
                self.unsupported(f, f"feature type '{f.kind}'")
            else:
                handler(f)
        table = ParameterTable()
        units = self.infer_units()
        for name, raw in self.variables.items():
            value = d._var_cache.get(name, raw if not is_expression(raw) else 0.0)
            table.add(Param(name, self.num(raw, value, "other") if is_expression(raw) else Expr(None, float(raw)), units[name]))
        ir = IR(d.name, table, self.nodes, self.warnings, generated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
        for n in self.nodes:
            if n.fidelity == "evaluated" and n.notes:
                self.warnings.append(f"{n.id}: " + "; ".join(dict.fromkeys(n.notes)))
            elif n.fidelity == "unsupported":
                self.warnings.append(f"{n.id}: " + "; ".join(n.notes))
        return ir

    def infer_units(self) -> dict:
        """mm / deg / '' for every variable: by what it is used for (a length, an angle or a count), with name hints first. A variable that is only used inside other
        variables' formulas takes the unit of the formula it adds to (never one it divides by)."""
        direct = {k: dict(v) for k, v in self.votes.items()}
        votes = {k: dict(v) for k, v in self.votes.items()}
        for _ in range(len(votes) + 1):
            changed = False
            for name, raw in self.variables.items():
                if not is_expression(raw):
                    continue
                best = max(votes[name], key=lambda u: votes[name][u])
                if votes[name][best] == 0:
                    continue
                for inner in _additive_names(raw.lstrip()[1:]):
                    if inner in votes and sum(direct[inner].values()) == 0 and votes[inner][best] < votes[name][best]:
                        votes[inner][best] = votes[name][best]
                        changed = True
            if not changed:
                break
        out = {}
        for name in self.variables:
            up = name.upper()
            if up.endswith("_MM") or up.endswith("_LEN"):
                out[name] = "mm"
            elif up.endswith("_DEG") or "ANGLE" in up:
                out[name] = "deg"
            elif up.endswith("_COUNT") or up in ("COUNT", "N") or up.endswith("RATIO") or up.endswith("_N"):
                out[name] = ""
            else:
                v = votes[name]
                best = max(v, key=lambda u: v[u])
                out[name] = best if v[best] > 0 else ""
        return out

    def unsupported(self, f, what: str) -> None:
        self.add(f.name, "Unsupported", dict(feature=f.kind), f, notes=[f"{what} has no native equivalent in the scripts; use the STEP file for this body"], fidelity="unsupported")

    # ---- feature handlers ----------------------------------------------------------------------------------------------------------------
    def k_assembly(self, f) -> None:
        p = f.params
        self.assembly_parent[f.name] = p["parent"]
        a = self.pose(f)
        a["pivot_x"], a["pivot_y"], a["pivot_z"] = (self.num(f.raw.get(k, p[k]), p[k], k) for k in ("pivot_x", "pivot_y", "pivot_z"))
        a["angle_deg"] = self.num(f.raw.get("angle_deg", p["angle_deg"]), p["angle_deg"], "angle_deg")
        a["joint_axis"] = p["joint_axis"]
        self.assembly_raw[f.name] = a
        self.add(f.name, "Assembly_Group", dict(parent=p["parent"], joint_axis=p["joint_axis"], angle=a["angle_deg"], **{k: a[k] for k in ("x", "y", "z", "rx", "ry", "rz")}), f)

    def _edge_treatment(self, f, outer_notes=()) -> None:
        if f.params.get("fillet") or f.params.get("chamfer"):
            self.add(f.name, "Fillet_Chamfer", dict(target=f.name, edges="all", radius=self.num(f.raw.get("fillet", 0), f.params.get("fillet", 0), "fillet"),
                                                    chamfer=self.num(f.raw.get("chamfer", 0), f.params.get("chamfer", 0), "chamfer")), f, op="Edit")

    def k_box(self, f) -> None:
        a = dict(name=f.name, length=self.num(f.raw["length"], f.params["length"], "length"), width=self.num(f.raw["width"], f.params["width"], "width"),
                 height=self.num(f.raw["height"], f.params["height"], "height"), **self.pose(f))
        self.add(f.name, "Primitive_Box", a, f)
        self._edge_treatment(f)
        self.placement(f)

    def k_chassis(self, f) -> None:
        self.k_box_like_chassis(f)

    def k_box_like_chassis(self, f) -> None:
        p = f.params
        a = dict(name=f.name, length=self.num(f.raw["length"], p["length"], "length"), width=self.num(f.raw["width"], p["width"], "width"),
                 height=self.num(f.raw["height"], p["height"], "height"), **self.pose(f))
        self.add(f.name, "Primitive_Box", a, f)
        self._edge_treatment(f)
        if p["wall"] > 0:
            notes = ["the inside corners of a rounded chassis are written square"] if p.get("fillet") else []
            self.add(f.name, "Hollow_Shell", dict(target=f.name, mode="cavity", wall=self.num(f.raw.get("wall"), p["wall"], "wall"), open_faces=[],
                                                  length=a["length"], width=a["width"], height=a["height"], **self.pose(f)), f, op="Cut", notes=notes)
        self.placement(f)

    def k_cylinder(self, f) -> None:
        p = f.params
        self.add(f.name, "Primitive_Cylinder", dict(name=f.name, radius=self.num(f.raw["radius"], p["radius"], "radius"), height=self.num(f.raw["height"], p["height"], "height"),
                                                    axis=p["axis"], **self.pose(f)), f)
        self.placement(f)

    def k_sphere(self, f) -> None:
        self.add(f.name, "Primitive_Sphere", dict(name=f.name, radius=self.num(f.raw["radius"], f.params["radius"], "radius"), **self.pose(f)), f)
        self.placement(f)

    def k_extrude(self, f) -> None:
        p = f.params
        prof = p["profile"]
        if prof not in ("rect", "circle", "polygon"):
            return self.unsupported(f, f"the '{prof}' profile")
        if p["taper_deg"]:
            return self.unsupported(f, "a tapered extrusion")
        a = dict(name=f.name, profile=prof, plane=p["plane"], depth=self.num(f.raw["depth"], p["depth"], "depth"), both_sides=bool(p["both_sides"]), **self.pose(f))
        if prof == "rect":
            a["width"], a["height"] = self.num(f.raw["width"], p["width"], "width"), self.num(f.raw["height"], p["height"], "height")
        elif prof == "circle":
            a["radius"] = self.num(f.raw["radius"], p["radius"], "radius")
        else:
            a["points"] = [[self.num(rr, vv, "points") for rr, vv in zip(rp, vp)] for rp, vp in zip(_rows(f.raw["points"]), _rows(p["points"]))]
        self.add(f.name, "Sketch_Extrude", a, f)
        if p["wall"] > 0 and prof in ("rect", "circle"):
            wall = self.num(f.raw.get("wall"), p["wall"], "wall")
            inner = dict(a, name=f.name + "__bore")
            two = lit(2)
            if prof == "rect":
                inner["width"], inner["height"] = combine("-", a["width"], combine("*", two, wall)), combine("-", a["height"], combine("*", two, wall))
            else:
                inner["radius"] = combine("-", a["radius"], wall)
            inner["depth"] = a["depth"]
            self.add(f.name, "Sketch_Extrude", dict(inner, target=f.name), f, op="Cut")
        self.placement(f)

    def k_revolve(self, f) -> None:
        p = f.params
        if p["axis"] != "z" and p["axis"] is not None:
            pass
        pts = [[self.num(rr, vv, "points") for rr, vv in zip(rp, vp)] for rp, vp in zip(_rows(f.raw["points"]), _rows(p["points"]))]
        self.add(f.name, "Revolve", dict(name=f.name, points=pts, angle=self.num(f.raw.get("angle", 360), p["angle"], "angle"), axis=p["axis"], **self.pose(f)), f)
        self.placement(f)

    def k_boolean(self, f) -> None:
        p = f.params
        op = {"unite": "Join", "subtract": "Cut", "intersect": "Intersect"}[str(p["operation"]).lower()]
        self.add(p["target"], "Boolean_Op", dict(target=p["target"], tools=[p["tool"]], operation=str(p["operation"]).lower()), f, op=op)

    def k_pattern(self, f) -> None:
        p = f.params
        src = p["source"]
        if p["mode"] == "linear":
            self.add(f.name, "Linear_Pattern", dict(source=src, count=self.num(f.raw["count"], p["count"], "count"),
                                                    pitch=[self.num(f.raw.get(k, 0), p[k], k) for k in ("dx", "dy", "dz")]), f)
        elif p["mode"] == "circular":
            self.add(f.name, "Circular_Pattern", dict(source=src, count=self.num(f.raw["count"], p["count"], "count"), axis=p["axis"],
                                                      center=[self.num(f.raw.get(k, 0), p[k], k) for k in ("cx", "cy", "cz")],
                                                      total_angle=self.num(f.raw.get("total_angle", 360), p["total_angle"], "total_angle")), f)
        else:
            steps = [dict(pitch=[self.num(rr, vv, k) for rr, vv, k in zip(rp[:3], vp[:3], ("dx", "dy", "dz"))], count=self.num(rp[3], vp[3], "count"))
                     for rp, vp in zip(_rows(f.raw["grid"]), _rows(p["grid"]))]
            self.add(f.name, "Linear_Pattern", dict(source=src, grid=steps, count=lit(p["count"]), pitch=[lit(0)] * 3), f,
                     notes=["a grid is written as one linear pattern per step"])

    def k_mirror(self, f) -> None:
        p = f.params
        self.add(f.name, "Mirror", dict(source=p["source"], plane=p["plane"], offset=self.num(f.raw.get("offset", 0), p["offset"], "offset")), f)

    def k_fillet_edges(self, f) -> None:
        p = f.params
        self.add(p["target"], "Fillet_Chamfer", dict(target=p["target"], edges=str(p["edges"]), radius=self.num(f.raw.get("radius", 0), p["radius"], "radius"),
                                                    chamfer=self.num(f.raw.get("chamfer", 0), p["chamfer"], "chamfer")), f, op="Edit")

    def k_shell(self, f) -> None:
        p = f.params
        self.add(p["target"], "Hollow_Shell", dict(target=p["target"], mode="faces", wall=self.num(f.raw["thickness"], p["thickness"], "thickness"),
                                                   open_faces=[str(s).lower() for s in p["open_faces"]], direction=p["direction"]), f, op="Edit")

    def k_cutout(self, f) -> None:
        p = f.params
        part = self.d.parts[p["target"]]
        side = str(p["side"]).lower()
        sign = 1 if side[0] == "+" else -1
        a = "xyz".index(side[1])
        u, v = (a + 1) % 3, (a + 2) % 3
        mn, mx = part.bbox()
        centre = [(mn[i] + mx[i]) / 2 for i in range(3)]
        depth = (mx[a] - mn[a]) if p["depth"] == 0 else p["depth"]
        length = depth + 1.0
        at = [0.0, 0.0, 0.0]
        at[a] = (mx[a] if sign > 0 else mn[a]) - sign * depth / 2 + sign * 0.5
        at[u], at[v] = centre[u] + p["offset_u"], centre[v] + p["offset_v"]
        note = ["position and size are the evaluated numbers (the cut is placed from the target's current size)"]
        if p["shape"] == "rect":
            dims = [0.0, 0.0, 0.0]
            dims[a], dims[u], dims[v] = length, p["width"], p["height"]
            self.add(p["target"], "Cut_Box", dict(target=p["target"], center=[lit(c) for c in at], size=[lit(x) for x in dims]), f, op="Cut", notes=note, fidelity="evaluated")
        else:
            self.add(p["target"], "Cut_Cylinder", dict(target=p["target"], center=[lit(c) for c in at], radius=lit(p["diameter"] / 2), height=lit(length), axis="xyz"[a]),
                     f, op="Cut", notes=note, fidelity="evaluated")

    def k_hole(self, f) -> None:
        from . import hardware
        p = f.params
        part = self.d.parts[p["target"]]
        side = str(p["side"]).lower()
        fr = self.d._frame(part, side)
        a, sign, u, v = fr["a"], fr["sign"], fr["u"], fr["v"]
        h = hardware.spec(p["standard"])
        kind = p["hole_type"]
        depth = fr["size"] if p["depth"] == 0 else p["depth"]
        dia = h["tap_drill"] if kind == "tapped" else h["clearance"]
        if p["circle_count"] > 0:
            r = p["circle_diameter"] / 2
            spots = [(p["offset_u"] + r * math.cos(math.radians(p["circle_start_deg"] + 360.0 * k / p["circle_count"])),
                      p["offset_v"] + r * math.sin(math.radians(p["circle_start_deg"] + 360.0 * k / p["circle_count"]))) for k in range(p["circle_count"])]
        else:
            spots = [(p["offset_u"] + (i - (p["count_u"] - 1) / 2) * p["pitch_u"], p["offset_v"] + (j - (p["count_v"] - 1) / 2) * p["pitch_v"])
                     for i in range(p["count_u"]) for j in range(p["count_v"])]
        notes = ["hole positions and sizes are the evaluated numbers"]
        if kind in ("counterbore", "countersink"):
            notes.append(f"the {kind} is written as a plain {dia:g} mm clearance hole")
        for du, dv in spots:
            at = [0.0, 0.0, 0.0]
            at[u], at[v] = fr["centre"][u] + du, fr["centre"][v] + dv
            at[a] = fr["face"] - sign * (depth / 2) + sign * 0.5
            self.add(p["target"], "Cut_Cylinder", dict(target=p["target"], center=[lit(c) for c in at], radius=lit(dia / 2), height=lit(depth + 1.0), axis="xyz"[a]), f,
                     op="Cut", notes=notes, fidelity="evaluated")

    def k_panel(self, f) -> None:
        part = self.d.parts[f.name]
        mn, mx = part.bbox()
        size = [mx[i] - mn[i] for i in range(3)]
        centre = [(mn[i] + mx[i]) / 2 for i in range(3)]
        if f.params.get("tilt_deg"):
            return self.unsupported(f, "a tilted panel")
        self.add(f.name, "Primitive_Box", dict(name=f.name, length=lit(size[0]), width=lit(size[1]), height=lit(size[2]), x=lit(centre[0]), y=lit(centre[1]), z=lit(centre[2]),
                                               rx=lit(0), ry=lit(0), rz=lit(0)), f, notes=["a panel attached to another part is written as a box at its evaluated place"],
                 fidelity="evaluated")

    def k_sweep(self, f) -> None:
        self.unsupported(f, "a sweep")

    def k_loft(self, f) -> None:
        self.unsupported(f, "a loft")

    def k_fastener(self, f) -> None:
        self.unsupported(f, "a screw")

    def k_step_import(self, f) -> None:
        self.unsupported(f, "an imported STEP body")


_ALL_FUNC_NAMES = set(_FUNCS["nx"]) | {"degrees", "radians"}


def _rows(v):
    return [list(r) for r in v]


def build_ir(design) -> IR:
    """Walk a satCAD design (variables + feature history) and return the intermediate representation. The design must have been built (it always is)."""
    if not design.features:
        raise ValueError("The design is empty: there is nothing to transpile.")
    if not design.parts or len(design._var_cache) < len(design.variables):
        design.rebuild()                                                                 # every variable value and evaluated setting must be current
    return _Builder(design).run()


# ======================================================================================================================================
# Shared pieces of the backends
# ======================================================================================================================================
class _Names:
    """Variable spelling for one target (and, for FreeCAD, for one context)."""

    def __init__(self, target: str, binding: bool = False):
        self.target, self.binding = target, binding

    def __call__(self, name: str) -> str:
        if self.target == "solidworks":
            return f'"{name}"'
        if self.target == "freecad":
            alias = _sanitize_alias(name)
            return f"Params.{alias}" if self.binding else alias
        return name


def _header(ir: IR, target: str, title: str) -> list:
    c = COMMENT[target]
    cov = ir.coverage()
    lines = [f"{c} " + "=" * 100, f"{c} {title}", f"{c} Generated by satCAD ({ir.generated}) from the design '{ir.name}'. Standalone: it needs no satCAD file.",
             f"{c}", f"{c} COVERAGE  {len(ir.params)} parameters, {sum(cov.values())} features: {cov['native']} written natively, {cov['evaluated']} with some values "
             f"as evaluated numbers, {cov['unsupported']} unsupported (left out; use the STEP file for those bodies).",
             f"{c} HONEST NOTE  The expressions and the feature order were checked inside satCAD. The calls into this program's API were NOT run against the real",
             f"{c}              program (no licence here): read the script, run it on a copy of a part, and expect to adjust a call or two for your version.",
             f"{c} " + "=" * 100]
    shown = [w for w in ir.warnings][:40]
    if shown:
        lines.append(f"{c} NOTES")
        lines += [f"{c}   - {w}" for w in shown]
        if len(ir.warnings) > len(shown):
            lines.append(f"{c}   - ... and {len(ir.warnings) - len(shown)} more (see the coverage report in the dialog)")
    return lines


def _pyq(text: str) -> str:
    return json.dumps(text)


def _bind_ok(ir: IR, target: str) -> bool:
    return all(is_renderable(p.expr, target) for p in ir.params)


def _param_order(ir: IR) -> list:
    """Parameters in definition order; satCAD guarantees a formula only uses variables defined before it? No: it resolves lazily, so sort by dependency."""
    done, out = set(), []
    names = list(ir.params.params)

    def visit(n, stack=()):
        if n in done or n in stack:
            return
        for dep in ir.params.params[n].expr.names():
            if dep in ir.params.params:
                visit(dep, stack + (n,))
        done.add(n)
        out.append(ir.params.params[n])
    for n in names:
        visit(n)
    return out


def _unit_of(ir: IR, name: str) -> str:
    return ir.params.params[name].unit if name in ir.params.params else ""


# ======================================================================================================================================
# Backend 1: Siemens NX (NXOpen journal)
# ======================================================================================================================================
_NX_PRELUDE = '''
import math
import NXOpen
import NXOpen.Features
import NXOpen.GeometricUtilities

theSession = NXOpen.Session.GetSession()
workPart = theSession.Parts.Work
unit_mm = workPart.UnitCollection.FindObject("MilliMeter")
unit_deg = workPart.UnitCollection.FindObject("Degrees")
nullFeature = NXOpen.Features.Feature.Null
BODIES = {}          # satCAD part name -> NXOpen.Body (the body currently carrying that name)
SKIPPED = []         # features this script could not create (printed at the end)


def register_expression(name, formula, unit):
    """Adds one named expression, so it shows up in Tools > Expressions (Ctrl+E)."""
    text = "%s=%s" % (name, formula)
    if unit == "mm":
        workPart.Expressions.CreateWithUnits(text, unit_mm)
    elif unit == "deg":
        workPart.Expressions.CreateWithUnits(text, unit_deg)
    else:
        workPart.Expressions.Create(text)


def _boolean_type(op):
    t = NXOpen.GeometricUtilities.BooleanOperation.BooleanType
    return {"NewBody": t.Create, "Join": t.Unite, "Cut": t.Subtract, "Intersect": t.Intersect}[op]


def _first_body(feature):
    bodies = feature.GetBodies()
    return bodies[0] if bodies else None


def _guard(name, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:          # one bad feature must not stop the rebuild of the rest
        SKIPPED.append((name, str(exc)))
        theSession.LogFile.WriteLine("satCAD transpiler: skipped %s: %s" % (name, exc))
        return None


def _move(body, dx=0.0, dy=0.0, dz=0.0, axis=None, angle="0", pivot=(0.0, 0.0, 0.0)):
    """Moves or rotates a body with a Move Object feature (distance / angle strings are NX expressions)."""
    b = workPart.BaseFeatures.CreateMoveObjectBuilder(nullFeature)
    b.ObjectToMoveObject.Add(body)
    if axis is None:
        b.TransformMotion.Option = NXOpen.GeometricUtilities.ModlMotion.Options.DeltaXyz
        b.TransformMotion.DeltaXc.RightHandSide = str(dx)
        b.TransformMotion.DeltaYc.RightHandSide = str(dy)
        b.TransformMotion.DeltaZc.RightHandSide = str(dz)
    else:
        b.TransformMotion.Option = NXOpen.GeometricUtilities.ModlMotion.Options.Angle
        vec = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
        origin = NXOpen.Point3d(pivot[0], pivot[1], pivot[2])
        direction = workPart.Directions.CreateDirection(origin, NXOpen.Vector3d(*vec), NXOpen.SmartObject.UpdateOption.WithinModeling)
        b.TransformMotion.AngularAxis = workPart.Axes.CreateAxis(workPart.Points.CreatePoint(origin), direction, NXOpen.SmartObject.UpdateOption.WithinModeling)
        b.TransformMotion.Angle.RightHandSide = str(angle)
    feature = b.CommitFeature()
    b.Destroy()
    return feature


def _is_zero(v):
    return str(v).strip() in ("0", "0.0", "")


def _place(body, tx, ty, tz, rx, ry, rz):
    """satCAD builds each solid at the origin, then applies Rot(rx, ry, rz) = Rx*Ry*Rz (the vector is turned about the fixed Z axis first, then Y, then X),
    then translates. Angles and distances are NX expression strings."""
    for axis, ang in (("z", rz), ("y", ry), ("x", rx)):
        if not _is_zero(ang):
            _move(body, axis=axis, angle=ang)
    if not all(_is_zero(v) for v in (tx, ty, tz)):
        _move(body, tx, ty, tz)


def _boolean(op, target_name, tool_names):
    target = BODIES.get(target_name)
    tools = [BODIES[t] for t in tool_names if t in BODIES]
    if target is None or not tools:
        raise RuntimeError("missing body for boolean on %s" % target_name)
    b = workPart.Features.CreateBooleanBuilderUsingCollector(nullFeature)
    b.Function = {"Join": NXOpen.Features.Feature.BooleanType.Unite, "Cut": NXOpen.Features.Feature.BooleanType.Subtract,
                  "Intersect": NXOpen.Features.Feature.BooleanType.Intersect}[op]
    b.Targets.Add(target)
    for t in tools:
        b.Tool.Add(t)
    feature = b.CommitFeature()
    b.Destroy()
    BODIES[target_name] = _first_body(feature) or target
    for t in tool_names:
        BODIES.pop(t, None)
    return feature


def f_box(name, L, W, H, rx, ry, rz, tx, ty, tz):
    """A block (corner at the origin) whose edge lengths L, W, H are expressions, moved so its CENTRE sits at (tx, ty, tz) after the rotation."""
    b = workPart.Features.CreateBlockFeatureBuilder(nullFeature)
    b.Type = NXOpen.Features.BlockFeatureBuilder.Types.OriginAndEdgeLengths
    b.SetOriginAndLengths(NXOpen.Point3d(0.0, 0.0, 0.0), L, W, H)
    b.BooleanOption.Type = NXOpen.GeometricUtilities.BooleanOperation.BooleanType.Create
    feature = b.CommitFeature()
    b.Destroy()
    body = BODIES[name] = _first_body(feature)
    if all(_is_zero(v) for v in (rx, ry, rz)):
        _move(body, "(%s) - (%s) / 2" % (tx, L), "(%s) - (%s) / 2" % (ty, W), "(%s) - (%s) / 2" % (tz, H))
    else:
        _move(body, "-(%s) / 2" % L, "-(%s) / 2" % W, "-(%s) / 2" % H)
        _place(body, tx, ty, tz, rx, ry, rz)


def f_cylinder(name, axis, radius, height, rx, ry, rz, tx, ty, tz):
    b = workPart.Features.CreateCylinderBuilder(nullFeature)
    b.Diameter.RightHandSide = "2 * (%s)" % radius
    b.Height.RightHandSide = str(height)
    b.BooleanOption.Type = NXOpen.GeometricUtilities.BooleanOperation.BooleanType.Create
    feature = b.CommitFeature()
    b.Destroy()
    body = BODIES[name] = _first_body(feature)
    _move(body, 0, 0, "-(%s) / 2" % height)           # built along +Z from the origin: centre it first
    if axis == "x":
        _move(body, axis="y", angle="90")
    elif axis == "y":
        _move(body, axis="x", angle="-90")
    _place(body, tx, ty, tz, rx, ry, rz)


def f_sphere(name, radius, rx, ry, rz, tx, ty, tz):
    b = workPart.Features.CreateSphereBuilder(nullFeature)
    b.Diameter.RightHandSide = "2 * (%s)" % radius
    b.BooleanOption.Type = NXOpen.GeometricUtilities.BooleanOperation.BooleanType.Create
    feature = b.CommitFeature()
    b.Destroy()
    body = BODIES[name] = _first_body(feature)
    _place(body, tx, ty, tz, rx, ry, rz)


def f_pattern_linear(name, source, count, dx, dy, dz):
    """Pattern Feature (linear): the number of copies and the pitch are NX expressions."""
    b = workPart.Features.CreatePatternFeatureBuilder(nullFeature)
    b.PatternService.PatternType = NXOpen.GeometricUtilities.PatternDefinition.PatternEnum.Linear
    b.PatternService.LinearDefinition.XDirection.NumberOfCopies.RightHandSide = str(count)
    b.PatternService.LinearDefinition.XDirection.PitchDistance.RightHandSide = "sqrt((%s)^2 + (%s)^2 + (%s)^2)" % (dx, dy, dz)
    b.FeatureList.Add(BODIES[source])
    feature = b.CommitFeature()
    b.Destroy()
    return feature
'''


class _NX:
    target = "nx"

    def __init__(self, ir):
        self.ir = ir
        self.n = _Names("nx")
        self.skipped: list[str] = []

    def r(self, e, unit="mm"):
        return render(e, "nx", unit, self.n)

    def s(self, e, unit="mm"):
        return _pyq(self.r(e, unit))

    def gen(self) -> str:
        ir = self.ir
        out = _header(ir, "nx", "Siemens NX Open journal: rebuilds the satCAD feature history with associative features and expressions")
        out += ["# Run in NX: File > Execute > NX Open... (Alt+F8) with a new, empty metric part open. Python journal.", _NX_PRELUDE, "",
                "# ---- 1. Expressions (Tools > Expressions, Ctrl+E): every satCAD variable and formula --------------------------------------------------"]
        for p in _param_order(ir):
            out.append(f"register_expression({_pyq(p.name)}, {_pyq(render_param(p.expr, 'nx', self.n))}, {_pyq(p.unit)})  # satCAD: {p.expr.neutral()}")
        out += ["", "# ---- 2. Feature history ---------------------------------------------------------------------------------------------------------"]
        for node in ir.nodes:
            out += self.node(node)
        out += ["", "if SKIPPED:", "    theSession.LogFile.WriteLine('satCAD transpiler: %d feature(s) skipped' % len(SKIPPED))",
                "print('satCAD rebuild finished: %d bodies, %d skipped' % (len(BODIES), len(SKIPPED)))", ""]
        return "\n".join(out)

    def pose(self, a):
        return [self.s(a[k], "deg" if k.startswith("r") else "mm") for k in ("rx", "ry", "rz", "x", "y", "z")]

    def node(self, n: FeatureNode) -> list:
        a, k = n.args, n.kind
        tag = f"# [{n.fidelity}] {n.source_kind or n.kind} '{n.id}'" + (f"  ({'; '.join(dict.fromkeys(n.notes))})" if n.notes else "")
        if k == "CSYS_Datum":
            return [tag.replace("[native] ", ""), "# The model origin is the NX absolute coordinate system: X forward, Y left, Z up (as in satCAD)."]
        if k == "Assembly_Group":
            return [tag + f"  parent={a['parent']} (bodies below are moved through their assemblies by the Component_Transform steps)"]
        if k == "Primitive_Box":
            return [tag, f"_guard({_pyq(n.id)}, f_box, {_pyq(n.id)}, {self.s(a['length'])}, {self.s(a['width'])}, {self.s(a['height'])}, {', '.join(self.pose(a))})"]
        if k == "Primitive_Cylinder":
            return [tag, f"_guard({_pyq(n.id)}, f_cylinder, {_pyq(n.id)}, {_pyq(a['axis'])}, {self.s(a['radius'])}, {self.s(a['height'])}, {', '.join(self.pose(a))})"]
        if k == "Primitive_Sphere":
            return [tag, f"_guard({_pyq(n.id)}, f_sphere, {_pyq(n.id)}, {self.s(a['radius'])}, {', '.join(self.pose(a))})"]
        if k == "Sketch_Extrude":
            return [tag, f"# {a['profile']} profile on the {a['plane']} plane, depth {self.r(a['depth'])}: a sketch + Extrude feature; not generated for NX yet "
                         "(NX sketches are built from interactive section objects); use the STEP body"]
        if k == "Revolve":
            return [tag, "# UNSUPPORTED in this journal: revolve (profile sketch + Features.CreateRevolveBuilder needs interactive section selection); use the STEP body."]
        if k == "Hollow_Shell" and a["mode"] == "cavity":
            inner = [f"({self.r(a[d])}) - 2 * ({self.r(a['wall'])})" for d in ("length", "width", "height")]
            return [tag, f"_guard({_pyq(n.id + '__cavity')}, f_box, {_pyq(n.id + '__cavity')}, {_pyq(inner[0])}, {_pyq(inner[1])}, {_pyq(inner[2])}, "
                         f"{', '.join(self.pose(a))})",
                    f"_guard({_pyq(n.id)}, _boolean, 'Cut', {_pyq(n.id)}, [{_pyq(n.id + '__cavity')}])"]
        if k == "Hollow_Shell":
            return [tag, f"# Hollow_Shell on faces {a['open_faces']} with wall {self.r(a['wall'])}: Features.CreateShellBuilder with the faces of '{a['target']}' selected by normal; "
                         "left to the user (face selection is by pick in NX)."]
        if k == "Boolean_Op":
            return [tag, f"_guard({_pyq(n.id)}, _boolean, {_pyq({'unite': 'Join', 'subtract': 'Cut', 'intersect': 'Intersect'}[a['operation']])}, {_pyq(a['target'])}, {a['tools']!r})"]
        if k == "Linear_Pattern":
            if "grid" in a:
                return [tag, "# grid pattern: one linear pattern per step"] + [
                    f"_guard({_pyq(n.id)}, f_pattern_linear, {_pyq(n.id)}, {_pyq(a['source'])}, {self.s(st['count'], '')}, " + ", ".join(self.s(x) for x in st["pitch"]) + ")" for st in a["grid"]]
            return [tag, f"_guard({_pyq(n.id)}, f_pattern_linear, {_pyq(n.id)}, {_pyq(a['source'])}, {self.s(a['count'], '')}, " + ", ".join(self.s(x) for x in a["pitch"]) + ")"]
        if k == "Circular_Pattern":
            return [tag, f"# circular pattern of '{a['source']}': count {self.r(a['count'], '')}, axis {a['axis']}, total angle {self.r(a['total_angle'], 'deg')} "
                         "(PatternFeatureBuilder with PatternEnum.Circular; the axis point is the evaluated centre)"]
        if k == "Mirror":
            return [tag, f"# mirror of '{a['source']}' about the {a['plane']} plane offset {self.r(a['offset'])}: Features.CreateMirrorBodyBuilder"]
        if k == "Fillet_Chamfer":
            return [tag, f"# edge treatment on '{a['target']}', edges '{a['edges']}', radius {self.r(a['radius'])}, chamfer {self.r(a['chamfer'])}: Features.CreateEdgeBlendBuilder "
                         "(edges are selected by their direction in NX)"]
        if k in ("Cut_Box", "Cut_Cylinder"):
            self._cut = getattr(self, "_cut", 0) + 1
            tool = f"{a['target']}__cut{self._cut}"
            c = [_pyq(fmt_num(v.value)) for v in a["center"]]
            if k == "Cut_Box":
                make = f"_guard({_pyq(tool)}, f_box, {_pyq(tool)}, {', '.join(_pyq(fmt_num(v.value)) for v in a['size'])}, '0', '0', '0', {', '.join(c)})"
            else:
                make = (f"_guard({_pyq(tool)}, f_cylinder, {_pyq(tool)}, {_pyq(a['axis'])}, {_pyq(fmt_num(a['radius'].value))}, {_pyq(fmt_num(a['height'].value))}, "
                        f"'0', '0', '0', {', '.join(c)})")
            return [tag, make, f"_guard({_pyq(n.id)}, _boolean, 'Cut', {_pyq(a['target'])}, [{_pyq(tool)}])"]
        if k == "Component_Transform":
            lines = [tag]
            for st in a["steps"]:
                if st["type"] == "hinge":
                    piv = [v.value for v in st["pivot"]]
                    lines.append(f"_guard({_pyq(n.id)}, _move, BODIES[{_pyq(a['body'])}], axis={_pyq(st['axis'])}, angle={self.s(st['angle'], 'deg')}, pivot={tuple(piv)!r})")
                elif st["type"] == "rotate":
                    for ax, key in (("z", "rz"), ("y", "ry"), ("x", "rx")):
                        if st[key].is_formula or st[key].value:
                            lines.append(f"_guard({_pyq(n.id)}, _move, BODIES[{_pyq(a['body'])}], axis={_pyq(ax)}, angle={self.s(st[key], 'deg')})")
                else:
                    lines.append(f"_guard({_pyq(n.id)}, _move, BODIES[{_pyq(a['body'])}], {self.s(st['x'])}, {self.s(st['y'])}, {self.s(st['z'])})")
            return lines
        return [tag, f"# UNSUPPORTED: {'; '.join(n.notes)}"]


def export_nxopen_journal(ir: IR) -> str:
    """The NXOpen journal (Python) for `ir`."""
    return _NX(ir).gen()


# ======================================================================================================================================
# Backend 2: Autodesk Fusion (Python add-in script)
# ======================================================================================================================================
_FUSION_PRELUDE = '''
import math
import traceback
import adsk.core
import adsk.fusion

app = adsk.core.Application.get()
ui = app.userInterface
design = adsk.fusion.Design.cast(app.activeProduct)
root = design.rootComponent
units = design.unitsManager
BODIES = {}          # satCAD part name -> BRepBody
SKIPPED = []


def val(text):
    return adsk.core.ValueInput.createByString(text)


def add_param(name, expression, comment):
    """A user parameter (Modify > Change Parameters). Unitless on purpose: satCAD formulas mix lengths, angles and counts, which Fusion's unit checking would
    reject; every use below multiplies by '1 mm' or '1 deg'."""
    design.userParameters.add(name, val(expression), "", comment)


def _guard(name, fn, *args):
    try:
        return fn(*args)
    except Exception:
        SKIPPED.append((name, traceback.format_exc().splitlines()[-1]))


def _bind_dimension(sketch, line, expression, vertical=False):
    """A driving dimension on a sketch line, bound to an expression of user parameters."""
    orientation = adsk.fusion.DimensionOrientations.VerticalDimensionOrientation if vertical else adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation
    dim = sketch.sketchDimensions.addDistanceDimension(line.startSketchPoint, line.endSketchPoint, orientation, adsk.core.Point3D.create(0.5, 0.5, 0))
    dim.parameter.expression = expression


def _extrude(sketch, distance_expr, symmetric=True):
    ext = root.features.extrudeFeatures
    inp = ext.createInput(sketch.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    if symmetric:
        inp.setSymmetricExtent(val(distance_expr), True)
    else:
        inp.setDistanceExtent(False, val(distance_expr))
    return ext.add(inp)


def _move(body, matrix):
    items = adsk.core.ObjectCollection.create()
    items.add(body)
    moves = root.features.moveFeatures
    inp = moves.createInput2(items)
    inp.defineAsFreeMove(matrix)
    return moves.add(inp)


def _rotate(body, axis, angle_expr, pivot=(0.0, 0.0, 0.0)):
    """Turn a body about a fixed axis through `pivot` (mm). The angle is an expression, evaluated when the script runs."""
    m = adsk.core.Matrix3D.create()
    vec = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
    m.setToRotation(units.evaluateExpression(angle_expr, "deg"), adsk.core.Vector3D.create(*vec),
                    adsk.core.Point3D.create(pivot[0] / 10.0, pivot[1] / 10.0, pivot[2] / 10.0))     # Fusion works in centimetres
    _move(body, m)


def _translate(body, tx, ty, tz):
    m = adsk.core.Matrix3D.create()
    m.translation = adsk.core.Vector3D.create(units.evaluateExpression(tx, "mm"), units.evaluateExpression(ty, "mm"), units.evaluateExpression(tz, "mm"))
    _move(body, m)


def _place(body, tx, ty, tz, rx, ry, rz):
    """satCAD: Rot(rx, ry, rz) = Rx*Ry*Rz (the vector is turned about the fixed Z axis first, then Y, then X), then the translation."""
    for axis, ang in (("z", rz), ("y", ry), ("x", rx)):
        if ang not in ("0 deg", "0"):
            _rotate(body, axis, ang)
    if any(v not in ("0 mm", "0") for v in (tx, ty, tz)):
        _translate(body, tx, ty, tz)


def f_box(name, L, W, H, rx, ry, rz, tx, ty, tz):
    """A centred rectangle on the XY plane, mid-plane extruded: width, depth and height are expressions of the user parameters."""
    sk = root.sketches.add(root.xYConstructionPlane)
    lines = sk.sketchCurves.sketchLines.addCenterPointRectangle(adsk.core.Point3D.create(0, 0, 0), adsk.core.Point3D.create(0.5, 0.5, 0))
    _bind_dimension(sk, lines.item(0), L)
    _bind_dimension(sk, lines.item(1), W, vertical=True)
    body = _extrude(sk, H, True).bodies.item(0)
    body.name = name
    BODIES[name] = body
    _place(body, tx, ty, tz, rx, ry, rz)


def f_cylinder(name, axis, radius, height, rx, ry, rz, tx, ty, tz):
    sk = root.sketches.add(root.xYConstructionPlane)
    circle = sk.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), 0.5)
    sk.sketchDimensions.addDiameterDimension(circle, adsk.core.Point3D.create(1, 1, 0)).parameter.expression = "2 * (%s)" % radius
    body = _extrude(sk, height, True).bodies.item(0)
    body.name = name
    BODIES[name] = body
    if axis == "x":
        _rotate(body, "y", "90 deg")
    elif axis == "y":
        _rotate(body, "x", "-90 deg")
    _place(body, tx, ty, tz, rx, ry, rz)


def _combine(op, target_name, tool_names):
    tools = adsk.core.ObjectCollection.create()
    for t in tool_names:
        tools.add(BODIES[t])
    combines = root.features.combineFeatures
    inp = combines.createInput(BODIES[target_name], tools)
    inp.operation = {"Join": adsk.fusion.FeatureOperations.JoinFeatureOperation, "Cut": adsk.fusion.FeatureOperations.CutFeatureOperation,
                     "Intersect": adsk.fusion.FeatureOperations.IntersectFeatureOperation}[op]
    inp.isKeepToolBodies = False
    combines.add(inp)
    for t in tool_names:
        BODIES.pop(t, None)


def f_pattern_linear(name, source, count_expr, dx, dy, dz):
    """Rectangular pattern whose quantity and spacing are expressions of the user parameters (along the first axis with a non-zero pitch)."""
    items = adsk.core.ObjectCollection.create()
    items.add(BODIES[source])
    axis, spacing = next((a for a in (("x", dx), ("y", dy), ("z", dz)) if a[1] not in ("0 mm", "0")), ("x", dx))
    axis_obj = {"x": root.xConstructionAxis, "y": root.yConstructionAxis, "z": root.zConstructionAxis}[axis]
    patterns = root.features.rectangularPatternFeatures
    inp = patterns.createInput(items, axis_obj, val(count_expr), val(spacing), adsk.fusion.PatternDistanceType.SpacingPatternDistanceType)
    patterns.add(inp)
'''


class _Fusion:
    def __init__(self, ir):
        self.ir = ir
        self.n = _Names("fusion")

    def r(self, e, unit="mm"):
        return render(e, "fusion", unit, self.n)

    def s(self, e, unit="mm"):
        return _pyq(self.r(e, unit))

    def pose(self, a):
        return [self.s(a[k], "deg") for k in ("rx", "ry", "rz")] + [self.s(a[k], "mm") for k in ("x", "y", "z")]

    def gen(self) -> str:
        ir = self.ir
        out = _header(ir, "fusion", "Autodesk Fusion API script: rebuilds the satCAD history as a parametric timeline driven by User Parameters")
        out += ["# Run in Fusion: Utilities > Add-Ins > Scripts and Add-Ins > '+' > pick this file. Start with a new empty design.", _FUSION_PRELUDE, "",
                "def run(context):",
                "    try:",
                "        # ---- 1. User parameters (Modify > Change Parameters) ----------------------------------------------------------------------"]
        for p in _param_order(ir):
            unit = f" [{p.unit}]" if p.unit else ""
            out.append(f"        add_param({_pyq(p.name)}, {_pyq(render_param(p.expr, 'fusion', self.n))}, {_pyq('satCAD' + unit + ': ' + p.expr.neutral())})")
        out.append("        # ---- 2. Timeline ----------------------------------------------------------------------------------------------------------")
        for node in ir.nodes:
            out += ["        " + line for line in self.node(node)]
        out += ["        if SKIPPED:", "            ui.messageBox('satCAD rebuild finished with %d skipped feature(s):\\n' % len(SKIPPED) + '\\n'.join('%s: %s' % s for s in SKIPPED))",
                "        else:", "            ui.messageBox('satCAD rebuild finished: %d bodies' % len(BODIES))",
                "    except Exception:", "        if ui:", "            ui.messageBox('Failed:\\n{}'.format(traceback.format_exc()))", "", "", "run(None)", ""]
        return "\n".join(out)

    def node(self, n: FeatureNode) -> list:
        a, k = n.args, n.kind
        tag = f"# [{n.fidelity}] {n.source_kind or n.kind} '{n.id}'" + (f"  ({'; '.join(dict.fromkeys(n.notes))})" if n.notes else "")
        if k in ("CSYS_Datum",):
            return ["# Model origin = Fusion origin; satCAD axes: X forward, Y left, Z up (Fusion's default is Y up: set the design orientation to Z up in Preferences)"]
        if k == "Assembly_Group":
            return [tag]
        if k == "Primitive_Box":
            return [tag, f"_guard({_pyq(n.id)}, f_box, {_pyq(n.id)}, {self.s(a['length'])}, {self.s(a['width'])}, {self.s(a['height'])}, {', '.join(self.pose(a))})"]
        if k == "Primitive_Cylinder":
            return [tag, f"_guard({_pyq(n.id)}, f_cylinder, {_pyq(n.id)}, {_pyq(a['axis'])}, {self.s(a['radius'])}, {self.s(a['height'])}, {', '.join(self.pose(a))})"]
        if k == "Primitive_Sphere":
            return [tag, "# UNSUPPORTED in this script: sphere"]
        if k == "Sketch_Extrude":
            return [tag, f"# sketch ({a['profile']} on the {a['plane']} plane) + extrude {self.r(a['depth'])}: written as a note; profile sketches other than the centred "
                         "box / cylinder are not generated for Fusion yet; use the STEP body"]
        if k == "Revolve":
            return [tag, "# UNSUPPORTED in this script: revolve"]
        if k == "Hollow_Shell" and a["mode"] == "cavity":
            inner = [f"({self.r(a[d])}) - 2 * ({self.r(a['wall'])})" for d in ("length", "width", "height")]
            return [tag, f"_guard({_pyq(n.id + '__cavity')}, f_box, {_pyq(n.id + '__cavity')}, {_pyq(inner[0])}, {_pyq(inner[1])}, {_pyq(inner[2])}, {', '.join(self.pose(a))})",
                    f"_guard({_pyq(n.id)}, _combine, 'Cut', {_pyq(n.id)}, [{_pyq(n.id + '__cavity')}])"]
        if k == "Hollow_Shell":
            return [tag, f"# shell of '{a['target']}', faces {a['open_faces']}, wall {self.r(a['wall'])}: root.features.shellFeatures (faces are picked in Fusion)"]
        if k == "Boolean_Op":
            return [tag, f"_guard({_pyq(n.id)}, _combine, {_pyq({'unite': 'Join', 'subtract': 'Cut', 'intersect': 'Intersect'}[a['operation']])}, {_pyq(a['target'])}, {a['tools']!r})"]
        if k == "Linear_Pattern":
            if "grid" in a:
                return [tag] + [f"_guard({_pyq(n.id)}, f_pattern_linear, {_pyq(n.id)}, {_pyq(a['source'])}, {self.s(st['count'], '')}, " + ", ".join(self.s(x) for x in st["pitch"]) + ")"
                                for st in a["grid"]]
            return [tag, f"_guard({_pyq(n.id)}, f_pattern_linear, {_pyq(n.id)}, {_pyq(a['source'])}, {self.s(a['count'], '')}, " + ", ".join(self.s(x) for x in a["pitch"]) + ")"]
        if k == "Circular_Pattern":
            return [tag, f"# circular pattern of '{a['source']}': quantity {self.r(a['count'], '')}, axis {a['axis']}: root.features.circularPatternFeatures"]
        if k == "Mirror":
            return [tag, f"# mirror of '{a['source']}' about the {a['plane']} plane: root.features.mirrorFeatures"]
        if k == "Fillet_Chamfer":
            return [tag, f"# edge treatment on '{a['target']}', edges '{a['edges']}', radius {self.r(a['radius'])}: root.features.filletFeatures (edges selected by direction)"]
        if k in ("Cut_Box", "Cut_Cylinder"):
            self._cut = getattr(self, "_cut", 0) + 1
            tool = f"{a['target']}__cut{self._cut}"
            mm = lambda v: _pyq(fmt_num(v.value) + " mm")
            c = [mm(v) for v in a["center"]]
            if k == "Cut_Box":
                make = f"_guard({_pyq(tool)}, f_box, {_pyq(tool)}, {', '.join(mm(v) for v in a['size'])}, '0 deg', '0 deg', '0 deg', {', '.join(c)})"
            else:
                make = (f"_guard({_pyq(tool)}, f_cylinder, {_pyq(tool)}, {_pyq(a['axis'])}, {mm(a['radius'])}, {mm(a['height'])}, '0 deg', '0 deg', '0 deg', {', '.join(c)})")
            return [tag, make, f"_guard({_pyq(n.id)}, _combine, 'Cut', {_pyq(a['target'])}, [{_pyq(tool)}])"]
        if k == "Component_Transform":
            lines = [tag]
            for st in a["steps"]:
                if st["type"] == "hinge":
                    lines.append(f"_guard({_pyq(n.id)}, _rotate, BODIES[{_pyq(a['body'])}], {_pyq(st['axis'])}, {self.s(st['angle'], 'deg')}, {tuple(v.value for v in st['pivot'])!r})")
                elif st["type"] == "rotate":
                    for ax, key in (("z", "rz"), ("y", "ry"), ("x", "rx")):
                        if st[key].is_formula or st[key].value:
                            lines.append(f"_guard({_pyq(n.id)}, _rotate, BODIES[{_pyq(a['body'])}], {_pyq(ax)}, {self.s(st[key], 'deg')})")
                else:
                    lines.append(f"_guard({_pyq(n.id)}, _translate, BODIES[{_pyq(a['body'])}], {self.s(st['x'])}, {self.s(st['y'])}, {self.s(st['z'])})")
            return lines
        return [tag, f"# UNSUPPORTED: {'; '.join(n.notes)}"]


def export_fusion_script(ir: IR) -> str:
    """The Autodesk Fusion API script (Python) for `ir`."""
    return _Fusion(ir).gen()


# ======================================================================================================================================
# Backend 3: SolidWorks (VBA macro)
# ======================================================================================================================================
_SW_PRELUDE = '''
Dim swApp As SldWorks.SldWorks
Dim Part As SldWorks.ModelDoc2
Dim swEqnMgr As SldWorks.EquationMgr
Dim boolstatus As Boolean
Dim skippedCount As Long
Dim sketchCount As Long
Dim featCount As Long

Private Sub AddEquation(ByVal text As String)
    swEqnMgr.Add2 -1, text, True
End Sub

Private Sub BindDimension(ByVal dimName As String, ByVal expr As String)
    ' links a dimension of a sketch or feature to an expression of the global variables
    AddEquation """" & dimName & """ = " & expr
End Sub

Private Sub NextSketch()
    sketchCount = sketchCount + 1
End Sub

Private Sub MakeBox(ByVal nm As String, ByVal cx As Double, ByVal cy As Double, ByVal cz As Double, ByVal Lx As Double, ByVal Wy As Double, ByVal Hz As Double, _
                    ByVal LExpr As String, ByVal WExpr As String, ByVal HExpr As String)
    ' centre-rectangle sketch on the Top plane at the box's mid-height, extruded half the height both ways (mid-plane): dimensions bound to the equations
    Dim seg As Variant
    boolstatus = Part.Extension.SelectByID2("Top Plane", "PLANE", 0, 0, 0, False, 0, Nothing, 0)
    Part.SketchManager.InsertSketch True
    NextSketch
    Set seg = Part.SketchManager.CreateCenterRectangle(cx / 1000#, cy / 1000#, 0#, (cx + Lx / 2) / 1000#, (cy + Wy / 2) / 1000#, 0#)
    Part.ClearSelection2 True
    ' name the sketch dimensions by adding smart dimensions on the two edges (D1/D2) and binding them
    Part.AddDimension2 (cx + Lx / 2) / 1000#, cy / 1000#, 0#
    Part.AddDimension2 cx / 1000#, (cy + Wy / 2) / 1000#, 0#
    If LExpr <> "" Then BindDimension "D1@Sketch" & sketchCount, LExpr
    If WExpr <> "" Then BindDimension "D2@Sketch" & sketchCount, WExpr
    Part.SketchManager.InsertSketch True
    boolstatus = Part.Extension.SelectByID2("Sketch" & sketchCount, "SKETCH", 0, 0, 0, False, 0, Nothing, 0)
    Dim f As Object
    Set f = Part.FeatureManager.FeatureExtrusion3(True, False, False, swEndCondMidPlane, 0, Hz / 1000#, 0, False, False, False, False, 0, 0, False, False, False, False, True, True, True, 0, 0, False)
    featCount = featCount + 1
    If Not f Is Nothing Then f.Name = nm
    If HExpr <> "" Then BindDimension "D1@" & nm, HExpr
End Sub
'''


class _SW:
    def __init__(self, ir):
        self.ir = ir
        self.n = _Names("solidworks")

    def r(self, e, unit="mm"):
        return render(e, "solidworks", unit, self.n)

    def q(self, text: str) -> str:
        return '"' + text.replace('"', '""') + '"'

    def val(self, e) -> str:
        """The VBA number for an evaluated value (SolidWorks wants metres in the API, so the helpers divide by 1000)."""
        return fmt_num(e.value)

    def gen(self) -> str:
        ir = self.ir
        out = _header(ir, "solidworks", "SolidWorks VBA macro: rebuilds the satCAD history; every variable goes into the Equation Manager")
        out += ["' Run in SolidWorks: Tools > Macro > New... paste this file's text into the module (or save it as .swp). Needs references to SldWorks and SwConst",
                "' (they are added automatically in a new macro). Start with Application.NewDocument of a PART template set to millimetres.", "",
                _SW_PRELUDE, "", "Sub main()", "    Set swApp = Application.SldWorks",
                "    Set Part = swApp.NewDocument(swApp.GetUserPreferenceStringValue(swDefaultTemplatePart), 0, 0, 0)",
                "    Part.SetUserPreferenceIntegerValue swUnitsLinear, swMM",
                "    Set swEqnMgr = Part.GetEquationMgr",
                "    swEqnMgr.AutomaticRebuild = False",
                "    ' ---- 1. Equation Manager: every satCAD variable and formula (global variables; values are in millimetres / degrees) -----------------------"]
        for p in _param_order(ir):
            body = render_param(p.expr, "solidworks", self.n)
            out.append(f"    AddEquation {self.q(chr(34) + p.name + chr(34) + ' = ' + body)}   ' satCAD: {p.expr.neutral()}")
        out.append("    ' ---- 2. Feature history ---------------------------------------------------------------------------------------------------------------")
        for node in ir.nodes:
            out += ["    " + line for line in self.node(node)]
        out += ["    swEqnMgr.AutomaticRebuild = True", "    Part.ForceRebuild3 False", "    Part.ViewZoomtofit2",
                '    MsgBox "satCAD rebuild finished: " & featCount & " features"', "End Sub", ""]
        return "\n".join(out)

    def node(self, n: FeatureNode) -> list:
        a, k = n.args, n.kind
        tag = f"' [{n.fidelity}] {n.source_kind or n.kind} '{n.id}'" + (f"  ({'; '.join(dict.fromkeys(n.notes))})" if n.notes else "")
        if k == "CSYS_Datum":
            return ["' Model origin = SolidWorks Origin; satCAD axes X forward, Y left, Z up (SolidWorks Top Plane normal is Y: bodies below are built in satCAD's Z)"]
        if k == "Assembly_Group":
            return [tag]
        if k == "Primitive_Box":
            return [tag, f"MakeBox {self.q(n.id)}, {self.val(a['x'])}, {self.val(a['y'])}, {self.val(a['z'])}, {self.val(a['length'])}, {self.val(a['width'])}, "
                         f"{self.val(a['height'])}, {self.q(self.r(a['length']) if a['length'].is_formula else '')}, "
                         f"{self.q(self.r(a['width']) if a['width'].is_formula else '')}, {self.q(self.r(a['height']) if a['height'].is_formula else '')}"]
        if k == "Hollow_Shell" and a["mode"] == "cavity":
            return [tag, f"' hollow: cut a box inset by wall = {self.r(a['wall'])} on every side (InsertFeatureShell needs a picked face; the cavity is built explicitly)",
                    f"MakeBox {self.q(n.id + '__cavity')}, {self.val(a['x'])}, {self.val(a['y'])}, {self.val(a['z'])}, {self.val(combine('-', a['length'], combine('*', lit(2), a['wall'])))}, "
                    f"{self.val(combine('-', a['width'], combine('*', lit(2), a['wall'])))}, {self.val(combine('-', a['height'], combine('*', lit(2), a['wall'])))}, "
                    f"{self.q('(' + self.r(a['length']) + ') - 2 * (' + self.r(a['wall']) + ')')}, {self.q('(' + self.r(a['width']) + ') - 2 * (' + self.r(a['wall']) + ')')}, "
                    f"{self.q('(' + self.r(a['height']) + ') - 2 * (' + self.r(a['wall']) + ')')}", "' (combine the cavity body with Insert > Features > Combine, Subtract)"]
        if k == "Hollow_Shell":
            return [tag, f"' shell of '{a['target']}' with wall {self.r(a['wall'])}: Part.FeatureManager.InsertFeatureShell(thickness, outward) after selecting the open faces"]
        if k in ("Primitive_Cylinder", "Primitive_Sphere", "Sketch_Extrude", "Revolve"):
            return [tag, f"' {k} '{n.id}': not generated for SolidWorks yet (use the STEP body). Parameters: " + ", ".join(
                f"{kk}={self.r(vv)}" for kk, vv in a.items() if isinstance(vv, Expr))]
        if k == "Boolean_Op":
            return [tag, f"' {a['operation']} of {a['tools']} into '{a['target']}': Insert > Features > Combine"]
        if k == "Linear_Pattern":
            first = a["pitch"]
            return [tag, f"' linear pattern of '{a['source']}': Part.FeatureManager.FeatureLinearPattern4(count, spacing, ...), count = {self.r(a['count'], '')}, "
                         f"spacing = {', '.join(self.r(x) for x in first)}; then bind with an equation  \"D1@LPattern1\" = {self.r(a['count'], '')}"]
        if k in ("Circular_Pattern", "Mirror", "Fillet_Chamfer", "Cut_Box", "Cut_Cylinder", "Component_Transform"):
            return [tag, f"' {k}: described here, not generated for SolidWorks yet"]
        return [tag, f"' UNSUPPORTED: {'; '.join(n.notes)}"]


def export_solidworks_macro(ir: IR) -> str:
    """The SolidWorks VBA macro for `ir`."""
    return _SW(ir).gen()


# ======================================================================================================================================
# Backend 4: FreeCAD (FCMacro)
# ======================================================================================================================================
_FC_PRELUDE = '''
import math
import FreeCAD as App
import Part

doc = App.newDocument(__DOC__)
sheet = doc.addObject("Spreadsheet::Sheet", "Params")
OBJ = {}             # satCAD part name -> the FreeCAD object that currently carries that name
SKIPPED = []
_row = [0]


def add_param(name, cell_text, unit, note):
    """One row of the Params spreadsheet: A = name, B = the value or formula (its alias is the name), C = unit, D = the original satCAD formula."""
    _row[0] += 1
    r = _row[0]
    sheet.set("A%d" % r, name)
    sheet.set("B%d" % r, cell_text)
    sheet.setAlias("B%d" % r, name)
    sheet.set("C%d" % r, unit)
    sheet.set("D%d" % r, note)


def _guard(name, fn, *args):
    try:
        return fn(*args)
    except Exception as exc:
        SKIPPED.append((name, str(exc)))
        App.Console.PrintWarning("satCAD transpiler: skipped %s: %s\\n" % (name, exc))


def bind(obj, prop, expr):
    """Bind a property to an expression of the Params spreadsheet (aliases are read as Params.<alias>)."""
    obj.setExpression(prop, expr)


def _rot(rx, ry, rz):
    """Rot(rx, ry, rz) as satCAD (build123d) defines it: Rx * Ry * Rz, i.e. a vector is turned about the fixed Z axis first, then Y, then X."""
    return App.Rotation(App.Vector(1, 0, 0), rx) * App.Rotation(App.Vector(0, 1, 0), ry) * App.Rotation(App.Vector(0, 0, 1), rz)


def _is_turned(rot):
    return abs(rot.Angle) > 1e-12


def f_box(name, L, W, H, cx, cy, cz, rx, ry, rz, num):
    """Part::Box. L, W, H are expressions (always bound). cx, cy, cz are expressions when the box is not rotated and not moved afterwards, else None and the
    numbers in `num` = (L, W, H, cx, cy, cz) are used."""
    o = doc.addObject("Part::Box", name)
    bind(o, "Length", L)
    bind(o, "Width", W)
    bind(o, "Height", H)
    OBJ[name] = o
    rot = _rot(rx, ry, rz)
    if cx is not None and not _is_turned(rot):
        bind(o, "Placement.Base.x", "(%s) - (%s) / 2" % (cx, L))
        bind(o, "Placement.Base.y", "(%s) - (%s) / 2" % (cy, W))
        bind(o, "Placement.Base.z", "(%s) - (%s) / 2" % (cz, H))
    else:
        half = rot.multVec(App.Vector(num[0] / 2.0, num[1] / 2.0, num[2] / 2.0))
        o.Placement = App.Placement(App.Vector(num[3], num[4], num[5]) - half, rot)


def f_cylinder(name, axis, radius, height, cx, cy, cz, rx, ry, rz, num):
    """Part::Cylinder (built along +Z from its base). num = (radius, height, cx, cy, cz)."""
    o = doc.addObject("Part::Cylinder", name)
    bind(o, "Radius", radius)
    bind(o, "Height", height)
    OBJ[name] = o
    turn = {"x": App.Rotation(App.Vector(0, 1, 0), 90), "y": App.Rotation(App.Vector(1, 0, 0), -90)}.get(axis, App.Rotation())
    rot = _rot(rx, ry, rz) * turn
    if cx is not None and not _is_turned(rot):
        bind(o, "Placement.Base.x", cx)
        bind(o, "Placement.Base.y", cy)
        bind(o, "Placement.Base.z", "(%s) - (%s) / 2" % (cz, height))
    else:
        o.Placement = App.Placement(App.Vector(num[2], num[3], num[4]) - rot.multVec(App.Vector(0, 0, num[1] / 2.0)), rot)


def f_sphere(name, radius, cx, cy, cz, rx, ry, rz, num):
    o = doc.addObject("Part::Sphere", name)
    bind(o, "Radius", radius)
    OBJ[name] = o
    rot = _rot(rx, ry, rz)
    if cx is not None:
        bind(o, "Placement.Base.x", cx)
        bind(o, "Placement.Base.y", cy)
        bind(o, "Placement.Base.z", cz)
        o.Placement = App.Placement(o.Placement.Base, rot)
    else:
        o.Placement = App.Placement(App.Vector(num[0], num[1], num[2]), rot)


def _boolean(kind, target_name, tool_names):
    result = OBJ[target_name]
    cls = {"Join": "Part::Fuse", "Cut": "Part::Cut", "Intersect": "Part::Common"}[kind]
    for i, t in enumerate(tool_names):
        r = doc.addObject(cls, "%s_%s%d" % (target_name, kind.lower(), i))
        r.Base, r.Tool = result, OBJ[t]
        result = r
    OBJ[target_name] = result
    for t in tool_names:
        OBJ.pop(t, None)


def f_cut_box(target, tool, size, centre):
    f_box(tool, "%r mm" % size[0], "%r mm" % size[1], "%r mm" % size[2], None, None, None, 0, 0, 0, (size[0], size[1], size[2], centre[0], centre[1], centre[2]))
    _boolean("Cut", target, [tool])


def f_cut_cylinder(target, tool, axis, radius, height, centre):
    f_cylinder(tool, axis, "%r mm" % radius, "%r mm" % height, None, None, None, 0, 0, 0, (radius, height, centre[0], centre[1], centre[2]))
    _boolean("Cut", target, [tool])


def f_pattern_linear(name, source, count_expr, dx, dy, dz):
    """A Draft ortho array: the number of copies and the spacing vector are bound to the Params spreadsheet."""
    import Draft
    arr = Draft.make_ortho_array(OBJ[source], App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1), n_x=2, n_y=1, n_z=1, use_link=False)
    bind(arr, "NumberX", count_expr)
    bind(arr, "IntervalX.x", dx)
    bind(arr, "IntervalX.y", dy)
    bind(arr, "IntervalX.z", dz)
    OBJ[name] = arr


def f_pattern_polar(name, source, count_expr, total_angle, centre):
    import Draft
    arr = Draft.make_polar_array(OBJ[source], 2, total_angle, App.Vector(centre[0], centre[1], centre[2]), use_link=False)
    bind(arr, "NumberPolar", count_expr)
    OBJ[name] = arr


def f_mirror(name, source, plane, offset):
    m = doc.addObject("Part::Mirroring", name)
    m.Source = OBJ[source]
    m.Normal = {"xy": App.Vector(0, 0, 1), "yz": App.Vector(1, 0, 0), "zx": App.Vector(0, 1, 0)}[plane]
    m.Base = App.Vector(m.Normal.x * offset, m.Normal.y * offset, m.Normal.z * offset)
    OBJ[name] = m


def f_fillet(target_name, selector, radius, chamfer):
    """Round (radius > 0) or bevel the edges selected by `selector` ('all' or 'parallel:x|y|z'). FreeCAD cannot bind a fillet size to an expression: numbers."""
    base = OBJ[target_name]
    idx = []
    for i, e in enumerate(base.Shape.Edges, 1):
        if len(e.Vertexes) < 2:
            continue
        d = e.Vertexes[-1].Point - e.Vertexes[0].Point
        keep = selector == "all"
        if selector.startswith("parallel:") and d.Length > 1e-9:
            axis = {"x": App.Vector(1, 0, 0), "y": App.Vector(0, 1, 0), "z": App.Vector(0, 0, 1)}[selector[9:]]
            keep = abs(d.normalize().dot(axis)) > 0.999
        if keep:
            idx.append(i)
    if not idx:
        raise RuntimeError("no edges matched " + selector)
    size = radius if radius else chamfer
    f = doc.addObject("Part::Fillet" if radius else "Part::Chamfer", target_name + ("_fillet" if radius else "_chamfer"))
    f.Base = base
    f.Edges = [(i, size, size) for i in idx]
    OBJ[target_name] = f


def _xform(name, steps):
    """Component_Transform: move a finished body through its assemblies. steps (innermost assembly first, evaluated numbers):
    ("hinge", axis, degrees, px, py, pz), ("rotate", rx, ry, rz), ("translate", x, y, z). Each step acts on the result of the previous ones."""
    obj = OBJ[name]
    pl = obj.Placement
    for step in steps:
        if step[0] == "translate":
            st = App.Placement(App.Vector(step[1], step[2], step[3]), App.Rotation())
        elif step[0] == "rotate":
            st = App.Placement(App.Vector(0, 0, 0), _rot(step[1], step[2], step[3]))
        else:
            axis = {"x": App.Vector(1, 0, 0), "y": App.Vector(0, 1, 0), "z": App.Vector(0, 0, 1)}[step[1]]
            st = App.Placement(App.Vector(0, 0, 0), App.Rotation(axis, step[2]), App.Vector(step[3], step[4], step[5]))
        pl = st.multiply(pl)
    obj.Placement = pl
'''


class _FC:
    def __init__(self, ir):
        self.ir = ir
        self.cell = _Names("freecad", binding=False)
        self.bind = _Names("freecad", binding=True)
        self.moved = {n.id for n in ir.nodes if n.kind == "Component_Transform"}
        self._cut = 0

    def b(self, e, unit="mm"):
        return render(e, "freecad", unit, self.bind)

    def sb(self, e, unit="mm") -> str:
        return _pyq(self.b(e, unit))

    def cell_text(self, p: Param) -> str:
        if not p.expr.is_formula:
            return fmt_num(p.expr.value)
        try:
            return "=" + to_dialect(p.expr.src, "freecad", self.cell).replace("1 deg", "1")
        except _Unsupported:
            return fmt_num(p.value)

    def next_tool(self, target: str) -> str:
        self._cut += 1
        return f"{target}__cut{self._cut}"

    def rot(self, a):
        return [fmt_num(a[k].value) for k in ("rx", "ry", "rz")]

    def turned(self, a) -> bool:
        return any(abs(a[k].value) > 1e-12 for k in ("rx", "ry", "rz"))

    def centre(self, a, nid):
        """Bound centre expressions, or None when the body is rotated or moved afterwards (then numbers are used)."""
        if self.turned(a) or nid in self.moved:
            return ["None"] * 3
        return [self.sb(a[k]) for k in ("x", "y", "z")]

    def gen(self) -> str:
        ir = self.ir
        out = _header(ir, "freecad", "FreeCAD macro: a Params spreadsheet drives Part features through expressions")
        out += ["# Run in FreeCAD: Macro > Macros... > Create (paste) > Execute. Creates a new document.", _FC_PRELUDE.replace("__DOC__", _pyq(re.sub(r"[^A-Za-z0-9_]", "_", ir.name))), "",
                "# ---- 1. Params spreadsheet: every satCAD variable (alias = its name) ----------------------------------------------------------------------"]
        for p in _param_order(ir):
            out.append(f"add_param({_pyq(_sanitize_alias(p.name))}, {_pyq(self.cell_text(p))}, {_pyq(p.unit)}, {_pyq(p.expr.neutral())})")
        out += ["doc.recompute()", "", "# ---- 2. Features ---------------------------------------------------------------------------------------------------------------------------"]
        for node in ir.nodes:
            out += self.node(node)
        out += ["", "doc.recompute()", "print('satCAD rebuild finished: %d objects, %d skipped' % (len(OBJ), len(SKIPPED)))", ""]
        return "\n".join(out)

    def node(self, n: FeatureNode) -> list:
        a, k = n.args, n.kind
        tag = f"# [{n.fidelity}] {n.source_kind or n.kind} '{n.id}'" + (f"  ({'; '.join(dict.fromkeys(n.notes))})" if n.notes else "")
        if k == "CSYS_Datum":
            return ["# Model origin = FreeCAD origin; axes X forward, Y left, Z up (as in satCAD)."]
        if k == "Assembly_Group":
            return [tag]
        if k == "Primitive_Box":
            num = tuple(a[d].value for d in ("length", "width", "height", "x", "y", "z"))
            return [tag, f"_guard({_pyq(n.id)}, f_box, {_pyq(n.id)}, {self.sb(a['length'])}, {self.sb(a['width'])}, {self.sb(a['height'])}, "
                         f"{', '.join(self.centre(a, n.id))}, {', '.join(self.rot(a))}, {num!r})"]
        if k == "Primitive_Cylinder":
            num = (a["radius"].value, a["height"].value, a["x"].value, a["y"].value, a["z"].value)
            return [tag, f"_guard({_pyq(n.id)}, f_cylinder, {_pyq(n.id)}, {_pyq(a['axis'])}, {self.sb(a['radius'])}, {self.sb(a['height'])}, "
                         f"{', '.join(self.centre(a, n.id))}, {', '.join(self.rot(a))}, {num!r})"]
        if k == "Primitive_Sphere":
            num = (a["x"].value, a["y"].value, a["z"].value)
            return [tag, f"_guard({_pyq(n.id)}, f_sphere, {_pyq(n.id)}, {self.sb(a['radius'])}, {', '.join(self.centre(a, n.id))}, {', '.join(self.rot(a))}, {num!r})"]
        if k in ("Sketch_Extrude", "Revolve"):
            return [tag, f"# {k} '{n.id}' ({a.get('profile', 'revolved profile')}): not generated for FreeCAD yet; use the STEP body"]
        if k == "Hollow_Shell" and a["mode"] == "cavity":
            inner = [Expr(f"({a[d].neutral()}) - 2 * ({a['wall'].neutral()})", a[d].value - 2 * a["wall"].value) if (a[d].is_formula or a["wall"].is_formula)
                     else lit(a[d].value - 2 * a["wall"].value) for d in ("length", "width", "height")]
            cav = n.id + "__cavity"
            num = (inner[0].value, inner[1].value, inner[2].value, a["x"].value, a["y"].value, a["z"].value)
            c = self.centre(a, n.id)
            return [tag, f"_guard({_pyq(cav)}, f_box, {_pyq(cav)}, {self.sb(inner[0])}, {self.sb(inner[1])}, {self.sb(inner[2])}, {', '.join(c)}, {', '.join(self.rot(a))}, {num!r})",
                    f"_guard({_pyq(n.id)}, _boolean, 'Cut', {_pyq(n.id)}, [{_pyq(cav)}])"]
        if k == "Hollow_Shell":
            return [tag, f"# shell of '{a['target']}' faces {a['open_faces']} wall {self.b(a['wall'])}: Part::Thickness with the faces chosen by normal; not generated"]
        if k == "Boolean_Op":
            return [tag, f"_guard({_pyq(n.id)}, _boolean, {_pyq({'unite': 'Join', 'subtract': 'Cut', 'intersect': 'Intersect'}[a['operation']])}, {_pyq(a['target'])}, {a['tools']!r})"]
        if k == "Linear_Pattern":
            if "grid" in a:
                return [tag] + [f"_guard({_pyq(n.id)}, f_pattern_linear, {_pyq(n.id)}, {_pyq(a['source'])}, {self.sb(st['count'], '')}, " + ", ".join(self.sb(x) for x in st["pitch"]) + ")"
                                for st in a["grid"]]
            return [tag, f"_guard({_pyq(n.id)}, f_pattern_linear, {_pyq(n.id)}, {_pyq(a['source'])}, {self.sb(a['count'], '')}, " + ", ".join(self.sb(x) for x in a["pitch"]) + ")"]
        if k == "Circular_Pattern":
            if a["axis"] != "z":
                return [tag, f"# circular pattern about the {a['axis']} axis: FreeCAD's polar array turns about Z only; not generated"]
            return [tag, f"_guard({_pyq(n.id)}, f_pattern_polar, {_pyq(n.id)}, {_pyq(a['source'])}, {self.sb(a['count'], '')}, {a['total_angle'].value!r}, "
                         f"{tuple(c.value for c in a['center'])!r})"]
        if k == "Mirror":
            return [tag, f"_guard({_pyq(n.id)}, f_mirror, {_pyq(n.id)}, {_pyq(a['source'])}, {_pyq(a['plane'])}, {a['offset'].value!r})"]
        if k == "Fillet_Chamfer":
            rad = a["radius"].value if a["radius"].value > 0 else None
            return [tag, f"# (FreeCAD cannot bind a fillet size to an expression: the number {rad if rad else a['chamfer'].value:g} is used)",
                    f"_guard({_pyq(n.id)}, f_fillet, {_pyq(a['target'])}, {_pyq(a['edges'])}, {rad!r}, {a['chamfer'].value!r})"]
        if k == "Cut_Box":
            tool = self.next_tool(a["target"])
            return [tag, f"_guard({_pyq(n.id)}, f_cut_box, {_pyq(a['target'])}, {_pyq(tool)}, {tuple(x.value for x in a['size'])!r}, {tuple(c.value for c in a['center'])!r})"]
        if k == "Cut_Cylinder":
            tool = self.next_tool(a["target"])
            return [tag, f"_guard({_pyq(n.id)}, f_cut_cylinder, {_pyq(a['target'])}, {_pyq(tool)}, {_pyq(a['axis'])}, {a['radius'].value!r}, {a['height'].value!r}, "
                         f"{tuple(c.value for c in a['center'])!r})"]
        if k == "Component_Transform":
            steps = []
            for st in a["steps"]:
                if st["type"] == "hinge":
                    steps.append(("hinge", st["axis"], st["angle"].value, *(v.value for v in st["pivot"])))
                elif st["type"] == "rotate":
                    steps.append(("rotate", st["rx"].value, st["ry"].value, st["rz"].value))
                else:
                    steps.append(("translate", st["x"].value, st["y"].value, st["z"].value))
            return [tag, f"# (the placement through the assemblies is applied as evaluated numbers)", f"_guard({_pyq(n.id)}, _xform, {_pyq(a['body'])}, {steps!r})"]
        return [tag, f"# UNSUPPORTED: {'; '.join(n.notes)}"]


def export_freecad_macro(ir: IR) -> str:
    """The FreeCAD macro (Python) for `ir`."""
    return _FC(ir).gen()


GENERATORS = {"nx": export_nxopen_journal, "fusion": export_fusion_script, "solidworks": export_solidworks_macro, "freecad": export_freecad_macro}


def transpile(design, target: str) -> str:
    """One call: the script for `target` ('nx', 'fusion', 'solidworks' or 'freecad') from a design."""
    if target not in GENERATORS:
        raise ValueError(f"Unknown target '{target}'. Choose one of: {', '.join(TARGETS)}.")
    return GENERATORS[target](build_ir(design))


def coverage_report(ir: IR) -> str:
    """Plain-words summary of what the scripts do and do not carry over."""
    cov = ir.coverage()
    lines = [f"{len(ir.params)} parameters, {sum(cov.values())} features: {cov['native']} native, {cov['evaluated']} with evaluated numbers, {cov['unsupported']} unsupported."]
    lines += [f"  - {w}" for w in ir.warnings[:60]]
    if len(ir.warnings) > 60:
        lines.append(f"  - ... and {len(ir.warnings) - 60} more")
    return "\n".join(lines)


def export_bundle(design, folder, basename: str | None = None) -> dict:
    """Write the STEP file and all four scripts, plus a coverage report, into `folder` (and a .zip of them). Returns {name: path}."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    base = basename or design.name
    ir = build_ir(design)
    out = {}
    step = design.export(folder, base, ["step"])["step"]["path"]
    out["step"] = Path(step)
    for t in TARGETS:
        path = folder / TARGET_FILE[t]
        path.write_text(GENERATORS[t](ir), encoding="utf-8")
        out[t] = path
    rep = folder / "COVERAGE.txt"
    rep.write_text(coverage_report(ir) + "\n", encoding="utf-8")
    out["coverage"] = rep
    zpath = folder / f"{base}_parametric_bundle.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for key, p in out.items():
            z.write(p, Path(p).name)
    out["zip"] = zpath
    return out


def main(argv=None) -> int:
    """python -m satcad.parametric_transpiler design.json [--out folder] [--target nx|fusion|solidworks|freecad|all]"""
    import argparse
    import sys
    from .engine import Design, DesignError
    ap = argparse.ArgumentParser(prog="satcad.parametric_transpiler", description="Write native rebuild scripts (NX, Fusion, SolidWorks, FreeCAD) from a satCAD design.")
    ap.add_argument("design", help="a satCAD design (.json)")
    ap.add_argument("--out", default=".", help="folder for the scripts (default: the current folder)")
    ap.add_argument("--target", default="all", choices=list(TARGETS) + ["all"], help="one program, or all four (default)")
    ap.add_argument("--bundle", action="store_true", help="also write the STEP file, a coverage report and a .zip of everything")
    args = ap.parse_args(argv)
    try:
        d = Design("x")
        d.load(args.design)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        if args.bundle:
            paths = export_bundle(d, out)
            for k, v in paths.items():
                print(f"{k:9s} {v}")
            return 0
        ir = build_ir(d)
        for t in (TARGETS if args.target == "all" else (args.target,)):
            path = out / TARGET_FILE[t]
            path.write_text(GENERATORS[t](ir), encoding="utf-8")
            print(f"{t:10s} {path}")
        print(coverage_report(ir))
        return 0
    except (DesignError, ValueError, OSError) as e:
        print(f"Can't transpile: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
