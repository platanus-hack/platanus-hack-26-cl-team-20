// Prueba del solver JS con datos sintéticos (sin casco):  node quest/pruebas/test_solver.mjs
// Escribe quest/pruebas/resultados/sintetico.json (un caso fijo para comparar con calib_solver.py).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as C from '../js/calib.js';

const aqui = path.dirname(fileURLToPath(import.meta.url));
const W = 1920, H = 1080;

function rng(seed) {
  let a = seed >>> 0;
  const u = () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  const g = () => Math.sqrt(-2 * Math.log(u() + 1e-12)) * Math.cos(2 * Math.PI * u());
  return { u, g };
}

// Verdad: proyector de bolsillo (throw 1.2 → fx = 2304 px), eje óptico bajo el centro (cy = 980, típico con offset).
const Kv = [[2304, 0, 960], [0, 2304, 980], [0, 0, 1]];
// Montado en la cabeza: 8 cm arriba, 5 cm adelante, mirando al frente e inclinado 10° hacia abajo.
// Marco cabeza WebXR: x derecha, y arriba, −z adelante. Ejes del proyector (OpenCV): x der, y abajo, z adelante.
function verdadCabeza() {
  const a = (10 * Math.PI) / 180;
  const xc = [1, 0, 0], yc = [0, -Math.cos(a), Math.sin(a)], zc = [0, -Math.sin(a), -Math.cos(a)];
  const Rrc = [[xc[0], yc[0], zc[0]], [xc[1], yc[1], zc[1]], [xc[2], yc[2], zc[2]]]; // columnas = ejes cam en rig
  const centro = [0.0, 0.08, -0.05];
  const R = C.T(Rrc); // rig → cam
  const t = C.mulv(R, centro).map((x) => -x);
  return { K: Kv, R, t };
}

/** Rayo desde la verdad por (u,v) hasta una profundidad z (m) en el marco del proyector → punto en el rig. */
function puntoEnRig(ver, uv, z) {
  const Kinv = C.inv3(ver.K);
  const d = C.mulv(Kinv, [uv[0], uv[1], 1]);
  const Xc = d.map((x) => (x / d[2]) * z);
  const Rt = C.T(ver.R);
  const c = C.mulv(Rt, ver.t).map((x) => -x);
  return C.mulv(Rt, Xc).map((x, k) => x + c[k]);
}

function caso({ n, ruidoCm, plano, seed, modelo, Kguess }) {
  const r = rng(seed);
  const ver = verdadCabeza();
  const uv = C.crucesPorDefecto(W, H, n);
  // superficies: profundidades variadas 1.0–3.5 m (mesa, muro, piso…) o un solo muro a 2.5 m
  const X = uv.map((p) => {
    const z = plano ? 2.5 : 1.0 + 2.5 * r.u();
    const Xr = puntoEnRig(ver, p, z);
    const s = (ruidoCm / 100) / Math.sqrt(3); // |e| rms = ruidoCm
    return Xr.map((x) => x + s * r.g());
  });
  if (plano) {
    // en un muro real todos los puntos están en un plano del rig, no a z constante del proyector: re-proyectar al plano z_rig = −2.5
    for (let i = 0; i < n; i++) {
      const Kinv = C.inv3(ver.K);
      const d = C.mulv(Kinv, [uv[i][0], uv[i][1], 1]);
      const Rt = C.T(ver.R);
      const dirR = C.mulv(Rt, d);
      const c = C.mulv(Rt, ver.t).map((x) => -x);
      const s = (-2.5 - c[2]) / dirR[2];
      const e = (ruidoCm / 100) / Math.sqrt(3);
      X[i] = c.map((x, k) => x + s * dirR[k] + e * r.g());
    }
  }
  const t0 = performance.now();
  const est = C.calibrar(uv, X, { w: W, h: H, modelo, Kguess });
  const ms = performance.now() - t0;
  const ev = C.errorContraVerdad(est, ver, { w: W, h: H, dist: 2.0 });
  const cEst = est.poseEnRig.slice(0, 3).map((row) => row[3]);
  const cVer = C.poseEnRig(ver.R, ver.t).slice(0, 3).map((row) => row[3]);
  return {
    uv, X, ver, est, ms,
    rms: est.rms, modelo2m: ev.rms, modelo2mMax: ev.max,
    dF: ((est.K[0][0] - Kv[0][0]) / Kv[0][0]) * 100,
    dCx: est.K[0][2] - Kv[0][2], dCy: est.K[1][2] - Kv[1][2],
    dRdeg: C.rotDiffDeg(est.R, ver.R),
    dTcm: Math.hypot(...cEst.map((x, k) => x - cVer[k])) * 100,
    metodo: est.metodo,
  };
}

const pct = (a, p) => { const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };
const f = (x, d = 2) => (Math.abs(x) < 1e-3 && x !== 0 ? x.toExponential(1) : x.toFixed(d));

