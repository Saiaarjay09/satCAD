## SatCAD Studio

An app for designing spacecraft: add parts, use variables and formulas, check for mistakes, view in 3D, run a first-order
vibration or stress analysis, **open a design to improve it**, and export STEP / STL / GLB for other CAD programs. It runs
entirely on your own computer and sends nothing anywhere.

**Download the file for your computer** (below, under Assets):

| System | File |
|---|---|
| Mac with Apple silicon (M1 or newer) | `SatCAD-Studio-…-macos-arm64.dmg`: open it and drag the app into Applications |
| Windows 10/11, 64-bit | `SatCAD-Studio-…-windows-x64-setup.exe`: run the installer |

Intel Macs are not built yet (see the README to run from source).

### The first time you open it

These apps are **not code-signed** (signing costs money and needs a registered developer identity), so your computer will warn you:

- **Mac:** "SatCAD Studio can't be opened because Apple cannot check it." Open **System Settings → Privacy & Security**, scroll down
  and press **Open Anyway** next to SatCAD Studio. You only do this once.
- **Windows:** a blue "Windows protected your PC" screen. Press **More info → Run anyway**.

SatCAD Studio opens in your web browser (the address starts with `127.0.0.1`, which means "this computer only"). Leave the app
running while you work; press **Quit** in the page when you are done. **Save** downloads a `.json` design file; **Open** loads
one, including designs made by Claude or by `build_satellite.py`.

### Please note

This is a **concept-design** tool. The analysis is a BETA flat-plate model. It is not certified, and it should not be the sole basis
for flight hardware. See `docs/NX_BENCHMARK.md` in the repository for exactly what it can and cannot do. Importing STEP files from
other CAD programs is not supported yet.
