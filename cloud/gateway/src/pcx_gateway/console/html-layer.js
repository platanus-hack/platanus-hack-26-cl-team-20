// UI real dentro de la proyección: widgets {type: "effect", shader: "html", rect_mm, params: {html, css, rotate_deg}}.
// Cada uno es un iframe aislado montado sobre el canvas, donde 1 px de CSS = 1 mm de la mesa.
//
// Aislamiento: `sandbox="allow-scripts"` sin `allow-same-origin` (origen opaco: no ve la sesión, el almacenamiento ni la
// consola) y una CSP que solo deja correr scripts con nuestro nonce, sin red salvo imágenes.
//
// Modo interactivo: con `params.js` el script del agente corre dentro de ese mismo iframe (sin red: ni siquiera
// imágenes https) y habla con la página de afuera por postMessage, a través del objeto `wui`:
//   wui.onHands((hands, msg) => …)  ~30 veces por segundo. hands: [{tip, palm, pinch, inside, handed, points}], en mm
//                                   relativos al rect del widget (= px de CSS; la rotación ya está deshecha).
//   wui.emit(valor)                 → widget_pressed{widget_id, value} para el agente (máx. 10 por segundo).
//   wui.size                        [ancho, alto] del widget en mm.
//   wui.onAudio(({freq, level}) => …) espectro del micrófono del computador, ~30 veces por segundo (solo si el widget
//                                   se suscribe; nunca audio crudo): freq son 128 bandas 0..255, level va de 0 a 1.
// Chrome corre los iframes aislados en otro proceso: un bucle infinito no congela la ventana del proyector. Si el
// script deja de responder, se detiene solo (WATCHDOG_MS) y la consola tiene un botón para matarlo.

import { INK, LIGHT, SANS, widgetFont } from "./wui-brand.js";

const fontCss = typeof document === "undefined" ? "" : await widgetFont();
export const WATCHDOG_MS = 4000; // sin señal de vida del script este tiempo → se detiene
export const EMIT_PER_S = 10; // eventos wui.emit por segundo y por widget
const EMIT_MAX_CHARS = 500;

const BASE_CSS = `
${fontCss}
html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; background: transparent; color: ${LIGHT};
  font: 400 24px/1.3 ${SANS}; }
* { box-sizing: border-box; }
[data-press] { cursor: pointer; }
[data-pcx-hover] { outline: 4px solid ${LIGHT}; outline-offset: 3px; }
[data-pcx-fired] { outline-color: ${LIGHT}; outline-style: double; }
`;

// Corre dentro del iframe: avisa dónde están los [data-press], reenvía clics y marca el elemento bajo el dedo.
const INNER = (token, live) => `
const all = () => [...document.querySelectorAll("[data-press]")];
const send = () => parent.postMessage({ pcx: "${token}", rects: all().map((e) => {
  const r = e.getBoundingClientRect();
  return [e.dataset.press, r.x, r.y, r.width, r.height];
}) }, "*");
addEventListener("DOMContentLoaded", () => { send(); new ResizeObserver(send).observe(document.body); });
addEventListener("load", send);
setInterval(send, 1000);
addEventListener("click", (e) => {
  const t = e.target.closest("[data-press]");
  if (t) parent.postMessage({ pcx: "${token}", press: t.dataset.press }, "*");
});
const handlers = [];
const report = (e) => parent.postMessage({ pcx: "${token}", error: String(e && e.message || e).slice(0, 300) }, "*");
window.wui = {
  size: [innerWidth, innerHeight],
  hands: [],
  onHands(fn) { handlers.push(fn); },
  emit(value) { parent.postMessage({ pcx: "${token}", emit: value === undefined ? null : value }, "*"); },
  onAudio(fn) { if (!audio.length) parent.postMessage({ pcx: "${token}", audio: 1 }, "*"); audio.push(fn); },
};
const audio = [];
addEventListener("error", (e) => report(e.error || e.message));
addEventListener("unhandledrejection", (e) => report(e.reason));
addEventListener("message", ({ data: h }) => {
  if (!h) return;
  if (h.wui === "hands") {
    wui.hands = h.hands;
    wui.size = h.size;
    for (const fn of handlers) { try { fn(h.hands, h); } catch (e) { report(e); } }
    return;
  }
  if (h.wui === "audio") {
    for (const fn of audio) { try { fn(h); } catch (e) { report(e); } }
    return;
  }
  if (!("hover" in h)) return;
  all().forEach((el) => {
    const on = el.dataset.press === h.hover;
    el.toggleAttribute("data-pcx-hover", on);
    el.toggleAttribute("data-pcx-fired", on && !!h.fired);
    el.style.setProperty("--press", on ? h.progress : 0);
  });
});
${live ? `setInterval(() => parent.postMessage({ pcx: "${token}", alive: 1 }, "*"), 500);` : ""}
`;

