import json
import re

import pytest

from satcad import Design, DesignError
from satcad.viewer import mesh_data, viewer_html, write_viewer


@pytest.fixture
def design():
    d = Design("view_me")
    d.add("chassis", "bus", length=2000, width=1000, height=800, wall=10, internal_mass_kg=400)
    d.add("panel", "rad", attach_to="bus", side="+y", length=3000, width=1000, thickness=20)
    d.add("cylinder", "tube", radius=100, height=500, axis="x", x=1250)
    d.add("revolve", "tank", points=[[0, 0], [200, 0], [200, 300], [0, 400]], z=700)
    return d


def embedded(html: str) -> dict:
    return json.loads(re.search(r"const D = (\{.*?\});\n", html, re.S).group(1))


def test_mesh_data_is_valid_triangle_soup(design):
    data = mesh_data(design)
    assert [p["name"] for p in data["parts"]] == list(design.parts)
    for p in data["parts"]:
        assert len(p["v"]) % 3 == 0 and len(p["i"]) % 3 == 0 and len(p["i"]) > 0
        assert max(p["i"]) < len(p["v"]) // 3 and min(p["i"]) >= 0
    assert data["mass"] == pytest.approx(design.measure()["total_mass_kg"])
    assert len(data["moments"]) == 3


def test_html_is_self_contained_and_parseable(design, tmp_path):
    path = write_viewer(design, tmp_path / "v.html")
    html = open(path, encoding="utf-8").read()
    data = embedded(html)
    assert data["name"] == "view_me" and len(data["parts"]) == 4
    assert "three.min.js" in html and "OrbitControls" in html and "__DATA__" not in html and "__THREE__" not in html
    assert html.count("</script>") == 3                                    # nothing in the data can end a script early


def test_viewer_rejects_empty_design():
    with pytest.raises(DesignError, match="Nothing to show"):
        viewer_html(Design())


def test_title_is_safe(tmp_path):
    d = Design("x")
    d.name = "<b>evil</b>"
    d.add("box", "b", length=1, width=1, height=1)
    assert "<b>evil" not in viewer_html(d)


def test_hostile_design_names_cannot_inject_script(tmp_path):
    """A design file from someone else could carry a malicious name; opening its viewer must not run it."""
    d = Design("x")
    d.add("box", "b", length=1, width=1, height=1)
    d.name = '</script><script>alert("pwned")</script>&'
    html = viewer_html(d)
    assert "<script>alert" not in html and html.count("</script>") == 3
    assert embedded(html)["name"] == d.name                               # still round-trips as data
