"""
Interactive 3D viewer: writes ONE self-contained .html file you can open in any browser.

    from satcad.viewer import write_viewer
    write_viewer(design, "my_sat_viewer.html")

What you can do in the page: drag to orbit, scroll to zoom, right-drag to pan; click a part (in the model or in the tree) to see
its material, mass and size; hide/show parts; explode the assembly; see the centre of mass; switch standard views;
save a PNG. The model data is embedded in the file. The 3D library (three.js) is loaded from a public CDN, so the
first open needs an internet connection.
"""
from __future__ import annotations

import json
from pathlib import Path

from .engine import Design, DesignError

THREE_URL = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"
ORBIT_URL = "https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"


def mesh_data(design: Design) -> dict:
    """Tessellate every part into triangles the browser can draw."""
    if not design.parts:
        raise DesignError("Nothing to show: the design is empty. Add a chassis first.")
    meas = design.measure()
    parts = []
    for p in design.parts.values():
        mn, mx = p.bbox()
        size = max(mx[i] - mn[i] for i in range(3))
        verts, tris = p.solid.tessellate(max(size / 150.0, 0.02), 0.25)
        c = p.solid.center()
        parts.append(dict(
            name=p.name, type=p.kind, role=p.role, material=p.material, c=[round(v, 3) for v in p.color],
            mass=round(p.mass_kg, 2), size=[round(mx[i] - mn[i], 1) for i in range(3)], bb=[mn, mx],
            com=[c.X, c.Y, c.Z], v=[round(co, 2) for q in verts for co in (q.X, q.Y, q.Z)],
            i=[i for t in tris for i in t]))
    return dict(name=design.name, parts=parts, mass=meas["total_mass_kg"], com=meas["centre_of_mass_mm"],
                size=meas["overall_size_mm"], moments=meas.get("principal_moments_kg_m2"))


