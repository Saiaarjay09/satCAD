"""
Systems: the wiring, tubing and fibre networks of a spacecraft, tied to its 3D model.

A satellite is more than its shell: power cables and busbars, coolant tubes, optical fibres. `Systems` is a netlist kept
INSIDE the design file next to the geometry. It knows which component connects to which, what each connection is (a 4/0 cable, a 40 x 10 mm
bar, a 20 mm coolant tube, a fibre conduit), and WHERE it runs in 3D. From that one description satCAD

  - measures every run from the real 3D route (cut lengths, bend counts, bundle diameter, mass),
  - checks it with engineering rules (current density, voltage drop, flow speed, pressure drop against the pump, radiator capacity, fibre bend
    radius and loss budget, clearance between electrical and coolant runs, stale routes after the model changed),
  - draws the diagrams (see satcad.diagrams): wiring, cooling and optical.

WHAT THE NUMBERS ARE. Everything here is concept-level engineering from textbook formulas and typical handbook values, labelled as assumptions
in the netlist (`assumptions`) so you can see and change them. It is NOT a flight-qualified harness analysis: wire derating tables, outgassing,
radiation, EMC and fluid thermo-physics over temperature are not modelled.

UNITS: millimetres in the geometry; metres, amperes, volts, pascals, kelvin in the results.
"""
from __future__ import annotations

import copy
import math
import re

import numpy as np

SIDES = {"+x": (0, 1), "-x": (0, -1), "+y": (1, 1), "-y": (1, -1), "+z": (2, 1), "-z": (2, -1)}
DOMAINS = ("power", "optical", "fluid")
DOMAIN_COLORS = {"power": "#ff8c00", "optical": "#00c8ff", "fluid": "#e13228", "fluid_cold": "#2870eb"}


class SystemsError(ValueError):
    """Raised for a netlist that is malformed (unknown component, duplicate id, bad value). The message says what to fix."""


# ---- reference data (typical handbook values, 20 degC) ---------------------------------------------------------------------------------
# AWG: conductor diameter (mm), copper area (mm2), resistance (milliohm per metre at 20 degC)
AWG = {
    "4/0": (11.68, 107.2, 0.1608), "3/0": (10.40, 85.01, 0.2028), "2/0": (9.27, 67.43, 0.2557), "1/0": (8.25, 53.49, 0.3224),
    "1": (7.35, 42.41, 0.4066), "2": (6.54, 33.62, 0.5127), "4": (5.19, 21.15, 0.8152), "6": (4.11, 13.30, 1.296), "8": (3.26, 8.366, 2.061),
    "10": (2.59, 5.261, 3.277), "12": (2.05, 3.309, 5.211), "14": (1.63, 2.081, 8.286), "16": (1.29, 1.309, 13.17), "18": (1.02, 0.823, 20.95),
    "20": (0.812, 0.5176, 33.31), "22": (0.644, 0.3243, 52.96), "24": (0.511, 0.2047, 84.22), "26": (0.404, 0.128, 133.9),
}
COPPER_RESISTIVITY = 1.724e-8      # ohm metre at 20 degC
COPPER_DENSITY = 8960.0
INSULATION_DENSITY = 2200.0        # PTFE
WIRE_COLORS = {"red": "#e53935", "black": "#212121", "white": "#f5f5f5", "orange": "#fb8c00", "yellow": "#fdd835", "green": "#43a047",
               "blue": "#1e88e5", "violet": "#8e24aa", "brown": "#6d4c41", "grey": "#9e9e9e"}

# working fluids at about 20 degC: density kg/m3, dynamic viscosity Pa s, specific heat J/(kg K)
FLUIDS = {
    "ammonia": dict(density=610.0, viscosity=1.39e-4, cp=4740.0, note="liquid ammonia (the usual spacecraft two-phase / single-phase loop fluid)"),
    "water": dict(density=998.0, viscosity=1.0e-3, cp=4182.0, note="water (freezes: only for crewed or heated loops)"),
    "pg50": dict(density=1040.0, viscosity=3.9e-3, cp=3350.0, note="50 / 50 propylene-glycol and water"),
}

# fibre: attenuation dB per km at 1550 nm, group index, minimum long-term bend radius (mm)
FIBRES = {
    "SMF-28": dict(attenuation_db_km=0.20, group_index=1.468, min_bend_mm=30.0, note="standard single-mode fibre"),
    "BIF": dict(attenuation_db_km=0.25, group_index=1.468, min_bend_mm=7.5, note="bend-insensitive single-mode fibre"),
}

DEFAULT_ASSUMPTIONS = dict(
    bus_voltage_v=48.0,
    max_current_density_a_mm2=2.5,      # concept screen for bundled wire and bars in vacuum, NOT a MIL-W-5088 derating table
    max_voltage_drop_pct=3.0,
    return_factor=2.0,                  # loop = supply + return conductor of the same length
    contact_resistance_mohm=0.05,       # a bolted busbar joint
    wire_slack_mm_per_end=40.0,
    wire_slack_pct=3.0,
    bundle_fill=0.60,                   # packing: bundle cross-section = sum of wire cross-sections / fill
    min_separation_mm={"fluid|power": 25.0, "fluid|optical": 15.0},                # keys are the two domains, sorted
    fluid_velocity_max_m_s=3.0,
    fluid_velocity_min_m_s=0.05,
    pump_margin=0.8,                    # system pressure drop should stay below this fraction of the pump head
    bend_minor_loss_k=0.25,             # smooth 90 degree pipe bend
    tee_minor_loss_k=0.9,
    connector_loss_db=0.3,
    route_tolerance_mm=2.0,
)


def _pt(v) -> np.ndarray:
    return np.asarray(v, dtype=float)


def polyline_length(points) -> float:
    pts = [_pt(p) for p in points]
    return float(sum(np.linalg.norm(b - a) for a, b in zip(pts, pts[1:])))


