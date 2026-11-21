// quest.html: WebXR immersive-ar en el Meta Quest 3 → vista del proyector → WebRTC → proyector.html.
// ?sim=1 corre lo mismo en Chrome del Mac con el escaneo de la sala (sin casco).
import * as THREE from 'three';
import * as C from './calib.js';
import { ProyectorCam, PredictorPose, arr4 } from './proyector-cam.js';
import { Sala, EFECTOS } from './sala.js';
import { Overlay } from './overlay.js';
import { PanelXR } from './panel.js';
import { fuenteVideo, Senal, Emisor, urlSenalPorDefecto, emisorPeerJS } from './transporte.js';
import { log, warn, error, capturarErrores, capacidades, onLog } from './log.js';

// ───────────────────────── configuración (URL) ─────────────────────────
const Q = new URLSearchParams(location.search);
const num = (k, d) => (Q.has(k) && Q.get(k) !== '' && Number.isFinite(Number(Q.get(k))) ? Number(Q.get(k)) : d);
const CFG = {
  sim: Q.has('sim') && Q.get('sim') !== '0',
  auto: Q.get('auto') === '1',
  latenciaMs: num('latencia-ms', num('latencia', 0)),   // predicción de pose: N ms al futuro
  fps: num('fps', 30),
  res: Q.get('res') === '1080' ? [1920, 1080] : Q.get('res') === '540' ? [960, 540] : [1280, 720],
  w: num('w', 1920), h: num('h', 1080),                  // resolución nativa del proyector (px de calibración)
  senal: Q.get('senal') || 'ws',
  sala: Q.get('sala') || 'sala',
  puntos: Math.max(6, num('puntos', 12)),
  modo: Q.get('modo') === 'fijo' ? 'fijo' : 'cabeza',
  efecto: EFECTOS.includes(Q.get('efecto')) ? Q.get('efecto') : 'wireframe',
  kbps: num('kbps', 12000),
  hint: Q.get('hint') || 'motion',
  fuente: Q.get('fuente') || 'auto',
  ref: Q.get('ref') || 'local-floor',
  throw: num('throw', 1.2),
  ruidoCm: num('ruido', 1),
  celda: num('celda', 0.08),
  modelo: Q.get('modelo') || 'auto',
  stun: Q.get('stun') === '1',
  scan: Q.get('scan') || '/scan/sala.glb',
  doble: Q.get('doble') === '1',
  cast: Q.get('cast') === '1',          // respaldo: vista proyector fija a la cabeza en el ojo izq. (para cast nativo)
  castAncho: num('castAncho', 1.2),      // m, a 1 m de los ojos
};
window.__origenLog = CFG.sim ? 'sim' : 'quest';
capturarErrores();
const [RW, RH] = CFG.res;
const LS = 'quest-proyector:';
const guardar = (k, v) => { try { localStorage.setItem(LS + k, JSON.stringify(v)); } catch { /* privado */ } };
const leer = (k) => { try { return JSON.parse(localStorage.getItem(LS + k)); } catch { return null; } };

// ───────────────────────── render ─────────────────────────
const canvasProy = document.getElementById('proy');
canvasProy.width = RW; canvasProy.height = RH;
const rProy = new THREE.WebGLRenderer({ canvas: canvasProy, antialias: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
rProy.setPixelRatio(1); rProy.setSize(RW, RH, false); rProy.setClearColor(0x000000, 1);

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: !CFG.sim });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.xr.enabled = !CFG.sim;
document.getElementById('vista').appendChild(renderer.domElement);
window.addEventListener('resize', () => renderer.xr.isPresenting || renderer.setSize(window.innerWidth, window.innerHeight));

const escena = new THREE.Scene();
const escenaProy = new THREE.Scene(); escenaProy.background = new THREE.Color(0x000000);
const camCasco = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.02, 60);

const sala = new Sala({ escenaCasco: escena, escenaProy, log });
const proyCam = new ProyectorCam({ w: CFG.w, h: CFG.h, modo: CFG.modo, throwRatio: CFG.throw });
const overlay = new Overlay(CFG.w, CFG.h);
const predictor = new PredictorPose();

const rayo = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 0, -1)]),
  new THREE.LineBasicMaterial({ color: 0xffd166, transparent: true, opacity: 0.8 }));
rayo.visible = false; escena.add(rayo);
const puntaMesh = new THREE.Mesh(new THREE.SphereGeometry(0.005, 16, 12), new THREE.MeshBasicMaterial({ color: 0xff3050, depthTest: false }));
puntaMesh.renderOrder = 6; puntaMesh.visible = false; escena.add(puntaMesh);

// ── modo cast (?cast=1): sin WebRTC. La vista proyector va como cuadro fijo a la cabeza solo en el ojo izquierdo,
// que es lo que el Quest manda por cast nativo (Chromecast / app Meta Horizon). La calibración absorbe la escala/offset.
let quadCast = null, texCast = null;
if (CFG.cast) {
  texCast = new THREE.CanvasTexture(canvasProy);
  texCast.colorSpace = THREE.SRGBColorSpace; texCast.generateMipmaps = false; texCast.minFilter = THREE.LinearFilter;
  quadCast = new THREE.Mesh(new THREE.PlaneGeometry(CFG.castAncho, (CFG.castAncho * RH) / RW),
    new THREE.MeshBasicMaterial({ map: texCast, depthTest: false, depthWrite: false, transparent: true, opacity: 1 })); // transparent: se dibuja al final
  quadCast.layers.set(1); quadCast.renderOrder = 100; quadCast.matrixAutoUpdate = false; quadCast.visible = false;
  escena.add(quadCast);
}

// ───────────────────────── estado ─────────────────────────
const estado = {
  modo: CFG.modo, efecto: CFG.efecto, latenciaMs: CFG.latenciaMs, verCasco: true, patron: false,
  ajusteModo: 'off', mensaje: '', dobleToque: CFG.doble,
  calib: { activa: false, cruces: [], idx: 0, puntos: [], res: null },
  punta: leer('punta') || [0, 0, -0.02], // offset de la punta en el espacio del rayo del control (m)
  pivote: null,
  xr: { session: null, features: [], refTipo: '', hitSource: null, depth: '' },
};
sala.efecto = estado.efecto;
const AJUSTES = ['off', 'giro', 'rollzoom', 'mover', 'centro'];
const cabezaActual = new THREE.Matrix4();
const ojoActual = new THREE.Vector3();
let ultimoHit = null;
const muestrasPunta = []; // {t, Xw, Xcab, Xsala}

