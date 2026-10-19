"""
Materials: density for mass, plus the mechanical and thermal properties that structural and thermal analysis need.

IMPORTANT: the numbers are TYPICAL room-temperature handbook values, good for concept design and first-order analysis.
They are NOT certified design allowables. For anything you will rely on, use your supplier's certified data or a
published allowables source (for example MMPDS A-basis / B-basis values) and temperature-dependent properties.

Units: density kg/m^3, Young's modulus GPa, strengths MPa, thermal expansion 1e-6 per K, conductivity W/(m K),
specific heat J/(kg K).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Material:
    name: str
    density: float
    color: tuple
    note: str
    youngs_gpa: float | None = None
    poisson: float | None = None
    yield_mpa: float | None = None
    ultimate_mpa: float | None = None
    cte_ppm_k: float | None = None
    conductivity_w_mk: float | None = None
    specific_heat_j_kgk: float | None = None
    structural: bool = True          # False: carries mass but is not a load-bearing material (equipment stand-ins)
    caveat: str = ""                 # shown when a result depends on a simplified material model


_M = Material
MATERIALS: dict[str, Material] = {m.name: m for m in (
    _M("aluminum_6061", 2700.0, (0.72, 0.74, 0.78), "6061-T6 aluminium. General-purpose spacecraft structure: light, easy to machine and weld.",
       68.9, 0.33, 276.0, 310.0, 23.6, 167.0, 896.0),
    _M("aluminum_7075", 2810.0, (0.70, 0.72, 0.80), "7075-T6 aluminium. Much stronger than 6061, used for highly loaded fittings; harder to weld.",
       71.7, 0.33, 503.0, 572.0, 23.4, 130.0, 960.0),
    _M("titanium_ti6al4v", 4430.0, (0.55, 0.57, 0.60), "Ti-6Al-4V. Strong, stiff and heat-tolerant; heavier and costlier than aluminium.",
       113.8, 0.342, 880.0, 950.0, 8.6, 6.7, 526.0),
    _M("stainless_316", 8000.0, (0.62, 0.64, 0.66), "316 stainless steel (annealed). Tough and corrosion-resistant, but heavy.",
       193.0, 0.27, 290.0, 580.0, 16.0, 16.3, 500.0),
    _M("inconel_718", 8190.0, (0.50, 0.52, 0.56), "Inconel 718 (aged). Very strong at high temperature; used for engine parts and hot fasteners.",
       200.0, 0.29, 1034.0, 1241.0, 13.0, 11.4, 435.0),
    _M("invar_36", 8050.0, (0.58, 0.60, 0.62), "Invar 36. Almost no thermal expansion, used where optics must hold alignment as temperature changes.",
       141.0, 0.26, 276.0, 490.0, 1.3, 10.5, 515.0),
    _M("copper", 8960.0, (0.80, 0.50, 0.30), "Oxygen-free copper. Excellent heat conductor for heat spreaders; soft and heavy.",
       117.0, 0.34, 70.0, 220.0, 17.0, 391.0, 385.0),
    _M("cfrp", 1600.0, (0.18, 0.18, 0.20), "Carbon-fibre / epoxy composite, APPROXIMATED as an isotropic quasi-isotropic laminate. Very stiff for its weight.",
       55.0, 0.30, None, 500.0, 2.0, 5.0, 800.0,
       caveat="Real composites are directional and depend on the ply layup; these are rough quasi-isotropic values. "
              "Use ply-level data for any result you rely on."),
    _M("aluminum_honeycomb", 100.0, (0.95, 0.95, 0.97),
       "EFFECTIVE properties of an aluminium-honeycomb sandwich panel (thin skins over a hollow core). Very light for its stiffness.",
       5.0, 0.30, None, None, 23.0, 3.0, 900.0,
       caveat="A sandwich panel is not an isotropic material. The stiffness here (about 5 GPa) is an equivalent bending value "
              "for a panel around 40 mm thick with 0.5 mm aluminium skins, and the density is the effective average. "
              "It is only indicative for other build-ups."),
    _M("generic_equipment", 1500.0, (0.35, 0.55, 0.80), "Placeholder for equipment-filled volumes (rough average density). Carries mass, not load.",
       None, None, None, None, None, None, None, structural=False,
       caveat="Equipment stand-ins add mass but are treated as non-load-bearing in structural analysis."),
)}

# Colour per role, used when a part does not say otherwise.
ROLE_COLORS: dict[str, tuple[float, float, float]] = {
    "structure": (0.72, 0.74, 0.78),
    "chassis": (0.62, 0.66, 0.72),
    "radiator": (0.95, 0.55, 0.20),
    "solar": (0.15, 0.25, 0.60),
    "payload": (0.35, 0.60, 0.35),
    "antenna": (0.85, 0.85, 0.30),
}


def get(material: str) -> Material:
    if material not in MATERIALS:
        raise ValueError(f"Unknown material '{material}'. Choose one of: {', '.join(MATERIALS)}.")
    return MATERIALS[material]


def density(material: str) -> float:
    return get(material).density


def describe_all() -> dict:
    """Every material with its properties, for tools and docs."""
    out = {}
    for m in MATERIALS.values():
        out[m.name] = dict(density_kg_m3=m.density, youngs_modulus_gpa=m.youngs_gpa, poisson_ratio=m.poisson,
                           yield_mpa=m.yield_mpa, ultimate_mpa=m.ultimate_mpa, thermal_expansion_ppm_per_k=m.cte_ppm_k,
                           conductivity_w_per_m_k=m.conductivity_w_mk, specific_heat_j_per_kg_k=m.specific_heat_j_kgk,
                           load_bearing=m.structural, note=m.note, **({"caveat": m.caveat} if m.caveat else {}))
    return out
