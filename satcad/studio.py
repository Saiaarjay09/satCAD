"""
SatCAD Studio: a desktop-style editor for SatCAD designs that runs on your own computer.

It starts a small web server that only your own machine can reach (127.0.0.1), opens your web browser on it, and shows
a parts list, forms for adding and editing parts, variables, a 3D view, checks and analysis. Nothing is uploaded anywhere,
and nothing is saved unless you press Save. Open .json designs (made here, by Claude, or by build_satellite.py) to improve
them, and export STEP / STL / GLB for other CAD programs.

Run:   python -m satcad.studio        (or the packaged SatCAD Studio app)
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import socket
import socketserver
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import materials as _materials
from .engine import NAME_RE, REQ, SCHEMAS, Design, DesignError
from .viewer import mode_viewer_html, viewer_html

__version__ = "0.3"


def _resource_dir() -> Path:
    """Folder holding the static files, both in a normal checkout and inside a packaged (PyInstaller) app."""
    base = getattr(sys, "_MEIPASS", None)
    return (Path(base) / "satcad" / "static") if base else Path(__file__).resolve().parent / "static"


STATIC = _resource_dir()
MAX_BODY = 50 * 1024 * 1024                         # refuse absurdly large uploads
WATCHDOG_SECONDS = 300                              # exit this long after the browser tab stops pinging


class StudioState:
    """The one open design plus the little bit of server state around it."""

    def __init__(self, token: str, port: int):
        self.design = Design("my_design")
        self.lock = threading.RLock()
        self.token = token
        self.port = port
        self.last_modes_html: str | None = None
        self.last_ping = time.time()
        self.seen_ping = False

    # ---- reads -------------------------------------------------------------------------------------------------------------
    def state(self) -> dict:
        d = self.design
        feats = []
        for f in d.features.values():
            part = d.parts.get(f.name)
            feats.append(dict(name=f.name, kind=f.kind, raw=f.raw, mass_kg=None if part is None else round(part.mass_kg, 3),
                              assembly=None if part is None else d.assembly_path(part.assembly)))
        return dict(name=d.name, features=feats, variables=d.list_variables(), measure=d.measure(),
                    assembly_tree=d.assembly_tree(), joints=d.joints())

    def schemas(self) -> dict:
        out = {}
        for kind, sch in SCHEMAS.items():
            out[kind] = {k: ("__required__" if v == REQ else v) for k, v in sch.items()}
        return dict(kinds=out, materials=list(_materials.MATERIALS), version=__version__)


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, default=_default).encode("utf-8")


def _default(o):
    try:
        return o.tolist()
    except AttributeError:
        return str(o)


EMPTY_VIEW = (b"<!doctype html><meta charset=utf-8><body style='font:16px Georgia,serif;color:#555;background:transparent;"
              b"display:grid;place-items:center;height:100vh;margin:0'><p style='max-width:28em;text-align:center'>"
              b"The design is empty.<br>Add a part from the <b>Add</b> tab, or open an existing design.</p>")


def make_handler(app: StudioState):
    allowed_hosts = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "SatCADStudio"

        def log_message(self, *a):                  # keep the terminal quiet
            pass

        # ---- helpers ---------------------------------------------------------------------------------------------------------
        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, _json_bytes(obj), "application/json")

        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "") in allowed_hosts

        # ---- GET -------------------------------------------------------------------------------------------------------------
        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"Forbidden", "text/plain")
            url = urlparse(self.path)
            path, query = url.path, parse_qs(url.query)
            try:
                if path == "/":
                    html = (STATIC / "studio.html").read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                    return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                if path in ("/static/three.min.js", "/static/OrbitControls.js"):
                    return self._send(200, (STATIC / path.split("/")[-1]).read_bytes(), "application/javascript")
                if path == "/api/schemas":
                    return self._json(app.schemas())
                if path == "/api/state":
                    with app.lock:
                        return self._json(app.state())
                if path == "/viewer":
                    with app.lock:
                        try:
                            body = viewer_html(app.design, offline=True).encode("utf-8")
                        except DesignError:
                            body = EMPTY_VIEW
                    return self._send(200, body, "text/html; charset=utf-8")
                if path == "/modes":
                    if app.last_modes_html is None:
                        return self._send(404, b"No vibration analysis has been run yet.", "text/plain")
                    return self._send(200, app.last_modes_html.encode("utf-8"), "text/html; charset=utf-8")
                if path == "/api/save":
                    with app.lock:
                        name = app.design.name
                        body = json.dumps(app.design.to_json(), indent=2).encode("utf-8")
                    return self._send(200, body, "application/json",
                                      {"Content-Disposition": f'attachment; filename="{name}.json"'})
                if path == "/api/export":
                    fmt = (query.get("fmt") or ["step"])[0]
                    if fmt not in ("step", "stl", "glb"):
                        return self._json({"error": "Choose step, stl or glb."}, 400)
                    with app.lock, tempfile.TemporaryDirectory() as tmp:
                        name = app.design.name
                        info = app.design.export(tmp, name, [fmt])
                        data = Path(info[fmt]["path"]).read_bytes()
                    ctype = {"step": "application/step", "stl": "model/stl", "glb": "model/gltf-binary"}[fmt]
                    return self._send(200, data, ctype, {"Content-Disposition": f'attachment; filename="{name}.{fmt}"'})
                if path == "/api/check":
                    with app.lock:
                        return self._json(app.design.check())
                if path == "/api/ping":
                    app.last_ping, app.seen_ping = time.time(), True
                    return self._json({"ok": True})
                return self._send(404, b"Not found", "text/plain")
            except DesignError as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:                  # pragma: no cover - last-resort message instead of a dropped connection
                self._json({"error": f"Unexpected problem: {type(e).__name__}: {e}"}, 500)

        # ---- POST ------------------------------------------------------------------------------------------------------------
        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-Studio-Token") != app.token:
                return self._send(403, b"Forbidden", "text/plain")
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._json({"error": "That file is too large."}, 413)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json({"error": "The request was not valid JSON."}, 400)
            path = urlparse(self.path).path
            try:
                with app.lock:
                    result = self._route(path, body)
                self._json(result)
                if path == "/api/quit":
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
            except (DesignError, ValueError, KeyError, TypeError) as e:
                msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
                self._json({"error": str(msg)}, 400)
            except Exception as e:                  # pragma: no cover
                self._json({"error": f"Unexpected problem: {type(e).__name__}: {e}"}, 500)

        def _route(self, path: str, b: dict):
            d = app.design
            if path == "/api/add":
                d.add(str(b["kind"]), str(b["name"]), **dict(b.get("settings") or {}))
            elif path == "/api/edit":
                d.edit(str(b["name"]), **dict(b.get("changes") or {}))
            elif path == "/api/remove":
                d.remove(str(b["name"]))
            elif path == "/api/variable":
                d.set_variable(str(b["name"]), b["value"])
            elif path == "/api/variable/delete":
                d.delete_variable(str(b["name"]))
            elif path == "/api/new":
                name = str(b.get("name") or "my_design")
                if not NAME_RE.match(name):
                    raise DesignError("Names may only use letters, digits, '_' and '-' (max 40 characters).")
                app.design = Design(name)
            elif path == "/api/rename":
                name = str(b["name"])
                if not NAME_RE.match(name):
                    raise DesignError("Names may only use letters, digits, '_' and '-' (max 40 characters).")
                d.name = name
            elif path == "/api/open":
                fresh = Design("my_design")
                with tempfile.TemporaryDirectory() as tmp:
                    f = Path(tmp) / "in.json"
                    f.write_text(json.dumps(b["data"]))
                    try:
                        fresh.load(f)
                    except DesignError:
                        raise
                    except Exception as e:
                        raise DesignError(f"Couldn't open that design: {e}") from None
                if not NAME_RE.match(fresh.name or ""):
                    fresh.name = re.sub(r"[^A-Za-z0-9_-]", "_", str(fresh.name))[:40] or "my_design"
                app.design = fresh
            elif path == "/api/analyze":
                return self._analyze(b)
            elif path in ("/api/quit",):
                return {"ok": True}
            else:
                raise DesignError("Unknown request.")
            return app.state()

        def _analyze(self, b: dict) -> dict:
            from . import fem                                     # scipy is slow to import, so only when asked
            d = app.design
            if not d.parts:
                raise DesignError("The design is empty. Add a part first.")
            fp, fs = b.get("fixed_part") or None, b.get("fixed_side") or None
            if (fp is None) != (fs is None):
                raise DesignError("Give both the supported part and its face, or leave both empty for a free structure.")
            constraint = b.get("constraint") or "perimeter"
            try:
                model = fem.from_design(d, fixed=None if fp is None else {"part": fp, "side": fs}, constraint=constraint)
                if b.get("kind") == "static":
                    g = [float(v) for v in (b.get("accel_g") or [0, 0, 0])]
                    if fp is None:
                        raise DesignError("A stress analysis needs a support: choose the part and face that is held fixed.")
                    r = model.static(accel_g=g, safety_factor=float(b.get("safety_factor") or 1.25))
                    plates = {k: dict(stress_mpa=round(v["max_von_mises_mpa"], 3), margin=None if v["margin_of_safety"] is None
                                      else round(v["margin_of_safety"], 3)) for k, v in r["stress_by_plate"].items()}
                    return dict(kind="static", max_displacement_mm=round(r["max_displacement_mm"], 3),
                                max_von_mises_mpa=round(r["max_von_mises_mpa"], 3), worst_plate=r["worst_plate"],
                                reaction_n=[round(v, 1) for v in r["reaction_force_n"]], plates=plates, warnings=model.warnings)
                res = model.modal(int(b.get("n_modes") or 6))
            except ValueError as e:
                raise DesignError(str(e)) from None
            app.last_modes_html = mode_viewer_html(model, res, d.name, offline=True)
            return dict(kind="modal", frequencies_hz=[round(f, 4) for f in res["frequencies_hz"]],
                        rigid_body_modes_removed=res["rigid_body_modes_removed"],
                        mass_kg=round(res["total_mass_tonne"] * 1000, 1), cad_mass_kg=d.measure()["total_mass_kg"],
                        warnings=model.warnings, has_viewer=True)

    return Handler


class LocalServer(ThreadingHTTPServer):
    """HTTP server for this computer only. The standard class looks up the machine's host name when it binds, which can
    stall for a minute on some networks; we know we are 127.0.0.1, so skip that."""
    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


def _free_port(preferred: int = 0) -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", preferred))
        return s.getsockname()[1]


def serve(port: int = 0, open_browser: bool = True, watchdog: bool = True, ready=None) -> None:
    port = _free_port(port)
    app = StudioState(secrets.token_urlsafe(24), port)
    server = LocalServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}/"
    if watchdog:
        def watch():
            while True:
                time.sleep(5)
                if app.seen_ping and time.time() - app.last_ping > WATCHDOG_SECONDS:
                    server.shutdown()
                    return
        threading.Thread(target=watch, daemon=True).start()
    print(f"SatCAD Studio is running at {url}\nLeave this window open while you work. Close it (or press Ctrl+C) to quit.", flush=True)
    if ready:
        ready(app, url)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SatCAD Studio: edit spacecraft designs in your browser, entirely on your own computer.")
    ap.add_argument("--port", type=int, default=0, help="port to use (default: any free port)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    ap.add_argument("--keep-running", action="store_true", help="do not quit when the browser tab has been closed")
    args = ap.parse_args(argv)
    serve(args.port, not args.no_browser, not args.keep_running)
    return 0


if __name__ == "__main__":
    sys.exit(main())
