# SatCAD vs Siemens NX: an honest scoreboard

Siemens NX (sold today as **Designcenter** for CAD and **Simcenter 3D** for simulation) is the benchmark SatCAD is measured
against. This page lists what NX is publicly described as doing, what SatCAD does today, and the size of the gap. It is updated
whenever SatCAD gains a capability, and it is deliberately unflattering.

**How the benchmark was made.** It uses only what Siemens publishes about NX (product pages and documentation). NX was not
installed, run, disassembled or reverse engineered: its licence forbids that, and it was not needed to know what a professional
tool offers. SatCAD is independent open-source code on the OpenCascade kernel.

**The honest headline.** SatCAD is a *concept-stage* spacecraft design tool. It is not "as good as NX", and it will not be in one
step: NX is the product of decades of work by thousands of engineers, with certified solvers, a huge validation history and a full
ecosystem. Nothing here should be the sole basis for flight hardware. What SatCAD can do is cover the same *workflow* openly, verify
every number it produces against textbook solutions, and be driven by an AI assistant.

Status key: **Parity-ish** = does the same job for simple cases · **Partial** = real but limited · **Beta** = works, validated on simple
problems only · **No** = not there · **Ahead** = SatCAD does something NX does not.

## Modelling

| Capability | NX (public description) | SatCAD today | Status | Gap |
|---|---|---|---|---|
| Geometry kernel | Parasolid (exact B-rep) | OpenCascade via build123d (exact B-rep) | Partial | Different kernel; fewer healing/robustness tools |
| Feature-based parametric history | Yes, with edit-and-rebuild | Recipes that rebuild every time; rollback on failure | Parity-ish | No feature suppression/reorder UI |
| Expressions and variables | Yes | Named variables, `=formulas`, references to other parts | Parity-ish | No spreadsheets or reuse libraries |
| Sketcher with constraints | Full 2D constraint solver | Profiles are point lists (rect, circle, slot, rounded rect, ellipse, polygon) | No | Constraint-solved sketches are the biggest modelling gap |
| Extrude / revolve | Yes | Yes, with taper, both sides, hollow tubes | Parity-ish | |
| Sweep / loft | Yes | Sweep along a polyline with tangent bends; loft between circle/rect sections | Partial | No guide curves, no twist, no sharp-corner sweeps |
| Shell / thin-wall | Yes | Shell with open faces, inward or outward | Partial | Fails near tight fillets (reports why) |
| Boolean operations (unite, subtract, intersect) | Yes | Between any two bodies; the tool body is consumed; friendly errors when bodies do not overlap | Parity-ish | Operates on whole bodies only; no multi-tool or retained-tool options |
| Fillet / chamfer on chosen edges | Yes, variable radius, blends | Fixed radius or chamfer on edge groups (`all`, `parallel:z`, `face:+z`) | Partial | No variable radius, no face blends |
| Holes with standards | Hole feature with standard sizes | ISO 273 clearance, tapped, DIN 974 counterbore, countersink; M3-M12; patterns | Partial | Threads are cosmetic; no inch or other standards |
| Fasteners | Library | ISO 4762 socket-head screw (head, socket, shank); engagement check against tapped holes | Partial | Only one fastener family |
| Freeform / subdivision surfaces, class-A | Yes | No | No | Not relevant to most spacecraft; large effort |
| Sheet metal, weldments, composites layup | Yes | No (panels are slabs) | No | Important for real structures |
| Direct / synchronous modelling | Yes | No | No | |

## Assemblies and motion

