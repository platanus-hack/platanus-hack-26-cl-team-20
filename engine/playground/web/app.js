// Visor 3D del Live Mapping Playground.
// Escena (heightfield texturizado con scene_pov) + objetos segmentados + frustum del proyector, y el lienzo
// en vivo (/api/frame.png, ~10 Hz) como textura emisiva sobre la malla. Datos: GET /api/scene3d (px del proyector).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = (s) => document.querySelector(s);

// Colores por clase: teclas verdes, cocodrilo amarillo, buda naranja, torre cian, papel blanco, ddj magenta
const CLASS_COLORS = {
  tecla_blanca: '#3ddc84', tecla_negra: '#1c9d57', teclado: '#3ddc84',
  cocodrilo: '#ffd23f', buda: '#ff8c1a', cuadro: '#ff8c1a', torre: '#22d3ee', papel: '#f4f4f5',
  ddj: '#ff3fd8', circuit: '#a78bfa', pantalla: '#60a5fa', mesa: '#6b7280',
};
const colorOf = (cls) => CLASS_COLORS[cls] || '#94a3b8';
const FRAME_HZ = 10;          // refresco del lienzo del proyector
const FOV = 40;

const ui = { exag: 1, overlay: 1, room: 1, mode: 'solid', live: true };
let data = null;              // último JSON de /api/scene3d
let W = 1920, H = 1080;       // tamaño de la escena en px del proyector
const apex = new THREE.Vector3(0, 0, 2304);   // apex del proyector en el mundo
let selectedId = null;
const items = new Map();      // id -> { obj, mesh, line }

// ---------------------------------------------------------------- three
const canvas = $('#view');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b0d10);
const camera = new THREE.PerspectiveCamera(FOV, 1, 5, 80000);
camera.position.set(900, -2200, 2600);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
const hemi = new THREE.HemisphereLight(0xe8eeff, 0x1a1d22, 1);
const sun = new THREE.DirectionalLight(0xffffff, 0.8);
sun.position.set(-900, 700, 1800);
scene.add(hemi, sun);

// la malla: foto de la escena como albedo + lienzo del proyector como emisivo (luz que se suma, como la real)
const terrainMat = new THREE.MeshStandardMaterial({
  color: 0xffffff, roughness: 0.95, metalness: 0, emissive: 0x000000, side: THREE.DoubleSide,
});
let terrain = null;
let frustum = null;
const objGroup = new THREE.Group();
scene.add(objGroup);

// ---------------------------------------------------------------- coordenadas
// px del proyector (x a la derecha, y hacia abajo) → mundo (X derecha, Y arriba, Z hacia el proyector)
const toWorld = (x, y) => [x - W / 2, H / 2 - y];

// un punto del plano (X, Y) a altura Z se mueve sobre el rayo que sale del apex:
// así el lienzo cae sobre la malla donde lo pondría el proyector real
function onRay(X, Y, Z, out = new THREE.Vector3()) {
  const t = (apex.z - Z) / apex.z;
  return out.set(apex.x + t * (X - apex.x), apex.y + t * (Y - apex.y), Z);
}
const place = (x, y, z, out) => onRay(...toWorld(x, y), z, out);

function geometryOnRays(g) {
  const p = g.attributes.position;
  const v = new THREE.Vector3();
  for (let k = 0; k < p.count; k++) {
    onRay(p.getX(k), p.getY(k), p.getZ(k), v);
    p.setXYZ(k, v.x, v.y, v.z);
  }
  p.needsUpdate = true;
  g.computeVertexNormals();
}

// ---------------------------------------------------------------- texturas
async function fetchBitmap(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return createImageBitmap(await r.blob());
}

// ImageBitmap ignora UNPACK_FLIP_Y: flipY=false y las UV con v=0 arriba (fila 0 de la imagen)
function bitmapTexture(bmp) {
  const t = new THREE.Texture(bmp);
  t.flipY = false;
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = renderer.capabilities.getMaxAnisotropy();
  t.needsUpdate = true;
  return t;
}

function dropTexture(t) {
  if (!t) return;
  t.image?.close?.();
  t.dispose();
}

async function loadSceneTexture() {
  const bmp = await fetchBitmap(`${data.texture.url}?t=${Date.now()}`);
  const old = terrainMat.map;
  terrainMat.map = bitmapTexture(bmp);
  terrainMat.needsUpdate = true;
  dropTexture(old);
}

