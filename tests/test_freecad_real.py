"""Runs the generated FreeCAD macro in a REAL FreeCAD (headless) and compares the result with satCAD, before and after changing variables.

Skipped unless FreeCAD's command-line program is available: set SATCAD_FREECADCMD to its path (on a Mac with FreeCAD installed:
/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd) or put `freecadcmd` on the PATH. FreeCAD is free: https://www.freecad.org/downloads.php"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from satcad import parametric_transpiler as tp  # noqa: E402

FREECADCMD = os.environ.get("SATCAD_FREECADCMD") or shutil.which("freecadcmd") or shutil.which("FreeCADCmd")
pytestmark = pytest.mark.skipif(not FREECADCMD or not Path(FREECADCMD).exists(), reason="FreeCAD (freecadcmd) is not installed; set SATCAD_FREECADCMD to run this")

DRIVER = '''
import json, os
macro, out, edits = os.environ["MACRO"], os.environ["OUT"], json.loads(os.environ["EDITS"])
ns = {"__name__": "macro"}
exec(compile(open(macro).read(), macro, "exec"), ns)
doc, sheet, OBJ = ns["doc"], ns["sheet"], ns["OBJ"]


def snapshot():
    doc.recompute()
    res = {}
    for name, o in OBJ.items():
        sh = o.Shape
        bb = sh.BoundBox
        res[name] = dict(volume=sh.Volume, bbox=[bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax], valid=sh.isValid(), solids=len(sh.Solids))
    return res


result = dict(skipped=ns["SKIPPED"], before=snapshot())
for alias, value in edits.items():
    sheet.set(sheet.getCellFromAlias(alias), str(value))
result["after"] = snapshot()
json.dump(result, open(out, "w"))
'''


def run_macro(tmp_path, name, design, edits):
    macro = tmp_path / f"{name}.FCMacro"
    macro.write_text(tp.export_freecad_macro(tp.build_ir(design)))
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    out = tmp_path / f"{name}.json"
    env = dict(os.environ, MACRO=str(macro), OUT=str(out), EDITS=json.dumps(edits))
    r = subprocess.run([FREECADCMD, str(driver)], env=env, capture_output=True, text=True, timeout=240, check=False)
    assert out.exists(), r.stdout[-2000:] + r.stderr[-2000:]
    return json.loads(out.read_text())


def differences(design, snapshot):
    bad = []
    for part, r in snapshot.items():
        if part not in design.parts:
            continue                                         # a Draft array is one FreeCAD object for satCAD's source + copies
        p = design.parts[part]
        mn, mx = p.bbox()
        v = p.solid.volume
        if abs(v - r["volume"]) > max(2.0, 1e-4 * v) or any(abs(a - b) > 0.05 for a, b in zip([*mn, *mx], r["bbox"])):
            bad.append(part)
        assert r["valid"], f"{part} is not a valid shape in FreeCAD"
    return bad


def test_demo_part_matches_satcad_in_real_freecad_and_follows_the_spreadsheet(tmp_path):
    import test_parametric_transpiler as T
    d = T.demo_design()
    res = run_macro(tmp_path, "demo", d, {"PLATE_L": 300, "PLATE_W": 200})
    assert res["skipped"] == []                                                              # union, fillet and hole all built
    assert set(res["before"]) == {"block_1"} and differences(d, res["before"]) == []
    d.set_variable("PLATE_L", 300)
    d.set_variable("PLATE_W", 200)
    assert differences(d, res["after"]) == []                                                # the Params spreadsheet drives the model


def test_rich_design_in_real_freecad_arrays_and_hollow_bodies_follow_variables(tmp_path):
    import test_parametric_transpiler as T
    d = T.rich_design(with_port=False)
    edits = {"BUS_W": 520, "TRAYS": 6, "WALL": 12, "ARM_LEN": 360}
    res = run_macro(tmp_path, "rich", d, edits)
    assert {s[0] for s in res["skipped"]} <= {"grid"}                                        # the sketch-extrude grid is not written (reported)
    assert differences(d, res["before"]) == []
    assert res["before"]["trays"]["solids"] == 4 and res["after"]["trays"]["solids"] == 6     # the array count is bound to TRAYS
    for k, v in edits.items():
        d.set_variable(k, v)
    bad = differences(d, res["after"])
    assert set(bad) <= {"arm_plate", "tip_pin"}, bad                                          # bodies placed through an assembly hinge keep their evaluated position
    assert "bus" not in bad                                                                    # the hollow chassis follows BUS_W and WALL
