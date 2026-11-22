// Calibración del proyector por correspondencias 2D↔3D (toques con el control).
// Módulo puro (sin three.js): corre en el casco, en Chrome y en node (pruebas/test_solver.mjs).
//
// Convención OpenCV:  x_cam = R·X + t ;  [u v 1]ᵀ ~ K·x_cam ;  u → derecha, v → abajo, z → adelante.
// X está en el marco del "rig": la cabeza (modo cabeza) o la sala (modo fijo).
// Matrices row-major como arrays de arrays (3x3, 3x4) y vectores como arrays.

// ───────────────────────── álgebra chica ─────────────────────────

export function mul(A, B) {
  const n = A.length, m = B[0].length, k = B.length;
  const C = Array.from({ length: n }, () => new Array(m).fill(0));
  for (let i = 0; i < n; i++) for (let j = 0; j < m; j++) {
    let s = 0;
    for (let q = 0; q < k; q++) s += A[i][q] * B[q][j];
    C[i][j] = s;
  }
  return C;
}
export const T = (A) => A[0].map((_, j) => A.map((r) => r[j]));
export const mulv = (A, v) => A.map((r) => r.reduce((s, a, j) => s + a * v[j], 0));
const dot = (a, b) => a.reduce((s, x, i) => s + x * b[i], 0);
const norm = (a) => Math.sqrt(dot(a, a));
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const sub = (a, b) => a.map((x, i) => x - b[i]);
const scale = (a, s) => a.map((x) => x * s);
const eye = (n) => Array.from({ length: n }, (_, i) => Array.from({ length: n }, (_, j) => (i === j ? 1 : 0)));

export function det3(M) {
  return M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1])
       - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
       + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0]);
}
export function inv3(M) {
  const d = det3(M);
  const [[a, b, c], [e, f, g], [h, i, j]] = M;
  return [
    [(f * j - g * i) / d, (c * i - b * j) / d, (b * g - c * f) / d],
    [(g * h - e * j) / d, (a * j - c * h) / d, (c * e - a * g) / d],
    [(e * i - f * h) / d, (b * h - a * i) / d, (a * f - b * e) / d],
  ];
}

/** Resuelve A·x = b (n×n) con eliminación gaussiana y pivoteo parcial. */
export function solve(A, b) {
  const n = A.length;
  const M = A.map((r, i) => [...r, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    if (Math.abs(M[p][c]) < 1e-300) throw new Error('sistema singular');
    [M[c], M[p]] = [M[p], M[c]];
    for (let r = c + 1; r < n; r++) {
      const f = M[r][c] / M[c][c];
      if (f === 0) continue;
      for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k];
    }
  }
  const x = new Array(n).fill(0);
  for (let r = n - 1; r >= 0; r--) {
    let s = M[r][n];
    for (let k = r + 1; k < n; k++) s -= M[r][k] * x[k];
    x[r] = s / M[r][r];
  }
  return x;
}

/** Autovalores/autovectores de una matriz simétrica (Jacobi cíclico). vectors[:, i] ↔ values[i], orden ascendente. */
export function eigSym(S) {
  const n = S.length;
  const A = S.map((r) => r.slice());
  const V = eye(n);
  for (let sweep = 0; sweep < 100; sweep++) {
    let off = 0;
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) off += A[i][j] * A[i][j];
    if (off < 1e-30) break;
    for (let p = 0; p < n; p++) for (let q = p + 1; q < n; q++) {
      if (Math.abs(A[p][q]) < 1e-300) continue;
      const theta = (A[q][q] - A[p][p]) / (2 * A[p][q]);
      const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
      const c = 1 / Math.sqrt(t * t + 1), s = t * c;
      for (let k = 0; k < n; k++) {
        const akp = A[k][p], akq = A[k][q];
        A[k][p] = c * akp - s * akq;
        A[k][q] = s * akp + c * akq;
      }
      for (let k = 0; k < n; k++) {
        const apk = A[p][k], aqk = A[q][k];
        A[p][k] = c * apk - s * aqk;
        A[q][k] = s * apk + c * aqk;
      }
      for (let k = 0; k < n; k++) {
        const vkp = V[k][p], vkq = V[k][q];
        V[k][p] = c * vkp - s * vkq;
        V[k][q] = s * vkp + c * vkq;
      }
    }
  }
  const idx = [...Array(n).keys()].sort((a, b) => A[a][a] - A[b][b]);
  return { values: idx.map((i) => A[i][i]), vectors: V.map((r) => idx.map((i) => r[i])) };
}
const smallestEigvec = (S) => { const e = eigSym(S); return e.vectors.map((r) => r[0]); };