// lienzo del proyector: un fetch a la vez, ~10 Hz, en pausa si la pestaña no se ve
let frameTex = null;
let framesIn = 0;
async function pumpFrame() {
  const t0 = performance.now();
  if (ui.live && data && !document.hidden) {
    try {
      setFrame(await fetchBitmap(`${data.frame.url}?t=${Date.now()}`));
      framesIn++;
    } catch {
      // el runtime puede estar reiniciando: se reintenta en el siguiente ciclo
    }
  }
  setTimeout(pumpFrame, Math.max(10, 1000 / FRAME_HZ - (performance.now() - t0)));
}

function setFrame(bmp) {
  const same = frameTex && frameTex.image.width === bmp.width && frameTex.image.height === bmp.height;
  if (!same) {                         // primera vez o cambió el tamaño del lienzo: textura nueva
    dropTexture(frameTex);
    frameTex = bitmapTexture(bmp);
    frameTex.generateMipmaps = false;
    frameTex.minFilter = THREE.LinearFilter;
    terrainMat.emissiveMap = frameTex;
    terrainMat.emissive.set(0xffffff);
    terrainMat.needsUpdate = true;
    applyLights();
    return;
  }
  const old = frameTex.image;
  frameTex.image = bmp;
  frameTex.needsUpdate = true;
  old.close?.();
}

function applyLights() {
  hemi.intensity = 1.0 * ui.room;
  sun.intensity = 0.8 * ui.room;
  terrainMat.emissiveIntensity = frameTex && ui.live ? ui.overlay : 0;
}

// ---------------------------------------------------------------- heightfield
function buildTerrain(hf) {
  if (terrain) {
    terrain.geometry.dispose();
    scene.remove(terrain);
  }
  const { nx, ny, xs, ys } = hf;
  const uv = new Float32Array(nx * ny * 2);
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      const k = j * nx + i;
      uv[2 * k] = (xs[i] + 0.5) / W;
      uv[2 * k + 1] = (ys[j] + 0.5) / H;
    }
  }
  const index = [];
  for (let j = 0; j < ny - 1; j++) {
    for (let i = 0; i < nx - 1; i++) {
      const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
      index.push(a, c, b, b, c, d);          // caras hacia +Z (hacia el proyector)
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(nx * ny * 3), 3));
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  g.setIndex(index);
  terrain = new THREE.Mesh(g, terrainMat);
  scene.add(terrain);
  updateTerrain();
}

function updateTerrain() {
  const { nx, ny, xs, ys, z } = data.heightfield;
  const pos = terrain.geometry.attributes.position;
  const v = new THREE.Vector3();
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      const k = j * nx + i;
      place(xs[i], ys[j], z[k] * ui.exag, v);
      pos.setXYZ(k, v.x, v.y, v.z);
    }
  }
  pos.needsUpdate = true;
  terrain.geometry.computeVertexNormals();
  terrain.geometry.computeBoundingSphere();
  terrain.geometry.computeBoundingBox();
}

// ---------------------------------------------------------------- frustum del proyector
function buildFrustum() {
  if (frustum) {
    frustum.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); });
    scene.remove(frustum);
  }
  const corners = [[0, 0], [W, 0], [W, H], [0, H]].map(([x, y]) => new THREE.Vector3(...toWorld(x, y), 0));
  const segs = [];
  const tris = [];
  for (let i = 0; i < 4; i++) {
    segs.push(apex, corners[i], corners[i], corners[(i + 1) % 4]);
    tris.push(apex, corners[i], corners[(i + 1) % 4]);
  }
  const amber = 0xffb347;
  const lines = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(segs),
    new THREE.LineBasicMaterial({ color: amber, transparent: true, opacity: 0.75 }));
  const cone = new THREE.Mesh(new THREE.BufferGeometry().setFromPoints(tris),
    new THREE.MeshBasicMaterial({ color: amber, transparent: true, opacity: 0.045, side: THREE.DoubleSide,
      depthWrite: false, blending: THREE.AdditiveBlending }));
  const body = new THREE.Mesh(new THREE.BoxGeometry(120, 70, 80),
    new THREE.MeshStandardMaterial({ color: 0x2a2f37, emissive: amber, emissiveIntensity: 0.25 }));
  body.position.copy(apex).add(new THREE.Vector3(0, 0, 40));
  frustum = new THREE.Group();
  frustum.add(lines, cone, body);
  frustum.visible = $('#frustum').checked;
  scene.add(frustum);
}

