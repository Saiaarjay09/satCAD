import pytest

from satcad import Design, DesignError
from satcad.materials import MATERIALS, density, describe_all, get


def test_every_material_has_a_density_color_and_note():
    for m in MATERIALS.values():
        assert m.density > 0 and len(m.color) == 3 and all(0 <= c <= 1 for c in m.color) and m.note


def test_structural_materials_have_a_complete_mechanical_set():
    for m in MATERIALS.values():
        if m.structural:
            assert m.youngs_gpa and 0 < m.poisson < 0.5, m.name
            assert m.cte_ppm_k is not None and m.conductivity_w_mk and m.specific_heat_j_kgk, m.name


def test_values_are_in_plausible_handbook_ranges():
    al, ti, st, inv = (get(n) for n in ("aluminum_6061", "titanium_ti6al4v", "stainless_316", "invar_36"))
    assert 68 <= al.youngs_gpa <= 70 and al.density == 2700 and al.yield_mpa == 276
    assert 110 <= ti.youngs_gpa <= 115 and 190 <= st.youngs_gpa <= 200
    assert inv.cte_ppm_k < 2 < al.cte_ppm_k                         # Invar barely expands: that's why it's used
    assert get("copper").conductivity_w_mk > 300
    # specific stiffness ordering a structures engineer would expect: carbon composite beats aluminium
    spec = lambda m: m.youngs_gpa / m.density
    assert spec(get("cfrp")) > spec(al) and spec(ti) > spec(st) * 0.9


def test_unknown_material_message_lists_choices():
    with pytest.raises(ValueError, match="aluminum_6061"):
        density("unobtainium")
    with pytest.raises(DesignError, match="Unknown material"):
        Design().add("box", "b", length=1, width=1, height=1, material="unobtainium")


def test_describe_all_flags_simplified_models():
    info = describe_all()
    assert "caveat" in info["cfrp"] and "caveat" in info["aluminum_honeycomb"] and "caveat" in info["generic_equipment"]
    assert "caveat" not in info["aluminum_6061"] and info["generic_equipment"]["load_bearing"] is False


def test_new_materials_work_in_designs():
    d = Design()
    d.add("box", "b", length=1000, width=1000, height=1000, material="aluminum_7075")
    assert d.describe("b")["mass_kg"] == pytest.approx(2810.0)
