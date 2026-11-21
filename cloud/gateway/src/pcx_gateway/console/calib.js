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
export function signal(canvas) {
  const k = DETECT_W / canvas.width;
  const w = DETECT_W;
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

/** Dibuja sobre la foto la superficie proyectable y una grilla cada 100 mm, con sus coordenadas. */
export function annotate(ctx, pxToMm, surface) {
  const toPx = invert(pxToMm);
  const P = (x, y) => apply(toPx, [x, y]);
  const { width_mm: W, height_mm: H } = surface;
  const line = (a, b) => (ctx.beginPath(), ctx.moveTo(...a), ctx.lineTo(...b), ctx.stroke());
  ctx.save();
  ctx.strokeStyle = "#ff5b24";
  ctx.lineWidth = 1;
  ctx.globalAlpha = 0.55;
  for (let x = 100; x < W; x += 100) line(P(x, 0), P(x, H));
  for (let y = 100; y < H; y += 100) line(P(0, y), P(W, y));
  ctx.globalAlpha = 0.95;
  ctx.lineWidth = 2;
  ctx.beginPath();
  [P(0, 0), P(W, 0), P(W, H), P(0, H)].forEach((p, i) => (i ? ctx.lineTo(...p) : ctx.moveTo(...p)));
  ctx.closePath();
  ctx.stroke();
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