// ---------------------------------------------------------------- objetos
const baseOpacity = (o) => (o.extra && o.extra.visible !== undefined && o.extra.visible < 0.5 ? 0.15 : 0.38);

function clearObjects() {
  for (const it of items.values()) {
    for (const m of [it.mesh, it.line]) {
      if (!m) continue;
      m.geometry.dispose();
      m.material.dispose();
    }
  }
  objGroup.clear();
  items.clear();
}

function buildObjects() {
  clearObjects();
  for (const o of data.objects) {
    const color = new THREE.Color(colorOf(o.class));
    const base = o.z_min * ui.exag;
    const top = Math.max(o.z_max * ui.exag, base + 3);
    const it = { obj: o, mesh: null, line: null, top };
    if (o.role === 'solid' && ui.mode === 'solid' && o.polygon.length >= 3) {
      // extrusión del polígono entre z_min y z_max (p10 y p90 de la altura dentro del objeto)
      const shape = new THREE.Shape(o.polygon.map(([x, y]) => new THREE.Vector2(...toWorld(x, y))));
      const g = new THREE.ExtrudeGeometry(shape, { depth: top - base, bevelEnabled: false });
      g.translate(0, 0, base);
      geometryOnRays(g);
      const m = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.25, roughness: 0.6,
        transparent: true, opacity: baseOpacity(o), depthWrite: false });
      it.mesh = new THREE.Mesh(g, m);
      it.mesh.userData.id = o.id;
      it.mesh.renderOrder = 2;
      objGroup.add(it.mesh);
    }
    if (ui.mode !== 'off') {
      // contorno arriba del objeto; los contenedores (teclado, torre, ddj) van punteados
      const g = new THREE.BufferGeometry().setFromPoints(o.polygon.map(([x, y]) => place(x, y, top + 0.5)));
      const dashed = o.role === 'outline';
      const m = dashed
        ? new THREE.LineDashedMaterial({ color, dashSize: 14, gapSize: 9 })
        : new THREE.LineBasicMaterial({ color });
      it.line = new THREE.LineLoop(g, m);
      if (dashed) it.line.computeLineDistances();
      it.line.userData.id = o.id;
      it.line.renderOrder = 3;
      objGroup.add(it.line);
    }
    items.set(o.id, it);
  }
  highlight();
}

// selección: el objeto (y sus hijos, si es un grupo como el teclado) en blanco y más opaco
function highlight() {
  for (const [id, it] of items) {
    const on = selectedId !== null && (id === selectedId || it.obj.group === selectedId);
    if (it.mesh) it.mesh.material.opacity = on ? 0.72 : baseOpacity(it.obj);
    if (it.line) it.line.material.color.set(on ? 0xffffff : colorOf(it.obj.class));
  }
  document.querySelectorAll('.obj').forEach((el) => el.classList.toggle('sel', el.dataset.id === selectedId));
  const chip = $('#chip');
  const it = items.get(selectedId);
  if (!it) {
    chip.hidden = true;
    return;
  }
  const o = it.obj;
  const kids = data.objects.filter((k) => k.group === o.id).length;
  const vis = o.extra && o.extra.visible !== undefined ? ` · visible ${Math.round(o.extra.visible * 100)} %` : '';
  chip.innerHTML = '';
  const b = document.createElement('b');
  b.textContent = o.name;
  b.style.color = colorOf(o.class);
  chip.append(b, ` · ${o.class}${kids ? ` · ${kids} hijos` : ''}${vis}`, document.createElement('br'));
  const m = document.createElement('span');
  m.className = 'mono muted';
  m.textContent = `z ${o.z} (${o.z_min} … ${o.z_max}) · bbox ${o.bbox.map(Math.round).join(', ')} px · ${o.source}`;
  chip.append(m);
  chip.hidden = false;
}

function select(id, focus) {
  selectedId = id;
  highlight();
  if (focus && id) focusOn(id);
}

