// Ventana del proyector: dibuja lo que manda la consola (mismo origen, BroadcastChannel) y le devuelve toques.
import { HtmlLayer } from "./html-layer.js";
import { drawHands, drawScene, drawUi, hit, trackTrails } from "./render.js";

const sid = new URLSearchParams(location.search).get("s");
const bus = new BroadcastChannel(`pcx-proj-${sid}`);
const canvas = document.getElementById("out");
const ctx = canvas.getContext("2d");
const top = document.getElementById("top");
const tctx = top.getContext("2d");
const layer = new HtmlLayer(document.getElementById("html"), (w, name, point_mm) => bus.postMessage({ t: "press", id: w.id, value: name, point_mm }));
// La superficie (mm) es la pantalla completa del proyector. Si la ventana no está en pantalla completa (barra de menú,
// Dock), `geo` dice dónde cae la ventana dentro de esa pantalla para dibujar corrido y que nada se mueva de su lugar.
let details = null;
let geo = { sw: innerWidth, sh: innerHeight, ox: 0, oy: 0, exact: true };
function measure() {
  const cs = details?.currentScreen;
  const frame = (outerWidth - innerWidth) / 2;
  const next = document.fullscreenElement
    ? { sw: innerWidth, sh: innerHeight, ox: 0, oy: 0, exact: true }
    : cs
      ? { sw: cs.width, sh: cs.height, ox: screenX - cs.left + frame, oy: screenY - cs.top + (outerHeight - innerHeight) - frame, exact: true }
      : { sw: screen.width, sh: screen.height, ox: screenX - (screen.availLeft ?? 0) + frame, oy: screen.height - screen.availHeight + (outerHeight - innerHeight) - frame, exact: false };
  const changed = JSON.stringify(next) !== JSON.stringify(geo);
  geo = next;
  return changed;
}
const scale = () => geo.sw / (world?.surface.width_mm ?? 1000); // px de CSS por mm
const syncHtml = () => world && layer.sync(calib ? { ...world, widgets: [] } : world, scale(), geo.ox, geo.oy);
let world = null;
let pattern = true;
let calib = null;
let textScale = 1;
let ui = null; // franja de pestañas y botón de voz
let uiBounds = new Map();
let hands = null; // {hands, hover, progress, fired, opts} + at
const trails = [];
let bounds = new Map();

function size() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(innerWidth * dpr);
  canvas.height = Math.round(innerHeight * dpr);
  top.width = canvas.width;
  top.height = canvas.height;
  measure();
  syncHtml();
  report();
}

function report() {
  const dpr = window.devicePixelRatio || 1;
  if (measure()) syncHtml();
  bus.postMessage({
    t: "alive",
    w: Math.round(geo.sw * dpr), // la pantalla completa, no la ventana
    h: Math.round(geo.sh * dpr),
    window: [canvas.width, canvas.height],
    fullscreen: !!document.fullscreenElement,
    exact: geo.exact,
  });
}

bus.onmessage = ({ data }) => {
  if (data.t === "world") {
    world = data.world;
    pattern = data.pattern;
    calib = data.calib;
    textScale = data.textScale;
    ui = data.ui;
    syncHtml();
  } else if (data.t === "hands") {
    hands = { ...data, at: performance.now() };
    trackTrails(trails, hands, hands.at);
    layer.hover(data.hover, data.progress, data.fired);
  } else if (data.t === "close") window.close();
};

function frame(t) {
  if (world) {
    const dpr = window.devicePixelRatio || 1;
    const [W, H, off] = [geo.sw * dpr, geo.sh * dpr, [geo.ox * dpr, geo.oy * dpr]];
    bounds = drawScene(ctx, W, H, world, t, { pattern, calib, textScale, offset: off });
    const now = performance.now();
    tctx.setTransform(1, 0, 0, 1, 0, 0);
    tctx.clearRect(0, 0, top.width, top.height); // las manos van en un canvas encima del html
    tctx.translate(-off[0], -off[1]);
    uiBounds = calib ? new Map() : drawUi(tctx, W, world, ui, t);
    if (hands && !calib && now - hands.at < 300) drawHands(tctx, W, world, hands, trails, new Map([...bounds, ...uiBounds]), now);
  }
  requestAnimationFrame(frame);
}

async function fullscreen() {
  if (document.fullscreenElement) return document.exitFullscreen();
  try {
    await document.documentElement.requestFullscreen({ navigationUI: "hide" });
  } catch (err) {
    console.warn(err);
  }
}

document.getElementById("full").onclick = (e) => (e.stopPropagation(), fullscreen());
addEventListener("keydown", (e) => e.key.toLowerCase() === "f" && fullscreen());
addEventListener("fullscreenchange", report);
addEventListener("resize", size);
addEventListener("beforeunload", () => bus.postMessage({ t: "closed" }));
canvas.addEventListener("click", (e) => {
  if (!world) return;
  const pt = [(e.clientX + geo.ox) / scale(), (e.clientY + geo.oy) / scale()];
  const tab = [...uiBounds].find(([, r]) => pt[0] >= r[0] && pt[0] <= r[0] + r[2] && pt[1] >= r[1] && pt[1] <= r[1] + r[3])?.[0];
  if (tab) return bus.postMessage({ t: "press", id: tab, point_mm: pt });
  const w = hit(world, bounds, pt);
  if (w) bus.postMessage({ t: "press", id: w.id, point_mm: pt });
});

size();
setInterval(report, 1000);
// Con el permiso de Window Management se sabe dónde está la ventana dentro de su pantalla.
if ("getScreenDetails" in window)
  navigator.permissions
    .query({ name: "window-management" })
    .then((p) => p.state === "granted" && window.getScreenDetails())
    .then((d) => d && ((details = d), report()))
    .catch(() => {});
requestAnimationFrame(frame);
// Con el permiso de Window Management, Chrome abre el popup ya en pantalla completa; si no, queda el botón.
if (new URLSearchParams(location.search).get("auto")) fullscreen();
