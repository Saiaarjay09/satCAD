"""Desmos-style symbolic expression engine and sidebar for Motion Studio.

Type functions of time the way you would in Desmos:

    theta_solar(t) = 360 * (t / 5400)
    az_laser(t)    = A * sin(2 * pi * f * t)
    omega          = 2 * pi * 0.5            <- a constant: any name without (t)
    wave(t, k)     = sin(2*pi*(f*t - k/24))  <- a second argument k (a row index) is allowed

Any name you use but never define (A and f above) becomes a SLIDER right under the equation. SymPy parses and differentiates the
formulas; velocity v(t) = dx/dt and acceleration a(t) = d2x/dt2 come from exact symbolic derivatives, compiled to NumPy for speed.

Units: angles in DEGREES (trig functions take radians, as in Desmos, so write sin(2*pi*f*t)); time in seconds; lengths in mm.
Helpers: sigmoid(x), smoothstep(x) (0 below 0, 1 above 1, S-curve between), smootherstep, clamp(x, lo, hi), step(x), sind/cosd/tand (degrees),
min, max, abs, sign, floor, ceil, mod, sqrt, exp, log (natural), asin/acos/atan/atan2, sinh/cosh/tanh, constants pi and e.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import numpy as np
import sympy as sp
from sympy.parsing.sympy_parser import convert_xor, parse_expr, standard_transformations

TRANSFORMS = standard_transformations + (convert_xor,)
_x, _lo, _hi = sp.symbols("x lo hi")
_PI180 = sp.pi / 180
SAFE = {
    "sin": sp.sin, "cos": sp.cos, "tan": sp.tan, "asin": sp.asin, "acos": sp.acos, "atan": sp.atan, "atan2": sp.atan2,
    "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh, "exp": sp.exp, "log": sp.log, "ln": sp.log, "sqrt": sp.sqrt, "abs": sp.Abs,
    "sign": sp.sign, "floor": sp.floor, "ceil": sp.ceiling, "min": sp.Min, "max": sp.Max, "mod": sp.Mod, "pi": sp.pi, "e": sp.E,
    "sigmoid": sp.Lambda(_x, 1 / (1 + sp.exp(-_x))),
    "smoothstep": sp.Lambda(_x, sp.Piecewise((0, _x < 0), (1, _x > 1), (3 * _x ** 2 - 2 * _x ** 3, True))),
    "smootherstep": sp.Lambda(_x, sp.Piecewise((0, _x < 0), (1, _x > 1), (6 * _x ** 5 - 15 * _x ** 4 + 10 * _x ** 3, True))),
    "clamp": sp.Lambda((_x, _lo, _hi), sp.Min(sp.Max(_x, _lo), _hi)),
    "step": sp.Lambda(_x, sp.Piecewise((0, _x < 0), (1, True))),
    "sind": sp.Lambda(_x, sp.sin(_x * _PI180)), "cosd": sp.Lambda(_x, sp.cos(_x * _PI180)), "tand": sp.Lambda(_x, sp.tan(_x * _PI180)),
}
# what the parser itself needs in order to build numbers and symbols (never exposes anything else)
_PARSER_NEEDS = {"Integer": sp.Integer, "Float": sp.Float, "Rational": sp.Rational, "Symbol": sp.Symbol, "Function": sp.Function,
                 "factorial": sp.factorial, "Pow": sp.Pow, "Mul": sp.Mul, "Add": sp.Add, "Tuple": sp.Tuple, "Dummy": sp.Dummy}
RESERVED_NAMES = set(SAFE) | {"t"}
_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_DEF = re.compile(rf"^\s*({_NAME})\s*(?:\(\s*({_NAME})\s*(?:,\s*({_NAME})\s*)?\))?\s*=\s*(.+?)\s*$")
_ALLOWED = re.compile(r"^[A-Za-z0-9_+\-*/^(),.\s]+$")
_WORD = re.compile(_NAME)
MAX_TERMS = 4000


class ExpressionError(ValueError):
    """A formula the engine could not use; the message is written for the person who typed it."""


@dataclass
class Equation:
    line: str
    name: str = ""
    args: tuple = ()
    rhs: str = ""
    expr: object = None                 # SymPy expression (None if the line has an error)
    error: str = ""
    params: tuple = ()                  # undefined symbols this equation uses (they become sliders)

    @property
    def is_function(self) -> bool:
        return bool(self.args)

    @property
    def ok(self) -> bool:
        return self.expr is not None and not self.error


def _check_text(rhs: str) -> None:
    if not _ALLOWED.match(rhs):
        bad = sorted({c for c in rhs if not re.match(r"[A-Za-z0-9_+\-*/^(),.\s]", c)})
        raise ExpressionError(f"Unsupported character(s) {' '.join(repr(c) for c in bad)}. Use letters, numbers, + - * / ^ ( ) and commas.")
    if "__" in rhs or re.search(r"\.\s*[A-Za-z_]", rhs):
        raise ExpressionError("Names with double underscores or attributes (a.b) are not allowed.")
    if rhs.count("(") != rhs.count(")"):
        raise ExpressionError("The brackets do not match.")


class ExpressionEngine:
    def __init__(self):
        self.equations: list[Equation] = []
        self.param_values: dict[str, float] = {}
        self.param_ranges: dict[str, tuple] = {}
        self.version = 0                                # bumps when the set of equations changes
        self._compiled: dict[str, tuple] = {}
        self._param_order: list[str] = []

    # ---- equations -------------------------------------------------------------------------------------------------------------------
    def set_equations(self, lines: list[str]) -> list[Equation]:
        """Replace all equations. Lines may reference each other in any order; errors are reported per line and do not stop the others."""
        records: list[Equation] = []
        for line in lines:
            if not line.strip() or line.strip().startswith("#"):
                records.append(Equation(line))
                continue
            m = _DEF.match(line)
            eq = Equation(line)
            if not m:
                eq.error = "Write it as  name(t) = formula  or  name = formula."
            else:
                name, a1, a2, rhs = m.group(1), m.group(2), m.group(3), m.group(4)
                eq.name, eq.args, eq.rhs = name, tuple(a for a in (a1, a2) if a), rhs
                if name in RESERVED_NAMES or name in ("t",):
                    eq.error = f"'{name}' is a built-in name. Choose another."
                elif eq.args and len(set(eq.args)) != len(eq.args):
                    eq.error = "The two arguments must have different names."
                elif eq.args and eq.args[0] != "t":
                    eq.error = "The first argument must be t (time): write name(t) = ... or name(t, k) = ..."
                elif name in eq.args:
                    eq.error = "A function cannot have the same name as its argument."
            records.append(eq)
        defined: dict[str, Equation] = {}
        for eq in records:
            if eq.name and not eq.error:
                if eq.name in defined:
                    eq.error = f"'{eq.name}' is defined twice."
                else:
                    defined[eq.name] = eq
        deps = {n: {w for w in _WORD.findall(e.rhs) if w in defined and w != n and w not in e.args} for n, e in defined.items()}
        order, state = [], {}

        def visit(n, path):
            if state.get(n) == 2:
                return
            if state.get(n) == 1:
                for c in path[path.index(n):]:
                    defined[c].error = "This formula refers back to itself (circular definition)."
                return
            state[n] = 1
            for d in sorted(deps[n]):
                visit(d, path + [n])
            state[n] = 2
            order.append(n)
        for n in defined:
            visit(n, [])
        local: dict = {}
        for n in order:
            eq = defined[n]
            if eq.error or any(defined[d].error for d in deps[n]):
                eq.error = eq.error or "This formula uses another formula that has an error."
                continue
            try:
                _check_text(eq.rhs)
                scope = dict(local)
                for a in eq.args:
                    scope[a] = sp.Symbol(a)
                expr = parse_expr(eq.rhs, local_dict=scope, global_dict={**_PARSER_NEEDS, **SAFE, "__builtins__": {}}, transformations=TRANSFORMS,
                                  evaluate=True)
                if not isinstance(expr, sp.Basic):
                    expr = sp.sympify(expr)
                undefined = sorted({str(a.func) for a in expr.atoms(sp.core.function.AppliedUndef)})
                if undefined:
                    if n in undefined:
                        raise ExpressionError("This formula refers back to itself (circular definition).")
                    raise ExpressionError(f"Unknown function '{undefined[0]}'. Define it first (name(t) = ...) or use one of the built-in functions.")
                if sp.count_ops(expr) > MAX_TERMS:
                    raise ExpressionError("That formula is too large.")
                if expr.has(sp.zoo, sp.nan, sp.oo, -sp.oo):
                    raise ExpressionError("That formula is not a finite number (check for division by zero).")
                arg_syms = tuple(sp.Symbol(a) for a in eq.args)
                eq.expr = expr
                eq.params = tuple(sorted((str(s) for s in expr.free_symbols if s not in arg_syms), key=str))
                local[n] = sp.Lambda(arg_syms, expr) if arg_syms else expr
            except ExpressionError as e:
                eq.error = str(e)
            except (SyntaxError, TypeError, ValueError, AttributeError, NameError, ZeroDivisionError, sp.SympifyError, TokenError) as e:
                eq.error = _friendly(e, eq.rhs)
        self.equations = records
        self._param_order = []
        for eq in records:
            for p in eq.params:
                if p not in self._param_order and eq.ok:
                    self._param_order.append(p)
        for p in self._param_order:
            self.param_values.setdefault(p, 1.0)
        self.version += 1
        self._compile()
        return records

    def lines(self) -> list[str]:
        return [e.line for e in self.equations]

    # ---- parameters ------------------------------------------------------------------------------------------------------------------
    @property
    def parameters(self) -> list[str]:
        """Names used in formulas but never defined, in order of first use: Desmos would make a slider for each."""
        return list(self._param_order)

    def set_param(self, name: str, value: float) -> None:
        self.param_values[name] = float(value)

    def get_param(self, name: str) -> float:
        return float(self.param_values.get(name, 1.0))

    def owner_of(self, param: str) -> int | None:
        """Index of the first equation that uses this parameter (the slider is shown under it)."""
        for i, e in enumerate(self.equations):
            if e.ok and param in e.params:
                return i
        return None

    # ---- numerics --------------------------------------------------------------------------------------------------------------------
    def _compile(self) -> None:
        """Compile x, dx/dt and d2x/dt2 of every function to NumPy. Parameters are call arguments, so a slider move needs no recompiling."""
        self._compiled = {}
        syms = [sp.Symbol(p) for p in self._param_order]
        mods = [{"DiracDelta": lambda x: 0.0 * np.asarray(x, dtype=float), "Heaviside": lambda x, h=0.5: np.heaviside(x, h)}, "numpy"]
        for eq in self.equations:
            if not eq.ok or not eq.name:
                continue
            args = [sp.Symbol(a) for a in eq.args]
            try:
                f0 = eq.expr
                t = args[0] if args else None
                f1 = sp.diff(f0, t) if t is not None else sp.Integer(0)
                f2 = sp.diff(f1, t) if t is not None else sp.Integer(0)
                fns = tuple(sp.lambdify([*args, *syms], f, modules=mods) for f in (f0, f1, f2))
                self._compiled[eq.name] = (len(args), fns)
            except Exception as e:                       # a derivative SymPy could not express: still usable for the position
                try:
                    f0c = sp.lambdify([*args, *syms], eq.expr, modules=mods)
                    self._compiled[eq.name] = (len(args), (f0c, None, None))
                except Exception:
                    eq.error = f"Could not compile: {e}"

    def function_names(self, arity: int | None = None) -> list[str]:
        out = []
        for e in self.equations:
            if e.ok and e.name and e.is_function and (arity is None or len(e.args) == arity):
                out.append(e.name)
        return out

    def has(self, name: str) -> bool:
        return name in self._compiled

    def arity(self, name: str) -> int:
        return self._compiled[name][0]

    def _call(self, name: str, order: int, t, k=None):
        if name not in self._compiled:
            raise ExpressionError(f"There is no formula called '{name}'.")
        arity, fns = self._compiled[name]
        fn = fns[order]
        if fn is None:
            return self._numeric_derivative(name, order, t, k)
        t = np.asarray(t, dtype=float) if np.ndim(t) else float(t)
        k = np.asarray(0.0 if k is None else k, dtype=float) if np.ndim(k) else float(0.0 if k is None else k)
        p = [self.param_values.get(s, 1.0) for s in self._param_order]
        args = [] if arity == 0 else [t] if arity == 1 else [t, k]
        out = np.asarray(fn(*args, *p), dtype=float)
        shape = np.broadcast(np.asarray(t), np.asarray(k)).shape if arity == 2 else np.shape(t)
        if out.ndim == 0 and shape != ():
            out = np.broadcast_to(out, shape).copy()
        return float(out) if out.ndim == 0 else out

    def _numeric_derivative(self, name, order, t, k):
        h = 1e-3
        f = lambda x: self._call(name, 0, x, k)
        if order == 1:
            return (f(np.asarray(t) + h) - f(np.asarray(t) - h)) / (2 * h)
        return (f(np.asarray(t) + h) - 2 * f(t) + f(np.asarray(t) - h)) / h ** 2

    def value(self, name: str, t, k=None):
        """x(t)."""
        return self._call(name, 0, t, k)

    def velocity(self, name: str, t, k=None):
        """v(t) = dx/dt."""
        return self._call(name, 1, t, k)

    def acceleration(self, name: str, t, k=None):
        """a(t) = d2x/dt2."""
        return self._call(name, 2, t, k)

    def sample(self, name: str, t, k=None) -> dict:
        t = np.asarray(t, dtype=float)
        return dict(t=t, x=np.atleast_1d(self.value(name, t, k)) * np.ones_like(t), v=np.atleast_1d(self.velocity(name, t, k)) * np.ones_like(t),
                    a=np.atleast_1d(self.acceleration(name, t, k)) * np.ones_like(t))

    def symbolic(self, name: str) -> tuple:
        """(x, dx/dt, d2x/dt2) as SymPy expressions, for display."""
        for e in self.equations:
            if e.ok and e.name == name and e.args:
                t = sp.Symbol(e.args[0])
                d1 = sp.diff(e.expr, t)
                return e.expr, d1, sp.diff(d1, t)
        raise ExpressionError(f"There is no formula called '{name}'.")

    # ---- saving ----------------------------------------------------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return dict(equations=self.lines(), params=dict(self.param_values), ranges={k: list(v) for k, v in self.param_ranges.items()})

    def load_dict(self, d: dict) -> None:
        self.param_values = {k: float(v) for k, v in (d.get("params") or {}).items()}
        self.param_ranges = {k: tuple(v) for k, v in (d.get("ranges") or {}).items()}
        self.set_equations(list(d.get("equations") or []))


try:
    from tokenize import TokenError
except ImportError:                                      # pragma: no cover
    TokenError = SyntaxError


def _friendly(e: Exception, rhs: str) -> str:
    text = str(e)
    if isinstance(e, ZeroDivisionError):
        return "Division by zero."
    if isinstance(e, (SyntaxError, TokenError)):
        return "That formula could not be read. Check the brackets and operators (use * for multiplication, ^ or ** for powers)."
    if "not callable" in text or "object is not callable" in text:
        return "A name followed by brackets was used as a function but it is not one (write 2*x, not 2(x))."
    if isinstance(e, NameError):
        return f"Unknown name: {text}."
    return f"That formula could not be used: {text[:120]}"


# ==========================================================================================================
#  The sidebar (Qt)
# ==========================================================================================================
from PySide6.QtCore import QTimer, Qt, Signal  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDockWidget, QDoubleSpinBox, QFormLayout, QHBoxLayout,  # noqa: E402
                               QLabel, QLineEdit, QPushButton, QScrollArea, QSlider, QTableWidget, QTableWidgetItem, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget, QDialogButtonBox)

from .kinematics_solver import JOINT_KINDS, Joint, KinematicsError  # noqa: E402

HELP = ("Type formulas of time t, Desmos-style, e.g.  theta(t) = 360 * (t / 5400)  or  az(t) = A * sin(2*pi*f*t).  "
        "Names you use but do not define (A, f) get sliders. Angles are in degrees.")


class ParamSlider(QWidget):
    """Desmos-style slider for one undefined constant. Top row: the name and an exact value box; bottom row: lower end, slider, upper end
    (both ends editable)."""
    changed = Signal(str, float)
    range_changed = Signal(str, float, float)

    def __init__(self, name: str, value: float, lo: float, hi: float):
        super().__init__()
        self.name = name
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 2, 4, 4)
        lay.setSpacing(2)
        top = QHBoxLayout()
        label = QLabel(name)
        label.setStyleSheet("color:#3d8bfd; font-style:italic; font-weight:600;")
        self.value = QDoubleSpinBox()
        self.value.setRange(-1e9, 1e9)
        self.value.setDecimals(4)
        self.value.setMinimumWidth(120)
        self.value.setValue(value)
        top.addWidget(label)
        top.addStretch(1)
        top.addWidget(self.value)
        lay.addLayout(top)
        self.lo, self.hi = QDoubleSpinBox(), QDoubleSpinBox()
        for box, v in ((self.lo, lo), (self.hi, hi)):
            box.setRange(-1e9, 1e9)
            box.setDecimals(3)
            box.setValue(v)
            box.setMinimumWidth(92)
            box.setToolTip("slider end (editable)")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        lay.addWidget(self.slider)                                  # the slider gets the full width
        ends = QHBoxLayout()
        ends.addWidget(QLabel("from"))
        ends.addWidget(self.lo)
        ends.addStretch(1)
        ends.addWidget(QLabel("to"))
        ends.addWidget(self.hi)
        lay.addLayout(ends)
        self._sync_slider()
        self.slider.valueChanged.connect(self._slider_moved)
        self.value.valueChanged.connect(self._value_typed)
        self.lo.editingFinished.connect(self._range_edited)
        self.hi.editingFinished.connect(self._range_edited)

    def _sync_slider(self) -> None:
        lo, hi, v = self.lo.value(), self.hi.value(), self.value.value()
        if hi <= lo:
            hi = lo + 1.0
            self.hi.setValue(hi)
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(1000 * (min(max(v, lo), hi) - lo) / (hi - lo))))
        self.slider.blockSignals(False)

    def _slider_moved(self, pos: int) -> None:
        lo, hi = self.lo.value(), self.hi.value()
        v = lo + (hi - lo) * pos / 1000.0
        self.value.blockSignals(True)
        self.value.setValue(v)
        self.value.blockSignals(False)
        self.changed.emit(self.name, v)

    def _value_typed(self, v: float) -> None:
        if v < self.lo.value() or v > self.hi.value():             # typed outside the ends: widen them, like Desmos
            span = max(abs(v) * 2, 1.0)
            self.lo.setValue(min(self.lo.value(), -span if v < 0 else 0.0 if self.lo.value() >= 0 else self.lo.value()))
            self.hi.setValue(max(self.hi.value(), span if v > 0 else self.hi.value()))
            self.range_changed.emit(self.name, self.lo.value(), self.hi.value())
        self._sync_slider()
        self.changed.emit(self.name, v)

    def _range_edited(self) -> None:
        self._sync_slider()
        self.range_changed.emit(self.name, self.lo.value(), self.hi.value())


class EquationRow(QWidget):
    edited = Signal()
    delete_requested = Signal(object)

    def __init__(self, text: str = ""):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(1)
        top = QHBoxLayout()
        self.edit = QLineEdit(text)
        f = QFont("Menlo")
        f.setStyleHint(QFont.Monospace)
        self.edit.setFont(f)
        self.edit.setPlaceholderText("name(t) = formula")
        self.edit.textEdited.connect(lambda _t: self.edited.emit())
        delete = QToolButton()
        delete.setText("×")
        delete.setToolTip("Delete this expression")
        delete.clicked.connect(lambda: self.delete_requested.emit(self))
        top.addWidget(self.edit, 1)
        top.addWidget(delete)
        lay.addLayout(top)
        self.status = QLabel("")
        self.status.setContentsMargins(4, 0, 0, 0)
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.sliders = QVBoxLayout()
        self.sliders.setSpacing(1)
        lay.addLayout(self.sliders)

    def clear_sliders(self) -> None:
        while self.sliders.count():
            w = self.sliders.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()


class EquationsPanel(QWidget):
    def __init__(self, studio):
        super().__init__()
        self.studio = studio
        self.rows: list[EquationRow] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(220)
        self._timer.timeout.connect(self._commit)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        hint = QLabel(HELP)
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#9aa2ad")
        outer.addWidget(hint)
        self.body = QVBoxLayout()
        self.body.setAlignment(Qt.AlignTop)
        holder = QWidget()
        holder.setLayout(self.body)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        add = QPushButton("+ Expression")
        add.clicked.connect(lambda: self.add_row(""))
        preset = QPushButton("Alpha presets")
        preset.setToolTip("Load the Orbital-Node-Alpha mechanisms: solar tracking, radiator fold, laser turrets, coolant pulse")
        preset.clicked.connect(studio.load_alpha_presets)
        buttons.addWidget(add)
        buttons.addWidget(preset)
        outer.addLayout(buttons)
        studio.model_changed.connect(self._on_model_changed)
        studio.time_changed.connect(self._update_values)
        self.rebuild()

    def rebuild(self) -> None:
        for r in self.rows:
            r.setParent(None)
            r.deleteLater()
        self.rows = []
        for line in self.studio.engine.lines():
            self.add_row(line, commit=False)
        if not self.rows:
            self.add_row("", commit=False)
        self._refresh_status()

    def add_row(self, text: str, commit: bool = True) -> EquationRow:
        row = EquationRow(text)
        row.edited.connect(self._timer.start)
        row.delete_requested.connect(self._delete)
        self.rows.append(row)
        self.body.addWidget(row)
        if commit:
            row.edit.setFocus()
        return row

    def _delete(self, row: EquationRow) -> None:
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._commit()

    def _commit(self) -> None:
        self._editing = True
        self.studio.set_equations([r.edit.text() for r in self.rows])
        self._editing = False

    def _on_model_changed(self) -> None:
        if getattr(self, "_editing", False):
            self._refresh_status()
        elif [r.edit.text() for r in self.rows] != self.studio.engine.lines():
            self.rebuild()                                           # changed from outside (a preset or a loaded file)
        else:
            self._refresh_status()

    def _refresh_status(self) -> None:
        eng = self.studio.engine
        for i, row in enumerate(self.rows):
            row.clear_sliders()
            eq = eng.equations[i] if i < len(eng.equations) else None
            if eq is None or (not eq.line.strip()):
                row.status.setText("")
                continue
            if eq.error:
                row.status.setText(eq.error)
                row.status.setStyleSheet("color:#ff6b6b")
                continue
            row.status.setStyleSheet("color:#9aa2ad")
            row.status.setText("")
            for p in eng.parameters:
                if eng.owner_of(p) == i:
                    v = eng.get_param(p)
                    lo, hi = eng.param_ranges.get(p) or ((-10.0, 10.0) if abs(v) <= 10 else (0.0, max(2 * abs(v), 10.0)))
                    s = ParamSlider(p, v, lo, hi)
                    s.changed.connect(self.studio.set_parameter)
                    s.range_changed.connect(lambda n, a, b: eng.param_ranges.__setitem__(n, (a, b)))
                    row.sliders.addWidget(s)
        self._update_values(self.studio.t)

    def _update_values(self, t: float) -> None:
        if self.studio.playing:                                      # readouts refresh ~10 times a second during playback
            now = time.perf_counter()
            if now - getattr(self, "_last_ui", 0.0) < 0.1:
                return
            self._last_ui = now
        eng = self.studio.engine
        for i, row in enumerate(self.rows):
            if i >= len(eng.equations) or not eng.equations[i].ok or not eng.equations[i].name:
                continue
            eq = eng.equations[i]
            try:
                if not eq.args:
                    row.status.setText(f"= {float(eng.value(eq.name, 0.0)):.6g}")
                elif len(eq.args) == 1:
                    x, v, a = (float(eng.value(eq.name, t)), float(eng.velocity(eq.name, t)), float(eng.acceleration(eq.name, t)))
                    row.status.setText(f"t = {t:.2f}   x = {x:.4g}   v = {v:.4g}   a = {a:.4g}")
                else:
                    row.status.setText(f"function of (t, k): at t = {t:.2f}, k = 0: {float(eng.value(eq.name, t, 0.0)):.4g}")
            except Exception:
                pass


class JointDialog(QDialog):
    """Create or edit a joint binding."""

    def __init__(self, parent, studio, kind: str = "revolute", joint: Joint | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{kind.capitalize()} joint" if joint is None else f"Edit {joint.name}")
        self.setMinimumWidth(420)
        self.studio = studio
        d = studio.session.design
        assemblies = [n for n, f in d.features.items() if f.kind == "assembly"]
        funcs = studio.engine.function_names(1)
        j = joint or Joint("", kind, "")
        form = QFormLayout()
        self.name = QLineEdit(j.name or f"{kind.capitalize()}_{len(studio.joints) + 1}")
        self.kind = QComboBox()
        self.kind.addItems(JOINT_KINDS)
        self.kind.setCurrentText(j.kind)
        self.assembly = QComboBox()
        self.assembly.addItems(assemblies)
        sel = studio.session.selected
        if j.assembly in assemblies:
            self.assembly.setCurrentText(j.assembly)
        elif sel in assemblies:
            self.assembly.setCurrentText(sel)
        self.axis = QLineEdit(", ".join(f"{v:g}" for v in j.axis))
        self.pivot = QLineEdit(", ".join(f"{v:g}" for v in j.pivot))
        self.expr = QComboBox()
        self.expr.setEditable(True)
        self.expr.addItems([""] + funcs)
        self.expr.setCurrentText(j.expr)
        self.axis2 = QLineEdit(", ".join(f"{v:g}" for v in j.axis2))
        self.expr2 = QComboBox()
        self.expr2.setEditable(True)
        self.expr2.addItems([""] + funcs)
        self.expr2.setCurrentText(j.expr2)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-1e6, 1e6)
        self.offset.setValue(j.offset)
        form.addRow("Name", self.name)
        form.addRow("Type", self.kind)
        form.addRow("Moves assembly", self.assembly)
        form.addRow("Axis (x, y, z)", self.axis)
        form.addRow("Pivot (mm)", self.pivot)
        form.addRow("Driven by", self.expr)
        form.addRow("2nd axis (spherical)", self.axis2)
        form.addRow("2nd driver (spherical)", self.expr2)
        form.addRow("Offset (deg / mm)", self.offset)
        note = QLabel("Axis and pivot are in the model's home pose. The driver is one of your equations of t (degrees; mm for a prismatic joint).")
        note.setWordWrap(True)
        note.setStyleSheet("color:#9aa2ad")
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(box)
        self.result_joint: Joint | None = None
        self.assembly.currentTextChanged.connect(self._suggest_pivot)
        if joint is None:
            self._suggest_pivot(self.assembly.currentText())

    def _suggest_pivot(self, assembly: str) -> None:
        """Default pivot: the centre of the assembly's parts (a sensible hinge location to adjust)."""
        d = self.studio.session.design
        try:
            members = [p for p in d._members(assembly)]
            if members:
                lo = np.min([p.bbox()[0] for p in members], axis=0)
                hi = np.max([p.bbox()[1] for p in members], axis=0)
                self.pivot.setText(", ".join(f"{v:.1f}" for v in (lo + hi) / 2))
        except Exception:
            pass

    @staticmethod
    def _vec(text: str, what: str) -> tuple:
        try:
            v = tuple(float(x) for x in re.split(r"[,\s]+", text.strip()) if x)
        except ValueError:
            raise KinematicsError(f"The {what} must be three numbers like 0, 0, 1.") from None
        if len(v) != 3:
            raise KinematicsError(f"The {what} must be three numbers like 0, 0, 1.")
        return v

    def _accept(self) -> None:
        try:
            joint = Joint(self.name.text().strip(), self.kind.currentText(), self.assembly.currentText(), axis=self._vec(self.axis.text(), "axis"),
                          pivot=self._vec(self.pivot.text(), "pivot"), expr=self.expr.currentText().strip(), axis2=self._vec(self.axis2.text(), "second axis"),
                          expr2=self.expr2.currentText().strip(), offset=self.offset.value())
            joint.validate()
            for e in joint.drivers:
                if not self.studio.engine.has(e):
                    raise KinematicsError(f"'{e}' is not one of your equations. Add it in the Equations tab first.")
        except KinematicsError as e:
            self.studio.session.message.emit("error", f"Joint: {e}")
            return
        self.result_joint = joint
        self.accept()


