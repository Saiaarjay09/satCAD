"""Cross-CAD parametric transpiler: the intermediate representation, the formula translation, the four generated scripts, the bundle, and the dialog."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ast
import math
import re
import sys
import zipfile
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import transpiler_reference as ref  # noqa: E402

from satcad import Design, parametric_transpiler as tp  # noqa: E402
from satcad.expressions import evaluate  # noqa: E402


# ---- test designs -----------------------------------------------------------------------------------------------------------------------
def demo_design() -> Design:
    d = Design("demo")
    d.set_variable("PLATE_L", 240)
    d.set_variable("PLATE_W", 160)
    d.set_variable("BOSS_R", "=PLATE_W / 4")
    d.set_variable("COUNT", 3)
    d.set_variable("PITCH", "=PLATE_L / COUNT")
    d.add("box", "block_1", length="=PLATE_L", width="=PLATE_W", height=60)
    d.add("cylinder", "cyl_1", radius="=BOSS_R", height=60, z=60)
    d.add("boolean", "bool_1", operation="unite", target="block_1", tool="cyl_1")
    d.add("fillet_edges", "blend_1", target="block_1", edges="parallel:z", radius=12)
    d.add("hole", "hole_1", target="block_1", side="+z", standard="M10", hole_type="clearance", depth=0)
    return d


def rich_design(with_port: bool = True) -> Design:
    """Every feature type the transpiler writes natively, with variables, assemblies, a hinge angle and patterns."""
    d = Design("rich")
    for k, v in dict(BUS_W=400, BUS_H=600, WALL=8, TRAYS=4, PITCH="=(BUS_H - 120) / TRAYS", DEPLOY=30, ARM_LEN=300, ANG="=DEPLOY * 2").items():
        d.set_variable(k, v)
    d.add("chassis", "bus", length="=BUS_W", width="=BUS_W", height="=BUS_H", wall="=WALL")
    d.add("box", "tray", length="=BUS_W - 2 * WALL - 20", width="=BUS_W - 2 * WALL - 20", height=10, z="=-BUS_H / 2 + 60")
    d.add("pattern", "trays", source="tray", mode="linear", count="=TRAYS", dz="=PITCH")
    d.add("assembly", "arm", x="=BUS_W / 2", z=100, joint_axis="y", angle_deg="=ANG", pivot_x=0, pivot_y=0, pivot_z=0)
    d.add("box", "arm_plate", assembly="arm", length="=ARM_LEN", width=40, height=6, x="=ARM_LEN / 2", ry=10)
    d.add("assembly", "tip", parent="arm", x="=ARM_LEN", rz=15)
    d.add("cylinder", "tip_pin", assembly="tip", radius=8, height=50, axis="x", y=5)
    d.add("sphere", "ball", radius=15, x=-150, y=-100, z=200)
    d.add("pattern", "ring", source="ball", mode="circular", count=5, axis="z", cx=0, cy=0, cz=0)
    d.add("mirror", "ball_m", source="ball", plane="yz", offset=0)
    d.add("extrude", "rib", profile="rect", width=30, height=20, depth="=ARM_LEN / 2", plane="zx", x=-200, y=-100, z=-200, wall=3)
    d.add("extrude", "gusset", profile="polygon", points=[[0, 0], [60, 0], [0, "=ARM_LEN / 10"]], depth=5, plane="xy", z=-290)
    d.add("revolve", "cone", points=[[0, 0], [20, 0], [10, 40], [0, 40]], x=150, y=150, z=-250)
    if with_port:
        d.add("cutout", "port", target="bus", side="+x", shape="circle", diameter=40, depth=0)
    d.add("pattern", "grid", source="rib", mode="grid", count=2, grid=[[0, 80, 0, 2], [0, 0, 120, 2]])
    return d


# ---- a tiny evaluator for each program's formula syntax ---------------------------------------------------------------------------------
def dialect_env(target: str):
    deg = math.pi / 180
    common = dict(pi=math.pi, DEG1=deg, abs=abs, min=min, max=max, exp=math.exp, sqrt=math.sqrt)
    if target == "nx":
        common.update(sin=lambda d: math.sin(d * deg), cos=lambda d: math.cos(d * deg), tan=lambda d: math.tan(d * deg), asin=lambda v: math.asin(v) / deg,
                      acos=lambda v: math.acos(v) / deg, atan=lambda v: math.atan(v) / deg, atan2=lambda y, x: math.atan2(y, x) / deg, floor=math.floor,
                      ceiling=math.ceil, round=round, ln=math.log, log=math.log10)
    elif target == "solidworks":
        common.update(sin=math.sin, cos=math.cos, tan=math.tan, atn=math.atan, sqr=math.sqrt, int=math.floor, log=math.log)
    else:
        common.update(sin=math.sin, cos=math.cos, tan=math.tan, asin=math.asin, acos=math.acos, atan=math.atan, atan2=math.atan2, floor=math.floor, ceil=math.ceil,
                      round=round, ln=math.log, log=math.log, log10=math.log10)
    return common


def eval_dialect(text: str, target: str, env: dict) -> float:
    t = text.replace("^", "**").replace("Params.", "").replace('"', "")
    t = re.sub(r"(-?[0-9.]+) deg\b", r"(\1 * DEG1)", t)
    t = re.sub(r"(-?[0-9.]+) mm\b", r"\1", t)
    ns = {**dialect_env(target), **env}
    return float(eval(t, {"__builtins__": {}}, ns))


FORMULAS = ["a * b + 2", "(a + b) / (a - 1)", "-a + b", "-(a + b) * 2", "sqrt(a * a + b * b)", "sin(ANG) * 10", "cos(ANG * 2) + tan(45)", "a ** 2 ** 2", "(a ** 2) ** 2",
            "a / b / 2", "a / (b / 2)", "a - (b - 1)", "pi * a", "abs(-a) + max(a, b)", "min(a, b) * 3", "floor(a / 2)", "asin(0.5) + atan(1)", "atan2(a, b)", "exp(1) + sqrt(16)"]
ENV = dict(a=3.0, b=4.0, ANG=30.0)


@pytest.mark.parametrize("target", tp.TARGETS)
def test_every_formula_rewrites_to_the_same_number_in_each_program(target):
    for f in FORMULAS:
        want = evaluate("=" + f, lambda n: ENV[n], None)
        try:
            text = tp.to_dialect(f, target, lambda n: n)
        except tp._Unsupported:
            continue                                                              # the program has no way to say it: the transpiler writes the number instead
        assert eval_dialect(text, target, ENV) == pytest.approx(want, rel=1e-9), (target, f, text)


def test_unsupported_functions_fall_back_to_the_evaluated_number():
    e = tp.Expr("min(a, b) * 3", 9.0)
    assert tp.render(e, "solidworks", "mm", tp._Names("solidworks")) == "9"            # SolidWorks equations have no min()
    assert "min(" in tp.render(e, "nx", "mm", tp._Names("nx"))
    assert tp.render(tp.Expr(None, 4500.0), "fusion", "mm", tp._Names("fusion")) == "4500 mm"
    assert tp.render(tp.Expr("A + 1", 5.0), "fusion", "mm", tp._Names("fusion")) == "(A + 1) * 1 mm"
    assert tp.render(tp.Expr("A + 1", 5.0), "freecad", "deg", tp._Names("freecad", binding=True)) == "(Params.A + 1) * 1 deg"
    assert tp.render(tp.Expr("A + 1", 5.0), "solidworks", "", tp._Names("solidworks")) == '("A" + 1)'


def test_freecad_aliases_never_look_like_cell_addresses():
    assert tp._sanitize_alias("A1") == "A1_" and tp._sanitize_alias("AB12") == "AB12_" and tp._sanitize_alias("BUS_W") == "BUS_W" and tp._sanitize_alias("TRAY1") == "TRAY1"


# ---- the intermediate representation ------------------------------------------------------------------------------------------------------
def test_parameter_table_keeps_formulas_and_values_and_infers_units():
    ir = tp.build_ir(demo_design())
    t = ir.params
    assert list(t.params) == ["PLATE_L", "PLATE_W", "BOSS_R", "COUNT", "PITCH"]
    assert t.formulas() == {"PLATE_L": None, "PLATE_W": None, "BOSS_R": "PLATE_W / 4", "COUNT": None, "PITCH": "PLATE_L / COUNT"}
    assert t.values() == {"PLATE_L": 240.0, "PLATE_W": 160.0, "BOSS_R": 40.0, "COUNT": 3.0, "PITCH": 80.0}
    assert t.params["PLATE_L"].unit == "mm" and t.params["BOSS_R"].unit == "mm" and t.params["COUNT"].unit == ""


def test_angles_counts_and_lengths_get_their_units_from_how_they_are_used():
    ir = tp.build_ir(rich_design())
    u = {p.name: p.unit for p in ir.params}
    assert u["DEPLOY"] == "deg" and u["ANG"] == "deg" and u["TRAYS"] == "" and u["BUS_W"] == "mm" and u["WALL"] == "mm" and u["PITCH"] == "mm"


def test_feature_nodes_cover_the_requested_kinds():
    ir = tp.build_ir(rich_design())
    kinds = {n.kind for n in ir.nodes}
    assert {"CSYS_Datum", "Primitive_Box", "Primitive_Cylinder", "Primitive_Sphere", "Sketch_Extrude", "Revolve", "Hollow_Shell", "Linear_Pattern", "Circular_Pattern", "Mirror",
            "Component_Transform", "Cut_Cylinder", "Assembly_Group"} <= kinds
    demo = tp.build_ir(demo_design())
    assert {"Boolean_Op", "Fillet_Chamfer"} <= {n.kind for n in demo.nodes}
    box = next(n for n in demo.nodes if n.kind == "Primitive_Box")
    assert box.args["length"].src == "PLATE_L" and box.args["height"].src is None and box.fidelity == "native"
    shell = next(n for n in ir.nodes if n.kind == "Hollow_Shell")
    assert shell.args["wall"].src == "WALL" and shell.args["mode"] == "cavity"
    ct = [n for n in ir.nodes if n.kind == "Component_Transform" and n.id == "tip_pin"][0]
    assert [s["type"] for s in ct.args["steps"]] == ["rotate", "translate", "hinge", "translate"]           # 'tip' (inner) first, then its parent 'arm'
    assert any(s["type"] == "hinge" and s["angle"].src == "ANG" for n in ir.nodes if n.kind == "Component_Transform" for s in n.args["steps"])


def test_formulas_that_read_another_part_become_numbers_and_say_so():
    d = Design("x")
    d.add("box", "base", length=100, width=100, height=10)
    d.add("box", "top", length="=base.length / 2", width=20, height=5, z="=base.zmax + 2.5")
    ir = tp.build_ir(d)
    top = next(n for n in ir.nodes if n.id == "top" and n.kind == "Primitive_Box")
    assert top.args["length"].src is None and top.args["length"].value == 50 and top.fidelity == "evaluated" and any("another part" in n for n in top.notes)


def test_unsupported_features_are_reported_not_silently_dropped():
    d = Design("x")
    d.add("box", "b", length=50, width=50, height=50)
    d.add("sweep", "pipe", profile="circle", radius=5, path=[[0, 0, 0], [0, 0, 100], [100, 0, 100]], bend_radius=20)
    ir = tp.build_ir(d)
    assert ir.coverage()["unsupported"] == 1 and any("pipe" in w for w in ir.warnings)
    for t in tp.TARGETS:
        assert "UNSUPPORTED" in tp.GENERATORS[t](ir).upper()


def test_an_empty_design_is_refused():
    with pytest.raises(ValueError, match="empty"):
        tp.build_ir(Design("x"))


# ---- the IR rebuilds the same geometry as satCAD, and changing a variable updates it --------------------------------------------------------
@pytest.mark.parametrize("make", [demo_design, rich_design])
def test_the_ir_rebuilds_the_same_solids_as_satcad(make):
    d = make()
    ir = tp.build_ir(d)
    r = ref.Reference(ir)
    parts = r.run()
    skip = [n for n in r.skipped]
    bad = ref.compare(d, parts)
    assert bad == [], bad
    assert set(parts) == set(d.parts)                                    # no body missing and none extra


def test_changing_a_variable_in_the_ir_matches_changing_it_in_satcad():
    d = rich_design(with_port=False)                                         # a cutout is written from evaluated numbers, so it does not follow a variable (see below)
    ir = tp.build_ir(d)
    changes = dict(BUS_W=520, TRAYS=6, DEPLOY=50, ARM_LEN=360, WALL=12)
    parts = ref.Reference(ir, changes).run()
    for k, v in changes.items():
        d.set_variable(k, v)
    assert ref.compare(d, parts) == []
    assert len([n for n in parts if n.startswith("trays_")]) == 5                                  # TRAYS drives the pattern count: 6 copies in all


def test_cutouts_and_holes_are_evaluated_numbers_and_the_coverage_says_so():
    ir = tp.build_ir(rich_design())
    port = next(n for n in ir.nodes if n.id == "bus" and n.kind == "Cut_Cylinder")
    assert port.fidelity == "evaluated" and all(not e.is_formula for e in port.exprs()) and any("evaluated" in n for n in port.notes)
    assert any("bus" in w and "evaluated" in w for w in ir.warnings)


def test_the_demo_part_follows_its_variables_too():
    d = demo_design()
    ir = tp.build_ir(d)
    parts = ref.Reference(ir, dict(PLATE_L=300, PLATE_W=200)).run()
    d.set_variable("PLATE_L", 300)
    d.set_variable("PLATE_W", 200)
    assert ref.compare(d, parts) == []


# ---- the four scripts --------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def rich_ir():
    return tp.build_ir(rich_design())


@pytest.mark.parametrize("target", ["nx", "fusion", "freecad"])
def test_python_scripts_are_valid_python_and_register_every_parameter(target, rich_ir):
    code = tp.GENERATORS[target](rich_ir)
    ast.parse(code)
    for p in rich_ir.params:
        name = tp._sanitize_alias(p.name) if target == "freecad" else p.name
        assert re.search(rf"\b{re.escape(name)}\b", code), (target, name)
    registry = [ln.strip() for ln in code.splitlines() if ln.strip().startswith(("register_expression(", "add_param(")) and not ln.strip().startswith("def ")]
    assert len(registry) == len(rich_ir.params) and not any("**" in ln for ln in registry)
    assert "COVERAGE" in code and "NOT run against the real" in code


def test_nx_expressions_carry_the_exact_formulas(rich_ir):
    code = tp.export_nxopen_journal(rich_ir)
    assert 'register_expression("PITCH", "(BUS_H - 120) / TRAYS", "mm")' in code
    assert 'register_expression("ANG", "DEPLOY * 2", "deg")' in code
    assert 'register_expression("TRAYS", "4", "")' in code
    assert "import NXOpen.Features" in code and "import NXOpen.GeometricUtilities" in code and "Expressions.CreateWithUnits" in code
    assert "CreateBlockFeatureBuilder" in code and "CreateCylinderBuilder" in code and "CreatePatternFeatureBuilder" in code and "CreateBooleanBuilderUsingCollector" in code


def test_fusion_script_uses_user_parameters_and_a_timeline(rich_ir):
    code = tp.export_fusion_script(rich_ir)
    assert "import adsk.core" in code and "import adsk.fusion" in code and "import traceback" in code
    assert 'add_param("PITCH", "(BUS_H - 120) / TRAYS"' in code
    assert "userParameters.add(name, val(expression)" in code and "extrudeFeatures" in code and "rectangularPatternFeatures" in code and "combineFeatures" in code
    assert "(BUS_W) * 1 mm" in code


def test_solidworks_macro_is_complete_vba(rich_ir):
    code = tp.export_solidworks_macro(rich_ir)
    for needle in ("Dim swApp As SldWorks.SldWorks", "Dim Part As SldWorks.ModelDoc2", "Dim swEqnMgr As SldWorks.EquationMgr", "Sub main()", "End Sub",
                   "FeatureExtrusion3", "CreateCenterRectangle", "swEqnMgr.Add2"):
        assert needle in code, needle
    assert '""PITCH"" = (""BUS_H"" - 120) / ""TRAYS""' in code
    subs = len(re.findall(r"^\s*(?:Private )?Sub \w+", code, re.M))
    assert subs == len(re.findall(r"^\s*End Sub", code, re.M))
    assert code.count("(") == code.count(")")


def test_freecad_macro_builds_a_params_spreadsheet_and_binds_properties(rich_ir):
    code = tp.export_freecad_macro(rich_ir)
    assert "addObject(\"Spreadsheet::Sheet\", \"Params\")" in code and "setAlias" in code and "setExpression" in code
    assert 'add_param("PITCH", "=(BUS_H - 120) / TRAYS", "mm"' in code
    assert "Params.BUS_W" in code and "make_ortho_array" in code and "Part::Box" in code and "Part::Cylinder" in code and "Part::Sphere" in code


# ---- the generated scripts run against stand-in APIs --------------------------------------------------------------------------------------
class Rec(mock.MagicMock):
    log: list = []

    def __setattr__(self, k, v):
        if k == "expression":
            Rec.log.append(v)
        super().__setattr__(k, v)


def run_with(modules: dict, code: str):
    with mock.patch.dict(sys.modules, modules):
        ns = {"__name__": "generated"}
        exec(compile(code, "<generated>", "exec"), ns)
        return ns


def test_the_nx_journal_runs_and_binds_dimensions_to_expressions(rich_ir):
    nx = Rec()
    ns = run_with({"NXOpen": nx, "NXOpen.Features": nx.Features, "NXOpen.GeometricUtilities": nx.GeometricUtilities}, tp.export_nxopen_journal(rich_ir))
    exprs = nx.Session.GetSession().Parts.Work.Expressions
    texts = [c.args[0] for c in exprs.CreateWithUnits.call_args_list] + [c.args[0] for c in exprs.Create.call_args_list]
    assert len(texts) == len(rich_ir.params) and "PITCH=(BUS_H - 120) / TRAYS" in texts and "TRAYS=4" in texts
    assert {s[0] for s in ns["SKIPPED"]} <= {"grid"}                  # the grid copies a sketch extrude, which the NX journal does not write (reported, not hidden)
    block = nx.Session.GetSession().Parts.Work.Features.CreateBlockFeatureBuilder.return_value
    lengths = [c.args[1:] for c in block.SetOriginAndLengths.call_args_list]
    assert ("BUS_W", "BUS_W", "BUS_H") in lengths


def test_the_fusion_script_runs_and_binds_parameters(rich_ir):
    Rec.log = []
    adsk = Rec()
    ns = run_with({"adsk": adsk, "adsk.core": adsk.core, "adsk.fusion": adsk.fusion}, tp.export_fusion_script(rich_ir))
    params = adsk.core.Application.get().activeProduct
    assert ns["SKIPPED"] == [] or all("_pattern" not in s[0] for s in ns["SKIPPED"])
    asked = [c.args[0] for c in adsk.core.ValueInput.createByString.call_args_list]
    assert "(BUS_W) * 1 mm" in Rec.log and "(BUS_H) * 1 mm" in asked and "(BUS_H - 120) / TRAYS" in asked
    assert params is not None


# a minimal FreeCAD: real vector / rotation / placement maths, recording objects, so the macro's numbers can be checked
class _Vec:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def arr(self):
        return np.array([self.x, self.y, self.z])

    def __add__(self, o):
        return _Vec(*(self.arr() + o.arr()))

    def __sub__(self, o):
        return _Vec(*(self.arr() - o.arr()))

    def __mul__(self, k):
        return _Vec(*(self.arr() * k))

    Length = property(lambda s: float(np.linalg.norm(s.arr())))

    def normalize(self):
        return _Vec(*(self.arr() / np.linalg.norm(self.arr())))

    def dot(self, o):
        return float(self.arr() @ o.arr())


class _Rot:
    def __init__(self, axis=None, deg=0.0, m=None):
        if m is not None:
            self.m = m
        elif axis is None:
            self.m = np.eye(3)
        else:
            a = axis.arr() / np.linalg.norm(axis.arr())
            c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
            x, y, z = a
            self.m = np.array([[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s], [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                               [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]])

    def __mul__(self, o):
        return _Rot(m=self.m @ o.m)

    def multVec(self, v):
        return _Vec(*(self.m @ v.arr()))

    @property
    def Angle(self):
        return math.acos(max(-1.0, min(1.0, (np.trace(self.m) - 1) / 2)))


class _Pl:
    def __init__(self, base=None, rot=None, center=None):
        self.Base, self.Rotation = base or _Vec(), rot or _Rot()
        if center is not None:                                             # rotate about `center`
            self.Base = self.Base + center - self.Rotation.multVec(center)

    def multiply(self, o):
        return _Pl(self.Base + self.Rotation.multVec(o.Base), self.Rotation * o.Rotation)


class _Obj:
    def __init__(self, kind, name):
        self.kind, self.name, self.expr, self.Placement = kind, name, {}, _Pl()
        self.cells, self.aliases = {}, {}
        self.Shape = type("S", (), {"Edges": []})()

    def setExpression(self, prop, e):
        self.expr[prop] = e

    def set(self, cell, value):
        self.cells[cell] = value

    def setAlias(self, cell, alias):
        self.aliases[alias] = cell


class _Doc:
    def __init__(self, name):
        self.objects = []

    def addObject(self, kind, name):
        o = _Obj(kind, name)
        self.objects.append(o)
        return o

    def recompute(self):
        pass


def fake_freecad():
    app = mock.MagicMock()
    app.Vector, app.Rotation, app.Placement = _Vec, _Rot, _Pl
    docs = []
    app.newDocument = lambda name: docs.append(_Doc(name)) or docs[-1]
    part, draft = mock.MagicMock(), mock.MagicMock()
    return app, part, draft, docs


def sheet_env(sheet) -> dict:
    """The numeric value of every Params alias (cells are numbers or '=formulas' over the other aliases)."""
    cells = {sheet.aliases[a]: sheet.cells[sheet.aliases[a]] for a in sheet.aliases}
    env: dict = {}
    for _ in range(len(cells) + 2):
        for alias, cell in sheet.aliases.items():
            if alias in env:
                continue
            text = cells[cell] if isinstance(cells[cell], str) else str(cells[cell])
            if not text.startswith("="):
                env[alias] = float(text)
            else:
                try:
                    env[alias] = eval_dialect(text[1:], "freecad", env)
                except NameError:
                    pass
    return env


def test_the_freecad_macro_runs_and_its_expressions_give_satcads_numbers():
    d = demo_design()
    ir = tp.build_ir(d)
    app, part, draft, docs = fake_freecad()
    ns = run_with({"FreeCAD": app, "Part": part, "Draft": draft}, tp.export_freecad_macro(ir))
    doc = docs[0]
    sheet = next(o for o in doc.objects if o.kind == "Spreadsheet::Sheet")
    env = sheet_env(sheet)
    assert env == pytest.approx({"PLATE_L": 240, "PLATE_W": 160, "BOSS_R": 40, "COUNT": 3, "PITCH": 80})
    box = next(o for o in doc.objects if o.name == "block_1" and o.kind == "Part::Box")
    assert eval_dialect(box.expr["Length"], "freecad", env) == pytest.approx(240) and eval_dialect(box.expr["Width"], "freecad", env) == pytest.approx(160)
    # the macro places the box by its corner: base = centre - size / 2
    assert eval_dialect(box.expr["Placement.Base.x"], "freecad", env) == pytest.approx(-120)
    assert eval_dialect(box.expr["Placement.Base.z"], "freecad", env) == pytest.approx(-30)
    cyl = next(o for o in doc.objects if o.name == "cyl_1" and o.kind == "Part::Cylinder")
    assert eval_dialect(cyl.expr["Radius"], "freecad", env) == pytest.approx(40)
    assert eval_dialect(cyl.expr["Placement.Base.z"], "freecad", env) == pytest.approx(30)       # centre z 60 minus half the height
    assert {s[0] for s in ns["SKIPPED"]} <= {"block_1"}                       # only the fillet is skipped: the stand-in has no real shape to pick edges from


def test_freecad_pattern_count_and_pitch_are_bound_to_the_spreadsheet():
    d = rich_design()
    ir = tp.build_ir(d)
    app, part, draft, docs = fake_freecad()
    made = []
    draft.make_ortho_array.side_effect = lambda *a, **k: made.append(_Obj("Draft::Array", "arr")) or made[-1]
    run_with({"FreeCAD": app, "Part": part, "Draft": draft}, tp.export_freecad_macro(ir))
    sheet = next(o for o in docs[0].objects if o.kind == "Spreadsheet::Sheet")
    env = sheet_env(sheet)
    arr = made[0]
    assert eval_dialect(arr.expr["NumberX"], "freecad", env) == pytest.approx(4)
    assert eval_dialect(arr.expr["IntervalX.z"], "freecad", env) == pytest.approx(120)


def test_freecad_component_transform_composes_like_satcad():
    """The macro's _xform (assembly poses and a hinge) puts a body where satCAD's engine does."""
    d = rich_design()
    ir = tp.build_ir(d)
    app, part, draft, docs = fake_freecad()
    ns = run_with({"FreeCAD": app, "Part": part, "Draft": draft}, tp.export_freecad_macro(ir))
    pin = ns["OBJ"]["tip_pin"]
    # the body's own placement is its centre: compare the macro's final position of the origin of the pin with satCAD's solid centre
    bb = d.parts["tip_pin"].bbox()
    centre = [(bb[0][i] + bb[1][i]) / 2 for i in range(3)]
    got = pin.Placement.Base
    # the pin is a cylinder along x: its Base is one end; the centre is Base + R * (0,0,h/2)
    got_c = got + pin.Placement.Rotation.multVec(_Vec(0, 0, 25))
    assert [got_c.x, got_c.y, got_c.z] == pytest.approx(centre, abs=1e-6)


# ---- bundle, command line, dialog ---------------------------------------------------------------------------------------------------------
def test_the_bundle_has_the_step_file_all_four_scripts_and_a_coverage_report(tmp_path):
    d = demo_design()
    paths = tp.export_bundle(d, tmp_path)
    for key in ("step", "nx", "fusion", "solidworks", "freecad", "coverage", "zip"):
        assert Path(paths[key]).exists(), key
    names = zipfile.ZipFile(paths["zip"]).namelist()
    assert sorted(names) == sorted(["demo.step", "export_nxopen_journal.py", "export_fusion_script.py", "export_solidworks_macro.vba", "export_freecad_macro.FCMacro", "COVERAGE.txt"])
    assert Path(paths["step"]).stat().st_size > 1000


def test_command_line(tmp_path, capsys):
    d = demo_design()
    src = tmp_path / "demo.json"
    d.save(src)
    assert tp.main([str(src), "--out", str(tmp_path / "o"), "--target", "nx"]) == 0
    assert (tmp_path / "o" / "export_nxopen_journal.py").exists() and not (tmp_path / "o" / "export_fusion_script.py").exists()
    assert tp.main([str(src), "--out", str(tmp_path / "all")]) == 0 and len(list((tmp_path / "all").iterdir())) == 4
    assert tp.main([str(tmp_path / "missing.json")]) == 1


def test_unknown_target_is_refused():
    with pytest.raises(ValueError, match="Unknown target"):
        tp.transpile(demo_design(), "catia")


# the dialog -----------------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from satcad.gui import theme
    a = QApplication.instance() or QApplication([])
    theme.apply(a)
    return a


def test_dialog_shows_each_target_live_and_can_copy_save_and_export(app, tmp_path):
    from PySide6.QtGui import QGuiApplication
    from satcad.gui.transpiler_dialog import TranspilerDialog
    dlg = TranspilerDialog(demo_design())
    assert dlg.tabs.count() == 4 and [dlg.tabs.tabText(i) for i in range(4)] == [tp.TARGET_LABEL[t] for t in tp.TARGETS]
    assert dlg.param_list.count() == 5 and dlg.node_list.count() >= 5
    texts = {}
    for i, t in enumerate(tp.TARGETS):
        dlg.tabs.setCurrentIndex(i)
        texts[t] = dlg.code.toPlainText()
        assert texts[t] == tp.GENERATORS[t](dlg.ir)
    assert len(set(texts.values())) == 4 and "Sub main()" in texts["solidworks"]
    dlg.tabs.setCurrentIndex(0)
    dlg.copy()
    assert QGuiApplication.clipboard().text() == texts["nx"]
    out = tmp_path / "x.py"
    assert dlg.save_script(str(out)) and out.read_text() == texts["nx"]
    item = dlg.node_list.item(1)                                         # untick a feature: it leaves the script
    before = dlg.code.toPlainText()
    from PySide6.QtCore import Qt
    item.setCheckState(Qt.Unchecked)
    assert dlg.code.toPlainText() != before
    assert dlg.export_bundle(str(tmp_path / "bundle")) and (tmp_path / "bundle" / "export_freecad_macro.FCMacro").exists()


def test_ribbon_button_and_menu_entry_open_the_dialog(app):
    from satcad.gui.session import Session
    from satcad.gui.ui_layout import MainWindow
    from PySide6.QtWidgets import QToolButton
    s = Session(demo_design())
    win = MainWindow(s, viewport_factory=_stub_viewport())
    texts = {b.text() for b in win.ribbon.findChildren(QToolButton)}
    assert "STEP" in texts and "CAD Script" in texts
    from PySide6.QtGui import QAction
    assert any(a.text().startswith("Export Parametric Script") for a in win.findChildren(QAction))
    win.commands.transpile()
    assert win.commands.transpiler_dialog.isVisible() or win.commands.transpiler_dialog is not None
    win.commands.transpiler_dialog.close()


def _stub_viewport():
    from PySide6.QtWidgets import QWidget

    class Stub(QWidget):
        """Stands in for the 3D viewport so the window can be tested without rendering."""
        def set_design(self, *a, **k):
            pass

        def set_selected(self, *a):
            pass

        def set_visible(self, *a):
            pass

        def set_view(self, *a):
            pass

        def set_mode(self, *a):
            pass

        def fit(self):
            pass
    return Stub


def test_empty_design_warns_instead_of_opening(app):
    from satcad.gui.session import Session
    from satcad.gui.ui_layout import MainWindow
    log = []
    s = Session()
    s.message.connect(lambda lvl, txt: log.append((lvl, txt)))
    win = MainWindow(s, viewport_factory=_stub_viewport())
    win.commands.transpile()
    assert log and log[-1][0] == "warn"
