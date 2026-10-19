"""End-to-end: launch the MCP server as a subprocess and talk to it the way Claude does (stdio)."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT_DIR = Path(__file__).resolve().parents[1]


def _run(coro):
    return asyncio.run(coro)


def _text(result) -> str:
    return "".join(c.text for c in result.content if c.type == "text")


def _items(result) -> list:
    """Tools that return a list arrive as one text block per item."""
    return [json.loads(c.text) for c in result.content if c.type == "text"]


def test_full_conversation(tmp_path):
    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=["-m", "satcad.server"], cwd=str(ROOT_DIR),
                                       env={**os.environ, "SATCAD_OUT": str(tmp_path)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                info = await s.initialize()
                assert info.serverInfo.name == "satellite-cad"
                tools = {t.name for t in (await s.list_tools()).tools}
                assert {"design_status", "add_chassis", "add_radiator_panel", "add_radiator_pair", "edit_part", "preview",
                        "measure_design", "check_design", "export_design", "size_radiator", "save_design"} <= tools

                assert "empty" in _text(await s.call_tool("design_new", {"name": "e2e"})).lower()
                r = await s.call_tool("add_chassis", {"name": "chassis", "length": 800, "width": 600, "height": 400,
                                                      "wall": 8, "internal_mass_kg": 250})
                assert not r.isError and json.loads(_text(r))["size_mm"] == [800, 600, 400]
                r = await s.call_tool("add_radiator_pair", {"name_prefix": "rad", "attach_to": "chassis", "axis": "y",
                                                            "length": 1500, "width": 800, "thickness": 20})
                assert not r.isError and "rad_pos" in _text(r) and "rad_neg" in _text(r)

                m = json.loads(_text(await s.call_tool("measure_design", {})))
                assert m["parts"] == 3 and m["overall_size_mm"][1] == 3600

                # parametric edit through the connector: panels follow the chassis
                await s.call_tool("edit_part", {"name": "chassis", "changes": {"width": 1000}})
                parts = {p["name"]: p for p in _items(await s.call_tool("list_parts", {}))}
                assert parts["rad_pos"]["bbox_min_mm"][1] == 500

                # same setting names as add_radiator_panel work in edit_part too
                await s.call_tool("edit_part", {"name": "rad_pos", "changes": {"offset_along_width": 100}})
                parts = {p["name"]: p for p in _items(await s.call_tool("list_parts", {}))}
                assert parts["rad_pos"]["settings"]["offset_span"] == 100

                pv = await s.call_tool("preview", {})
                assert any(c.type == "image" and c.mimeType == "image/png" for c in pv.content)
                assert (tmp_path / "e2e_preview.png").stat().st_size > 20_000

                ex = json.loads(_text(await s.call_tool("export_design", {"formats": ["step", "stl"]})))
                assert Path(ex["step"]["path"]).exists() and Path(ex["stl"]["path"]).exists()

                sized = json.loads(_text(await s.call_tool("size_radiator", {"heat_load_w": 100000, "panel_temp_k": 330})))
                assert 80 < sized["required_panel_area_m2"] < 95

                bad = await s.call_tool("add_chassis", {"name": "chassis", "length": 1, "width": 1, "height": 1})
                assert bad.isError and "already exists" in _text(bad)          # friendly error, server keeps running
                assert not (await s.call_tool("design_status", {})).isError

                await s.call_tool("save_design", {"filename": "keep"})
                await s.call_tool("design_new", {"name": "blank"})
                loaded = json.loads(_text(await s.call_tool("load_design", {"filename": "keep"})))
                assert loaded["name"] == "e2e" and len(loaded["parts"]) == 3

                res = await s.read_resource("satcad://guide")
                assert "Parametric" in res.contents[0].text

    _run(scenario())


def test_autosave_restores_design_on_restart(tmp_path):
    async def session(calls):
        params = StdioServerParameters(command=sys.executable, args=["-m", "satcad.server"], cwd=str(ROOT_DIR),
                                       env={**os.environ, "SATCAD_OUT": str(tmp_path)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                return await calls(s)

    async def first(s):
        await s.call_tool("add_chassis", {"name": "bus", "length": 500, "width": 500, "height": 500})

    async def second(s):
        return json.loads(_text(await s.call_tool("design_status", {})))

    _run(session(first))
    assert _run(session(second))["parts"] == ["bus"]


def test_new_capabilities_over_the_protocol(tmp_path):
    """Formulas as text, variables, extrusions, cutouts, patterns, mirrors, inertia, BOM and the 3D viewer, via MCP."""
    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=["-m", "satcad.server"], cwd=str(ROOT_DIR),
                                       env={**os.environ, "SATCAD_OUT": str(tmp_path)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                assert {"add_extrusion", "add_revolve", "add_cutout", "add_pattern", "add_mirror", "set_variable",
                        "list_variables", "export_bom", "open_3d_viewer"} <= tools

                call = s.call_tool
                await call("design_new", {"name": "v2"})
                assert not (await call("set_variable", {"name": "bay", "value": 500})).isError
                r = await call("add_chassis", {"name": "bus", "length": "=bay * 4", "width": 1500, "height": 1200, "wall": 8,
                                               "internal_mass_kg": 600, "fillet": 20})
                assert not r.isError and json.loads(_text(r))["size_mm"][0] == 2000
                await call("add_radiator_pair", {"name_prefix": "rad", "attach_to": "bus", "axis": "y",
                                                 "length": "=(12000 - bus.width) / 2", "width": 2000, "thickness": 30})
                r = await call("add_extrusion", {"name": "strut", "profile": "circle", "radius": 20, "wall": 3, "depth": 800,
                                                 "plane": "yz", "x": "=bus.xmax", "z": 0, "material": "cfrp"})
                assert not r.isError
                r = await call("add_revolve", {"name": "tank", "points": [[0, 0], [300, 0], [300, 400], [0, 560]],
                                               "z": "=bus.zmax", "material": "titanium_ti6al4v"})
                assert not r.isError
                await call("add_cutout", {"name": "port", "target": "bus", "side": "-x", "shape": "circle", "diameter": 200,
                                          "depth": 8})
                r = await call("add_pattern", {"name": "ring", "source": "strut", "mode": "circular", "count": 3, "axis": "z"})
                assert not r.isError and "ring_3" in _text(r)

                m = json.loads(_text(await call("measure_design", {})))
                assert len(m["inertia_kg_m2"]) == 3 and len(m["principal_moments_kg_m2"]) == 3 and m["total_mass_kg"] > 600

                await call("set_variable", {"name": "bay", "value": 600})               # one number resizes the chassis
                parts = {p["name"]: p for p in _items(await call("list_parts", {}))}
                assert parts["bus"]["size_mm"][0] == 2400 and parts["strut"]["bbox_min_mm"][0] == 1200
                vs = json.loads(_text(await call("list_variables", {})))
                assert vs["bay"]["value"] == 600

                bad = await call("set_variable", {"name": "bay", "value": -5})           # would break the chassis
                assert bad.isError and "greater than 0" in _text(bad)
                assert json.loads(_text(await call("list_variables", {})))["bay"]["value"] == 600   # rolled back

                notnum = await call("add_box", {"name": "q", "length": "abc", "width": 1, "height": 1})
                assert notnum.isError and "not a number" in _text(notnum)

                bom = json.loads(_text(await call("export_bom", {})))
                assert bom["parts"] == len(parts) and (tmp_path / "exports" / "v2_bom.csv").exists()

                viewer = json.loads(_text(await call("open_3d_viewer", {"open_in_browser": False})))
                html = open(viewer["path"], encoding="utf-8").read()
                assert "three.min.js" in html and "strut" in html and "const D = " in html

    _run(scenario())


def test_assemblies_hole_fastener_and_analysis_over_the_protocol(tmp_path):
    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=["-m", "satcad.server"], cwd=str(ROOT_DIR),
                                       env={**os.environ, "SATCAD_OUT": str(tmp_path)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                assert {"add_assembly", "list_assemblies", "list_joints", "add_shell", "add_sweep", "add_loft",
                        "add_fillet_edges", "add_hole", "add_fastener", "analyze_modal", "analyze_static"} <= tools
                call = s.call_tool
                await call("design_new", {"name": "fe"})
                await call("set_variable", {"name": "deploy", "value": 0})
                await call("add_chassis", {"name": "bus", "length": 2000, "width": 1200, "height": 1000, "wall": 10,
                                           "internal_mass_kg": 800})
                await call("add_assembly", {"name": "power"})
                await call("add_assembly", {"name": "wing_a", "parent": "power", "joint_axis": "x", "angle_deg": "=deploy",
                                            "pivot_y": 600, "angle_min": 0, "angle_max": 90})
                r = await call("add_radiator_panel", {"name": "wing", "attach_to": "bus", "side": "+y", "length": 3000, "width": 1500,
                                                      "thickness": 40, "material": "aluminum_honeycomb", "role": "solar",
                                                      "assembly": "wing_a"})
                assert not r.isError and json.loads(_text(r))["assembly"] == "power/wing_a"
                tree = json.loads(_text(await call("list_assemblies", {})))
                assert tree["assemblies"][0]["children"][0]["name"] == "wing_a" and tree["ungrouped"] == ["bus"]
                joints = _items(await call("list_joints", {}))
                assert joints[0]["variable"] == "deploy" and joints[0]["parts"] == ["wing"]

                # detail features through the connector
                await call("add_box", {"name": "plate", "length": 100, "width": 100, "height": 20, "x": 4000})
                assert not (await call("add_hole", {"name": "h", "target": "plate", "side": "+z", "standard": "M6",
                                                    "hole_type": "counterbore", "circle_count": 4, "circle_diameter": 50})).isError
                assert not (await call("add_fastener", {"name": "screw", "size": "M6", "length": 20, "x": 4025, "z": 3.5})).isError
                assert not (await call("add_sweep", {"name": "pipe", "path": [[5000, 0, 0], [5300, 0, 0], [5300, 300, 0]],
                                                     "radius": 10, "bend_radius": 60})).isError
                assert not (await call("add_loft", {"name": "adapter", "sections": [["rect", 0, 200, 200], ["circle", 300, 80]],
                                                    "x": 6000})).isError
                bad = await call("add_sweep", {"name": "p2", "path": [[0, 0, 0], [100, 0, 0], [100, 100, 0]], "radius": 5})
                assert bad.isError and "bend_radius" in _text(bad)
                for n in ("h", "screw", "pipe", "adapter", "plate"):                          # leave a plain bus and wing for the analysis
                    assert not (await call("remove_part", {"name": n})).isError

                modal = json.loads(_text(await call("analyze_modal", {"n_modes": 4, "fixed_part": "bus", "fixed_side": "-z",
                                                                      "mesh_size": 300})))
                assert len(modal["frequencies_hz"]) == 4 and modal["frequencies_hz"] == sorted(modal["frequencies_hz"])
                assert Path(modal["mode_shape_viewer"]).exists() and "BETA" in modal["note"]
                assert modal["model"]["mass_kg"] == pytest.approx(modal["model"]["cad_mass_kg"], rel=0.01)
                stat = json.loads(_text(await call("analyze_static", {"accel_z_g": 5, "fixed_part": "bus", "fixed_side": "-z",
                                                                      "constraint": "face", "mesh_size": 300})))
                assert stat["reaction_force_n"][2] == pytest.approx(stat["model"]["cad_mass_kg"] * 5 * 9.80665, rel=0.01)
                assert stat["worst_plate"] and "BETA" in stat["note"]
                free = json.loads(_text(await call("analyze_modal", {"n_modes": 2, "mesh_size": 400, "show_modes": False})))
                assert free["rigid_body_modes_removed"] == 6 and "mode_shape_viewer" not in free
                no_support = await call("analyze_static", {"accel_z_g": 1})
                assert no_support.isError and "needs a support" in _text(no_support)
                half = await call("analyze_modal", {"fixed_part": "bus"})
                assert half.isError and "both fixed_part and fixed_side" in _text(half)

    _run(scenario())
