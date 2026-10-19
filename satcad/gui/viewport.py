"""The 3D viewport: a VTK render window embedded in a Qt widget.

HOW THE GEOMETRY GETS ON SCREEN
  1. The CAD kernel (OpenCASCADE, through build123d) holds each body as an exact B-rep solid.
  2. `part_polydata()` asks the kernel to *tessellate* the solid into triangles and packs them into a vtkPolyData. A vtkPolyDataNormals filter
     computes smooth normals (split at sharp edges) so curved faces shade smoothly and flat faces stay crisp.
  3. `part_edges()` samples the solid's real B-rep edges into polylines, drawn as dark lines on top, which is what gives the
     "shaded with edges" look of professional CAD instead of a plain mesh.
  4. Each body becomes two actors (surface + edges). VTK draws them with Phong shading, a three-point light kit, FXAA anti-aliasing and
     polygon offset so the edge lines never z-fight with the surface.
  5. Picking uses a VTK prop picker on the surface actors, so clicking a body selects its feature in the Part Navigator.

HOW VTK IS EMBEDDED IN QT
  VTK renders into an OFF-SCREEN render window (no native window surface, which makes it behave identically on macOS, Windows and Linux,
  and avoids the platform-specific problems of putting a GL surface inside a Qt widget). Each frame is read back as pixels, supersampled
  2x for smooth edges, and painted by an ordinary QWidget. Mouse events are translated to camera moves here, so the 3D controls
  are fully under our control. The axis triad in the corner is drawn from the camera's view matrix.

MOUSE (NX style, plus Mac trackpad alternatives)
  Left click        select a body          Middle drag      rotate            Shift + middle drag   pan
  Wheel / pinch     zoom                   Right drag       zoom              Right click           context menu
  Alt + left drag   rotate (no middle button)                 Alt + Shift + left drag   pan
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

import vtkmodules.vtkRenderingOpenGL2  # noqa: F401  (registers the OpenGL backend)
import vtkmodules.vtkInteractionStyle  # noqa: F401
import vtkmodules.vtkRenderingFreeType  # noqa: F401
from build123d import GeomType
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray, vtk_to_numpy
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkFiltersCore import vtkPolyDataNormals
from vtkmodules.vtkRenderingCore import (vtkActor, vtkLightKit, vtkPolyDataMapper, vtkPropPicker, vtkRenderer, vtkRenderWindow,
                                         vtkWindowToImageFilter)

from . import theme

CLICK_SLOP_PX = 5


# ==========================================================================================================
#  Geometry conversion (pure functions: no window needed, so they are unit-tested)
# ==========================================================================================================
def part_polydata(part, tolerance: float | None = None) -> vtkPolyData:
    """Triangle mesh of a body with smooth-where-curved normals."""
    mn, mx = part.bbox()
    size = max(mx[i] - mn[i] for i in range(3)) or 1.0
    verts, tris = part.solid.tessellate(tolerance or max(size / 250.0, 0.02), 0.2)
    pts = np.array([(v.X, v.Y, v.Z) for v in verts], dtype=float).reshape(-1, 3)
    tri = np.array(tris, dtype=np.int64).reshape(-1, 3)
    poly = vtkPolyData()
    vtk_pts = vtkPoints()
    vtk_pts.SetData(numpy_to_vtk(pts, deep=True))
    poly.SetPoints(vtk_pts)
    cells = vtkCellArray()
    flat = np.column_stack([np.full(len(tri), 3, dtype=np.int64), tri]).ravel()
    cells.SetCells(len(tri), numpy_to_vtkIdTypeArray(flat, deep=True))
    poly.SetPolys(cells)
    normals = vtkPolyDataNormals()
    normals.SetInputData(poly)
    normals.SetFeatureAngle(35.0)
    normals.SplittingOn()
    normals.ConsistencyOn()
    normals.AutoOrientNormalsOff()
    normals.Update()
    out = vtkPolyData()
    out.ShallowCopy(normals.GetOutput())
    return out


def part_edges(part) -> vtkPolyData:
    """The solid's true B-rep edges as polylines (straight edges as 2 points, curves sampled)."""
    pts: list = []
    lines = vtkCellArray()
    for edge in part.solid.edges():
        if edge.geom_type == GeomType.LINE:
            samples = [edge.start_point(), edge.end_point()]
        else:
            n = int(min(96, max(12, edge.length / 4.0)))
            samples = [edge.position_at(t) for t in np.linspace(0.0, 1.0, n)]
        base = len(pts)
        pts.extend((s.X, s.Y, s.Z) for s in samples)
        lines.InsertNextCell(len(samples), list(range(base, base + len(samples))))
    poly = vtkPolyData()
    vtk_pts = vtkPoints()
    if pts:
        vtk_pts.SetData(numpy_to_vtk(np.array(pts, dtype=float), deep=True))
    poly.SetPoints(vtk_pts)
    poly.SetLines(lines)
    return poly


