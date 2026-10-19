# Roadmap: from concept-stage CAD toward engineering-grade spacecraft design

This page is deliberately blunt about where SatCAD stands. Aerospace programmes carry real money and safety, so the
documentation should never let anyone believe this software is more capable than it is.

## 1. How this project relates to Siemens NX

SatCAD is an **independent, open-source** project. It is **not** derived from Siemens NX and was **not** built by examining NX's
software. NX is licensed proprietary software whose licence forbids reverse engineering, so this project uses only
publicly documented engineering concepts (feature-based parametric modelling, expressions, assemblies, mass properties,
STEP exchange) and open-source libraries. The goal is to cover the *workflow* a spacecraft designer needs, using open tools.

**Matching NX "almost exactly" is not a realistic goal**, and chasing it would be the wrong target. NX represents decades of work by
very large teams, together with an ecosystem around it (simulation, manufacturing, data management, certified processes). What
makes a satellite design *accurate* is mostly not the modelling interface. It is: correct geometry, correct mass properties,
verified analysis (structures, thermal, dynamics), requirements traceability and review. The roadmap below targets those.

## 2. Where SatCAD is today

A line-by-line comparison with NX's publicly described capabilities is in **[NX_BENCHMARK.md](NX_BENCHMARK.md)**. The summary table:

Legend: **yes** = implemented and tested, **partial** = works for simple cases, **no** = not there.

| Capability (as commonly found in professional CAD) | SatCAD | Notes |
|---|---|---|
| Exact solid modelling (B-rep) kernel | **yes** | OpenCascade via build123d: the same class of geometry kernel professional systems use |
| Parametric feature history that rebuilds | **yes** | Every part is a recipe; edits rebuild dependants |
| Named variables and formulas ("expressions") | **yes** | `set_variable`, formulas such as `=(wingspan - chassis.width)/2`, references to other parts' size and position |
| Primitive solids, extrude, revolve | **yes** | box, cylinder, sphere, rect/circle/polygon extrusions (hollow tubes, taper), revolves |
| Fillets / chamfers | **partial** | On boxes and the chassis, and on chosen edge groups of any part (`fillet_edges`); fixed radius only |
| Holes, pockets (cuts) | **yes** | Round and rectangular cuts; ISO clearance, tapped, counterbore and countersink holes M3-M12 with patterns; cosmetic threads |
| Patterns and mirrors | **yes** | Linear, circular, mirror; follow their source |
| Sketcher with geometric/dimensional constraints | **no** | Profiles are explicit point lists, not constraint-solved sketches |
| Lofts, sweeps, shell | **partial** | Sweep along bent paths, loft between sections, shell with open faces. No free-form surfacing |
| Sheet metal, weldments, composites layup | **no** | Panels are simple slabs |
| Assemblies with mating constraints / sub-assemblies | **partial** | Nested sub-assemblies with rolled-up mass properties; hinge joints with limits and viewer sliders. No general mates or kinematic solver |
| Mass, centre of mass, **full inertia tensor** | **yes** | From real solids; verified against closed-form formulas (boxes, shells, spheres, cylinders, parallel-axis) |
| Interference (overlap) and floating-part checks | **yes** | Pairwise solid intersection; screws in tapped holes are reported as thread engagement, not overlap |
| Bill of materials | **yes** | CSV, with assembly column |
| Materials with structural properties | **partial** | 10 materials: density, E, nu, yield, CTE, conductivity. Typical values only, not allowables |
| STEP / STL / GLB export | **yes** | STEP carries part names and colours. Metadata such as PMI is not written |
| STEP import (use vendor/other-CAD parts) | **no** | |
| Interactive 3D viewer | **yes** | Browser-based: orbit, pick, hide, explode, centre of mass |
| 2D engineering drawings (dimensions, tolerances, PMI) | **no** | |
| Structural analysis (FEA: stress, modal/vibration) | **beta** | Own shell solver: g-load/pressure statics with stress and margin of safety, and modal analysis with animated mode shapes. Flat plates only; validated against plate theory. Not a substitute for a certified solver |
| Thermal network / environment analysis | **no** | `size_radiator` is a first-order estimate only |
| Orbit, attitude, eclipse, link-budget analysis | **no** | The separate Orbital-Datacentre repo has an optical-link simulator |
| Product-data management, revision control, approvals | **no** | Designs are JSON recipes, so `git` gives basic revision history |
| Manufacturing (CAM, tolerance analysis) | **no** | |

## 3. What actually makes a satellite design "accurate"

1. **Right geometry and mass properties**: centre of mass and inertia drive attitude control. SatCAD now computes these from real solids and the tests
   check them against textbook formulas. Remaining weakness: equipment is modelled as idealised blocks with assumed density.
2. **Verified analysis**: launch-load stiffness (first natural frequency must clear the launch vehicle's requirement), stress margins, thermal
   cases (hot, cold, eclipse), pointing stability. SatCAD has a BETA structural solver for flat-plate structures; thermal and pointing analysis do not exist yet.
3. **Requirements and traceability**: every number traced to a requirement and a source.
4. **Independent review**: a second tool and a second engineer. Treat SatCAD results as concept-stage until cross-checked.

## 4. Proposed roadmap (each step shippable on its own)

**Phase A: finish a strong concept-design tool** (SatCAD only)
- Sketcher with dimensional/geometric constraints and a constraint solver (the largest remaining modelling gap)
- Variable-radius fillets, draft, guide curves for sweeps and lofts
- Named mates and a kinematic solver (hinges, sliders) beyond today's revolute joints
- STEP import of off-the-shelf parts; STEP AP242 export with materials and assembly structure
- 2D drawings with dimensions
- Materials with certified allowables, citations and temperature dependence
- *Done so far:* shell, sweep, loft, edge fillets, ISO holes and fasteners, assemblies with hinges, materials with structural properties

**Phase B: analysis by integrating proven open-source solvers** (do not reinvent them)
- *Started:* a built-in shell finite-element solver (flat plates) for static and modal analysis, verified against textbook solutions (BETA).
- Next: solid and beam elements; meshing with **Gmsh** and structural FEA with **CalculiX**, validated against NAFEMS benchmarks and hand calculations before trusting. Buckling, random vibration and contact.
- Thermal: a lumped-parameter network with orbital heating (solar, albedo, Earth IR) and eclipse, validated against textbook cases.
- Orbit and attitude: use an established astrodynamics library (for example Orekit, poliastro/hapsira or SPICE) for eclipse timing, beta angle,
  ground tracks, torques; connect to the existing optical-link simulator for link-availability studies.

**Phase C: process and assurance**
- A verification suite: every analysis has a benchmark and a published tolerance
- Requirements tracking (each requirement linked to the analysis that verifies it)
- Design review checklists and automated report generation

**Phase D: ecosystem**
- Plug-in interface for custom part types and analyses; a library of spacecraft building blocks (reaction wheels, star trackers, tanks, thrusters)
  with verified mass/inertia data

## 5. Practical advice for now

- Use SatCAD for **concept design and trade studies**: sizing, layout, mass and inertia budgets, quick what-ifs, and driving the work from Claude.
- For hardware you intend to fly, **verify with established analysis tools and engineers**. If you need a mature open-source CAD environment today,
  FreeCAD is the closest free alternative to commercial parametric CAD; SatCAD exports STEP that it can open.
- Keep exporting STEP and keep designs in git, so nothing here locks you in.