/** Rotación más cercana (polar): R = M·(MᵀM)^-1/2. */
export function nearestRotation(M) {
  const e = eigSym(mul(T(M), M));
  const D = e.values.map((l) => 1 / Math.sqrt(Math.max(l, 1e-300)));
  const V = e.vectors;
  const invSqrt = mul(mul(V, D.map((d, i) => D.map((_, j) => (i === j ? d : 0)))), T(V));
  let R = mul(M, invSqrt);
  if (det3(R) < 0) R = R.map((r) => r.map((x) => -x));
  return R;
}

export function rodrigues(r) {
  const th = norm(r);
  if (th < 1e-12) {
    // aproximación de primer orden
    return [[1, -r[2], r[1]], [r[2], 1, -r[0]], [-r[1], r[0], 1]];
  }
  const [x, y, z] = scale(r, 1 / th);
  const c = Math.cos(th), s = Math.sin(th), C = 1 - c;
  return [
    [c + x * x * C, x * y * C - z * s, x * z * C + y * s],
    [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
    [z * x * C - y * s, z * y * C + x * s, c + z * z * C],
  ];
}
export function rotToRvec(R) {
  const c = Math.min(1, Math.max(-1, (R[0][0] + R[1][1] + R[2][2] - 1) / 2));
  const th = Math.acos(c);
  if (th < 1e-9) return [0, 0, 0];
  if (Math.PI - th < 1e-6) {
    // ~180°: eje desde la diagonal
    const ax = [Math.sqrt(Math.max(0, (R[0][0] + 1) / 2)), Math.sqrt(Math.max(0, (R[1][1] + 1) / 2)), Math.sqrt(Math.max(0, (R[2][2] + 1) / 2))];
    if (R[0][1] < 0) ax[1] = -ax[1];
    if (R[0][2] < 0) ax[2] = -ax[2];
    return scale(ax, th / norm(ax));
  }
  const k = th / (2 * Math.sin(th));
  return [(R[2][1] - R[1][2]) * k, (R[0][2] - R[2][0]) * k, (R[1][0] - R[0][1]) * k];
}
/** Ángulo (grados) entre dos rotaciones. */
export function rotDiffDeg(Ra, Rb) {
  return (norm(rotToRvec(mul(Ra, T(Rb)))) * 180) / Math.PI;
}

// ───────────────────────── modelo de cámara ─────────────────────────

export function project(K, R, t, X) {
  const xc = [dot(R[0], X) + t[0], dot(R[1], X) + t[1], dot(R[2], X) + t[2]];
  const u = K[0][0] * xc[0] + K[0][1] * xc[1] + K[0][2] * xc[2];
  const v = K[1][1] * xc[1] + K[1][2] * xc[2];
  return [u / xc[2], v / xc[2], xc[2]];
}

export function residuos(K, R, t, uv, X) {
  return uv.map((p, i) => {
    const q = project(K, R, t, X[i]);
    return Math.hypot(q[0] - p[0], q[1] - p[1]);
  });
}
const rmsOf = (e) => Math.sqrt(e.reduce((s, x) => s + x * x, 0) / Math.max(1, e.length));

/** Centro del proyector en el marco del rig y su pose (4x4 row-major, ejes OpenCV) = inv([R|t]). */
export function poseEnRig(R, t) {
  const Rt = T(R);
  const c = mulv(Rt, t).map((x) => -x);
  return [[...Rt[0], c[0]], [...Rt[1], c[1]], [...Rt[2], c[2]], [0, 0, 0, 1]];
}

// ───────────────────────── DLT ─────────────────────────

function normaliza2(uv) {
  const n = uv.length;
  const c = [0, 0];
  uv.forEach((p) => { c[0] += p[0] / n; c[1] += p[1] / n; });
  const d = uv.reduce((s, p) => s + Math.hypot(p[0] - c[0], p[1] - c[1]), 0) / n;
  const s = Math.SQRT2 / (d || 1);
  return { T: [[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1]], pts: uv.map((p) => [(p[0] - c[0]) * s, (p[1] - c[1]) * s]) };
}
function normaliza3(X) {
  const n = X.length;
  const c = [0, 0, 0];
  X.forEach((p) => { for (let k = 0; k < 3; k++) c[k] += p[k] / n; });
  const d = X.reduce((s, p) => s + norm(sub(p, c)), 0) / n;
  const s = Math.sqrt(3) / (d || 1);
  return {
    T: [[s, 0, 0, -s * c[0]], [0, s, 0, -s * c[1]], [0, 0, s, -s * c[2]], [0, 0, 0, 1]],
    pts: X.map((p) => sub(p, c).map((x) => x * s)),
  };
}

/** DLT normalizado (Hartley): P 3x4 con ≥ 6 puntos no coplanares. */
export function dlt(uv, X) {
  if (uv.length < 6) throw new Error('DLT necesita ≥ 6 puntos');
  const n2 = normaliza2(uv), n3 = normaliza3(X);
  const S = Array.from({ length: 12 }, () => new Array(12).fill(0));
  const acc = (row) => {
    for (let i = 0; i < 12; i++) { if (row[i] === 0) continue; for (let j = 0; j < 12; j++) S[i][j] += row[i] * row[j]; }
  };
  for (let i = 0; i < uv.length; i++) {
    const [u, v] = n2.pts[i];
    const Xh = [...n3.pts[i], 1];
    acc([...Xh, 0, 0, 0, 0, ...Xh.map((x) => -u * x)]);
    acc([0, 0, 0, 0, ...Xh, ...Xh.map((x) => -v * x)]);
  }
  const p = smallestEigvec(S);
  const Pn = [p.slice(0, 4), p.slice(4, 8), p.slice(8, 12)];
  const T2inv = inv3(n2.T);
  return mul(mul(T2inv, Pn), n3.T);
}

function qr3(A) {
  // Gram-Schmidt modificado por columnas: A = Q·R
  const cols = [0, 1, 2].map((j) => A.map((r) => r[j]));
  const q = [];
  const Rm = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (let j = 0; j < 3; j++) {
    let v = cols[j].slice();
    for (let i = 0; i < j; i++) {
      Rm[i][j] = dot(q[i], v);
      v = sub(v, scale(q[i], Rm[i][j]));
    }
    Rm[j][j] = norm(v);
    q.push(scale(v, 1 / Rm[j][j]));
  }
  const Q = [0, 1, 2].map((i) => [q[0][i], q[1][i], q[2][i]]);
  return { Q, R: Rm };
}

/** RQ 3x3: M = K·R con K triangular superior (diagonal positiva) y R ortogonal. */
export function rq3(M) {
  const F = [[0, 0, 1], [0, 1, 0], [1, 0, 0]];
  const { Q, R: Rq } = qr3(T(mul(F, M)));
  const K = mul(mul(F, T(Rq)), F);
  const Rot = mul(F, T(Q));
  for (let i = 0; i < 3; i++) {
    if (K[i][i] < 0) {
      for (let r = 0; r < 3; r++) K[r][i] = -K[r][i];
      Rot[i] = Rot[i].map((x) => -x);
    }
  }
  return { K, R: Rot };
}

/** P → K (K22 = 1), R (det +1), t. */
export function descomponerP(P0) {
  let P = P0.map((r) => r.slice());
  const M = P.map((r) => r.slice(0, 3));
  if (det3(M) < 0) P = P.map((r) => r.map((x) => -x));
  const { K: Ku, R } = rq3(P.map((r) => r.slice(0, 3)));
  const t = mulv(inv3(Ku), P.map((r) => r[3]));
  const K = Ku.map((r) => r.map((x) => x / Ku[2][2]));
  return { K, R, t };
}

// ───────────────────────── PnP con K conocida ─────────────────────────

export function planitud(X) {
  const n = X.length;
  const c = [0, 0, 0];
  X.forEach((p) => { for (let k = 0; k < 3; k++) c[k] += p[k] / n; });
  const C = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  X.forEach((p) => { const d = sub(p, c); for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) C[i][j] += d[i] * d[j] / n; });
  const e = eigSym(C);
  return { ratio: Math.sqrt(Math.max(0, e.values[0]) / Math.max(1e-300, e.values[2])), centro: c, ejes: e.vectors };
}