function srcdoc(w, token, cueAt = null) {
  const nonce = crypto.randomUUID().replaceAll("-", "");
  const p = w.params || {};
  const live = isLive(w);
  // Con script del agente no hay red de ningún tipo: tampoco imágenes https (servirían para sacar datos).
  const csp = `default-src 'none'; img-src ${live ? "" : "https: "}data: blob:; style-src 'unsafe-inline'; font-src data:; script-src 'nonce-${nonce}'`;
  // Si cambia una lectura o botón dentro del mismo paso, conservar el tiempo del texto al remontar el iframe.
  const cue = cueAt === null ? "" : `:root{--wui-pista-retardo:-${Math.max(0, performance.now() - cueAt)}ms}`;
  const js = live ? `<script nonce="${nonce}">${String(p.js).replace(/<\/script/gi, "<\\/script")}</script>` : "";
  return (
    `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}">` +
    `<script nonce="${nonce}">${INNER(token, live)}</script><style>${BASE_CSS}</style><style>${p.css ?? ""}${cue}</style></head>` +
    `<body>${p.html ?? ""}${js}</body></html>`
  );
}

const DEAD = (why) =>
  `<!doctype html><meta charset="utf-8"><body style="margin:0;height:100vh;display:grid;place-items:center;background:${INK};color:${LIGHT};font:600 22px system-ui;text-align:center;border:3px dashed ${LIGHT};box-sizing:border-box">Script detenido<br>${why}</body>`;

export const isHtml = (w) => w.type === "effect" && w.shader === "html";
/** Widget html interactivo: trae params.js. */
export const isLive = (w) => isHtml(w) && typeof w.params?.js === "string" && w.params.js.trim() !== "";

/** Rectángulo en mm de un widget html: rect_mm, o centrado en el ancla con params.size_mm (por defecto 300×200). */
export function htmlRect(w, world) {
  if (w.rect_mm) return w.rect_mm;
  const [sw, sh] = w.params?.size_mm ?? [300, 200];
  const obj = w.anchor?.object_id && (world.objects || []).find((x) => x.id === w.anchor.object_id); // sigue al objeto
  const p = w.anchor?.point_mm ?? obj?.center_mm ?? [world.surface.width_mm / 2, world.surface.height_mm / 2];
  const o = w.anchor?.offset_mm ?? [0, 0];
  return [p[0] + o[0] - sw / 2, p[1] + o[1] - sh / 2, sw, sh];
}

export class HtmlLayer {
  /** host: contenedor posicionado sobre el canvas; onPress(widget, nombre, point_mm) al hacer clic en un [data-press]. */
  constructor(host, onPress, { onDead, onError, onAudioWant } = {}) {
    this.host = host;
    this.onDead = onDead;
    this.onAudioWant = onAudioWant;
    this.audioWanted = 0;
    this.frames = new Map(); // id → {el, key, token, widget, rect, rects, alive, emits, dead}
    addEventListener("message", (e) => {
      const f = [...this.frames.values()].find((x) => x.el.contentWindow === e.source && x.token === e.data?.pcx);
      if (!f || f.dead) return;
      if (e.data.alive) f.alive = performance.now();
      if (e.data.audio && isLive(f.widget)) ((f.audio = true), this.countAudio());
      if (e.data.error) onError?.(f.widget, String(e.data.error).slice(0, 300));
      if ("emit" in e.data && isLive(f.widget)) {
        // wui.emit: evento del script para el agente, con tope de frecuencia por widget.
        const now = performance.now();
        f.emits = (f.emits || []).filter((t) => now - t < 1000);
        if (f.emits.length >= EMIT_PER_S) return;
        f.emits.push(now);
        const v = e.data.emit;
        onPress(f.widget, (typeof v === "string" ? v : JSON.stringify(v)).slice(0, EMIT_MAX_CHARS), null, "emit");
        return;
      }
      if (e.data.rects) f.rects = e.data.rects;
      if (e.data.press) {
        const t = this.targets().find((x) => x.widget.id === f.widget.id && x.name === e.data.press);
        onPress(f.widget, String(e.data.press), t ? [t.box[0] + t.box[2] / 2, t.box[1] + t.box[3] / 2] : null);
      }
    });
    // Vigilancia: un script interactivo que deja de dar señales de vida (bucle infinito) se detiene. Chrome pone
    // todos los iframes aislados de la página en un mismo proceso, así que uno colgado congela a los demás: se
    // detiene el sospechoso (el que cargó último) y el resto se vuelve a montar en un proceso nuevo. Si el culpable
    // era otro, se repite a los pocos segundos con el siguiente.
    setInterval(() => {
      if (document.visibilityState !== "visible") return;
      const now = performance.now();
      const live = [...this.frames.values()].filter((f) => isLive(f.widget) && !f.dead);
      if (!live.some((f) => now - f.alive > WATCHDOG_MS)) return;
      const culprit = live.sort((a, b) => b.loaded - a.loaded)[0]; // comparten proceso: si uno no responde, ninguno responde
      this.kill(culprit.widget.id, "dejó de responder", true);
    }, 1000);
  }

