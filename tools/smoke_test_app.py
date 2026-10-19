"""Starts a packaged SatCAD Studio and checks it really works: serves the page, builds a part, exports STEP, runs a vibration analysis.

Usage:  python tools/smoke_test_app.py "path/to/SatCAD Studio executable"
Uses only the standard library, so it can run on any build machine. Exit code 0 = the app works.
"""
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return r.read()


def main(exe: str) -> int:
    proc = subprocess.Popen([exe, "--port", str(PORT), "--no-browser", "--keep-running"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        started = time.time()
        while True:
            try:
                get("/api/schemas")
                break
            except (urllib.error.URLError, ConnectionError, OSError):
                if proc.poll() is not None:
                    print("FAIL: the app exited during start-up (exit code %s)" % proc.returncode)
                    return 1
                if time.time() - started > 180:
                    print("FAIL: the app did not start within 3 minutes")
                    return 1
                time.sleep(1)
        print(f"started in {time.time() - started:.1f}s")
        token = re.search(r'const TOKEN="([^"]+)"', get("/").decode()).group(1)

        def post(path, body):
            req = urllib.request.Request(BASE + path, json.dumps(body).encode(), {"Content-Type": "application/json", "X-Studio-Token": token})
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)

        assert len(get("/static/three.min.js")) > 100_000, "3D library missing from the app"
        state = post("/api/add", {"kind": "chassis", "name": "bus", "settings": dict(length=1000, width=800, height=600, wall=6, internal_mass_kg=100)})
        assert state["measure"]["parts"] == 1, "could not add a part"
        assert b"three.min.js" in get("/viewer"), "3D view page missing"
        assert get("/api/export?fmt=step").startswith(b"ISO-10303-21"), "STEP export failed"
        modal = post("/api/analyze", {"kind": "modal", "n_modes": 3})
        assert len(modal["frequencies_hz"]) == 3, "vibration analysis failed"
        saved = json.loads(get("/api/save"))
        assert post("/api/open", {"data": saved})["name"] == saved["name"], "reopening a saved design failed"
        print("OK: page, 3D view, add part, STEP export, vibration analysis, save/open all work")
        return 0
    except Exception as e:                                  # noqa: BLE001 - report any failure plainly
        print("FAIL:", type(e).__name__, e)
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
