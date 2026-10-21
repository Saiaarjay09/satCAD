"""Motion Studio controller: ties the expression engine, the joint solver, the playback clock and the 3D viewport together.

Frame loop (about 60 per second while playing):  t -> expression engine (microseconds per formula) -> joint values -> solver
(4x4 matrices) -> viewport (one matrix update per moving group; no re-meshing) -> one render.
"""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from ..engine import DesignError
from .kinematics_solver import ColorPulse, Joint, KinematicsError, KinematicsSolver
from .motion_expressions import ExpressionEngine, ExpressionError

FRAME_MS = 16


class MotionStudio(QObject):
    time_changed = Signal(float)
    playing_changed = Signal(bool)
    model_changed = Signal()                      # equations, parameters, joints or pulses changed (panels and plots must refresh)

    def __init__(self, session, viewport):
        super().__init__()
        self.session, self.viewport = session, viewport
        self.engine = ExpressionEngine()
        self.solver = KinematicsSolver()
        self.joints: list[Joint] = []
        self.pulses: list[ColorPulse] = []
        self.t, self.t_max, self.speed, self.loop = 0.0, 5400.0, 1.0, True
        self.playing = False
        self.exposed: list[str] = []                 # parameter names shown as sliders in the Property Editor (a design can expose its key motion parameters)
        self.suspended = False                       # the Digital Twin owns the moving groups and colours while this is True
        self._last = 0.0
        self.fps = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self._tick)
        session.changed.connect(self._on_design_changed)
        session.loaded.connect(self._on_loaded)
        session.saving.connect(self._on_saving)

    # ---- definitions ---------------------------------------------------------------------------------------------------------------
    def set_equations(self, lines: list[str]) -> None:
        self.engine.set_equations(lines)
        self._refresh_pose()
        self.model_changed.emit()

    def set_parameter(self, name: str, value: float) -> None:
        """A slider moved: no reparse, no recompile; just re-pose the model."""
        self.engine.set_param(name, value)
        self._refresh_pose()
        self.model_changed.emit()

    def add_joint(self, joint: Joint) -> bool:
        return self.set_joints([j for j in self.joints if j.name != joint.name] + [joint])

    def remove_joint(self, name: str) -> None:
        self.set_joints([j for j in self.joints if j.name != name])

    def set_joints(self, joints: list[Joint]) -> bool:
        """Replace the joint bindings. Returns False (and says why in the Information window) if they cannot be used."""
        try:
            solver = KinematicsSolver(joints, self.session.design)
        except KinematicsError as e:
            self.session.message.emit("error", f"Motion: {e}")
            return False
        self.joints, self.solver = list(joints), solver
        self._bind()
        self._refresh_pose()
        self.model_changed.emit()
        return True

    def set_pulses(self, pulses: list[ColorPulse]) -> None:
        self.pulses = list(pulses)
        self._refresh_pose()
        self.model_changed.emit()

    def clear(self) -> None:
        self.pause()
        self.joints, self.pulses, self.exposed = [], [], []
        self.solver = KinematicsSolver()
        self.engine.set_equations([])
        self.t = 0.0
        self._bind()
        self.viewport.set_part_colors(None)
        self.model_changed.emit()
        self.time_changed.emit(0.0)

    def load_preset(self, preset) -> None:
        """Load a ready-made motion setup (an object with .equations, .params, .ranges, .joints, .pulses and .t_max)."""
        self.pause()
        self.engine.param_values.update(preset.params)
        self.engine.param_ranges.update(preset.ranges)
        self.engine.set_equations(preset.equations)
        self.t_max = preset.t_max
        self.joints, self.pulses = list(preset.joints), list(preset.pulses)
        try:
            self.solver = KinematicsSolver(self.joints, self.session.design)
        except KinematicsError as e:
            self.session.message.emit("error", f"Motion: {e}")
            self.joints, self.solver = [], KinematicsSolver()
        self._bind()
        self.seek(0.0)
        self.model_changed.emit()

    # ---- binding to the viewport -------------------------------------------------------------------------------------------------
    def suspend(self) -> None:
        """Hand the viewport's moving groups and colours to another controller (Live Digital Twin Sync): stop playing and stop touching the viewport."""
        self.pause()
        self.suspended = True

    def resume(self) -> None:
        """Take the viewport back and pose the model where Motion Studio says it should be."""
        self.suspended = False
        self._bind()
        self._refresh_pose()

    def _bind(self) -> None:
        if self.suspended:
            return
        groups = {j.name: self.solver.parts_of(j.name) for j in self.solver.joints}
        self.viewport.bind_motion_groups({g: p for g, p in groups.items() if p})

    def _on_design_changed(self) -> None:
        """The part was rebuilt (new actors): rebind, dropping joints whose assembly no longer exists."""
        if not self.joints:
            return
        assemblies = {n for n, f in self.session.design.features.items() if f.kind == "assembly"}
        keep = [j for j in self.joints if j.assembly in assemblies]
        if len(keep) != len(self.joints):
            self.session.message.emit("warn", f"Motion: removed {len(self.joints) - len(keep)} joint(s) whose assembly no longer exists.")
        try:
            self.solver = KinematicsSolver(keep, self.session.design)
            self.joints = keep
        except KinematicsError as e:
            self.session.message.emit("error", f"Motion: {e}")
            return
        self._bind()
        self._refresh_pose()
        self.model_changed.emit()

    def _on_loaded(self) -> None:
        data = self.session.extras.get("motion")
        self.pause()
        if data:
            try:
                self.load_dict(data)
                return
            except (KinematicsError, ExpressionError, KeyError, TypeError, ValueError, DesignError) as e:
                self.session.message.emit("warn", f"The saved motion setup could not be restored: {e}")
        self.clear()

    def _on_saving(self) -> None:
        if self.joints or self.pulses or self.engine.equations:
            self.session.extras["motion"] = self.to_dict()
        else:
            self.session.extras.pop("motion", None)

    # ---- posing --------------------------------------------------------------------------------------------------------------------
    def joint_values(self, t: float) -> dict:
        out = {}
        for j in self.joints:
            v = []
            for name in (j.expr, j.expr2 if j.kind == "spherical" else ""):
                v.append(float(self.engine.value(name, t)) if name and self.engine.has(name) and self.engine.arity(name) == 1 else 0.0)
            out[j.name] = tuple(v)
        return out

    def pose(self, t: float) -> None:
        """Move the model to time t."""
        if self.suspended:
            return
        matrices = self.solver.joint_matrices(self.joint_values(t))
        self.viewport.set_group_matrices(matrices)
        if self.pulses:
            colours = {}
            for pulse in self.pulses:
                if self.engine.has(pulse.expr) and self.engine.arity(pulse.expr) == 2:
                    k = np.arange(len(pulse.parts), dtype=float)
                    colours.update(pulse.colours(self.engine.value(pulse.expr, t, k)))
            self.viewport.set_part_colors(colours)

    def _refresh_pose(self) -> None:
        self.pose(self.t)

    # ---- playback ------------------------------------------------------------------------------------------------------------------
    def seek(self, t: float) -> None:
        self.t = float(min(max(t, 0.0), self.t_max))
        self.pose(self.t)
        self.time_changed.emit(self.t)

    def play(self) -> None:
        if self.playing:
            return
        if self.t >= self.t_max:
            self.t = 0.0
        self.playing = True
        self._last = time.perf_counter()
        self.viewport.clear_face()
        self.viewport.set_motion_quality(True)
        self.timer.start()
        self.playing_changed.emit(True)

    def pause(self) -> None:
        if not self.playing:
            return
        self.playing = False
        self.timer.stop()
        self.viewport.set_motion_quality(False)
        self.playing_changed.emit(False)

    def stop(self) -> None:
        self.pause()
        self.seek(0.0)

    def reset_pose(self) -> None:
        """Show the model in its home (drawn) pose without losing the motion setup."""
        self.pause()
        self.viewport.set_group_matrices({j.name: np.eye(4) for j in self.joints})
        self.viewport.set_part_colors(None)

    def set_speed(self, speed: float) -> None:
        self.speed = float(min(max(speed, 0.1), 10.0))

    def set_t_max(self, t_max: float) -> None:
        self.t_max = max(1.0, float(t_max))
        if self.t > self.t_max:
            self.seek(self.t_max)
        self.model_changed.emit()

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = now - self._last
        self._last = now
        if dt > 0:
            self.fps = 0.9 * self.fps + 0.1 / dt if self.fps else 1.0 / dt
        t = self.t + dt * self.speed
        if t >= self.t_max:
            if self.loop:
                t = t % self.t_max
            else:
                self.seek(self.t_max)
                self.pause()
                return
        self.t = t
        self.pose(t)
        self.time_changed.emit(t)

    # ---- saving --------------------------------------------------------------------------------------------------------------------
    def to_dict(self) -> dict:
        out = dict(engine=self.engine.to_dict(), joints=[j.to_dict() for j in self.joints], pulses=[p.to_dict() for p in self.pulses], t_max=self.t_max)
        if self.exposed:
            out["expose"] = list(self.exposed)
        return out

    def load_dict(self, d: dict) -> None:
        self.engine.load_dict(d.get("engine") or {})
        self.t_max = float(d.get("t_max", 5400.0))
        self.joints = [Joint.from_dict(j) for j in d.get("joints", [])]
        self.pulses = [ColorPulse.from_dict(p) for p in d.get("pulses", [])]
        self.exposed = [str(n) for n in d.get("expose", []) if isinstance(n, str)]
        self.solver = KinematicsSolver(self.joints, self.session.design)
        self._bind()
        self.seek(0.0)
        self.model_changed.emit()
