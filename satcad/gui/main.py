"""Entry point: python -m satcad.gui  [part.json]   (or  python main.py)"""
from __future__ import annotations

import argparse
import sys

def build_demo(session) -> None:
    """A small example part so a first-time user sees something: a block with a hole, a rounded edge and a cylinder boss."""
    s = session
    base = s.add("box", length=240, width=160, height=60)
    boss = s.add("cylinder", radius=40, height=60, z=60)
    s.add("boolean", operation="unite", target=base, tool=boss)
    s.add("fillet_edges", target=base, edges="parallel:z", radius=12)
    s.add("hole", target=base, side="+z", standard="M10", hole_type="clearance", depth=0)
    s.select(None)
    s.dirty_changed.emit(False)
    s._dirty = False
    s.message.emit("info", "Demo part loaded. Click a body to select it, or press New to start from scratch.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="satCAD desktop application")
    ap.add_argument("file", nargs="?", help="a saved part (.json) to open")
    ap.add_argument("--demo", action="store_true", help="start with a small example part")
    ap.add_argument("--screenshot", metavar="PNG", help="render the window to a PNG file and quit (for documentation)")
    args = ap.parse_args(argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from . import theme
    from .session import Session
    from .ui_layout import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("satCAD")
    theme.apply(app)
    session = Session()
    win = MainWindow(session)
    if args.file:
        session.open(args.file)
    elif args.demo or args.screenshot:
        build_demo(session)
    win.show()
    if args.screenshot:
        def snap():
            session.select(next(iter(session.design.features), None))       # show the Property Editor with a feature open
            win.viewport.set_view("iso")
            for _ in range(3):
                app.processEvents()                                       # let the panels lay themselves out before the grab
            win.save_screenshot(args.screenshot)
            app.quit()
        QTimer.singleShot(1500, snap)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