def _script_safe_json(obj) -> str:
    """JSON that is safe to embed inside a <script> tag: no '<', '>' or '&' (so a design named "</script>..." can't
    break out of the page) and no line separators that older browsers treat as newlines."""
    return (json.dumps(obj, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def viewer_html(design: Design) -> str:
    return (_TEMPLATE.replace("__DATA__", _script_safe_json(mesh_data(design)))
            .replace("__THREE__", THREE_URL).replace("__ORBIT__", ORBIT_URL)
            .replace("__TITLE__", design.name.replace("<", "").replace(">", "")))


def write_viewer(design: Design, path) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(viewer_html(design), encoding="utf-8")
    return str(path)


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ (SatCAD viewer)</title>
<style>
:root{--bg:#10151c;--panel:#18202b;--line:#2a3544;--text:#e6ebf2;--muted:#8b97a8;--accent:#4da3ff}
:root.light{--bg:#eef1f5;--panel:#ffffff;--line:#d3dae3;--text:#1b2430;--muted:#5d6b7d;--accent:#1565c0}
*{box-sizing:border-box}html,body{margin:0;height:100%;font:13px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--text)}
#app{display:grid;grid-template-columns:270px 1fr;grid-template-rows:auto 1fr;height:100%}
header{grid-column:1/3;display:flex;align-items:center;gap:14px;padding:8px 14px;background:var(--panel);border-bottom:1px solid var(--line);flex-wrap:wrap}
header h1{font-size:15px;margin:0}header .stat{color:var(--muted)}header .stat b{color:var(--text);font-weight:600}
#tree{background:var(--panel);border-right:1px solid var(--line);overflow:auto;padding:8px}
#tree h2{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:10px 4px 4px}
.row{display:flex;align-items:center;gap:6px;padding:3px 4px;border-radius:4px;cursor:pointer}.row:hover{background:rgba(127,127,127,.15)}.row.sel{background:rgba(77,163,255,.22)}
.sw{width:12px;height:12px;border-radius:3px;flex:none;border:1px solid rgba(0,0,0,.35)}.row .n{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.row .m{color:var(--muted);font-size:11px}
#view{position:relative;overflow:hidden}canvas{display:block}
#toolbar{position:absolute;top:10px;left:10px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
button,label.btn{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:5px 9px;font:inherit;cursor:pointer}
button:hover,label.btn:hover{border-color:var(--accent)}button.on{border-color:var(--accent);color:var(--accent)}
#info{position:absolute;right:10px;top:10px;width:250px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px;display:none}
#info h3{margin:0 0 6px;font-size:14px}#info table{width:100%;border-collapse:collapse}#info td{padding:2px 0;vertical-align:top}#info td:first-child{color:var(--muted);width:90px}
#tip{position:absolute;pointer-events:none;background:#000c;color:#fff;padding:3px 7px;border-radius:4px;font-size:12px;display:none;white-space:nowrap}
#err{position:absolute;inset:0;display:none;align-items:center;justify-content:center;text-align:center;padding:30px;color:var(--muted)}
#slider{display:flex;align-items:center;gap:6px;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:4px 8px}
#foot{position:absolute;left:10px;bottom:8px;color:var(--muted);font-size:11px}
@media(max-width:700px){#app{grid-template-columns:1fr;grid-template-rows:auto 200px 1fr}#tree{border-right:0;border-bottom:1px solid var(--line)}#info{width:200px}}
</style></head>
<body><div id="app">
<header><h1>__TITLE__</h1><span class="stat">Mass <b id="smass"></b></span><span class="stat">Size <b id="ssize"></b></span>
<span class="stat">Centre of mass <b id="scom"></b></span><span class="stat" id="smom"></span><span style="flex:1"></span>
<button id="theme">Light / dark</button></header>
<div id="tree"></div>
<div id="view"><div id="toolbar">
<button data-v="iso">Isometric</button><button data-v="top">Top</button><button data-v="front">Front</button><button data-v="side">Side</button><button id="fit">Fit</button>
<button id="edges" class="on">Edges</button><button id="wire">Wireframe</button><button id="comb">Centre of mass</button><button id="shot">Save PNG</button>
<div id="slider"><span>Explode</span><input id="explode" type="range" min="0" max="100" value="0"></div></div>
<div id="info"></div><div id="tip"></div>
<div id="err">The 3D viewer needs the three.js library from the internet (cdnjs.cloudflare.com / cdn.jsdelivr.net).<br>Connect to the internet and reload this page.</div>
<div id="foot">Drag: orbit &nbsp; Scroll: zoom &nbsp; Right-drag: pan &nbsp; Click a part for details. Units: mm, kg. Axes: X forward, Y left, Z up.</div></div>
</div>
<script src="__THREE__"></script><script src="__ORBIT__"></script>
<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
$('smass').textContent = D.mass.toLocaleString(undefined,{maximumFractionDigits:0}) + ' kg';
$('ssize').textContent = D.size.map(v => (v/1000).toFixed(2)).join(' x ') + ' m';
$('scom').textContent = D.com ? '(' + D.com.map(v => (v/1000).toFixed(2)).join(', ') + ') m' : 'n/a';
if (D.moments) $('smom').innerHTML = 'Principal inertia <b>' + D.moments.map(v => Math.round(v).toLocaleString()).join(' / ') + '</b> kg·m²';
$('theme').onclick = () => { document.documentElement.classList.toggle('light'); applyTheme(); };
if (typeof THREE === 'undefined' || !THREE.OrbitControls) { $('err').style.display = 'flex'; }
else {
const MM = 0.001, root = $('view');
const renderer = new THREE.WebGLRenderer({antialias:true, preserveDrawingBuffer:true});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2)); root.insertBefore(renderer.domElement, root.firstChild);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 5000); camera.up.set(0,0,1);
const controls = new THREE.OrbitControls(camera, renderer.domElement); controls.enableDamping = true; controls.dampingFactor = 0.1;
scene.add(new THREE.HemisphereLight(0xffffff, 0x556677, 0.7));
const key = new THREE.DirectionalLight(0xffffff, 0.85); key.position.set(6,-9,12); scene.add(key);
const fill = new THREE.DirectionalLight(0xffffff, 0.35); fill.position.set(-8,7,-4); scene.add(fill);
const group = new THREE.Group(); scene.add(group);
const centre = new THREE.Vector3(); let items = [];
D.parts.forEach(p => {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(p.v.map(x => x*MM), 3)); g.setIndex(p.i); g.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({color:new THREE.Color(p.c[0],p.c[1],p.c[2]), metalness:0.25, roughness:0.55,
    side:THREE.DoubleSide, polygonOffset:true, polygonOffsetFactor:1, polygonOffsetUnits:1});
  const mesh = new THREE.Mesh(g, mat);
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(g, 28), new THREE.LineBasicMaterial({color:0x0b0f14}));
  const holder = new THREE.Group(); holder.add(mesh, edges); group.add(holder);
  const bc = new THREE.Vector3((p.bb[0][0]+p.bb[1][0])/2, (p.bb[0][1]+p.bb[1][1])/2, (p.bb[0][2]+p.bb[1][2])/2).multiplyScalar(MM);
  items.push({p, mesh, edges, holder, mat, bc, visible:true});
  mesh.userData.item = items[items.length-1];
});
const box = new THREE.Box3().setFromObject(group), sphere = box.getBoundingSphere(new THREE.Sphere()); box.getCenter(centre);
const R = sphere.radius;
const grid = new THREE.GridHelper(Math.ceil(R*4/ Math.pow(10, Math.floor(Math.log10(R*4)))) * Math.pow(10, Math.floor(Math.log10(R*4))), 20, 0x3a4658, 0x252d3a);
grid.rotation.x = Math.PI/2; grid.position.z = box.min.z - R*0.02; scene.add(grid);
const axes = new THREE.AxesHelper(R*0.35); axes.position.set(box.min.x, box.min.y, box.min.z - R*0.02); scene.add(axes);
const comMark = new THREE.Mesh(new THREE.SphereGeometry(R*0.03, 20, 14), new THREE.MeshBasicMaterial({color:0xff3b3b, depthTest:false}));
comMark.renderOrder = 10; comMark.visible = false; if (D.com) comMark.position.set(D.com[0]*MM, D.com[1]*MM, D.com[2]*MM); scene.add(comMark);

function resize(){ const w = root.clientWidth, h = root.clientHeight; renderer.setSize(w, h); camera.aspect = w/h; camera.updateProjectionMatrix(); }
function setView(dir){ const d = new THREE.Vector3(...dir).normalize().multiplyScalar(R/Math.sin(camera.fov*Math.PI/360)*1.15);
  controls.target.copy(centre); camera.position.copy(centre).add(d); camera.near = R/200; camera.far = R*60; camera.updateProjectionMatrix(); controls.update(); }
const VIEWS = {iso:[0.9,-1.1,0.75], top:[0,-0.0008,1], front:[1,0,0.0001], side:[0,-1,0.0001]};
document.querySelectorAll('[data-v]').forEach(b => b.onclick = () => setView(VIEWS[b.dataset.v]));
$('fit').onclick = () => setView(VIEWS.iso);
let showEdges = true, wire = false;
$('edges').onclick = e => { showEdges = !showEdges; e.target.classList.toggle('on', showEdges); items.forEach(i => i.edges.visible = showEdges && i.visible); };
$('wire').onclick = e => { wire = !wire; e.target.classList.toggle('on', wire); items.forEach(i => i.mat.wireframe = wire); };
$('comb').onclick = e => { comMark.visible = !comMark.visible; e.target.classList.toggle('on', comMark.visible); };
$('shot').onclick = () => { renderer.render(scene, camera); const a = document.createElement('a'); a.download = D.name + '.png'; a.href = renderer.domElement.toDataURL('image/png'); a.click(); };
$('explode').oninput = e => { const k = e.target.value/100*1.5; items.forEach(i => i.holder.position.copy(i.bc.clone().sub(centre).multiplyScalar(k))); };
function applyTheme(){ const light = document.documentElement.classList.contains('light');
  renderer.setClearColor(light ? 0xeef1f5 : 0x10151c); grid.visible = true;
  const c = light ? [0xb9c3d1, 0xd9dfe8] : [0x3a4658, 0x252d3a]; grid.material.color.setHex(c[0]); }

// tree grouped by role
const tree = $('tree'); const roles = {}; items.forEach(i => (roles[i.p.role] = roles[i.p.role] || []).push(i));
Object.keys(roles).sort().forEach(role => {
  const h = document.createElement('h2'); h.textContent = role + ' (' + roles[role].length + ')'; tree.appendChild(h);
  roles[role].forEach(i => {
    const r = document.createElement('div'); r.className = 'row';
    r.innerHTML = '<input type="checkbox" checked><span class="sw" style="background:rgb(' + i.p.c.map(v => Math.round(v*255)).join(',') + ')"></span><span class="n"></span><span class="m"></span>';
    r.querySelector('.n').textContent = i.p.name; r.querySelector('.m').textContent = i.p.mass.toLocaleString(undefined,{maximumFractionDigits:0}) + ' kg';
    r.querySelector('input').onchange = e => { i.visible = e.target.checked; i.holder.visible = i.visible; };
    r.onclick = e => { if (e.target.tagName !== 'INPUT') select(i); }; i.row = r; tree.appendChild(r);
  });
});
let sel = null, hov = null;
function paint(i){ if (!i) return; const on = i === sel ? 0x2a6cb8 : (i === hov ? 0x1d4d85 : 0x000000); i.mat.emissive.setHex(on); }
function select(i){ const prev = sel; sel = i; paint(prev); paint(i); items.forEach(x => x.row && x.row.classList.toggle('sel', x === sel));
  const info = $('info'); if (!i) { info.style.display = 'none'; return; } const p = i.p;
  info.style.display = 'block';
  info.innerHTML = '<h3></h3><table><tr><td>Type</td><td></td></tr><tr><td>Role</td><td></td></tr><tr><td>Material</td><td></td></tr><tr><td>Mass</td><td></td></tr><tr><td>Size</td><td></td></tr><tr><td>Centre</td><td></td></tr></table>';
  info.querySelector('h3').textContent = p.name; const td = info.querySelectorAll('td:nth-child(2)');
  td[0].textContent = p.type; td[1].textContent = p.role; td[2].textContent = p.material.replace(/_/g,' ');
  td[3].textContent = p.mass.toLocaleString(undefined,{maximumFractionDigits:1}) + ' kg';
  td[4].textContent = p.size.map(v => (v/1000).toFixed(3)).join(' x ') + ' m'; td[5].textContent = '(' + p.com.map(v => (v/1000).toFixed(2)).join(', ') + ') m'; }
const ray = new THREE.Raycaster(), mouse = new THREE.Vector2(), tip = $('tip');
function pick(ev){ const r = renderer.domElement.getBoundingClientRect(); mouse.set((ev.clientX-r.left)/r.width*2-1, -(ev.clientY-r.top)/r.height*2+1);
  ray.setFromCamera(mouse, camera); const hits = ray.intersectObjects(items.filter(i => i.visible).map(i => i.mesh), false); return hits.length ? hits[0].object.userData.item : null; }
renderer.domElement.addEventListener('pointermove', ev => { const h = pick(ev); if (h !== hov) { const p = hov; hov = h; paint(p); paint(h); }
  if (h) { tip.style.display = 'block'; tip.style.left = (ev.clientX - root.getBoundingClientRect().left + 12) + 'px'; tip.style.top = (ev.clientY - root.getBoundingClientRect().top + 12) + 'px'; tip.textContent = h.p.name + ' · ' + h.p.mass.toLocaleString(undefined,{maximumFractionDigits:0}) + ' kg'; } else tip.style.display = 'none'; });
let down = null; renderer.domElement.addEventListener('pointerdown', e => down = [e.clientX, e.clientY]);
renderer.domElement.addEventListener('pointerup', e => { if (down && Math.hypot(e.clientX-down[0], e.clientY-down[1]) < 4) select(pick(e)); });
window.addEventListener('resize', resize); resize(); applyTheme(); setView(VIEWS.iso);
(function loop(){ requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
window.__satcad = {items, select, setView};
}
</script></body></html>
"""
