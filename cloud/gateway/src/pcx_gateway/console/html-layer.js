// UI real dentro de la proyección: widgets {type: "effect", shader: "html", rect_mm, params: {html, css, rotate_deg}}.
// Cada uno es un iframe aislado montado sobre el canvas, donde 1 px de CSS = 1 mm de la mesa.
//
// Aislamiento: `sandbox="allow-scripts"` sin `allow-same-origin` (origen opaco: no ve la sesión, el almacenamiento ni la
// consola) y una CSP que solo deja correr nuestro script (nonce), sin red salvo imágenes. Los scripts del agente no corren.

const BASE_CSS = `
html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; background: transparent; color: #fff;
  font: 400 24px/1.3 system-ui, -apple-system, "Segoe UI", sans-serif; }
* { box-sizing: border-box; }
[data-press] { cursor: pointer; }
[data-pcx-hover] { outline: 4px solid #ff5b24; outline-offset: 3px; }
[data-pcx-fired] { outline-color: #fff; }
`;

// Corre dentro del iframe: avisa dónde están los [data-press], reenvía clics y marca el elemento bajo el dedo.
const INNER = (token) => `
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
addEventListener("message", ({ data: h }) => all().forEach((el) => {
  const on = el.dataset.press === h.hover;
  el.toggleAttribute("data-pcx-hover", on);
  el.toggleAttribute("data-pcx-fired", on && !!h.fired);
  el.style.setProperty("--press", on ? h.progress : 0);
}));
`;

function srcdoc(w, token) {
  const nonce = crypto.randomUUID().replaceAll("-", "");
  const csp = `default-src 'none'; img-src https: data: blob:; style-src 'unsafe-inline'; font-src data:; script-src 'nonce-${nonce}'`;
  const p = w.params || {};
  return (
    `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}">` +
    `<script nonce="${nonce}">${INNER(token)}</script><style>${BASE_CSS}</style><style>${p.css ?? ""}</style></head>` +
    `<body>${p.html ?? ""}</body></html>`
  );
}

export const isHtml = (w) => w.type === "effect" && w.shader === "html";

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
  constructor(host, onPress) {
    this.host = host;
    this.frames = new Map(); // id → {el, key, token, widget, rect, rects}
    addEventListener("message", (e) => {
      const f = [...this.frames.values()].find((x) => x.el.contentWindow === e.source && x.token === e.data?.pcx);
      if (!f) return;
      if (e.data.rects) f.rects = e.data.rects;
      if (e.data.press) {
        const t = this.targets().find((x) => x.widget.id === f.widget.id && x.name === e.data.press);
        onPress(f.widget, String(e.data.press), t ? [t.box[0] + t.box[2] / 2, t.box[1] + t.box[3] / 2] : null);
      }
    });
  }

  /** Monta, actualiza o quita iframes según el mundo. `s`: px de CSS por mm; (ox, oy): origen de la ventana en la superficie. */
  sync(world, s, ox = 0, oy = 0) {
    const wanted = new Map((world.widgets || []).filter(isHtml).map((w) => [w.id, w]));
    for (const [id, f] of this.frames) {
      if (wanted.has(id)) continue;
      f.el.remove();
      this.frames.delete(id);
    }
    for (const [id, w] of wanted) {
      const key = JSON.stringify([w.params?.html, w.params?.css]);
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
        f.el.srcdoc = srcdoc(w, f.token);
      }
      f.widget = w;
      f.rect = htmlRect(w, world);
      const [x, y, rw, rh] = f.rect;
      const rot = Number(w.params?.rotate_deg) || 0;
      Object.assign(f.el.style, {
        width: `${rw}px`,
        height: `${rh}px`,
        zIndex: String(w.z ?? 0),
        opacity: String(w.params?.opacity ?? 1),
        transform: `translate(${x * s - ox}px, ${y * s - oy}px) scale(${s}) translate(${rw / 2}px, ${rh / 2}px) rotate(${rot}deg) translate(${-rw / 2}px, ${-rh / 2}px)`,
      });
    }
  }

  /** Zonas tocables en mm de la mesa: [{widget, name, box: [x, y, w, h]}], con la rotación ya aplicada. */
  targets() {
    const out = [];
    for (const f of this.frames.values()) {
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