def _actor(poly: vtkPolyData, surface: bool) -> vtkActor:
    mapper = vtkPolyDataMapper()
    mapper.SetInputData(poly)
    mapper.ScalarVisibilityOff()
    if surface:
        mapper.SetResolveCoincidentTopologyToPolygonOffset()
        mapper.SetRelativeCoincidentTopologyPolygonOffsetParameters(1.0, 1.0)
    actor = vtkActor()
    actor.SetMapper(mapper)
    return actor


VIEWS = {   # name: (direction from the focal point toward the camera, view-up)
    "iso": ((1.0, -1.0, 0.8), (0, 0, 1)), "front": ((0, -1, 0), (0, 0, 1)), "back": ((0, 1, 0), (0, 0, 1)),
    "right": ((1, 0, 0), (0, 0, 1)), "left": ((-1, 0, 0), (0, 0, 1)), "top": ((0, 0, 1), (0, 1, 0)), "bottom": ((0, 0, -1), (0, -1, 0)),
}


# ==========================================================================================================
#  The widget
# ==========================================================================================================
SUPERSAMPLE = 2          # render at 2x and scale down: smooth edges without relying on GPU multisampling
AXIS_COLORS = {"X": "#ff5d5d", "Y": "#5fd08a", "Z": "#5aa8ff"}


