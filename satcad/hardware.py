"""
Standard hardware dimensions for holes and fasteners (metric, millimetres).

Hole sizes follow the usual metric tables: ISO 273 "medium" clearance holes, standard tap-drill sizes for coarse ISO threads,
DIN 974-1 counterbores for ISO 4762 socket-head cap screws, and countersinks sized for ISO 7046/10642 flat-head screws.
Screw head and socket sizes are the ISO 4762 values. These are the common catalogue numbers; check them against the
standard your project follows before releasing a drawing. Threads are COSMETIC: a tapped hole is modelled at the tap-drill
diameter and a screw shank at its nominal diameter.
"""
from __future__ import annotations

# size -> dict(pitch, clearance, tap_drill, cbore_dia, cbore_depth, csk_dia, head_dia, head_height, socket_af, socket_depth)
METRIC: dict[str, dict] = {
    "M3": dict(d=3.0, pitch=0.5, clearance=3.4, tap_drill=2.5, cbore_dia=6.5, cbore_depth=3.4, csk_dia=6.3,
               head_dia=5.5, head_height=3.0, socket_af=2.5, socket_depth=1.3),
    "M4": dict(d=4.0, pitch=0.7, clearance=4.5, tap_drill=3.3, cbore_dia=8.0, cbore_depth=4.4, csk_dia=8.4,
               head_dia=7.0, head_height=4.0, socket_af=3.0, socket_depth=2.0),
    "M5": dict(d=5.0, pitch=0.8, clearance=5.5, tap_drill=4.2, cbore_dia=10.0, cbore_depth=5.4, csk_dia=9.3,
               head_dia=8.5, head_height=5.0, socket_af=4.0, socket_depth=2.5),
    "M6": dict(d=6.0, pitch=1.0, clearance=6.6, tap_drill=5.0, cbore_dia=11.0, cbore_depth=6.5, csk_dia=11.3,
               head_dia=10.0, head_height=6.0, socket_af=5.0, socket_depth=3.0),
    "M8": dict(d=8.0, pitch=1.25, clearance=9.0, tap_drill=6.8, cbore_dia=15.0, cbore_depth=8.6, csk_dia=15.8,
               head_dia=13.0, head_height=8.0, socket_af=6.0, socket_depth=4.0),
    "M10": dict(d=10.0, pitch=1.5, clearance=11.0, tap_drill=8.5, cbore_dia=18.0, cbore_depth=10.8, csk_dia=18.3,
                head_dia=16.0, head_height=10.0, socket_af=8.0, socket_depth=5.0),
    "M12": dict(d=12.0, pitch=1.75, clearance=13.5, tap_drill=10.2, cbore_dia=20.0, cbore_depth=13.0, csk_dia=None,
                head_dia=18.0, head_height=12.0, socket_af=10.0, socket_depth=6.0),
}

HOLE_KINDS = ("clearance", "tapped", "counterbore", "countersink")


def spec(size: str) -> dict:
    key = str(size).upper()
    if key not in METRIC:
        raise ValueError(f"Unknown size '{size}'. Available metric sizes: {', '.join(METRIC)}.")
    return METRIC[key]
