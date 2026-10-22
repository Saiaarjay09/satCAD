"""A small, independent interpreter for the transpiler's intermediate representation, used only by the tests.

It rebuilds the solids from the IR (nothing from the satCAD feature code) with build123d, evaluating every formula from the parameter table, so a test can compare
its result with satCAD's own rebuild after changing a variable. It proves the IR carries the right expressions, transforms and operation order; the vendor API calls in
the generated scripts are not exercised by it."""
from __future__ import annotations

import math

from build123d import Axis, Box, Circle, Cylinder, Location, Plane, Polygon, Pos, Rectangle, Sphere, extrude, mirror, revolve
from build123d import fillet as bd_fillet

from satcad import parametric_transpiler as tp
from satcad.expressions import FUNCTIONS, CONSTANTS, evaluate


def param_values(ir, overrides=None) -> dict:
    """Evaluate the parameter table from its formulas (with optional overrides of plain-number variables)."""
    overrides = overrides or {}
    values: dict = {}
    pending = dict(ir.params.params)
    for _ in range(len(pending) + 2):
        for name, p in list(pending.items()):
            if name in overrides:
                values[name] = float(overrides[name])
                pending.pop(name)
            elif not p.expr.is_formula:
                values[name] = p.value
                pending.pop(name)
            elif all(n in values for n in p.expr.names()):
                values[name] = float(evaluate("=" + p.expr.src, lambda n: values[n], None))
                pending.pop(name)
    assert not pending, f"unresolved parameters {list(pending)}"
    return values