/** Pose con K fija y puntos ~coplanares: homografía (DLT 2D) + descomposición de Zhang. */
function pnpPlano(uv, X, K) {
  const { centro, ejes } = planitud(X);
  // base del plano: e1, e2 = mayores autovectores, n = el menor
  const e1 = [0, 1, 2].map((i) => ejes[i][2]);
  const e2 = [0, 1, 2].map((i) => ejes[i][1]);
  const nrm = cross(e1, e2);
  const ab = X.map((p) => { const d = sub(p, centro); return [dot(d, e1), dot(d, e2)]; });
  const Kinv = inv3(K);
  const m = uv.map((p) => { const q = mulv(Kinv, [p[0], p[1], 1]); return [q[0] / q[2], q[1] / q[2]]; });
  const na = normaliza2(ab), nm = normaliza2(m);
  const S = Array.from({ length: 9 }, () => new Array(9).fill(0));
  const acc = (row) => { for (let i = 0; i < 9; i++) for (let j = 0; j < 9; j++) S[i][j] += row[i] * row[j]; };
  for (let i = 0; i < ab.length; i++) {
    const [a, b] = na.pts[i], [x, y] = nm.pts[i];
    acc([a, b, 1, 0, 0, 0, -x * a, -x * b, -x]);
    acc([0, 0, 0, a, b, 1, -y * a, -y * b, -y]);
  }
  const h = smallestEigvec(S);
  const Hn = [h.slice(0, 3), h.slice(3, 6), h.slice(6, 9)];
  const H = mul(mul(inv3(nm.T), Hn), na.T);
  const h1 = H.map((r) => r[0]), h2 = H.map((r) => r[1]), h3 = H.map((r) => r[2]);
  let lam = 2 / (norm(h1) + norm(h2));
  if (h3[2] * lam < 0) lam = -lam; // el plano delante del proyector
  const r1 = scale(h1, lam), r2 = scale(h2, lam), tp = scale(h3, lam);
  const Rp = nearestRotation([0, 1, 2].map((i) => [r1[i], r2[i], cross(r1, r2)[i]]));
  // X_cam = Rp·Bᵀ·(X − c) + tp, B = [e1 e2 n]
  const Bt = [e1, e2, nrm];
  const R = mul(Rp, Bt);
  const t = sub(tp, mulv(R, centro));
  return { R, t };
}

