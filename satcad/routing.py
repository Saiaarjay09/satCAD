"""
Routing: find a path for a cable, tube or fibre through the inside of the satellite, and turn routes into 3D tubes in the model.

THE ROUTER finds a right-angled path (the way harnesses are laid out) from one port to another that stays clear of every body in the model by a
safety clearance and stays inside the hull. It searches a grid whose lines are the faces of the obstacles, so it never squeezes through a gap that is
not there. Among all clear paths it prefers short ones with few bends, and every straight leg is long enough for the bend radius, so the result can be
swept into a smooth tube. Routes are stored in the netlist (`link["route"]`); change the model and run `update` to lay them again.

A route that never meets an obstacle is not a guarantee: the exact 3D clash check (`Design.check`, run by `Systems.check`) is the final word.
"""
from __future__ import annotations

import heapq

import numpy as np

from .engine import DesignError
from .systems import Systems, SystemsError, _pt, polyline_length

AXES = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], dtype=float)
BEND_PENALTY_MM = 150.0
WEIGHT = 1.3                      # weighted A*: a little longer than the shortest path, much faster


class RouteError(SystemsError):
    """No clear path exists (the message says between which ports)."""


def part_boxes(design, ignore=(), container=None) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """Axis-aligned boxes of the model's bodies that a run must avoid. Anything that fills the container (the hull) is left out: the container is its boundary."""
    out = []
    clo, chi = (container if container is not None else (None, None))
    for name, p in design.parts.items():
        if name in ignore:
            continue
        lo, hi = (_pt(v) for v in p.bbox())
        if clo is not None and (np.any(hi < clo) or np.any(lo > chi)):
            continue                                              # outside the hull: not an obstacle to routes inside it
        if clo is not None and np.all(lo <= clo + 1) and np.all(hi >= chi - 1):
            continue                                              # the hull itself
        out.append((name, lo, hi))
    return out


def find_hull(design) -> str:
    """The enclosure the runs live inside: 'bus_hull' if there is one, otherwise the biggest hollow body (volume well under its bounding box)."""
    if "bus_hull" in design.parts:
        return "bus_hull"
    best, best_vol = None, 0.0
    named = [n for n in design.parts if n.lower().endswith("hull")]
    for name, p in ((n, design.parts[n]) for n in (named or design.parts)):
        lo, hi = (_pt(v) for v in p.bbox())
        box = float(np.prod(hi - lo))
        if box > best_vol and p.volume_mm3 < 0.5 * box:
            best, best_vol = name, box
    if best is None:
        raise RouteError("There is no hollow enclosure (hull) in the model for the runs to be routed inside.")
    return best


