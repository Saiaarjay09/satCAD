# sattelite designs

Every satellite design made with satCAD is saved in this folder: **STEP** files for other CAD programs and the editable **`.json`** recipes the
satCAD app opens. (The folder name is spelled the way the project owner chose it.)

| File | What it is |
|---|---|
| `orbital_node_alpha.step` / `.json` | Orbital-Node-Alpha: the detailed AI-datacentre satellite (6 sub-assemblies, 405 bodies), made by `generate_orbital_node_assembly.py`. An example you can **Open** in satCAD: it is not built into the app |
| `orbital_node_alpha_with_motion.json` | the same satellite together with a Motion Studio setup (solar tracking, radiator fold, laser turrets, coolant pulse); open it and press Play. Made by `tools/alpha_motion.py` |
| `orbital_datacentre.step` | the earlier orbital datacentre model from `build_satellite.py` |

The satCAD app's Open / Save / Export dialogs start here, the Claude connector's `save_design` and `export_design` write here, and
`generate_orbital_node_assembly.py --step name.step` puts a bare file name here. Set the environment variable `SATCAD_DESIGNS` to use another folder.