/** Pose con K fija y puntos no coplanares: DLT sobre coordenadas normalizadas → [R|t] + polar. */
function pnpGeneral(uv, X, K) {
  const Kinv = inv3(K);
  const m = uv.map((p) => { const q = mulv(Kinv, [p[0], p[1], 1]); return [q[0] / q[2], q[1] / q[2]]; });
  let P = dlt(m, X);
  const M = P.map((r) => r.slice(0, 3));
  if (det3(M) < 0) P = P.map((r) => r.map((x) => -x));
  const R = nearestRotation(P.map((r) => r.slice(0, 3)));
  const e = eigSym(mul(T(P.map((r) => r.slice(0, 3))), P.map((r) => r.slice(0, 3))));
  const s = e.values.reduce((a, l) => a + Math.sqrt(Math.max(l, 0)), 0) / 3;
  const t = P.map((r) => r[3] / s);
  return { R, t };
}

// ───────────────────────── Levenberg–Marquardt ─────────────────────────

/**
 * Refina K y/o pose minimizando el error de reproyección (px).
 * modelo: 'completo' (fx, fy, cx, cy) · 'fxfy' (f único, cx, cy) · 'kfija' (solo pose). Skew = 0.
 */
export function refinarLM(uv, X, K0, R0, t0, { modelo = 'completo', iter = 100 } = {}) {
  const r0 = rotToRvec(R0);
  let theta;
  if (modelo === 'completo') theta = [K0[0][0], K0[1][1], K0[0][2], K0[1][2], ...r0, ...t0];
  else if (modelo === 'fxfy') theta = [(K0[0][0] + K0[1][1]) / 2, K0[0][2], K0[1][2], ...r0, ...t0];
  else theta = [...r0, ...t0];
  const unpack = (th) => {
    let K, o;
    if (modelo === 'completo') { K = [[th[0], 0, th[2]], [0, th[1], th[3]], [0, 0, 1]]; o = 4; }
    else if (modelo === 'fxfy') { K = [[th[0], 0, th[1]], [0, th[0], th[2]], [0, 0, 1]]; o = 3; }
    else { K = K0; o = 0; }
    return { K, R: rodrigues(th.slice(o, o + 3)), t: th.slice(o + 3, o + 6) };
  };
  const res = (th) => {
    const { K, R, t } = unpack(th);
    const r = [];
    for (let i = 0; i < uv.length; i++) { const q = project(K, R, t, X[i]); r.push(q[0] - uv[i][0], q[1] - uv[i][1]); }
    return r;
  };
  const cost = (r) => r.reduce((s, x) => s + x * x, 0);
  const np = theta.length;
  let r = res(theta), c = cost(r), lambda = -1;
  for (let it = 0; it < iter; it++) {
    // jacobiano numérico (diferencias centrales)
    const J = r.map(() => new Array(np).fill(0));
    for (let j = 0; j < np; j++) {
      const h = 1e-6 * Math.max(1, Math.abs(theta[j]));
      const tp = theta.slice(), tm = theta.slice();
      tp[j] += h; tm[j] -= h;
      const rp = res(tp), rm = res(tm);
      for (let i = 0; i < r.length; i++) J[i][j] = (rp[i] - rm[i]) / (2 * h);
    }
    const JtJ = Array.from({ length: np }, (_, a) => Array.from({ length: np }, (_, b) => J.reduce((s, row) => s + row[a] * row[b], 0)));
    const Jtr = Array.from({ length: np }, (_, a) => J.reduce((s, row, i) => s + row[a] * r[i], 0));
    if (lambda < 0) lambda = 1e-3; // Marquardt: (JᵀJ + λ·diag(JᵀJ))·δ = −Jᵀr
    let mejoro = false;
    for (let k = 0; k < 12; k++) {
      const A = JtJ.map((row, i) => row.map((x, j) => (i === j ? x + lambda * Math.max(x, 1e-12) : x)));
      let delta;
      try { delta = solve(A, Jtr.map((x) => -x)); } catch { lambda *= 10; continue; }
      const tn = theta.map((x, i) => x + delta[i]);
      const rn = res(tn), cn = cost(rn);
      if (cn < c) {
        const paso = Math.sqrt(delta.reduce((s, d, i) => s + (d / Math.max(1, Math.abs(theta[i]))) ** 2, 0));
        theta = tn; r = rn;
        const dc = c - cn; c = cn;
        lambda = Math.max(lambda / 3, 1e-12);
        mejoro = true;
        if (paso < 1e-10 || dc < 1e-12 * (1 + c)) it = iter;
        break;
      }
      lambda *= 4;
    }
    if (!mejoro) break;
  }
  return unpack(theta);
}

