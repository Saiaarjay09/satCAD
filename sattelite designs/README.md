# sattelite designs

Every satellite design made with satCAD is saved in this folder: **STEP** files for other CAD programs and the editable **`.json`** recipes the
satCAD app opens. (The folder name is spelled the way the project owner chose it.)

| File | What it is |
|---|---|
| `orbital_node_alpha.step` / `.json` | Orbital-Node-Alpha: the detailed AI-datacentre satellite (6 sub-assemblies, 405 bodies), made by `generate_orbital_node_assembly.py`. An example you can **Open** in satCAD: it is not built into the app |
| `orbital_node_alpha_with_motion.json` | the same satellite together with a Motion Studio setup (solar tracking, radiator fold, laser turrets, coolant pulse); open it and press Play. Made by `tools/alpha_motion.py` |
| `heavy_orbital_node.step` / `.json` | the heavy-class Orbital-Node-Alpha: 16 server trays in a 3.5 x 3.5 x 5.5 m hollow bus, three colour-coded power / optical / fluid schematics and two 16 m radiator wings (320 bodies, no overlaps). Made by `generate_heavy_orbital_node.py`; open it and use **View -> X-Ray**. Not built into the app |
| `heavy_orbital_node_systems/` | the drawings made from that file's netlist: wiring, cooling and optical diagrams, the interactive 3D routing view (open the `.html` files in a browser) and CSV cut lists. Made by `python generate_heavy_orbital_node.py --diagrams` |
| `alpha_sigma_production_node.py` | the generator for Alpha-Sigma, the 1:1 production-scale orbital AI datacentre (32 racks, 768 accelerators, folding radiators and solar arrays, three schematic networks). Run `python3 "sattelite designs/alpha_sigma_production_node.py"` |
| `alpha_sigma_production_node.json` / `Alpha_Sigma_v1.step` | Alpha-Sigma: the editable satCAD design (with the `DEPLOYMENT_RATIO` motion setup and the wiring / cooling / optical netlist, 2,446 bodies) and its STEP export. Not built into the app: **Open** it |
| `alpha_sigma_production_node_systems/` | Alpha-Sigma's wiring, cooling and optical diagrams, 3D routing view and CSV cut lists (open the `.html` files in a browser) |
| `orbital_datacentre.step` | the earlier orbital datacentre model from `build_satellite.py` |

The satCAD app's Open / Save / Export dialogs start here, the Claude connector's `save_design` and `export_design` write here, and
`generate_orbital_node_assembly.py --step name.step` puts a bare file name here. Set the environment variable `SATCAD_DESIGNS` to use another folder.