def count_bends(points, min_angle_deg: float = 5.0) -> int:
    pts = [_pt(p) for p in points]
    n = 0
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        u, w = b - a, c - b
        lu, lw = np.linalg.norm(u), np.linalg.norm(w)
        if lu < 1e-9 or lw < 1e-9:
            continue
        cos = float(np.clip(np.dot(u, w) / (lu * lw), -1, 1))
        if math.degrees(math.acos(cos)) > min_angle_deg:
            n += 1
    return n


def segment_distance(p0, p1, q0, q1) -> float:
    """Shortest distance between two 3D line segments."""
    p0, p1, q0, q1 = _pt(p0), _pt(p1), _pt(q0), _pt(q1)
    d1, d2, r = p1 - p0, q1 - q0, p0 - q0
    a, e, f = float(d1 @ d1), float(d2 @ d2), float(d2 @ r)
    if a < 1e-12 and e < 1e-12:
        return float(np.linalg.norm(r))
    if a < 1e-12:
        s, t = 0.0, float(np.clip(f / e, 0, 1))
    else:
        c = float(d1 @ r)
        if e < 1e-12:
            s, t = float(np.clip(-c / a, 0, 1)), 0.0
        else:
            b = float(d1 @ d2)
            den = a * e - b * b
            s = float(np.clip((b * f - c * e) / den, 0, 1)) if den > 1e-12 else 0.0
            t = (b * s + f) / e
            if t < 0:
                t, s = 0.0, float(np.clip(-c / a, 0, 1))
            elif t > 1:
                t, s = 1.0, float(np.clip((b - c) / a, 0, 1))
    return float(np.linalg.norm((p0 + d1 * s) - (q0 + d2 * t)))


def polyline_distance(a, b) -> float:
    return min(segment_distance(p, q, r, s) for p, q in zip(a, a[1:]) for r, s in zip(b, b[1:]))


def awg_info(awg: str) -> tuple:
    key = str(awg)
    if key not in AWG:
        raise SystemsError(f"Unknown wire gauge '{awg}'. Use one of {list(AWG)}.")
    return AWG[key]


def wire_outer_diameter_mm(awg: str) -> float:
    d = AWG[str(awg)][0]
    return d + 2 * (0.8 if d > 6 else 0.4 if d > 1.5 else 0.25)


