# PyInstaller recipe for SatCAD Studio.  Build with:   pyinstaller --noconfirm studio.spec
# Produces dist/SatCAD Studio (a folder; on macOS also dist/SatCAD Studio.app).
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [("satcad/static", "satcad/static")], [], []
for pkg in ("build123d", "OCP", "ocpsvg", "scipy", "lib3mf", "py_lib3mf", "bd_materials", "threejs_materials", "ocp_gordon", "ezdxf", "sklearn"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("satcad")

a = Analysis(["studio_app.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter", "matplotlib", "mcp", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SatCAD Studio", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SatCAD Studio")
import sys
if sys.platform == "darwin":
    app = BUNDLE(coll, name="SatCAD Studio.app", bundle_identifier="org.satcad.studio",
                 info_plist={"CFBundleShortVersionString": "0.3", "NSHighResolutionCapable": True})
