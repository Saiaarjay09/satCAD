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
  Wheel / 2-finger scroll  zoom                   Right drag       zoom              Right click           context menu
  Alt + left drag   rotate (no middle button)                 Alt + Shift + left drag   pan
"""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

import vtkmodules.vtkRenderingOpenGL2  # noqa: F401  (registers the OpenGL backend)
import vtkmodules.vtkInteractionStyle  # noqa: F401
import vtkmodules.vtkRenderingFreeType  # noqa: F401
from build123d import GeomType
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray, vtk_to_numpy
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkFiltersCore import vtkPolyDataNormals, vtkTubeFilter
from vtkmodules.vtkFiltersSources import vtkLineSource
from vtkmodules.vtkCommonCore import vtkUnsignedCharArray
from vtkmodules.vtkCommonMath import vtkMatrix4x4
from vtkmodules.vtkRenderingCore import (vtkActor, vtkCellPicker, vtkLightKit, vtkPolyDataMapper, vtkPropPicker, vtkRenderer, vtkRenderWindow)

from . import theme

CLICK_SLOP_PX = 5


# ==========================================================================================================
#  Geometry conversion (pure functions: no window needed, so they are unit-tested)
# ==========================================================================================================
AXIS_NAMES = "xyz"


def part_face_data(part, tolerance: float | None = None) -> list[dict]:
    """Tessellate a solid face by face. Each entry: pts (N,3), tris (M,3), plus what the interface needs to act on the face: whether it is flat,
    its outward normal and centre, and `side` ('+z', '-x' ...) when it is a flat face square to an axis (what Hole / Shell commands take)."""
    mn, mx = part.bbox()
    size = max(mx[i] - mn[i] for i in range(3)) or 1.0
    tol = tolerance or max(size / 250.0, 0.02)
    faces = []
    for face in part.solid.faces():
        verts, tris = face.tessellate(tol, 0.2)
        if not tris:
            continue
        pts = np.array([(v.X, v.Y, v.Z) for v in verts], dtype=float).reshape(-1, 3)
        planar = face.geom_type == GeomType.PLANE
        c = face.center()
        normal, side = None, None
        if planar:
            n = face.normal_at()
            normal = (n.X, n.Y, n.Z)
            axis = int(np.argmax(np.abs(normal)))
            if abs(normal[axis]) > 0.9999:
                side = ("+" if normal[axis] > 0 else "-") + AXIS_NAMES[axis]
        faces.append(dict(pts=pts, tris=np.array(tris, dtype=np.int64).reshape(-1, 3), planar=planar, normal=normal, side=side,
                          centre=(c.X, c.Y, c.Z), area=float(face.area)))
    return faces


def polydata_from_faces(faces: list[dict]) -> vtkPolyData:
    """One triangle mesh from per-face tessellations, with a 'face_id' cell array and smooth-where-curved normals."""
    pts, tris, ids, base = [], [], [], 0
    for i, f in enumerate(faces):
        pts.append(f["pts"])
        tris.append(f["tris"] + base)
        ids.append(np.full(len(f["tris"]), i, dtype=np.int32))
        base += len(f["pts"])
    pts = np.vstack(pts) if pts else np.zeros((0, 3))
    tri = np.vstack(tris) if tris else np.zeros((0, 3), dtype=np.int64)
    poly = vtkPolyData()
    vtk_pts = vtkPoints()
    vtk_pts.SetData(numpy_to_vtk(pts, deep=True))
    poly.SetPoints(vtk_pts)
    cells = vtkCellArray()
    flat = np.column_stack([np.full(len(tri), 3, dtype=np.int64), tri]).ravel()
    cells.SetCells(len(tri), numpy_to_vtkIdTypeArray(flat, deep=True))
    poly.SetPolys(cells)
    fid = numpy_to_vtk(np.concatenate(ids) if ids else np.zeros(0, dtype=np.int32), deep=True)
    fid.SetName("face_id")
    poly.GetCellData().AddArray(fid)
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


def part_polydata(part, tolerance: float | None = None) -> vtkPolyData:
    """Triangle mesh of a body with smooth-where-curved normals (cell array 'face_id' says which B-rep face each triangle belongs to)."""
    return polydata_from_faces(part_face_data(part, tolerance))


def face_polydata(face: dict) -> vtkPolyData:
    """Just one face (used for the selection overlay)."""
    poly = vtkPolyData()
    vtk_pts = vtkPoints()
    vtk_pts.SetData(numpy_to_vtk(face["pts"], deep=True))
    poly.SetPoints(vtk_pts)
    cells = vtkCellArray()
    flat = np.column_stack([np.full(len(face["tris"]), 3, dtype=np.int64), face["tris"]]).ravel()
    cells.SetCells(len(face["tris"]), numpy_to_vtkIdTypeArray(flat, deep=True))
    poly.SetPolys(cells)
    return poly


def face_pick_info(part, face: dict, point) -> dict:
    """What a Hole / Shell command needs from a picked flat face: its side token and the hole offsets of the picked point, measured the way the
    engine measures them (from the body's bounding-box centre along the face's two sliding axes)."""
    side = face["side"]
    info = dict(side=side, planar=face["planar"], point=tuple(point), normal=face["normal"], offset_u=0.0, offset_v=0.0, at_outer_face=False)
    if side:
        a = AXIS_NAMES.index(side[1])
        u, v = (a + 1) % 3, (a + 2) % 3
        mn, mx = part.bbox()
        centre = [(mn[i] + mx[i]) / 2 for i in range(3)]
        info["offset_u"] = round(float(point[u]) - centre[u], 3)
        info["offset_v"] = round(float(point[v]) - centre[v], 3)
        outer = mx[a] if side[0] == "+" else mn[a]
        info["at_outer_face"] = abs(face["centre"][a] - outer) < 0.05
    return info


def part_edge_polylines(part) -> list:
    """The solid's true B-rep edges as polylines (arrays of 3D points): straight edges are 2 points, curves are sampled."""
    out = []
    for edge in part.solid.edges():
        if edge.geom_type == GeomType.LINE:
            samples = [edge.start_point(), edge.end_point()]
        else:
            n = int(min(96, max(12, edge.length / 4.0)))
            samples = [edge.position_at(t) for t in np.linspace(0.0, 1.0, n)]
        out.append(np.array([(s.X, s.Y, s.Z) for s in samples], dtype=float))
    return out