  /** Esconde la capa sin desmontar nada (foto limpia, calibración): los scripts siguen corriendo con su estado. */
  hide(on) {
    this.hidden = !!on;
    this.host.style.visibility = on ? "hidden" : "";
  }

  /** Cuántos widgets pidieron el espectro del micrófono (wui.onAudio); avisa cuando cambia. */
  countAudio() {
    const n = [...this.frames.values()].filter((f) => f.audio && !f.dead).length;
    if (n !== this.audioWanted) this.onAudioWant?.((this.audioWanted = n));
  }

  /** Espectro del micrófono para los widgets suscritos. */
  audio(msg) {
    for (const f of this.frames.values()) if (f.audio && !f.dead) f.el.contentWindow?.postMessage({ wui: "audio", ...msg }, "*");
  }

  /** Cambia el iframe por uno nuevo con ese documento. El anterior se saca primero: sin marcos, su proceso muere. */
  remount(f, doc, delay = 0) {
    const old = f.el;
    const el = old.cloneNode(false);
    old.remove();
    f.el = el;
    f.rects = [];
    f.hadHands = false;
    f.audio = false; // si sigue vivo, se vuelve a suscribir al cargar
    f.alive = performance.now() + 2000 + delay;
    const go = () => {
      if (f.el !== el || ![...this.frames.values()].includes(f)) return; // lo quitaron mientras tanto
      el.srcdoc = doc;
      this.host.append(el);
    };
    if (delay) setTimeout(go, delay);
    else go();
  }

  /** Detiene el script de un widget: reemplaza su iframe por un aviso. Vuelve a correr si el agente cambia el widget.
   *  `hung`: el proceso de los iframes está colgado; se vuelven a montar todos (los otros scripts parten de cero). */
  kill(id, why = "detenido desde la consola", hung = false) {
    const f = this.frames.get(id);
    if (!f || f.dead) return false;
    f.dead = true;
    if (hung) for (const other of this.frames.values()) if (other !== f) other.el.remove();
    this.remount(f, DEAD(why), hung ? 80 : 0);
    if (hung)
      for (const other of this.frames.values()) {
        if (other === f) continue;
        other.token = crypto.randomUUID();
        this.remount(other, other.dead ? DEAD("detenido") : srcdoc(other.widget, other.token, other.cueAt), 80);
      }
    this.onDead?.(f.widget, why);
    this.countAudio();
    return true;
  }

  /** Manos para los widgets interactivos: mm de la mesa → mm relativos al rect de cada widget (rotación deshecha). */
  hands(hands, { pinch = [], handed = [] } = {}) {
    for (const f of this.frames.values()) {
      if (!isLive(f.widget) || f.dead || !f.rect) continue;
      if (f.waitingAnchor) continue;
      const [x, y, rw, rh] = f.rect;
      const a = ((Number(f.widget.params?.rotate_deg) || 0) * Math.PI) / 180;
      const local = ([px, py]) => {
        const [dx, dy] = [px - x - rw / 2, py - y - rh / 2];
        return [Math.round((rw / 2 + dx * Math.cos(a) + dy * Math.sin(a)) * 10) / 10, Math.round((rh / 2 - dx * Math.sin(a) + dy * Math.cos(a)) * 10) / 10];
      };
      const list = hands.map((h, i) => {
        const points = h.map(local);
        const palm = [0, 5, 9, 13, 17].reduce((acc, k) => [acc[0] + points[k][0] / 5, acc[1] + points[k][1] / 5], [0, 0]).map((v) => Math.round(v * 10) / 10);
        const tip = points[8];
        return { tip, palm, pinch: !!pinch[i], inside: tip[0] >= 0 && tip[0] <= rw && tip[1] >= 0 && tip[1] <= rh, handed: handed[i] ?? null, points };
      });
      if (!list.length && !f.hadHands) continue; // sin manos se avisa una vez y después silencio
      f.hadHands = list.length > 0;
      f.el.contentWindow?.postMessage({ wui: "hands", t: Date.now(), size: [rw, rh], hands: list }, "*");
    }
  }

