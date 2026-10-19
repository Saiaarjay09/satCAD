"""
HELLO WORLD SATELLITE
=====================
Builds a rectangular metal chassis (the "server housing") with two flat radiator panels on its sides,
draws a picture, and writes CAD files (STEP) that Siemens NX, Fusion 360, SolidWorks or FreeCAD can import.

HOW TO USE: change a number below, save, and run
        python examples/hello_satellite.py
You do not need to understand the code underneath. Everything is in MILLIMETRES (1000 mm = 1 metre).
"""

# ============================================================================
#  >>> CHANGE THESE NUMBERS <<<
# ============================================================================
CHASSIS_LENGTH = 800      # mm, front to back   (X direction)
CHASSIS_WIDTH = 600       # mm, side to side    (Y direction)
CHASSIS_HEIGHT = 400      # mm, bottom to top   (Z direction)
CHASSIS_WALL = 8          # mm, thickness of the metal skin (0 = a solid block)
EQUIPMENT_MASS = 250      # kg, servers and electronics inside the chassis

RADIATOR_LENGTH = 1500    # mm, how far each panel sticks out from the chassis
RADIATOR_WIDTH = 800      # mm, how wide each panel is
RADIATOR_THICKNESS = 20   # mm, how thick each panel is
RADIATOR_TILT = 0         # degrees, tilt the panels up (+) or down (-) about where they attach
# ============================================================================

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))          # find the satcad package
from satcad import Design
from satcad.render import render_png

OUTPUT = Path(__file__).resolve().parent / "output"                    # files are written here
OUTPUT.mkdir(exist_ok=True)

design = Design("hello_satellite")

# 1) The chassis: a hollow aluminium box with equipment inside.
design.add("chassis", "chassis",
           length=CHASSIS_LENGTH, width=CHASSIS_WIDTH, height=CHASSIS_HEIGHT,
           wall=CHASSIS_WALL, internal_mass_kg=EQUIPMENT_MASS)

# 2) Two radiator panels, one on each side (+y and -y). "attach_to" makes them follow the chassis.
for name, side, tilt in (("radiator_left", "+y", RADIATOR_TILT), ("radiator_right", "-y", -RADIATOR_TILT)):
    design.add("panel", name, attach_to="chassis", side=side,
               length=RADIATOR_LENGTH, width=RADIATOR_WIDTH, thickness=RADIATOR_THICKNESS, tilt_deg=tilt)

# 3) Check it, measure it, draw it, save it.
print("Overlap check:", design.check()["advice"])
m = design.measure()
print(f"Total mass: {m['total_mass_kg']} kg   Overall size: {m['overall_size_mm']} mm")
(OUTPUT / "hello_satellite.png").write_bytes(render_png(design))
files = design.export(OUTPUT, "hello_satellite")
design.save(OUTPUT / "hello_satellite.json")
print("Picture :", OUTPUT / "hello_satellite.png")
print("CAD file:", files["step"]["path"], " (import this into Siemens NX / Fusion / SolidWorks / FreeCAD)")