| Capability | NX | SatCAD today | Status | Gap |
|---|---|---|---|---|
| Product structure, sub-assemblies | Yes | Nested assemblies with rolled-up mass, centre of mass, inertia | Parity-ish | No large-assembly performance work |
| Mating constraints | Full constraint set | Placement + attach-to-face; no general mates | Partial | |
| Joints / articulation | Motion simulation | Hinge joints with angle formulas and limits; parts, mass properties and checks follow the pose; interactive sliders in the viewer | Partial | Revolute only; no kinematic solver, loops, or dynamics |
| Motion (kinematics) | NX Motion / Simcenter Motion: joints, drivers, dynamics, contact | Motion Studio: revolute / prismatic / spherical joints driven by symbolic equations of time, nested chains, timeline with x, v, a plots, colour animations | Partial | Kinematics only: no dynamics, contact, force or collision analysis |
| Interference checking | Yes, clash detection | Exact solid intersection, floating-part check, thread-engagement awareness | Parity-ish | |
| Mass, centre of mass, inertia | Yes | Exact from solids, per assembly; verified against closed-form solutions | Parity-ish | Equipment is idealised blocks |
| Materials database | Yes, with vendor data | 10 materials with density, E, nu, yield, CTE, conductivity (typical values, caveats shown) | Partial | Not certified allowables |

## Simulation

| Capability | Simcenter 3D (public description) | SatCAD today | Status | Gap |
|---|---|---|---|---|
| Linear static structures | Yes, many element types | g-load and pressure on flat shell plates; stress and margin of safety | Beta | Only flat plates; no solids, beams, contact, buckling, nonlinear |
| Modal (vibration) | Yes | First modes with effective modal mass; animated mode shapes | Beta | Same limits; sandwich and composite panels idealised |
| Verification of solver | Decades of benchmarks | Checked against Hooke, plate theory, Leissa frequencies, Timoshenko deflections (see `tests/test_fem.py`) | Partial | A few dozen cases, not thousands |
| Thermal | Network and finite-element, with space-systems orbital heating | First-order radiator sizing only | No | Needs an orbital heating and conduction solver |
| Meshing | Advanced, automatic | Automatic triangle mesh of rectangular plates | Partial | No solids, no local refinement control |
| Optimisation, durability, acoustics, CFD | Yes | No | No | |
| Pre/post-processing | Rich | Mode viewer; stress numbers | Partial | No contour viewer for stress yet |

## Documentation and exchange

| Capability | NX | SatCAD today | Status | Gap |
|---|---|---|---|---|
| Desktop user interface | Ribbon, Part Navigator, property panels, 3D viewport | A Qt desktop app in the same layout: ribbon, feature tree, property editor, shaded viewport with edges, undo / redo | Partial | Far fewer commands and no sketching; bodies and flat faces can be picked (a picked face drives Hole, Shell and Edge Blend); no single-edge picking, no sketcher |
| STEP export | AP203/214/242 | STEP with part names and colours | Partial | No PMI or assembly metadata beyond names |
| STEP import | Yes | Solid bodies, with names and colours | Partial | No assembly structure, PMI or surfaces; the design points at the file instead of embedding it |
| 2D drawings, GD&T, PMI | Yes | No | No | |
| Data management / revisions | Teamcenter | Designs are JSON recipes, versioned with git | Partial | No approvals or traceability |
| CAM, tolerance analysis | Yes | No | No | |

## Where SatCAD is ahead or different

- **Free, open and inspectable.** Every solver line can be read and tested. There is no licence fee.
- **Drivable by an AI assistant.** Describe a design in plain English; 42 tools build, check, analyse and view it. NX has no equivalent.
- **Designs are text.** A design is a small JSON recipe: it diffs, merges and reviews in git like source code.
- **Built-in honesty.** Analyses print their limits and refuse (with an explanation) cases they cannot solve, such as loose parts or unsupported structures.

## What would close the biggest gaps (in order)

1. A constraint-solved sketcher (needed for almost all real part design).
2. Solid-element and beam FEA, ideally by integrating a proven open solver (CalculiX) and validating it on NAFEMS benchmarks.
3. A thermal solver with orbital heating (Earth IR, albedo, solar, eclipse).
4. STEP import and richer export (assembly structure, materials).
5. 2D drawings with dimensions and tolerances.
6. Sheet metal and composite layup.

These are months of work each, not tasks for one session. See [ROADMAP.md](ROADMAP.md) for the plan.
