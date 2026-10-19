"""Materials used for mass estimates. Densities are in kg/m^3."""
from __future__ import annotations

# name -> (density kg/m^3, display colour RGB 0..1, plain-English note)
MATERIALS: dict[str, tuple[float, tuple[float, float, float], str]] = {
    "aluminum_6061": (2700.0, (0.72, 0.74, 0.78), "General-purpose spacecraft structure. Light, easy to machine."),
    "aluminum_honeycomb": (100.0, (0.95, 0.95, 0.97),
                           "Rough EFFECTIVE density of an aluminium-honeycomb sandwich panel (thin skins over a hollow core). "
                           "Very light for its stiffness; the real value depends on the panel build-up."),
    "titanium_ti6al4v": (4430.0, (0.55, 0.57, 0.60), "Strong and heat-tolerant, heavier and costlier than aluminium."),
    "stainless_316": (8000.0, (0.62, 0.64, 0.66), "Tough and corrosion-resistant, but heavy."),
    "cfrp": (1600.0, (0.18, 0.18, 0.20), "Carbon-fibre composite. Very stiff for its weight."),
    "copper": (8960.0, (0.80, 0.50, 0.30), "Excellent heat conductor, used for heat spreaders. Heavy."),
    "generic_equipment": (1500.0, (0.35, 0.55, 0.80), "Placeholder for equipment-filled volumes (rough average)."),
}

# Colour per role, used when a part does not say otherwise.
ROLE_COLORS: dict[str, tuple[float, float, float]] = {
    "structure": (0.72, 0.74, 0.78),
    "chassis": (0.62, 0.66, 0.72),
    "radiator": (0.95, 0.55, 0.20),
    "solar": (0.15, 0.25, 0.60),
    "payload": (0.35, 0.60, 0.35),
    "antenna": (0.85, 0.85, 0.30),
}


def density(material: str) -> float:
    if material not in MATERIALS:
        raise ValueError(f"Unknown material '{material}'. Choose one of: {', '.join(MATERIALS)}.")
    return MATERIALS[material][0]