// ───────────────────────── API principal ─────────────────────────

function kSensata(K, w, h) {
  const fx = K[0][0], fy = K[1][1];
  return fx > 0.2 * w && fx < 20 * w && fy > 0 && Math.abs(fx / fy - 1) < 0.25
    && K[0][2] > -0.5 * w && K[0][2] < 1.5 * w && K[1][2] > -1.0 * h && K[1][2] < 2.0 * h;
}

/**
 * Calibra con pares uv (px del proyector) ↔ X (m, marco del rig).
 * opts: { w, h, Kguess (3x3, para K fija o respaldo), modelo: 'auto'|'completo'|'fxfy'|'kfija' }
 * Devuelve { K, R, t, rms, errores, metodo, planitud, n, centro (pose del proyector en el rig) }.
 */
export function calibrar(uv, X, opts = {}) {
  const { w = 1920, h = 1080, Kguess = null } = opts;
  let modelo = opts.modelo || 'auto';
  const n = uv.length;
  if (n !== X.length) throw new Error('uv y X de distinto largo');
  const pl = planitud(X);
  const plano = pl.ratio < 0.03;
  let sol = null, metodo = '';

  const intentaLibre = (m) => {
    const P = dlt(uv, X);
    const d = descomponerP(P);
    if (!kSensata(d.K, w, h)) return null;
    const ini = { K: [[d.K[0][0], 0, d.K[0][2]], [0, d.K[1][1], d.K[1][2]], [0, 0, 1]], R: d.R, t: d.t };
    const r = refinarLM(uv, X, ini.K, ini.R, ini.t, { modelo: m });
    if (!kSensata(r.K, w, h)) return null;
    return r;
  };

  if (modelo === 'auto') modelo = n >= 8 ? 'fxfy' : 'kfija';
  // varios arranques: DLT y, si hay K de respaldo, PnP con esa K → LM. Gana el de menor error con los puntos delante.
  const cands = [];
  const libre = modelo !== 'kfija' && n >= 6 && !plano;
  if (libre) {
    try { const s = intentaLibre(modelo); if (s) cands.push({ ...s, metodo: `dlt+lm(${modelo})` }); } catch { /* degenerado */ }
  }
  if (Kguess && n >= 4) {
    try {
      const ini = plano || n < 6 ? pnpPlano(uv, X, Kguess) : pnpGeneral(uv, X, Kguess);
      const s = refinarLM(uv, X, Kguess, ini.R, ini.t, { modelo: 'kfija' });
      s.K = Kguess.map((r) => r.slice());
      const tipo = plano ? 'plano' : n < 6 ? 'pocos' : 'general';
      if (libre) {
        const s2 = refinarLM(uv, X, s.K, s.R, s.t, { modelo });
        if (kSensata(s2.K, w, h)) cands.push({ ...s2, metodo: `pnp(${tipo})+lm(${modelo})` });
      } else cands.push({ ...s, metodo: `${tipo}+lm(kfija)` });
    } catch { /* sin solución con K de respaldo */ }
  }
  for (const c of cands) {
    c.rms = rmsOf(residuos(c.K, c.R, c.t, uv, X));
    c.delante = X.every((p) => dot(c.R[2], p) + c.t[2] > 0);
  }
  cands.sort((a, b) => (b.delante - a.delante) || (a.rms - b.rms));
  sol = cands[0] || null;
  metodo = sol ? sol.metodo : '';
  if (!sol) {
    throw new Error(plano
      ? 'Puntos casi coplanares: hace falta K conocida (o toca puntos en superficies a distinta profundidad)'
      : n < 6 ? 'Faltan puntos (≥ 6)' : 'No hubo solución sensata (revisa los toques)');
  }
  const errores = residuos(sol.K, sol.R, sol.t, uv, X);
  const z = X.map((p) => dot(sol.R[2], p) + sol.t[2]);
  return {
    K: sol.K, R: sol.R, t: sol.t,
    rms: rmsOf(errores), errores, metodo, n,
    planitud: pl.ratio, delante: z.every((d) => d > 0),
    poseEnRig: poseEnRig(sol.R, sol.t),
  };
}

