"""
ORBITAL DATACENTRE MODEL
========================
The name used in the original brief. All the geometry lives in build_satellite.py (one place to maintain); this file just runs it.

The tweakable dimensions (chassis length/width, radiator wingspan, laser radius ...) are the UPPER-CASE numbers in the marked
block at the top of build_satellite.py, or can be given on the command line without editing anything:

    .venv/bin/python orbital_datacentre_model.py --set RADIATOR_WINGSPAN=30000 --set TRANSCEIVER_RADIUS=300 --name big_radiators

Run with --view to open the interactive 3D viewer. Output goes to ./output/<name>/ (STEP, STL, GLB, pictures, BOM, report).
"""
import sys

from build_satellite import main

if __name__ == "__main__":
    sys.exit(main())
