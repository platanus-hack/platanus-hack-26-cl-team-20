// Dibuja el mundo de pcx (widgets en mm de `table_mm`) sobre un canvas 2D.
// Lo usan la ventana del proyector y la vista previa de la consola: la misma función, el mismo resultado.

import { DIM, LIGHT, SANS } from "./wui-brand.js";

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

let K = 1; // escala de texto elegida en la consola
let SCREEN = null; // {source: video, hint}: la pantalla compartida, si esta ventana la tiene

const draw = {
  label(ctx, w, world, s) {
    const p = anchorPoint(w, world) || [world.surface.width_mm / 2, world.surface.height_mm / 2];
    const size = (w.size_mm ?? 28) * s * K; // por defecto 28 mm: 12 no se lee desde la cámara
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
    ctx.fillStyle = w.color ?? LIGHT;
    lines.forEach((l, i) => ctx.fillText(l, x, y - height / 2 + lh * (i + 0.5)));
    return [(x - width / 2) / s, (y - height / 2) / s, width / s, height / s];
  },

  halo(ctx, w, world, s, t) {
    let poly = w.polygon_mm;
    if (!poly && w.anchor?.object_id) poly = (world.objects || []).find((o) => o.id === w.anchor.object_id)?.polygon_mm;
    const hz = w.pulse_hz ?? 0;
    const color = w.color ?? LIGHT;
    ctx.globalAlpha = hz > 0 ? 0.84 + 0.16 * Math.sin(2 * Math.PI * hz * t) : 1;
    ctx.strokeStyle = color;
    ctx.lineWidth = (w.width_mm ?? 4) * s;
    ctx.lineJoin = "round";
    ctx.shadowColor = color;
    ctx.shadowBlur = ctx.lineWidth;
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
      const r = w.radius_mm ?? 60; // sin polígono, el halo es un círculo de este radio en el ancla
      ctx.arc(p[0] * s, p[1] * s, r * s, 0, Math.PI * 2);
      box = [p[0] - r, p[1] - r, r * 2, r * 2];
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
    ctx.strokeStyle = ctx.fillStyle = w.color ?? LIGHT;
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
    const color = w.color ?? LIGHT;
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
      const size = 24 * s * K;
      ctx.font = `700 ${size}px ${SANS}`;
      for (const l of wrap(ctx, w.title, rw - pad * 2)) {
        ctx.fillText(l, x + pad, cy);
        cy += size * 1.2;
      }
      cy += 6 * s;
    }
    if (w.body) {
      const size = 18 * s * K;
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
    const color = w.color ?? LIGHT;
    ctx.fillStyle = `${color.slice(0, 7)}33`;
    ctx.strokeStyle = color;
    ctx.lineWidth = 3 * s;
    roundRect(ctx, x, y, rw, rh, rh / 2);
    ctx.fill();
    ctx.stroke();
    const size = Math.min(rh * 0.5, 30 * s * K);
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
    if (w.shader === "hands" || w.shader === "html") return null; // hands: drawHands; html: iframe de html-layer.js
    const r = w.rect_mm || [0, 0, world.surface.width_mm, world.surface.height_mm];
    if (w.shader === "screen") {
      // Pantalla o ventana del computador que la persona comparte desde la consola (getDisplayMedia), en vivo.
      const [x, y, rw, rh] = r.map((v) => v * s);
      const v = SCREEN?.source;
      const [vw, vh] = [v?.videoWidth, v?.videoHeight];
      ctx.globalAlpha = w.params?.opacity ?? 1;
      if (vw) {
        const cover = w.params?.fit === "cover";
        const k = cover ? Math.max(rw / vw, rh / vh) : Math.min(rw / vw, rh / vh);
        ctx.beginPath();
        ctx.rect(x, y, rw, rh);
        ctx.clip();
        ctx.drawImage(v, x + (rw - vw * k) / 2, y + (rh - vh * k) / 2, vw * k, vh * k);
      } else {
        ctx.strokeStyle = DIM;
        ctx.lineWidth = 2 * s;
        ctx.setLineDash([10 * s, 8 * s]);
        ctx.strokeRect(x, y, rw, rh);
        ctx.fillStyle = LIGHT;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.font = `600 ${Math.max(14, Math.min(26, r[2] / 18)) * s}px ${SANS}`;
        ctx.fillText(SCREEN?.hint ?? "Pantalla sin compartir: aprieta «Compartir pantalla» en la consola", x + rw / 2, y + rh / 2, rw - 20 * s);
      }
      return r;
    }
    ctx.save();
    ctx.beginPath();
    if (w.polygon_mm) w.polygon_mm.forEach((q, i) => (i ? ctx.lineTo(q[0] * s, q[1] * s) : ctx.moveTo(q[0] * s, q[1] * s)));
    else ctx.rect(r[0] * s, r[1] * s, r[2] * s, r[3] * s);
    ctx.clip();
    const cx = (r[0] + r[2] / 2) * s;
    const cy = (r[1] + r[3] / 2) * s;
    const reach = (Math.hypot(r[2], r[3]) / 2) * s;
    const color = typeof w.params?.color === "string" ? w.params.color.slice(0, 7) : LIGHT;
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
  ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  // W×H es la superficie completa (la pantalla del proyector); si la ventana es más chica, `offset` dice dónde cae.
  if (opts.offset) ctx.translate(-opts.offset[0], -opts.offset[1]);
  const s = W / world.surface.width_mm;
  const bounds = new Map();
  K = opts.textScale || 1;
  SCREEN = opts.screen ?? null;
  if (opts.calib) {
    // Calibrando: solo las marcas, sobre negro, para que la cámara las encuentre por diferencia.
    ctx.fillStyle = "#00ff00"; // verde puro: es la señal que busca calib.js
    if (opts.calib.fill) ctx.fillRect(0, 0, W, H);
    for (const [x, y, r] of opts.calib.dots || []) (ctx.beginPath(), ctx.arc(x * s, y * s, r * s, 0, Math.PI * 2), ctx.fill());
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
  const color = typeof o.color === "string" ? o.color : LIGHT;
  const accent = LIGHT;
  ctx.save();
  ctx.lineCap = ctx.lineJoin = "round";
  const b = view.hover && bounds.get(view.hover);
  if (b) {
    ctx.strokeStyle = view.fired ? LIGHT : DIM;
    ctx.fillStyle = view.fired ? "#F4F4F247" : `rgba(244, 244, 242, ${0.22 * view.progress})`;
    ctx.lineWidth = 5 * s;
    ctx.shadowColor = accent;
    ctx.shadowBlur = 5 * s;
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
        ctx.lineWidth = (2 + 5 * k) * s;
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
      ctx.shadowBlur = 4 * s;
      ctx.lineWidth = 4 * s;
      ctx.beginPath();
      for (const [a, c] of BONES) (ctx.moveTo(pts[a][0] * s, pts[a][1] * s), ctx.lineTo(pts[c][0] * s, pts[c][1] * s));
      ctx.stroke();
      for (const p of pts) (ctx.beginPath(), ctx.arc(p[0] * s, p[1] * s, 4.5 * s, 0, Math.PI * 2), ctx.fill());
    }
    ctx.fillStyle = accent;
    ctx.shadowColor = accent;
    ctx.shadowBlur = 7 * s;
    ctx.beginPath();
    ctx.arc(pts[8][0] * s, pts[8][1] * s, 6 * s, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

// ------------------------------------------------------------------ interfaz propia de la mesa
// Barra de pestañas (escenas) a la izquierda y botón de voz: zona reservada, visible en todas las escenas. Con `flip` todo gira
// 180° alrededor del centro, para quien mira la mesa desde el otro lado.
export const UI = { bar: 150, tab: 46, gap: 8, pad: 8, voice: 130, footer: 52, slideMs: 420 };

const flipRect = ([x, y, w, h], { width_mm: W, height_mm: H }, flip) => (flip ? [W - x - w, H - y - h, w, h] : [x, y, w, h]);

/** Barra de pestañas del lado izquierdo: cuántas caben, cuántas páginas hay y qué pestañas van en cada una.
 *  Si no caben todas, cada página deja el primer puesto para «subir» y el último para «bajar». */
export function uiPages(surface, names) {
  const slots = Math.max(3, Math.floor((surface.height_mm - UI.footer - 2 * UI.pad + UI.gap) / (UI.tab + UI.gap)));
  const per = names.length <= slots ? slots : slots - 2;
  const pages = [];
  for (let i = 0; i < Math.max(1, names.length); i += per) pages.push(names.slice(i, i + per));
  return { slots, per, pages, paged: pages.length > 1 };
}
/** Página de la barra donde está una escena. */
export const uiPageOf = (surface, names, name) => Math.max(0, uiPages(surface, names).pages.findIndex((p) => p.includes(name)));

/** Rectángulos reservados en mm: [{id, rect}] (la barra completa, cada pestaña de la página, los botones de página
 *  y la esquina de voz). `page` elige otra página que la actual y `dy` la desplaza (para el deslizamiento). */
export function uiRects(surface, ui, page = null, dy = 0) {
  if (!ui?.enabled) return [];
  const { width_mm: W, height_mm: H } = surface;
  const out = [{ id: "pcx:strip", rect: flipRect([0, 0, UI.bar, H], surface, ui.flip) }];
  const { slots, pages, paged } = uiPages(surface, ui.scenes);
  const k = Math.min(pages.length - 1, Math.max(0, page ?? ui.page ?? 0));
  const slot = (n) => flipRect([UI.pad, UI.pad + n * (UI.tab + UI.gap) + dy, UI.bar - 2 * UI.pad, UI.tab], surface, ui.flip);
  if (paged && k > 0) out.push({ id: "pcx:page:up", pager: "up", rect: slot(0) });
  pages[k].forEach((name, i) => out.push({ id: `pcx:tab:${name}`, name, rect: slot(i + (paged ? 1 : 0)) }));
  if (paged && k < pages.length - 1) out.push({ id: "pcx:page:down", pager: "down", rect: slot(slots - 1) });
  if (ui.voice) out.push({ id: "pcx:voice", rect: flipRect([W - UI.voice, 0, UI.voice, UI.voice], surface, ui.flip) });
  return out;
}

/** Dibuja la franja y el botón de voz. Devuelve id → rect en mm para resolver toques (clic o dedo). */
export function drawUi(ctx, W, world, ui, tMs) {
  const bounds = new Map();
  const waiting = (world.widgets || []).find((w) => w.params?.wui_anclado === true
    && typeof w.params?.wui_recuperacion?.texto === "string"
    && !(world.objects || []).some((obj) => obj.id === w.anchor?.object_id));
  if (waiting) {
    const s = W / world.surface.width_mm;
    const { texto, centro_mm: center, ancho_mm: width } = waiting.params.wui_recuperacion;
    if (Array.isArray(center) && center.length === 2 && center.every(Number.isFinite) && Number.isFinite(width) && width > 0) {
      ctx.save();
      ctx.translate(center[0] * s, center[1] * s);
      ctx.font = `450 ${27 * s}px ${SANS}`;
      ctx.fillStyle = LIGHT;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.shadowColor = "#000";
      ctx.shadowBlur = 9 * s;
      const lines = wrap(ctx, texto, width * s);
      lines.forEach((line, i) => ctx.fillText(line, 0, (i - (lines.length - 1) / 2) * 33 * s, width * s));
      ctx.restore();
    }
  }
  if (!ui?.enabled) return bounds;
  const s = W / world.surface.width_mm;
  ctx.save();
  ctx.textBaseline = "middle";
  const local = ([x, y, w, h], draw) => {
    ctx.save();
    ctx.translate((x + (ui.flip ? w : 0)) * s, (y + (ui.flip ? h : 0)) * s);
    if (ui.flip) ctx.rotate(Math.PI);
    ctx.scale(s, s);
    draw(w, h);
    ctx.restore();
  };
  const text = (label, x, y, size, maxW, color, weight = 500, align = "left") => {
    ctx.font = `${weight} ${size}px ${SANS}`;
    ctx.fillStyle = color;
    ctx.textAlign = align;
    ctx.fillText(label, x, y, maxW);
  };
  // Al cambiar de página la barra se desliza: la página que sale y la que entra se dibujan corridas un rato.
  const H = world.surface.height_mm;
  const anim = ui.slide && Date.now() - ui.slide.at < UI.slideMs ? ui.slide : null;
  const k = anim ? 1 - (1 - (Date.now() - anim.at) / UI.slideMs) ** 3 : 1;
  const dir = anim ? Math.sign((ui.page ?? 0) - anim.from) || 1 : 0;
  const bar = flipRect([0, 0, UI.bar, H - UI.footer], world.surface, ui.flip);
  const tab = ({ name, pager, rect }) => local(rect, (w, h) => {
    const active = name === ui.active;
    const y = h / 2;
    ctx.strokeStyle = active ? "#F4F4F280" : "#F4F4F229";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(12, 0);
    ctx.lineTo(12, h + UI.gap);
    ctx.stroke();
    if (active) {
      ctx.fillStyle = LIGHT;
      ctx.beginPath();
      ctx.arc(12, y, 3.1, 0, Math.PI * 2);
      ctx.fill();
    }
    text(pager ? (pager === "down" ? "↓ Más" : "↑ Volver") : name,
      25, y, 16, w - 35, active ? LIGHT : DIM, active ? 600 : 450);
  });
  ctx.save();
  ctx.beginPath();
  ctx.rect(bar[0] * s, bar[1] * s, bar[2] * s, bar[3] * s);
  ctx.clip();
  const isTab = (r) => r.id.startsWith("pcx:tab:") || r.pager;
  if (anim) uiRects(world.surface, ui, anim.from, -dir * H * k).filter(isTab).forEach(tab);
  uiRects(world.surface, ui, null, anim ? dir * H * (1 - k) : 0).filter(isTab).forEach(tab);
  ctx.restore();
  for (const { id, rect } of uiRects(world.surface, ui)) {
    if (id === "pcx:strip") continue;
    if (!anim || id === "pcx:voice") bounds.set(id, rect);
    if (id !== "pcx:voice") continue;
    const v = ui.voice;
    local(rect, (w) => {
      const x = w / 2, y = 40;
      const listening = v.state === "listening", arming = v.state === "arming";
      ctx.strokeStyle = listening || arming ? LIGHT : DIM;
      ctx.lineWidth = 1.8;
      ctx.lineCap = "round";
      for (let i = -2; i <= 2; i++) {
        const wave = listening ? 0.85 + 0.25 * Math.sin(tMs * .007 + i * .65) : 1;
        const height = (6 - Math.abs(i) * 2) * wave;
        ctx.beginPath();
        ctx.moveTo(x + i * 4.5, y - height);
        ctx.lineTo(x + i * 4.5, y + height);
        ctx.stroke();
      }
      text(listening ? "Te escucho" : arming ? "Mantén" : v.state === "unsupported" ? "Sin voz" : "Hablar",
        x, y + 24, 15, w - 14, listening || arming ? LIGHT : DIM, 500, "center");
      if (arming) {
        const k = Math.min(1, Math.max(0, (Date.now() - v.since) / (v.ms || 600)));
        ctx.beginPath();
        ctx.moveTo(x - 19, y + 40);
        ctx.lineTo(x - 19 + 38 * k, y + 40);
        ctx.stroke();
      }
      if (v.text) {
        const line = v.text.length > 60 ? `…${v.text.slice(-60)}` : v.text;
        text(line, -12, y + 2, 20, world.surface.width_mm - w - UI.bar - 30, LIGHT, 500, "right");
      }
    });
  }
  ctx.restore();
  return bounds;
}
