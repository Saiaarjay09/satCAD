# SatCAD: open-source spacecraft CAD you can talk to

SatCAD lets you describe a spacecraft in plain English and have **Claude** build it as a real 3D model,
draw it, weigh it, check it for mistakes and export it as a CAD file. You do not need any CAD experience.
It can also be used as an ordinary Python library, or through ready-made example scripts.

![A server housing with two radiator wings and a laser terminal, built through SatCAD](docs/example_orbital_node.png)

*Built entirely by Claude through SatCAD: a 3 m server housing, two radiator wings sized from a 50 kW heat
load, and a laser terminal.*

SatCAD is built on **[build123d](https://github.com/gumyr/build123d)** (a free, open-source CAD library on the
OpenCascade geometry kernel) and talks to Claude through the **Model Context Protocol (MCP)**. It was written
for an orbital AI datacentre project ([Orbital-Datacentre](https://github.com/Saiaarjay09/Orbital-Datacentre))
but works for any simple spacecraft or box-and-panel design. It is an independent project: it is not derived
from, and not affiliated with, Siemens NX. Files it exports (STEP) are a standard format that NX can import.

---

## Contents

1. [What you can do with it](#1-what-you-can-do-with-it)
2. [Install](#2-install)
3. [Use it](#3-use-it), including [building the orbital AI datacentre](#3f-build-the-orbital-ai-datacentre-build_satellitepy)
4. [Five ideas that make CAD easy](#4-five-ideas-that-make-cad-easy)
5. [What each file does](#5-what-each-file-does)
6. [Reference: tools Claude can call](#6-reference-tools-claude-can-call)
7. [Reference: part types and their settings](#7-reference-part-types-and-their-settings)
8. [How it works](#8-how-it-works)
9. [Extending it](#9-extending-it)
10. [Testing](#10-testing)
11. [Troubleshooting](#11-troubleshooting)
12. [Limits](#12-limits)

---

## 1. What you can do with it

- **Sketch a spacecraft by talking.** "Make a hollow 3 m server housing and put radiators on both sides."
- **Size the big things from numbers.** Give it a heat load and it works out how much radiator area is
  needed; give it a power budget and it works out the solar array.
- **Change one number and everything follows.** Make the housing wider and the attached radiators move out
  with it. This is what "parametric" means.
- **See what you built.** Four views (isometric, top, front, side) after every change.
- **Catch mistakes.** Mass and centre of mass, overlapping parts, parts floating in space.
- **Hand it on.** Export **STEP** (opens in most CAD programs, including Siemens NX), **STL** (3D printing,
  mesh viewers) or **GLB** (web and AR viewers).
- **Build the full orbital AI datacentre in one command** (`python build_satellite.py`): chassis with compute
  blades, radiators, laser transceivers and solar arrays, all driven by variables at the top of one file.
- **Run trade studies.** Because designs are saved as small JSON recipes, you can version them in git and
  compare variants.

## 2. Install

### What you need

- macOS or Linux (Windows: use WSL, see [Troubleshooting](#11-troubleshooting))
- Python 3.10 or newer (`python3 --version` to check)
- `git`
- About 1 GB of disk space (most of it is the CAD library)
- For the Claude connector: [Claude Code](https://claude.com/claude-code)

### Quick install (recommended)

```bash
git clone git@github.com:Saiaarjay09/Open-source-CAD-software.git
cd Open-source-CAD-software
./setup.sh
```

`setup.sh` does three things: creates a private Python environment in `.venv/`, installs the libraries from
`requirements.txt`, and runs the self-tests. It takes about a minute. It ends with
`65 passed` and the path of the connector.

### Let Claude use it

```bash
./setup.sh --register
```

This does everything above and then registers SatCAD with Claude Code under the name `satellite-cad`.
**Restart Claude Code afterwards.** If the `claude` command isn't available, the script prints the exact entry to
add under `"mcpServers"` in `~/.claude.json`; it looks like this:

```json
"satellite-cad": { "command": "/full/path/to/Open-source-CAD-software/run_server.sh", "args": [] }
```

To check it worked, ask Claude: *"Run design_status on the satellite CAD connector."*

### Manual install (if you prefer to see each step)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

## 3. Use it

### 3a. Talk to Claude (the main way)

Once registered, just describe what you want. A good first conversation:

> Start a new satellite design. Make a hollow aluminium server housing 3 m long, 1.8 m wide and 1.8 m tall
> with 10 mm walls and 3,000 kg of servers inside. Show me a picture.

> I need to reject 50 kW of heat at a radiator temperature of 330 K. Size the radiators and add them to both
> sides of the housing.

> Add a 300 mm diameter laser terminal on the front face. Check for overlaps and tell me the total mass.

> Make the housing 4 m long. What happened to the radiators? Then export it as a STEP file.

Claude explains each step, shows the four-view picture after every change, and saves files under
`cad_output/`. Your design is **autosaved**, so it is still there the next time you start Claude.

Other things to ask for: "How big must the solar array be for 100 kW?", "Tilt the radiators 30 degrees",
"Which material should the panels be, and what does that do to the mass?", "Save this as `baseline`", "Load
`baseline`", "List every part with its mass".

### 3b. Run a ready-made example script

No Claude needed. Open the file, change the numbers in the clearly marked box at the top, and run it:

```bash
source .venv/bin/activate
python examples/hello_satellite.py      # chassis + two radiator panels
python examples/datacentre_v0.py        # sizes radiators and solar wings from a power budget
```

Each prints a summary and writes a picture and CAD files to `examples/output/`.

### 3c. Use it as a Python library

```python
from satcad import Design, size_radiator
from satcad.render import render_png

d = Design("my_sat")
d.add("chassis", "bus", length=3000, width=1800, height=1800, wall=10, internal_mass_kg=3000)

rad = size_radiator(50_000, panel_temp_k=330)            # how much radiator for 50 kW?
d.add("panel", "radiator_left", attach_to="bus", side="+y",
      length=rad["suggested_each_length_mm"], width=rad["suggested_each_width_mm"], thickness=25)

d.edit("bus", width=2200)                                # change a number: the radiator follows
print(d.measure())                                       # mass, centre of mass, overall size
print(d.check())                                         # overlaps and floating parts
open("my_sat.png", "wb").write(render_png(d))
d.export("out", "my_sat")                                # out/my_sat.step, .stl, .glb
d.save("out/my_sat.json")                                # the recipe, reloadable with d.load(...)
```

### 3d. Where your files go

| What | Where |
|---|---|
| Autosave of the current design | `cad_output/designs/current.json` |
| Designs you save by name | `cad_output/designs/<name>.json` |
| Preview pictures | `cad_output/<design name>_preview.png` |
| STEP / STL / GLB exports | `cad_output/exports/` |
| Example script output | `examples/output/` |

Change the output folder with the `SATCAD_OUT` environment variable. All of these are ignored by git.

### 3e. Opening the result in Siemens NX (or another CAD program)

Export a STEP file, then in NX use **File → Import → STEP** (menu wording varies by version). Each part's
name and colour are stored in the file; whether NX shows them depends on its import settings. This path has
not been tested against NX itself (no licence was available). The STEP output *was* verified by re-importing it
into the open-source kernel and checking the volume matches.

### 3f. Build the orbital AI datacentre (`build_satellite.py`)

`build_satellite.py` uses SatCAD to design a complete space-based AI compute node with four subsystems:

| # | Subsystem | What it is in the model |
|---|---|---|
| 1 | **Central datacentre chassis** | A large hollow aluminium box (6 x 2.4 x 2.4 m) holding a row of 12 upright **compute blades**, plus the mass of the pumps and coolant |
| 2 | **Thermal radiators** | Two flat panels reaching out of the left and right sides, 22 m tip to tip: about **8 times the chassis's top-down footprint** |
| 3 | **Optical inter-satellite links (OISL)** | Two cylindrical laser transceivers, one on the front and one on the rear, each a gimbal housing (for the Fast Steering Mirror) plus a telescope barrel |
| 4 | **Solar arrays** | Two flat wings on the top and bottom faces, in a plane **perpendicular to the radiators**, with their big faces toward the sun |

![The whole satellite in four views](docs/orbital_datacentre.png)

![Cut-away: the compute blades and laser terminals with the chassis shell hidden](docs/orbital_datacentre_core_detail.png)

**Run it** (from the repository folder, after `./setup.sh` has been run once):

```bash
source .venv/bin/activate
python build_satellite.py --view
```

Or in one line without activating the environment:

```bash
.venv/bin/python build_satellite.py --view
```

It takes a few seconds. `--view` opens the pictures when done (macOS and Linux); leave it off to just write the files.

**What you get**, in `output/orbital_datacentre/`:

| File | What it is |
|---|---|
| `orbital_datacentre.png` | The whole satellite in four views (isometric, top, front, side) |
| `orbital_datacentre_core_detail.png` | A cut-away of the compute blades and laser terminals, with the chassis shell hidden |
| `orbital_datacentre.step` | The 3D model as a CAD file. Import it into Siemens NX, Fusion 360, SolidWorks or FreeCAD |
| `orbital_datacentre.stl` / `.glb` | Mesh versions for 3D-printing slicers and web / AR / mesh viewers |
| `orbital_datacentre.json` | The editable recipe: reload it with `Design().load(...)` or the connector's `load_design` |
| `orbital_datacentre_report.txt` | The design report (also printed to the screen) |

**Change the size.** Open `build_satellite.py`. The numbers you can change are in one clearly marked block at the
very top. Edit one, save, and run it again. Everything is in millimetres.

| Variable | Default | Meaning |
|---|---|---|
| `CHASSIS_LENGTH` / `_WIDTH` / `_HEIGHT` | 6000 / 2400 / 2400 | Chassis size: front-to-back (X), left-to-right (Y), bottom-to-top (Z) |
| `CHASSIS_WALL` | 15 | Wall thickness of the hollow chassis |
| `PLUMBING_MASS_KG` | 1500 | Pumps, coolant and pipework inside (counted in mass, not drawn) |
| `BLADE_COUNT`, `BLADE_THICKNESS`, `BLADE_PITCH`, `BLADE_HEIGHT`, `BLADE_SIDE_CLEARANCE` | 12, 120, 180, 1600, 60 | The row of compute blades inside the chassis. Pitch is centre-to-centre spacing |
| `RADIATOR_WINGSPAN` | 22000 | **Tip-to-tip** span across both panels and the chassis |
| `RADIATOR_CHORD` | 6000 | How wide each radiator panel is, along the satellite's length |
| `RADIATOR_THICKNESS` | 40 | Radiator panel thickness |
| `RADIATOR_TILT_DEG` | 0 | Hinge both panels up (+) or down (-) as a symmetric V |
| `TRANSCEIVER_RADIUS` | 250 | Radius of each laser telescope barrel |
| `TRANSCEIVER_LENGTH` | 800 | Length of each telescope barrel |
| `GIMBAL_RADIUS_FACTOR`, `GIMBAL_LENGTH` | 1.4, 300 | Gimbal housing size, as a multiple of the telescope radius, and its length |
| `SOLAR_WING_LENGTH` / `_WIDTH` / `_THICKNESS` | 14000 / 12000 / 40 | Size of each solar wing |
| `RADIATOR_TEMP_K`, `RADIATOR_EMISSIVITY`, `SOLAR_CELL_EFFICIENCY`, `SOLAR_PACKING_FACTOR`, `SOLAR_END_OF_LIFE_FACTOR` | 330, 0.85, 0.30, 0.90, 0.85 | Operating assumptions used only for the report, not for the shapes |

**Or change a size without editing the file** with `--set` (repeat it for several), and give the result its own name
so it does not overwrite the first:

```bash
python build_satellite.py --set RADIATOR_WINGSPAN=30000 --set BLADE_COUNT=16 --name wide_radiators
python build_satellite.py --set TRANSCEIVER_RADIUS=400 --set CHASSIS_LENGTH=8000 --name big_optics
```

If a combination can't work (for example more blades than fit, or a wingspan narrower than the chassis), the script
stops with a plain-English message saying what to change.

**Read the design report.** After building, the script prints the total mass, the mass by role, the **radiator area
compared with the chassis**, how much heat the radiators can reject at the chosen temperature, how much electrical
power the solar wings make, and a **heat balance**: all electrical power ends up as heat, so the radiators must be
able to reject at least as much as the arrays generate. If they cannot, it prints a warning. If you shrink the radiators
or enlarge the solar wings and see that warning, the design no longer closes thermally. It also lists any overlapping
or floating parts. The radiator and solar numbers are rough estimates (see [Limits](#12-limits)).

**Link to the optical-link simulator.** `TRANSCEIVER_RADIUS` is the barrel radius; the usable optical aperture is a bit
smaller. It corresponds to the `--tx-aperture-m` / `--rx-aperture-m` settings in the
[Orbital-Datacentre](https://github.com/Saiaarjay09/Orbital-Datacentre) link simulator, so you can size the telescope
here and test its link performance there.

**Use it from your own code:**

```python
import build_satellite
d = build_satellite.build({"RADIATOR_WINGSPAN": 30000})     # any variable can be overridden
text, facts = build_satellite.report(d, {"RADIATOR_WINGSPAN": 30000})
print(facts["heat_rejection_kw"], facts["solar_power_kw"])
```

## 4. Five ideas that make CAD easy

1. **Part.** One solid object: the chassis, a radiator panel.
2. **Parametric.** A part is stored as a *recipe* (`length = 800`), not as a frozen shape. Change the number
   and the part, and everything attached to it, rebuilds.
3. **Units.** Everything is in **millimetres** (1000 mm = 1 m) and **kilograms**.
4. **Axes.** **X** is forward, **Y** is left, **Z** is up. A face is named by where it points: `+y` is the left
   side, `-y` the right, `+z` the top, `-z` the bottom, `+x` the front, `-x` the back.
5. **Attach.** A panel is attached to a face of another part. It sticks out of that face and follows the part
   if the part is resized or moved.

```
          +z (top)
            |   +x (front)
            |  /
   +y ------+------ -y
  (left)   /|        (right)
        -x  |
          -z (bottom)
```

## 5. What each file does

```
Open-source-CAD-software/
├── README.md                 this guide
├── setup.sh                  one-time installer: environment + libraries + tests (+ optional Claude registration)
├── build_satellite.py        THE ORBITAL DATACENTRE: builds the whole satellite from variables at its top
├── run_server.sh             starts the Claude connector; Claude runs this for you
├── requirements.txt          the Python libraries needed (build123d, mcp, matplotlib, numpy, pytest)
├── pytest.ini                test settings
├── .gitignore                keeps .venv/, cad_output/ and example output out of git
│
├── satcad/                   the software itself (a Python package)
│   ├── __init__.py           what "from satcad import ..." exposes
│   ├── __main__.py           lets you run the server with: python -m satcad.server
│   ├── engine.py             THE CORE: the parametric design engine (parts, recipes, rebuilding,
│   │                         measuring, overlap checks, export, save/load). No Claude code in here.
│   ├── materials.py          materials table (density, colour, note) and role colours
│   ├── thermal.py            size_radiator(): radiator area needed for a heat load
│   ├── power.py              size_solar_array(): solar array area needed for a power load
│   ├── render.py             draws the four-view PNG picture
│   └── server.py             THE CONNECTOR: wraps the engine as 20 MCP tools Claude can call
│
├── examples/
│   ├── hello_satellite.py    chassis + two radiators; the numbers to change are at the top
│   └── datacentre_v0.py      a 100 kW orbital datacentre sketch sized from its power budget
│
├── tests/
│   ├── test_engine.py        engine, physics and file-format tests (checked against hand calculations)
│   ├── test_server.py        launches the real server and talks to it like Claude does
│   └── test_build_satellite.py  checks the satellite: radiators flat and symmetric, solar wings perpendicular, lasers front and rear, validation, command line
│
└── docs/
    ├── example_orbital_node.png          the picture at the top of this README
    ├── orbital_datacentre.png            whole-satellite picture used in section 3f
    └── orbital_datacentre_core_detail.png  cut-away picture used in section 3f
```

**Which file do I edit?**

| I want to... | Edit |
|---|---|
| change the orbital datacentre's dimensions | the numbers at the top of `build_satellite.py`, or `--set NAME=VALUE` on the command line |
| change any other satellite's dimensions | nothing: ask Claude, or edit the numbers at the top of an `examples/` script |
| add a material | `satcad/materials.py` |
| add a new kind of part (e.g. a truss or a dish) | `satcad/engine.py` (see [Extending it](#9-extending-it)), then `satcad/server.py` |
| change how the picture looks | `satcad/render.py` |
| change what Claude is told about how to behave | `INSTRUCTIONS` at the top of `satcad/server.py` |
| change the radiator or solar maths | `satcad/thermal.py` / `satcad/power.py` |

## 6. Reference: tools Claude can call

All sizes are in **mm**, masses in **kg**. Settings marked `?` are optional.

| Tool | What it does | Settings |
|---|---|---|
| `design_status` | Name, parts, total mass and overall size. Call first. | none |
| `design_new` | Throw the design away and start empty. | `name?` |
| `add_chassis` | The main box (housing / bus), hollow or solid, with equipment mass inside. | `name, length, width, height, wall?, internal_mass_kg?, material?, x?, y?, z?` |
| `add_radiator_panel` | A flat panel attached to a face of an existing part. | `name, attach_to, side, length, width, thickness, gap?, offset_along_width?, offset_along_thickness?, tilt_deg?, thin_axis?, material?, role?` |
| `add_radiator_pair` | Two identical panels on opposite sides (`_pos` and `_neg`). | `name_prefix, attach_to, axis, length, width, thickness, gap?, tilt_deg?, material?` |
| `add_box` | A plain block. | `name, length, width, height, x?, y?, z?, material?, role?` |
| `add_cylinder` | A cylinder (tank, telescope barrel). | `name, radius, height, axis?, x?, y?, z?, material?, role?` |
| `add_sphere` | A sphere. | `name, radius, x?, y?, z?, material?, role?` |
| `edit_part` | Change settings of an existing part; attached parts follow. | `name, changes` (a dictionary) |
| `remove_part` | Delete a part (fails if others are attached to it). | `name` |
| `list_parts` | Every part: size, position, material, mass, settings. | none |
| `measure_design` | Total mass, centre of mass, overall size, mass by role. | none |
| `check_design` | Overlapping parts (error) and floating parts (warning). | none |
| `preview` | Isometric / top / front / side picture. | `views?` |
| `size_radiator` | Radiator area for a heat load (Stefan-Boltzmann). | `heat_load_w, panel_temp_k?, emissivity?, sides?, sink_temp_k?, panel_count?, aspect_length_over_width?` |
| `size_solar_array` | Solar-cell area for an electrical load. | `power_w, cell_efficiency?, packing_factor?, end_of_life_factor?, sun_angle_deg?, panel_count?, aspect_length_over_width?` |
| `list_materials` | Available materials with density and notes. | none |
| `export_design` | Write STEP / STL / GLB files. | `basename?, formats?` |
| `save_design` / `load_design` | Save or reload the editable recipe by name. | `filename?` / `filename` |

The server also offers a resource, `satcad://guide`, a plain-English glossary Claude can read.

## 7. Reference: part types and their settings

Every part has these **common settings**:

| Setting | Meaning | Default |
|---|---|---|
| `material` | Name from the materials table. Sets mass. | `aluminum_6061` (panels: `aluminum_honeycomb`) |
| `role` | A label that sets the colour in pictures and groups the mass report (`structure`, `chassis`, `radiator`, `solar`, `payload`, `antenna`). | `structure` (chassis: `chassis`, panels: `radiator`) |
| `color` | Override colour as `[r, g, b]` with values from 0 to 1. Set with `edit_part`. | from role |
| `notes` | Free text. | empty |

Parts that are not attached to something also take a **pose**: `x, y, z` (centre, mm) and `rx, ry, rz`
(rotation about the part's own centre, degrees).

| Type | Settings (default in brackets; no default = required) |
|---|---|
| `chassis` | `length` (X), `width` (Y), `height` (Z), `wall` (0 = solid block, otherwise hollow with this wall thickness; the wall must be under half the smallest dimension), `internal_mass_kg` (0) |
| `box` | `length`, `width`, `height` |
| `cylinder` | `radius`, `height`, `axis` (`z`; `x`, `y` also allowed) |
| `sphere` | `radius` |
| `panel` | `attach_to` (name of an existing part), `side` (`+x -x +y -y +z -z`), `length` (how far it sticks out), `width` (along the face), `thickness`, `gap` (0, distance off the face), `offset_span` (0, slide along the width), `offset_thin` (0, slide along the thickness), `thin_axis` (default `z` for side faces, `x` for top/bottom faces), `tilt_deg` (0, hinge about the attached edge, on whichever edge keeps the panel out of the part it is attached to; positive follows the right-hand rule about the width axis) |

`edit_part` also accepts `offset_along_width` and `offset_along_thickness` as aliases for
`offset_span` and `offset_thin`.

**Materials** (density, kg/m³): `aluminum_6061` 2700, `aluminum_honeycomb` 100 (a rough *effective* value for a
sandwich panel), `titanium_ti6al4v` 4430, `stainless_316` 8000, `cfrp` 1600, `copper` 8960,
`generic_equipment` 1500.

## 8. How it works

```
 You  ──►  Claude  ──(MCP, over stdin/stdout)──►  server.py  ──►  engine.py  ──►  build123d / OpenCascade
 words      picks a tool                          20 tools        recipes →        solids, booleans,
                                                  + autosave      geometry         STEP/STL/GLB export
                                                       │
                                                       └──►  render.py → PNG picture back to Claude
```

- **Recipes, not shapes.** The engine stores each part as a `Feature` (name, type, settings). After any
  change it **rebuilds every part from its recipe, in order**. Panels look up the *current* bounding box of
  the part they are attached to, which is why they follow it.
- **Failed changes undo themselves.** If an add or edit is invalid, the design is restored to how it was.
- **Hollow chassis.** A chassis with a `wall` is a box minus a slightly smaller box, so volume and mass count
  only the metal. Equipment mass (`internal_mass_kg`) is added at the chassis centre. A part sitting inside
  the hollow interior is *not* flagged as an overlap, because equipment inside a housing is normal.
- **Mass** = volume × material density + any equipment mass.
- **Overlap check** intersects every pair of parts; overlap volume above 1 mm³ is reported. **Floating**
  means no other part within 0.05 mm.
- **Autosave.** After every change the server writes `cad_output/designs/current.json` and reloads it on start.
- **stdout belongs to MCP.** The server never prints to standard output; that channel carries the protocol.

## 9. Extending it

**Add a material:** add a line to `MATERIALS` in `satcad/materials.py` (density in kg/m³, colour, note).

**Add a new part type** (say, a `dish`):

1. In `satcad/engine.py`, add an entry to `SCHEMAS` with the settings and defaults (use `REQ` for required ones).
2. In `Design._build`, add a branch that builds the shape for that kind and returns it with the pose applied.
3. If it needs special checks, add them in `Design._resolve`.
4. In `satcad/server.py`, add a tool with `@mcp.tool()` that calls `design.add("dish", name, ...)` and wrap
   the result in `_changed(...)` so it autosaves. Write a clear docstring: Claude reads it to decide when to
   use the tool.
5. Add a test in `tests/test_engine.py` and run `python -m pytest -q`.

## 10. Testing

```bash
source .venv/bin/activate
python -m pytest -q
```

65 tests. They check volumes and masses against hand calculations, panel placement and tilt, parametric
updates, error messages, save/load, STEP round-tripping, the Stefan-Boltzmann and solar maths, picture output,
and a full conversation with the real server over the MCP protocol.

## 11. Troubleshooting

| Problem | Fix |
|---|---|
| `setup.sh: Python 3.10 or newer is required` | Install a newer Python from python.org, then `PYTHON=python3.12 ./setup.sh` |
| Claude doesn't list the tools | Run `./setup.sh --register`, then **fully restart** Claude Code. Check the path in `~/.claude.json` points at `run_server.sh` |
| `ModuleNotFoundError: mcp.server.fastmcp` | The wrong MCP version is installed. `requirements.txt` pins `mcp>=1.9,<2`. Run `.venv/bin/pip install -r requirements.txt` |
| `Permission denied: run_server.sh` | `chmod +x run_server.sh setup.sh` |
| "Can't remove part: others are attached" | Remove the attached panels first, or edit the part instead |
| Picture looks empty or tiny | A very large part (e.g. a huge solar wing) shrinks everything else. Use `preview` with one view, e.g. `["side"]` |
| Windows | `setup.sh` and `run_server.sh` are shell scripts. Use WSL, or follow the manual install and register `.venv\Scripts\python.exe -m satcad.server` yourself. Not tested on Windows |
| The first run is slow | Matplotlib builds a font cache once; later runs are fast |

## 12. Limits

- **Simple geometry only:** boxes, cylinders, spheres, hollow shells and flat panels. No sketches, fillets,
  holes, free-form surfaces or assemblies with mating constraints.
- **Masses are estimates** from one density per material.
- **`size_radiator` is a lower bound.** It ignores sunlight, Earth heat and albedo, and the temperature drop
  across the panel and its plumbing.
- **`size_solar_array`** ignores eclipse, batteries and self-shading.
- **No simulation.** There is no structural, vibration, orbit or thermal analysis, only geometry and
  bookkeeping.
- **Not tested with Siemens NX** (no licence). STEP files are standard, but import behaviour is up to NX.

## Licence

No licence file has been chosen yet. Until one is added, the default is "all rights reserved". Add a `LICENSE`
(for example MIT or Apache-2.0) before sharing it. Dependencies carry their own licences (build123d,
OpenCascade, the MCP SDK, matplotlib).
