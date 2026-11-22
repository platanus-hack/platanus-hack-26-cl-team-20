// Calibración cámara↔mesa en el navegador: homografía por mínimos cuadrados, detección de la marca proyectada
// por diferencia de cuadros y grilla en mm sobre la foto de `look`.

/** Homografía 3x3 (fila mayor, h33 = 1) que lleva `src[i]` a `dst[i]`; necesita 4 pares o más. */
export function solveH(src, dst) {
  const s1 = 1 / Math.max(1, ...src.flat().map(Math.abs));
  const s2 = 1 / Math.max(1, ...dst.flat().map(Math.abs));
  const A = Array.from({ length: 8 }, () => new Float64Array(9)); // [AᵀA | Aᵀb]
  const add = (row, b) => {
    for (let i = 0; i < 8; i++) {
      if (!row[i]) continue;
      for (let j = 0; j < 8; j++) A[i][j] += row[i] * row[j];
      A[i][8] += row[i] * b;
    }
  };
  src.forEach(([x0, y0], k) => {
    const [x, y, u, v] = [x0 * s1, y0 * s1, dst[k][0] * s2, dst[k][1] * s2];
    add([x, y, 1, 0, 0, 0, -u * x, -u * y], u);
    add([0, 0, 0, x, y, 1, -v * x, -v * y], v);
  });
  for (let c = 0; c < 8; c++) {
    let p = c;
    for (let r = c + 1; r < 8; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    if (Math.abs(A[p][c]) < 1e-12) return null; // puntos degenerados (colineales)
    [A[c], A[p]] = [A[p], A[c]];
    for (let r = 0; r < 8; r++) {
      if (r === c) continue;
      const f = A[r][c] / A[c][c];
      for (let j = c; j < 9; j++) A[r][j] -= f * A[c][j];
    }
  }
  const h = A.map((row, i) => row[8] / row[i]);
  // Deshace la normalización: H = diag(1/s2, 1/s2, 1) · Hn · diag(s1, s1, 1).
  return [(h[0] * s1) / s2, (h[1] * s1) / s2, h[2] / s2, (h[3] * s1) / s2, (h[4] * s1) / s2, h[5] / s2, h[6] * s1, h[7] * s1, 1];
}

export function apply(H, [x, y]) {
  const w = H[6] * x + H[7] * y + H[8];
  return [(H[0] * x + H[1] * y + H[2]) / w, (H[3] * x + H[4] * y + H[5]) / w];
}

export function invert(m) {
  const [a, b, c, d, e, f, g, h, i] = m;
  const A = e * i - f * h;
  const B = f * g - d * i;
  const C = d * h - e * g;
  const det = a * A + b * B + c * C;
  return [A, c * h - b * i, b * f - c * e, B, a * i - c * g, c * d - a * f, C, b * g - a * h, a * e - b * d].map((v) => v / det);
}

const DETECT_W = 640;

/** «Verdor» del cuadro a 640 px de ancho: G − (R+B)/2. Las marcas son verde puro; manos, sombras y cambios de luz
 *  mueven los tres canales a la vez y casi no cambian esta señal. */
export function signal(canvas, width = DETECT_W) {
  const k = width / canvas.width;
  const w = width;
  const h = Math.round(canvas.height * k);
  const c = Object.assign(document.createElement("canvas"), { width: w, height: h });
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(canvas, 0, 0, w, h);
  const rgba = ctx.getImageData(0, 0, w, h).data;
  const g = new Float32Array(w * h);
  for (let i = 0; i < g.length; i++) g[i] = rgba[i * 4 + 1] - 0.5 * (rgba[i * 4] + rgba[i * 4 + 2]);
  return { g, w, h, k };
}

/** Promedio de las fotos con la marca menos el promedio de las fotos sin ella. */
export function diff(on, off) {
  const d = new Float32Array(on[0].g.length);
  for (const f of on) for (let i = 0; i < d.length; i++) d[i] += f.g[i] / on.length;
  for (const f of off) for (let i = 0; i < d.length; i++) d[i] -= f.g[i] / off.length;
  return { d, w: on[0].w, h: on[0].h, k: on[0].k };
}

/** Zona de la foto que el proyector ilumina (de la diferencia con todo encendido): máscara, área y nivel. */
export function litArea({ d }) {
  const sorted = Float32Array.from(d).sort();
  const level = sorted[Math.floor(sorted.length * 0.98)];
  const mask = new Uint8Array(d.length);
  let area = 0;
  if (level >= 6) for (let i = 0; i < d.length; i++) if (d[i] > level * 0.4) ((mask[i] = 1), area++);
  return { mask, area, level };
}

/** Busca la marca: la ventana (del tamaño esperado de la marca) con más señal dentro de la zona iluminada.
 *  Devuelve su centro en px del cuadro original y qué tan clara es (`z`: desviaciones sobre el resto), o null. */
export function findDot({ d, w, h, k }, mask, win) {
  const n = Math.max(5, Math.min(60, Math.round(win))) | 1;
  const r = n >> 1;
  const S = new Float64Array((w + 1) * (h + 1)); // imagen integral de la señal positiva dentro de la máscara
  for (let y = 0; y < h; y++) {
    let row = 0;
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      row += mask[i] && d[i] > 0 ? d[i] : 0;
      S[(y + 1) * (w + 1) + x + 1] = S[y * (w + 1) + x + 1] + row;
    }
  }
  const box = (x, y) => S[(y + r + 1) * (w + 1) + x + r + 1] - S[(y - r) * (w + 1) + x + r + 1] - S[(y + r + 1) * (w + 1) + x - r] + S[(y - r) * (w + 1) + x - r];
  let best = -1;
  let bx = 0;
  let by = 0;
  let sum = 0;
  let sq = 0;
  let cnt = 0;
  for (let y = r; y < h - r; y += 2)
    for (let x = r; x < w - r; x += 2) {
      if (!mask[y * w + x]) continue;
      const v = box(x, y) / (n * n);
      sum += v;
      sq += v * v;
      cnt++;
      if (v > best) ((best = v), (bx = x), (by = y));
    }
  if (cnt < 20) return null;
  const mean = sum / cnt;
  const z = (best - mean) / (Math.sqrt(Math.max(1e-6, sq / cnt - mean * mean)) + 0.5);
  if (best < 4 || z < 5) return null;
  // Centro fino: centroide de la señal alrededor de la mejor ventana.
  let peak = 0;
  for (let y = Math.max(0, by - n); y <= Math.min(h - 1, by + n); y++)
    for (let x = Math.max(0, bx - n); x <= Math.min(w - 1, bx + n); x++) peak = Math.max(peak, d[y * w + x]);
  let m = 0;
  let mx = 0;
  let my = 0;
  for (let y = Math.max(0, by - n); y <= Math.min(h - 1, by + n); y++)
    for (let x = Math.max(0, bx - n); x <= Math.min(w - 1, bx + n); x++) {
      const v = d[y * w + x] - peak * 0.35;
      if (v > 0) ((m += v), (mx += v * x), (my += v * y));
    }
  if (!m) return null;
  return { x: (mx / m + 0.5) / k, y: (my / m + 0.5) / k, z: Math.round(z * 10) / 10 };
}

