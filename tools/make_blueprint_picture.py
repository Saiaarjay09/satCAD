"""Draws the example sheet shown in the README: a small bracket (no satellite), third-angle views, dimensions tied to its variables, title block and bill of materials.

    python tools/make_blueprint_picture.py          # writes docs/blueprint_sheet.png
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from satcad import Design, drawing_studio as ds  # noqa: E402
from satcad.gui.blueprint_view import BlueprintScene  # noqa: E402


def bracket() -> Design:
    d = Design("mounting_bracket")
    for name, value in dict(BASE_L=240, BASE_W=160, BASE_T=24, BOSS_R=30, BOSS_H=60, RIB_T=16).items():
        d.set_variable(name, value)
    d.add("box", "base_plate", length="=BASE_L", width="=BASE_W", height="=BASE_T", material="aluminum_6061")
    d.add("cylinder", "boss", radius="=BOSS_R", height="=BOSS_H", z=42, x=-60, material="aluminum_6061")
    d.add("box", "rib", length=120, width="=RIB_T", height=48, x=30, z=36, material="aluminum_6061")
    d.add("boolean", "join_boss", operation="unite", target="base_plate", tool="boss")
    d.add("boolean", "join_rib", operation="unite", target="base_plate", tool="rib")
    d.add("hole", "mount_holes", target="base_plate", side="+z", standard="M10", hole_type="clearance", depth=0, count_u=2, pitch_u=180, offset_v=50)
    d.add("hole", "boss_bore", target="base_plate", side="+z", standard="M12", hole_type="clearance", depth=0, offset_u=-60 - 0, offset_v=0)
    return d


def main() -> None:
    app = QApplication.instance() or QApplication([])
    sheet = ds.build_sheet(bracket(), None, ds.DrawingOptions(author="satCAD example"))
    scene = BlueprintScene()
    scene.load(sheet, "print")
    w, h = sheet.size
    img = QImage(int(w * 5), int(h * 5), QImage.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    scene.render(p, QRectF(0, 0, img.width(), img.height()), QRectF(0, 0, w, h))
    p.end()
    out = ROOT / "docs" / "blueprint_sheet.png"
    img.save(str(out))
    print("wrote", out, f"({sheet.meta['scale']}, {len(sheet.dims)} dimensions)")
    del app


if __name__ == "__main__":
    main()
