"""
Diagrams: the drawings you make from a Systems netlist.

    from satcad import diagrams
    diagrams.write_all(systems, design, "output/diagrams")      # wiring, cooling, optical, 3D routing view, schedules

Every drawing is ONE self-contained .html file (no internet, no libraries) that you can open in any browser, print or email:

  - wiring diagram    a connection diagram for the 48 V power network: sources, the busbar with its taps, every cable and bar with its gauge, current and voltage drop
  - cooling diagram   a P&ID-style drawing of each coolant loop: pumps, accumulator, manifolds, cold plates, radiator, with flow, speed and pressure drop on every tube
  - optical diagram   the fibre backbone, EDFAs, core switches and turrets with loss and latency per run
  - routing view      the real 3D routes of every cable, tube and fibre inside the hull: drag to rotate, wheel to zoom, tick layers, hover a run for its details

Each page also lists the schedules (cut lists), the numbers behind the picture, and the engineering-rule findings.
Everything is drawn from the same data as the 3D model, so the drawings cannot drift from the design.
"""
from __future__ import annotations

import datetime
import html
import json
from pathlib import Path

from .systems import DOMAIN_COLORS, WIRE_COLORS, Systems, natural_key

FONT = "Helvetica, Arial, sans-serif"
INK = "#1d232b"
PAPER = "#ffffff"
GRID = "#e6e9ee"
ROLE_COLORS = {"hot": "#d93025", "cold": "#1a62d6"}


def esc(text) -> str:
    return html.escape(str(text), quote=True)


# ==============================================================================================================================
#  The graph and its layout
# ==============================================================================================================================
class Node:
    def __init__(self, comp: dict, domain: str):
        self.comp = comp
        self.name = comp["name"]
        self.domain = domain
        self.label = comp["label"]
        self.kind = comp["kind"]
        self.col = (comp.get("cols") or {}).get(domain, 0)
        self.ports: dict[str, dict] = {}              # port name -> {"side": "l" | "r", "y": float, "edges": [...]}
        self.x = self.y = 0.0
        self.w, self.h = 200.0, 50.0
        self.lines: list[str] = []
        self.z = 0.0

    @property
    def is_bus(self) -> bool:
        return bool(self.comp.get("bus"))


def _nodes_and_edges(S: Systems, domain: str, loop: str | None, design=None):
    edges = []
    for l in S.links.values():
        if l["domain"] != domain or l.get("generated"):
            continue
        if loop and l.get("loop") not in (None, loop):
            continue
        edges.append(l)
    nodes: dict[str, Node] = {}
    for l in edges:
        for end in (l["a"], l["b"]):
            comp = end.split(".", 1)[0]
            if comp not in nodes:
                nodes[comp] = Node(S.components[comp], domain)
    for c in S.components.values():                     # a backbone with no drawn edges is still worth showing
        if c.get("bus") and c["bus"]["domain"] == domain and c["name"] in nodes:
            pass
    for n in nodes.values():
        zs = [a["pos"][2] for a in n.comp["ports"].values() if a.get("pos")]
        n.z = sum(zs) / len(zs) if zs else 0.0
    return nodes, edges


