"""Named variables and formulas (NX-style expressions)."""
import pytest

from satcad import Design, DesignError
from satcad.expressions import ExpressionError, evaluate


def ev(text, **names):
    return evaluate(text, lambda n: names[n], lambda o, a: (_ for _ in ()).throw(ExpressionError("no attrs here")))


def test_arithmetic_and_precedence():
    assert ev("=2 + 3 * 4") == 14
    assert ev("=(2 + 3) * 4") == 20
    assert ev("=2 ** 3 ** 2") == 512
    assert ev("=-5 + 10 // 3") == -2
    assert ev("= 7 % 4") == 3
    assert ev("=10 / 4") == 2.5


def test_functions_use_degrees():
    assert ev("=sin(30) * 100") == pytest.approx(50)
    assert ev("=cos(60)") == pytest.approx(0.5)
    assert ev("=atan2(1, 1)") == pytest.approx(45)
    assert ev("=sqrt(16) + max(1, 9, 3) + min(4, 2)") == 15
    assert ev("=pi") == pytest.approx(3.14159265)


def test_variables_resolve():
    assert ev("=a * 2 + b", a=10, b=5) == 25


@pytest.mark.parametrize("text", [
    "=__import__('os').system('echo hi')",
    "=open('/etc/passwd')",
    "=(1).__class__",
    "=[x for x in range(3)]",
    "='text'",
    "=lambda: 1",
    "=sqrt(4, key=1)",
    "=a.b.c",
])
def test_formulas_cannot_run_code(text):
    with pytest.raises(ExpressionError):
        ev(text, a=1)


@pytest.mark.parametrize("text, message", [
    ("=1/0", "Division by zero"),
    ("=2 +", "can't read"),
    ("=", "empty"),
    ("=sqrt(-1)", "failed"),
])
def test_clear_errors(text, message):
    with pytest.raises(ExpressionError, match=message):
        ev(text)


def make():
    d = Design("t")
    d.set_variable("bay", 500)
    d.set_variable("wingspan", 20000)
    d.add("chassis", "chassis", length="=bay * 4", width=2000, height=1500, wall=10)
    d.add("panel", "rad", attach_to="chassis", side="+y", length="=(wingspan - chassis.width) / 2", width=3000, thickness=20)
    return d


def test_formulas_drive_geometry():
    d = make()
    assert d.describe("chassis")["size_mm"][0] == 2000
    assert d.describe("rad")["size_mm"][1] == 9000
    assert d.describe("rad")["bbox_max_mm"][1] == pytest.approx(10000)             # tip is half the wingspan from the centre
    assert d.describe("chassis")["expressions"] == {"length": "=bay * 4"}


def test_changing_a_variable_rebuilds_everything_that_depends_on_it():
    d = make()
    d.set_variable("wingspan", 30000)
    assert d.describe("rad")["bbox_max_mm"][1] == pytest.approx(15000)
    d.set_variable("bay", 800)
    assert d.describe("chassis")["size_mm"][0] == 3200


def test_formulas_can_read_another_parts_geometry():
    d = make()
    d.add("cylinder", "mast", radius=100, height=1000, x="=chassis.xmax + 50", z="=chassis.zmax + mast_h/2" if False else 0)
    assert d.describe("mast")["centre_mm"][0] == pytest.approx(1050)
    d.edit("chassis", length=3000)
    assert d.describe("mast")["centre_mm"][0] == pytest.approx(1550)                # follows the chassis automatically


def test_variables_can_refer_to_other_variables():
    d = Design()
    d.set_variable("a", 100)
    d.set_variable("b", "=a * 3")
    d.set_variable("c", "=b + a")
    d.add("box", "x", length="=c", width=10, height=10)
    assert d.describe("x")["size_mm"][0] == 400
    d.set_variable("a", 200)
    assert d.describe("x")["size_mm"][0] == 800
    assert d.list_variables()["c"]["value"] == 800