// ---------------------------------------------------------------- panel de objetos
function objRow(o, count) {
  const b = document.createElement('button');
  b.className = 'obj' + (baseOpacity(o) < 0.3 ? ' dim' : '');
  b.dataset.id = o.id;
  const sw = document.createElement('span');
  sw.className = 'sw' + (o.role === 'outline' ? ' outline' : '');
  sw.style.background = colorOf(o.class);
  sw.style.color = colorOf(o.class);
  const name = document.createElement('span');
  name.className = 'name';
  name.textContent = o.name;
  if (count) {
    const c = document.createElement('span');
    c.className = 'count';
    c.textContent = `${count}`;
    name.append(' ', c);
  }
  const z = document.createElement('span');
  z.className = 'z mono';
  z.textContent = `z ${o.z}`;
  b.append(sw, name, z);
  b.addEventListener('click', (e) => {
    e.preventDefault();              // dentro de <summary>: enfoca sin abrir/cerrar el grupo
    select(o.id, true);
  });
  return b;
}

function buildList() {
  const ul = $('#objects');
  ul.textContent = '';
  const objs = data.objects;
  const ids = new Set(objs.map((o) => o.id));
  for (const o of objs.filter((x) => !x.group || !ids.has(x.group))) {
    const kids = objs.filter((k) => k.group === o.id);
    const li = document.createElement('li');
    if (kids.length) {
      const det = document.createElement('details');
      const sum = document.createElement('summary');
      const caret = document.createElement('span');
      caret.className = 'caret';
      caret.textContent = '▸';
      caret.title = 'Abrir o cerrar el grupo';
      sum.append(caret, objRow(o, kids.length));      // la flecha abre el grupo; la fila lo enfoca
      const sub = document.createElement('ul');
      for (const k of kids) {
        const kl = document.createElement('li');
        kl.append(objRow(k));
        sub.append(kl);
      }
      det.append(sum, sub);
      li.append(det);
    } else {
      li.className = 'leaf';
      li.append(objRow(o));
    }
    ul.append(li);
  }
  $('#nobj').textContent = `${objs.length}`;
  highlight();
}

// ---------------------------------------------------------------- cámara
let fly = null;
function flyTo(target, pos, fov = FOV, ms = 550) {
  fly = { t0: performance.now(), ms, fromT: controls.target.clone(), fromP: camera.position.clone(),
    toT: target.clone(), toP: pos.clone(), fromFov: camera.fov, toFov: fov };
}

function stepFly(now) {
  if (!fly) return;
  const k = Math.min(1, (now - fly.t0) / fly.ms);
  const e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
  controls.target.lerpVectors(fly.fromT, fly.toT, e);
  camera.position.lerpVectors(fly.fromP, fly.toP, e);
  camera.fov = fly.fromFov + (fly.toFov - fly.fromFov) * e;
  camera.updateProjectionMatrix();
  if (k >= 1) fly = null;
}

function focusOn(id) {
  const box = new THREE.Box3();
  for (const [k, it] of items) {
    if (k !== id && it.obj.group !== id) continue;
    if (it.mesh) box.expandByObject(it.mesh);
    if (it.line) box.expandByObject(it.line);
  }
  const o = items.get(id)?.obj;
  if (box.isEmpty() && o) for (const [x, y] of o.polygon) box.expandByPoint(place(x, y, o.z * ui.exag));
  if (box.isEmpty()) return;
  const c = box.getCenter(new THREE.Vector3());
  const r = Math.max(70, box.getSize(new THREE.Vector3()).length() / 2);
  const tanV = Math.tan(THREE.MathUtils.degToRad(FOV / 2));
  const half = Math.atan(tanV * Math.min(1, camera.aspect));       // el ángulo más angosto de la vista
  const dist = (r / Math.sin(half)) * 1.1;
  const dir = camera.position.clone().sub(controls.target).normalize();
  flyTo(c, c.clone().addScaledVector(dir, dist));
}

function viewAll(now = false) {
  // casi de frente (como el proyector), un poco desde abajo y a la derecha para que se lea el relieve
  const target = new THREE.Vector3(0, -H * 0.04, 0);
  const dir = new THREE.Vector3(0.28, -0.5, 1).normalize();
  const tanV = Math.tan(THREE.MathUtils.degToRad(FOV / 2));
  const dist = 1.3 * Math.max(H / 2 / tanV, W / 2 / (tanV * camera.aspect));   // que quepa a lo alto y a lo ancho
  const pos = target.clone().addScaledVector(dir, dist);
  if (!now) return flyTo(target, pos);
  controls.target.copy(target);
  camera.position.copy(pos);
}

