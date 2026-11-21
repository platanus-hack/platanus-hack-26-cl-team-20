// Visor de solo lectura: muestra en vivo la cámara cenital, lo proyectado y lo que hace el agente.
// Todo llega por un WebSocket (/view) que el gateway alimenta con lo que manda el nodo navegador.
import { HtmlLayer } from "/html-layer.js";
import { drawHands, drawScene, trackTrails } from "/render.js";

const $ = (id) => document.getElementById(id);
const token = location.pathname.split("/").pop();
const cam = $("camara");
const cctx = cam.getContext("2d");
const previa = $("previa");
const pctx = previa.getContext("2d");
const ptop = $("previa-top");
const tctx = ptop.getContext("2d");
const layer = new HtmlLayer($("previa-html"), () => {});
const trails = [];
let view = null; // {world, pattern, textScale, calib}
let hands = { hands: [], at: 0 };
let frames = 0;
let lastCall = 0;

function status(state, text) {
  $("estado").dataset.state = state;
  $("estado-texto").textContent = text;
}

const syncHtml = () =>
  view && layer.sync(view.calib ? { ...view.world, widgets: [] } : view.world, previa.getBoundingClientRect().width / view.world.surface.width_mm);
new ResizeObserver(syncHtml).observe(previa);

function row(a) {
  const li = document.createElement("li");
  li.className = a.ok ? "" : "mal";
  li.innerHTML = `<time></time><b></b><span></span><small></small>`;
  li.querySelector("time").textContent = a.time;
  li.querySelector("b").textContent = a.tool;
  li.querySelector("span").textContent = li.querySelector("span").title = a.brief;
  li.querySelector("small").textContent = `${a.ms} ms`;
  return li;
}

const on = {
  status({ connected }) {
    status(connected ? "online" : "offline", connected ? "En vivo" : "La mesa está desconectada; esperando…");
  },
  world(m) {
    view = m;
    const { width_mm, height_mm } = m.world.surface;
    $("previa-mm").textContent = `· ${width_mm} × ${height_mm} mm`;
    syncHtml();
  },
  hands(m) {
    hands = { ...m, at: performance.now() };
    trackTrails(trails, hands, hands.at);
    layer.hover(m.hover, m.progress, m.fired);
  },
  activity(m) {
    if (m.all) $("actividad").replaceChildren(...m.all.map(row));
    else $("actividad").prepend(row(m.entry));
    while ($("actividad").children.length > 60) $("actividad").lastChild.remove();
    if (m.entry || m.all.length) lastCall = Date.now();
    $("agente-estado").textContent = lastCall ? "Agente operando" : "Esperando al agente…";
    $("agente-estado").classList.toggle("vivo", !!lastCall);
  },
};

async function showFrame(blob) {
  const img = await createImageBitmap(blob);
  if (cam.width !== img.width || cam.height !== img.height) Object.assign(cam, { width: img.width, height: img.height });
  cctx.drawImage(img, 0, 0);
  img.close();
  frames++;
}

// Descargar foto: se le pide al nodo el cuadro actual a resolución completa y llega como mensaje `photo`.
let socket = null;
let wantPhoto = false;
const fotoText = (t) => (($("foto").textContent = t), setTimeout(() => ($("foto").textContent = "Descargar foto"), 2500));
$("foto").onclick = () => {
  if (socket?.readyState !== WebSocket.OPEN) return fotoText("Sin conexión");
  wantPhoto = true;
  socket.send(JSON.stringify({ type: "photo" }));
  $("foto").textContent = "Pidiendo foto…";
  setTimeout(() => wantPhoto && ((wantPhoto = false), fotoText("La mesa no respondió")), 8000);
};
on.photo = async (m) => {
  if (!wantPhoto) return; // la pidió otra persona
  wantPhoto = false;
  if (m.error) return fotoText(m.error);
  const blob = await (await fetch(m.data)).blob();
  const stamp = new Date().toLocaleString("sv").replace(/[-: ]/g, "").replace(/(\d{8})(\d{6})/, "$1-$2");
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `pcx-${stamp}.jpg` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  fotoText(`Foto ${m.size.join("×")} descargada`);
};

let delay = 500;
function connect() {
  const ws = (socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/view`));
  ws.binaryType = "blob";
  ws.onopen = () => (ws.send(JSON.stringify({ token })), (delay = 500));
  ws.onmessage = ({ data }) => {
    if (typeof data !== "string") return showFrame(data);
    const m = JSON.parse(data);
    on[m.kind]?.(m);
  };
  ws.onclose = (e) => {
    if (e.code === 4401) return status("offline", "Este enlace no es válido o ya venció.");
    if (e.code === 4429) return status("offline", "Hay demasiada gente mirando esta mesa. Prueba en un rato.");
    status("offline", "Sin conexión; reintentando…");
    setTimeout(connect, delay);
    delay = Math.min(delay * 2, 8000);
  };
}

function frame(t) {
  if (view) {
    const { width_mm, height_mm } = view.world.surface;
    const h = Math.round((previa.width * height_mm) / width_mm);
    if (previa.height !== h) previa.height = ptop.height = h;
    const bounds = drawScene(pctx, previa.width, previa.height, view.world, t, view);
    tctx.clearRect(0, 0, ptop.width, ptop.height);
    if (!view.calib && performance.now() - hands.at < 300) drawHands(tctx, ptop.width, view.world, hands, trails, bounds, performance.now());
  }
  requestAnimationFrame(frame);
}

setInterval(() => {
  $("camara-nota").textContent = frames ? `· ${frames} cuadros/s` : "· sin video";
  frames = 0;
}, 1000);
connect();
requestAnimationFrame(frame);
