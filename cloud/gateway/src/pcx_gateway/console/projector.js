// Ventana del proyector: dibuja lo que manda la consola (mismo origen, BroadcastChannel) y le devuelve toques.
import { drawHands, drawScene, hit, trackTrails } from "./render.js";

const sid = new URLSearchParams(location.search).get("s");
const bus = new BroadcastChannel(`pcx-proj-${sid}`);
const canvas = document.getElementById("out");
const ctx = canvas.getContext("2d");
let world = null;
let pattern = true;
let calib = null;
let hands = null; // {hands, hover, progress, fired, opts} + at
const trails = [];
let bounds = new Map();

function size() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(innerWidth * dpr);
  canvas.height = Math.round(innerHeight * dpr);
  report();
}

function report() {
  bus.postMessage({
    t: "alive",
    w: canvas.width,
    h: canvas.height,
    fullscreen: !!document.fullscreenElement,
    screen: window.screen?.label || "",
  });
}

bus.onmessage = ({ data }) => {
  if (data.t === "world") {
    world = data.world;
    pattern = data.pattern;
    calib = data.calib;
  } else if (data.t === "hands") {
    hands = { ...data, at: performance.now() };
    trackTrails(trails, hands, hands.at);
  } else if (data.t === "close") window.close();
};

function frame(t) {
  if (world) {
    bounds = drawScene(ctx, canvas.width, canvas.height, world, t, { pattern, calib });
    const now = performance.now();
    if (hands && !calib && now - hands.at < 300) drawHands(ctx, canvas.width, world, hands, trails, bounds, now);
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
  const s = world.surface.width_mm / innerWidth;
  const pt = [e.clientX * s, e.clientY * s];
  const w = hit(world, bounds, pt);
  if (w) bus.postMessage({ t: "press", id: w.id, point_mm: pt });
});

size();
setInterval(report, 1500);
requestAnimationFrame(frame);
// Con el permiso de Window Management, Chrome abre el popup ya en pantalla completa; si no, queda el botón.
if (new URLSearchParams(location.search).get("auto")) fullscreen();
