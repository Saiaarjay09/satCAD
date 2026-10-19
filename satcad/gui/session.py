"""The open document: wraps a satcad Design, records undo history, and tells the interface what changed (Qt signals).

Every edit goes through `run()`, which is transactional: if the engine rejects it, the design is unchanged, the user gets a plain-English
message in the Information window, and nothing is added to the undo history.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..engine import SCHEMAS, Design, DesignError

NX_NOUN = {"box": "Block", "chassis": "Block", "cylinder": "Cylinder", "sphere": "Sphere", "panel": "Panel", "extrude": "Extrude",
           "revolve": "Revolve", "sweep": "Sweep", "loft": "Through Curves", "shell": "Shell", "fillet_edges": "Edge Blend",
           "hole": "Hole", "fastener": "Fastener", "cutout": "Pocket", "pattern": "Pattern Feature", "mirror": "Mirror Feature",
           "boolean": "Boolean", "assembly": "Assembly"}
BASE_NAME = {"box": "block", "chassis": "chassis", "cylinder": "cylinder", "sphere": "sphere", "panel": "panel", "extrude": "extrude",
             "revolve": "revolve", "sweep": "sweep", "loft": "loft", "shell": "shell", "fillet_edges": "blend", "hole": "hole",
             "fastener": "screw", "cutout": "pocket", "pattern": "pattern", "mirror": "mirror", "boolean": "bool", "assembly": "assy"}
# kinds that change an existing body instead of making a new one
MODIFIERS = {"shell", "fillet_edges", "hole", "cutout", "boolean"}


def feature_label(kind: str, raw: dict, number: int, name: str | None = None) -> str:
    """The name shown in the Part Navigator, NX style: 'Block(1)', 'Edge Blend(3)', 'Subtract(4)'. An assembly shows its own name."""
    if kind == "assembly" and name:
        return name
    if kind == "boolean":
        noun = str(raw.get("operation", "Boolean")).capitalize()
    elif kind == "fillet_edges" and raw.get("chamfer") not in (None, 0, 0.0, "") and raw.get("radius") in (None, 0, 0.0, ""):
        noun = "Chamfer"
    elif kind == "hole":
        noun = {"clearance": "Hole", "tapped": "Tapped Hole", "counterbore": "Counterbore Hole",
                "countersink": "Countersink Hole"}.get(str(raw.get("hole_type", "clearance")), "Hole")
    else:
        noun = NX_NOUN.get(kind, kind.title())
    return f"{noun}({number})"


class Session(QObject):
    changed = Signal()                  # the design was rebuilt (parts, tree and viewport must refresh)
    message = Signal(str, str)          # level ("info" / "warn" / "error"), text for the Information window
    selection_changed = Signal(object)  # selected feature name, or None
    dirty_changed = Signal(bool)
    loaded = Signal()                   # a different part was opened or started (the view should re-frame it)
    saving = Signal()                   # about to write the file: panels that keep extra data in it should update `extras` now

    def __init__(self, design: Design | None = None):
        super().__init__()
        self.design = design or Design("untitled")
        self.path: Path | None = None
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._selected: str | None = None
        self._dirty = False
        self.extras: dict = {}              # extra data saved with the part under its own keys (for example 'motion')

    # ---- state -------------------------------------------------------------------------------------------------------------------
    @property
    def selected(self) -> str | None:
        return self._selected

    def select(self, name: str | None) -> None:
        if name is not None and name not in self.design.features:
            name = None
        if name != self._selected:
            self._selected = name
            self.selection_changed.emit(name)

    @property
    def dirty(self) -> bool:
        return self._dirty

    def _set_dirty(self, value: bool) -> None:
        if value != self._dirty:
            self._dirty = value
            self.dirty_changed.emit(value)

    def feature_numbers(self) -> dict[str, int]:
        """Sequence number of every feature in build order (the number in 'Block(1)')."""
        return {name: i + 1 for i, name in enumerate(self.design.features)}

    def labels(self) -> dict[str, str]:
        nums = self.feature_numbers()
        return {n: feature_label(f.kind, f.raw, nums[n], n) for n, f in self.design.features.items()}

    def body_names(self) -> list[str]:
        """Names of the parts that exist right now (what a Boolean or Blend can act on)."""
        return list(self.design.parts)

    def unique_name(self, base: str) -> str:
        i = 1
        while self.design._name_taken(f"{base}_{i}"):
            i += 1
        return f"{base}_{i}"

    # ---- transactions -----------------------------------------------------------------------------------------------------------
    def info(self, text: str) -> None:
        self.message.emit("info", text)

    def run(self, label: str, fn):
        """Run an edit against the design. Returns fn's result, or None if the engine refused it (then a message explains why)."""
        before = copy.deepcopy(self.design.to_json())
        try:
            result = fn()
        except DesignError as e:
            self.message.emit("error", f"{label}: {e}")
            return None
        except Exception as e:                           # a bug must not take the application down
            self.message.emit("error", f"{label} failed unexpectedly: {type(e).__name__}: {e}")
            return None
        self._undo.append(before)
        del self._undo[:-100]
        self._redo.clear()
        self._set_dirty(True)
        self.message.emit("info", label)
        if self._selected not in self.design.features:
            self._selected = None
            self.selection_changed.emit(None)
        self.changed.emit()
        return True if result is None else result

    def add(self, kind: str, name: str | None = None, **settings):
        if kind not in SCHEMAS:
            raise DesignError(f"Unknown feature type '{kind}'.")
        name = name or self.unique_name(BASE_NAME.get(kind, kind))
        done = self.run(f"Created {name}", lambda: self.design.add(kind, name, **settings))
        if done:
            self.select(name)
            return name
        return None

    def edit(self, name: str, **changes) -> bool:
        return bool(self.run(f"Edited {name}", lambda: self.design.edit(name, **changes)))

    def remove(self, name: str) -> bool:
        return bool(self.run(f"Deleted {name}", lambda: self.design.remove(name)))

    def set_variable(self, name: str, value) -> bool:
        return bool(self.run(f"Expression {name} set", lambda: self.design.set_variable(name, value)))

    def delete_variable(self, name: str) -> bool:
        return bool(self.run(f"Expression {name} deleted", lambda: self.design.delete_variable(name)))

    def rename_design(self, name: str) -> bool:
        if not re.match(r"^[A-Za-z0-9_-]{1,40}$", name):
            self.message.emit("error", "Names may only use letters, digits, '_' and '-' (max 40 characters).")
            return False
        self.design.name = name
        self._set_dirty(True)
        self.changed.emit()
        return True

    # ---- undo / redo ------------------------------------------------------------------------------------------------------------
    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _restore(self, data: dict) -> None:
        fresh = Design("untitled")
        fresh.load_data(copy.deepcopy(data))
        self.design = fresh
        if self._selected not in self.design.features:
            self._selected = None
            self.selection_changed.emit(None)
        self._set_dirty(True)
        self.changed.emit()

    def undo(self) -> None:
        if self._undo:
            self._redo.append(copy.deepcopy(self.design.to_json()))
            self._restore(self._undo.pop())
            self.message.emit("info", "Undo")

    def redo(self) -> None:
        if self._redo:
            self._undo.append(copy.deepcopy(self.design.to_json()))
            self._restore(self._redo.pop())
            self.message.emit("info", "Redo")

    # ---- files ------------------------------------------------------------------------------------------------------------------
    def new(self, name: str = "untitled") -> None:
        self.design = Design(name)
        self.path, self._selected = None, None
        self.extras = {}
        self._undo.clear()
        self._redo.clear()
        self._set_dirty(False)
        self.selection_changed.emit(None)
        self.loaded.emit()
        self.changed.emit()
        self.message.emit("info", f"New part {name}")

    def load_design(self, design: Design, message: str = "") -> None:
        """Replace the open part with a design built elsewhere (for example a script that generates a satellite), as a new unsaved part."""
        self.design, self.path, self._selected, self.extras = design, None, None, {}
        self._undo.clear()
        self._redo.clear()
        self._set_dirty(False)
        self.selection_changed.emit(None)
        self.loaded.emit()
        self.changed.emit()
        self.message.emit("info", message or f"Loaded {design.name}: {len(design.features)} features, {len(design.parts)} bodies")

    def open(self, path) -> bool:
        path = Path(path)
        extras = {}
        try:
            fresh = Design("untitled")
            raw = json.loads(path.read_text())
            fresh.load_data(raw)
            extras = {k: raw[k] for k in ("motion",) if isinstance(raw, dict) and k in raw}
        except (DesignError, ValueError, OSError, KeyError, TypeError) as e:
            self.message.emit("error", f"Couldn't open {path.name}: {e}")
            return False
        if not re.match(r"^[A-Za-z0-9_-]{1,40}$", fresh.name or ""):
            fresh.name = re.sub(r"[^A-Za-z0-9_-]", "_", str(fresh.name))[:40] or "untitled"
        self.design, self.path, self._selected, self.extras = fresh, path, None, extras
        self._undo.clear()
        self._redo.clear()
        self._set_dirty(False)
        self.selection_changed.emit(None)
        self.loaded.emit()
        self.changed.emit()
        self.message.emit("info", f"Opened {path.name}: {len(fresh.features)} features, {len(fresh.parts)} bodies")
        return True

    def save(self, path=None) -> bool:
        path = Path(path or self.path or f"{self.design.name}.json")
        self.saving.emit()
        try:
            data = self.design.to_json()
            data.update({k: v for k, v in self.extras.items() if v})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2))
        except OSError as e:
            self.message.emit("error", f"Couldn't save: {e}")
            return False
        self.path = path
        self._set_dirty(False)
        self.message.emit("info", f"Saved {path}")
        return True
