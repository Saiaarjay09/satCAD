"""End-to-end: launch the MCP server as a subprocess and talk to it the way Claude does (stdio)."""
import asyncio
import json
import os
import sys
from pathlib import Path

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
