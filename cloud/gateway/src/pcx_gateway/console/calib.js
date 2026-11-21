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

/** Luminancia del cuadro a 640 px de ancho (el reescalado promedia y baja el ruido). */
export function gray(canvas) {
  const k = DETECT_W / canvas.width;
  const w = DETECT_W;
  const h = Math.round(canvas.height * k);
  const c = Object.assign(document.createElement("canvas"), { width: w, height: h });
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(canvas, 0, 0, w, h);
  const rgba = ctx.getImageData(0, 0, w, h).data;
  const g = new Float32Array(w * h);
  for (let i = 0; i < g.length; i++) g[i] = 0.3 * rgba[i * 4] + 0.59 * rgba[i * 4 + 1] + 0.11 * rgba[i * 4 + 2];
  return { g, w, h, k };
}

/** Centro (en px del cuadro original) de la mancha que apareció entre `off` y `on`, o null si no es una marca clara. */
export function blob(on, off) {
  const { w, h, k } = on;
  let peak = 0;
  for (let i = 0; i < on.g.length; i++) peak = Math.max(peak, on.g[i] - off.g[i]);
  if (peak < 18) return null;
  const t = Math.max(18, peak * 0.5);
  let n = 0;
  let sum = 0;
  let sx = 0;
  let sy = 0;
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const d = on.g[y * w + x] - off.g[y * w + x];
      if (d < t) continue;
      n++;
      sum += d;
      sx += d * x;
      sy += d * y;
    }
  const cx = sx / sum;
  const cy = sy / sum;
  let spread = 0;
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const d = on.g[y * w + x] - off.g[y * w + x];
      if (d >= t) spread += d * ((x - cx) ** 2 + (y - cy) ** 2);
    }
  // Una marca es chica y compacta; reflejos, manos o dos marcas a la vez dan una mancha extendida.
  if (n < 4 || Math.sqrt(spread / sum) > w * 0.05) return null;
  return { x: (cx + 0.5) / k, y: (cy + 0.5) / k, peak };
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
