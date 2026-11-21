// Dibuja el mundo de pcx (widgets en mm de `table_mm`) sobre un canvas 2D.
// Lo usan la ventana del proyector y la vista previa de la consola: la misma función, el mismo resultado.

const images = new Map();

function image(src) {
  let img = images.get(src);
  if (!img) {
    img = new Image();
    if (!src.startsWith("data:")) img.crossOrigin = "anonymous";
    img.src = src;
    images.set(src, img);
  }
  return img.complete && img.naturalWidth ? img : null;
}

function anchorPoint(w, world) {
  const a = w.anchor;
  if (!a) return null;
  let p = null;
  if (a.point_mm) p = [a.point_mm[0], a.point_mm[1]];
  else if (a.object_id) {
    const o = (world.objects || []).find((x) => x.id === a.object_id);
    if (o) p = [o.center_mm[0], o.center_mm[1]];
  }
  if (p && a.offset_mm) p = [p[0] + a.offset_mm[0], p[1] + a.offset_mm[1]];
  return p;
}

function rectFor(w, world, dw, dh) {
  if (w.rect_mm) return w.rect_mm;
  const p = anchorPoint(w, world) || [world.surface.width_mm / 2, world.surface.height_mm / 2];
  return [p[0] - dw / 2, p[1] - dh / 2, dw, dh];
}

function wrap(ctx, text, maxW) {
  const out = [];
  for (const para of String(text).split("\n")) {
    let line = "";
    for (const word of para.split(/\s+/)) {
      const probe = line ? `${line} ${word}` : word;
      if (line && ctx.measureText(probe).width > maxW) {
        out.push(line);
        line = word;
      } else line = probe;
    }
    out.push(line);
  }
  return out;
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, Math.min(r, w / 2, h / 2));
}

const SANS = '"Archivo", system-ui, sans-serif';