/** RANSAC sobre todos los cuartetos: la homografía que deja más marcas a menos de `tol` mm, reajustada con ellas. */
export function ransacH(px, mm, tol = 15) {
  const n = px.length;
  const err = (H, i) => Math.hypot(...apply(H, px[i]).map((v, a) => v - mm[i][a]));
  let best = [];
  for (let a = 0; a < n; a++)
    for (let b = a + 1; b < n; b++)
      for (let c = b + 1; c < n; c++)
        for (let e = c + 1; e < n; e++) {
          const H = solveH([px[a], px[b], px[c], px[e]], [mm[a], mm[b], mm[c], mm[e]]);
          if (!H) continue;
          const inl = [];
          for (let i = 0; i < n; i++) if (err(H, i) < tol) inl.push(i);
          if (inl.length > best.length) best = inl;
        }
  if (best.length < 4) return null;
  const H = solveH(best.map((i) => px[i]), best.map((i) => mm[i]));
  if (!H) return null;
  const residual = Math.sqrt(best.reduce((acc, i) => acc + err(H, i) ** 2, 0) / best.length);
  return { H, inliers: best, residual };
}

// ------------------------------------------------------------------ modelo de lente
// Calibración = {matrix, image_px, lens?}. Sin `lens`, `matrix` lleva píxeles de la foto a mm. Con `lens`
// ({f, cx, cy, k1, k2}, modelo radial de OpenCV: x_d = x·(1 + k1·r² + k2·r⁴) en coordenadas normalizadas), primero se
// quita la distorsión y `matrix` lleva esos píxeles corregidos a mm. Todo en píxeles de una foto de tamaño `image_px`.

