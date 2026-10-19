"""satcad: a beginner-friendly parametric CAD toolkit for spacecraft, drivable by Claude."""
from .engine import Design, DesignError, SCHEMAS
from .materials import MATERIALS
from .power import size_solar_array
from .thermal import size_radiator

__all__ = ["Design", "DesignError", "SCHEMAS", "MATERIALS", "size_radiator", "size_solar_array"]
