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

import numpy as np

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
            name=p.name, type=p.kind, role=p.role, material=p.material, asm=design.assembly_path(p.assembly),
            c=[round(v, 3) for v in p.color],
            mass=round(p.mass_kg, 2), size=[round(mx[i] - mn[i], 1) for i in range(3)], bb=[mn, mx],
            com=[c.X, c.Y, c.Z], v=[round(co, 2) for q in verts for co in (q.X, q.Y, q.Z)],
            i=[i for t in tris for i in t]))
    return dict(name=design.name, parts=parts, joints=design.joints(), mass=meas["total_mass_kg"], com=meas["centre_of_mass_mm"],
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
#joints{position:absolute;left:10px;bottom:30px;width:300px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px;display:none}
#joints h4{margin:0 0 6px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.jr{display:grid;grid-template-columns:1fr 54px;gap:2px 8px;align-items:center;margin:5px 0}.jr .lab{grid-column:1/3;display:flex;justify-content:space-between}
.jr input[type=range]{width:100%}.jr .deg{font-variant-numeric:tabular-nums;text-align:right}
.grp{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:10px 4px 2px;display:flex;justify-content:space-between}
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
<div id="info"></div><div id="tip"></div><div id="joints"></div>
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
  const holder = new THREE.Group(); holder.add(mesh, edges); holder.matrixAutoUpdate = false; group.add(holder);
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
let explodeK = 0;
const joints = (D.joints || []).map(j => ({...j, cur: j.angle_deg}));
const byName = {}; items.forEach(i => byName[i.p.name] = i);
function jointMatrix(j){ const delta = (j.cur - j.angle_deg) * Math.PI/180; const piv = new THREE.Vector3(...j.pivot_mm).multiplyScalar(MM);
  const R = new THREE.Matrix4().makeRotationAxis(new THREE.Vector3(...j.axis).normalize(), delta);
  return new THREE.Matrix4().makeTranslation(piv.x,piv.y,piv.z).multiply(R).multiply(new THREE.Matrix4().makeTranslation(-piv.x,-piv.y,-piv.z)); }
function applyPose(){
  items.forEach(i => { const M = new THREE.Matrix4();
    // outermost joint first, so a flap on a wing moves with the wing
    joints.filter(j => j.parts.indexOf(i.p.name) >= 0).sort((a,b) => a.inside_joints.length - b.inside_joints.length)
      .forEach(j => M.multiply(jointMatrix(j)));
    const off = i.bc.clone().sub(centre).multiplyScalar(explodeK);
    i.holder.matrix.copy(new THREE.Matrix4().makeTranslation(off.x, off.y, off.z).multiply(M)); i.holder.matrixWorldNeedsUpdate = true; }); }
$('explode').oninput = e => { explodeK = e.target.value/100*1.5; applyPose(); };
if (joints.length) { const box = $('joints'); box.style.display = 'block'; box.innerHTML = '<h4>Joints (hinges)</h4>';
  joints.forEach(j => { const row = document.createElement('div'); row.className = 'jr';
    row.innerHTML = '<div class="lab"><span></span><span class="deg"></span></div><input type="range" step="0.5"><button>Reset</button>';
    row.querySelector('.lab span').textContent = j.path + (j.variable ? '  (' + j.variable + ')' : '');
    const rng = row.querySelector('input'), deg = row.querySelector('.deg');
    rng.min = j.angle_min; rng.max = j.angle_max; rng.value = j.cur; deg.textContent = j.cur.toFixed(1) + '°';
    rng.oninput = () => { j.cur = parseFloat(rng.value); deg.textContent = j.cur.toFixed(1) + '°'; applyPose(); };
    row.querySelector('button').onclick = () => { rng.value = j.angle_deg; rng.oninput(); }; box.appendChild(row); }); }
function applyTheme(){ const light = document.documentElement.classList.contains('light');
  renderer.setClearColor(light ? 0xeef1f5 : 0x10151c); grid.visible = true;
  const c = light ? [0xb9c3d1, 0xd9dfe8] : [0x3a4658, 0x252d3a]; grid.material.color.setHex(c[0]); }

// part tree: nested by assembly when the design has assemblies, otherwise grouped by role
const tree = $('tree'); const hasAsm = D.parts.some(p => p.asm);
function addRow(i, depth){ const r = document.createElement('div'); r.className = 'row'; r.style.paddingLeft = (4 + depth*14) + 'px';
  r.innerHTML = '<input type="checkbox" checked><span class="sw" style="background:rgb(' + i.p.c.map(v => Math.round(v*255)).join(',') + ')"></span><span class="n"></span><span class="m"></span>';
  r.querySelector('.n').textContent = i.p.name; r.querySelector('.m').textContent = i.p.mass.toLocaleString(undefined,{maximumFractionDigits:0}) + ' kg';
  r.querySelector('input').onchange = e => { i.visible = e.target.checked; i.holder.visible = i.visible; };
  r.onclick = e => { if (e.target.tagName !== 'INPUT') select(i); }; i.row = r; tree.appendChild(r); }
function addHead(text, mass, depth){ const h = document.createElement('div'); h.className = 'grp'; h.style.paddingLeft = (4 + depth*14) + 'px';
  h.innerHTML = '<span></span><span></span>'; h.children[0].textContent = text; h.children[1].textContent = mass.toLocaleString(undefined,{maximumFractionDigits:0}) + ' kg'; tree.appendChild(h); }
if (hasAsm) {
  const paths = [...new Set(D.parts.map(p => p.asm || ''))];
  const all = new Set(); paths.forEach(p => { const seg = p.split('/').filter(Boolean); seg.forEach((_, k) => all.add(seg.slice(0, k+1).join('/'))); });
  const order = ['', ...[...all].sort()];
  order.forEach(path => { const depth = path ? path.split('/').length - 1 : 0;
    const direct = items.filter(i => (i.p.asm || '') === path);
    const total = items.filter(i => path === '' ? !i.p.asm : ((i.p.asm || '') === path || (i.p.asm || '').startsWith(path + '/'))).reduce((a, i) => a + i.p.mass, 0);
    if (path === '' && !direct.length) return;
    addHead(path ? path.split('/').pop() : 'Top level', total, depth); direct.forEach(i => addRow(i, depth + 1)); });
} else {
  const roles = {}; items.forEach(i => (roles[i.p.role] = roles[i.p.role] || []).push(i));
  Object.keys(roles).sort().forEach(role => { addHead(role + ' (' + roles[role].length + ')', roles[role].reduce((a, i) => a + i.p.mass, 0), 0); roles[role].forEach(i => addRow(i, 0)); });
}
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
window.__satcad = {items, select, setView, joints, applyPose};
applyPose();
}
</script></body></html>
"""


# --------------------------------------------------------------------------- #
# Mode-shape viewer: the FE mesh, animated mode by mode
# --------------------------------------------------------------------------- #
def mode_data(model, results: dict) -> dict:
    """Mesh, rest positions and the first few normalised mode shapes, as plain lists for the browser."""
    xyz = model.mesh.xyz
    shapes = results["mode_shapes"]
    eff = results["effective_mass_fraction"]
    modes = []
    for k, u in enumerate(shapes):
        modes.append(dict(index=k + 1, hz=round(results["frequencies_hz"][k], 4),
                          eff=[round(eff[a][k], 4) for a in "xyz"], u=[round(float(c), 4) for c in np.asarray(u).ravel()]))
    plate_names = [p.name for p in model.mesh.plates]
    return dict(name="mode shapes", nodes=[round(float(c), 2) for c in xyz.ravel()],
                tris=[int(i) for t in model.mesh.tris for i in t], tri_plate=[int(p) for p in model.mesh.tri_plate],
                plates=plate_names, modes=modes, rigid=results.get("rigid_body_modes_removed", 0),
                size=[round(float(v), 1) for v in (xyz.max(axis=0) - xyz.min(axis=0))],
                warnings=list(model.warnings))


def mode_viewer_html(model, results: dict, title: str = "design") -> str:
    return (_MODE_TEMPLATE.replace("__DATA__", _script_safe_json(mode_data(model, results)))
            .replace("__THREE__", THREE_URL).replace("__ORBIT__", ORBIT_URL)
            .replace("__TITLE__", title.replace("<", "").replace(">", "")))


def write_mode_viewer(model, results: dict, path, title: str = "design") -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(mode_viewer_html(model, results, title), encoding="utf-8")
    return str(path)


_MODE_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ (SatCAD mode shapes)</title>
<style>
:root{--bg:#10151c;--panel:#18202b;--line:#2a3544;--text:#e6ebf2;--muted:#8b97a8;--accent:#4da3ff}
*{box-sizing:border-box}html,body{margin:0;height:100%;font:13px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--text)}
#app{display:grid;grid-template-columns:300px 1fr;height:100%}#side{background:var(--panel);border-right:1px solid var(--line);overflow:auto;padding:12px}
h1{font-size:15px;margin:0 0 4px}.muted{color:var(--muted)}.mode{display:grid;grid-template-columns:1fr auto;gap:2px 8px;padding:7px 8px;border:1px solid var(--line);border-radius:6px;margin:6px 0;cursor:pointer}
.mode:hover{border-color:var(--accent)}.mode.on{border-color:var(--accent);background:rgba(77,163,255,.14)}.mode b{font-size:14px}.mode .eff{grid-column:1/3;color:var(--muted);font-size:11px}
.row{margin:10px 0}.row label{display:flex;justify-content:space-between}input[type=range]{width:100%}
button{background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:5px 9px;font:inherit;cursor:pointer;margin-right:4px}button:hover,button.on{border-color:var(--accent);color:var(--accent)}
#view{position:relative;overflow:hidden}.warn{margin-top:10px;font-size:11px;color:#d9a441;border-top:1px solid var(--line);padding-top:8px}
#err{position:absolute;inset:0;display:none;align-items:center;justify-content:center;text-align:center;padding:30px;color:var(--muted)}
#bar{position:absolute;left:12px;bottom:12px;right:12px;height:10px;border-radius:5px;background:linear-gradient(90deg,#2b3a8f,#2aa9d6,#5bd27a,#f1e24a,#e8613a);opacity:.9;max-width:260px}
#barl{position:absolute;left:12px;bottom:26px;font-size:11px;color:var(--muted)}
@media(max-width:700px){#app{grid-template-columns:1fr;grid-template-rows:auto 1fr}}
</style></head>
<body><div id="app"><div id="side">
<h1>Mode shapes</h1><div class="muted" id="sub"></div>
<div id="modes"></div>
<div class="row"><label><span>Amplitude</span><span id="av"></span></label><input id="amp" type="range" min="0" max="100" value="35"></div>
<div class="row"><label><span>Speed</span><span id="sv"></span></label><input id="spd" type="range" min="1" max="100" value="30"></div>
<div class="row"><button id="play" class="on">Pause</button><button id="wire">Wireframe</button><button id="reset">Reset view</button></div>
<div class="muted" style="font-size:11px">Colour = relative displacement (blue small, red large). The motion is exaggerated: it shows the SHAPE, not the real size. Frequencies come from the beta shell solver; cross-check before relying on them.</div>
<div class="warn" id="warn"></div></div>
<div id="view"><div id="err">The 3D viewer needs the three.js library from the internet. Connect and reload.</div><div id="barl">relative displacement</div><div id="bar"></div></div></div>
<script src="__THREE__"></script><script src="__ORBIT__"></script>
<script>
const D = __DATA__; const $ = id => document.getElementById(id);
if (typeof THREE === 'undefined' || !THREE.OrbitControls) { $('err').style.display = 'flex'; } else {
const MM = 0.001, root = $('view');
const renderer = new THREE.WebGLRenderer({antialias:true}); renderer.setPixelRatio(Math.min(devicePixelRatio,2)); renderer.setClearColor(0x10151c); root.insertBefore(renderer.domElement, root.firstChild);
const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(35,1,0.01,5000); camera.up.set(0,0,1);
const controls = new THREE.OrbitControls(camera, renderer.domElement); controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xffffff,0x556677,0.9)); const key = new THREE.DirectionalLight(0xffffff,0.6); key.position.set(6,-9,12); scene.add(key);
const n = D.nodes.length/3, rest = new Float32Array(D.nodes.map(v => v*MM)), pos = new Float32Array(rest), col = new Float32Array(n*3);
const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(pos,3)); geo.setAttribute('color', new THREE.BufferAttribute(col,3)); geo.setIndex(D.tris);
const mat = new THREE.MeshBasicMaterial({vertexColors:true, side:THREE.DoubleSide}); const mesh = new THREE.Mesh(geo, mat); scene.add(mesh);
const ghost = new THREE.LineSegments(new THREE.WireframeGeometry(new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(rest,3)).setIndex(D.tris)), new THREE.LineBasicMaterial({color:0x445066,transparent:true,opacity:.35})); scene.add(ghost);
const lo = [1e9,1e9,1e9], hi = [-1e9,-1e9,-1e9]; for (let i=0;i<n;i++) for (let k=0;k<3;k++){ lo[k]=Math.min(lo[k],rest[3*i+k]); hi[k]=Math.max(hi[k],rest[3*i+k]); }
const ctr = new THREE.Vector3((lo[0]+hi[0])/2,(lo[1]+hi[1])/2,(lo[2]+hi[2])/2), R = Math.hypot(hi[0]-lo[0],hi[1]-lo[1],hi[2]-lo[2])/2;
function resize(){ const w=root.clientWidth,h=root.clientHeight; renderer.setSize(w,h); camera.aspect=w/h; camera.updateProjectionMatrix(); }
function view(){ const d = new THREE.Vector3(0.9,-1.1,0.75).normalize().multiplyScalar(R/Math.sin(camera.fov*Math.PI/360)*1.1); controls.target.copy(ctr); camera.position.copy(ctr).add(d); camera.near=R/200; camera.far=R*60; camera.updateProjectionMatrix(); controls.update(); }
const ramp = t => { const s=[[0,[43,58,143]],[.25,[42,169,214]],[.5,[91,210,122]],[.75,[241,226,74]],[1,[232,97,58]]]; for (let i=1;i<s.length;i++) if (t<=s[i][0]){ const f=(t-s[i-1][0])/(s[i][0]-s[i-1][0]); return s[i][1].map((c,k)=>(c+(s[i-1][1][k]-c)*(1-f))/255); } return [232/255,97/255,58/255]; };
let cur = 0, play = true, t0 = performance.now();
$('sub').textContent = D.modes.length + ' modes shown' + (D.rigid ? ' (free-free: ' + D.rigid + ' rigid-body modes removed)' : ' (fixed base)') + '; model ' + D.size.map(v=>(v/1000).toFixed(1)).join(' x ') + ' m';
D.modes.forEach((m,i) => { const el = document.createElement('div'); el.className='mode'; el.innerHTML='<b></b><span class="muted"></span><span class="eff"></span>';
  el.children[0].textContent = 'Mode ' + m.index; el.children[1].textContent = m.hz.toFixed(m.hz<10?3:2) + ' Hz';
  el.children[2].textContent = 'effective mass  X ' + (100*m.eff[0]).toFixed(1) + '%   Y ' + (100*m.eff[1]).toFixed(1) + '%   Z ' + (100*m.eff[2]).toFixed(1) + '%';
  el.onclick = () => { cur = i; document.querySelectorAll('.mode').forEach((x,j)=>x.classList.toggle('on',j===i)); recolor(); }; $('modes').appendChild(el); });
document.querySelectorAll('.mode')[0].classList.add('on'); $('warn').innerHTML = D.warnings.length ? '<b>Modelling notes</b><br>' + D.warnings.map(w=>w.replace(/</g,'&lt;')).join('<br>') : '';
function recolor(){ const u = D.modes[cur].u; for (let i=0;i<n;i++){ const m = Math.hypot(u[3*i],u[3*i+1],u[3*i+2]); const c = ramp(Math.min(1,m)); col[3*i]=c[0]; col[3*i+1]=c[1]; col[3*i+2]=c[2]; } geo.attributes.color.needsUpdate = true; }
function frame(now){ requestAnimationFrame(frame); controls.update(); const u = D.modes[cur].u; const amp = $('amp').value/100*0.2*R*2, spd = $('spd').value/100*4;
  const ph = play ? Math.sin((now-t0)/1000*spd*2*Math.PI) : 1; $('av').textContent = ($('amp').value) + '%'; $('sv').textContent = spd.toFixed(1)+' Hz (slowed)';
  for (let i=0;i<n;i++){ pos[3*i]=rest[3*i]+u[3*i]*amp*ph; pos[3*i+1]=rest[3*i+1]+u[3*i+1]*amp*ph; pos[3*i+2]=rest[3*i+2]+u[3*i+2]*amp*ph; } geo.attributes.position.needsUpdate = true; renderer.render(scene,camera); }
$('play').onclick = e => { play = !play; e.target.textContent = play ? 'Pause' : 'Play'; e.target.classList.toggle('on', play); };
$('wire').onclick = e => { mat.wireframe = !mat.wireframe; e.target.classList.toggle('on', mat.wireframe); }; $('reset').onclick = view;
addEventListener('resize', resize); resize(); view(); recolor(); requestAnimationFrame(frame); window.__modes = {D, mesh, setMode:i=>document.querySelectorAll('.mode')[i].click()};
}
</script></body></html>
"""