class Viewport(QWidget):
    body_clicked = Signal(object, bool)          # body (part) name or None, ctrl/cmd held
    context_requested = Signal(object, object)   # body name or None, global QPoint

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(False)
        self.ren = vtkRenderer()
        self.ren.GradientBackgroundOn()
        self.ren.SetBackground(*theme.VIEWPORT_BOTTOM)
        self.ren.SetBackground2(*theme.VIEWPORT_TOP)
        vtkLightKit().AddLightsToRenderer(self.ren)
        self.rw = vtkRenderWindow()
        self.rw.SetOffScreenRendering(1)
        self.rw.SetMultiSamples(0)
        self.rw.SetSize(640, 480)
        self.rw.AddRenderer(self.ren)
        self._picker = vtkPropPicker()
        self._surfaces: dict[str, vtkActor] = {}
        self._edges: dict[str, vtkActor] = {}
        self._base_color: dict[str, tuple] = {}
        self._by_actor: dict[int, str] = {}
        self._selected: str | None = None
        self._mode = "shaded_edges"
        self._hidden: set[str] = set()
        self._press: tuple | None = None         # (x, y, button, modifiers) of a press awaiting its release
        self._drag: tuple | None = None          # ("rotate" | "pan" | "zoom", last x, last y)
        self._have_fit = False
        self._frame: QImage | None = None
        self._dirty = True
        self._pending = False
        self._empty_text = "Create a Block, Cylinder or Sphere from the Home tab"

    # ---- rendering -------------------------------------------------------------------------------------------------------------------
    def request_render(self) -> None:
        """Ask for a redraw; many requests in one event-loop turn collapse into a single render."""
        self._dirty = True
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._flush)

    Render = request_render                      # name used by earlier code

    def _flush(self) -> None:
        self._pending = False
        self.update()

    def _pixel_size(self) -> tuple[int, int, float]:
        ratio = self.devicePixelRatioF()
        return max(2, int(self.width() * ratio)), max(2, int(self.height() * ratio)), ratio

    def _render_frame(self) -> None:
        w, h, ratio = self._pixel_size()
        self.rw.SetSize(w * SUPERSAMPLE, h * SUPERSAMPLE)
        self.rw.Render()
        f = vtkWindowToImageFilter()
        f.SetInput(self.rw)
        f.SetInputBufferTypeToRGB()
        f.ReadFrontBufferOff()
        f.Update()
        img = f.GetOutput()
        iw, ih, _ = img.GetDimensions()
        arr = vtk_to_numpy(img.GetPointData().GetScalars()).reshape(ih, iw, -1)[::-1, :, :3].copy()
        big = QImage(arr.data, iw, ih, 3 * iw, QImage.Format_RGB888)
        small = big.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation) if SUPERSAMPLE > 1 else big.copy()
        small.setDevicePixelRatio(ratio)
        self._frame = small
        self._dirty = False

    def paintEvent(self, ev):
        if self._dirty or self._frame is None:
            self._render_frame()
        p = QPainter(self)
        p.drawImage(0, 0, self._frame)
        p.setRenderHint(QPainter.Antialiasing)
        self._draw_triad(p)
        if not self._surfaces:
            p.setPen(QColor(theme.PALETTE["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter, self._empty_text)
        p.end()

    def _draw_triad(self, p: QPainter) -> None:
        """Axis triad in the lower-left corner, drawn from the camera's view matrix (X red, Y green, Z blue)."""
        m = self.ren.GetActiveCamera().GetViewTransformMatrix()
        ox, oy, length = 46.0, self.height() - 46.0, 28.0
        axes = []
        for name, vec in (("X", (1, 0, 0)), ("Y", (0, 1, 0)), ("Z", (0, 0, 1))):
            sx = sum(m.GetElement(0, i) * vec[i] for i in range(3))
            sy = sum(m.GetElement(1, i) * vec[i] for i in range(3))
            sz = sum(m.GetElement(2, i) * vec[i] for i in range(3))
            axes.append((sz, name, sx, sy))
        font = QFont(p.font())
        font.setBold(True)
        font.setPointSize(9)
        p.setFont(font)
        for _sz, name, sx, sy in sorted(axes):                      # draw the axes pointing away from the viewer first
            end = QPointF(ox + sx * length, oy - sy * length)
            color = QColor(AXIS_COLORS[name])
            p.setPen(QPen(color, 2.4, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(ox, oy), end)
            p.drawText(QPointF(end.x() + (4 if sx >= 0 else -11), end.y() + (4 if sy <= 0 else 0) + 2), name)

    def resizeEvent(self, ev):
        self.request_render()

    # ---- content ---------------------------------------------------------------------------------------------------------------------
    def set_design(self, design, hidden: set | None = None, selected: str | None = None, fit: bool = False) -> None:
        """Rebuild the scene from the design's current bodies."""
        for actors in (self._surfaces, self._edges):
            for a in actors.values():
                self.ren.RemoveActor(a)
        self._surfaces, self._edges, self._base_color, self._by_actor = {}, {}, {}, {}
        self._hidden = set(hidden or ())
        for name, part in design.parts.items():
            surf = _actor(part_polydata(part), True)
            prop = surf.GetProperty()
            prop.SetColor(*part.color)
            prop.SetInterpolationToPhong()
            prop.SetAmbient(0.22)
            prop.SetDiffuse(0.78)
            prop.SetSpecular(0.35)
            prop.SetSpecularPower(35)
            edges = _actor(part_edges(part), False)
            edges.GetProperty().SetColor(*theme.EDGE_COLOR)
            edges.GetProperty().SetLineWidth(1.4 * SUPERSAMPLE)
            edges.GetProperty().LightingOff()
            edges.PickableOff()
            self.ren.AddActor(surf)
            self.ren.AddActor(edges)
            self._surfaces[name], self._edges[name] = surf, edges
            self._base_color[name] = tuple(part.color)
            self._by_actor[id(surf)] = name
        self._selected = selected if selected in self._surfaces else None
        self._apply_state()
        if fit or (not self._have_fit and self._surfaces):
            self.set_view("iso")
        self.request_render()

    def _apply_state(self) -> None:
        for name, surf in self._surfaces.items():
            visible = name not in self._hidden
            wire = self._mode == "wireframe"
            surf.SetVisibility(visible and not wire)
            self._edges[name].SetVisibility(visible and self._mode != "shaded")
            sel = name == self._selected
            surf.GetProperty().SetColor(*(theme.SELECT_COLOR if sel else self._base_color[name]))
            self._edges[name].GetProperty().SetColor(*((1.0, 0.85, 0.3) if sel else theme.EDGE_COLOR))
            self._edges[name].GetProperty().SetLineWidth((2.6 if sel else 1.4) * SUPERSAMPLE)

    def set_selected(self, name: str | None) -> None:
        self._selected = name if name in self._surfaces else None
        self._apply_state()
        self.request_render()

    def set_visible(self, name: str, visible: bool) -> None:
        (self._hidden.discard if visible else self._hidden.add)(name)
        self._apply_state()
        self.request_render()

    def set_mode(self, mode: str) -> None:
        """'shaded_edges' (default), 'shaded' or 'wireframe'."""
        self._mode = mode if mode in ("shaded_edges", "shaded", "wireframe") else "shaded_edges"
        self._apply_state()
        self.request_render()

    # ---- camera ----------------------------------------------------------------------------------------------------------------------
    def _bounds(self):
        b = [1e300, -1e300, 1e300, -1e300, 1e300, -1e300]
        any_visible = False
        for name, surf in self._surfaces.items():
            if name in self._hidden:
                continue
            sb = surf.GetBounds()
            for i in (0, 2, 4):
                b[i], b[i + 1] = min(b[i], sb[i]), max(b[i + 1], sb[i + 1])
            any_visible = True
        return b if any_visible else None

    def set_view(self, name: str = "iso") -> None:
        direction, up = VIEWS.get(name, VIEWS["iso"])
        b = self._bounds() or [-500, 500, -500, 500, -500, 500]
        centre = [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2]
        cam = self.ren.GetActiveCamera()
        d = np.array(direction, float)
        d /= np.linalg.norm(d)
        cam.SetFocalPoint(*centre)
        cam.SetPosition(*(np.array(centre) + d * 1000.0))
        cam.SetViewUp(*up)
        self.ren.ResetCamera(b)
        cam.Dolly(1.05)
        self.ren.ResetCameraClippingRange()
        self._have_fit = True
        self.request_render()

    def fit(self) -> None:
        b = self._bounds()
        if b:
            self.ren.ResetCamera(b)
            self.ren.ResetCameraClippingRange()
            self.request_render()

    def rotate_by(self, dx: float, dy: float) -> None:
        """Orbit about the focal point, keeping Z up (a turntable). dx, dy in screen pixels."""
        cam = self.ren.GetActiveCamera()
        focal = np.array(cam.GetFocalPoint())
        d = np.array(cam.GetPosition()) - focal
        r = np.linalg.norm(d)
        yaw = np.arctan2(d[1], d[0]) - np.radians(dx * 0.45)
        elev = np.arcsin(np.clip(d[2] / r, -1, 1)) + np.radians(dy * 0.45)
        elev = float(np.clip(elev, -np.radians(89.5), np.radians(89.5)))
        new = focal + r * np.array([np.cos(elev) * np.cos(yaw), np.cos(elev) * np.sin(yaw), np.sin(elev)])
        cam.SetPosition(*new)
        cam.SetViewUp(0, 0, 1)
        self.ren.ResetCameraClippingRange()
        self.request_render()

    def pan_by(self, dx: float, dy: float) -> None:
        cam = self.ren.GetActiveCamera()
        pos, focal, up = np.array(cam.GetPosition()), np.array(cam.GetFocalPoint()), np.array(cam.GetViewUp())
        forward = focal - pos
        dist = np.linalg.norm(forward)
        forward /= dist
        right = np.cross(forward, up)
        right /= np.linalg.norm(right)
        true_up = np.cross(right, forward)
        per_pixel = 2.0 * dist * np.tan(np.radians(cam.GetViewAngle()) / 2.0) / max(1, self.height())
        shift = (-dx * right + dy * true_up) * per_pixel
        cam.SetPosition(*(pos + shift))
        cam.SetFocalPoint(*(focal + shift))
        self.ren.ResetCameraClippingRange()
        self.request_render()

    def zoom_by(self, factor: float) -> None:
        cam = self.ren.GetActiveCamera()
        cam.Dolly(float(np.clip(factor, 0.2, 5.0)))
        self.ren.ResetCameraClippingRange()
        self.request_render()

    # ---- picking and mouse -----------------------------------------------------------------------------------------------------------
    def pick(self, x: float, y: float) -> str | None:
        """Body under a widget position (device-independent pixels, origin top-left), or None."""
        _w, h, ratio = self._pixel_size()
        px, py = int(x * ratio * SUPERSAMPLE), int((self.height() - y) * ratio * SUPERSAMPLE)
        self.rw.SetSize(_w * SUPERSAMPLE, h * SUPERSAMPLE)
        if self._picker.Pick(px, py, 0, self.ren):
            actor = self._picker.GetActor()
            if actor is not None:
                return self._by_actor.get(id(actor))
        return None

    def mousePressEvent(self, ev):
        btn, mods = ev.button(), ev.modifiers()
        pos = ev.position()
        alt, shift = bool(mods & Qt.AltModifier), bool(mods & Qt.ShiftModifier)
        self._press = (pos.x(), pos.y(), btn, mods)
        mode = None
        if btn == Qt.MiddleButton or (btn == Qt.LeftButton and alt):
            mode = "pan" if shift else "rotate"
        elif btn == Qt.RightButton:
            mode = "zoom"
        self._drag = (mode, pos.x(), pos.y()) if mode else None

    def mouseMoveEvent(self, ev):
        if not self._drag:
            return
        mode, lx, ly = self._drag
        pos = ev.position()
        dx, dy = pos.x() - lx, pos.y() - ly
        self._drag = (mode, pos.x(), pos.y())
        if mode == "rotate":
            self.rotate_by(dx, dy)
        elif mode == "pan":
            self.pan_by(dx, dy)
        elif mode == "zoom":
            self.zoom_by(1.0 + dy * 0.01)

    def mouseReleaseEvent(self, ev):
        self._drag = None
        if self._press is None:
            return
        x0, y0, btn, mods = self._press
        self._press = None
        pos = ev.position()
        if abs(pos.x() - x0) + abs(pos.y() - y0) > CLICK_SLOP_PX:
            return
        if btn == Qt.LeftButton and not (mods & Qt.AltModifier):
            self.body_clicked.emit(self.pick(pos.x(), pos.y()), bool(mods & (Qt.ControlModifier | Qt.MetaModifier)))
        elif btn == Qt.RightButton:
            self.context_requested.emit(self.pick(pos.x(), pos.y()), ev.globalPosition().toPoint())

    def wheelEvent(self, ev):
        steps = ev.angleDelta().y() / 120.0
        if steps:
            self.zoom_by(1.12 ** steps)
        ev.accept()

    # ---- output ----------------------------------------------------------------------------------------------------------------------
    def grab_image(self) -> QImage:
        """The rendered 3D view as a QImage (for screenshots)."""
        self._render_frame()
        return self._frame.copy()
