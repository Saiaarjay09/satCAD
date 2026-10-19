# PyInstaller recipe for the satCAD desktop application (PySide6 + VTK).  Build with:   pyinstaller --noconfirm satcad_app.spec
# Produces dist/satCAD (a folder; on macOS also dist/satCAD.app).
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("build123d", "OCP", "ocpsvg", "scipy", "lib3mf", "py_lib3mf", "bd_materials", "threejs_materials", "ocp_gordon", "ezdxf", "sklearn",
            "vtkmodules"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("satcad")

QT_UNUSED = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtQuick", "PySide6.QtQml",
             "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
             "PySide6.QtPdf", "PySide6.QtDesigner", "PySide6.QtSql", "PySide6.QtBluetooth"]
a = Analysis(["main.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter", "matplotlib", "mcp", "pytest", "wx", "PyQt5", "PyQt6"] + QT_UNUSED, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="satCAD", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="satCAD")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="satCAD.app", bundle_identifier="org.satcad.app",
                 info_plist={"CFBundleShortVersionString": "0.4", "NSHighResolutionCapable": True})