const draw = {
  label(ctx, w, world, s) {
    const p = anchorPoint(w, world) || [world.surface.width_mm / 2, world.surface.height_mm / 2];
    const size = (w.size_mm ?? 12) * s;
    ctx.font = `600 ${size}px ${SANS}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const lines = String(w.text ?? "").split("\n");
    const lh = size * 1.2;
    const width = Math.max(...lines.map((l) => ctx.measureText(l).width));
    const height = lh * lines.length;
    const x = p[0] * s;
    const y = p[1] * s;
    if (w.bg) {
      ctx.fillStyle = w.bg;
      roundRect(ctx, x - width / 2 - size * 0.4, y - height / 2 - size * 0.25, width + size * 0.8, height + size * 0.5, size * 0.3);
      ctx.fill();
    }
    ctx.fillStyle = w.color ?? "#FFFFFF";
    lines.forEach((l, i) => ctx.fillText(l, x, y - height / 2 + lh * (i + 0.5)));
    return [(x - width / 2) / s, (y - height / 2) / s, width / s, height / s];
  },

  halo(ctx, w, world, s, t) {
    let poly = w.polygon_mm;
    if (!poly && w.anchor?.object_id) poly = (world.objects || []).find((o) => o.id === w.anchor.object_id)?.polygon_mm;
    const hz = w.pulse_hz ?? 1;
    const color = w.color ?? "#00E5FF";
    ctx.globalAlpha = hz > 0 ? 0.55 + 0.45 * Math.sin(2 * Math.PI * hz * t) : 1;
    ctx.strokeStyle = color;
    ctx.lineWidth = (w.width_mm ?? 4) * s;
    ctx.lineJoin = "round";
    ctx.shadowColor = color;
    ctx.shadowBlur = ctx.lineWidth * 3;
    ctx.beginPath();
    let box;
    if (poly) {
      poly.forEach((q, i) => (i ? ctx.lineTo(q[0] * s, q[1] * s) : ctx.moveTo(q[0] * s, q[1] * s)));
      ctx.closePath();
      const xs = poly.map((q) => q[0]);
      const ys = poly.map((q) => q[1]);
      box = [Math.min(...xs), Math.min(...ys), Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)];
    } else {
      const p = anchorPoint(w, world) || [world.surface.width_mm / 2, world.surface.height_mm / 2];
      ctx.arc(p[0] * s, p[1] * s, 60 * s, 0, Math.PI * 2);
      box = [p[0] - 60, p[1] - 60, 120, 120];
    }
    ctx.stroke();
    return box;
  },

  arrow(ctx, w, world, s) {
    const a = w.from_mm;
    const b = w.to_mm || anchorPoint(w, world);
    if (!a || !b) return null;
    const lw = (w.width_mm ?? 5) * s;
    const ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
    const head = lw * 3.5;
    ctx.strokeStyle = ctx.fillStyle = w.color ?? "#FFD400";
    ctx.lineWidth = lw;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(a[0] * s, a[1] * s);
    ctx.lineTo(b[0] * s - Math.cos(ang) * head * 0.8, b[1] * s - Math.sin(ang) * head * 0.8);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(b[0] * s, b[1] * s);
    ctx.lineTo(b[0] * s - Math.cos(ang - 0.45) * head, b[1] * s - Math.sin(ang - 0.45) * head);
    ctx.lineTo(b[0] * s - Math.cos(ang + 0.45) * head, b[1] * s - Math.sin(ang + 0.45) * head);
    ctx.closePath();
    ctx.fill();
    return null;
  },

  panel(ctx, w, world, s) {
    const r = rectFor(w, world, 320, 180);
    const [x, y, rw, rh] = r.map((v) => v * s);
    const color = w.color ?? "#FFFFFF";
    const pad = 14 * s;
    ctx.fillStyle = "#00000099";
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5 * s;
    roundRect(ctx, x, y, rw, rh, 8 * s);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    let cy = y + pad;
    if (w.title) {
      const size = 18 * s;
      ctx.font = `700 ${size}px ${SANS}`;
      for (const l of wrap(ctx, w.title, rw - pad * 2)) {
        ctx.fillText(l, x + pad, cy);
        cy += size * 1.2;
      }
      cy += 6 * s;
    }
    if (w.body) {
      const size = 13 * s;
      ctx.font = `400 ${size}px ${SANS}`;
      for (const l of wrap(ctx, w.body, rw - pad * 2)) {
        if (cy + size > y + rh - pad / 2) break;
        ctx.fillText(l, x + pad, cy);
        cy += size * 1.35;
      }
    }
    return r;
  },

  button(ctx, w, world, s) {
    const r = rectFor(w, world, 190, 70);
    const [x, y, rw, rh] = r.map((v) => v * s);
    const color = w.color ?? "#00E676";
    ctx.fillStyle = `${color.slice(0, 7)}33`;
    ctx.strokeStyle = color;
    ctx.lineWidth = 3 * s;
    roundRect(ctx, x, y, rw, rh, rh / 2);
    ctx.fill();
    ctx.stroke();
    const size = Math.min(rh * 0.38, 26 * s);
    ctx.font = `700 ${size}px ${SANS}`;
    ctx.fillStyle = "#FFFFFF";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(w.label ?? "", x + rw / 2, y + rh / 2, rw - size);
    if (w.progress > 0) {
      ctx.beginPath();
      ctx.lineWidth = 5 * s;
      ctx.arc(x + rw / 2, y + rh / 2, rh * 0.62, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * w.progress);
      ctx.stroke();
    }
    return r;
  },

  image(ctx, w, world, s) {
    const r = rectFor(w, world, 300, 300);
    const img = image(w.src);
    if (img) {
      ctx.globalAlpha = w.opacity ?? 1;
      ctx.drawImage(img, r[0] * s, r[1] * s, r[2] * s, r[3] * s);
    }
    return r;
  },

  // El navegador no corre los shaders WGSL del compositor: muestra un pulso de luz en la misma zona.
  effect(ctx, w, world, s, t) {
    if (w.shader === "hands") return null; // modo manos: lo dibuja drawHands con los datos en vivo
    const r = w.rect_mm || [0, 0, world.surface.width_mm, world.surface.height_mm];
    ctx.save();
    ctx.beginPath();
    if (w.polygon_mm) w.polygon_mm.forEach((q, i) => (i ? ctx.lineTo(q[0] * s, q[1] * s) : ctx.moveTo(q[0] * s, q[1] * s)));
    else ctx.rect(r[0] * s, r[1] * s, r[2] * s, r[3] * s);
    ctx.clip();
    const cx = (r[0] + r[2] / 2) * s;
    const cy = (r[1] + r[3] / 2) * s;
    const reach = (Math.hypot(r[2], r[3]) / 2) * s;
    const color = typeof w.params?.color === "string" ? w.params.color.slice(0, 7) : "#FFF6E6";
    ctx.strokeStyle = color;
    for (let i = 0; i < 6; i++) {
      const k = (t * 0.25 + i / 6) % 1;
      ctx.globalAlpha = (1 - k) * 0.7;
      ctx.lineWidth = 6 * s * (1 - k) + 1;
      ctx.beginPath();
      ctx.arc(cx, cy, k * reach, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();
    return r;
  },
};

function pattern(ctx, W, H, world, s) {
  const { width_mm: w, height_mm: h } = world.surface;
  ctx.strokeStyle = "#f1eee4";
  ctx.lineWidth = Math.max(2, 2 * s);
  ctx.strokeRect(ctx.lineWidth / 2, ctx.lineWidth / 2, W - ctx.lineWidth, H - ctx.lineWidth);
  ctx.globalAlpha = 0.35;
  ctx.lineWidth = Math.max(1, s);
  ctx.beginPath();
  for (let x = 100; x < w; x += 100) (ctx.moveTo(x * s, 0), ctx.lineTo(x * s, H));
  for (let y = 100; y < h; y += 100) (ctx.moveTo(0, y * s), ctx.lineTo(W, y * s));
  ctx.stroke();
  ctx.globalAlpha = 1;
  ctx.strokeStyle = "#ff5b24";
  ctx.lineWidth = Math.max(2, 2 * s);
  ctx.beginPath();
  ctx.moveTo(W / 2 - 40 * s, H / 2);
  ctx.lineTo(W / 2 + 40 * s, H / 2);
  ctx.moveTo(W / 2, H / 2 - 40 * s);
  ctx.lineTo(W / 2, H / 2 + 40 * s);
  ctx.stroke();
  ctx.fillStyle = "#f1eee4";
  ctx.font = `500 ${14 * s}px "Martian Mono", ui-monospace, monospace`;
  ctx.textBaseline = "top";
  ctx.textAlign = "left";
  ctx.fillText("0, 0", 12 * s, 12 * s);
  ctx.textAlign = "right";
  ctx.textBaseline = "bottom";
  ctx.fillText(`${Math.round(w)}, ${Math.round(h)} mm`, W - 12 * s, H - 12 * s);
}

/** Dibuja `world` completo. Devuelve id → rect en mm de cada widget, para resolver toques. */
export function drawScene(ctx, W, H, world, tMs, opts = {}) {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, W, H);
  const s = W / world.surface.width_mm;
  const bounds = new Map();
  if (opts.calib) {
    // Calibrando: solo las marcas, sobre negro, para que la cámara las encuentre por diferencia.
    ctx.fillStyle = "#fff";
    for (const [x, y, r] of opts.calib) (ctx.beginPath(), ctx.arc(x * s, y * s, r * s, 0, Math.PI * 2), ctx.fill());
    return bounds;
  }
  if (opts.pattern) {
    ctx.save();
    pattern(ctx, W, H, world, s);
    ctx.restore();
  }
  const widgets = [...(world.widgets || [])].sort((a, b) => (a.z ?? 0) - (b.z ?? 0));
  for (const w of widgets) {
    const fn = draw[w.type];
    if (!fn) continue;
    ctx.save();
    try {
      const box = fn(ctx, w, world, s, tMs / 1000);
      if (box) bounds.set(w.id, box);
    } catch (err) {
      console.warn("widget", w.id, err);
    }
    ctx.restore();
  }
  return bounds;
}

/** Widget tocable más alto bajo el punto (mm), o null. */
export function hit(world, bounds, pt) {
  const order = [...(world.widgets || [])].sort((a, b) => (b.z ?? 0) - (a.z ?? 0));
  for (const w of order) {
    if (!(w.on_press || w.type === "button")) continue;
    const b = bounds.get(w.id);
    if (b && pt[0] >= b[0] && pt[0] <= b[0] + b[2] && pt[1] >= b[1] && pt[1] <= b[1] + b[3]) return w;
  }
  return null;
}

const BONES = [
  [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17],
];

/** Estela de la punta del índice por mano: guarda los últimos ~0,5 s. */
export function trackTrails(trails, view, now) {
  view.hands.forEach((pts, i) => (trails[i] ||= []).push({ p: pts[8], t: now }));
  trails.length = Math.max(trails.length, view.hands.length);
  for (let i = 0; i < trails.length; i++) {
    trails[i] = (trails[i] || []).filter((q) => now - q.t < 500);
    if (i >= view.hands.length && !trails[i].length) trails.length = i;
  }
}

/** Modo manos: esqueleto con brillo sobre la mano real, estela del índice y el widget bajo el dedo resaltado. */
export function drawHands(ctx, W, world, view, trails, bounds, now) {
  const s = W / world.surface.width_mm;
  const o = view.opts || {};
  const color = typeof o.color === "string" ? o.color : "#FFF6E6";
  const accent = "#FF5B24";
  ctx.save();
  ctx.lineCap = ctx.lineJoin = "round";
  const b = view.hover && bounds.get(view.hover);
  if (b) {
    ctx.strokeStyle = view.fired ? "#FFFFFF" : accent;
    ctx.fillStyle = view.fired ? "#FFFFFF55" : `rgba(255, 91, 36, ${0.4 * view.progress})`;
    ctx.lineWidth = 5 * s;
    ctx.shadowColor = accent;
    ctx.shadowBlur = 18 * s;
    ctx.beginPath();
    ctx.roundRect((b[0] - 8) * s, (b[1] - 8) * s, (b[2] + 16) * s, (b[3] + 16) * s, 14 * s);
    ctx.fill();
    ctx.stroke();
  }
  if (o.trail !== false)
    for (const trail of trails) {
      for (let i = 1; i < trail.length; i++) {
        const k = 1 - (now - trail[i].t) / 500;
        ctx.strokeStyle = accent;
        ctx.globalAlpha = Math.max(0, k);
        ctx.lineWidth = (3 + 9 * k) * s;
        ctx.shadowBlur = 0;
        ctx.beginPath();
        ctx.moveTo(trail[i - 1].p[0] * s, trail[i - 1].p[1] * s);
        ctx.lineTo(trail[i].p[0] * s, trail[i].p[1] * s);
        ctx.stroke();
      }
    }
  ctx.globalAlpha = 1;
  for (const pts of view.hands) {
    if (o.skeleton !== false) {
      ctx.strokeStyle = ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = 14 * s;
      ctx.lineWidth = 4 * s;
      ctx.beginPath();
      for (const [a, c] of BONES) (ctx.moveTo(pts[a][0] * s, pts[a][1] * s), ctx.lineTo(pts[c][0] * s, pts[c][1] * s));
      ctx.stroke();
      for (const p of pts) (ctx.beginPath(), ctx.arc(p[0] * s, p[1] * s, 4.5 * s, 0, Math.PI * 2), ctx.fill());
    }
    ctx.fillStyle = accent;
    ctx.shadowColor = accent;
    ctx.shadowBlur = 20 * s;
    ctx.beginPath();
    ctx.arc(pts[8][0] * s, pts[8][1] * s, 9 * s, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}
