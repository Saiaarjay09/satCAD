"""2D Blueprint Studio: the zoomable drawing sheet that sits next to the 3D viewport.

    BlueprintScene   draws a satcad.drawing_studio.Sheet into a QGraphicsScene (millimetres, y down), one path item per layer so thousands of edges stay fast,
                     in a dark 'blueprint' theme or an ISO / ASME 'print' theme (white paper, black ink).
    BlueprintView    a QGraphicsView with wheel zoom and drag to pan.
    DrawingWorker    runs the OpenCASCADE hidden-line removal on a QThread and reports progress, so the window stays responsive.
    BlueprintStudio  the widget: view + progress bar + status line; it regenerates the sheet (debounced) when the design or the Motion Studio pose changes.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QGraphicsPathItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QLabel, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget, QHBoxLayout)

from .. import drawing_studio as ds

THEME_PAPER = {"dark": "#0B132B", "print": "#ffffff"}
THEME_BACKDROP = {"dark": "#060b1a", "print": "#6b7280"}
FONT_PX = 100                                   # fonts are made at 100 px and scaled so that a capital letter is exactly the requested height in mm
CAP_HEIGHT = 0.72


class BlueprintScene(QGraphicsScene):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme = "dark"
        self.sheet: ds.Sheet | None = None
        self._layer_items: dict = {}                 # layer key -> [items] (for re-colouring)

    def load(self, sheet: ds.Sheet, theme: str = "dark") -> None:
        self.clear()
        self._layer_items = {}
        self.sheet = sheet
        self.theme = theme
        w, h = sheet.size
        colors = ds.THEMES[theme]
        self.setSceneRect(-8, -8, w + 16, h + 16)
        self.setBackgroundBrush(QBrush(QColor(THEME_BACKDROP[theme])))
        paper = self.addRect(QRectF(0, 0, w, h), QPen(Qt.NoPen), QBrush(QColor(THEME_PAPER[theme])))
        paper.setZValue(-10)
        self._paper = paper
        # polylines are merged into one path per (layer, line style, weight)
        paths: dict = {}
        fills: dict = {}
        texts = []
        for p in sheet.prims:
            if p.kind == "line" and len(p.pts) >= 2:
                weight = p.weight or ds.LAYERS[p.layer][3]
                path = paths.setdefault((p.layer, p.style, weight), QPainterPath())
                x, y = p.pts[0]
                path.moveTo(x, h - y)
                for x, y in p.pts[1:]:
                    path.lineTo(x, h - y)
            elif p.kind == "fill" and len(p.pts) >= 3:
                path = fills.setdefault(p.layer, QPainterPath())
                path.moveTo(p.pts[0][0], h - p.pts[0][1])
                for x, y in p.pts[1:]:
                    path.lineTo(x, h - y)
                path.closeSubpath()
            elif p.kind == "text" and p.text:
                texts.append(p)
        for (layer, style, weight), path in paths.items():
            item = QGraphicsPathItem(path)
            pen = QPen(QColor(colors[layer]), weight)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            if style == "dashed":
                pen.setDashPattern([d / weight for d in (ds.HIDDEN_DASH[0], ds.HIDDEN_DASH[1])])
            elif style == "center":
                pen.setDashPattern([d / weight for d in ds.CENTER_DASH])
            item.setPen(pen)
            item.setData(0, (layer, style, weight))
            item.setZValue({"hidden": 1, "visible": 2, "centerlines": 1.5, "dimensions": 3, "titleblock": 3}[layer])
            self.addItem(item)
            self._layer_items.setdefault(layer, []).append(item)
        for layer, path in fills.items():
            item = QGraphicsPathItem(path)
            item.setPen(QPen(Qt.NoPen))
            item.setBrush(QBrush(QColor(colors[layer])))
            item.setData(0, (layer, "fill", 0))
            item.setZValue(3)
            self.addItem(item)
            self._layer_items.setdefault(layer, []).append(item)
        font = QFont("Helvetica")
        font.setPixelSize(FONT_PX)
        for p in texts:
            t = QGraphicsSimpleTextItem(p.text)
            t.setFont(font)
            t.setBrush(QBrush(QColor(colors["text"])))
            k = p.height / CAP_HEIGHT / FONT_PX
            t.setScale(k)
            r = t.boundingRect()
            x, y = p.pts[0]
            ax = {"l": 0.0, "c": 0.5, "r": 1.0}[p.anchor[0]] * r.width() * k
            ay = {"t": 0.0, "m": 0.5, "b": 1.0}[p.anchor[1]] * r.height() * k
            t.setTransformOriginPoint(0, 0)
            if p.rot:
                t.setRotation(-p.rot)
                c, s = np.cos(np.radians(-p.rot)), np.sin(np.radians(-p.rot))
                off = QPointF(ax * c - ay * s, ax * s + ay * c)
            else:
                off = QPointF(ax, ay)
            t.setPos(QPointF(x, h - y) - off)
            t.setData(0, ("text", "text", 0))
            t.setZValue(4)
            self.addItem(t)
            self._layer_items.setdefault("text", []).append(t)

    def set_theme(self, theme: str) -> None:
        if self.sheet is not None:
            self.load(self.sheet, theme)


class BlueprintView(QGraphicsView):
    def __init__(self, scene: BlueprintScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)

    def wheelEvent(self, event) -> None:
        factor = 1.0015 ** event.angleDelta().y()
        self.scale(factor, factor)

    def fit_sheet(self) -> None:
        if self.scene() is not None and self.scene().sheet is not None:
            w, h = self.scene().sheet.size
            self.fitInView(QRectF(0, 0, w, h), Qt.KeepAspectRatio)


class DrawingWorker(QThread):
    """Hidden-line removal off the GUI thread."""
    progress = Signal(float, str)
    done = Signal(object, int)                 # Sheet, generation
    failed = Signal(str, int)

    def __init__(self, prepared: ds.Prepared, context: ds.Context, opts: ds.DrawingOptions, generation: int, parent=None):
        super().__init__(parent)
        self.prepared, self.context, self.opts, self.generation = prepared, context, opts, generation
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            proj = ds.project_prepared(self.prepared, self.opts, lambda f, m: self.progress.emit(f, m), lambda: self._cancel)
            self.progress.emit(0.95, "Laying out the sheet")
            sheet = ds.compose_sheet(self.context, proj, self.opts)
        except ds.DrawingCancelled:
            return
        except Exception as e:                                       # noqa: BLE001 - any failure becomes a message, not a crash
            self.failed.emit(f"{type(e).__name__}: {e}", self.generation)
            return
        if not self._cancel:
            self.done.emit(sheet, self.generation)


class BlueprintStudio(QWidget):
    """The 2D Blueprint Studio workspace."""
    status_changed = Signal(str)
    sheet_ready = Signal(object)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = BlueprintScene(self)
        self.view = BlueprintView(self.scene)
        self.opts = ds.DrawingOptions()
        self.theme = "dark"
        self.design = None
        self.selection = ""
        self.matrices = None
        self.sheet: ds.Sheet | None = None
        self.worker: DrawingWorker | None = None
        self._generation = 0
        self._pending = False
        self.live = False                                   # becomes True after the first sheet: later changes re-project automatically
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view, 1)
        bar = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        self.progress.setMaximumHeight(16)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.label = QLabel("Press Generate 2D Drawing Sheet in the Drawing tab.")
        self.label.setStyleSheet("color:#9aa2ad")
        bar.addWidget(self.label, 1)
        bar.addWidget(self.progress, 1)
        bar.addWidget(self.cancel_button)
        lay.addLayout(bar)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self._refresh_now)

    # ---- running -------------------------------------------------------------------------------------------------------------------------
    def generate(self, design, selection: str = "", matrices=None, opts: ds.DrawingOptions | None = None) -> bool:
        """Start (or restart) the projection. Returns False if the design cannot be drawn."""
        self.design, self.selection, self.matrices = design, selection or "", matrices
        if opts is not None:
            self.opts = opts
        try:
            prepared = ds.prepare_projection(design, self.selection, matrices)
            context = ds.make_context(design)
        except ds.DesignError as e:
            self._fail(str(e))
            return False
        self.cancel()
        self._generation += 1
        self.worker = DrawingWorker(prepared, context, ds.DrawingOptions(**vars(self.opts)), self._generation, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(lambda msg, gen: self._fail(msg) if gen == self._generation else None)
        self.worker.finished.connect(self._worker_finished)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.cancel_button.setVisible(True)
        self.label.setText("Hidden-line removal ...")
        self.worker.start()
        return True

    def request_refresh(self) -> None:
        """The design or the pose changed: re-project shortly (many quick changes collapse into one run)."""
        if self.live and self.design is not None:
            self.timer.start()

    def set_context(self, design, selection=None, matrices=None) -> None:
        self.design = design
        if selection is not None:
            self.selection = selection
        self.matrices = matrices

    def _refresh_now(self) -> None:
        if self.design is not None and self.live:
            self.generate(self.design, self.selection, self.matrices)

    def cancel(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)

    def wait(self, ms: int = 60000) -> None:
        """For tests and scripts: block until the running projection has finished and its result was applied."""
        from PySide6.QtWidgets import QApplication
        w = self.worker
        if w is not None:
            w.wait(ms)
        QApplication.processEvents()

    def _worker_finished(self) -> None:
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)

    def _on_progress(self, fraction: float, text: str) -> None:
        self.progress.setValue(int(fraction * 1000))
        self.progress.setFormat(text)

    def _on_done(self, sheet: ds.Sheet, generation: int) -> None:
        if generation != self._generation or self.design is None:
            return
        self.sheet = sheet
        self.scene.load(self.sheet, self.theme)
        first = not self.live
        self.live = True
        if first:
            self.view.fit_sheet()
        m = self.sheet.meta
        what = "the whole assembly" if not self.selection or self.selection in ("all", "Full assembly") else self.selection
        text = (f"{what}: {m['bodies']} bodies, scale {m['scale']}, {'bounding-box (fast)' if m['mode'] == 'bbox' else 'full'} hidden-line removal in {m['seconds']:.1f} s, "
                f"{len(self.sheet.dims)} dimensions")
        self.label.setText(text)
        self.status_changed.emit(text)
        self.sheet_ready.emit(self.sheet)

    def _fail(self, message: str) -> None:
        self.label.setText(f"Can't draw: {message}")
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self.error.emit(message)

    # ---- options -------------------------------------------------------------------------------------------------------------------------
    def set_theme(self, theme: str) -> None:
        self.theme = theme
        self.scene.set_theme(theme)

    def set_hidden(self, on: bool) -> None:
        self.opts.show_hidden = bool(on)
        if self.sheet is not None and self.design is not None:
            self.generate(self.design, self.selection, self.matrices)

    def set_sheet_size(self, name: str) -> None:
        self.opts.sheet = name
        if self.design is not None and self.sheet is not None:
            self.generate(self.design, self.selection, self.matrices)

    # ---- export ----------------------------------------------------------------------------------------------------------------------------
    def export(self, fmt: str, path: str) -> str:
        if self.sheet is None:
            raise ds.DesignError("Generate the drawing sheet first.")
        if fmt == "dxf":
            return str(ds.export_dxf(self.sheet, path))
        if fmt == "svg":
            return str(ds.export_svg(self.sheet, path, self.theme))
        if fmt == "pdf":
            return str(ds.export_pdf(self.sheet, path, "print"))
        raise ds.DesignError(f"Unknown format '{fmt}'.")