# ==============================================================================================================================
#  The netlist
# ==============================================================================================================================
class Systems:
    """The wiring / tubing / fibre netlist. Plain data inside (`to_dict`), so it is saved with the design and never needs the CAD kernel to read."""

    def __init__(self, name: str = "Systems"):
        self.name = name
        self.assumptions: dict = copy.deepcopy(DEFAULT_ASSUMPTIONS)
        self.components: dict[str, dict] = {}
        self.links: dict[str, dict] = {}
        self.loops: list[dict] = []
        self.chains: list[dict] = []

    # ---- building -----------------------------------------------------------------------------------------------------------
    def component(self, name: str, domain: str | tuple, kind: str, part: str | None = None, label: str | None = None, ports: dict | None = None,
                  col: int | None = None, merge: bool = False, **attrs) -> dict:
        """A box on the diagrams: a battery, a pump, a tray, a manifold. `domain` may list several (a tray is power, optical AND fluid)."""
        doms = [domain] if isinstance(domain, str) else list(domain)
        if not doms or any(d not in DOMAINS for d in doms):
            raise SystemsError(f"Component '{name}': domain must be one of {DOMAINS}.")
        if name in self.components:
            if not merge:
                raise SystemsError(f"Component '{name}' is already defined.")
            c = self.components[name]                                   # the same equipment on another network (a tray is power, optical AND fluid)
            c["domains"] = sorted(set(c["domains"]) | set(doms))
            for k, v in attrs.items():
                if isinstance(v, dict) and isinstance(c.get(k), dict):
                    c[k].update(v)
                else:
                    c[k] = v
            if label:
                c["label"] = label
        else:
            c = dict(name=name, domains=doms, kind=kind, part=part, label=label or name, ports={}, col=col, **attrs)
            self.components[name] = c
        for pname, anchor in (ports or {}).items():
            self.port(name, pname, anchor)
        return c

    def port(self, comp: str, name: str, anchor: dict | str | None = None) -> None:
        """A connection point on a component, anchored to a part of the model so it follows the part when the design changes.
        anchor: {"part": ..., "at": "+z" | "center" | [fx, fy, fz], "offset": [dx, dy, dz]}; a bare string means a face of the component's own part."""
        c = self._comp(comp)
        if isinstance(anchor, str):
            anchor = {"at": anchor}
        anchor = dict(anchor or {"at": "center"})
        anchor.setdefault("part", c.get("part"))
        c["ports"][name] = anchor

    def link(self, id: str, domain: str, a: str, b: str, part: str | None = None, route: list | None = None, auto: bool = False, bundle: str | None = None,
             **spec) -> dict:
        """A run between two ports 'component.port'. `part` is an existing body that already shows it in 3D; `route` is a polyline in mm that satCAD
        will draw as a swept tube of the right size; `auto=True` lets the router find the route."""
        if id in self.links:
            raise SystemsError(f"Link '{id}' is already defined.")
        if domain not in DOMAINS:
            raise SystemsError(f"Link '{id}': domain must be one of {DOMAINS}.")
        for end in (a, b):
            self._endpoint(end, id)
        if route is not None and (len(route) < 2 or any(len(p) != 3 for p in route)):
            raise SystemsError(f"Link '{id}': a route needs at least two [x, y, z] points.")
        l = dict(id=id, domain=domain, a=a, b=b, part=part, route=[list(map(float, p)) for p in route] if route else None, auto=bool(auto), bundle=bundle, **spec)
        self.links[id] = l
        return l

    def loop(self, name: str, **spec) -> dict:
        """A coolant loop: fluid, heat load, stages in series (each stage lists parallel elements), the pump and the radiator."""
        lp = dict(name=name, **spec)
        for st in lp.get("stages", []):
            for el in st.get("elements", []):
                if not isinstance(el, dict):
                    self.element(el)
        self.loops.append(lp)
        return lp

    def chain(self, name: str, links: list, **spec) -> dict:
        """An optical path through several links (for the loss budget)."""
        for l in links:
            if l not in self.links:
                raise SystemsError(f"Chain '{name}': no link '{l}'.")
        ch = dict(name=name, links=list(links), **spec)
        self.chains.append(ch)
        return ch

    def _comp(self, name: str) -> dict:
        if name not in self.components:
            raise SystemsError(f"No component '{name}'. Components: {list(self.components)[:12]}...")
        return self.components[name]

    def _endpoint(self, end: str, link_id: str = "") -> tuple[str, str]:
        if "." not in end:
            raise SystemsError(f"Link '{link_id}': an end must look like 'component.port', not '{end}'.")
        comp, port = end.split(".", 1)
        c = self._comp(comp)
        if port not in c["ports"]:
            raise SystemsError(f"Link '{link_id}': component '{comp}' has no port '{port}'. Ports: {list(c['ports'])}.")
        return comp, port

    def element(self, id: str) -> dict:
        if id in self.links:
            return self.links[id]
        if id in self.components:
            return self.components[id]
        raise SystemsError(f"No link or component called '{id}'.")

    # ---- saving -------------------------------------------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return copy.deepcopy(dict(name=self.name, assumptions=self.assumptions, components=self.components, links=self.links, loops=self.loops,
                                  chains=self.chains))

    @classmethod
    def from_dict(cls, data: dict) -> "Systems":
        if not isinstance(data, dict) or "components" not in data or "links" not in data:
            raise SystemsError("That isn't a systems netlist.")
        s = cls(data.get("name", "Systems"))
        s.assumptions.update(copy.deepcopy(data.get("assumptions", {})))
        s.components = copy.deepcopy(data["components"])
        s.links = copy.deepcopy(data["links"])
        s.loops = copy.deepcopy(data.get("loops", []))
        s.chains = copy.deepcopy(data.get("chains", []))
        return s

    @classmethod
    def of(cls, design) -> "Systems | None":
        return cls.from_dict(design.systems) if getattr(design, "systems", None) else None

    def save_to(self, design) -> None:
        design.systems = self.to_dict()

    # ---- geometry: ports, routes ---------------------------------------------------------------------------------------------
    @staticmethod
    def anchor_point(design, anchor: dict) -> list[float]:
        part = anchor.get("part")
        if part not in design.parts:
            raise SystemsError(f"The model has no part '{part}'.")
        lo, hi = design.parts[part].bbox()
        lo, hi = _pt(lo), _pt(hi)
        centre = (lo + hi) / 2
        at = anchor.get("at", "center")
        pt = centre.copy()
        if isinstance(at, str):
            if at in SIDES:
                axis, sign = SIDES[at]
                pt[axis] = hi[axis] if sign > 0 else lo[axis]
            elif at != "center":
                raise SystemsError(f"Port anchor '{at}' isn't +x -x +y -y +z -z, center or [fx, fy, fz].")
        else:
            frac = _pt(at)
            pt = lo + (hi - lo) * frac
        pt = pt + _pt(anchor.get("offset", [0, 0, 0]))
        return [round(float(v), 3) for v in pt]

    def bus(self, comp: str, domain: str, axis: int = 2, bar_mm: list | None = None, **spec) -> None:
        """Mark a component as a backbone (a busbar or a fibre trunk) along one axis. Every port on it becomes a node; satCAD keeps the segments between
        consecutive ports in order along the backbone, so they stay right when the model changes."""
        c = self._comp(comp)
        c["bus"] = dict(domain=domain, axis=axis, bar_mm=bar_mm, spec=spec)

    def _rebuild_buses(self, design=None) -> None:
        for c in self.components.values():
            bus = c.get("bus")
            if not bus:
                continue
            for lid in [i for i, l in self.links.items() if l.get("generated") == c["name"]]:
                del self.links[lid]
            ports = [(p, a["pos"]) for p, a in c["ports"].items() if a.get("pos") is not None]
            if len(ports) < 2:
                continue
            ax = bus["axis"]
            ports.sort(key=lambda t: (t[1][ax], natural_key(t[0])))
            centre = None
            if design is not None and c.get("part") in design.parts:
                lo, hi = (_pt(v) for v in design.parts[c["part"]].bbox())
                centre = (lo + hi) / 2
            for i, ((pa, qa), (pb, qb)) in enumerate(zip(ports, ports[1:])):
                a_pt, b_pt = _pt(qa), _pt(qb)
                if centre is not None:
                    for q in (a_pt, b_pt):
                        for k in range(3):
                            if k != ax:
                                q[k] = centre[k]
                span = [[round(float(v), 3) for v in a_pt], [round(float(v), 3) for v in b_pt]]
                spec = dict(bus["spec"])
                if bus["domain"] == "power":
                    spec.update(kind="bar", bar_mm=bus["bar_mm"])
                self.links[f"{c['name']}/seg{i + 1}"] = dict(id=f"{c['name']}/seg{i + 1}", domain=bus["domain"], a=f"{c['name']}.{pa}", b=f"{c['name']}.{pb}", part=None,
                                                          route=None, auto=False, bundle=None, generated=c["name"], span=span, length_mm=abs(qb[ax] - qa[ax]), **spec)

    def sync(self, design) -> list[str]:
        """Re-read every port position from the current 3D model and refresh the backbone segments. Returns messages about anything that could not be resolved."""
        problems = []
        for c in self.components.values():
            for pname, anchor in c["ports"].items():
                try:
                    anchor["pos"] = self.anchor_point(design, anchor)
                except SystemsError as e:
                    problems.append(f"{c['name']}.{pname}: {e}")
        self._rebuild_buses(design)
        return problems

    def port_pos(self, end: str) -> list[float] | None:
        comp, port = self._endpoint(end)
        return self.components[comp]["ports"][port].get("pos")

    def centreline(self, design, part: str) -> list[list[float]]:
        """Axis of a straight body (a bar, tube or conduit): the centres of its two end faces along its longest dimension."""
        lo, hi = design.parts[part].bbox()
        lo, hi = _pt(lo), _pt(hi)
        axis = int(np.argmax(hi - lo))
        a, b = (lo + hi) / 2, (lo + hi) / 2
        a[axis], b[axis] = lo[axis], hi[axis]
        return [[round(float(v), 3) for v in a], [round(float(v), 3) for v in b]]

    def polyline(self, link_id: str, design=None) -> list[list[float]] | None:
        """The run's path in mm: its route, or the axis of the body that draws it, or the straight line between its ports."""
        l = self.links[link_id]
        if l.get("route"):
            return l["route"]
        if design is not None and l.get("part") and l["part"] in design.parts and not l.get("span"):
            return self.centreline(design, l["part"])
        if l.get("span"):
            return l["span"]
        pa, pb = self.port_pos(l["a"]), self.port_pos(l["b"])
        if pa is not None and pb is not None and l.get("part") is None and not l.get("contact"):
            return [pa, pb]
        return None

    def length_mm(self, link_id: str, design=None) -> float:
        l = self.links[link_id]
        if l.get("length_mm"):
            return float(l["length_mm"])
        pl = self.polyline(link_id, design)
        return polyline_length(pl) if pl else 0.0

    # ---- derived: the three domains -------------------------------------------------------------------------------------------
    def analyse(self, design=None) -> dict:
        out = dict(power=self._analyse_power(design), fluid=self._analyse_fluid(design), optical=self._analyse_optical(design))
        out["totals"] = self._totals(out)
        return out

    # ---- power -----------------------------------------------------------------------------------------------------------------
    def _power_link_electrics(self, l: dict, design) -> dict:
        a = self.assumptions
        length_m = self.length_mm(l["id"], design) / 1000.0
        kind = l.get("kind", "wire")
        if kind == "contact":
            r = a["contact_resistance_mohm"] / 1000.0
            return dict(kind=kind, length_m=0.0, cut_length_m=0.0, area_mm2=None, r_ohm=r, mass_kg=0.0, od_mm=0.0, gauge="bolted joint")
        if kind == "bar":
            w, t = l["bar_mm"]
            area = w * t
            r = COPPER_RESISTIVITY * length_m / (area * 1e-6)
            return dict(kind=kind, length_m=length_m, cut_length_m=length_m, area_mm2=area, r_ohm=r, mass_kg=area * 1e-6 * length_m * COPPER_DENSITY, od_mm=max(w, t),
                        gauge=f"bar {w:g} x {t:g} mm")
        d, area, mohm = awg_info(l["awg"])
        cut = length_m * (1 + a["wire_slack_pct"] / 100.0) + 2 * a["wire_slack_mm_per_end"] / 1000.0
        od = wire_outer_diameter_mm(l["awg"])
        mass = (area * 1e-6 * COPPER_DENSITY + math.pi / 4 * ((od / 1000) ** 2 - (d / 1000) ** 2) * INSULATION_DENSITY) * cut
        return dict(kind=kind, length_m=length_m, cut_length_m=cut, area_mm2=area, r_ohm=mohm / 1000.0 * length_m, mass_kg=mass, od_mm=od, gauge=f"AWG {l['awg']}")

    def _analyse_power(self, design) -> dict:
        a = self.assumptions
        links = [l for l in self.links.values() if l["domain"] == "power"]
        comps = {n: c for n, c in self.components.items() if "power" in c["domains"]}
        if not links:
            return dict(links=[], loads=[], totals=dict(load_w=0.0, bus_current_a=0.0, mass_kg=0.0), bundles=[], modes={})
        elec = {l["id"]: self._power_link_electrics(l, design) for l in links}
        nodes = sorted({e for l in links for e in (l["a"], l["b"])})
        index = {n: i for i, n in enumerate(nodes)}
        v_bus = float(a["bus_voltage_v"])
        loads = {n: float(c["load_w"]) / v_bus for n, c in comps.items() if c.get("load_w")}
        sources = {n: c for n, c in comps.items() if c.get("source_w") or c.get("source_group")}
        total_a = sum(loads.values())
        port_of = {}
        for n in comps:
            for pname in comps[n]["ports"]:
                if f"{n}.{pname}" in index:
                    port_of.setdefault(n, []).append(f"{n}.{pname}")
        modes = {}
        for mode in ("sunlit", "eclipse"):
            group = "regulator" if mode == "sunlit" else "battery"
            active = [n for n, c in sources.items() if c.get("source_group") == group]
            inj = np.zeros(len(nodes))
            for n, amps in loads.items():
                inj[index[port_of[n][0]]] -= amps
            if active:
                share = total_a / len(active)
                for n in active:
                    inj[index[port_of[n][0]]] += share
            else:
                modes[mode] = dict(currents={}, node_v={}, source_count=0)
                continue
            ref = index[port_of[active[0]][0]]
            G = np.zeros((len(nodes), len(nodes)))
            for l in links:
                r = max(elec[l["id"]]["r_ohm"], 1e-7)
                i, j = index[l["a"]], index[l["b"]]
                G[i, i] += 1 / r
                G[j, j] += 1 / r
                G[i, j] -= 1 / r
                G[j, i] -= 1 / r
            keep = [k for k in range(len(nodes)) if k != ref]
            v = np.zeros(len(nodes))
            try:
                v[keep] = np.linalg.solve(G[np.ix_(keep, keep)], inj[keep])
            except np.linalg.LinAlgError:
                raise SystemsError("The power network is not fully connected: some component has no path to the source. Check for missing links.") from None
            currents = {l["id"]: float((v[index[l["a"]]] - v[index[l["b"]]]) / max(elec[l["id"]]["r_ohm"], 1e-7)) for l in links}
            modes[mode] = dict(currents=currents, node_v={n: float(v[index[n]]) for n in nodes}, source_count=len(active), ref=nodes[ref])
        rows, mass = [], 0.0
        for l in links:
            e = elec[l["id"]]
            cur = max((abs(m["currents"].get(l["id"], 0.0)) for m in modes.values()), default=0.0)
            area = e["area_mm2"]
            rows.append(dict(id=l["id"], a=l["a"], b=l["b"], kind=e["kind"], gauge=e["gauge"], color=l.get("color"), length_m=round(e["length_m"], 4),
                             cut_length_m=round(e["cut_length_m"], 4), current_a=round(cur, 3), area_mm2=area,
                             density_a_mm2=round(cur / area, 3) if area else None, r_mohm=round(e["r_ohm"] * 1000, 4),
                             drop_v=round(cur * e["r_ohm"] * a["return_factor"], 5), mass_kg=round(e["mass_kg"], 4), od_mm=round(e["od_mm"], 2),
                             bundle=l.get("bundle"), has_route=bool(self.polyline(l["id"], design))))
            mass += e["mass_kg"]
        load_rows = []
        for n, amps in loads.items():
            drops = {}
            for mode, m in modes.items():
                if m["currents"]:
                    drops[mode] = abs(m["node_v"][port_of[n][0]]) * a["return_factor"]
            worst = max(drops.values(), default=0.0)
            load_rows.append(dict(name=n, label=comps[n]["label"], power_w=comps[n]["load_w"], current_a=round(amps, 3), drop_v=round(worst, 4),
                                  drop_pct=round(100 * worst / v_bus, 3)))
        bundles = {}
        for r in rows:
            if r["bundle"] and r["kind"] == "wire":
                bundles.setdefault(r["bundle"], []).append(r["od_mm"])
        bundle_rows = [dict(name=k, wires=len(v), diameter_mm=round(math.sqrt(sum(d * d for d in v) / a["bundle_fill"]), 2)) for k, v in bundles.items()]
        capacity = {g: sum(float(c.get("source_w", 0)) for c in sources.values() if c.get("source_group") == g) for g in ("regulator", "battery")}
        return dict(links=rows, loads=load_rows, bundles=bundle_rows, modes={k: dict(source_count=m["source_count"]) for k, m in modes.items()},
                    totals=dict(load_w=sum(float(comps[n]["load_w"]) for n in loads), bus_current_a=round(total_a, 3), mass_kg=round(mass, 3), bus_voltage_v=v_bus,
                                source_capacity_w=capacity))

    # ---- fluid -----------------------------------------------------------------------------------------------------------------
    @staticmethod
    def friction_factor(re: float, rel_rough: float = 1e-5) -> float:
        if re < 2300:
            return 64.0 / max(re, 1.0)
        f_t = 0.25 / math.log10(rel_rough / 3.7 + 5.74 / re ** 0.9) ** 2          # Swamee-Jain, turbulent
        if re > 4000:
            return f_t
        return (64.0 / 2300.0) + (f_t - 64.0 / 2300.0) * (re - 2300.0) / 1700.0      # blend through the transition

    def _fluid_element(self, el_id: str, design, flow_m3_s: float, fluid: dict) -> dict:
        branches = 1
        if isinstance(el_id, dict):                                                  # a bundle of identical parallel tubes, e.g. the six hot radiator tubes
            el = el_id
            branches = max(len(el.get("parts", [])), 1)
            flow_m3_s = flow_m3_s / branches
            el_id = el["id"]
        else:
            el = self.element(el_id)
        a = self.assumptions
        od, wall = el.get("od_mm"), el.get("wall_mm", 0.0)
        if od is None:
            raise SystemsError(f"'{el_id}' needs od_mm (outer diameter) to be used in a coolant loop.")
        d_in = (od - 2 * wall) / 1000.0
        area = math.pi * d_in ** 2 / 4
        body = el["parts"][0] if el.get("parts") else el.get("part")
        is_link = el_id in self.links and "a" in el
        if el.get("length_mm") is not None:
            length = float(el["length_mm"]) / 1000.0
        elif is_link:
            length = self.length_mm(el_id, design) / 1000.0
        else:                                                                       # a component or tube bundle with a body (a manifold, the radiator tubes)
            length = polyline_length(self.centreline(design, body)) / 1000.0 if design is not None and body in design.parts else 0.0
        v = flow_m3_s / area if area else 0.0
        re = fluid["density"] * v * d_in / fluid["viscosity"] if d_in else 0.0
        f = self.friction_factor(re) if re > 0 else 0.0
        pl = self.polyline(el_id, design) if is_link else (self.centreline(design, body) if design is not None and body in design.parts else None)
        bends = count_bends(pl) if pl else 0
        k = bends * a["bend_minor_loss_k"] + float(el.get("tees", 0)) * a["tee_minor_loss_k"] + float(el.get("k_extra", 0.0))
        dyn = 0.5 * fluid["density"] * v * v
        dp = f * (length / d_in) * dyn + k * dyn if d_in else 0.0
        if el.get("dp_kpa") is not None:
            dp = float(el["dp_kpa"]) * 1000.0
        return dict(id=el_id, length_m=round(length, 4), id_mm=round(d_in * 1000, 2), velocity_m_s=round(v, 4), reynolds=round(re), bends=bends, dp_pa=round(dp, 3),
                    volume_l=round(area * length * 1000.0 * branches, 4), label=el.get("label", el_id), branches=branches,
                    flow_l_s=round(flow_m3_s * 1000.0, 5))

    def _analyse_fluid(self, design) -> dict:
        a = self.assumptions
        loops = []
        for lp in self.loops:
            fl = FLUIDS[lp.get("fluid", "ammonia")]
            q_w, dt = float(lp["heat_w"]), float(lp.get("delta_t_k", 10.0))
            m_dot = q_w / (fl["cp"] * dt)
            vol = m_dot / fl["density"]
            stages, total_dp, inventory = [], 0.0, 0.0
            for st in lp["stages"]:
                n = max(len(st["elements"]), 1) if st.get("parallel", len(st["elements"]) > 1) else 1
                active = [e for e in st["elements"]]
                share = vol / (len(active) if st.get("parallel", len(active) > 1) else 1)
                rows = [self._fluid_element(e, design, share, fl) for e in active]
                if st.get("parallel", len(active) > 1):
                    dp = max(r["dp_pa"] for r in rows)
                    vol_l = sum(r["volume_l"] for r in rows)
                else:
                    dp = sum(r["dp_pa"] for r in rows)
                    vol_l = sum(r["volume_l"] for r in rows)
                total_dp += dp
                inventory += vol_l
                stages.append(dict(name=st["name"], parallel=len(active) if st.get("parallel", len(active) > 1) else 1, dp_pa=round(dp, 2), elements=rows,
                                   velocity_m_s=max(r["velocity_m_s"] for r in rows)))
            pump = lp.get("pump") or {}
            head = float(pump.get("head_kpa", 0)) * 1000.0
            rad = lp.get("radiator") or {}
            capacity = None
            if rad:
                t_fluid = float(rad.get("fluid_temp_k", 293.0))
                t_panel = t_fluid - float(rad.get("fin_drop_k", 5.0))
                sink = float(rad.get("sink_temp_k", 200.0))
                flux = float(rad.get("emissivity", 0.85)) * 5.670374419e-8 * (t_panel ** 4 - sink ** 4)
                capacity = dict(flux_w_m2=round(flux, 1), area_m2=float(rad["area_m2"]), rejects_w=round(flux * float(rad["area_m2"]), 1), panel_temp_k=t_panel, sink_temp_k=sink)
            loops.append(dict(name=lp["name"], fluid=lp.get("fluid", "ammonia"), heat_w=q_w, delta_t_k=dt, mass_flow_kg_s=round(m_dot, 5), flow_l_min=round(vol * 60000.0, 3),
                              stages=stages, dp_total_kpa=round(total_dp / 1000.0, 3), pump_head_kpa=head / 1000.0, pump_use=round(total_dp / head, 4) if head else None,
                              inventory_l=round(inventory, 2), inventory_kg=round(inventory / 1000.0 * fl["density"], 2), radiator=capacity))
        return dict(loops=loops)

    # ---- optical ---------------------------------------------------------------------------------------------------------------
    def _analyse_optical(self, design) -> dict:
        a = self.assumptions
        rows = []
        for l in (l for l in self.links.values() if l["domain"] == "optical"):
            fib = FIBRES[l.get("fibre", "SMF-28")]
            length_m = self.length_mm(l["id"], design) / 1000.0
            pl = self.polyline(l["id"], design)
            loss = fib["attenuation_db_km"] * length_m / 1000.0
            rows.append(dict(id=l["id"], a=l["a"], b=l["b"], fibre=l.get("fibre", "SMF-28"), length_m=round(length_m, 4), loss_db=round(loss, 6),
                             latency_ns=round(length_m * fib["group_index"] / 0.299792458, 3), conduit_od_mm=l.get("conduit_od_mm"), bends=count_bends(pl) if pl else 0,
                             bend_radius_mm=l.get("bend_radius_mm"), min_bend_mm=fib["min_bend_mm"], has_route=bool(pl)))
        by_id = {r["id"]: r for r in rows}
        chains = []
        for ch in self.chains:
            conns = int(ch.get("connectors", len(ch["links"]) + 1))
            loss = sum(by_id[i]["loss_db"] for i in ch["links"]) + conns * a["connector_loss_db"]
            length = sum(by_id[i]["length_m"] for i in ch["links"])
            chains.append(dict(name=ch["name"], links=ch["links"], length_m=round(length, 4), connectors=conns, loss_db=round(loss, 4),
                               latency_ns=round(sum(by_id[i]["latency_ns"] for i in ch["links"]), 3), budget_db=ch.get("budget_db"),
                               margin_db=round(ch["budget_db"] - loss, 3) if ch.get("budget_db") is not None else None))
        return dict(links=rows, chains=chains)

    def _totals(self, out: dict) -> dict:
        return dict(components=len(self.components), links=len(self.links), power_links=len(out["power"]["links"]), fluid_loops=len(out["fluid"]["loops"]),
                    optical_links=len(out["optical"]["links"]), wire_mass_kg=out["power"]["totals"]["mass_kg"])

    # ---- the rule checks ------------------------------------------------------------------------------------------------------
    def check(self, design=None, include_clash: bool = True) -> list[dict]:
        """Engineering-rule findings: a list of {severity: error|warn|info, rule, message, items}. Empty of errors means the network passes these screens."""
        a = self.assumptions
        out: list[dict] = []

        def add(sev, rule, msg, *items):
            out.append(dict(severity=sev, rule=rule, message=msg, items=list(items)))

        res = self.analyse(design)
        pw, fl, op = res["power"], res["fluid"], res["optical"]
        # power
        for r in pw["links"]:
            if r["density_a_mm2"] is not None:
                lim = a["max_current_density_a_mm2"]
                if r["density_a_mm2"] > lim:
                    add("error", "P1", f"{r['id']} ({r['gauge']}) carries {r['current_a']:.1f} A: {r['density_a_mm2']:.2f} A/mm2 is over the {lim:g} A/mm2 limit. Use a heavier conductor.", r["id"])
                elif r["density_a_mm2"] > 0.8 * lim:
                    add("warn", "P1", f"{r['id']} ({r['gauge']}) is at {100 * r['density_a_mm2'] / lim:.0f}% of the current-density limit.", r["id"])
        for ld in pw["loads"]:
            if ld["drop_pct"] > a["max_voltage_drop_pct"]:
                add("error", "P2", f"{ld['label']}: voltage drop {ld['drop_v']:.2f} V is {ld['drop_pct']:.1f}% of the bus, over the {a['max_voltage_drop_pct']:g}% limit.", ld["name"])
            elif ld["drop_pct"] > 0.7 * a["max_voltage_drop_pct"]:
                add("warn", "P2", f"{ld['label']}: voltage drop is {ld['drop_pct']:.1f}% of the bus (limit {a['max_voltage_drop_pct']:g}%).", ld["name"])
        cap = pw["totals"].get("source_capacity_w", {})
        for group in ("regulator", "battery"):
            if pw["totals"]["load_w"] and cap.get(group, 0) and cap[group] < pw["totals"]["load_w"]:
                add("error", "P3", f"The {group} group can supply {cap[group]:,.0f} W but the loads need {pw['totals']['load_w']:,.0f} W.", group)
        # fluid
        for lp in fl["loops"]:
            vmax, vmin = a["fluid_velocity_max_m_s"], a["fluid_velocity_min_m_s"]
            for st in lp["stages"]:
                if st["velocity_m_s"] > vmax:
                    add("error", "F1", f"{lp['name']}, {st['name']}: flow speed {st['velocity_m_s']:.2f} m/s is over {vmax:g} m/s (erosion and noise). Use a wider tube.", st["name"])
                for el in st["elements"]:
                    if el["reynolds"] and el["reynolds"] < 2300 and el["velocity_m_s"] < vmin:
                        add("info", "F1", f"{lp['name']}, {el['id']}: very slow laminar flow ({el['velocity_m_s']:.3f} m/s); heat transfer there will be poor.", el["id"])
            if lp["pump_head_kpa"]:
                use = lp["pump_use"]
                if use > 1.0:
                    add("error", "F2", f"{lp['name']}: the loop needs {lp['dp_total_kpa']:.1f} kPa but the pump gives {lp['pump_head_kpa']:.0f} kPa.", lp["name"])
                elif use > a["pump_margin"]:
                    add("warn", "F2", f"{lp['name']}: the loop uses {100 * use:.0f}% of the pump head (keep under {100 * a['pump_margin']:.0f}%).", lp["name"])
            if lp["radiator"]:
                rej = lp["radiator"]["rejects_w"]
                if rej < lp["heat_w"]:
                    add("error", "F3", f"{lp['name']}: the radiator rejects about {rej / 1000:.1f} kW but the loop carries {lp['heat_w'] / 1000:.1f} kW.", lp["name"])
                elif rej < 1.1 * lp["heat_w"]:
                    add("warn", "F3", f"{lp['name']}: radiator margin is only {100 * (rej / lp['heat_w'] - 1):.0f}%.", lp["name"])
        # optical
        for r in op["links"]:
            if r["bend_radius_mm"] is not None and r["bends"] and r["bend_radius_mm"] < r["min_bend_mm"]:
                add("error", "O1", f"{r['id']}: bends of {r['bend_radius_mm']:g} mm are tighter than the {r['min_bend_mm']:g} mm the {r['fibre']} fibre allows.", r["id"])
        for ch in op["chains"]:
            if ch["margin_db"] is not None and ch["margin_db"] < 0:
                add("error", "O2", f"{ch['name']}: loss {ch['loss_db']:.2f} dB is over the {ch['budget_db']:g} dB budget.", ch["name"])
        # geometry coupling
        tol = a["route_tolerance_mm"]
        if design is not None:
            fresh = {}
            for c in self.components.values():
                moved = []
                for pname, anc in c["ports"].items():
                    try:
                        fresh[f"{c['name']}.{pname}"] = self.anchor_point(design, anc)
                    except SystemsError as e:
                        add("error", "G2", f"{c['name']}.{pname}: {e}", c["name"])
                        continue
                    if anc.get("pos"):
                        off = float(np.linalg.norm(_pt(fresh[f"{c['name']}.{pname}"]) - _pt(anc["pos"])))
                        if off > tol:
                            moved.append(off)
                if moved:
                    add("warn", "G1", f"{c['name']}: {len(moved)} port(s) moved up to {max(moved):.0f} mm since the netlist was last updated. Run Systems > Update.", c["name"])
            for l in self.links.values():
                if l.get("route"):
                    for end, key in ((l["route"][0], l["a"]), (l["route"][-1], l["b"])):
                        p = fresh.get(key)
                        if p is not None and np.linalg.norm(_pt(end) - _pt(p)) > tol:
                            add("error", "G1", f"{l['id']}: the route no longer reaches its port ({np.linalg.norm(_pt(end) - _pt(p)):.0f} mm off). Run Systems > Update to re-route.", l["id"])
                            break
        # clearance between different kinds of run
        runs = []
        for l in self.links.values():
            pl = self.polyline(l["id"], design) if design is not None else (l.get("route") or None)
            if pl:
                runs.append((l, pl, self._radius_mm(l)))
        for i, (l1, p1, r1) in enumerate(runs):
            for l2, p2, r2 in runs[i + 1:]:
                if l1["domain"] == l2["domain"]:
                    continue
                key = "|".join(sorted((l1["domain"], l2["domain"])))
                need = a["min_separation_mm"].get(key)
                if need is None:
                    continue
                gap = polyline_distance(p1, p2) - r1 - r2
                if gap < need:
                    add("warn" if gap > 0 else "error", "X1", f"{l1['id']} ({l1['domain']}) and {l2['id']} ({l2['domain']}) are only {max(gap, 0):.0f} mm apart (want {need:g} mm).", l1["id"], l2["id"])
        if design is not None and include_clash:
            ids = {f"route_{i}": i for i, l in self.links.items() if l.get("route") and not l.get("part")}
            chk = design.check()
            for o in chk["overlaps"]:
                hit = [ids[n] for n in (o["a"], o["b"]) if n in ids]
                if hit:
                    other = o["b"] if o["a"] in ids else o["a"]
                    add("error", "G3", f"{hit[0]} runs through {other} ({o['overlap_cm3']:.2f} cm3 of overlap).", hit[0], other)
        for l in self.links.values():
            if l.get("auto") and not l.get("route"):
                add("warn", "G4", f"{l['id']} has no route yet. Run Systems > Update to route it.", l["id"])
        # unconnected ports
        used = {e for l in self.links.values() for e in (l["a"], l["b"])}
        for c in self.components.values():
            for pname in c["ports"]:
                if f"{c['name']}.{pname}" not in used and not c["ports"][pname].get("spare"):
                    add("info", "N1", f"Port {c['name']}.{pname} is not connected to anything.", c["name"])
        order = {"error": 0, "warn": 1, "info": 2}
        out.sort(key=lambda f: order[f["severity"]])
        return out

    def _radius_mm(self, l: dict) -> float:
        if l["domain"] == "fluid":
            return float(l.get("od_mm", 20)) / 2
        if l["domain"] == "optical":
            return float(l.get("conduit_od_mm", 12)) / 2
        if l.get("kind") == "bar":
            return max(l["bar_mm"]) / 2
        return wire_outer_diameter_mm(l["awg"]) / 2 if l.get("awg") else 5.0

    # ---- reports ---------------------------------------------------------------------------------------------------------------
    def schedules(self, design=None) -> dict:
        """Cut lists for manufacturing: the wire / bar list, the tube list and the fibre list, as lists of dicts (write them with `to_csv`)."""
        res = self.analyse(design)
        power = [dict(id=r["id"], from_=r["a"], to=r["b"], type=r["gauge"], colour=r["color"] or "", length_m=r["length_m"], cut_length_m=r["cut_length_m"],
                      current_a=r["current_a"], drop_v=r["drop_v"], mass_kg=r["mass_kg"], bundle=r["bundle"] or "") for r in res["power"]["links"]]
        fluid = []
        for lp in res["fluid"]["loops"]:
            for st in lp["stages"]:
                for el in st["elements"]:
                    fluid.append(dict(loop=lp["name"], stage=st["name"], id=el["id"], length_m=el["length_m"], inner_diameter_mm=el["id_mm"], velocity_m_s=el["velocity_m_s"],
                                      reynolds=el["reynolds"], bends=el["bends"], pressure_drop_pa=el["dp_pa"], volume_l=el["volume_l"]))
        optical = [dict(id=r["id"], from_=r["a"], to=r["b"], fibre=r["fibre"], length_m=r["length_m"], loss_db=r["loss_db"], latency_ns=r["latency_ns"],
                        conduit_od_mm=r["conduit_od_mm"] or "") for r in res["optical"]["links"]]
        return dict(power=power, fluid=fluid, optical=optical)

    @staticmethod
    def to_csv(rows: list[dict]) -> str:
        import csv
        import io
        if not rows:
            return ""
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=[k.rstrip("_") for k in rows[0]])
        w.writeheader()
        for r in rows:
            w.writerow({k.rstrip("_"): v for k, v in r.items()})
        return buf.getvalue()

    def report(self, design=None) -> str:
        res = self.analyse(design)
        pw, fl, op = res["power"], res["fluid"], res["optical"]
        lines = [f"{self.name}: {len(self.components)} components, {len(self.links)} runs"]
        t = pw["totals"]
        if pw["links"]:
            lines.append(f"Power: {t['load_w'] / 1000:.1f} kW at {t['bus_voltage_v']:g} V = {t['bus_current_a']:.0f} A; {len(pw['links'])} conductors, {t['mass_kg']:.1f} kg of copper and insulation")
            worst = max(pw["loads"], key=lambda r: r["drop_pct"], default=None)
            if worst:
                lines.append(f"  worst voltage drop: {worst['label']} {worst['drop_v']:.3f} V ({worst['drop_pct']:.2f}%)")
        for lp in fl["loops"]:
            rj = f", radiator rejects {lp['radiator']['rejects_w'] / 1000:.1f} kW" if lp["radiator"] else ""
            lines.append(f"{lp['name']}: {lp['heat_w'] / 1000:.1f} kW, {lp['fluid']} {lp['flow_l_min']:.1f} L/min, loop pressure drop {lp['dp_total_kpa']:.1f} kPa"
                         f" of {lp['pump_head_kpa']:.0f} kPa pump{rj}; holds {lp['inventory_l']:.0f} L ({lp['inventory_kg']:.0f} kg)")
        if op["links"]:
            lines.append(f"Optical: {len(op['links'])} fibre runs, {sum(r['length_m'] for r in op['links']):.1f} m of conduit")
            for ch in op["chains"][:6]:
                lines.append(f"  {ch['name']}: {ch['loss_db']:.2f} dB, {ch['latency_ns']:.0f} ns" + (f", margin {ch['margin_db']:.1f} dB" if ch["margin_db"] is not None else ""))
        found = self.check(design)
        n = {s: sum(1 for f in found if f["severity"] == s) for s in ("error", "warn", "info")}
        lines.append(f"Checks: {n['error']} errors, {n['warn']} warnings, {n['info']} notes")
        for f in found:
            if f["severity"] != "info":
                lines.append(f"  [{f['severity'].upper()} {f['rule']}] {f['message']}")
        return "\n".join(lines)


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", text).strip("_")


def natural_key(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]