class JointsPanel(QWidget):
    def __init__(self, studio):
        super().__init__()
        self.studio = studio
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        note = QLabel("A joint moves a whole assembly. Select an assembly in the Part Navigator, then add a joint and choose the equation that drives it.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#9aa2ad")
        lay.addWidget(note)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Joint", "Type", "Assembly", "Driven by"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemDoubleClicked.connect(lambda _i: self.edit())
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for kind in JOINT_KINDS:
            b = QPushButton("+ " + kind.capitalize())
            b.clicked.connect(lambda _=False, k=kind: self.add(k))
            row.addWidget(b)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        e, r = QPushButton("Edit"), QPushButton("Delete")
        e.clicked.connect(self.edit)
        r.clicked.connect(self.delete)
        row2.addWidget(e)
        row2.addWidget(r)
        lay.addLayout(row2)
        studio.model_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        js = self.studio.joints
        self.table.setRowCount(len(js))
        for i, j in enumerate(js):
            for c, text in enumerate((j.name, j.kind, j.assembly, " + ".join(j.drivers) or "(none)")):
                self.table.setItem(i, c, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def add(self, kind: str) -> None:
        dlg = JointDialog(self, self.studio, kind)
        if dlg.exec() == QDialog.Accepted and dlg.result_joint:
            self.studio.add_joint(dlg.result_joint)

    def _selected(self) -> Joint | None:
        rows = {i.row() for i in self.table.selectedItems()}
        return self.studio.joints[min(rows)] if rows else None

    def edit(self) -> None:
        j = self._selected()
        if j is None:
            return
        dlg = JointDialog(self, self.studio, j.kind, j)
        if dlg.exec() == QDialog.Accepted and dlg.result_joint:
            self.studio.set_joints([dlg.result_joint if x.name == j.name else x for x in self.studio.joints])

    def delete(self) -> None:
        j = self._selected()
        if j is not None:
            self.studio.remove_joint(j.name)


class MotionExpressionsDock(QDockWidget):
    """Right-hand dock: the equations (with their auto-generated sliders) and the joint bindings."""

    def __init__(self, studio, parent=None):
        super().__init__("Motion Expressions", parent)
        self.setObjectName("motionExpressionsDock")
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetClosable)
        tabs = QTabWidget()
        self.equations = EquationsPanel(studio)
        self.joints = JointsPanel(studio)
        tabs.addTab(self.equations, "Equations")
        tabs.addTab(self.joints, "Joints")
        self.setWidget(tabs)
        self.setMinimumWidth(340)
