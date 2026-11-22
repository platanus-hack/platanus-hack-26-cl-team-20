// Modo presentación: láminas a pantalla completa intercaladas con la mesa en vivo. Solo lectura, salvo cambiar de
// lámina: llega por el mismo WebSocket del visor (/view). Las vistas en vivo son el visor embebido en un iframe.
const $ = (id) => document.getElementById(id);
const token = location.pathname.split("/").pop();
let deck = { slides: [], index: 0 };
let online = false;
let socket = null;
let shown = null; // JSON de la lámina dibujada, para no rehacer el DOM (ni recargar imágenes) sin cambios

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
/** Texto de lámina: párrafos, líneas «- » como puntos y **negrita**. */
function body(text) {
  const out = [];
  let list = null;
  for (const raw of String(text).split("\n")) {
    const line = esc(raw.trim()).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    const item = /^[-*•]\s+(.*)$/.exec(line);
    if (item) (list ??= out[out.push({ items: [] }) - 1]).items.push(item[1]);
    else {
      list = null;
      if (line) out.push({ p: line });
    }
  }
  return out.map((b) => (b.p ? `<p>${b.p}</p>` : `<ul>${b.items.map((i) => `<li>${i}</li>`).join("")}</ul>`)).join("");
}

const DIAGRAM = `<meta name="color-scheme" content="dark"><style>:root{color-scheme:dark}html,body{height:100%;margin:0;background:transparent;color:#f1eee4;font:20px Archivo,system-ui,sans-serif}
body{display:grid;place-items:center}svg{max-width:100%;max-height:100vh}</style>`;

function aviso(title, text = "") {
  $("aviso").hidden = !title;
  $("aviso-titulo").textContent = title || "";
  $("aviso-texto").textContent = text;
}

function paint() {
  const { slides, index } = deck;
  const s = slides[index];
  if (!s) {
    aviso(online ? "Todavía no hay láminas" : "Esperando a la mesa…", online ? "Cárgalas en la mesa (paso «Presentación») o pídele al agente que use present." : "La consola de la mesa tiene que estar abierta.");
    $("cuenta").textContent = "";
    $("avance").style.width = "0";
    return;
  }
  aviso(null);
  $("cuenta").textContent = `${index + 1} / ${slides.length}`;
  $("avance").style.width = `${((index + 1) / slides.length) * 100}%`;
  // El visor embebido se carga apenas hay alguna vista en vivo, para que el cambio sea inmediato.
  const vivo = $("vivo");
  if (slides.some((x) => x.live) && !vivo.src) vivo.src = `/ver/${token}?embebido=1`;
  if (s.live) {
    vivo.contentWindow?.postMessage({ pcxModo: s.live, cam: s.camera ?? null }, location.origin);
    vivo.removeAttribute("data-off");
  } else vivo.setAttribute("data-off", "");
  $("lamina").hidden = !!s.live;
  $("rotulo").hidden = !s.live || !s.title;
  $("rotulo").querySelector("span").textContent = s.title ?? "";
  const key = JSON.stringify(s);
  if (s.live || key === shown) return;
  shown = key;
  const media = s.image || s.html;
  const el = $("lamina");
  el.className = media ? (s.text ? "con-medio" : "solo-medio") : s.text ? "con-texto" : "";
  const words = `${s.title ? `<h1>${esc(s.title)}</h1>` : ""}${s.text ? `<div class="cuerpo">${body(s.text)}</div>` : ""}`;
  el.innerHTML = (media && s.text ? `<div>${words}</div>` : words) + (media ? `<div class="medio"></div>` : "");
  if (s.image) el.querySelector(".medio").append(Object.assign(new Image(), { src: s.image, alt: s.title ?? "" }));
  else if (s.html) {
    // Diagrama del agente: iframe sin scripts ni origen.
    const f = Object.assign(document.createElement("iframe"), { srcdoc: DIAGRAM + s.html, title: s.title ?? "Diagrama" });
    f.setAttribute("sandbox", "");
    el.querySelector(".medio").append(f);
  }
}
$("vivo").addEventListener("load", paint); // recién cargado, el visor todavía no sabe qué vista mostrar

function go(to) {
  const { slides, index } = deck;
  const n = to === "next" ? index + 1 : to === "prev" ? index - 1 : to - 1;
  if (!slides.length || n < 0 || n >= slides.length || n === index) return;
  deck.index = n; // se dibuja al tiro; la mesa confirma después
  paint();
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "slide", to: n + 1 }));
}

const fullscreen = () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen({ navigationUI: "hide" }).catch(() => {}));
addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key;
  if (["ArrowRight", "ArrowDown", "PageDown", " ", "Enter"].includes(k)) go("next");
  else if (["ArrowLeft", "ArrowUp", "PageUp", "Backspace"].includes(k)) go("prev");
  else if (k === "Home") go(1);
  else if (k === "End") go(deck.slides.length);
  else if (k.toLowerCase() === "f") fullscreen();
  else return;
  e.preventDefault();
});
$("tapa").onclick = (e) => go(e.clientX < innerWidth * 0.25 ? "prev" : "next");
setTimeout(() => $("ayuda").classList.add("ido"), 6000);

const on = {
  status({ connected }) {
    online = connected;
    paint();
  },
  deck(m) {
    deck = { slides: m.slides ?? [], index: m.index ?? 0 };
    paint();
  },
};

let delay = 500;
function connect() {
  const ws = (socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/view`));
  ws.onopen = () => (ws.send(JSON.stringify({ token, video: false })), (delay = 500));
  ws.onmessage = ({ data }) => {
    if (typeof data !== "string") return;
    const m = JSON.parse(data);
    on[m.kind]?.(m);
  };
  ws.onclose = (e) => {
    if (e.code === 4401) return aviso("Este enlace no es válido o ya venció.");
    if (e.code === 4429) return aviso("Hay demasiada gente mirando esta mesa.", "Prueba en un rato.");
    setTimeout(connect, delay);
    delay = Math.min(delay * 2, 8000);
  };
}
connect();