// la vista del proyector: desde el apex, mirando al punto principal, con el campo que cubre la imagen
function viewProjector() {
  const halfH = Math.max(H / 2 - apex.y, apex.y + H / 2);
  const halfW = Math.max(W / 2 - apex.x, apex.x + W / 2) / camera.aspect;
  const fov = 2 * THREE.MathUtils.radToDeg(Math.atan(Math.max(halfH, halfW) / apex.z)) * 1.02;
  flyTo(new THREE.Vector3(apex.x, apex.y, 0), apex.clone().add(new THREE.Vector3(0, 0, -1)), fov);
}

// ---------------------------------------------------------------- carga de la escena
async function loadScene() {
  const r = await fetch('/api/scene3d', { cache: 'no-store' });
  if (!r.ok) throw new Error(`/api/scene3d respondió HTTP ${r.status}`);
  data = await r.json();
  [W, H] = data.size;
  const a = data.projector.apex;
  apex.set(...toWorld(a[0], a[1]), a[2]);

  const badge = $('#badge');
  badge.textContent = data.source === 'scan' ? 'escaneo' : 'demo';
  badge.className = `badge ${data.source}`;
  const hf = data.heightfield;
  const src = $('#source');
  src.textContent = `${data.scan_dir || 'escena demo (playground/scene.py)'} · ${hf.nx}×${hf.ny} · proyector ${data.projector.source}`;
  src.title = `${hf.units}\n${data.projector.note}`;

  buildTerrain(hf);
  buildFrustum();
  buildObjects();
  buildList();
  await loadSceneTexture();
}

// la demo se mueve (el cocodrilo da vueltas): sus objetos se refrescan solos
async function pollDemoObjects() {
  if (!data || data.source !== 'demo' || document.hidden) return;
  try {
    const r = await fetch('/api/scene3d?parts=objects', { cache: 'no-store' });
    if (!r.ok) return;
    const d = await r.json();
    const sameIds = d.objects.length === data.objects.length && d.objects.every((o, i) => o.id === data.objects[i].id);
    data.objects = d.objects;
    buildObjects();
    if (!sameIds) buildList();
  } catch {
    // runtime caído: el SSE lo muestra
  }
}

// ---------------------------------------------------------------- eventos (SSE) y runtime
function setSse(cls, text) {
  $('#sse').className = `dot ${cls}`;
  $('#sseText').textContent = text;
}

function addEvent(ev) {
  const list = $('#events');
  const { type = '?', t, ...rest } = ev;
  const li = document.createElement('li');
  const time = document.createElement('span');
  time.className = 't';
  time.textContent = new Date((t || Date.now() / 1000) * 1000).toLocaleTimeString('es-CL', { hour12: false });
  const ty = document.createElement('span');
  ty.className = 'ty' + (/error|kill/.test(type) ? ' bad' : /spawn|^reload$|request|bind/.test(type) ? ' good' : '');
  ty.textContent = type;
  const d = document.createElement('span');
  d.className = 'd';
  d.textContent = Object.entries(rest).map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' ');
  li.title = JSON.stringify(ev);
  li.append(time, ty, d);
  list.prepend(li);
  while (list.children.length > 40) list.lastChild.remove();
}

function connectEvents() {
  setSse('', 'conectando…');
  const es = new EventSource('/api/events');
  es.onopen = () => setSse('ok', 'conectado');
  es.onerror = () => setSse('retry', 'reconectando…');      // EventSource reintenta solo
  es.onmessage = (m) => {
    try { addEvent(JSON.parse(m.data)); } catch { /* evento no JSON: se ignora */ }
  };
}

async function pollRuntime() {
  try {
    const s = await (await fetch('/api/state', { cache: 'no-store' })).json();
    $('#rtfps').textContent = (s.stats?.fps ?? 0).toFixed(1);
    $('#rtfps').title = `${s.effects?.length ?? 0} efectos vivos · ${s.stats?.ticks ?? 0} ticks`;
  } catch {
    $('#rtfps').textContent = '×';
  }
}

// ---------------------------------------------------------------- interacción
const ray = new THREE.Raycaster();
ray.params.Line.threshold = 6;
const ndc = new THREE.Vector2();
let downAt = null;
let hoverEvt = null;

