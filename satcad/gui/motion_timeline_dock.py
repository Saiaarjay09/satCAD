"""Timeline dock: Play / Pause / Stop, Loop, speed 0.1x to 10x, a time scrubber, and live Desmos-style plots of position, velocity and
acceleration of the selected function, with a cursor that follows (and can be dragged to set) the current time."""
from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget)

from . import theme

pg.setConfigOptions(antialias=True, background=theme.PALETTE["field"], foreground=theme.PALETTE["text"])
SCRUB_STEPS = 10000
WINDOWS = (("Whole timeline", None), ("±300 s", 300.0), ("±60 s", 60.0), ("±10 s", 10.0), ("±2 s", 2.0))
CURVES = (("Position  x(t)", "x", "#5aa8ff", "deg"), ("Velocity  v = dx/dt", "v", "#5fd08a", "deg/s"), ("Acceleration  a = d²x/dt²", "a", "#ffa31a", "deg/s²"))


class MotionTimelineDock(QDockWidget):
    def __init__(self, studio, parent=None):
        super().__init__("Motion Timeline", parent)
        self.setObjectName("motionTimelineDock")
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetClosable)
        self.studio = studio
        self._busy = False
        body = QWidget()
        self.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(8, 6, 8, 6)

        controls = QHBoxLayout()
        self.play = QPushButton("▶  Play")
        self.play.setCheckable(True)
        self.play.setMinimumWidth(86)
        self.stop = QPushButton("■  Stop")
        self.loop = QCheckBox("Loop")
        self.loop.setChecked(studio.loop)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.1, 10.0)
        self.speed.setSingleStep(0.1)
        self.speed.setValue(studio.speed)
        self.speed.setSuffix(" ×")
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(-100, 100)                     # log scale: 0.1x .. 10x
        self.speed_slider.setValue(int(round(100 * np.log10(studio.speed))))
        self.speed_slider.setFixedWidth(110)
        self.tmax = QDoubleSpinBox()
        self.tmax.setRange(1, 1e6)
        self.tmax.setDecimals(1)
        self.tmax.setValue(studio.t_max)
        self.tmax.setSuffix(" s")
        self.func = QComboBox()
        self.func.setMinimumWidth(130)
        self.window = QComboBox()
        for label, _ in WINDOWS:
            self.window.addItem(label)
        for w in (self.play, self.stop, self.loop, QLabel("Speed"), self.speed, self.speed_slider, QLabel("T max"), self.tmax, QLabel("Plot"), self.func,
                  self.window):
            controls.addWidget(w)
        controls.addStretch(1)
        self.fps = QLabel("")
        self.fps.setStyleSheet("color:#9aa2ad")
        controls.addWidget(self.fps)
        root.addLayout(controls)

        scrub = QHBoxLayout()
        self.scrub = QSlider(Qt.Horizontal)
        self.scrub.setRange(0, SCRUB_STEPS)
        self.time_label = QLabel("t = 0.00 s")
        self.time_label.setMinimumWidth(110)
        self.time_label.setStyleSheet("font-weight:600")
        scrub.addWidget(self.scrub, 1)
        scrub.addWidget(self.time_label)
        root.addLayout(scrub)

        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setMinimumHeight(150)
        root.addWidget(self.glw, 1)
        self.plots, self.lines, self.dots, self.curves = [], [], [], []
        for i, (title, key, color, unit) in enumerate(CURVES):
            p = self.glw.addPlot(row=0, col=i, title=title)
            p.showGrid(x=True, y=True, alpha=0.25)
            p.setLabel("bottom", "t (s)")
            p.setLabel("left", unit)
            for axis in ("bottom", "left"):
                p.getAxis(axis).enableAutoSIPrefix(False)                      # plain numbers: no "ks" or "x0.001" scaling
            curve = p.plot(pen=pg.mkPen(color, width=2))
            line = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen("#ffffff", width=1.2, style=Qt.DashLine))
            line.sigPositionChanged.connect(self._cursor_dragged)
            p.addItem(line)
            dot = pg.ScatterPlotItem(size=10, brush=pg.mkBrush(color), pen=pg.mkPen("#ffffff", width=1.5))
            p.addItem(dot)
            if i:
                p.setXLink(self.plots[0])
            self.plots.append(p)
            self.lines.append(line)
            self.dots.append(dot)
            self.curves.append(curve)

        self._plot_timer = QTimer(self)
        self._plot_timer.setSingleShot(True)
        self._plot_timer.setInterval(160)
        self._plot_timer.timeout.connect(self.replot)
        self._fps_timer = QTimer(self)
        self._fps_timer.setInterval(500)
        self._fps_timer.timeout.connect(self._show_fps)

        self.play.toggled.connect(lambda on: studio.play() if on else studio.pause())
        self.stop.clicked.connect(studio.stop)
        self.loop.toggled.connect(lambda v: setattr(studio, "loop", v))
        self.speed.valueChanged.connect(self._speed_typed)
        self.speed_slider.valueChanged.connect(self._speed_slid)
        self.tmax.editingFinished.connect(lambda: studio.set_t_max(self.tmax.value()))
        self.scrub.valueChanged.connect(self._scrubbed)
        self.func.currentTextChanged.connect(lambda _t: self.replot())
        self.window.currentIndexChanged.connect(lambda _i: self.replot())
        studio.time_changed.connect(self._on_time)
        studio.playing_changed.connect(self._on_playing)
        studio.model_changed.connect(self._on_model)
        self._fill_functions()
        self.replot()

    # ---- controls ---------------------------------------------------------------------------------------------------------------------
    def _speed_typed(self, v: float) -> None:
        self.studio.set_speed(v)
        self.speed_slider.blockSignals(True)
        self.speed_slider.setValue(int(round(100 * np.log10(max(v, 0.1)))))
        self.speed_slider.blockSignals(False)

    def _speed_slid(self, pos: int) -> None:
        v = 10 ** (pos / 100.0)
        self.speed.blockSignals(True)
        self.speed.setValue(round(v, 2))
        self.speed.blockSignals(False)
        self.studio.set_speed(v)

    def _scrubbed(self, pos: int) -> None:
        if self._busy:
            return
        self.studio.seek(self.studio.t_max * pos / SCRUB_STEPS)

    def _cursor_dragged(self, line) -> None:
        if self._busy:
            return
        self.studio.seek(float(line.value()))

    def _on_playing(self, on: bool) -> None:
        self.play.blockSignals(True)
        self.play.setChecked(on)
        self.play.blockSignals(False)
        self.play.setText("⏸  Pause" if on else "▶  Play")
        if on:
            self._fps_timer.start()
        else:
            self._fps_timer.stop()
            self.fps.setText("")

    def _show_fps(self) -> None:
        v = self.studio.viewport
        self.fps.setText(f"{self.studio.fps:4.0f} fps   frame {getattr(v, 'last_frame_ms', 0):.0f} ms")

    # ---- time and plots ---------------------------------------------------------------------------------------------------------------
    def _on_time(self, t: float) -> None:
        if self.studio.playing:                                      # while playing, the 3D view gets every frame but the plots only ~20 times a second
            now = time.perf_counter()
            if now - getattr(self, "_last_ui", 0.0) < 0.05:
                return
            self._last_ui = now
        self._busy = True
        self.scrub.setValue(int(round(SCRUB_STEPS * t / self.studio.t_max)) if self.studio.t_max else 0)
        self.time_label.setText(f"t = {t:,.2f} s")
        for line in self.lines:
            line.setValue(t)
        self._update_dots(t)
        self._busy = False
        if self._windowed():
            if not self._plot_timer.isActive():
                self._plot_timer.start()

    def _windowed(self) -> float | None:
        return WINDOWS[self.window.currentIndex()][1]

    def _fill_functions(self) -> None:
        names = self.studio.engine.function_names(1)
        cur = self.func.currentText()
        self.func.blockSignals(True)
        self.func.clear()
        self.func.addItems(names)
        if cur in names:
            self.func.setCurrentText(cur)
        self.func.blockSignals(False)

    def _on_model(self) -> None:
        self._fill_functions()
        self.tmax.blockSignals(True)
        self.tmax.setValue(self.studio.t_max)
        self.tmax.blockSignals(False)
        self._plot_timer.start()

    def replot(self) -> None:
        eng, name = self.studio.engine, self.func.currentText()
        if not name or not eng.has(name) or eng.arity(name) != 1:
            for c in self.curves:
                c.setData([], [])
            return
        w = self._windowed()
        t = self.studio.t
        lo, hi = (0.0, self.studio.t_max) if w is None else (t - w, t + w)
        xs = np.linspace(lo, hi, 2400)
        data = eng.sample(name, xs)
        for curve, (_title, key, _c, _u) in zip(self.curves, CURVES):
            curve.setData(xs, data[key])
        self.plots[0].setXRange(lo, hi, padding=0.01)
        for p in self.plots:
            p.enableAutoRange(axis="y")
        self._update_dots(t)

    def _update_dots(self, t: float) -> None:
        eng, name = self.studio.engine, self.func.currentText()
        if not name or not eng.has(name) or eng.arity(name) != 1:
            return
        vals = (eng.value(name, t), eng.velocity(name, t), eng.acceleration(name, t))
        for dot, v in zip(self.dots, vals):
            dot.setData([t], [float(v)])
