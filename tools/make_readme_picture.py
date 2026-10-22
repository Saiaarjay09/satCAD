"""Captures the picture at the top of the README: the satCAD window with the small demo part (a block, a boss, a rounded edge and a hole). Software only, no satellite.

    python tools/make_readme_picture.py          # writes docs/satcad_app.png
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")                                    # a sharp, retina-style picture
os.environ["SATCAD_DESIGNS"] = tempfile.mkdtemp(prefix="satcad_readme_")        # keep the real designs folder untouched
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDockWidget  # noqa: E402

from satcad.gui import theme  # noqa: E402
from satcad.gui.session import Session  # noqa: E402
from satcad.gui.ui_layout import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.apply(app)


def settle(n: int = 6) -> None:
    for _ in range(n):
        app.processEvents()


def main() -> None:
    s = Session()
    win = MainWindow(s)
    win.resize(1480, 900)
    win.show()
    settle()
    docks = {d.objectName(): d for d in win.findChildren(QDockWidget)}
    win.resizeDocks([docks["infoDock"]], [96], Qt.Vertical)
    s.add("box", length=240, width=160, height=60)
    s.add("cylinder", radius=40, height=60, z=60)
    s.add("boolean", operation="unite", target="block_1", tool="cylinder_1")
    s.add("fillet_edges", target="block_1", edges="parallel:z", radius=12)
    s.add("hole", target="block_1", side="+z", standard="M10", hole_type="clearance", depth=0)
    s.select("block_1")
    win.viewport.set_view("iso")
    settle()
    out = ROOT / "docs" / "satcad_app.png"
    win.grab().save(str(out))
    print("wrote", out)
    win.close()


if __name__ == "__main__":
    main()