def test_expression_inside_point_lists():
    d = Design()
    d.set_variable("w", 80)
    r = d.add("extrude", "tri", profile="polygon", points=[[0, 0], ["=w", 0], [0, "=w"]], depth=10)
    assert r["volume_cm3"] == pytest.approx(0.5 * 80 * 80 * 10 / 1000)


def test_save_and_load_keep_formulas_and_variables(tmp_path):
    d = make()
    path = d.save(tmp_path / "d.json")
    e = Design()
    e.load(path)
    assert e.variables == d.variables and e.features["chassis"].raw["length"] == "=bay * 4"
    e.set_variable("bay", 1000)
    assert e.describe("chassis")["size_mm"][0] == 4000
    assert d.measure()["total_mass_kg"] == pytest.approx(make().measure()["total_mass_kg"])


def test_old_format_files_still_load(tmp_path):
    import json
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"format": "satcad-1", "name": "old", "units": "mm",
                             "features": [{"name": "b", "kind": "box", "params": {"length": 10, "width": 10, "height": 10}}]}))
    d = Design()
    d.load(p)
    assert d.describe("b")["volume_cm3"] == 1.0


def test_unknown_forward_and_circular_references_are_explained():
    d = Design()
    with pytest.raises(DesignError, match="Unknown name 'nope'"):
        d.add("box", "b", length="=nope", width=1, height=1)
    d.add("box", "a", length=10, width=10, height=10)
    with pytest.raises(DesignError, match="no part called 'ghost'"):
        d.add("box", "c", length="=ghost.width", width=1, height=1)
    with pytest.raises(DesignError, match="no number called 'bogus'"):
        d.add("box", "c", length="=a.bogus", width=1, height=1)
    d.set_variable("p", 5)
    with pytest.raises(DesignError, match="refers to itself"):
        d.set_variable("p", "=p + 1")
    assert d.variables["p"] == 5                                                    # failed change rolled back
    with pytest.raises(DesignError, match="is a part"):
        d.add("box", "d", length="=a", width=1, height=1)


def test_a_part_cannot_use_a_part_defined_after_it():
    d = Design()
    d.add("box", "first", length=10, width=10, height=10)
    d.add("box", "second", length="=first.length * 2", width=10, height=10)
    with pytest.raises(DesignError, match="defined after"):
        d.edit("first", length="=second.length")


def test_variable_safety_rules():
    d = Design()
    d.add("box", "thing", length=1, width=1, height=1)
    for bad in ("1abc", "has space", "pi", "sqrt", ""):
        with pytest.raises(DesignError):
            d.set_variable(bad, 1)
    with pytest.raises(DesignError, match="already a part name"):
        d.set_variable("thing", 1)
    with pytest.raises(DesignError, match="number, or a formula"):
        d.set_variable("v", "text")
    with pytest.raises(DesignError, match="only use other variables"):
        d.set_variable("v", "=thing.length")


def test_cannot_delete_a_variable_that_is_in_use():
    d = make()
    with pytest.raises(DesignError, match="used by"):
        d.delete_variable("bay")
    d.set_variable("unused", 1)
    d.delete_variable("unused")
    assert "unused" not in d.variables


def test_cannot_remove_a_part_other_formulas_depend_on():
    d = make()
    with pytest.raises(DesignError, match="attached to it or use it"):
        d.remove("chassis")
    d.add("box", "ref", length=10, width=10, height=10)
    d.add("box", "user", length="=ref.length + 1", width=10, height=10)
    with pytest.raises(DesignError, match="attached to it or use it"):
        d.remove("ref")
    d.remove("user")
    d.remove("ref")
    assert "ref" not in d.parts


def test_a_bad_formula_result_is_caught_by_validation_and_rolled_back():
    d = make()
    with pytest.raises(DesignError, match="greater than 0"):
        d.set_variable("bay", -5)                                                   # would make the chassis length negative
    assert d.variables["bay"] == 500 and d.describe("chassis")["size_mm"][0] == 2000
