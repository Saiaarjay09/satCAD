"""
Named variables and formulas, in the spirit of NX "Expressions".

Any numeric setting can be a formula written as a string that starts with "=":

    chassis length   "=2 * bay_pitch + 300"         # uses a variable called bay_pitch
    radiator length  "=(wingspan - chassis.width) / 2"   # uses another part's setting
    mast height      "=chassis.zmax + 500"          # uses the geometry of another part

Change a variable once and everything that depends on it rebuilds. Formulas are evaluated by a small,
safe interpreter (numbers, + - * / // % **, parentheses, a few maths functions). They can never run arbitrary code.

Trigonometric functions work in DEGREES, like NX.
"""
from __future__ import annotations

import ast
import math
import operator

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

FUNCTIONS = {
    "sqrt": math.sqrt, "abs": abs, "min": min, "max": max, "round": round, "floor": math.floor, "ceil": math.ceil,
    "log": math.log, "log10": math.log10, "exp": math.exp,
    "sin": lambda d: math.sin(math.radians(d)), "cos": lambda d: math.cos(math.radians(d)),
    "tan": lambda d: math.tan(math.radians(d)),
    "asin": lambda v: math.degrees(math.asin(v)), "acos": lambda v: math.degrees(math.acos(v)),
    "atan": lambda v: math.degrees(math.atan(v)), "atan2": lambda y, x: math.degrees(math.atan2(y, x)),
    "radians": math.radians, "degrees": math.degrees,
}
CONSTANTS = {"pi": math.pi, "e": math.e}
RESERVED = set(FUNCTIONS) | set(CONSTANTS)


class ExpressionError(ValueError):
    """A formula could not be evaluated. The message says which setting and why."""


def is_expression(value) -> bool:
    return isinstance(value, str) and value.lstrip().startswith("=")


def evaluate(text: str, resolve_name, resolve_attr):
    """
    Evaluate the formula `text` (with its leading "="). `resolve_name(name)` returns a variable's value;
    `resolve_attr(owner, attribute)` returns a part setting or measurement, e.g. ("chassis", "width").
    """
    source = text.lstrip()[1:].strip()
    if not source:
        raise ExpressionError("The formula is empty after the '='.")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError:
        raise ExpressionError(f"I can't read the formula '{source}'. Check brackets and operators.") from None

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            left, right = ev(node.left), ev(node.right)
            try:
                return _BINOPS[type(node.op)](left, right)
            except ZeroDivisionError:
                raise ExpressionError(f"Division by zero in '{source}'.") from None
            except OverflowError:
                raise ExpressionError(f"The number in '{source}' is too large.") from None
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Name):
            if node.id in CONSTANTS:
                return CONSTANTS[node.id]
            return resolve_name(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return resolve_attr(node.value.id, node.attr)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and not node.keywords:
            try:
                return FUNCTIONS[node.func.id](*[ev(a) for a in node.args])
            except (ValueError, TypeError, ZeroDivisionError) as e:
                raise ExpressionError(f"{node.func.id}() failed in '{source}': {e}") from None
        raise ExpressionError(f"'{source}' uses something formulas don't allow. Use numbers, variables, "
                              f"+ - * / ** and: {', '.join(sorted(FUNCTIONS))}.")

    value = ev(tree)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExpressionError(f"'{source}' did not produce a number.")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise ExpressionError(f"'{source}' did not produce a finite number.")
    return value