/** Píxel de la foto (distorsionado) → píxel corregido. La inversa del modelo se resuelve iterando. */
export function undistort(L, [u, v]) {
  const xd = (u - L.cx) / L.f;
  const yd = (v - L.cy) / L.f;
  let x = xd;
  let y = yd;
  for (let i = 0; i < 12; i++) {
    const r2 = x * x + y * y;
    const k = 1 + L.k1 * r2 + L.k2 * r2 * r2;
    x = xd / k;
    y = yd / k;
  }
  return [x * L.f + L.cx, y * L.f + L.cy];
}

/** Píxel corregido → píxel de la foto (fórmula directa). */
export function distort(L, [u, v]) {
  const x = (u - L.cx) / L.f;
  const y = (v - L.cy) / L.f;
  const r2 = x * x + y * y;
  const k = 1 + L.k1 * r2 + L.k2 * r2 * r2;
  return [x * k * L.f + L.cx, y * k * L.f + L.cy];
}

export const pxToMm = (c, p) => apply(c.matrix, c.lens ? undistort(c.lens, p) : p);
export function mmToPx(c, p, inv = invert(c.matrix)) {
  const q = apply(inv, p);
  return c.lens ? distort(c.lens, q) : q;
}

/** Manchas de la diferencia (componentes conexas sobre un umbral): [{x, y, area}] en px del cuadro original. */
export function components({ d, w, h, k }) {
  const sorted = Float32Array.from(d).sort();
  const t = Math.max(5, sorted[Math.floor(sorted.length * 0.997)] * 0.4);
  const seen = new Uint8Array(d.length);
  const out = [];
  const stack = [];
  for (let start = 0; start < d.length; start++) {
    if (seen[start] || d[start] < t) continue;
    let area = 0;
    let m = 0;
    let mx = 0;
    let my = 0;
    seen[start] = 1;
    stack.push(start);
    while (stack.length) {
      const i = stack.pop();
      const x = i % w;
      const y = (i - x) / w;
      area++;
      m += d[i];
      mx += d[i] * x;
      my += d[i] * y;
      for (const j of [x > 0 ? i - 1 : -1, x < w - 1 ? i + 1 : -1, i - w, i + w])
        if (j >= 0 && j < d.length && !seen[j] && d[j] >= t) ((seen[j] = 1), stack.push(j));
    }
    if (area >= 3 && area <= 4000) out.push({ x: (mx / m + 0.5) / k, y: (my / m + 0.5) / k, area });
  }
  return out;
}

const median = (list) => [...list].sort((a, b) => a - b)[list.length >> 1];

/** Ajusta k1, k2 y la homografía a pares (px de la foto, mm de la mesa) de una sola vista plana.
 *  Con una vista plana la focal no queda determinada (lo midió Max): se fija nominal y se buscan solo k1 y k2, con la
 *  homografía por mínimos cuadrados adentro. Descarta por rondas los puntos que cayeron sobre objetos altos. */