def _layout(S: Systems, nodes: dict[str, Node], edges: list[dict]) -> dict:
    """Columns by each node's `cols`. The biggest column of ordinary boxes (the trays) is stacked in physical order (+Z at the top); every busbar, trunk and
    manifold is stretched alongside it with each tap level with the box it feeds, and the rest (sources, pumps, turrets) line up with the tap they hang from.
    The result is mostly straight horizontal runs."""
    colw, gap, top, node_gap = 210.0, 190.0, 150.0, 12.0
    used: dict[str, set] = {name: set() for name in nodes}
    nbrs: dict[tuple, list] = {}                        # (component, port) -> [(other component, other port)]
    for l in edges:
        (ca, pa), (cb, pb) = l["a"].split(".", 1), l["b"].split(".", 1)
        if ca in nodes and cb in nodes:
            used[ca].add(pa)
            used[cb].add(pb)
            nbrs.setdefault((ca, pa), []).append((cb, pb))
            nbrs.setdefault((cb, pb), []).append((ca, pa))
    for n in nodes.values():
        n.port_list = sorted(used[n.name], key=natural_key)
        n.tall = n.is_bus or len(n.port_list) >= 5
        if n.tall:
            n.w = 150.0
            n.h = 40.0
        else:
            n.w = colw
            n.h = 24.0 + 12.0 * len(n.lines) + 8.0 * max(len(n.port_list) - 1, 0)
    flat = {}
    for n in nodes.values():
        if not n.tall:
            flat.setdefault(n.col, []).append(n)
    anchor_col = max(flat, key=lambda c: (len(flat[c]), c)) if flat else 0
    cols = sorted({n.col for n in nodes.values()})
    xs, x = {}, 40.0
    for c in cols:
        w = max(n.w for n in nodes.values() if n.col == c)
        xs[c] = (x, w)
        x += w + gap
    for n in nodes.values():
        n.x = xs[n.col][0] + (xs[n.col][1] - n.w) / 2

    def port_y(n: Node, p: str) -> float:
        if n.tall:
            return n.port_y[p]
        i = n.port_list.index(p)
        return n.y + n.h / 2 + (i - (len(n.port_list) - 1) / 2) * 9.0

    placed: set[str] = set()
    y = top
    for n in sorted(flat.get(anchor_col, []), key=lambda n: (-round(n.z), natural_key(n.name))):
        n.y = y
        y += n.h + node_gap
        placed.add(n.name)
    # tall nodes: each tap level with the placed box it feeds; taps with nothing placed yet go above or below by physical position
    for n in (n for n in nodes.values() if n.tall):
        want, free = {}, []
        for p in n.port_list:
            ys = [port_y(nodes[c], q) for c, q in nbrs.get((n.name, p), []) if c in placed]
            (want.__setitem__(p, sum(ys) / len(ys)) if ys else free.append(p))
        axis = n.comp["bus"]["axis"] if n.is_bus else 2

        def zpos(p):
            a = n.comp["ports"].get(p, {})
            return a["pos"][axis] if a.get("pos") else 0.0
        order = sorted(n.port_list, key=lambda p: -zpos(p))
        if want:
            lo, hi = min(want.values()), max(want.values())
            top_z = max(zpos(p) for p in want)
            above = sorted((p for p in free if zpos(p) > top_z), key=zpos)                  # physically above every aligned tap: stack upwards
            below = sorted((p for p in free if zpos(p) <= top_z), key=lambda p: -zpos(p))
            for i, p in enumerate(above):
                want[p] = lo - 14.0 * (i + 1)
            for i, p in enumerate(below):
                want[p] = hi + 14.0 * (i + 1)
        else:
            for i, p in enumerate(order):
                want[p] = top + 14.0 * i
        placed_y = 0.0
        ys = {}
        for p in sorted(want, key=lambda q: (want[q], -zpos(q))):
            placed_y = max(want[p], placed_y + 12.0) if ys else want[p]
            ys[p] = placed_y
        n.port_y = ys
        n.y = min(ys.values()) - 26.0
        n.h = max(ys.values()) - n.y + 16.0
        placed.add(n.name)
    # everything else hangs level with the tap it connects to, nearest first
    pending = [n for n in nodes.values() if n.name not in placed]
    guard = 0
    while pending and guard < 50:
        guard += 1
        rest = []
        for n in pending:
            ys = [port_y(nodes[c], q) - (n.h / 2 + (n.port_list.index(p) - (len(n.port_list) - 1) / 2) * 9.0 if not n.tall else 0)
                  for p in n.port_list for c, q in nbrs.get((n.name, p), []) if c in placed]
            if ys:
                n.y = sum(ys) / len(ys)
                placed.add(n.name)
            else:
                rest.append(n)
        pending = rest
    for n in pending:
        n.y = top
    for c in cols:                                        # push down any boxes that ended up on top of each other
        items = sorted((n for n in nodes.values() if n.col == c and not n.tall), key=lambda n: n.y)
        cursor = top - 1e9
        for n in items:
            n.y = max(n.y, cursor)
            cursor = n.y + n.h + node_gap
    for n in nodes.values():
        n.ports = {p: dict(y=port_y(n, p), edges=[]) for p in n.port_list}
    bottom = max(n.y + n.h for n in nodes.values())
    return dict(cols=cols, xs=xs, height=bottom + 50.0, width=x - gap + 40.0, gap=gap)