  /** Monta, actualiza o quita iframes según el mundo. `s`: px de CSS por mm; (ox, oy): origen de la ventana en la superficie. */
  sync(world, s, ox = 0, oy = 0) {
    const wanted = new Map((world.widgets || []).filter(isHtml).map((w) => [w.id, w]));
    for (const [id, f] of this.frames) {
      if (wanted.has(id)) continue;
      f.el.remove();
      this.frames.delete(id);
    }
    this.countAudio();
    const anchors = new Set((world.objects || []).map((obj) => obj.id));
    for (const [id, w] of wanted) {
      const key = JSON.stringify([w.params?.html, w.params?.css, w.params?.js, w.params?.wui_pista]);
      let f = this.frames.get(id);
      if (!f) {
        const el = document.createElement("iframe");
        el.setAttribute("sandbox", "allow-scripts");
        el.style.cssText = "position:absolute;left:0;top:0;border:0;transform-origin:0 0;pointer-events:auto;background:transparent;color-scheme:normal";
        this.host.append(el);
        f = { el, rects: [] };
        this.frames.set(id, f);
      }
      if (f.key !== key) {
        f.key = key;
        f.token = crypto.randomUUID();
        f.rects = [];
        f.dead = false;
        f.hadHands = false;
        f.audio = false;
        f.loaded = performance.now();
        f.alive = f.loaded + 2000; // margen para cargar
        const step = w.params?.wui_pista;
        if (typeof step !== "string") f.cueAt = null;
        else if (step !== f.cueStep) f.cueAt = f.loaded;
        f.cueStep = step;
        f.el.srcdoc = srcdoc(w, f.token, f.cueAt);
      }
      f.widget = w;
      f.waitingAnchor = w.params?.wui_anclado === true && !anchors.has(w.anchor?.object_id);
      f.rect = htmlRect(w, world);
      const [x, y, rw, rh] = f.rect;
      const rot = Number(w.params?.rotate_deg) || 0;
      Object.assign(f.el.style, {
        width: `${rw}px`,
        height: `${rh}px`,
        zIndex: String(w.z ?? 0),
        opacity: String(w.params?.opacity ?? 1),
        visibility: f.waitingAnchor ? "hidden" : "",
        pointerEvents: w.params?.wui_sin_toque === true ? "none" : "auto",
        transform: `translate(${x * s - ox}px, ${y * s - oy}px) scale(${s}) translate(${rw / 2}px, ${rh / 2}px) rotate(${rot}deg) translate(${-rw / 2}px, ${-rh / 2}px)`,
      });
    }
  }

  /** Zonas tocables en mm de la mesa: [{widget, name, box: [x, y, w, h]}], con la rotación ya aplicada. */
  targets() {
    const out = [];
    if (this.hidden) return out;
    for (const f of this.frames.values()) {
      if (f.waitingAnchor) continue;
      const [x, y, rw, rh] = f.rect;
      const a = ((Number(f.widget.params?.rotate_deg) || 0) * Math.PI) / 180;
      const turn = ([px, py]) => {
        const [dx, dy] = [px - rw / 2, py - rh / 2];
        return [x + rw / 2 + dx * Math.cos(a) - dy * Math.sin(a), y + rh / 2 + dx * Math.sin(a) + dy * Math.cos(a)];
      };
      for (const [name, rx, ry, w, h] of f.rects) {
        const c = [[rx, ry], [rx + w, ry], [rx + w, ry + h], [rx, ry + h]].map(turn);
        const xs = c.map((p) => p[0]);
        const ys = c.map((p) => p[1]);
        out.push({ widget: f.widget, name, box: [Math.min(...xs), Math.min(...ys), Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)] });
      }
    }
    return out;
  }

  /** Resalta el [data-press] bajo el dedo. `hover`: "widgetId#nombre" o null. */
  hover(hover, progress = 0, fired = false) {
    const [id, name] = hover?.includes("#") ? hover.split("#") : [];
    for (const [fid, f] of this.frames) f.el.contentWindow?.postMessage({ hover: fid === id ? name : null, progress, fired }, "*");
  }
}
