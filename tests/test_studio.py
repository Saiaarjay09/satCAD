"""SatCAD Studio: the local editor server (API, safety checks, open/save round trip, analysis)."""
import json
import re
import threading
import urllib.error
import urllib.request

import pytest

from satcad import studio


@pytest.fixture()
def app():
    state = studio.StudioState("test-token", 0)
    server = studio.LocalServer(("127.0.0.1", 0), studio.make_handler(state))
    state.port = server.server_address[1]
    server.RequestHandlerClass = studio.make_handler(state)         # allowed hosts need the real port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.base = f"http://127.0.0.1:{state.port}"
    yield state
    server.shutdown()
    server.server_close()


def call(app, path, body=None, token="test-token", host=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Studio-Token"] = token
    if host:
        headers["Host"] = host
    req = urllib.request.Request(app.base + path, None if body is None else json.dumps(body).encode(), headers)
    try:
        with urllib.request.urlopen(req) as r:
            data = r.read()
            return r.status, (json.loads(data) if r.headers["Content-Type"].startswith("application/json") else data)
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data)
        except ValueError:
            return e.code, data


def chassis(app, name="bus", **kw):
    settings = dict(length=1000, width=800, height=600, wall=6, internal_mass_kg=100) | kw
    return call(app, "/api/add", {"kind": "chassis", "name": name, "settings": settings})


def test_page_and_offline_libraries_are_served(app):
    code, html = call(app, "/")
    assert code == 200 and b"SatCAD Studio" in html and b"test-token" in html and b"__TOKEN__" not in html
    for lib in ("three.min.js", "OrbitControls.js"):
        code, js = call(app, "/static/" + lib)
        assert code == 200 and len(js) > 10_000
    assert call(app, "/static/../engine.py")[0] == 404


def test_viewer_is_offline_and_empty_design_gets_a_friendly_page(app):
    code, empty = call(app, "/viewer")
    assert code == 200 and b"design is empty" in empty
    chassis(app)
    code, page = call(app, "/viewer")
    assert code == 200 and b"/static/three.min.js" in page and b'src="https://' not in page


def test_rejects_wrong_host_and_missing_token(app):
    assert call(app, "/api/state", host="evil.example.com")[0] == 403         # DNS-rebinding style request
    assert call(app, "/api/new", {"name": "x"}, token=None)[0] == 403
    assert call(app, "/api/new", {"name": "x"}, token="wrong")[0] == 403
    assert call(app, "/api/state")[0] == 200


def test_add_edit_remove_and_variables_follow_formulas(app):
    code, s = chassis(app)
    assert code == 200 and s["measure"]["parts"] == 1
    assert call(app, "/api/variable", {"name": "span", "value": 2000})[0] == 200
    code, s = call(app, "/api/add", {"kind": "panel", "name": "rad", "settings": dict(attach_to="bus", side="+y", length="=span", width=800, thickness=20)})
    assert code == 200 and s["measure"]["overall_size_mm"][1] == pytest.approx(2800)
    call(app, "/api/variable", {"name": "span", "value": 3000})
    code, s = call(app, "/api/edit", {"name": "bus", "changes": {"width": 1000}})
    assert s["measure"]["overall_size_mm"][1] == pytest.approx(4000)
    code, err = call(app, "/api/remove", {"name": "bus"})                      # the panel depends on it
    assert code == 400 and "error" in err
    assert call(app, "/api/remove", {"name": "rad"})[0] == 200
    assert call(app, "/api/remove", {"name": "bus"})[1]["features"] == []


def test_errors_are_plain_english_and_leave_the_design_unchanged(app):
    chassis(app)
    code, err = call(app, "/api/add", {"kind": "panel", "name": "p", "settings": dict(attach_to="ghost", side="+y", length=5, width=5, thickness=1)})
    assert code == 400 and "ghost" in err["error"]
    code, err = call(app, "/api/add", {"kind": "chassis", "name": "bad name!", "settings": {}})
    assert code == 400 and "letters" in err["error"]
    code, err = call(app, "/api/add", {"kind": "teapot", "name": "t", "settings": {}})
    assert code == 400 and "Unknown part type" in err["error"]
    assert [f["name"] for f in call(app, "/api/state")[1]["features"]] == ["bus"]
    assert call(app, "/api/add", {"kind": "chassis"})[0] == 400                 # missing name is a clean 400, not a crash


