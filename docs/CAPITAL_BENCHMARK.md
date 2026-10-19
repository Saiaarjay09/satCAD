# satCAD Systems versus Siemens Capital: an honest benchmark

**The goal** was to give satCAD the kind of wiring, tubing and diagram capability that Siemens Capital is known for, using Capital as the yardstick.
**The honest answer** is that satCAD now covers the *spacecraft-internals slice* of that ground (one design, three networks, tied to the 3D model),
and that Capital is far ahead everywhere else. Nothing here should be read as "better than Capital in every way": it is not, and a tool that claimed
that would not be trustworthy. This page says exactly where each stands, and what to build next.

How this was researched: from Siemens' own public product pages for Capital (Logic Designer, Wiring Integrator, Harness Designer, Formboard Designer,
E/E Publisher, Service Explorer, Systems Integrator, Insight). No Siemens software, binary or licence was used, copied, decompiled or reverse engineered.
satCAD's code was written from textbook engineering (Ohm's law, Darcy-Weisbach, Stefan-Boltzmann, fibre attenuation).

## What Capital is (public description)

Capital is a family of electrical / electronic (E/E) systems and wire-harness tools for vehicles, aircraft and other platforms:

| Capital product | What Siemens says it does |
|---|---|
| Logic Designer | model-based schematic authoring of logical electrical systems, with design-rule checks "correct by construction" |
| Wiring Integrator | platform-level wiring routing; verifies signal separation; collaboration across ECAD and MCAD |
| Systems Integrator | rule-driven automated generation of complete platform wiring architectures |
| Harness Designer (and Modular) | detailed, validated, manufacturing-ready harnesses: wires, multicores, terminals, seals, plugs, tapes, tubes, heat-shrink, full bill of materials; wire lengths, bundle diameters, splice positions, weight; design merge from MCAD; options and variants from one "150%" design; change management |
| Formboard Designer | full-scale 2D harness assembly-board drawings linked to the harness design |
| E/E Publisher | rule-driven generation of diagrams from the engineering data |
| Service Explorer | 2D wiring diagrams and 3D routing views with real-time schematic generation and diagnostics |
| Insight | E/E design metrics and trade studies |

## Side by side

Legend: **ahead** = satCAD does this better for its niche; **parity** = comparable for what it covers; **behind** = Capital does much more; **none** = satCAD does not do this.

| Capability | Siemens Capital | satCAD Systems (this release) | Verdict |
|---|---|---|---|
| Wiring (connection) diagram generated from data | yes, rule-driven, many styles | yes: power diagram from the netlist, taps level with the loads, gauge / current / drop on every run, SVG in an HTML page | parity for one style; **behind** on styles and templates |
| Cooling / fluid diagrams | outside Capital's electrical / electronic scope | yes: P&ID-style loop drawing with flow direction, speed, pressure drop, radiator and pump budget | **ahead** for spacecraft loops (Capital does not cover fluids) |
| Optical fibre network | not its stated focus | yes: backbone, EDFAs, switches, per-run loss and latency, loss budgets | **ahead** in having it in the same file |
| 3D routing view | yes (Service Explorer, Wiring Integrator) | yes: every run in 3D, rotate / zoom / hover for numbers, plus the runs as real tubes in the CAD model | parity; **ahead** in that the tubes are real solids in the same model |
| Automatic 3D routing | yes, rule-driven | yes: right-angled router that avoids every body, keeps clearances, respects bend radius, nests cables | **behind** (no bundling, no multi-drop nets, no rule libraries) but real |
| Clash check against the real geometry | via MCAD integration | built in: routes are solids, checked by the same exact-geometry interference engine | **ahead** (one model, no hand-off) |
| Stays right when the mechanical design changes | design merge between MCAD and ECAD | ports are anchored to parts and follow them; stale routes are flagged; one click re-routes | **behind** on merge sophistication; **ahead** on being instant and in one file |
| Electrical calculations | wire length, bundle diameter, weight, splices | length (with slack), resistance, current by nodal solve, voltage drop, copper and insulation mass, bundle diameter | parity on the basics; **behind** (no splices, no derating tables) |
| Engineering rule checks | configurable design-rule checks | current density, voltage drop, source capacity, flow speed, pump head, radiator capacity, fibre bend radius and loss budget, clearance between electrical and coolant runs, clashes, stale routes | different set; **ahead** for coolant and optical, **behind** on configurability |
| Component library | large, supplier-grade parts, terminals, seals, connectors | a gauge table, fluids table, fibre table | **behind** by orders of magnitude |
| Connectors, pins, terminals, cavities | yes, full detail | ports only (named connection points) | **none** / minimal |
| Formboards (flat assembly drawings) | yes | no | **none** |
| Options and variants (150% design) | yes | no (a design is one configuration; parameters change it) | **none** |
| Manufacturing outputs | validated, supplier-ready | CSV cut lists (wire, tube, fibre schedules) | **behind**: concept-level cut lists only |
| Change and release management, multi-user | yes | no | **none** |
| Scale | whole vehicles, tens of thousands of wires | one spacecraft (about 190 runs here) | **behind** |
| Cost and licence | commercial | free and open source | **ahead** |
| Validation and certification | industrial track record | none: concept-stage, not flight-qualified | **behind**; this is the biggest gap |

## What was built (all in this repository)

- `satcad/systems.py`: the netlist, the engineering maths and the rule checks (saved inside the design file).
- `satcad/routing.py`: the right-angled router and the conversion of routes into swept 3D tubes.
- `satcad/diagrams.py`: wiring, cooling and optical diagrams, the interactive 3D routing view, the CSV schedules.
- The **Systems** tab in the app, plus layer toggles (Power, Optical, Cooling, Shell) that show one network at a time in 3D.
- `generate_heavy_orbital_node.py`: the worked example (a heavy AI-datacentre satellite) now carries a full netlist: 48 V busbar and cables, the optical backbone, and two
  redundant ammonia coolant loops with pump skids, drawn as real tubes inside the hull. 0 clashes, 0 rule errors.

## What the example's numbers are, and are not

The loads (1.2 kW per server tray), the 250 kPa pump heads, the 200 K radiator sink, the ammonia properties at 20 degC, 0.3 dB per connector and the 2.5 A/mm2 current-density screen
are **assumptions** for a concept study, written into the file (`assumptions`) so you can read and change them. They are textbook values, not measurements and not a
MIL-W-5088 or ECSS derating analysis. Two-phase flow, radiation darkening of fibre, EMC, outgassing, splices and connector pin-outs are not modelled.
The cooling-loop pressure drop is dominated by an assumed 20 kPa per cold plate, so treat the loop result as a layout check, not a pump specification.

## What to build next, in order of value

1. A connector and pin model (pin-outs on every end, mating, cavity numbers), so the wiring diagram can show a real harness.
2. Multi-drop nets and bundling: several wires sharing a route, with bundle diameter driving the tube size.
3. A parts library (gauges with insulation options, tube and fitting catalogues) that can be extended by users.
4. Formboards: unfold a bundle tree to a flat full-scale drawing.
5. Options and variants.
6. Validation: compare the loop and harness results with a trusted reference case and publish the differences.
