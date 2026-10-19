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


def selftest() -> int:
    """Used by the packaging build to prove an installation (or a frozen app) works: builds the demo part, tessellates it, creates the window.
    Rendering a frame needs a working OpenGL driver, which a build machine may lack (and a failed OpenGL start can end the process), so it is
    only attempted when SATCAD_SELFTEST_RENDER=1."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    stage = "start"
    try:
        stage = "imports"
        from PySide6.QtWidgets import QApplication
        from . import theme
        from .session import Session
        from .ui_layout import MainWindow
        from .viewport import Viewport, part_edges, part_polydata
        print("imports ok", flush=True)
        stage = "application"
        app = QApplication.instance() or QApplication([])
        theme.apply(app)
        stage = "demo part"
        session = Session()
        build_demo(session)
        part = next(iter(session.design.parts.values()))
        stage = "tessellation"
        poly, edges = part_polydata(part), part_edges(part)
        assert poly.GetNumberOfPolys() > 100 and edges.GetNumberOfLines() > 5, "tessellation produced no geometry"
        assert session.design.check()["ok"], "the demo part failed its own checks"
        print(f"kernel ok: {len(session.design.features)} features, {poly.GetNumberOfPolys()} triangles, {edges.GetNumberOfLines()} edges", flush=True)
        stage = "window"
        win = MainWindow(session, viewport_factory=Viewport)
        win.show()
        print("window ok", flush=True)
        stage = "motion"
        from .motion_expressions import ExpressionEngine
        eng = ExpressionEngine()
        eng.set_equations(["spin(t) = A * sin(2 * pi * f * t)"])
        eng.set_param("A", 2.0)
        assert eng.parameters == ["A", "f"] and abs(eng.velocity("spin", 0.0) - 4.0 * 3.141592653589793) < 1e-6, "motion expression engine"
        print("motion ok", flush=True)
        if os.environ.get("SATCAD_SELFTEST_RENDER") == "1":
            stage = "render"
            img = win.viewport.grab_image()
            print(f"render ok: {img.width()}x{img.height()} frame", flush=True)
        print("SELFTEST OK", flush=True)
        return 0
    except Exception as e:
        print(f"SELFTEST FAILED at {stage}: {type(e).__name__}: {e}", flush=True)
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="satCAD desktop application")
    ap.add_argument("file", nargs="?", help="a saved part (.json) to open")
    ap.add_argument("--demo", action="store_true", help="start with a small example part")
    ap.add_argument("--screenshot", metavar="PNG", help="render the window to a PNG file and quit (for documentation)")
    ap.add_argument("--selftest", action="store_true", help="check that the installation works (kernel, Qt, VTK) and exit 0 or 1")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()

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