def test_save_then_open_round_trips_and_keeps_formulas(app):
    chassis(app)
    call(app, "/api/variable", {"name": "span", "value": 1500})
    call(app, "/api/add", {"kind": "panel", "name": "rad", "settings": dict(attach_to="bus", side="+y", length="=span", width=800, thickness=20)})
    code, saved = call(app, "/api/save")
    saved = json.loads(saved) if isinstance(saved, bytes) else saved
    call(app, "/api/new", {"name": "other"})
    assert call(app, "/api/state")[1]["features"] == []
    code, s = call(app, "/api/open", {"data": saved})
    assert code == 200 and s["name"] == "my_design" and [f["name"] for f in s["features"]] == ["bus", "rad"]
    assert s["features"][1]["raw"]["length"] == "=span" and s["variables"]["span"]["value"] == 1500


def test_opening_a_bad_file_keeps_the_current_design(app):
    chassis(app)
    for bad in ({"hello": 1}, {"format": "satcad-2", "name": "x", "features": [{"name": "a", "kind": "box", "params": {}}]}):
        code, err = call(app, "/api/open", {"data": bad})
        assert code == 400 and err["error"]
    assert [f["name"] for f in call(app, "/api/state")[1]["features"]] == ["bus"]
    code, err = call(app, "/api/open", {"data": [1, 2]})
    assert code == 400


def test_opens_the_older_file_format(app):
    old = {"format": "satcad-1", "name": "legacy", "features": [{"name": "b", "kind": "box", "params": {"length": 100, "width": 100, "height": 100}}]}
    code, s = call(app, "/api/open", {"data": old})
    assert code == 200 and s["name"] == "legacy" and s["measure"]["parts"] == 1


def test_names_from_files_are_made_safe(app):
    data = {"format": "satcad-2", "name": "<script>alert(1)</script>", "features": [{"name": "b", "kind": "box", "params": {"length": 10, "width": 10, "height": 10}}]}
    code, s = call(app, "/api/open", {"data": data})
    assert code == 200 and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", s["name"])


def test_exports_are_real_cad_files(app):
    assert call(app, "/api/export?fmt=step")[0] == 400                           # empty design
    chassis(app)
    code, step = call(app, "/api/export?fmt=step")
    assert code == 200 and step.startswith(b"ISO-10303-21")
    code, glb = call(app, "/api/export?fmt=glb")
    assert glb[:4] == b"glTF"
    assert call(app, "/api/export?fmt=exe")[0] == 400


def test_check_and_rename(app):
    chassis(app)
    assert call(app, "/api/check")[1]["ok"] is True
    assert call(app, "/api/rename", {"name": "renamed"})[1]["name"] == "renamed"
    assert call(app, "/api/rename", {"name": "no spaces"})[0] == 400


def test_modal_and_static_analysis(app):
    chassis(app)
    code, r = call(app, "/api/analyze", {"kind": "modal", "n_modes": 3})
    assert code == 200 and len(r["frequencies_hz"]) == 3 and r["rigid_body_modes_removed"] == 6 and r["has_viewer"]
    code, page = call(app, "/modes")
    assert code == 200 and b"/static/three.min.js" in page
    code, r = call(app, "/api/analyze", {"kind": "static", "fixed_part": "bus", "fixed_side": "-z", "constraint": "face", "accel_g": [0, 0, 6]})
    assert code == 200 and r["max_von_mises_mpa"] > 0
    assert r["reaction_n"][2] == pytest.approx(call(app, "/api/state")[1]["measure"]["total_mass_kg"] * 6 * 9.80665, rel=0.01)
    assert call(app, "/api/analyze", {"kind": "static", "accel_g": [0, 0, 6]})[0] == 400           # no support given
    assert call(app, "/api/analyze", {"kind": "modal", "fixed_part": "bus"})[0] == 400              # half a support


def test_analysis_of_an_empty_design_is_refused(app):
    code, err = call(app, "/api/analyze", {"kind": "modal"})
    assert code == 400 and "empty" in err["error"]


def test_schemas_describe_every_part_type(app):
    code, s = call(app, "/api/schemas")
    assert code == 200 and "chassis" in s["kinds"] and s["kinds"]["chassis"]["length"] == "__required__" and "aluminum_6061" in s["materials"]
