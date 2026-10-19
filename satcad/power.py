"""Solar array sizing: how much panel area does it take to make a given amount of electrical power?"""
from __future__ import annotations

import math

SOLAR_CONSTANT_W_M2 = 1361.0     # sunlight intensity at Earth's distance


def size_solar_array(power_w: float, cell_efficiency: float = 0.30, packing_factor: float = 0.90,
                     end_of_life_factor: float = 0.85, sun_angle_deg: float = 0.0, panel_count: int = 2,
                     aspect_length_over_width: float = 1.6) -> dict:
    """
    Area of solar cells needed to deliver `power_w` of electricity.

        power per m^2 = sunlight * cell efficiency * packing factor * end-of-life factor * cos(sun angle)

    cell_efficiency   ~0.30 for modern triple-junction space cells.
    packing_factor    fraction of the panel actually covered by cells (the rest is gaps and wiring).
    end_of_life_factor  allowance for radiation damage and ageing over the mission (~0.85).
    sun_angle_deg     angle between the panel's normal and the sun (0 = facing the sun directly).
    Eclipse (time in Earth's shadow) and battery size are NOT modelled.
    """
    if power_w <= 0 or panel_count < 1 or not 0 < cell_efficiency <= 1 or not 0 < packing_factor <= 1 \
            or not 0 < end_of_life_factor <= 1 or not -90 < sun_angle_deg < 90:
        raise ValueError("Need power_w > 0, panel_count >= 1, fractions between 0 and 1, and sun angle between -90 and 90 degrees.")
    w_per_m2 = SOLAR_CONSTANT_W_M2 * cell_efficiency * packing_factor * end_of_life_factor * math.cos(math.radians(sun_angle_deg))
    area = power_w / w_per_m2
    each = area / panel_count
    width = math.sqrt(each / aspect_length_over_width)
    return dict(power_w=power_w, usable_w_per_m2=round(w_per_m2, 1), required_array_area_m2=round(area, 1),
                panel_count=panel_count, suggested_each_length_mm=round(width * aspect_length_over_width * 1000),
                suggested_each_width_mm=round(width * 1000),
                note="Sunlit-power only: no eclipse or battery sizing, and no allowance for the array shading itself.")