/**
 * Error del modelo contra la verdad (solo pruebas): rejilla de píxeles → rayo con la verdad → punto a `dist` m
 * → reproyección con el estimado. Devuelve rms/max en px.
 */
export function errorContraVerdad(est, ver, { w = 1920, h = 1080, dist = 2.0, nx = 9, ny = 5 } = {}) {
  const Kinv = inv3(ver.K);
  const Rt = T(ver.R);
  const C = mulv(Rt, ver.t).map((x) => -x);
  const errs = [];
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    const u = ((i + 0.5) / nx) * w, v = ((j + 0.5) / ny) * h;
    const d = mulv(Kinv, [u, v, 1]);
    const dirC = scale(d, 1 / d[2]); // z = 1
    const Xc = scale(dirC, dist / norm(dirC));
    const Xw = mulv(Rt, Xc).map((x, k) => x + C[k]);
    const q = project(est.K, est.R, est.t, Xw);
    errs.push(Math.hypot(q[0] - u, q[1] - v));
  }
  return { rms: rmsOf(errs), max: Math.max(...errs) };
}

/**
 * Calibración de pivote de la punta del control: N poses (R 3x3, p) con la punta quieta en un punto.
 * Resuelve R_i·punta + p_i = pivote (mínimos cuadrados). Devuelve { punta (marco del control), pivote, rms }.
 */