function setNdc(e) {
  const r = canvas.getBoundingClientRect();
  ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(ndc, camera);
}

canvas.addEventListener('pointerdown', (e) => { downAt = [e.clientX, e.clientY]; });
canvas.addEventListener('pointerup', (e) => {
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 5) return;   // fue un arrastre
  setNdc(e);
  const hit = ray.intersectObjects(objGroup.children, false)[0];
  select(hit ? hit.object.userData.id : null, false);
});
canvas.addEventListener('dblclick', () => { if (selectedId) focusOn(selectedId); });
canvas.addEventListener('pointermove', (e) => { hoverEvt = e; });
canvas.addEventListener('pointerleave', () => { hoverEvt = null; $('#cursor').hidden = true; });

// lectura bajo el cursor: px del proyector y altura relativa (sin la exageración)
function readCursor() {
  if (!hoverEvt || !terrain) return;
  setNdc(hoverEvt);
  hoverEvt = null;
  const h = ray.intersectObject(terrain, false)[0];
  const el = $('#cursor');
  if (!h || !h.uv) {
    el.hidden = true;
    return;
  }
  const z = ui.exag > 0 ? (h.point.z / ui.exag).toFixed(1) : '–';
  el.textContent = `px ${Math.round(h.uv.x * W - 0.5)}, ${Math.round(h.uv.y * H - 0.5)} · z ${z}`;
  el.hidden = false;
}

let exagDirty = false;
function bindRange(id, fmt, onChange) {
  const el = $(`#${id}`);
  const out = el.nextElementSibling;
  el.addEventListener('input', () => {
    ui[id] = Number(el.value);
    out.textContent = fmt(ui[id]);
    onChange();
  });
}
bindRange('overlay', (v) => v.toFixed(2), applyLights);
bindRange('room', (v) => v.toFixed(2), applyLights);
bindRange('exag', (v) => v.toFixed(1), () => { exagDirty = true; });

$('#live').addEventListener('change', (e) => { ui.live = e.target.checked; applyLights(); });
$('#frustum').addEventListener('change', (e) => { if (frustum) frustum.visible = e.target.checked; });
$('#wire').addEventListener('change', (e) => { terrainMat.wireframe = e.target.checked; });
document.querySelectorAll('.seg button').forEach((b) => b.addEventListener('click', () => {
  ui.mode = b.dataset.mode;
  document.querySelectorAll('.seg button').forEach((x) => x.classList.toggle('on', x === b));
  if (data) buildObjects();
}));
$('#viewAll').addEventListener('click', () => viewAll());
$('#viewProj').addEventListener('click', viewProjector);
$('#reload').addEventListener('click', () => start(false));

function resize() {
  const r = canvas.parentElement.getBoundingClientRect();
  renderer.setSize(Math.max(1, r.width), Math.max(1, r.height), false);
  camera.aspect = Math.max(1, r.width) / Math.max(1, r.height);
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(canvas.parentElement);
resize();

// ---------------------------------------------------------------- loop
let frames = 0;
let tStats = performance.now();
function animate(now) {
  requestAnimationFrame(animate);
  stepFly(now);
  if (exagDirty && data) {             // el slider de relieve se aplica una vez por cuadro
    exagDirty = false;
    updateTerrain();
    buildObjects();
  }
  controls.update();
  readCursor();
  renderer.render(scene, camera);
  frames++;
  if (now - tStats >= 500) {
    const s = (now - tStats) / 1000;
    $('#fps').textContent = (frames / s).toFixed(0);
    $('#hz').textContent = ui.live ? (framesIn / s).toFixed(1) : 'pausa';
    frames = 0;
    framesIn = 0;
    tStats = now;
  }
}

async function start(first) {
  const boot = $('#boot');
  try {
    await loadScene();
    window.__visorListo = true;
    boot.hidden = true;
    if (first) viewAll(true);
  } catch (err) {
    boot.hidden = false;
    boot.classList.add('err');
    boot.textContent = `No pude cargar la escena: ${err.message}. Reintento en 3 s…`;
    setTimeout(() => start(first), 3000);
  }
}

applyLights();
requestAnimationFrame(animate);
connectEvents();
pumpFrame();
pollRuntime();
setInterval(pollRuntime, 2000);
setInterval(pollDemoObjects, 500);
start(true);