def hull_cavity(design, hull: str | None = None, wall: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """The clear inside of the hull: its bounding box shrunk by the wall."""
    hull = hull or find_hull(design)
    lo, hi = (_pt(v) for v in design.parts[hull].bbox())
    t = wall if wall is not None else _guess_wall(design.parts[hull])
    return lo + t, hi - t


def _guess_wall(part) -> float:
    lo, hi = (_pt(v) for v in part.bbox())
    solid_cm3 = part.volume_mm3
    dims = hi - lo
    # volume of a thin-walled box of wall t: 2(ab + bc + ca) t  (small t)
    area = 2 * (dims[0] * dims[1] + dims[1] * dims[2] + dims[0] * dims[2])
    return float(np.clip(solid_cm3 / area, 1.0, 100.0)) if area else 10.0


def route(boxes, start, goal, container, radius: float, clearance: float, bend_radius: float, start_dir=None, goal_dir=None, spacing: float = 200.0,
          extra_obstacles=(), label: str = "", endpoint_parts=(), start_lead=None, goal_lead=None) -> list[list[float]]:
    """A right-angled, obstacle-free path from `start` to `goal` (mm). `start_dir` / `goal_dir` are outward port normals: the run leaves and arrives along them.
    The search starts in a window around the two ports and widens only if it has to."""
    start, goal = _pt(start), _pt(goal)
    lead = bend_radius + radius + 5.0
    s_dir = _pt(start_dir) if start_dir is not None else None
    g_dir = _pt(goal_dir) if goal_dir is not None else None
    s0 = start + s_dir * max(lead, float(start_lead or 0)) if s_dir is not None else start
    g0 = goal + g_dir * max(lead, float(goal_lead or 0)) if g_dir is not None else goal
    last_error = None
    for margin in (600.0, 1500.0, None):
        window = container
        if margin is not None:
            lo, hi = np.minimum(s0, g0) - margin, np.maximum(s0, g0) + margin
            window = (np.maximum(container[0], lo), np.minimum(container[1], hi))
        try:
            return _search(boxes, start, goal, s0, g0, s_dir, g_dir, window, radius, clearance, bend_radius, spacing, extra_obstacles, label, set(endpoint_parts))
        except RouteError as e:
            last_error = e
            if margin is None:
                raise
    raise last_error


def _search(boxes, start, goal, s0, g0, s_dir, g_dir, container, radius, clearance, bend_radius, spacing, extra_obstacles, label, endpoint_parts) -> list[list[float]]:
    inflate = radius + clearance
    clo, chi = container[0] + radius + 2.0, container[1] - radius - 2.0
    for tag, p in (("start", s0), ("goal", g0)):
        if np.any(p < clo - 1e-6) or np.any(p > chi + 1e-6):
            raise RouteError(f"{label}: the {tag} of the run ({np.round(p, 0)}) is outside the hull interior.")
    obs = []
    for name, lo, hi in list(boxes) + list(extra_obstacles):
        lo2, hi2 = lo - inflate, hi + inflate
        for tag, a, b, p in (("start", start, s0, s0), ("end", goal, g0, g0)):
            if name not in endpoint_parts and _segment_hits_box(a, b, lo2, hi2):
                raise RouteError(f"{label}: {name} blocks the {tag} of the run, within {np.linalg.norm(b - a):.0f} mm of the port. Move it, or route this run first.")
        for tag, p in (("start", s0), ("end", g0)):
            if np.all(lo2 < p) and np.all(p < hi2):
                raise RouteError(f"{label}: the {tag} of the run is too close to {name} for the run to leave its port ({bend_radius:g} mm bends need room).")
        if np.any(hi2 <= clo) or np.any(lo2 >= chi):
            continue
        obs.append((np.maximum(lo2, clo), np.minimum(hi2, chi), name))
    coords = []
    for ax in range(3):
        c = {round(float(s0[ax]), 3), round(float(g0[ax]), 3), round(float(clo[ax]), 3), round(float(chi[ax]), 3)}
        for lo2, hi2, _n in obs:
            c.add(round(float(lo2[ax]), 3))
            c.add(round(float(hi2[ax]), 3))
        for v in np.arange(clo[ax], chi[ax], spacing):
            c.add(round(float(v), 3))
        arr = np.array(sorted(c))
        keep = [arr[0]]
        for v in arr[1:]:
            if v - keep[-1] > 0.05:
                keep.append(v)
        coords.append(np.array(keep))
    n = [len(c) for c in coords]
    node_blocked = np.zeros(n, dtype=bool)
    edge_blocked = [np.zeros(n, dtype=bool) for _ in range(3)]
    for lo2, hi2, _name in obs:
        idx = [(int(np.searchsorted(coords[a], lo2[a] - 0.06)), int(np.searchsorted(coords[a], hi2[a] - 0.06))) for a in range(3)]    # lines within 0.05 mm were merged
        (i0, i1), (j0, j1), (k0, k1) = idx
        if i1 <= i0 or j1 <= j0 or k1 <= k0:
            continue
        node_blocked[i0 + 1:i1, j0 + 1:j1, k0 + 1:k1] = True
        edge_blocked[0][i0:i1, j0 + 1:j1, k0 + 1:k1] = True
        edge_blocked[1][i0 + 1:i1, j0:j1, k0 + 1:k1] = True
        edge_blocked[2][i0 + 1:i1, j0 + 1:j1, k0:k1] = True

    def index_of(p):
        return tuple(int(np.argmin(np.abs(coords[a] - p[a]))) for a in range(3))

    si, gi = index_of(s0), index_of(g0)
    for ax in range(3):                                           # the lines nearest the two ports are moved onto them exactly (they were merged within 0.5 mm)
        coords[ax][si[ax]] = s0[ax]
        coords[ax][gi[ax]] = g0[ax]
    if node_blocked[si] or node_blocked[gi]:
        raise RouteError(f"{label}: an end of the run is buried inside another body (the port is not in free space).")
    min_leg = 2.0 * bend_radius + 2.0                              # a leg between two bends must fit both bends
    first = None
    if s_dir is not None:
        first = int(np.argmax([float(np.dot(s_dir, a)) for a in AXES]))
    last = None
    if g_dir is not None:
        last = int(np.argmax([float(np.dot(-g_dir, a)) for a in AXES]))      # the direction of travel when arriving
    gpos = np.array([coords[a][gi[a]] for a in range(3)])

    def heuristic(node):
        return float(sum(abs(coords[a][node[a]] - gpos[a]) for a in range(3)))

    # state: (node index tuple, direction 0..5, long_enough flag)
    heap = [(WEIGHT * heuristic(si), 0.0, si, -1 if first is None else first, min_leg if first is not None else 0.0)]
    best = {}
    parent = {}
    start_state = (si, -1 if first is None else first, 1 if first is not None else 0)
    best[start_state] = 0.0
    parent[start_state] = None
    goal_state = None
    pops = 0
    while heap:
        f, g, node, d, run = heapq.heappop(heap)
        state = (node, d, 1 if run >= min_leg else 0)
        if g > best.get(state, 1e18) + 1e-9:
            continue
        pops += 1
        if pops > 400000:
            break
        if node == gi and (last is None or d == last or (d != (last ^ 1) and run >= min_leg)):          # straight in, or a bend at the lead point (its leg must fit two bends)
            goal_state = state
            break
        for nd in range(6):
            if d >= 0 and nd == (d ^ 1):
                continue                                                 # no U-turns
            if first is not None and d == first and node == si and nd != first and run == 0:
                continue                                                 # leave along the port normal
            turn = d >= 0 and nd != d
            if turn and run < min_leg:
                continue                                                 # the previous straight leg was too short for the bend
            ax = nd // 2
            step = 1 if nd % 2 == 0 else -1
            nxt = list(node)
            nxt[ax] += step
            if nxt[ax] < 0 or nxt[ax] >= n[ax]:
                continue
            nxt = tuple(nxt)
            eidx = list(node) if step == 1 else list(nxt)
            if edge_blocked[ax][tuple(eidx)] or node_blocked[nxt]:
                continue
            length = abs(coords[ax][nxt[ax]] - coords[ax][node[ax]])
            ng = g + length + (BEND_PENALTY_MM if turn else 0.0)
            nrun = (run + length) if (not turn and d >= 0) else length
            if d < 0:
                nrun = length
            key = (nxt, nd, 1 if nrun >= min_leg else 0)
            if ng < best.get(key, 1e18) - 1e-9:
                best[key] = ng
                parent[key] = state
                heapq.heappush(heap, (ng + WEIGHT * heuristic(nxt), ng, nxt, nd, nrun))
    if goal_state is None:
        if pops > 400000:
            raise RouteError(f"{label}: gave up after {pops:,} search steps (the free space is too large or too tangled; try a larger grid spacing).")
        raise RouteError(f"{label}: no clear path exists between the two ports (it may be boxed in, or the bend radius too large for the gaps).")
    path, state = [], goal_state
    while state is not None:
        node = state[0]
        path.append([float(coords[a][node[a]]) for a in range(3)])
        state = parent[state]
    path.reverse()
    pts = [[float(v) for v in start]]
    if s_dir is not None:
        pts.append([float(v) for v in s0])
    pts.extend(path)
    if g_dir is not None:
        pts.append([float(v) for v in goal])
    pts = _simplify([[round(v, 3) for v in p] for p in pts])
    for a, b in zip(pts, pts[1:]):                                                      # a last safety net: the finished path must not enter any clearance zone
        for lo2, hi2, name in obs:
            if name not in endpoint_parts and _segment_hits_box(_pt(a), _pt(b), lo2 + 0.2, hi2 - 0.2):
                raise RouteError(f"{label}: internal check failed: the path enters the clearance zone of {name} (segment {np.round(a, 1).tolist()} to {np.round(b, 1).tolist()}, zone {np.round(lo2, 1).tolist()} to {np.round(hi2, 1).tolist()}).")
    return pts


def _segment_hits_box(a, b, lo, hi) -> bool:
    """Does the segment a-b pass through the inside of the box (touching a face does not count)?"""
    d = b - a
    t0, t1 = 0.0, 1.0
    for k in range(3):
        if abs(d[k]) < 1e-12:
            if a[k] <= lo[k] + 1e-6 or a[k] >= hi[k] - 1e-6:
                return False
        else:
            ta, tb = (lo[k] - a[k]) / d[k], (hi[k] - a[k]) / d[k]
            t0, t1 = max(t0, min(ta, tb)), min(t1, max(ta, tb))
            if t0 >= t1 - 1e-9:
                return False
    return True


def _simplify(pts: list) -> list:
    """Drop repeated points and points in the middle of a straight line."""
    out = [pts[0]]
    for p in pts[1:]:
        if np.linalg.norm(_pt(p) - _pt(out[-1])) < 0.01:
            continue
        out.append(p)
    res = [out[0]]
    for i in range(1, len(out) - 1):
        a, b, c = _pt(res[-1]), _pt(out[i]), _pt(out[i + 1])
        u, w = b - a, c - b
        if np.linalg.norm(np.cross(u, w)) < 1e-6 * max(np.linalg.norm(u) * np.linalg.norm(w), 1.0) and np.dot(u, w) > 0:
            continue
        res.append(out[i])
    res.append(out[-1])
    return res


# ==============================================================================================================================
#  Whole-network operations on a design
# ==============================================================================================================================
def port_normal(systems: Systems, end: str):
    comp, port = systems._endpoint(end)
    anc = systems.components[comp]["ports"][port]
    if anc.get("dir"):
        return _pt(anc["dir"])
    at = anc.get("at")
    from .systems import SIDES
    if isinstance(at, str) and at in SIDES:
        axis, sign = SIDES[at]
        v = np.zeros(3)
        v[axis] = sign
        return v
    return None


def _lead(systems: Systems, end: str):
    comp, port = systems._endpoint(end)
    return systems.components[comp]["ports"][port].get("lead")


def link_geometry(systems: Systems, l: dict) -> tuple[float, float, float]:
    """(tube radius, clearance, bend radius) in mm for a run, from its type and size."""
    if l["domain"] == "fluid":
        r = float(l.get("od_mm", 20)) / 2
        return r, float(l.get("clearance_mm", 30)), float(l.get("bend_radius_mm", max(1.5 * 2 * r, 30)))
    if l["domain"] == "optical":
        r = float(l.get("conduit_od_mm", 12)) / 2
        return r, float(l.get("clearance_mm", 20)), float(l.get("bend_radius_mm", max(40.0, 3 * r)))
    from .systems import wire_outer_diameter_mm
    r = float(l["od_mm"]) / 2 if l.get("od_mm") else wire_outer_diameter_mm(l["awg"]) / 2 if l.get("awg") else 6.0
    return r, float(l.get("clearance_mm", 30)), float(l.get("bend_radius_mm", max(5 * 2 * r, 30)))


def autoroute(design, systems: Systems, link_ids=None, hull: str | None = None, wall: float | None = None, only_missing: bool = False) -> dict:
    """Lay out every `auto` link (or just `link_ids`). Routes found earlier become obstacles for later ones, so runs keep apart. Returns {id: length_mm}."""
    systems.sync(design)
    container = hull_cavity(design, hull, wall)
    routed, done = {}, []
    own = {f"route_{i}" for i in systems.links}
    ids = [i for i, l in systems.links.items() if (l.get("auto") and (link_ids is None or i in link_ids) and not (only_missing and l.get("route")))]
    ids.sort(key=lambda i: -link_geometry(systems, systems.links[i])[0])                     # fat tubes first: thin cables are easier to fit around them
    for lid in ids:
        systems.links[lid]["route"] = None
    all_boxes = part_boxes(design, set(own), container)                     # one pass over the model, not one per run
    for lid in ids:
        l = systems.links[lid]
        radius, clearance, bend = link_geometry(systems, l)
        ends = [systems.port_pos(l["a"]), systems.port_pos(l["b"])]
        if None in ends:
            raise RouteError(f"{lid}: a port has no position. Run sync first.")
        endpoint_parts = set()
        for end in (l["a"], l["b"]):
            comp, port = systems._endpoint(end)
            endpoint_parts |= {p for p in (systems.components[comp].get("part"), systems.components[comp]["ports"][port].get("part")) if p}
        boxes = all_boxes
        extra = []
        for other_id, pts in done:
            o = systems.links[other_id]
            orad, _, _ = link_geometry(systems, o)
            for p, q in zip(pts, pts[1:]):
                lo, hi = np.minimum(_pt(p), _pt(q)) - orad, np.maximum(_pt(p), _pt(q)) + orad
                extra.append((other_id, lo, hi))
        pts = route(boxes, ends[0], ends[1], container, radius, clearance, bend, port_normal(systems, l["a"]), port_normal(systems, l["b"]), extra_obstacles=extra, label=lid,
                    endpoint_parts=endpoint_parts, start_lead=_lead(systems, l["a"]), goal_lead=_lead(systems, l["b"]))
        l["route"] = pts
        l["bend_radius_mm"] = bend
        done.append((lid, pts))
        routed[lid] = polyline_length(pts)
    return routed


def materialize(design, systems: Systems) -> list[str]:
    """Add (or replace) a swept tube in the model for every routed run that has no body of its own, so it is visible, exportable and clash-checked."""
    made = []
    with design.batch():
        for lid, l in systems.links.items():
            name = f"route_{lid}"
            if name in design.features:
                design.remove(name)
        for lid, l in systems.links.items():
            if not l.get("route") or l.get("part"):
                continue
            name = f"route_{lid}"
            radius, _, bend = link_geometry(systems, l)
            kw = dict(profile="circle", path=l["route"], bend_radius=float(l.get("bend_radius_mm", bend)), layer=l["domain"], role="schematic")
            if l["domain"] == "fluid":
                kw.update(radius=radius, wall=float(l.get("wall_mm", 1.5)), material="stainless_316", color=_color(l, True))
            elif l["domain"] == "optical":
                kw.update(radius=radius, material="generic_equipment", color=_color(l, True))
            else:
                from .systems import awg_info
                kw.update(radius=float(l["od_mm"]) / 2 if l.get("od_mm") else awg_info(l["awg"])[0] / 2, material="copper", color=_color(l, True))
            if l.get("assembly"):
                kw["assembly"] = l["assembly"]
            design.add("sweep", name, **kw)
            made.append(name)
    return made


def _color(l: dict, as_rgb: bool):
    table = {"orange": (1.0, 0.55, 0.0), "red": (0.88, 0.2, 0.16), "blue": (0.16, 0.44, 0.92), "cyan": (0.0, 0.78, 1.0), "black": (0.13, 0.13, 0.15),
             "white": (0.96, 0.96, 0.98), "yellow": (0.99, 0.84, 0.2), "green": (0.26, 0.63, 0.28), "grey": (0.62, 0.62, 0.62)}
    name = l.get("color") or {"power": "orange", "optical": "cyan", "fluid": "red"}[l["domain"]]
    return list(table.get(name, table["orange"]))


def update(design, systems: Systems, reroute: bool = True) -> dict:
    """One-click refresh after the model changed: re-read ports, re-route the automatic runs, redraw the tubes."""
    problems = systems.sync(design)
    routed = autoroute(design, systems) if reroute else {}
    made = materialize(design, systems)
    systems.save_to(design)
    return dict(problems=problems, routed=routed, tubes=made)