export function pivote(poses) {
  if (poses.length < 6) throw new Error('pivote: faltan poses (≥ 6)');
  const A = Array.from({ length: 6 }, () => new Array(6).fill(0));
  const b = new Array(6).fill(0);
  for (const { R, p } of poses) {
    for (let k = 0; k < 3; k++) {
      const row = [R[k][0], R[k][1], R[k][2], 0, 0, 0];
      row[3 + k] = -1;
      for (let i = 0; i < 6; i++) { b[i] += row[i] * -p[k]; for (let j = 0; j < 6; j++) A[i][j] += row[i] * row[j]; }
    }
  }
  const x = solve(A, b);
  const punta = x.slice(0, 3), piv = x.slice(3, 6);
  const errs = poses.map(({ R, p }) => norm(sub(mulv(R, punta).map((v, k) => v + p[k]), piv)));
  let maxAng = 0;
  for (let i = 0; i < poses.length; i++) for (let j = i + 1; j < poses.length; j++) maxAng = Math.max(maxAng, rotDiffDeg(poses[i].R, poses[j].R));
  return { punta, pivote: piv, rms: rmsOf(errs), giroMaxDeg: maxAng };
}

/** Cruces de calibración repartidas por el cuadro (px del proyector), en orden de serpiente. */
export function crucesPorDefecto(w = 1920, h = 1080, n = 12) {
  const cols = n <= 8 ? 4 : n <= 12 ? 4 : n <= 16 ? 4 : 5;
  const rows = Math.ceil(n / cols);
  const out = [];
  for (let j = 0; j < rows; j++) {
    const fila = [];
    for (let i = 0; i < cols; i++) {
      const u = (0.1 + (0.8 * i) / (cols - 1)) * w;
      const v = (0.12 + (0.76 * j) / Math.max(1, rows - 1)) * h;
      fila.push([Math.round(u), Math.round(v)]);
    }
    if (j % 2) fila.reverse();
    out.push(...fila);
  }
  return out.slice(0, n);
}
