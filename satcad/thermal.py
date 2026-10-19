"""Radiator sizing: how much panel area does it take to throw heat into space?"""
from __future__ import annotations

import math

STEFAN_BOLTZMANN = 5.670374419e-8  # W / (m^2 K^4)


def size_radiator(heat_w: float, panel_temp_k: float = 320.0, emissivity: float = 0.85,
                  sides: int = 2, sink_temp_k: float = 4.0, aspect_length_over_width: float = 2.0,
                  panel_count: int = 2) -> dict:
    """
    Required radiating area to reject `heat_w` watts by thermal radiation alone.

        per-side flux = emissivity * sigma * (T_panel^4 - T_sink^4)

    `sides` is 2 for a panel radiating from both faces (it must see cold space on both).
    `sink_temp_k` is the effective temperature of what the panel looks at: about 4 K for deep
    space, but around 200-250 K if it faces the Earth. Sunlight, Earth albedo and heat flowing
    back from other hardware are NOT modelled: treat the answer as a lower bound.
    """
    if heat_w <= 0 or panel_temp_k <= 0 or not 0 < emissivity <= 1 or sides not in (1, 2) or panel_count < 1:
        raise ValueError("Need heat_w > 0, panel_temp_k > 0, 0 < emissivity <= 1, sides 1 or 2, panel_count >= 1.")
    if sink_temp_k >= panel_temp_k:
        raise ValueError("The surroundings are as warm as the panel, so no heat can be radiated away.")
    flux = emissivity * STEFAN_BOLTZMANN * (panel_temp_k ** 4 - sink_temp_k ** 4)      # W per m^2 per side
    area_one_side = heat_w / (flux * sides)                                           # m^2 of panel footprint
    each = area_one_side / panel_count
    width = math.sqrt(each / aspect_length_over_width)
    return dict(
        heat_w=heat_w, panel_temp_k=panel_temp_k, emissivity=emissivity, sides_radiating=sides, sink_temp_k=sink_temp_k,
        flux_w_per_m2_per_side=round(flux, 1),
        required_panel_area_m2=round(area_one_side, 2),
        panel_count=panel_count,
        suggested_each_length_mm=round(width * aspect_length_over_width * 1000),
        suggested_each_width_mm=round(width * 1000),
        note=("Lower bound: ignores sunlight, Earth heat and albedo, and heat conduction inside the panel. "
              "Hotter panels radiate much more (flux scales with temperature to the 4th power), "
              "which is why orbital radiators run as hot as the electronics tolerate."))
