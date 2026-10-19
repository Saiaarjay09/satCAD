"""
ORBITAL DATACENTRE, FIRST SKETCH
================================
Start from a power budget and let the numbers size the spacecraft:

    compute power  ->  solar array area   (how much sun-catching surface we need)
    heat to reject ->  radiator area      (how much surface we need to shed the heat to space)

Change the numbers in the box and re-run. Everything geometry-related is millimetres.
"""
# ============================================================================
#  >>> CHANGE THESE NUMBERS <<<
# ============================================================================
COMPUTE_POWER_W = 100_000     # electrical power the computers draw (all of it ends up as heat)
RADIATOR_TEMP_K = 330         # how hot the radiator surface runs (57 C). Hotter = smaller radiators
RACK_MASS_KG = 6_000          # mass of the compute racks and plumbing inside the housing
CHASSIS_SIZE = (5000, 2200, 2200)   # mm  (length X, width Y, height Z) of the housing
# ============================================================================
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from satcad import Design, size_radiator, size_solar_array
from satcad.render import render_png

OUTPUT = Path(__file__).resolve().parent / "output"
OUTPUT.mkdir(exist_ok=True)

radiator = size_radiator(COMPUTE_POWER_W, panel_temp_k=RADIATOR_TEMP_K, panel_count=2, aspect_length_over_width=1.5)
solar = size_solar_array(COMPUTE_POWER_W * 1.1, panel_count=2)           # +10% for losses and housekeeping
print(f"Radiators: {radiator['required_panel_area_m2']} m2 total -> 2 panels of "
      f"{radiator['suggested_each_length_mm']} x {radiator['suggested_each_width_mm']} mm")
print(f"Solar:     {solar['required_array_area_m2']} m2 total -> 2 wings of "
      f"{solar['suggested_each_length_mm']} x {solar['suggested_each_width_mm']} mm")

L, W, H = CHASSIS_SIZE
d = Design("datacentre_v0")
d.add("chassis", "compute_bus", length=L, width=W, height=H, wall=12, internal_mass_kg=RACK_MASS_KG)

# Radiators go out of the +/-Y faces (flat, edge-on to the sun); solar wings go out of the +/-Z faces
# and are offset so they don't shade the radiators.
d.add("panel", "radiator_pos", attach_to="compute_bus", side="+y", length=radiator["suggested_each_length_mm"],
      width=radiator["suggested_each_width_mm"], thickness=30)
d.add("panel", "radiator_neg", attach_to="compute_bus", side="-y", length=radiator["suggested_each_length_mm"],
      width=radiator["suggested_each_width_mm"], thickness=30)
d.add("panel", "solar_top", attach_to="compute_bus", side="+z", length=solar["suggested_each_length_mm"],
      width=solar["suggested_each_width_mm"], thickness=25, thin_axis="y", material="aluminum_honeycomb", role="solar")
d.add("panel", "solar_bottom", attach_to="compute_bus", side="-z", length=solar["suggested_each_length_mm"],
      width=solar["suggested_each_width_mm"], thickness=25, thin_axis="y", material="aluminum_honeycomb", role="solar")

m = d.measure()
print(f"Total mass ~{m['total_mass_kg']:.0f} kg, overall {m['overall_size_mm']} mm")
print(d.check()["advice"])
(OUTPUT / "datacentre_v0.png").write_bytes(render_png(d))
d.export(OUTPUT, "datacentre_v0")
d.save(OUTPUT / "datacentre_v0.json")
print("Written to", OUTPUT)
