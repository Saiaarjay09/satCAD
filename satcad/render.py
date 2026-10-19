"""Picture of the design: four views (isometric, top, front, side) as a PNG."""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")                                   # no window needed
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.collections import PolyCollection
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

from .engine import Design, DesignError

_LIGHT = np.array([0.4, -0.5, 0.75])
_LIGHT = _LIGHT / np.linalg.norm(_LIGHT)

# name -> (title, kind, ...). Isometric is 3D; the other three are flat engineering views.
#   2D views: (title, horizontal axis, vertical axis, depth axis, +1 if larger depth is nearer the viewer)
VIEWS = {
    "iso": ("Isometric", 24, -58),
    "top": ("Top view (X across, Y up)", 0, 1, 2, +1),
    "front": ("Front view from +X (Y across, Z up)", 1, 2, 0, +1),
    "side": ("Side view from -Y (X across, Z up)", 0, 2, 1, -1),
}
AXIS_NAME = "XYZ"


def _triangles(design: Design):
    tris, colors = [], []
    size = max(design.measure()["overall_size_mm"])
    tol = max(size / 400.0, 0.01)
    for p in design.parts.values():
        verts, faces = p.solid.tessellate(tol, 0.2)
        v = np.array([(q.X, q.Y, q.Z) for q in verts], dtype=float)
        for a, b, c in faces:
            t = v[[a, b, c]]
            n = np.cross(t[1] - t[0], t[2] - t[0])
            norm = np.linalg.norm(n)
            shade = 0.55 + 0.45 * (abs(float(n @ _LIGHT)) / norm if norm > 0 else 0.5)
            tris.append(t)
            colors.append((*(np.clip(np.array(p.color) * shade, 0, 1)), 1.0))
    return np.array(tris), colors


def render_png(design: Design, views=("iso", "top", "front", "side"), width_px: int = 1400) -> bytes:
    if not design.parts:
        raise DesignError("Nothing to draw: the design is empty. Add a chassis first.")
    tris, colors = _triangles(design)
    lo, hi = tris.reshape(-1, 3).min(axis=0), tris.reshape(-1, 3).max(axis=0)
    centre, half = (lo + hi) / 2, float(np.max(hi - lo)) / 2 * 1.08
    meas = design.measure()

    n = len(views)
    cols = 2 if n > 1 else 1
    rows = -(-n // cols)
    fig = plt.figure(figsize=(width_px / 100, width_px / 100 * rows / cols * 0.62), dpi=100)
    fig.patch.set_facecolor("white")
    for i, key in enumerate(views):
        spec = VIEWS[key]
        if key == "iso":
            ax = fig.add_subplot(rows, cols, i + 1, projection="3d")
            ax.add_collection3d(Poly3DCollection(tris, facecolors=colors, edgecolor="none", linewidths=0))
            ax.set_xlim(centre[0] - half, centre[0] + half)
            ax.set_ylim(centre[1] - half, centre[1] + half)
            ax.set_zlim(centre[2] - half, centre[2] + half)
            ax.set_box_aspect((1, 1, 1), zoom=1.5)
            ax.view_init(elev=spec[1], azim=spec[2])
            ax.set_xlabel("X (mm)", fontsize=8)
            ax.set_ylabel("Y (mm)", fontsize=8)
            ax.set_zlabel("Z (mm)", fontsize=8)
            ax.tick_params(labelsize=7)
            ax.set_title(spec[0], fontsize=11)
            continue
        title, h, v, dpt, near = spec
        ax = fig.add_subplot(rows, cols, i + 1)
        order = np.argsort(near * tris[:, :, dpt].mean(axis=1))            # draw far triangles first
        ax.add_collection(PolyCollection(tris[order][:, :, [h, v]], facecolors=[colors[k] for k in order],
                                         edgecolor="none", linewidths=0))
        pad_h, pad_v = 0.08 * (hi[h] - lo[h] or 1), 0.08 * (hi[v] - lo[v] or 1)
        ax.set_xlim(lo[h] - pad_h, hi[h] + pad_h)
        ax.set_ylim(lo[v] - pad_v, hi[v] + pad_v)
        ax.set_aspect("equal")
        ax.grid(True, linewidth=0.4, alpha=0.5)
        ax.set_xlabel(f"{AXIS_NAME[h]} (mm)", fontsize=8)
        ax.set_ylabel(f"{AXIS_NAME[v]} (mm)", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.set_title(title, fontsize=11)
    roles = {}
    for p in design.parts.values():
        roles.setdefault(p.role, p.color)
    fig.legend(handles=[Line2D([0], [0], marker="s", linestyle="", markersize=10, markerfacecolor=c, markeredgecolor="none",
                               label=r) for r, c in roles.items()], loc="lower center", ncol=len(roles), frameon=False)
    fig.suptitle(f"{design.name}: {meas['parts']} parts, {meas['total_mass_kg']} kg, "
                 f"{' x '.join(str(round(v)) for v in meas['overall_size_mm'])} mm overall", fontsize=12)
    fig.tight_layout(rect=(0, 0.05, 1, 0.96))
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    return buf.getvalue()
