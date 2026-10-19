## satCAD: desktop application

A desktop CAD application for spacecraft design, laid out like a professional CAD package: **ribbon** (Home, Assemblies, Simulate, View),
**Part Navigator** feature tree, **Property Editor** with formulas, a shaded **3D viewport with edge highlights**, **Boolean** operations (Unite,
Subtract, Intersect), face picking, Edge Blend / Chamfer / Hole / Shell, assemblies with hinges, mass properties, interference checks, a BETA structural
(modal and static) analysis, undo / redo, and STEP / STL / GLB export. It runs entirely on your computer. Open and save `.json` parts, including
ones made by Claude or the SatCAD scripts.

**Download the file for your computer** (below, under Assets):

| System | File |
|---|---|
| Mac with Apple silicon (M1 or newer) | `satCAD-…-macos-arm64.dmg`: open it and drag satCAD into Applications |
| Windows 10/11, 64-bit | `satCAD-…-windows-x64-setup.exe`: run the installer |

Intel Macs are not built (run from source: see the README). The files are large (hundreds of MB) because they include the CAD kernel, Qt and VTK.

### The first time you open it

The apps are **not code-signed** (signing needs a paid developer identity), so your computer will warn you:

- **Mac:** "satCAD can't be opened because Apple cannot check it." Open **System Settings → Privacy & Security**, scroll down and press **Open Anyway**. Once only.
- **Windows:** a blue "Windows protected your PC" screen. Press **More info → Run anyway**.

Mouse: left click selects, **middle drag rotates**, **Shift + middle drag pans**, scroll zooms. No middle button? **Alt + left drag** rotates, **Alt + Shift + left drag** pans.

### Please note

A **concept-design** tool: the analysis is a BETA flat-plate model, and nothing here is certified for flight hardware (see `docs/NX_BENCHMARK.md`). The 3D view needs a
working OpenGL driver (any ordinary PC or Mac). Bodies and flat faces can be picked (Select filter on the Home tab); there is no single-edge picking, no sketcher and no STEP import yet.
