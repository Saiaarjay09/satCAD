"""The shared 'sattelite designs' folder (where all designs are saved) and the integrity of the how-to guide."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

from satcad import paths

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "sattelite designs"


def test_designs_folder_defaults_to_the_repository_and_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.delenv("SATCAD_DESIGNS", raising=False)
    assert paths.repo_root() == ROOT
    assert paths.designs_dir(create=False) == FOLDER
    monkeypatch.setenv("SATCAD_DESIGNS", str(tmp_path / "mine"))
    assert paths.designs_dir() == tmp_path / "mine" and (tmp_path / "mine").is_dir()                  # created on first use


def test_a_packaged_app_saves_under_documents(monkeypatch, tmp_path):
    monkeypatch.delenv("SATCAD_DESIGNS", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert paths.repo_root() is None
    assert paths.designs_dir(create=False) == tmp_path / "Documents" / "satCAD" / "sattelite designs"


def test_bare_file_names_go_to_the_designs_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("SATCAD_DESIGNS", str(tmp_path))
    assert paths.in_designs("alpha.step") == tmp_path / "alpha.step"
    elsewhere = tmp_path / "other" / "alpha.step"
    assert paths.in_designs(elsewhere) == elsewhere                                                     # an explicit folder is respected


def test_the_generator_saves_into_the_designs_folder(tmp_path):
    import os
    env = {**os.environ, "SATCAD_DESIGNS": str(tmp_path / "designs")}
    result = subprocess.run([sys.executable, str(ROOT / "generate_orbital_node_assembly.py"), "--set", "NUM_SERVER_BLADES=2", "--step", "t.step",
                             "--json", "t.json"], capture_output=True, text=True, timeout=600, cwd=tmp_path, env=env)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "designs" / "t.step").stat().st_size > 50_000 and (tmp_path / "designs" / "t.json").exists()
    assert not (tmp_path / "t.step").exists()


def test_app_dialogs_start_in_the_designs_folder():
    source = (ROOT / "satcad" / "gui" / "commands.py").read_text()
    assert "Path.cwd()" not in source and source.count("designs_dir()") >= 3


def test_every_step_file_in_the_repository_lives_in_the_designs_folder():
    skip = {".venv", "dist", "build", ".git", "output", "node_modules", "installer"}
    strays = [p for p in ROOT.rglob("*") if p.suffix.lower() in (".step", ".stp") and not (skip & set(p.relative_to(ROOT).parts))
              and FOLDER not in p.parents]
    assert strays == [], f"STEP files outside 'sattelite designs': {strays}"
    assert FOLDER.is_dir() and (FOLDER / "orbital_node_alpha.step").stat().st_size > 100_000
    assert (FOLDER / "README.md").exists()


def test_the_saved_alpha_design_opens():
    from satcad import Design
    d = Design("x")
    d.load(FOLDER / "orbital_node_alpha.json")
    assert len(d.parts) == 405 and d.variables["NUM_SERVER_BLADES"] == 16


def test_how_to_guide_has_every_picture_it_links():
    text = (ROOT / "docs" / "HOW_TO_USE.md").read_text()
    links = re.findall(r"\]\((howto/[^)]+\.png)\)", text)
    assert len(links) >= 20 and len(set(links)) == len(links)
    missing = [l for l in links if not (ROOT / "docs" / l).exists()]
    assert missing == []
    unused = {p.name for p in (ROOT / "docs" / "howto").glob("*.png")} - {Path(l).name for l in links}
    assert unused == set()                                                                              # no orphan pictures either
    readme = (ROOT / "README.md").read_text()
    assert "docs/HOW_TO_USE.md" in readme


def test_how_to_guide_anchors_exist():
    text = (ROOT / "docs" / "HOW_TO_USE.md").read_text()
    anchors = set()
    for h in re.findall(r"^#{1,3} (.+)$", text, flags=re.M):
        a = re.sub(r"[^\w\s-]", "", h.lower()).strip()
        anchors.add(re.sub(r"\s+", "-", a))
    for target in re.findall(r"\]\(#([^)]+)\)", text):
        assert target in anchors, f"broken in-page link #{target}"