def edges_polydata(polylines: list) -> vtkPolyData:
    pts, lines = [], vtkCellArray()
    base = 0
    for poly_line in polylines:
        pts.append(poly_line)
        lines.InsertNextCell(len(poly_line), list(range(base, base + len(poly_line))))
        base += len(poly_line)
    poly = vtkPolyData()
    vtk_pts = vtkPoints()
    if pts:
        vtk_pts.SetData(numpy_to_vtk(np.vstack(pts), deep=True))
    poly.SetPoints(vtk_pts)
    poly.SetLines(lines)
    return poly


def part_edges(part) -> vtkPolyData:
    """The solid's true B-rep edges as a line mesh."""
    return edges_polydata(part_edge_polylines(part))


def _translated_faces(faces: list, d) -> list:
    off = np.asarray(d, dtype=float)
    return [dict(f, pts=f["pts"] + off, centre=tuple(np.asarray(f["centre"]) + off)) for f in faces]


def _mesh_signature(part) -> tuple:
    mn, mx = part.bbox()
    return (part.kind, round(part.volume_mm3, 3), tuple(round(v, 3) for v in (*mn, *mx)))


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
    face_clicked = Signal(object, object)        # body name, face info dict (see face_pick_info); only in 'face' selection mode

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
        self.ren.SetUseDepthPeeling(1)                  # correct see-through rendering for X-ray (only costs anything when something is translucent)
        self.ren.SetMaximumNumberOfPeels(6)
        self.ren.SetOcclusionRatio(0.05)
        self.rw_alpha = True
        self.rw = vtkRenderWindow()
        self.rw.SetOffScreenRendering(1)
        self.rw.SetMultiSamples(0)
        self.rw.SetAlphaBitPlanes(1)
        self.rw.SetSize(640, 480)
        self.rw.AddRenderer(self.ren)
        self._picker = vtkPropPicker()
        self._surfaces: dict[str, vtkActor] = {}
        self._edges: dict[str, vtkActor] = {}
        self._thin: dict[str, bool] = {}                                  # cables and fibres: thinner than a few pixels, so their edge lines carry the colour
        self._base_color: dict[str, tuple] = {}
        self._by_actor: dict[int, str] = {}
        self._faces: dict[str, list] = {}
        self._parts: dict[str, object] = {}
        self._mesh_cache: dict = {}
        self._cell_picker = vtkCellPicker()
        self._cell_picker.SetTolerance(0.0005)
        self._face_actor: vtkActor | None = None
        self._filter = "body"
        self._selected: str | None = None
        self._mode = "shaded_edges"
        self._hidden: set[str] = set()
        self._press: tuple | None = None         # (x, y, button, modifiers) of a press awaiting its release
        self._drag: tuple | None = None          # ("rotate" | "pan" | "zoom", last x, last y)
        self._have_fit = False
        self._frame: QImage | None = None
        self._dirty = True
        self._pending = False
        self._pixels = None
        self._ss = SUPERSAMPLE                       # supersampling factor (lowered while an animation plays)
        self.last_frame_ms = 0.0
        self._groups: dict[str, list] = {}           # motion group -> part names
        self._group_matrix: dict[str, vtkMatrix4x4] = {}
        self._color_override: dict[str, tuple] = {}
        self._opacity: dict[str, float] = {}
        self._rays: dict[str, dict] = {}                                 # glowing laser rays (digital twin): id -> line source, tube filters and actors
        self._hud: tuple | None = None                                   # (rows, status) painted over the frame in the top-right corner
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
        t0 = time.perf_counter()
        w, h, ratio = self._pixel_size()
        self.rw.SetSize(w * self._ss, h * self._ss)
        self.rw.Render()
        iw, ih = w * self._ss, h * self._ss
        if self._pixels is None:
            self._pixels = vtkUnsignedCharArray()
        self.rw.GetPixelData(0, 0, iw - 1, ih - 1, 0, self._pixels, 0)       # direct read-back: ~10x cheaper than vtkWindowToImageFilter
        arr = vtk_to_numpy(self._pixels).reshape(ih, iw, -1)[::-1, :, :3].copy()
        big = QImage(arr.data, iw, ih, 3 * iw, QImage.Format_RGB888)
        small = big.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation) if self._ss > 1 else big.copy()
        small.setDevicePixelRatio(ratio)
        self._frame = small
        self._dirty = False
        self.last_frame_ms = (time.perf_counter() - t0) * 1000.0

    def paintEvent(self, ev):
        if self._dirty or self._frame is None:
            self._render_frame()
        p = QPainter(self)
        p.drawImage(0, 0, self._frame)
        p.setRenderHint(QPainter.Antialiasing)
        self._draw_triad(p)
        self._draw_hud(p)
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
        self._surfaces, self._edges, self._base_color, self._by_actor, self._thin = {}, {}, {}, {}, {}
        self._faces, self._parts = {}, {}
        self.clear_face()
        self._hidden = set(hidden or ())
        built: dict[str, tuple] = {}                 # this build's meshes, so linear-pattern copies can reuse their source's
        for name, part in design.parts.items():
            faces, lines = self._mesh_for(part, built)
            built[name] = (faces, lines)
            self._faces[name], self._parts[name] = faces, part
            surf = _actor(polydata_from_faces(faces), True)
            prop = surf.GetProperty()
            prop.SetColor(*part.color)
            prop.SetInterpolationToPhong()
            prop.SetAmbient(0.22)
            prop.SetDiffuse(0.78)
            prop.SetSpecular(0.35)
            prop.SetSpecularPower(35)
            edges = _actor(edges_polydata(lines), False)
            edges.GetProperty().SetColor(*theme.EDGE_COLOR)
            edges.GetProperty().SetLineWidth(1.0 * self._ss)
            edges.GetProperty().LightingOff()
            edges.PickableOff()
            self.ren.AddActor(surf)
            self.ren.AddActor(edges)
            self._surfaces[name], self._edges[name] = surf, edges
            self._base_color[name] = tuple(part.color)
            lo, hi = part.bbox()
            radius = part.params.get("radius") if part.kind == "sweep" else None
            self._thin[name] = (radius is not None and radius < 8.0) or sorted(h - l for l, h in zip(lo, hi))[1] < 8.0     # a thin swept cable, or two small dimensions
            self._by_actor[id(surf)] = name
        self._selected = selected if selected in self._surfaces else None
        self._rebind_groups()
        self._apply_state()
        if fit or (not self._have_fit and self._surfaces):
            self.set_view("iso")
        self.request_render()

    def _mesh_for(self, part, built: dict):
        """(face data, edge polylines) for a part, reusing work: a linear-pattern copy is its source's mesh moved by the pattern offset, and a
        part unchanged since an earlier rebuild is served from a cache (so editing one feature does not re-mesh the whole assembly)."""
        src = part.translated_from
        if src in built and part.translation is not None and src in self._parts:
            try:
                same = abs(part.volume_mm3 - self._parts[src].volume_mm3) < 1e-6 * max(1.0, part.volume_mm3)
            except Exception:
                same = False
            if same:
                faces, lines = built[src]
                return _translated_faces(faces, part.translation), [ln + np.asarray(part.translation) for ln in lines]
        key = (part.name, _mesh_signature(part))
        hit = self._mesh_cache.get(key)
        if hit is None:
            hit = (part_face_data(part), part_edge_polylines(part))
            if len(self._mesh_cache) > 3000:
                self._mesh_cache.clear()
            self._mesh_cache[key] = hit
        return hit

    def _apply_state(self) -> None:
        for name, surf in self._surfaces.items():
            visible = name not in self._hidden
            wire = self._mode == "wireframe"
            surf.SetVisibility(visible and not wire)
            self._edges[name].SetVisibility(visible and self._mode != "shaded")
            op = self._opacity.get(name, 1.0)
            surf.GetProperty().SetOpacity(op)
            self._edges[name].GetProperty().SetOpacity(max(op, 0.35) if op < 1.0 else 1.0)
            surf.SetPickable(op >= 0.5)                                    # you can click through see-through parts (X-ray) to what is inside
            sel = name == self._selected
            surf.GetProperty().SetColor(*(theme.SELECT_COLOR if sel else self._color_override.get(name, self._base_color[name])))
            thin = self._thin.get(name, False)
            self._edges[name].GetProperty().SetColor(*((1.0, 0.85, 0.3) if sel else self._color_override.get(name, self._base_color[name]) if thin else theme.EDGE_COLOR))
            self._edges[name].GetProperty().SetLineWidth((2.0 if sel or thin else 1.0) * self._ss)     # whole pixels: fractional widths are ~5x slower

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

    # ---- motion: move existing actors without re-meshing ----------------------------------------------------------------------------
    def bind_motion_groups(self, groups: dict[str, list]) -> None:
        """Declare which parts move together: {group id: [part names]}. Every actor of those parts shares ONE 4x4 matrix per group, so
        moving a group later is a single matrix update, whatever the number of parts, and no mesh is rebuilt."""
        self._groups = {g: list(parts) for g, parts in groups.items()}
        self._rebind_groups()
        self.request_render()

    def _rebind_groups(self) -> None:
        for actor in list(self._surfaces.values()) + list(self._edges.values()):
            actor.SetUserMatrix(None)
        self._group_matrix = {}
        for g, parts in self._groups.items():
            m = vtkMatrix4x4()
            self._group_matrix[g] = m
            for p in parts:
                if p in self._surfaces:
                    self._surfaces[p].SetUserMatrix(m)
                    self._edges[p].SetUserMatrix(m)

    def set_group_matrices(self, matrices: dict[str, np.ndarray]) -> None:
        """Pose the groups: {group id: 4x4 world matrix}. Groups not listed keep their last pose."""
        for g, arr in matrices.items():
            m = self._group_matrix.get(g)
            if m is not None:
                m.DeepCopy(np.asarray(arr, dtype=float).ravel().tolist())
        self.request_render()

    def set_part_opacity(self, opacity: dict[str, float] | None) -> None:
        """Make some parts see-through: {part name: opacity 0..1}. Parts not listed are opaque. None / {} restores everything."""
        self._opacity = {k: float(x) for k, x in (opacity or {}).items()}
        self._apply_state()
        self.request_render()

    def set_hidden(self, hidden: set) -> None:
        """Replace the set of hidden parts in one go (a whole assembly can be hidden at once)."""
        self._hidden = set(hidden)
        self._apply_state()
        self.request_render()

    def set_part_colors(self, colors: dict[str, tuple] | None) -> None:
        """Override the display colour of some parts (an animation can colour them); None / {} restores the originals."""
        self._color_override = dict(colors or {})
        self._apply_state()
        self.request_render()

    def set_motion_quality(self, fast: bool) -> None:
        """Fast mode renders without supersampling (used while an animation plays); the full-quality frame returns when it stops."""
        ss = 1 if fast else SUPERSAMPLE
        if ss != self._ss:
            self._ss = ss
            self._apply_state()
            self.request_render()

    # ---- overlays: glowing rays and the heads-up display (digital twin) -----------------------------------------------------------------
    def set_overlay_rays(self, rays) -> None:
        """Draw glowing rays: [{id, p0, p1, color, radius?}] in model millimetres. Rays with a known id are moved, not rebuilt; ids not listed are removed.
        Each is a bright core plus a wider translucent halo. They are not pickable and do not count for Fit."""
        wanted = {r["id"]: r for r in (rays or [])}
        for rid in [i for i in self._rays if i not in wanted]:
            for _tube, actor in self._rays.pop(rid)["parts"]:
                self.ren.RemoveActor(actor)
        for rid, r in wanted.items():
            entry = self._rays.get(rid)
            if entry is None:
                src = vtkLineSource()
                parts = []
                for opacity, scale in ((1.0, 1.0), (0.22, 4.5)):
                    tube = vtkTubeFilter()
                    tube.SetInputConnection(src.GetOutputPort())
                    tube.SetNumberOfSides(14)
                    mapper = vtkPolyDataMapper()
                    mapper.SetInputConnection(tube.GetOutputPort())
                    actor = vtkActor()
                    actor.SetMapper(mapper)
                    actor.GetProperty().LightingOff()
                    actor.GetProperty().SetOpacity(opacity)
                    actor.PickableOff()
                    self.ren.AddActor(actor)
                    parts.append((tube, actor, scale))
                entry = self._rays[rid] = dict(src=src, parts=[(t, a) for t, a, _ in parts], scales=[sc for _, _, sc in parts])
            entry["src"].SetPoint1(*[float(v) for v in r["p0"]])
            entry["src"].SetPoint2(*[float(v) for v in r["p1"]])
            length = float(np.linalg.norm(np.asarray(r["p1"], float) - np.asarray(r["p0"], float)))
            radius = float(r.get("radius", max(5.0, length / 2500.0)))
            for (tube, actor), scale in zip(entry["parts"], entry["scales"]):
                tube.SetRadius(radius * scale)
                actor.GetProperty().SetColor(*[float(v) for v in r.get("color", (0.2, 1.0, 0.6))])
        self.request_render()

    def set_hud(self, rows, status: str = "LIVE") -> None:
        """Show a translucent heads-up panel in the top-right corner. rows: [(label, value) | ('title', text) | ('small', text)]; None hides it."""
        self._hud = (list(rows), status) if rows else None
        self.update()

    def _draw_hud(self, p: QPainter) -> None:
        if not self._hud:
            return
        rows, status = self._hud
        title_f, label_f, value_f, small_f = QFont(p.font()), QFont(p.font()), QFont(p.font()), QFont(p.font())
        title_f.setBold(True)
        title_f.setPointSize(10)
        label_f.setPointSize(9)
        value_f.setBold(True)
        value_f.setPointSize(11)
        small_f.setPointSize(8)
        for f in (title_f, label_f, value_f, small_f):
            f.setStyleHint(QFont.Monospace)
        m = {k: QFontMetrics(f) for k, f in (("title", title_f), ("label", label_f), ("value", value_f), ("small", small_f))}
        pad, line_gap = 12, 5
        width, height = 0, pad
        for kind, *rest in rows:
            if kind in ("title", "small"):
                width = max(width, m[kind].horizontalAdvance(rest[0]))
                height += m[kind].height() + (line_gap + 3 if kind == "title" else 1)
            else:
                width = max(width, m["label"].horizontalAdvance(kind) + 14 + m["value"].horizontalAdvance(rest[0]))
                height += max(m["label"].height(), m["value"].height()) + line_gap
        width = min(width + 2 * pad + 6, max(self.width() - 24, 80))
        x0, y0 = self.width() - width - 12, 12
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 176, 32, 150), 1.2))
        p.setBrush(QColor(10, 14, 20, 185))
        p.drawRoundedRect(x0, y0, width, height + pad - 4, 8, 8)
        y = y0 + pad
        dot = {"LIVE": QColor("#5fd08a"), "STALE": QColor("#ffb020")}.get(status, QColor("#8a929c"))
        for kind, *rest in rows:
            if kind == "title":
                p.setFont(title_f)
                p.setPen(QColor("#ffcf70"))
                y += m["title"].ascent()
                p.drawText(x0 + pad, y, rest[0].replace("\u25cf", "").rstrip())
                p.setBrush(dot)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(x0 + width - pad - 5, y - m["title"].ascent() / 2 + 1), 5, 5)
                y += m["title"].descent() + line_gap + 3
            elif kind == "small":
                p.setFont(small_f)
                p.setPen(QColor("#9aa6b4"))
                y += m["small"].ascent()
                p.drawText(x0 + pad, y, m["small"].elidedText(rest[0], Qt.ElideRight, width - 2 * pad))
                y += m["small"].descent() + 1
            else:
                p.setFont(label_f)
                p.setPen(QColor("#9aa6b4"))
                y += m["value"].ascent()
                p.drawText(x0 + pad, y, kind)
                p.setFont(value_f)
                p.setPen(QColor("#e8edf3"))
                text = rest[0]
                p.drawText(x0 + width - pad - m["value"].horizontalAdvance(text), y, text)
                y += m["value"].descent() + line_gap
        p.restore()

    # ---- selection filter and face overlay -------------------------------------------------------------------------------------------
    def set_filter(self, mode: str) -> None:
        """'body' (a click selects a whole body) or 'face' (a click also picks the flat face under the cursor)."""
        self._filter = "face" if mode == "face" else "body"
        if self._filter == "body":
            self.clear_face()

    @property
    def filter(self) -> str:
        return self._filter

    def show_face(self, name: str, index: int) -> None:
        self.clear_face()
        faces = self._faces.get(name)
        if not faces or not 0 <= index < len(faces):
            return
        actor = _actor(face_polydata(faces[index]), True)
        actor.GetMapper().SetRelativeCoincidentTopologyPolygonOffsetParameters(-2.0, -2.0)    # draw in front of the surface
        prop = actor.GetProperty()
        prop.SetColor(0.15, 0.75, 1.0)
        prop.SetOpacity(0.85)
        prop.LightingOff()
        actor.PickableOff()
        self.ren.AddActor(actor)
        self._face_actor = actor
        self.request_render()

    def clear_face(self) -> None:
        if self._face_actor is not None:
            self.ren.RemoveActor(self._face_actor)
            self._face_actor = None
            self.request_render()

    def pick_face(self, x: float, y: float):
        """(body name, face index, world point) under a widget position, or None."""
        _w, h, ratio = self._pixel_size()
        px, py = int(x * ratio * self._ss), int((self.height() - y) * ratio * self._ss)
        self.rw.SetSize(_w * self._ss, h * self._ss)
        if not self._cell_picker.Pick(px, py, 0, self.ren):
            return None
        actor = self._cell_picker.GetActor()
        name = self._by_actor.get(id(actor)) if actor is not None else None
        cell = self._cell_picker.GetCellId()
        if name is None or cell < 0:
            return None
        ids = actor.GetMapper().GetInput().GetCellData().GetArray("face_id")
        if ids is None or cell >= ids.GetNumberOfTuples():
            return None
        return name, int(ids.GetValue(cell)), self._cell_picker.GetPickPosition()

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
        px, py = int(x * ratio * self._ss), int((self.height() - y) * ratio * self._ss)
        self.rw.SetSize(_w * self._ss, h * self._ss)
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
            ctrl = bool(mods & (Qt.ControlModifier | Qt.MetaModifier))
            if self._filter == "face":
                hit = self.pick_face(pos.x(), pos.y())
                if hit is None:
                    self.clear_face()
                    self.body_clicked.emit(None, ctrl)
                    return
                name, index, point = hit
                self.body_clicked.emit(name, ctrl)
                self.show_face(name, index)
                self.face_clicked.emit(name, face_pick_info(self._parts[name], self._faces[name][index], point))
                return
            self.body_clicked.emit(self.pick(pos.x(), pos.y()), ctrl)
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