def _svg_page(title: str, subtitle: str, nodes: dict[str, Node], edges: list[dict], geo: dict, edge_style, node_style, legend: list[tuple[str, str]], notes: list[str],
              arrows: bool = False) -> str:
    """Draw nodes and right-angled connections. `edge_style(link)` -> (colour, width, label); `node_style(node)` -> (fill, stroke, title colour)."""
    W, H = geo["width"], geo["height"] + 70
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}" font-family="{FONT}">', "<!--DEFS-->",
           f'<rect width="{W:.0f}" height="{H:.0f}" fill="{PAPER}"/>']
    # frame and title block
    out.append(f'<rect x="10" y="10" width="{W - 20:.0f}" height="{H - 20:.0f}" fill="none" stroke="{INK}" stroke-width="1.5"/>')
    out.append(f'<text x="30" y="52" font-size="22" font-weight="700" fill="{INK}">{esc(title)}</text>')
    out.append(f'<text x="30" y="74" font-size="12" fill="#55606e">{esc(subtitle)}</text>')
    lx = 30
    for text, color in legend:
        out.append(f'<line x1="{lx}" y1="100" x2="{lx + 26}" y2="100" stroke="{color}" stroke-width="3"/><text x="{lx + 32}" y="104" font-size="11" fill="{INK}">{esc(text)}</text>')
        lx += 46 + 6.2 * len(text)
    # connections first, so nodes sit on top
    by_pair: dict[tuple, list] = {}
    lanes = 0
    markers: dict[str, str] = {}
    for l in edges:
        (ca, pa), (cb, pb) = l["a"].split(".", 1), l["b"].split(".", 1)
        if ca not in nodes or cb not in nodes:
            continue
        na, nb = nodes[ca], nodes[cb]
        swapped = na.col > nb.col                       # runs are drawn left to right; an arrow still shows which way the fluid goes
        if swapped:
            na, pa, nb, pb = nb, pb, na, pa
        by_pair.setdefault((na.col, nb.col), []).append((l, na, pa, nb, pb, swapped))
    for (c0, c1), items in by_pair.items():
        items.sort(key=lambda t: (t[3].ports[t[4]]["y"], t[1].ports[t[2]]["y"]))
        for rank, (l, na, pa, nb, pb, swapped) in enumerate(items):
            color, width, label = edge_style(l)
            forward = na.col < nb.col
            same = na.col == nb.col
            ax = na.x + na.w                                    # runs always leave a box on its right
            bx = nb.x if forward else nb.x + nb.w               # and arrive on its left (going forward) or its right (going back / same column)
            ay, by = na.ports[pa]["y"], nb.ports[pb]["y"]
            if forward and nb.col - na.col == 1:
                if abs(ay - by) < 0.5:
                    d = f"M{ax:.1f},{ay:.1f} H{bx:.1f}"
                    tx, ty, anchor = (ax + bx) / 2, by - 3, "middle"
                else:
                    xm = ax + geo["gap"] * (0.12 + 0.76 * (rank + 1) / (len(items) + 1))
                    d = f"M{ax:.1f},{ay:.1f} H{xm:.1f} V{by:.1f} H{bx:.1f}"
                    tx, ty, anchor = ((xm + bx) / 2 if bx - xm > 60 else xm + 3), by - 3, "middle"
            else:                                               # a long or backwards run goes round the top of the drawing
                lanes += 1
                lane = 112 + 7 * (lanes % 6)
                ex = ax + 16 + 4 * (lanes % 5)
                ix = (bx - 16 - 4 * (lanes % 5)) if forward else (bx + 16 + 4 * (lanes % 5))
                d = f"M{ax:.1f},{ay:.1f} H{ex:.1f} V{lane:.1f} H{ix:.1f} V{by:.1f} H{bx:.1f}"
                tx, ty, anchor = ix + (6 if forward else -6), by - 3, ("start" if forward else "end")
            mk = ""
            if arrows:
                mid = "ah" + color.strip("#")
                markers[mid] = color
                mk = f' marker-start="url(#{mid})"' if swapped else f' marker-end="url(#{mid})"'
            out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width:.1f}" stroke-linejoin="round" opacity="0.95"{mk}><title>{esc(l["id"])}</title></path>')
            if label:
                out.append(f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="8.5" fill="#39424e" text-anchor="{anchor}">{esc(label)}</text>')
    for n in nodes.values():
        fill, stroke, tcol = node_style(n)
        out.append(f'<g><title>{esc(n.label)}</title><rect x="{n.x:.1f}" y="{n.y:.1f}" width="{n.w:.1f}" height="{n.h:.1f}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>')
        out.append(f'<text x="{n.x + n.w / 2:.1f}" y="{(n.y - 7) if n.tall else (n.y + 15):.1f}" font-size="11.5" font-weight="700" text-anchor="middle" fill="{tcol}">{esc(n.label)}</text>')
        for i, line in enumerate(n.lines):
            out.append(f'<text x="{n.x + n.w / 2:.1f}" y="{n.y + 29 + 13 * i:.1f}" font-size="9.5" text-anchor="middle" fill="#46505d">{esc(line)}</text>')
        for p, info in n.ports.items():
            side_right = True
            out.append(f'<circle cx="{n.x:.1f}" cy="{info["y"]:.1f}" r="2.6" fill="{stroke}"/><circle cx="{n.x + n.w:.1f}" cy="{info["y"]:.1f}" r="2.6" fill="{stroke}"/>')
            if n.is_bus:
                out.append(f'<text x="{n.x + n.w / 2:.1f}" y="{info["y"] + 3:.1f}" font-size="8" text-anchor="middle" fill="{tcol}">{esc(p)}</text>')
            else:
                out.append(f'<text x="{n.x + 6:.1f}" y="{info["y"] + 3:.1f}" font-size="8" fill="#6b7480">{esc(p)}</text>')
        out.append("</g>")
    # notes at the bottom
    y = geo["height"] + 8
    for note in notes:
        out.append(f'<text x="30" y="{y:.0f}" font-size="10" fill="#55606e">{esc(note)}</text>')
        y += 14
    out.append(f'<text x="{W - 30:.0f}" y="{H - 24:.0f}" font-size="10" text-anchor="end" fill="#8a929c">satCAD systems drawing, generated {datetime.date.today().isoformat()}. Concept-level: not flight-qualified.</text>')
    out.append("</svg>")
    defs = "".join(f'<marker id="{i}" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
                   for i, c in markers.items())
    return "\n".join(out).replace("<!--DEFS-->", f"<defs>{defs}</defs>")


# ==============================================================================================================================
#  Pages
# ==============================================================================================================================
CSS = """
body{font-family:Helvetica,Arial,sans-serif;color:#1d232b;background:#f4f5f7;margin:0}
header{background:#101820;color:#fff;padding:18px 28px}
header h1{margin:0;font-size:22px;letter-spacing:.2px} header p{margin:4px 0 0;color:#aeb8c4;font-size:13px}
main{padding:18px 28px 40px;max-width:1900px} .sheet{background:#fff;border:1px solid #d5d9df;border-radius:6px;padding:10px;overflow:auto;margin-bottom:22px}
h2{font-size:16px;margin:26px 0 8px} table{border-collapse:collapse;font-size:12px;background:#fff} th,td{border:1px solid #d5d9df;padding:4px 8px;text-align:right}
th{background:#eef0f3;text-align:center} td:first-child,th:first-child,td.l{text-align:left} .err{color:#b3261e;font-weight:600} .warn{color:#a15c00;font-weight:600} .info{color:#4b5560}
.bar{display:inline-block;height:8px;background:#1a62d6;border-radius:2px} .note{font-size:12px;color:#55606e;max-width:980px}
"""


def _page(title: str, subtitle: str, body: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>'
            f'<style>{CSS}</style></head><body><header><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></header><main>{body}</main></body></html>')


def _table(headers: list[str], rows: list[list], classes: dict | None = None) -> str:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c if isinstance(c, _Raw) else esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


class _Raw(str):
    pass


def _findings_html(findings: list[dict]) -> str:
    if not findings:
        return '<p class="note">No findings.</p>'
    rows = [[_Raw(f'<span class="{f["severity"] if f["severity"] != "error" else "err"}">{f["severity"].upper()} {esc(f["rule"])}</span>'), f["message"]] for f in findings]
    return _table(["Finding", "What it says"], rows).replace("<td>" + esc(rows[0][1]), "<td class=\"l\">" + esc(rows[0][1]), 1)


def _fmt_a(v) -> str:
    return f"{v:.1f} A" if v >= 10 else f"{v:.2f} A"


def wiring_diagram(S: Systems, design=None, analysis: dict | None = None, findings: list | None = None) -> str:
    res = analysis or S.analyse(design)
    pw = res["power"]
    rows = {r["id"]: r for r in pw["links"]}
    loads = {r["name"]: r for r in pw["loads"]}
    nodes, edges = _nodes_and_edges(S, "power", None, design)
    for n in nodes.values():
        c = n.comp
        if c.get("source_w"):
            n.lines = [f"{c['source_group']}: up to {c['source_w'] / 1000:.0f} kW"]
        elif c["name"] in loads:
            r = loads[c["name"]]
            n.lines = [f"{r['power_w'] / 1000:.2f} kW  {_fmt_a(r['current_a'])}", f"drop {r['drop_v']:.3f} V ({r['drop_pct']:.2f}%)"]
        elif n.is_bus:
            n.lines = []
    geo = _layout(S, nodes, edges)

    def edge_style(l):
        r = rows.get(l["id"])
        if r is None:
            return "#999", 1.5, ""
        color = WIRE_COLORS.get(l.get("color") or "", DOMAIN_COLORS["power"]) if l.get("color") else DOMAIN_COLORS["power"]
        if r["kind"] == "contact":
            return "#6b7480", 2.0, "bolted joint"
        if r["kind"] == "bar":
            return DOMAIN_COLORS["power"], 3.4, f"{r['gauge'].replace('bar ', '')}  {_fmt_a(r['current_a'])}"
        label = f"{l['id'].replace('cable_', '')}  {r['gauge']}  {_fmt_a(r['current_a'])}  {r['length_m']:.1f} m"
        return color, 1.6 + min(r["area_mm2"] or 0, 120) / 40.0, label

    def node_style(n):
        k = n.kind
        fill = {"regulator": "#fff4e0", "battery": "#fff0d6", "busbar": "#ffe7c2", "server tray": "#eef7ee", "pump": "#e9f1fd", "edfa": "#e6f8fd", "core switch": "#e6f8fd",
                "computer": "#e6f8fd"}.get(k, "#f6f7f9")
        return fill, "#c76b00" if k in ("regulator", "battery", "busbar") else "#46505d", INK

    totals = pw["totals"]
    svg = _svg_page("48 V power wiring diagram", f"{S.name}  |  {totals['load_w'] / 1000:.1f} kW, {totals['bus_current_a']:.0f} A at {totals['bus_voltage_v']:g} V", nodes, edges, geo, edge_style, node_style,
                    [("busbar or bar (40 x 10 mm)", DOMAIN_COLORS["power"]), ("cable (colour as in the wire list)", WIRE_COLORS["red"]), ("bolted joint", "#6b7480")],
                    ["Up is +Z (towards the nose). Currents are the worst of sunlit (regulators supply) and eclipse (batteries supply). Voltage drop assumes a return conductor of the same length."])
    seg = [[l["id"], l["a"].split(".")[1], l["b"].split(".")[1], f"{rows[l['id']]['length_m'] * 1000:.0f}", f"{rows[l['id']]['current_a']:.1f}", f"{rows[l['id']]['density_a_mm2']:.2f}"]
           for l in S.links.values() if l.get("generated") and l["domain"] == "power" and l["id"] in rows]
    wire_tbl = _table(["Run", "From", "To", "Type", "Colour", "Length m", "Cut m", "Current A", "A/mm2", "Drop V", "Mass kg"],
                      [[r["id"], r["a"], r["b"], r["gauge"], r["color"] or "", f"{r['length_m']:.2f}", f"{r['cut_length_m']:.2f}", f"{r['current_a']:.2f}",
                        "" if r["density_a_mm2"] is None else f"{r['density_a_mm2']:.2f}", f"{r['drop_v']:.4f}", f"{r['mass_kg']:.3f}"] for r in pw["links"] if not r["id"].startswith(tuple(f"{c['name']}/" for c in S.components.values() if c.get("bus")))])
    load_tbl = _table(["Load", "Power W", "Current A", "Drop V", "Drop %"], [[r["label"], f"{r['power_w']:.0f}", f"{r['current_a']:.2f}", f"{r['drop_v']:.3f}", f"{r['drop_pct']:.2f}"] for r in pw["loads"]])
    bundle_tbl = _table(["Bundle", "Wires", "Bundle diameter mm"], [[b["name"], b["wires"], b["diameter_mm"]] for b in pw["bundles"]]) if pw["bundles"] else ""
    bus_tbl = _table(["Busbar segment", "From port", "To port", "Length mm", "Current A", "A/mm2"], seg[:60])
    body = (f'<div class="sheet">{svg}</div><h2>Cable and bar list</h2>{wire_tbl}<h2>Loads and voltage drop</h2>{load_tbl}'
            f'<h2>Busbar segments (current in the main bar between taps)</h2>{bus_tbl}' + (f"<h2>Harness bundles</h2>{bundle_tbl}" if bundle_tbl else "")
            + f"<h2>Findings</h2>{_findings_html([f for f in (findings or []) if f['rule'].startswith(('P', 'G', 'X', 'N'))])}"
            f'<p class="note">Concept-level numbers: copper at 20 degC, current density screen of {S.assumptions["max_current_density_a_mm2"]:g} A/mm2, not a MIL-W-5088 or ECSS derating analysis.</p>')
    return _page("48 V power wiring diagram", S.name, body)


def cooling_diagram(S: Systems, design=None, analysis: dict | None = None, findings: list | None = None) -> str:
    res = analysis or S.analyse(design)
    loops = {lp["name"]: lp for lp in res["fluid"]["loops"]}
    elem_rows = {}
    for lp in res["fluid"]["loops"]:
        for st in lp["stages"]:
            for el in st["elements"]:
                elem_rows[el["id"]] = el
    sheets, tables = [], []
    for tag in sorted({l.get("loop") for l in S.links.values() if l["domain"] == "fluid" and l.get("loop")}):
        lp = loops.get(f"Loop {tag}")
        nodes, edges = _nodes_and_edges(S, "fluid", tag, design)
        for n in nodes.values():
            c = n.comp
            row = elem_rows.get(c["name"])
            if c["kind"] == "server tray":
                n.lines = [f"cold plate {lp['flow_l_min'] / 16:.2f} L/min" if lp else ""]
            elif c["kind"] == "radiator wing":
                rad = lp["radiator"] if lp else None
                n.lines = [f"{c.get('area_m2', 0):.0f} m2 (both faces)"] + ([f"rejects {rad['rejects_w'] / 1000:.1f} kW at {rad['panel_temp_k'] - 273.15:.0f} degC"] if rad else [])
            elif c["kind"] == "pump":
                n.lines = ["duty" if "duty" in c["label"] else "standby", f"head {lp['pump_head_kpa']:.0f} kPa" if lp else ""]
            elif c["kind"] == "accumulator":
                n.lines = ["holds loop volume as it expands"]
            elif row:
                n.lines = [f"{row['length_m']:.1f} m, bore {row['id_mm']:g} mm", f"{row['velocity_m_s']:.2f} m/s, {row['dp_pa'] / 1000:.2f} kPa"]
            n.lines = [x for x in n.lines if x]
        geo = _layout(S, nodes, edges)

        def edge_style(l, elem_rows=elem_rows):
            role = l.get("role", "cold")
            row = elem_rows.get(l["id"])
            label = f"{l['id'].replace('_feeder', '').replace(tag + '_', '')}  O{l.get('od_mm', 0):g}"
            if row:
                label += f"  {row['velocity_m_s']:.2f} m/s  {row['dp_pa']:.0f} Pa"
            else:
                label += "  (standby path)" if "2" in l["id"][-2:] else ""
            return ROLE_COLORS.get(role, "#555"), 1.5 + float(l.get("od_mm", 20)) / 14.0, label

        def node_style(n):
            fill = {"pump": "#e9f1fd", "accumulator": "#eef1f6", "manifold": "#fdecea" if "hot" in n.name else "#e8f0fd", "server tray": "#eef7ee", "radiator wing": "#fff4e0",
                    "fitting": "#f6f7f9"}.get(n.kind, "#f6f7f9")
            return fill, ROLE_COLORS["hot"] if "hot_manifold" in n.name else ROLE_COLORS["cold"] if "cold_manifold" in n.name else "#46505d", INK

        title = f"Cooling diagram: loop {tag}"
        sub = (f"{lp['heat_w'] / 1000:.1f} kW, {lp['fluid']} {lp['flow_l_min']:.1f} L/min, {lp['mass_flow_kg_s']:.3f} kg/s, dT {lp['delta_t_k']:g} K, loop dP {lp['dp_total_kpa']:.1f} kPa of {lp['pump_head_kpa']:.0f} kPa pump" if lp else "")
        sheets.append(f'<div class="sheet">{_svg_page(title, sub, nodes, edges, geo, edge_style, node_style, [("hot (to radiator)", ROLE_COLORS["hot"]), ("cold (to trays)", ROLE_COLORS["cold"])], arrows=True, notes=["Up is +Z. Flow: pump to cold manifold to the 16 cold plates, hot manifold, radiator wing, back to the accumulator. Tube thickness follows outer diameter. Arrows show flow."])}</div>')
        if lp:
            stage_rows = [[st["name"], st["parallel"], f"{max(e['id_mm'] for e in st['elements']):g}", f"{sum(e['length_m'] for e in st['elements']) / (1 if st['parallel'] == 1 else len(st['elements'])):.2f}",
                           f"{st['velocity_m_s']:.3f}", max(e["reynolds"] for e in st["elements"]), f"{st['dp_pa'] / 1000:.3f}"] for st in lp["stages"]]
            rad = lp["radiator"]
            tables.append(f"<h2>Loop {tag}: pressure budget (stages in series)</h2>" + _table(["Stage", "Parallel", "Bore mm", "Length m", "Speed m/s", "Reynolds", "dP kPa"], stage_rows)
                          + f'<p class="note">Total {lp["dp_total_kpa"]:.2f} kPa = {100 * lp["pump_use"]:.0f}% of the {lp["pump_head_kpa"]:.0f} kPa pump head. Fluid holds {lp["inventory_l"]:.0f} L ({lp["inventory_kg"]:.0f} kg).'
                          + (f' Radiator: {rad["flux_w_m2"]:.0f} W/m2 x {rad["area_m2"]:.0f} m2 = {rad["rejects_w"] / 1000:.1f} kW at panel {rad["panel_temp_k"]:.0f} K against a {rad["sink_temp_k"]:.0f} K sink (margin {100 * (rad["rejects_w"] / lp["heat_w"] - 1):.0f}%).' if rad else "") + "</p>")
    body = "".join(sheets) + "".join(tables) + f"<h2>Findings</h2>{_findings_html([f for f in (findings or []) if f['rule'].startswith(('F', 'G', 'X', 'N'))])}" + \
        '<p class="note">Concept-level hydraulics: Darcy-Weisbach friction (Swamee-Jain), 0.25 per smooth bend, 0.9 per tee, equal flow split between identical parallel branches, fluid properties at 20 degC. ' \
        'Pump heads, cold-plate pressure drops and the radiator sink temperature are assumptions. Two-phase flow, micro-ridge (rifled) enhancement and temperature variation are not modelled.</p>'
    return _page("Cooling diagrams", S.name, body)


def optical_diagram(S: Systems, design=None, analysis: dict | None = None, findings: list | None = None) -> str:
    res = analysis or S.analyse(design)
    op = res["optical"]
    rows = {r["id"]: r for r in op["links"]}
    nodes, edges = _nodes_and_edges(S, "optical", None, design)
    for n in nodes.values():
        k = n.kind
        n.lines = {"OISL turret": ["1550 nm terminal"], "edfa": ["erbium-doped amplifier"], "core switch": ["optical core switch"], "server tray": ["optical port"], "computer": ["radiation-shielded"],
                   "fibre trunk": []}.get(k, [])
    geo = _layout(S, nodes, edges)

    def edge_style(l):
        r = rows.get(l["id"])
        label = f"{l['id'].replace('fibre_', '')}  {r['length_m']:.2f} m  {r['loss_db'] * 1000:.1f} mdB" if r and r["length_m"] else ""
        return DOMAIN_COLORS["optical"], 2.4, label

    def node_style(n):
        return {"OISL turret": "#eef1f6", "edfa": "#e6f8fd", "core switch": "#d9f3fb", "server tray": "#eef7ee", "computer": "#fff4e0", "fibre trunk": "#d9f3fb"}.get(n.kind, "#f6f7f9"), "#0088aa", INK

    svg = _svg_page("Optical data diagram", f"{S.name}  |  {len(op['links'])} fibre runs", nodes, edges, geo, edge_style, node_style, [("12 mm fibre conduit", DOMAIN_COLORS["optical"])],
                    ["Up is +Z. Loss per run is fibre attenuation only; connector loss is added in the budgets below (0.3 dB each, assumed)."])
    chain_rows = [[c["name"], f"{c['length_m']:.2f}", c["connectors"], f"{c['loss_db']:.2f}", f"{c['latency_ns']:.1f}", "" if c["budget_db"] is None else f"{c['budget_db']:g}", "" if c["margin_db"] is None else f"{c['margin_db']:.2f}"]
                  for c in op["chains"]]
    run_rows = [[r["id"], r["a"], r["b"], r["fibre"], f"{r['length_m']:.3f}", f"{r['loss_db']:.5f}", f"{r['latency_ns']:.2f}", r["conduit_od_mm"] or ""] for r in op["links"]]
    body = (f'<div class="sheet">{svg}</div><h2>Optical loss budgets</h2>{_table(["Path", "Length m", "Connectors", "Loss dB", "Latency ns", "Budget dB", "Margin dB"], chain_rows)}'
            f'<h2>Fibre runs</h2>{_table(["Run", "From", "To", "Fibre", "Length m", "Loss dB", "Latency ns", "Conduit mm"], run_rows)}'
            f"<h2>Findings</h2>{_findings_html([f for f in (findings or []) if f['rule'].startswith(('O', 'G', 'X', 'N'))])}"
            '<p class="note">SMF-28 at 1550 nm: 0.2 dB/km, group index 1.468, minimum long-term bend radius 30 mm. Radiation darkening, splices and optical power levels are not modelled.</p>')
    return _page("Optical data diagram", S.name, body)


# ==============================================================================================================================
#  The 3D routing view
# ==============================================================================================================================
def routing_view(S: Systems, design, analysis: dict | None = None) -> str:
    """An interactive 3D picture of every run: drag to rotate, wheel to zoom, tick layers, hover a run."""
    res = analysis or S.analyse(design)
    info = {}
    for r in res["power"]["links"]:
        info[r["id"]] = f"{r['gauge']}, {r['current_a']:.1f} A, {r['length_m']:.2f} m, drop {r['drop_v']:.3f} V"
    for lp in res["fluid"]["loops"]:
        for st in lp["stages"]:
            for el in st["elements"]:
                info[el["id"]] = f"{lp['name']}: {el['length_m']:.2f} m, bore {el['id_mm']:g} mm, {el['velocity_m_s']:.2f} m/s, {el['dp_pa']:.0f} Pa"
    for r in res["optical"]["links"]:
        info[r["id"]] = f"{r['fibre']}, {r['length_m']:.2f} m, {r['loss_db'] * 1000:.1f} mdB, {r['latency_ns']:.1f} ns"
    runs = []
    for lid, l in S.links.items():
        if l.get("generated") and l["domain"] != "power":
            pass
        pl = S.polyline(lid, design)
        if not pl or l.get("kind") == "contact":
            continue
        color = DOMAIN_COLORS["power"] if l["domain"] == "power" else DOMAIN_COLORS["optical"] if l["domain"] == "optical" else ROLE_COLORS.get(l.get("role", "hot"), "#d93025")
        if l["domain"] == "power" and l.get("color") in WIRE_COLORS and l.get("color") not in ("orange",):
            color = WIRE_COLORS[l["color"]]
        rad = S._radius_mm(l)
        runs.append(dict(id=lid, d=l["domain"], c=color, r=rad, p=[[round(v, 1) for v in p] for p in pl], i=info.get(lid, l["domain"])))
    boxes = []
    colors = {"power": "#ff8c00", "optical": "#00c8ff", "fluid": "#2870eb", "shell": "#8a929c"}
    for name, p in design.parts.items():
        layer = p.params.get("layer")
        if layer not in colors or "_ridges_" in name or "_stack_" in name and False:
            continue
        if name.startswith(("fin_", "tube_", "route_", "boom", "root_header", "tip_header")):
            continue
        lo, hi = p.bbox()
        if layer in ("power", "optical", "fluid") and (p.params.get("role") != "schematic" or max(h - l for l, h in zip(lo, hi)) < 150 and not name.startswith(("battery", "solar", "edfa", "pump", "accum"))):
            continue
        boxes.append(dict(n=name, l=layer, lo=[round(v) for v in lo], hi=[round(v) for v in hi]))
    hull = None
    if "bus_hull" in design.parts:
        lo, hi = design.parts["bus_hull"].bbox()
        hull = dict(lo=[round(v) for v in lo], hi=[round(v) for v in hi])
    data = json.dumps(dict(runs=runs, boxes=boxes, hull=hull, title=S.name)).replace("</", "<\\/")
    return _ROUTING_HTML.replace("__DATA__", data).replace("__TITLE__", esc(S.name))


_ROUTING_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>3D routing view</title>
<style>
html,body{margin:0;height:100%;background:#0e131a;color:#dfe5ec;font-family:Helvetica,Arial,sans-serif}
#bar{position:fixed;left:0;top:0;right:0;padding:10px 16px;background:#101820ee;border-bottom:1px solid #26303c;display:flex;gap:18px;align-items:center;flex-wrap:wrap;z-index:2;font-size:13px}
#bar h1{font-size:15px;margin:0 8px 0 0} label{cursor:pointer;user-select:none} .sw{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-2px;margin-right:5px}
canvas{display:block;width:100vw;height:100vh;cursor:grab} #tip{position:fixed;pointer-events:none;background:#000c;border:1px solid #3a4655;border-radius:6px;padding:6px 9px;font-size:12px;display:none;max-width:420px;z-index:3}
#help{position:fixed;left:16px;bottom:12px;font-size:12px;color:#8a97a6} button{background:#1c2733;color:#dfe5ec;border:1px solid #33404f;border-radius:5px;padding:4px 10px;cursor:pointer}
</style></head><body>
<div id="bar"><h1>3D routing view: __TITLE__</h1>
<label><input type="checkbox" id="power" checked><span class="sw" style="background:#ff8c00"></span>power</label>
<label><input type="checkbox" id="optical" checked><span class="sw" style="background:#00c8ff"></span>optical</label>
<label><input type="checkbox" id="fluid" checked><span class="sw" style="background:#e13228"></span>cooling</label>
<label><input type="checkbox" id="equip" checked>equipment</label><label><input type="checkbox" id="shell" checked>hull and trays</label>
<button id="iso">Isometric</button><button id="top">Top</button><button id="front">Front</button><button id="side">Side</button></div>
<canvas id="c"></canvas><div id="tip"></div><div id="help">drag to rotate, right-drag or shift-drag to pan, wheel to zoom, hover a run for its numbers, click to pin it</div>
<script>
const D=__DATA__;
const cv=document.getElementById('c'),ctx=cv.getContext('2d'),tip=document.getElementById('tip');
let yaw=-0.75,pitch=0.5,zoom=1,pan=[0,0],W=0,H=0,pinned=null,hover=null,drag=null;
const ext=D.hull?D.hull:{lo:[-2000,-2000,-3000],hi:[2000,2000,3000]};
const ctr=ext.lo.map((v,i)=>(v+ext.hi[i])/2),span=Math.max(...ext.hi.map((v,i)=>v-ext.lo[i]));
function rot(p){const x=p[0]-ctr[0],y=p[1]-ctr[1],z=p[2]-ctr[2];const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
 const x1=cy*x-sy*y,y1=sy*x+cy*y;return [x1,cp*z-sp*y1,sp*z+cp*y1];}   // screen x, screen up, depth
function proj(p){const r=rot(p),s=Math.min(W,H)/span*0.75*zoom,f=1/(1+r[2]/(span*3));return [W/2+pan[0]+r[0]*s*f,H/2+pan[1]-r[1]*s*f,r[2],s*f];}
const on=id=>document.getElementById(id).checked;
const edges=[[0,1],[1,3],[3,2],[2,0],[4,5],[5,7],[7,6],[6,4],[0,4],[1,5],[2,6],[3,7]];
function boxPts(lo,hi){const o=[];for(let i=0;i<8;i++)o.push([i&1?hi[0]:lo[0],i&2?hi[1]:lo[1],i&4?hi[2]:lo[2]]);return o;}
const BC={shell:'#5d6875',power:'#ff8c00',optical:'#00c8ff',fluid:'#2870eb'};
function draw(){ctx.clearRect(0,0,W,H);const segs=[];
 if(on('shell')&&D.hull){const P=boxPts(D.hull.lo,D.hull.hi).map(proj);ctx.strokeStyle='#7a8696';ctx.lineWidth=1.4;for(const [a,b] of edges){ctx.beginPath();ctx.moveTo(P[a][0],P[a][1]);ctx.lineTo(P[b][0],P[b][1]);ctx.stroke();}}
 for(const b of D.boxes){const sh=b.l==='shell';if(sh?!on('shell'):!on('equip'))continue;const P=boxPts(b.lo,b.hi).map(proj);ctx.strokeStyle=BC[b.l];ctx.globalAlpha=sh?0.28:0.55;ctx.lineWidth=sh?0.8:1.1;
  for(const [a,c] of edges){ctx.beginPath();ctx.moveTo(P[a][0],P[a][1]);ctx.lineTo(P[c][0],P[c][1]);ctx.stroke();}ctx.globalAlpha=1;}
 for(const r of D.runs){if(!on(r.d))continue;const P=r.p.map(proj);for(let i=0;i<P.length-1;i++)segs.push({r,a:P[i],b:P[i+1],z:(P[i][2]+P[i+1][2])/2});}
 segs.sort((u,v)=>v.z-u.z);let best=null,bd=9;const mx=hover?hover.x:-1e9,my=hover?hover.y:-1e9;
 for(const s of segs){const act=pinned===s.r.id;ctx.strokeStyle=s.r.c;ctx.globalAlpha=pinned&&!act?0.25:1;ctx.lineCap='round';ctx.lineWidth=Math.max(1.4,s.r.r*2*s.a[3]*(act?1.6:1));ctx.beginPath();ctx.moveTo(s.a[0],s.a[1]);ctx.lineTo(s.b[0],s.b[1]);ctx.stroke();
  const dx=s.b[0]-s.a[0],dy=s.b[1]-s.a[1],l2=dx*dx+dy*dy||1;let t=Math.max(0,Math.min(1,((mx-s.a[0])*dx+(my-s.a[1])*dy)/l2));const d=Math.hypot(mx-(s.a[0]+t*dx),my-(s.a[1]+t*dy));if(d<bd){bd=d;best=s.r;}}
 ctx.globalAlpha=1;if(hover){if(best){tip.style.display='block';tip.style.left=(hover.x+14)+'px';tip.style.top=(hover.y+14)+'px';tip.innerHTML='<b>'+best.id+'</b><br>'+best.i;cv.style.cursor='pointer';}else{tip.style.display='none';cv.style.cursor='grab';}}
 hoverRun=best;}
let hoverRun=null;
function size(){const r=window.devicePixelRatio||1;W=innerWidth;H=innerHeight;cv.width=W*r;cv.height=H*r;ctx.setTransform(r,0,0,r,0,0);draw();}
addEventListener('resize',size);size();
cv.addEventListener('mousedown',e=>{drag={x:e.clientX,y:e.clientY,pan:e.button===2||e.shiftKey,moved:false};});
addEventListener('mouseup',()=>{if(drag&&!drag.moved){pinned=hoverRun?(pinned===hoverRun.id?null:hoverRun.id):null;draw();}drag=null;});
addEventListener('mousemove',e=>{hover={x:e.clientX,y:e.clientY};if(drag){const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>2)drag.moved=true;drag.x=e.clientX;drag.y=e.clientY;
 if(drag.pan){pan[0]+=dx;pan[1]+=dy;}else{yaw+=dx*0.008;pitch=Math.max(-1.55,Math.min(1.55,pitch+dy*0.008));}}draw();});
cv.addEventListener('contextmenu',e=>e.preventDefault());cv.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(0.2,Math.min(12,zoom*Math.exp(-e.deltaY*0.0012)));draw();},{passive:false});
for(const id of ['power','optical','fluid','equip','shell'])document.getElementById(id).onchange=draw;
function view(y,p){yaw=y;pitch=p;pan=[0,0];zoom=1;draw();}
iso.onclick=()=>view(-0.75,0.5);top.onclick=()=>view(0,1.5707);front.onclick=()=>view(0,0);side.onclick=()=>view(-1.5707,0);
</script></body></html>"""


# ==============================================================================================================================
#  Everything at once
# ==============================================================================================================================
def write_all(S: Systems, design, folder, include_check: bool = True) -> dict:
    """Write the three diagrams, the 3D routing view and the CSV schedules into a folder. Returns {name: path}."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    res = S.analyse(design)
    findings = S.check(design, include_clash=include_check)
    paths = {}
    for name, html_text in (("wiring_diagram.html", wiring_diagram(S, design, res, findings)), ("cooling_diagram.html", cooling_diagram(S, design, res, findings)),
                            ("optical_diagram.html", optical_diagram(S, design, res, findings)), ("routing_3d_view.html", routing_view(S, design, res))):
        (folder / name).write_text(html_text, encoding="utf-8")
        paths[name] = str(folder / name)
    for kind, rows in S.schedules(design).items():
        p = folder / f"{kind}_schedule.csv"
        p.write_text(S.to_csv(rows), encoding="utf-8")
        paths[p.name] = str(p)
    return paths