function mensaje(m, nivel = 'info') { estado.mensaje = m; log(m, null, nivel); refrescarUI(); }

// ───────────────────────── calibración ─────────────────────────
function cargarCalibLocal() {
  const j = leer(`calib-${estado.modo}`);
  proyCam.modo = estado.modo;
  if (j) { try { proyCam.fromJSON(j); log(`calibración ${estado.modo} cargada (rms ${j.info?.rms?.toFixed?.(1) ?? '?'} px)`); return true; } catch (e) { warn(`calib local inválida: ${e.message}`); } }
  proyCam.restablecer();
  return false;
}

function iniciarCalibracion() {
  const c = estado.calib;
  c.activa = true; c.cruces = C.crucesPorDefecto(CFG.w, CFG.h, CFG.puntos); c.idx = 0; c.puntos = []; c.res = null;
  sala.calibrando = true; estado.patron = false;
  dibujarOverlay();
  mensaje(`Calibrando (${estado.modo}): toca el centro de la cruz 1 con la punta del control y aprieta el gatillo`);
}

function siguientePendiente(desde) {
  const c = estado.calib, hechos = new Set(c.puntos.map((p) => p.i));
  for (let k = 1; k <= c.cruces.length; k++) { const i = (desde + k) % c.cruces.length; if (!hechos.has(i) && i > desde) return i; }
  return c.cruces.length; // fin
}

function agregarPunto(Xrig) {
  const c = estado.calib;
  if (!c.activa || c.idx >= c.cruces.length) return;
  c.puntos.push({ i: c.idx, uv: c.cruces[c.idx], X: Xrig.map((x) => +x.toFixed(5)) });
  // modo fijo con todo en un plano (p. ej. solo la mesa): sin profundidad no se puede sacar K → doble toque
  if (estado.modo === 'fijo' && !estado.dobleToque && c.puntos.length >= 4 && C.planitud(c.puntos.map((p) => p.X)).ratio < 0.03) {
    estado.dobleToque = true;
    warn('Todos los toques están en un plano: activé Doble toque (cada cruz también sobre una cartulina levantada)');
  }
  const mismos = c.puntos.filter((p) => p.i === c.idx).length;
  const repetir = estado.dobleToque && mismos < 2;
  if (!repetir) c.idx = siguientePendiente(c.idx);
  resolver();
  if (c.idx >= c.cruces.length) terminarCalibracion();
  else {
    dibujarOverlay();
    mensaje(`Punto ${c.puntos.length} ok${c.res ? ` · rms ${c.res.rms.toFixed(1)} px` : ''} · ` +
      (repetir ? `ahora la MISMA cruz ${c.idx + 1} sobre una cartulina 20–40 cm más cerca del proyector` : `ahora la cruz ${c.idx + 1}`));
  }
}

function resolver() {
  const c = estado.calib;
  if (c.puntos.length < 6) { c.res = null; return; }
  try {
    c.res = C.calibrar(c.puntos.map((p) => p.uv), c.puntos.map((p) => p.X), { w: CFG.w, h: CFG.h, Kguess: proyCam.base.K, modelo: CFG.modelo });
    if (![...c.res.K.flat(), ...c.res.R.flat(), ...c.res.t].every(Number.isFinite)) throw new Error('solución con NaN');
    if (!c.res.delante) warn('hay puntos detrás del proyector: revisa el modo (cabeza/fijo) o los toques');
  } catch (e) { c.res = null; warn(`no resuelve aún: ${e.message}`); }
}

function terminarCalibracion() {
  const c = estado.calib;
  c.activa = false; sala.calibrando = false;
  if (c.res) {
    proyCam.aplicarCalibracion(c.res, { modo: estado.modo, puntos: c.puntos.length });
    guardarCalib();
    mensaje(`Calibrado ${estado.modo}: ${c.puntos.length} puntos, rms ${c.res.rms.toFixed(1)} px (${c.res.metodo}). Verifica a ojo y afina con Ajuste.`);
    subirCalib();
  } else mensaje(`Calibración sin resultado (${c.puntos.length} puntos): hacen falta ≥ 6 en superficies a distinta profundidad`, 'warn');
  dibujarOverlay();
}

function saltarCruz() {
  const c = estado.calib;
  if (!c.activa) return;
  c.idx = siguientePendiente(c.idx);
  if (c.idx >= c.cruces.length) terminarCalibracion(); else { dibujarOverlay(); mensaje(`Saltada; ahora la cruz ${c.idx + 1}`); }
}

function rehacerUltimo() {
  const c = estado.calib;
  if (!c.puntos.length) return;
  if (!c.activa) { c.activa = true; sala.calibrando = true; }
  const p = c.puntos.pop();
  c.idx = p.i; resolver(); dibujarOverlay();
  mensaje(`Rehaciendo la cruz ${p.i + 1}`);
}

function borrarCalib() {
  try { localStorage.removeItem(LS + `calib-${estado.modo}`); } catch { /* nada */ }
  proyCam.restablecer(); estado.calib.puntos = []; estado.calib.res = null;
  mensaje(`Calibración ${estado.modo} borrada (valores por defecto)`);
}

