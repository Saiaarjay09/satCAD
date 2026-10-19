"""
AVIONICS BOX
============
A small machined avionics enclosure that shows the detailed-design features: a hollow body made with SHELL, rounded edges,
a lid with ISO clearance holes and ISO 4762 socket-head screws, a bent coolant pipe made with SWEEP, and a vibration check.

HOW TO USE: change a number below, save, and run
        python examples/avionics_box.py
Everything is in MILLIMETRES. Files appear in examples/output/.
(The finite-element tools analyse chassis boxes and flat panels; a shelled box is not analysed. See the datacentre for that.)
"""

# ============================================================================
#  >>> CHANGE THESE NUMBERS <<<
# ============================================================================
BOX_LENGTH = 300       # mm (X)
BOX_WIDTH = 200        # mm (Y)
BOX_HEIGHT = 120       # mm (Z)
WALL = 6               # mm, wall thickness of the machined body
CORNER_RADIUS = 8      # mm, outside edge rounding (must be larger than the wall, or the shell cannot follow it)
SCREW = "M4"           # lid screw size (M3 ... M12)
SCREW_COUNT = 8        # screws around the lid
SCREW_LENGTH = 12      # mm, under-head length
PIPE_RADIUS = 6        # mm, outer radius of the coolant pipe
# ============================================================================

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from satcad import Design
from satcad.hardware import spec
from satcad.render import render_png

OUTPUT = Path(__file__).resolve().parent / "output"
OUTPUT.mkdir(exist_ok=True)

d = Design("avionics_box")
d.set_variable("box_length", BOX_LENGTH)
d.set_variable("box_width", BOX_WIDTH)
d.set_variable("box_height", BOX_HEIGHT)
d.set_variable("wall", WALL)

# 1) The body: a solid block hollowed out from the top (+z face stays open), edges rounded.
d.add("assembly", "enclosure")
d.add("box", "body", length="=box_length", width="=box_width", height="=box_height", fillet=CORNER_RADIUS,
      material="aluminum_6061", role="structure", assembly="enclosure")
d.add("shell", "body_shell", target="body", open_faces=["+z"], thickness="=wall")

# 2) The lid, with a ring of clearance holes for the screws.
screw = spec(SCREW)
ring_r = min(BOX_LENGTH, BOX_WIDTH) / 2 - WALL / 2 - 2
d.add("box", "lid", length="=box_length", width="=box_width", height=4, z="=box_height / 2 + 2",
      material="aluminum_6061", role="structure", assembly="enclosure")
d.add("hole", "lid_holes", target="lid", side="+z", standard=SCREW, hole_type="clearance", depth=0,
      circle_count=SCREW_COUNT, circle_diameter=2 * ring_r)

# 3) One screw, then a circular pattern copies it around the lid.
d.add("fastener", "lid_screw_1", size=SCREW, length=SCREW_LENGTH, axis="-z", x=ring_r, y=0,
      z="=box_height / 2 + 4 + %s" % spec(SCREW)["head_height"], assembly="enclosure")
d.add("pattern", "lid_screw", source="lid_screw_1", mode="circular", count=SCREW_COUNT, axis="z", cx=0, cy=0, cz=0)

# 4) A coolant pipe with two bends (tangent arcs), routed above the lid.
d.add("sweep", "coolant_pipe", profile="circle", radius=PIPE_RADIUS, wall=1.5, bend_radius=40,
      path=[[-BOX_LENGTH / 2 + 30, -BOX_WIDTH / 2 + 30, BOX_HEIGHT / 2 + 40],
            [-BOX_LENGTH / 2 + 30, BOX_WIDTH / 2 - 30, BOX_HEIGHT / 2 + 40],
            [BOX_LENGTH / 2 - 30, BOX_WIDTH / 2 - 30, BOX_HEIGHT / 2 + 40]],
      material="copper", role="payload")

# 5) Electronics inside, as mass.
d.add("box", "pcb_stack", length=200, width=140, height=60, z=-24, material="generic_equipment", role="payload")

m = d.measure()
c = d.check()
print(f"Mass {m['total_mass_kg']:.3f} kg   overlaps: {len(c['overlaps'])}")
print("Screws are in contact with the lid (not floating):", not any(f['part'].startswith('lid_screw') for f in c['floating_parts']))
print(f"Screw: {SCREW}, clearance hole {screw['clearance']} mm, tap drill {screw['tap_drill']} mm")

(OUTPUT / "avionics_box.png").write_bytes(render_png(d))
files = d.export(OUTPUT, "avionics_box")
d.save(OUTPUT / "avionics_box.json")
print("Picture :", OUTPUT / "avionics_box.png")
print("CAD file:", files["step"]["path"])