class Reference:
    def __init__(self, ir, overrides=None):
        self.ir, self.values = ir, param_values(ir, overrides)
        self.parts: dict = {}
        self.skipped: list = []

    def v(self, e) -> float:
        if isinstance(e, (int, float)):
            return float(e)
        if not e.is_formula:
            return e.value
        return float(evaluate("=" + e.src, lambda n: self.values[n], None))

    # transforms: satCAD's Rot(rx, ry, rz) is Rx*Ry*Rz: a vector turns about fixed Z first, then Y, then X
    def place(self, solid, a):
        s = solid.rotate(Axis.Z, self.v(a["rz"])).rotate(Axis.Y, self.v(a["ry"])).rotate(Axis.X, self.v(a["rx"]))
        return Pos(self.v(a["x"]), self.v(a["y"]), self.v(a["z"])) * s

    def run(self) -> dict:
        for n in self.ir.nodes:
            getattr(self, "n_" + n.kind, self.n_skip)(n)
        return self.parts

    def n_skip(self, n):
        if n.kind not in ("CSYS_Datum", "Assembly_Group"):
            self.skipped.append(n.id)

    n_Unsupported = n_skip

    def n_Primitive_Box(self, n):
        a = n.args
        self.parts[n.id] = self.place(Box(self.v(a["length"]), self.v(a["width"]), self.v(a["height"])), a)

    def n_Primitive_Cylinder(self, n):
        a = n.args
        s = Cylinder(self.v(a["radius"]), self.v(a["height"]))
        s = {"x": s.rotate(Axis.Y, 90), "y": s.rotate(Axis.X, -90), "z": s}[a["axis"]]
        self.parts[n.id] = self.place(s, a)

    def n_Primitive_Sphere(self, n):
        self.parts[n.id] = self.place(Sphere(self.v(n.args["radius"])), n.args)

    def n_Sketch_Extrude(self, n):
        a = n.args
        if a["profile"] == "rect":
            sk = Rectangle(self.v(a["width"]), self.v(a["height"]))
        elif a["profile"] == "circle":
            sk = Circle(self.v(a["radius"]))
        else:
            sk = Polygon(*[(self.v(u), self.v(w)) for u, w in a["points"]], align=None)
        plane = {"xy": Plane.XY, "yz": Plane.YZ, "zx": Plane.ZX}[a["plane"]]
        solid = extrude(plane * sk, amount=self.v(a["depth"]), both=a["both_sides"])
        solid = self.place(solid, a)
        if n.op == "Cut":
            self.parts[a["target"]] = self.parts[a["target"]] - solid
        else:
            self.parts[n.id] = solid

    def n_Revolve(self, n):
        a = n.args
        face = Plane.XZ * Polygon(*[(self.v(u), self.v(w)) for u, w in a["points"]], align=None)
        self.parts[n.id] = self.place(revolve(face, axis=Axis.Z, revolution_arc=self.v(a["angle"])), a)

    def n_Hollow_Shell(self, n):
        a = n.args
        if a["mode"] != "cavity":
            self.skipped.append(n.id)
            return
        w = self.v(a["wall"])
        inner = self.place(Box(self.v(a["length"]) - 2 * w, self.v(a["width"]) - 2 * w, self.v(a["height"]) - 2 * w), a)
        self.parts[a["target"]] = self.parts[a["target"]] - inner

    def n_Boolean_Op(self, n):
        a = n.args
        t = self.parts[a["target"]]
        for tool in a["tools"]:
            tool_s = self.parts.pop(tool)
            t = {"unite": t + tool_s, "subtract": t - tool_s, "intersect": t & tool_s}[a["operation"]]
        self.parts[a["target"]] = t

    def n_Fillet_Chamfer(self, n):
        a = n.args
        solid = self.parts[a["target"]]
        sel = a["edges"]
        edges = solid.edges() if sel == "all" else solid.edges().filter_by({"x": Axis.X, "y": Axis.Y, "z": Axis.Z}[sel[9:]])
        r = self.v(a["radius"])
        if r > 0:
            self.parts[a["target"]] = bd_fillet(edges, radius=r)
        else:
            from build123d import chamfer as bd_chamfer
            self.parts[a["target"]] = bd_chamfer(edges, length=self.v(a["chamfer"]))

    def n_Cut_Box(self, n):
        a = n.args
        size = [self.v(x) for x in a["size"]]
        c = [self.v(x) for x in a["center"]]
        self.parts[a["target"]] = self.parts[a["target"]] - (Pos(*c) * Box(*size))

    def n_Cut_Cylinder(self, n):
        a = n.args
        s = Cylinder(self.v(a["radius"]), self.v(a["height"]))
        s = {"x": s.rotate(Axis.Y, 90), "y": s.rotate(Axis.X, -90), "z": s}[a["axis"]]
        self.parts[a["target"]] = self.parts[a["target"]] - (Pos(*[self.v(x) for x in a["center"]]) * s)

    def n_Component_Transform(self, n):
        a = n.args
        s = self.parts[a["body"]]
        for st in a["steps"]:
            if st["type"] == "hinge":
                piv = [self.v(x) for x in st["pivot"]]
                s = s.rotate(Axis(Pos(*piv).position, {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[st["axis"]]), self.v(st["angle"]))
            elif st["type"] == "rotate":
                s = s.rotate(Axis.Z, self.v(st["rz"])).rotate(Axis.Y, self.v(st["ry"])).rotate(Axis.X, self.v(st["rx"]))
            else:
                s = Pos(self.v(st["x"]), self.v(st["y"]), self.v(st["z"])) * s
        self.parts[a["body"]] = s

    def n_Linear_Pattern(self, n):
        a = n.args
        src = self.parts[a["source"]]
        if "grid" in a:
            import itertools
            steps = [([self.v(x) for x in st["pitch"]], int(round(self.v(st["count"])))) for st in a["grid"]]
            k = 0
            for idx in itertools.product(*[range(c) for _, c in reversed(steps)]):
                idx = tuple(reversed(idx))
                off = [sum(i * st[0][ax] for i, st in zip(idx, steps)) for ax in range(3)]
                k += 1
                if k == 1:
                    continue
                self.parts[f"{n.id}_{k}"] = Pos(*off) * src
            return
        count = int(round(self.v(a["count"])))
        pitch = [self.v(x) for x in a["pitch"]]
        for i in range(1, count):
            self.parts[f"{n.id}_{i + 1}"] = Pos(*[i * p for p in pitch]) * src

    def n_Circular_Pattern(self, n):
        a = n.args
        src = self.parts[a["source"]]
        count = int(round(self.v(a["count"])))
        total = self.v(a["total_angle"])
        step = total / count if total == 360 else total / (count - 1)
        c = [self.v(x) for x in a["center"]]
        axis = Axis(Pos(*c).position, {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[a["axis"]])
        for i in range(1, count):
            self.parts[f"{n.id}_{i + 1}"] = src.rotate(axis, step * i)

    def n_Mirror(self, n):
        a = n.args
        plane = {"xy": Plane.XY, "yz": Plane.YZ, "zx": Plane.ZX}[a["plane"]].offset(self.v(a["offset"]))
        self.parts[n.id] = mirror(self.parts[a["source"]], about=plane)


def compare(design, ref_parts: dict, skip=()):
    """[(name, reason)] for every part both sides have whose volume or bounding box differ."""
    bad = []
    for name, solid in ref_parts.items():
        if name in skip or name not in design.parts:
            bad.append((name, "missing in satCAD" if name not in design.parts else "skipped"))
            continue
        p = design.parts[name]
        v1, v2 = float(solid.volume), float(p.solid.volume)
        if abs(v1 - v2) > 1e-6 * max(1.0, abs(v2)) + 0.05:
            bad.append((name, f"volume {v1:.3f} vs {v2:.3f}"))
            continue
        b1 = solid.bounding_box()
        mn, mx = p.bbox()
        got = [b1.min.X, b1.min.Y, b1.min.Z, b1.max.X, b1.max.Y, b1.max.Z]
        want = [*mn, *mx]
        if any(abs(a - b) > 0.01 for a, b in zip(got, want)):
            bad.append((name, f"bbox {[round(x, 2) for x in got]} vs {[round(x, 2) for x in want]}"))
    return bad
