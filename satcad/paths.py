"""Where satellite designs live. Every STEP file and saved design goes in ONE folder, called "sattelite designs" (spelled as chosen):

  * running from a checkout of the satCAD repository: the folder `sattelite designs` at the top of the repository;
  * a packaged app (or any install without the repository): `Documents/satCAD/sattelite designs` in your home folder.

Set the environment variable SATCAD_DESIGNS to use a different folder.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

DESIGNS_FOLDER_NAME = "sattelite designs"


def repo_root() -> Path | None:
    """The satCAD repository folder when running from source, else None (installed or packaged)."""
    if getattr(sys, "frozen", False):
        return None
    root = Path(__file__).resolve().parents[1]
    return root if (root / "satcad").is_dir() and (root / "main.py").exists() else None


def designs_dir(create: bool = True) -> Path:
    """The folder satellite designs are saved in (created on first use)."""
    env = os.environ.get("SATCAD_DESIGNS")
    if env:
        folder = Path(env).expanduser()
    else:
        root = repo_root()
        folder = (root if root else Path.home() / "Documents" / "satCAD") / DESIGNS_FOLDER_NAME
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def in_designs(path) -> Path:
    """A bare file name goes into the designs folder; a path that already names a folder is left alone."""
    p = Path(path)
    return p if p.parent != Path(".") else designs_dir() / p.name