const filas = [];
function bateria(nombre, cfg, trials = 200) {
  const rs = [];
  let fallos = 0;
  for (let k = 0; k < trials; k++) {
    try { rs.push(caso({ ...cfg, seed: 1000 + k })); } catch (e) { fallos++; }
  }
  const m = (key) => rs.map((r) => Math.abs(r[key]));
  const fila = {
    nombre, n: cfg.n, ruidoCm: cfg.ruidoCm, ok: rs.length, fallos,
    rmsMed: pct(m('rms'), 0.5), modeloMed: pct(m('modelo2m'), 0.5), modeloP90: pct(m('modelo2m'), 0.9),
    dFmed: pct(m('dF'), 0.5), dCyMed: pct(m('dCy'), 0.5), dRmed: pct(m('dRdeg'), 0.5), dTmed: pct(m('dTcm'), 0.5),
    msMed: pct(rs.map((r) => r.ms), 0.5), metodo: rs[0]?.metodo,
  };
  filas.push(fila);
  console.log(
    `${nombre.padEnd(34)} n=${String(cfg.n).padStart(2)} ruido=${cfg.ruidoCm}cm  ok=${fila.ok}/${trials}` +
    `  rms(med)=${f(fila.rmsMed)}px  modelo@2m med=${f(fila.modeloMed)}px p90=${f(fila.modeloP90)}px` +
    `  |Δf|=${f(fila.dFmed)}%  |Δcy|=${f(fila.dCyMed, 1)}px  ΔR=${f(fila.dRmed)}°  Δcentro=${f(fila.dTmed)}cm  ${f(fila.msMed, 1)}ms  [${fila.metodo}]`,
  );
}

console.log('— Solver JS (DLT normalizado + LM), verdad fx=fy=2304 cx=960 cy=980, 1920x1080 —');
for (const modelo of ['completo', 'fxfy']) {
  bateria(`sin ruido, ${modelo}`, { n: 8, ruidoCm: 0, modelo });
  for (const n of [8, 12, 20, 30]) bateria(`ruido 1 cm, ${modelo}`, { n, ruidoCm: 1, modelo });
}
bateria('ruido 0.5 cm, fxfy', { n: 12, ruidoCm: 0.5, modelo: 'fxfy' });
bateria('muro plano, K verdadera fija', { n: 12, ruidoCm: 1, plano: true, modelo: 'auto', Kguess: Kv });
const Kmal = [[2304 * 1.03, 0, 960], [0, 2304 * 1.03, 980], [0, 0, 1]];
bateria('muro plano, K con 3% de error', { n: 12, ruidoCm: 1, plano: true, modelo: 'auto', Kguess: Kmal });
bateria('K fija, puntos 3D', { n: 12, ruidoCm: 1, modelo: 'kfija', Kguess: Kv });

// Pivote de la punta del control
{
  const r = rng(7);
  const punta = [0.012, -0.018, -0.065], piv = [0.3, 0.9, -0.6];
  const poses = [];
  for (let i = 0; i < 30; i++) {
    const R = C.rodrigues([0.6 * r.g(), 0.6 * r.g(), 0.6 * r.g()]);
    const p = piv.map((x, k) => x - C.mulv(R, punta)[k] + 0.001 * r.g());
    poses.push({ R, p });
  }
  const pv = C.pivote(poses);
  const e = Math.hypot(...pv.punta.map((x, k) => x - punta[k])) * 1000;
  console.log(`pivote: punta=${pv.punta.map((x) => (x * 1000).toFixed(1)).join(', ')} mm (verdad 12, -18, -65)  error=${e.toFixed(2)} mm  rms=${(pv.rms * 1000).toFixed(2)} mm  giro=${pv.giroMaxDeg.toFixed(0)}°`);
  filas.push({ nombre: 'pivote', errorMm: e, rmsMm: pv.rms * 1000 });
}

// Caso fijo para comparar con Python/OpenCV
const fijo = caso({ n: 12, ruidoCm: 1, seed: 42, modelo: 'completo' });
const fijoFxfy = C.calibrar(fijo.uv, fijo.X, { w: W, h: H, modelo: 'fxfy' });
const out = {
  descripcion: 'Caso sintético fijo (seed 42, 12 cruces, ruido 1 cm rms, verdad cabeza). X en el marco del rig (cabeza), m.',
  w: W, h: H, uv: fijo.uv, X: fijo.X,
  verdad: { K: fijo.ver.K, R: fijo.ver.R, t: fijo.ver.t },
  js: { completo: { K: fijo.est.K, R: fijo.est.R, t: fijo.est.t, rms: fijo.est.rms }, fxfy: { K: fijoFxfy.K, R: fijoFxfy.R, t: fijoFxfy.t, rms: fijoFxfy.rms } },
  js_dlt: (() => { const d = C.descomponerP(C.dlt(fijo.uv, fijo.X)); return { K: d.K, R: d.R, t: d.t }; })(),
  resumen: filas,
};
fs.mkdirSync(path.join(aqui, 'resultados'), { recursive: true });
fs.writeFileSync(path.join(aqui, 'resultados', 'sintetico.json'), JSON.stringify(out, null, 1));
console.log('→ pruebas/resultados/sintetico.json');
