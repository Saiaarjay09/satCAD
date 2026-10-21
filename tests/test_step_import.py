"""Opening STEP files: the step_import feature in the engine, the session's Open, and the file dialog's filter."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import sys
from pathlib import Path

import pytest
from build123d import export_step

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from satcad import Design, DesignError  # noqa: E402
from satcad.gui.session import Session  # noqa: E402


@pytest.fixture(scope="module")
def step_file(tmp_path_factory):
    d = Design("src")
    d.add("assembly", "arm")
    d.add("box", "hub", length=100, width=60, height=40, color=[0.9, 0.1, 0.1])
    d.add("cylinder", "rod", radius=10, height=300, x=200, assembly="arm")
    d.add("sphere", "ball", radius=25, z=200)
    path = tmp_path_factory.mktemp("step") / "my part.step"
    export_step(d.compound(), str(path))
    return path


def test_every_solid_becomes_a_part_with_its_label_and_colour(step_file):
    d = Design("x")
    d.add("step_import", "imp", file=str(step_file))
    assert sorted(d.parts) == ["ball", "hub", "rod"]
    lo, hi = d.parts["hub"].bbox()
    assert [round(h - l) for l, h in zip(lo, hi)] == [100, 60, 40]
    assert d.parts["hub"].color == pytest.approx((0.9, 0.1, 0.1), abs=0.01)
    assert d.parts["rod"].volume_mm3 == pytest.approx(3.141592653589793 * 100 * 300, rel=1e-3)
    assert d.measure()["total_mass_kg"] > 0


def test_pose_and_scale_apply_to_the_whole_import(step_file):
    d = Design("x")
    d.add("step_import", "imp", file=str(step_file), x=1000, scale=2)
    lo, hi = d.parts["hub"].bbox()
    assert [round(h - l) for l, h in zip(lo, hi)] == [200, 120, 80]
    assert (lo[0] + hi[0]) / 2 == pytest.approx(1000, abs=1)


def test_the_design_json_round_trips_and_keeps_pointing_at_the_file(step_file, tmp_path):
    d = Design("x")
    d.add("step_import", "imp", file=str(step_file))
    data = json.loads(json.dumps(d.to_json()))
    e = Design("y")
    e.load_data(data)
    assert sorted(e.parts) == ["ball", "hub", "rod"]


def test_clear_messages_for_a_missing_damaged_or_empty_file(tmp_path):
    d = Design("x")
    with pytest.raises(DesignError, match="wasn't found"):
        d.add("step_import", "imp", file=str(tmp_path / "nope.step"))
    bad = tmp_path / "bad.step"
    bad.write_text("this is not a STEP file")
    with pytest.raises(DesignError, match="STEP"):
        d.add("step_import", "imp", file=str(bad))
    with pytest.raises(DesignError, match="scale"):
        d.add("step_import", "imp", file=str(bad), scale=0)
    with pytest.raises(DesignError, match="file"):
        d.add("step_import", "imp", file="  ")
    assert d.parts == {} and "imp" not in d.features


def test_session_opens_a_step_file_as_a_new_unsaved_design(step_file):
    s = Session()
    log = []
    s.message.connect(lambda lvl, txt: log.append((lvl, txt)))
    assert s.open(step_file) is True
    assert s.design.name == "my_part" and sorted(s.design.parts) == ["ball", "hub", "rod"]
    assert s.path is None and s.dirty                                   # Save asks for a .json name
    assert any("Imported my part.step: 3 bodies" in t for _lvl, t in log)


def test_session_reports_a_bad_step_file_and_keeps_the_current_design(tmp_path):
    s = Session()
    s.add("box", length=10, width=10, height=10)
    bad = tmp_path / "broken.stp"
    bad.write_text("nonsense")
    log = []
    s.message.connect(lambda lvl, txt: log.append((lvl, txt)))
    assert s.open(bad) is False
    assert len(s.design.parts) == 1 and log and log[0][0] == "error" and "broken.stp" in log[0][1]


def test_the_open_dialog_offers_step_files():
    src = (ROOT / "satcad" / "gui" / "commands.py").read_text()
    assert "*.json *.step *.stp" in src


def test_a_saved_json_from_a_step_import_reopens(step_file, tmp_path):
    s = Session()
    s.open(step_file)
    out = tmp_path / "saved.json"
    assert s.save(out)
    t = Session()
    assert t.open(out) and sorted(t.design.parts) == ["ball", "hub", "rod"]


def test_an_older_app_hint_is_in_the_error_for_settings_it_does_not_know():
    d = Design("x")
    with pytest.raises(DesignError, match="newer satCAD"):
        d.add("box", "b", length=1, width=1, height=1, brand_new_setting=3)
    with pytest.raises(DesignError, match="newer satCAD"):
        d.add("warp_drive", "w")