export function fitLens(px, mm, [w, h]) {
  const base = { f: 0.3516 * w, cx: w / 2, cy: h / 2 };
  const errors = (L, H, idx) => idx.map((i) => Math.hypot(...apply(H, undistort(L, px[i])).map((v, a) => v - mm[i][a])));
  const solve = (k1, k2, idx) => {
    const L = { ...base, k1, k2 };
    const H = solveH(idx.map((i) => undistort(L, px[i])), idx.map((i) => mm[i]));
    if (!H) return { cost: Infinity };
    const e = errors(L, H, idx);
    const cost = Math.sqrt(e.reduce((a, v) => a + v * v, 0) / e.length);
    return { L, H, cost: Number.isFinite(cost) ? cost : Infinity };
  };
  const search = (idx) => {
    let best = { ...solve(0, 0, idx), k1: 0, k2: 0 };
    let step = [0.08, 0.04];
    while (step[0] > 2e-5) {
      let moved = false;
      for (const [d1, d2] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, -1], [1, -1], [-1, 1]]) {
        const k1 = best.k1 + d1 * step[0];
        const k2 = best.k2 + d2 * step[1];
        if (Math.abs(k1) > 0.6 || Math.abs(k2) > 0.3) continue;
        const cand = solve(k1, k2, idx);
        if (cand.cost < best.cost - 1e-9) ((best = { ...cand, k1, k2 }), (moved = true));
      }
      if (!moved) step = [step[0] / 2, step[1] / 2];
    }
    return best;
  };
  let idx = px.map((_, i) => i);
  let fit = search(idx);
  for (let round = 0; round < 4 && fit.H; round++) {
    const e = errors(fit.L, fit.H, idx);
    const limit = Math.max(6, 3 * median(e));
    const keep = idx.filter((_, n) => e[n] <= limit);
    if (keep.length === idx.length || keep.length < 12) break;
    idx = keep;
    fit = search(idx);
  }
  if (!fit.H) return null;
  const e = errors(fit.L, fit.H, idx).sort((a, b) => a - b);
  const inv = invert(fit.H);
  const rpx = idx.map((i) => Math.hypot(...distort(fit.L, apply(inv, mm[i])).map((v, a) => v - px[i][a])));
  return {
    lens: { f: fit.L.f, cx: fit.L.cx, cy: fit.L.cy, k1: fit.k1, k2: fit.k2 },
    matrix: fit.H,
    used: idx,
    err_mm: { median: e[e.length >> 1], p95: e[Math.floor(e.length * 0.95)], max: e.at(-1) },
    rms_px: Math.sqrt(rpx.reduce((a, v) => a + v * v, 0) / rpx.length),
  };
}

/** Dibuja sobre la foto la superficie proyectable y una grilla cada 100 mm, con sus coordenadas.
 *  Con modelo de lente las líneas de la mesa se ven curvas en la foto: se dibujan por tramos. */
export function annotate(ctx, calib, surface) {
  const inv = invert(calib.matrix);
  const k = ctx.canvas.width / calib.image_px[0]; // la foto puede venir a otra resolución que la de la calibración
  const P = (x, y) => mmToPx(calib, [x, y], inv).map((v) => v * k);
  const { width_mm: W, height_mm: H } = surface;
  const line = (a, b) => {
    ctx.beginPath();
    for (let i = 0; i <= 24; i++) {
      const p = P(a[0] + ((b[0] - a[0]) * i) / 24, a[1] + ((b[1] - a[1]) * i) / 24);
      i ? ctx.lineTo(...p) : ctx.moveTo(...p);
    }
    ctx.stroke();
  };
  ctx.save();
  ctx.strokeStyle = "#ff5b24";
  ctx.lineWidth = 1;
  ctx.globalAlpha = 0.55;
  for (let x = 100; x < W; x += 100) line([x, 0], [x, H]);
  for (let y = 100; y < H; y += 100) line([0, y], [W, y]);
  ctx.globalAlpha = 0.95;
  ctx.lineWidth = 2;
  for (const [a, b] of [[[0, 0], [W, 0]], [[W, 0], [W, H]], [[W, H], [0, H]], [[0, H], [0, 0]]]) line(a, b);
  ctx.font = '500 13px "Martian Mono", ui-monospace, monospace';
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const tag = (text, [x, y]) => {
    const tw = ctx.measureText(text).width + 8;
    ctx.fillStyle = "#000000cc";
    ctx.fillRect(x - tw / 2, y - 9, tw, 18);
    ctx.fillStyle = "#ffb08f";
    ctx.fillText(text, x, y);
  };
  tag("0,0", P(0, 0));
  for (let x = 100; x <= W; x += 100) tag(`x${x}`, P(x, 0));
  for (let y = 100; y <= H; y += 100) tag(`y${y}`, P(0, y));
  ctx.restore();
}