function exportJSON() {
  const c = estado.calib, j = proyCam.toJSON();
  return {
    ...j, fecha: new Date().toISOString(), rms_px: j.info?.rms ?? null, metodo: j.info?.metodo ?? null,
    marco: estado.modo === 'cabeza' ? 'cabeza (viewer de WebXR)' : (sala.marcoSala()?.fuente || 'referencia de la sesión'),
    punta_m: estado.punta, latencia_ms: estado.latenciaMs,
    puntos: c.puntos.length ? c.puntos : (j.info?.puntosGuardados || []),
  };
}
function guardarCalib() {
  const j = proyCam.toJSON();
  j.info = { ...(j.info || {}), puntosGuardados: estado.calib.puntos };
  guardar(`calib-${estado.modo}`, j);
}
async function subirCalib() {
  try {
    const r = await fetch('/api/calib', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(exportJSON()) });
    if (r.ok) log(`calib.json guardada en el Mac (${(await r.json()).archivo})`);
  } catch { /* alojado sin server.py */ }
}
function exportarCalib() {
  guardarCalib(); subirCalib();
  const blob = new Blob([JSON.stringify(exportJSON(), null, 1)], { type: 'application/json' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'calib.json'; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  mensaje('Exportada calib.json (descarga + servidor si está)');
}
async function cargarCalib() {
  try {
    const r = await fetch('/api/calib', { cache: 'no-store' });
    if (r.ok) {
      const j = await r.json();
      if (j.modo && j.modo !== estado.modo) { estado.modo = j.modo; }
      proyCam.fromJSON(j); guardarCalib();
      if (j.punta_m) { estado.punta = j.punta_m; guardar('punta', estado.punta); }
      mensaje(`Cargada calib.json del Mac (${j.modo}, rms ${j.rms_px?.toFixed?.(1) ?? '?'} px)`);
      return;
    }
  } catch { /* sin servidor */ }
  mensaje(cargarCalibLocal() ? 'Cargada la calibración guardada en este navegador' : 'No hay calibración guardada', 'warn');
}
function importarArchivo(file) {
  file.text().then((t) => { const j = JSON.parse(t); proyCam.fromJSON(j); if (j.modo) estado.modo = j.modo; guardarCalib(); mensaje(`Importada ${file.name}`); })
    .catch((e) => mensaje(`No pude leer ${file.name}: ${e.message}`, 'error'));
}

function cambiarModo() {
  if (estado.calib.activa) return;
  estado.modo = estado.modo === 'cabeza' ? 'fijo' : 'cabeza';
  cargarCalibLocal();
  if (sim) sim.ponerVerdad(estado.modo);
  mensaje(`Modo ${estado.modo}${proyCam.calibrado ? '' : ' (sin calibrar)'}`);
}

/** Reproyección de los puntos tocados con la solución en curso (para dibujar el error en el proyector). */
function dibujarOverlay() {
  const c = estado.calib;
  if (c.activa) {
    const rep = c.res ? c.puntos.map((p) => C.project(c.res.K, c.res.R, c.res.t, p.X).slice(0, 2)) : null;
    overlay.calibracion({ cruces: c.cruces, idx: c.idx, puntos: c.puntos, reproy: rep });
  } else if (estado.patron) overlay.patron();
  else overlay.limpiar();
}

// ── toques ──
const tmpM = new THREE.Matrix4(), matOjo = new THREE.Matrix4();
function marcoSalaMatriz() { const m = sala.marcoSala(); return m ? m.matriz : new THREE.Matrix4(); }

/** Registra la punta (mundo) en el buffer con su posición en el marco de la cabeza y de la sala. */
function registrarPunta(t, Xw) {
  muestrasPunta.push({
    t, Xw: Xw.clone(),
    Xcab: Xw.clone().applyMatrix4(tmpM.copy(cabezaActual).invert()),
    Xsala: Xw.clone().applyMatrix4(tmpM.copy(marcoSalaMatriz()).invert()),
  });
  while (muestrasPunta.length && muestrasPunta[0].t < t - 600) muestrasPunta.shift();
}

/** Toque: promedio de la punta en los 300 ms previos al gatillo (el gatillo mueve el control). */
function capturarToque() {
  if (!muestrasPunta.length) { mensaje('No veo la punta del control', 'warn'); return; }
  const tf = muestrasPunta[muestrasPunta.length - 1].t;
  let sel = muestrasPunta.filter((s) => s.t >= tf - 340 && s.t <= tf - 40);
  if (sel.length < 3) sel = muestrasPunta.slice(-3);
  const k = estado.modo === 'cabeza' ? 'Xcab' : 'Xsala';
  const m = new THREE.Vector3(); sel.forEach((s) => m.add(s[k])); m.divideScalar(sel.length);
  const disp = Math.max(...sel.map((s) => s[k].distanceTo(m)));
  if (disp > 0.02) { mensaje(`Se movió ${(disp * 1000).toFixed(0)} mm durante el toque: quédate quieto y repite`, 'warn'); return; }
  if (disp > 0.008) warn(`toque algo movido (${(disp * 1000).toFixed(0)} mm)`);
  agregarPunto(m.toArray());
}

/** Toque desde un punto del mundo (simulador / pruebas). */
function capturarEnMundo(Xw) {
  const T = estado.modo === 'cabeza' ? cabezaActual : marcoSalaMatriz();
  agregarPunto(Xw.clone().applyMatrix4(tmpM.copy(T).invert()).toArray());
}

// ── pivote de la punta ──
function iniciarPivote() {
  if (CFG.sim) { mensaje('El pivote necesita el control real', 'warn'); return; }
  estado.pivote = { t0: performance.now() + 2000, poses: [] };
  mensaje('Punta: apoya la punta del control en un punto fijo (esquina de mesa) y gíralo en todas direcciones 4 s');
}
function procesarPivote(t, poseRayo) {
  const p = estado.pivote;
  if (!p || t < p.t0) return;
  if (t < p.t0 + 4000) {
    if (poseRayo && (p.poses.length === 0 || t - p.ultimo > 30)) {
      const e = poseRayo.elements;
      p.poses.push({ R: [0, 1, 2].map((r) => [0, 1, 2].map((c) => e[c * 4 + r])), p: [e[12], e[13], e[14]] });
      p.ultimo = t;
    }
    return;
  }
  estado.pivote = null;
  try {
    const r = C.pivote(p.poses);
    if (r.rms < 0.006 && r.giroMaxDeg > 25) {
      estado.punta = r.punta; guardar('punta', r.punta);
      mensaje(`Punta calibrada: (${r.punta.map((x) => (x * 1000).toFixed(0)).join(', ')}) mm, rms ${(r.rms * 1000).toFixed(1)} mm, giro ${r.giroMaxDeg.toFixed(0)}°`);
    } else mensaje(`Pivote malo (rms ${(r.rms * 1000).toFixed(1)} mm, giro ${r.giroMaxDeg.toFixed(0)}°): repite girando más y sin deslizar la punta`, 'warn');
  } catch (e) { mensaje(`Pivote falló: ${e.message}`, 'warn'); }
}

// ── ajuste manual (stick izquierdo / flechas en el sim) ──
function aplicarAjuste(x, y, dt, fino) {
  if (estado.ajusteModo === 'off' || (Math.abs(x) < 0.15 && Math.abs(y) < 0.15)) return;
  const s = dt * (fino ? 0.2 : 1);
  const m = estado.ajusteModo;
  if (m === 'giro') { proyCam.ajustar('yaw', x * 2 * s); proyCam.ajustar('pitch', -y * 2 * s); }
  else if (m === 'rollzoom') { proyCam.ajustar('roll', x * 2 * s); proyCam.ajustar('zoom', y * 0.03 * s); }
  else if (m === 'mover') { proyCam.ajustar('dx', x * 0.03 * s); proyCam.ajustar('dy', -y * 0.03 * s); }
  else if (m === 'centro') { proyCam.ajustar('dcx', x * 60 * s); proyCam.ajustar('dcy', -y * 60 * s); }
  ajusteSucio = true;
}
let ajusteSucio = false;
setInterval(() => { if (ajusteSucio) { ajusteSucio = false; guardarCalib(); } }, 2000);

function siguienteEfecto() { estado.efecto = EFECTOS[(EFECTOS.indexOf(estado.efecto) + 1) % EFECTOS.length]; sala.efecto = estado.efecto; refrescarUI(); }
function ponerPozo() {
  if (!ultimoHit) { mensaje('Apunta a una superficie (mesa) para poner el pozo', 'warn'); return; }
  sala.colocarPozo(ultimoHit, ojoActual);
  estado.efecto = 'pozo'; sala.efecto = 'pozo'; refrescarUI();
}

// ───────────────────────── botones (panel XR + HTML) ─────────────────────────
const botones = [
  { id: 'calibrar', texto: () => (estado.calib.activa ? 'Terminar calib' : 'Calibrar'), activo: () => estado.calib.activa, accion: () => (estado.calib.activa ? terminarCalibracion() : iniciarCalibracion()) },
  { id: 'saltar', texto: () => 'Saltar cruz', accion: saltarCruz },
  { id: 'rehacer', texto: () => 'Rehacer último', accion: rehacerUltimo },
  { id: 'borrar', texto: () => 'Borrar calib', accion: borrarCalib },
  { id: 'modo', texto: () => `Modo: ${estado.modo}`, accion: cambiarModo },
  { id: 'cargar', texto: () => 'Cargar calib', accion: cargarCalib },
  { id: 'exportar', texto: () => 'Exportar', accion: exportarCalib },
  { id: 'punta', texto: () => (estado.pivote ? 'Girando…' : 'Punta (pivote)'), activo: () => !!estado.pivote, accion: iniciarPivote },
  { id: 'efecto', texto: () => `Efecto: ${estado.efecto}`, accion: siguienteEfecto },
  { id: 'pozo', texto: () => 'Pozo aquí', accion: ponerPozo },
  { id: 'patron', texto: () => 'Patrón', activo: () => estado.patron, accion: () => { estado.patron = !estado.patron; dibujarOverlay(); refrescarUI(); } },
  { id: 'ajuste', texto: () => `Ajuste: ${estado.ajusteModo}`, activo: () => estado.ajusteModo !== 'off', accion: () => { estado.ajusteModo = AJUSTES[(AJUSTES.indexOf(estado.ajusteModo) + 1) % AJUSTES.length]; refrescarUI(); } },
  { id: 'lat-', texto: () => 'Latencia −10', accion: () => { estado.latenciaMs = Math.max(0, estado.latenciaMs - 10); guardar('latencia', estado.latenciaMs); refrescarUI(); } },
  { id: 'lat+', texto: () => 'Latencia +10', accion: () => { estado.latenciaMs += 10; guardar('latencia', estado.latenciaMs); refrescarUI(); } },
  { id: 'malla', texto: () => 'Malla casco', activo: () => estado.verCasco, accion: () => { estado.verCasco = !estado.verCasco; sala.verCasco = estado.verCasco; refrescarUI(); } },
  { id: 'extra', texto: () => (CFG.sim ? 'Calib sintética' : 'Space Setup'), accion: () => (CFG.sim ? calibracionSintetica() : spaceSetup()) },
  { id: 'doble', texto: () => 'Doble toque', activo: () => estado.dobleToque, accion: () => { estado.dobleToque = !estado.dobleToque; refrescarUI(); } },
];
if (!Q.has('latencia-ms') && !Q.has('latencia') && leer('latencia') != null) estado.latenciaMs = leer('latencia');

let statsTxt = '—';
function lineasEstado() {
  const c = estado.calib, st = sala.stats(), info = proyCam.info;
  const L = [estado.mensaje || '—'];
  if (c.activa) L.push(`Cruz ${Math.min(c.idx + 1, c.cruces.length)}/${c.cruces.length} · tocados ${c.puntos.length} · ${c.res ? `rms ${c.res.rms.toFixed(1)} px (${c.res.metodo})` : `faltan ${Math.max(0, 6 - c.puntos.length)} para resolver`}`);
  else L.push(`Calib ${estado.modo}: ${proyCam.calibrado ? `rms ${info?.rms?.toFixed?.(1) ?? '?'} px · ${info?.n ?? '?'} pts · ${info?.metodo ?? ''}` : 'sin calibrar (valores por defecto)'}`);
  L.push(`Sala: ${st.mallas} mallas (${st.tris} tri) · ${st.planos} planos · ${st.mesas} mesas · marco ${sala.marcoSala()?.fuente || '—'}`);
  if (!CFG.sim) L.push(`XR: ${estado.xr.session ? `${estado.xr.refTipo} · ${estado.xr.features.join(' ')}` : 'fuera de la sesión'}${estado.xr.depth ? ` · depth ${estado.xr.depth}` : ''}`);
  L.push(`Video: ${fuente.tipo} ${RW}x${RH}@${CFG.fps} → ${statsTxt}`);
  const a = proyCam.ajuste;
  L.push(`Latencia pred ${estado.latenciaMs} ms · punta (${estado.punta.map((x) => (x * 1000).toFixed(0)).join(',')}) mm · ajuste y${a.yaw.toFixed(1)} p${a.pitch.toFixed(1)} r${a.roll.toFixed(1)} z${a.zoom.toFixed(3)}`);
  return L;
}

const panel = new PanelXR(botones, lineasEstado);
panel.mesh.visible = false; escena.add(panel.mesh);

// HTML (página 2D y simulador)
const divBotones = document.getElementById('botones');
const divEstado = document.getElementById('estado');
const htmlBotones = botones.map((b) => {
  const el = document.createElement('button');
  el.addEventListener('click', () => { b.accion(); refrescarUI(); });
  divBotones.appendChild(el);
  return [b, el];
});
function refrescarUI() {
  htmlBotones.forEach(([b, el]) => { el.textContent = b.texto(); el.classList.toggle('activo', !!(b.activo && b.activo())); });
  divEstado.textContent = lineasEstado().join('\n');
  if (estado.xr.session) panel.dibujar();
}
setInterval(refrescarUI, 400);
onLog((m) => { if (!estado.mensaje) refrescarUI(); });
document.getElementById('importar')?.addEventListener('change', (e) => e.target.files[0] && importarArchivo(e.target.files[0]));

// ───────────────────────── transporte ─────────────────────────
const fuente = fuenteVideo(canvasProy, { fps: CFG.fps, preferencia: CFG.fuente, hint: CFG.hint });
let emisor = null;
const iceServers = CFG.stun ? [{ urls: 'stun:stun.l.google.com:19302' }] : [];
if (CFG.senal === 'peerjs') {
  emisorPeerJS({ sala: CFG.sala, fuente, bitrate: CFG.kbps * 1000, fps: CFG.fps, log }).then((e) => { emisor = e; });
} else if (CFG.senal !== 'no') {
  const senal = new Senal({ url: Q.get('ws') || urlSenalPorDefecto(), rol: 'quest', sala: CFG.sala });
  senal.on('estado', (s) => log(`señalización ${s}`));
  emisor = new Emisor({ senal, fuente, bitrate: CFG.kbps * 1000, fps: CFG.fps, iceServers, log });
}
setInterval(async () => {
  if (!emisor) { statsTxt = 'sin señalización'; return; }
  if (!emisor.stats) { statsTxt = emisor.conectados ? 'proyector conectado (peerjs)' : 'esperando proyector (peerjs)'; return; }
  const s = await emisor.stats();
  window.__statsEmisor = s;
  statsTxt = s.length ? s.map((x) => `${x.estado}${x.fps ? ` ${x.fps}fps` : ''}${x.kbps ? ` ${x.kbps}kbps` : ''}${x.codec ? ` ${x.codec.replace('video/', '')}` : ''}${x.rttMs != null ? ` rtt ${x.rttMs}ms` : ''}${x.limit && x.limit !== 'none' ? ` lim:${x.limit}` : ''}`).join(' | ') : 'esperando proyector';
}, 1000);

// ───────────────────────── render del proyector ─────────────────────────
let ultimoPush = -1e9;
const tmpRig = new THREE.Matrix4();
function rigMundo(predecir) {
  if (estado.modo === 'cabeza') return predecir ? predictor.predecir(estado.latenciaMs, tmpRig) : tmpRig.copy(cabezaActual);
  return tmpRig.copy(marcoSalaMatriz());
}
function renderProy(t) {
  proyCam.actualizar(rigMundo(true));
  sala.actualizarProy({ ojo: ojoActual, hit: ultimoHit, renderW: RW, renderH: RH });
  rProy.render(escenaProy, proyCam.camara);
  overlay.render(rProy, RW, RH);
}
function quizasEnviar(t) {
  if (t - ultimoPush < 1000 / CFG.fps - 2) return false;
  ultimoPush = t;
  fuente.empujar(t);
  return true;
}

// ───────────────────────── WebXR ─────────────────────────
const btnAR = document.getElementById('entrar');
async function entrarAR() {
  if (!navigator.xr) { mensaje('Este navegador no tiene WebXR', 'error'); return; }
  const base = ['local-floor', 'hit-test', 'plane-detection', 'mesh-detection', 'anchors', 'hand-tracking'];
  if (CFG.ref === 'unbounded') base.push('unbounded');
  const intentos = [
    { optionalFeatures: [...base, 'depth-sensing'], depthSensing: { usagePreference: ['gpu-optimized'], dataFormatPreference: ['luminance-alpha', 'float32'] } },
    { optionalFeatures: base },
    { optionalFeatures: ['local-floor'] },
  ];
  let session = null;
  for (const init of intentos) {
    try { session = await navigator.xr.requestSession('immersive-ar', init); break; } catch (e) { warn(`requestSession con [${init.optionalFeatures.join(',')}] falló: ${e.message}`); }
  }
  if (!session) { mensaje('No pude abrir la sesión immersive-ar', 'error'); return; }
  const feats = session.enabledFeatures ? [...session.enabledFeatures] : ['(enabledFeatures no disponible)'];
  estado.xr.features = feats;
  estado.xr.refTipo = CFG.ref === 'unbounded' && feats.includes('unbounded') ? 'unbounded' : feats.includes('local-floor') || !session.enabledFeatures ? 'local-floor' : 'local';
  try { estado.xr.depth = session.depthUsage ? `${session.depthUsage}/${session.depthDataFormat}` : ''; } catch { estado.xr.depth = ''; }
  // three r170 solo crea XRWebGLBinding con WebXR Layers; si depth-sensing viene concedido sin layers, su bucle se cae
  if (session.renderState.layers === undefined && feats.includes('depth-sensing')) {
    Object.defineProperty(session, 'enabledFeatures', { value: Object.freeze(feats.filter((f) => f !== 'depth-sensing')), configurable: true });
    warn('depth-sensing concedido pero sin WebXR Layers: lo ignoro (three r170 no tiene binding en ese camino)');
    estado.xr.depth += ' (ignorado)';
  }
  renderer.xr.setReferenceSpaceType(estado.xr.refTipo);
  renderer.setClearColor(0x000000, 0);
  await renderer.xr.setSession(session);
  estado.xr.session = session;
  log('sesión AR abierta', { features: feats, ref: estado.xr.refTipo, depth: estado.xr.depth, faltan: ['mesh-detection', 'plane-detection', 'hit-test', 'depth-sensing'].filter((f) => session.enabledFeatures && !feats.includes(f)) });
  session.addEventListener('end', () => {
    estado.xr.session = null; estado.xr.hitSource = null; panel.mesh.visible = false; rayo.visible = false; puntaMesh.visible = false;
    renderer.setClearColor(0x000000, 1); log('sesión AR cerrada');
  });
  session.addEventListener('inputsourceschange', () => pedirHitTest(session));
  // manos (sin gamepad): el pellizco llega como 'select'
  session.addEventListener('select', (e) => { if (!e.inputSource.gamepad && e.inputSource.handedness !== 'left') clickMano = true; });
  pedirHitTest(session);
  panelPendiente = true;
  setTimeout(() => {
    const st = sala.stats();
    if (!st.mallas && !st.planos) mensaje('Sin malla ni planos: haz Space Setup (botón) o revisa el permiso de datos espaciales', 'warn');
  }, 6000);
}
let panelPendiente = false, clickMano = false;
async function pedirHitTest(session) {
  if (!session.requestHitTestSource || (session.enabledFeatures && !session.enabledFeatures.includes('hit-test'))) return;
  const der = [...session.inputSources].find((s) => s.handedness === 'right') || session.inputSources[0];
  if (!der || der === estado.xr.hitFuente) return;
  try {
    estado.xr.hitSource?.cancel?.();
    estado.xr.hitSource = await session.requestHitTestSource({ space: der.targetRaySpace });
    estado.xr.hitFuente = der;
  } catch (e) { warn(`hit-test: ${e.message}`); }
}
async function spaceSetup() {
  const s = estado.xr.session;
  if (s?.initiateRoomCapture) { try { await s.initiateRoomCapture(); mensaje('Space Setup lanzado'); } catch (e) { mensaje(`Space Setup: ${e.message}`, 'warn'); } }
  else mensaje('Space Setup: hazlo desde Ajustes del casco › Entorno físico › Configuración del espacio', 'warn');
}
btnAR.addEventListener('click', entrarAR);
if (CFG.sim) btnAR.style.display = 'none';
else navigator.xr?.isSessionSupported('immersive-ar').then((ok) => { btnAR.disabled = !ok; if (!ok) btnAR.textContent = 'immersive-ar no disponible'; });

function colocarPanelFrente() {
  const p = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
  cabezaActual.decompose(p, q, s);
  const f = new THREE.Vector3(0, 0, -1).applyQuaternion(q); f.y = 0; f.normalize();
  panel.mesh.position.copy(p).addScaledVector(f, 0.55); panel.mesh.position.y -= 0.22;
  panel.mesh.lookAt(p.x, p.y - 0.1, p.z);
  panel.mesh.visible = true; panel.dibujar();
}

const prevBotones = { left: [], right: [] };
const flanco = (lado, gp, i) => { const v = !!gp?.buttons[i]?.pressed; const a = prevBotones[lado][i]; prevBotones[lado][i] = v; return v && !a; };
const raycasterPanel = new THREE.Raycaster();
let tAnterior = 0;

function cuadroXR(t, frame) {
  const ref = renderer.xr.getReferenceSpace();
  const vp = frame.getViewerPose(ref);
  const dt = Math.min(0.1, (t - tAnterior) / 1000 || 0); tAnterior = t;
  if (vp) {
    cabezaActual.fromArray(vp.transform.matrix);
    const p = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
    cabezaActual.decompose(p, q, s);
    predictor.push(t, p, q);
    ojoActual.setFromMatrixPosition(predictor.predecir(estado.latenciaMs, matOjo));
  }
  if (panelPendiente && vp) { panelPendiente = false; colocarPanelFrente(); }
  try { sala.actualizarXR(frame, ref); } catch (e) { error(`sala: ${e.message}`); }

  let der = null, izq = null;
  for (const src of frame.session.inputSources) {
    const pose = frame.getPose(src.targetRaySpace, ref);
    if (!pose) continue;
    const o = { src, gp: src.gamepad, m: new THREE.Matrix4().fromArray(pose.transform.matrix) };
    if (src.handedness === 'left') izq = o; else if (!der || src.handedness === 'right') der = o;
  }

  // ── control derecho: rayo, panel, punta, gatillo
  let sobrePanel = null;
  if (der) {
    const o = new THREE.Vector3().setFromMatrixPosition(der.m);
    const d = new THREE.Vector3(0, 0, -1).transformDirection(der.m);
    let largo = 3;
    if (panel.mesh.visible) {
      raycasterPanel.set(o, d);
      const h = raycasterPanel.intersectObject(panel.mesh, false)[0];
      if (h) { sobrePanel = h.uv; largo = h.distance; }
      panel.hover(sobrePanel);
    }
    let hit = null;
    if (estado.xr.hitSource) {
      const r = frame.getHitTestResults(estado.xr.hitSource)[0];
      const hp = r?.getPose(ref);
      if (hp) {
        const hm = new THREE.Matrix4().fromArray(hp.transform.matrix);
        hit = { punto: new THREE.Vector3().setFromMatrixPosition(hm), normal: new THREE.Vector3(0, 1, 0).transformDirection(hm), plano: null };
        const hp2 = sala.raycast(o, d, { mallas: false });
        if (hp2 && hp2.punto.distanceTo(hit.punto) < 0.05) hit.plano = hp2.plano;
      }
    }
    if (!hit) hit = sala.raycast(o, d);
    ultimoHit = sobrePanel ? null : hit;
    if (hit && !sobrePanel) largo = Math.min(largo, hit.punto.distanceTo(o));
    rayo.visible = true;
    rayo.matrixAutoUpdate = false; rayo.matrix.copy(der.m).multiply(new THREE.Matrix4().makeScale(1, 1, largo)); rayo.matrixWorldNeedsUpdate = true;
    const punta = new THREE.Vector3(...estado.punta).applyMatrix4(der.m);
    puntaMesh.visible = true; puntaMesh.position.copy(punta);
    registrarPunta(t, punta);
    procesarPivote(t, der.m);
    const gatillo = flanco('right', der.gp, 0) || clickMano;
    clickMano = false;
    if (gatillo) {
      if (sobrePanel) panel.click(sobrePanel);
      else if (estado.calib.activa) capturarToque();
      else if (estado.efecto === 'pozo') ponerPozo();
    }
    if (flanco('right', der.gp, 4)) { if (estado.calib.activa) capturarToque(); else ponerPozo(); }
    if (flanco('right', der.gp, 5)) { if (estado.calib.activa) rehacerUltimo(); else { panel.mesh.visible = !panel.mesh.visible; panel.dibujar(); } }
  } else { rayo.visible = false; puntaMesh.visible = false; ultimoHit = null; }

  // ── control izquierdo: ajuste manual y panel
  if (izq?.gp) {
    const ax = izq.gp.axes;
    aplicarAjuste(ax[2] || 0, ax[3] || 0, dt, !!izq.gp.buttons[1]?.pressed);
    if (flanco('left', izq.gp, 4)) { estado.ajusteModo = AJUSTES[(AJUSTES.indexOf(estado.ajusteModo) + 1) % AJUSTES.length]; mensaje(`Ajuste: ${estado.ajusteModo}`); }
    if (flanco('left', izq.gp, 5)) colocarPanelFrente();
  }

  if (performance.now() - panel.ultimoDibujo > 300 && panel.mesh.visible) panel.dibujar();
  if (quizasEnviar(t)) { renderProy(t); if (texCast) texCast.needsUpdate = true; }
  if (quadCast) {
    const cl = renderer.xr.getCamera().cameras[0];
    if (cl && !cl.layers.isEnabled(1)) cl.layers.enable(1); // capa 1 = solo ojo izquierdo
    quadCast.visible = true;
    quadCast.matrix.copy(cabezaActual).multiply(new THREE.Matrix4().makeTranslation(0, 0, -1));
    quadCast.matrixWorldNeedsUpdate = true;
  }
  renderer.render(escena, camCasco);
}

// ───────────────────────── página 2D (antes de entrar al casco) ─────────────────────────
function cuadro2D(t) {
  // sin XR: la vista proyector sigue saliendo (útil para el patrón y para ver que el video llega)
  if (quizasEnviar(t)) renderProy(t);
}

// ───────────────────────── simulador ─────────────────────────
let sim = null;
const clickTecla = new Set(['Space', 'Enter', 'Click']);
async function iniciarSim() {
  const { Simulador } = await import('./sim.js');
  sim = new Simulador({ renderer, escena, camCasco, canvasProy, w: CFG.w, h: CFG.h, modo: estado.modo, log });
  if (Q.get('cab')) { // ?cab=x,y,z,yaw,pitch (pose inicial de la cabeza simulada)
    const [x, y, z, yaw, pitch] = Q.get('cab').split(',').map(Number);
    sim.cabeza.pos.set(x, y, z); sim.cabeza.yaw = yaw || 0; sim.cabeza.pitch = pitch || 0;
  }
  document.body.classList.add('sim');
  mensaje('Cargando escaneo de la sala…');
  const { simp, planos } = await sim.cargar(CFG.scan, CFG.celda);
  sala.agregarMallaSim(simp.pos, simp.idx);
  planos.forEach((p, i) => sala.agregarPlanoSim(`sim-${i}`, p.poly, p.altura, p.etiqueta));
  sim.controles(renderer.domElement, teclaSim);
  autotestProyeccion();
  mensaje('Simulador listo · C calibrar · clic/espacio = tocar · T calibración sintética · 1-5 efectos · P pozo · M ajuste');
  window.__simListo = true;
  if (CFG.auto) setTimeout(() => calibracionSintetica(), 300);
}

function teclaSim(e) {
  const k = e.code;
  if (clickTecla.has(k)) {
    if (k !== 'Click') e.preventDefault?.();
    const { origen, dir } = sim.rayoMouse();
    const X = sim.golpeReal(origen, dir);
    if (estado.calib.activa) { if (X) capturarEnMundo(X); else mensaje('Ahí no hay superficie', 'warn'); }
    else if (estado.efecto === 'pozo' || k !== 'Click') ponerPozo();
    return;
  }
  const mapa = {
    KeyC: () => (estado.calib.activa ? terminarCalibracion() : iniciarCalibracion()), KeyN: saltarCruz, Backspace: rehacerUltimo,
    KeyT: () => calibracionSintetica(), KeyP: ponerPozo, KeyM: () => botones.find((b) => b.id === 'ajuste').accion(),
    KeyG: () => botones.find((b) => b.id === 'patron').accion(), KeyX: borrarCalib, KeyV: cambiarModo, KeyE: exportarCalib,
    BracketLeft: () => botones.find((b) => b.id === 'lat-').accion(), BracketRight: () => botones.find((b) => b.id === 'lat+').accion(),
    Digit1: () => setEf(0), Digit2: () => setEf(1), Digit3: () => setEf(2), Digit4: () => setEf(3), Digit5: () => setEf(4),
  };
  if (mapa[k]) { mapa[k](); refrescarUI(); }
}
function setEf(i) { estado.efecto = EFECTOS[i]; sala.efecto = estado.efecto; }

function cuadroSim(t) {
  const dt = Math.min(0.1, (t - tAnterior) / 1000 || 0); tAnterior = t;
  sim.mover(dt, estado.ajusteModo !== 'off');
  if (estado.ajusteModo !== 'off') {
    const k = sim.teclas, x = (k.has('ArrowRight') ? 1 : 0) - (k.has('ArrowLeft') ? 1 : 0), y = (k.has('ArrowUp') ? 1 : 0) - (k.has('ArrowDown') ? 1 : 0);
    aplicarAjuste(x, y, dt, k.has('ShiftLeft') || k.has('ShiftRight'));
  }
  sim.matrizCabeza(cabezaActual);
  const p = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
  cabezaActual.decompose(p, q, s);
  predictor.push(t, p, q);
  ojoActual.copy(p);
  camCasco.position.copy(p); camCasco.quaternion.copy(q); camCasco.updateMatrixWorld();
  const { origen, dir } = sim.rayoMouse();
  ultimoHit = sala.raycast(origen, dir);
  // el proyector de la app (estimado) renderiza; el "verdadero" del simulador lo proyecta sobre el escaneo
  renderProy(t);
  quizasEnviar(t);
  sim.render(estado.modo === 'cabeza' ? cabezaActual : new THREE.Matrix4(), window.innerWidth, window.innerHeight);
}

/** Toques sintéticos: la cruz sale del proyector verdadero, cae en el escaneo, y "Max" la toca con ruido. */
async function calibracionSintetica({ ruidoCm = CFG.ruidoCm, semilla = 7 } = {}) {
  if (!sim) return null;
  const rnd = rng(semilla);
  const cab0 = { pos: sim.cabeza.pos.clone(), yaw: sim.cabeza.yaw, pitch: sim.cabeza.pitch };
  iniciarCalibracion();
  const c = estado.calib;
  let guard = 0;
  while (c.activa && c.idx < c.cruces.length && guard++ < 200) {
    if (estado.modo === 'cabeza') {
      // la cabeza se mueve entre toques (la cruz va pegada a la cabeza)
      sim.cabeza.pos.copy(cab0.pos).add(new THREE.Vector3(rnd.g() * 0.03, rnd.g() * 0.02, rnd.g() * 0.03));
      sim.cabeza.yaw = cab0.yaw + rnd.g() * 3; sim.cabeza.pitch = cab0.pitch + rnd.g() * 3;
    }
    sim.matrizCabeza(cabezaActual);
    sim.verdad.actualizar(estado.modo === 'cabeza' ? cabezaActual : new THREE.Matrix4());
    const [u, v] = c.cruces[c.idx];
    const r = sim.verdad.rayo(u, v);
    let X = sim.golpeReal(r.origin, r.direction);
    if (!X) { saltarCruz(); continue; }
    // doble toque: si la cruz ya tiene un punto, el segundo es sobre una cartulina 20–40 cm más cerca del proyector
    if (c.puntos.some((p) => p.i === c.idx)) {
      const d = X.distanceTo(r.origin);
      X = r.origin.clone().addScaledVector(r.direction, Math.max(0.3, d - 0.2 - 0.2 * rnd.u()));
    }
    const s = (ruidoCm / 100) / Math.sqrt(3);
    X.add(new THREE.Vector3(rnd.g() * s, rnd.g() * s, rnd.g() * s));
    capturarEnMundo(X);
    await new Promise((ok) => setTimeout(ok, 60));
  }
  Object.assign(sim.cabeza, { pos: cab0.pos, yaw: cab0.yaw, pitch: cab0.pitch });
  if (c.activa) terminarCalibracion();
  if (!proyCam.calibrado) { window.__resultadoSim = { ok: false, puntos: c.puntos.length }; return window.__resultadoSim; }
  const est = { K: proyCam.K, ...proyCam.Rt };
  const ver = { K: sim.verdad.K, ...sim.verdad.Rt };
  const ev = C.errorContraVerdad(est, ver, { w: CFG.w, h: CFG.h, dist: 2.0 });
  const cE = C.poseEnRig(est.R, est.t).slice(0, 3).map((r) => r[3]), cV = C.poseEnRig(ver.R, ver.t).slice(0, 3).map((r) => r[3]);
  const res = {
    ok: true, modo: estado.modo, puntos: c.puntos.length, saltadas: c.cruces.length - c.puntos.length, ruidoCm,
    rmsPx: +proyCam.info.rms.toFixed(3), metodo: proyCam.info.metodo, dobleToque: estado.dobleToque,
    errorModelo2mPx: { rms: +ev.rms.toFixed(3), max: +ev.max.toFixed(3) },
    dFpct: +(((est.K[0][0] - ver.K[0][0]) / ver.K[0][0]) * 100).toFixed(3),
    dCxPx: +(est.K[0][2] - ver.K[0][2]).toFixed(2), dCyPx: +(est.K[1][2] - ver.K[1][2]).toFixed(2),
    dRdeg: +C.rotDiffDeg(est.R, ver.R).toFixed(4), dCentroCm: +(Math.hypot(...cE.map((x, i) => x - cV[i])) * 100).toFixed(3),
    Kest: est.K.map((r) => r.map((x) => +x.toFixed(2))), Kverdad: ver.K,
  };
  window.__resultadoSim = res;
  log('calibración sintética', res);
  return res;
}
function rng(seed) {
  let a = seed >>> 0;
  const u = () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  return { u, g: () => Math.sqrt(-2 * Math.log(u() + 1e-12)) * Math.cos(2 * Math.PI * u()) };
}

/** La matriz de proyección three.js y el modelo K[R|t] del solver tienen que dar el mismo píxel. */
function autotestProyeccion() {
  const cam = new ProyectorCam({ w: CFG.w, h: CFG.h, modo: 'fijo' });
  cam.base.K = [[2000, 0, 900.3], [0, 2010, 700.7], [0, 0, 1]]; cam.recalcular();
  const Twr = new THREE.Matrix4().makeRotationY(0.3).setPosition(0.2, 0.1, 0.3);
  cam.actualizar(Twr);
  let maxErr = 0;
  for (const X of [[0.1, 0.5, -2], [-0.8, 0.2, -3], [0.6, 1.4, -1.5]]) {
    const w = new THREE.Vector3(...X).applyMatrix4(Twr);
    const a = cam.proyectarMundo(w);
    const n = w.clone().applyMatrix4(cam.camara.matrixWorldInverse).applyMatrix4(cam.camara.projectionMatrix);
    const u = ((n.x + 1) / 2) * CFG.w - 0.5, v = ((1 - n.y) / 2) * CFG.h - 0.5;
    const { R, t } = cam.Rt;
    const b = C.project(cam.K, R, t, X); // X está en el marco del rig
    maxErr = Math.max(maxErr, Math.hypot(a.u - u, a.v - v), Math.hypot(b[0] - u, b[1] - v));
  }
  window.__autotestProyeccion = maxErr;
  log(`autotest proyección three.js vs K[R|t]: error máx ${maxErr.toExponential(2)} px`, null, maxErr < 0.01 ? 'info' : 'error');
}

// ───────────────────────── arranque ─────────────────────────
function bucle(t, frame) {
  try {
    if (frame) cuadroXR(t, frame);
    else if (sim && sim.escaneo) cuadroSim(t);
    else cuadro2D(t);
  } catch (e) { error(`bucle: ${e.message}`); }
}

cargarCalibLocal();
renderer.setAnimationLoop(bucle);
window.__app = {
  estado, proyCam, sala, calibracionSintetica, CFG, iniciarCalibracion, capturarEnMundo, fuente, get emisor() { return emisor; }, get sim() { return sim; },
  efecto(e) { estado.efecto = e; sala.efecto = e; refrescarUI(); },
  panel,
  /** Pruebas: punto del mundo en el centro de un botón del panel XR. */
  puntoBoton(id) {
    const i = botones.findIndex((b) => b.id === id);
    const bw = (panel.W - 24) / 4;
    const px = 12 + (i % 4) * bw + bw / 2, py = 12 + Math.floor(i / 4) * panel.bh + panel.bh / 2;
    const g = panel.mesh.geometry.parameters;
    panel.mesh.updateMatrixWorld();
    return new THREE.Vector3((px / panel.W - 0.5) * g.width, (0.5 - py / panel.H) * g.height, 0).applyMatrix4(panel.mesh.matrixWorld).toArray();
  },
  /** Pruebas: pone el pozo al centro de la mesa más grande. */
  pozoEnMesa() {
    if (ultimoHit?.plano?.etiqueta === 'table') { sala.colocarPozo(ultimoHit, ojoActual); this.efecto('pozo'); return ultimoHit.punto.toArray(); }
    const mesas = [...sala.planos.values()].filter((p) => p.etiqueta === 'table');
    if (!mesas.length) return null;
    const area = (p) => p.poly.reduce((s, [x, z], i) => { const [x2, z2] = p.poly[(i + 1) % p.poly.length]; return s + x * z2 - x2 * z; }, 0) / 2;
    const m = mesas.sort((a, b) => Math.abs(area(b)) - Math.abs(area(a)))[0];
    const c = m.poly.reduce((a, [x, z]) => [a[0] + x / m.poly.length, a[1] + z / m.poly.length], [0, 0]);
    const punto = new THREE.Vector3(c[0], 0, c[1]).applyMatrix4(m.matriz);
    sala.colocarPozo({ punto, normal: new THREE.Vector3(0, 1, 0).transformDirection(m.matriz), plano: m }, ojoActual);
    this.efecto('pozo');
    return punto.toArray();
  },
};
refrescarUI();
capacidades().then((c) => { window.__capacidades = c; log('capacidades', c); });
if (CFG.sim) iniciarSim().catch((e) => mensaje(`simulador: ${e.message}`, 'error'));
