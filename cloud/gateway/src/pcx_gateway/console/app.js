// Consola de WUI (la mesa): detecta cámaras y monitores, lanza la ventana del proyector y entrega el comando para un agente.
import { BrowserNode } from "./node.js";
import { pxToMm, solveH } from "./calib.js";
import { HandTracker } from "./hands.js";
import { HtmlLayer, isLive } from "./html-layer.js";
import { Motor, motorSettings } from "./motor.js";
import { Voice } from "./voice.js";
import { Spectrum } from "./audio.js";
import { drawHands, drawScene, drawUi, hit, trackTrails, uiPageOf, uiPages } from "./render.js";
import { loadWuiFont } from "./wui-brand.js";

loadWuiFont();

const $ = (id) => document.getElementById(id);
const store = {
  get: (k) => JSON.parse(localStorage.getItem(`pcx.${k}`) || "null"),
  set: (k, v) => localStorage.setItem(`pcx.${k}`, JSON.stringify(v)),
};

// ------------------------------------------------------------------ sesión
// Un agente puede crear la sesión (POST /api/sessions) y abrir /mesa#s=<session_id>.<node_token>.<user_token>:
// la mesa la adopta y borra el fragmento (nunca viaja al servidor).
const given = /^#s=([0-9a-f]+)\.([\w-]+)\.([\w-]+)$/.exec(location.hash);
if (given) {
  store.set("session", { session_id: given[1], node_id: `web-${given[1]}`, node_token: given[2], user_token: given[3] });
  history.replaceState(null, "", location.pathname + location.search);
}
async function session(fresh = false) {
  let s = fresh ? null : store.get("session");
  if (!s) {
    const res = await fetch("/api/sessions", { method: "POST" });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
    s = await res.json();
    store.set("session", s);
  }
  return s;
}

let ses;
try {
  ses = await session();
} catch (err) {
  $("estado").dataset.state = "offline";
  $("estado-texto").textContent = `Sin sesión: ${err.message}`;
  throw err;
}

const bus = new BroadcastChannel(`pcx-proj-${ses.session_id}`);
const proj = { win: null, alive: 0, w: 0, h: 0, fullscreen: false };
const projectorOpen = () => Date.now() - proj.alive < 4000;

// ------------------------------------------------------------------ cámaras
const cams = new Map(); // deviceId → {id, deviceId, label, stream, video, el}
const camIds = store.get("camIds") || {};
let mainCam = store.get("mainCam"); // solo se guarda cuando la persona elige la cenital
let camChosen = !!mainCam;

/** Menor es mejor para cenital: webcam externa, después un teléfono o cámara virtual, al final la del notebook. */
const camRank = (label) =>
  /facetime|built-?in|integrad|isight|macbook|notebook|internal/i.test(label) ? 2 : /iphone|ipad|continuity|virtual|obs|desk view/i.test(label) ? 1 : 0;

function camId(deviceId) {
  if (!camIds[deviceId]) {
    camIds[deviceId] = `cam-${Object.keys(camIds).length + 1}`;
    store.set("camIds", camIds);
  }
  return camIds[deviceId];
}

function addCam(deviceId, label, stream, video = Object.assign(document.createElement("video"), { autoplay: true, muted: true, playsInline: true, srcObject: stream })) {
  const el = document.createElement("figure");
  el.className = "camara";
  el.innerHTML = `<figcaption><div><strong></strong><small></small></div><button></button></figcaption>`;
  el.prepend(video);
  el.querySelector("strong").textContent = label || "Cámara";
  el.querySelector("button").onclick = () => {
    mainCam = deviceId;
    camChosen = true;
    store.set("mainCam", mainCam);
    paintCams();
  };
  video.onloadedmetadata = paintCams;
  if (stream) stream.getVideoTracks()[0].onended = () => refreshCams();
  $("camaras").append(el);
  // Un celular (sin stream) lleva id «cel-N»: su imagen llega por el gateway y se dibuja en un canvas.
  cams.set(deviceId, { id: stream ? camId(deviceId) : deviceId.replace("phone", "cel"), deviceId, label: label || "Cámara", stream, video, el });
}

// Celulares que mandan su cámara (QR → /cam/<token>): cada uno es una cámara más de la mesa.
function phoneCanvas() {
  const c = Object.assign(document.createElement("canvas"), { width: 16, height: 9 });
  let ready = false;
  Object.defineProperties(c, { videoWidth: { get: () => (ready ? c.width : 0) }, videoHeight: { get: () => (ready ? c.height : 0) } });
  c.show = (img) => {
    if (c.width !== img.width || c.height !== img.height) Object.assign(c, { width: img.width, height: img.height });
    c.getContext("2d").drawImage(img, 0, 0);
    if (!ready) (ready = true), paintCams();
  };
  return c;
}
function setPhones(list) {
  const want = new Map(list.map((p) => [`phone-${p.slot}`, p]));
  for (const [deviceId, c] of cams) {
    if (c.stream || want.has(deviceId)) continue;
    c.el.remove();
    cams.delete(deviceId);
  }
  for (const [deviceId, p] of want) if (!cams.has(deviceId)) addCam(deviceId, `Celular ${p.slot} · ${p.label}`, null, phoneCanvas());
  paintCams();
}
async function phoneFrame({ slot, blob }) {
  const cam = cams.get(`phone-${slot}`);
  if (!cam || cam.decoding) return; // si no se alcanza a decodificar, se salta el cuadro
  cam.decoding = true;
  try {
    const img = await createImageBitmap(blob);
    cam.video.show(img);
    img.close();
  } catch {
  } finally {
    cam.decoding = false;
  }
}

function paintCams() {
  if (!cams.has(mainCam)) camChosen = false;
  // Un celular nunca se elige solo como cenital: llega y se va, y cambiar la cenital rompe la calibración.
  if (!camChosen) mainCam = [...cams.values()].filter((c) => c.stream).sort((a, b) => camRank(a.label) - camRank(b.label))[0]?.deviceId ?? cams.keys().next().value;
  for (const c of cams.values()) {
    const main = c.deviceId === mainCam;
    c.el.classList.toggle("principal", main);
    c.el.querySelector("small").textContent = `${c.id} · ${c.video.videoWidth || "–"}×${c.video.videoHeight || "–"}`;
    c.el.querySelector("button").textContent = main ? "Cenital" : "Usar como cenital";
  }
  const n = cams.size;
  $("camaras-nota").textContent = n
    ? `${n} ${n === 1 ? "cámara conectada" : "cámaras conectadas"}. La cenital es la que mira la mesa: por ella mira el agente y se siguen las manos.`
    : "No hay cámaras conectadas. Enchufa una y aparece sola.";
  ensureCalib();
  syncDevices();
}

async function refreshCams() {
  $("camaras-activar").hidden = true;
  try {
    if (![...cams.values()].some((c) => c.stream)) {
      // La primera vez pide el permiso; con eso el navegador entrega nombres e ids reales.
      const first = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1920 }, height: { ideal: 1080 } } });
      const track = first.getVideoTracks()[0];
      addCam(track.getSettings().deviceId, track.label, first);
    }
    const found = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput" && d.deviceId);
    for (const [deviceId, c] of cams) {
      if (!c.stream || (found.some((d) => d.deviceId === deviceId) && c.stream.active)) continue;
      c.stream.getTracks().forEach((t) => t.stop());
      c.el.remove();
      cams.delete(deviceId);
    }
    for (const d of found) {
      if (cams.has(d.deviceId)) continue;
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { deviceId: { exact: d.deviceId }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        });
        addCam(d.deviceId, d.label, stream);
      } catch (err) {
        console.warn("cámara", d.label, err);
      }
    }
    paintCams();
  } catch (err) {
    const denied = err.name === "NotAllowedError";
    $("camaras-nota").textContent = denied
      ? "El navegador no tiene permiso para la cámara. Dáselo en el candado de la barra de direcciones y aprieta el botón."
      : err.name === "NotFoundError"
        ? "No hay cámaras conectadas. Enchufa una y aparece sola."
        : `No se pudo abrir la cámara: ${err.message}`;
    $("camaras-activar").hidden = false;
    syncDevices();
  }
}

// `?sim=1`: cámara simulada que «ve» la proyección rotada 180°, para probar sin proyector ni cámara.
const SIM = new URLSearchParams(location.search).has("sim");
const simCam = { id: "cam-sim", kind: "camera", adapter: "sim", caps: ["frames"], status: "online", meta: { label: "Cámara simulada", main: true } };
const SIM_HIDE = (new URLSearchParams(location.search).get("tapa") || "").split(";").filter(Boolean).map((r) => r.split(",").map(Number));
let calibDots = null;
let motorBlank = null; // id del blank del motor local en curso: la vista previa y la cámara simulada también en negro
// `?sim=ruido`: además agrega objetos, un foco y una «mano» que se mueve, para probar calibrate en condiciones malas.
const SIM_NOISE = new URLSearchParams(location.search).get("sim") === "ruido";
function simNoise(scene) {
  const ctx = scene.getContext("2d");
  const t = performance.now() / 1000;
  ctx.globalCompositeOperation = "lighter";
  const g = ctx.createRadialGradient(560, 120, 10, 560, 120, 260);
  g.addColorStop(0, "#ffffffcc");
  g.addColorStop(1, "#ffffff00");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, scene.width, scene.height); // foco de luz
  ctx.globalCompositeOperation = "source-over";
  ctx.fillStyle = "#7a4a2a";
  ctx.fillRect(90, 60, 110, 80); // caja
  ctx.fillStyle = "#dddddd";
  (ctx.beginPath(), ctx.arc(400, 300, 45, 0, 7), ctx.fill()); // taza: tapa la marca del centro
  ctx.fillStyle = "#c79a7a";
  ctx.fillRect(300 + 250 * Math.sin(t * 1.7), 330 + 40 * Math.cos(t * 2.3), 90, 160); // mano moviéndose
}
let textScale = store.get("textScale") || 1;

function simFrame() {
  const { width_mm, height_mm } = node.world.surface;
  const scene = Object.assign(document.createElement("canvas"), { width: 800, height: Math.round((800 * height_mm) / width_mm) });
  drawScene(scene.getContext("2d"), scene.width, scene.height, node.world, performance.now(), { calib: motorBlank ? { dots: [] } : calibDots });
  if (SIM_NOISE) simNoise(scene);
  // `&tapa=x0,y0,x1,y1;…` (mm): zonas de la proyección que la cámara simulada no ve.
  const sctx = scene.getContext("2d");
  sctx.fillStyle = "#2a2a2a";
  for (const r of SIM_HIDE) sctx.fillRect(r[0] * 0.8, r[1] * 0.8, (r[2] - r[0]) * 0.8, (r[3] - r[1]) * 0.8);
  const c = Object.assign(document.createElement("canvas"), { width: 1280, height: 720 });
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#2a2a2a";
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.translate(1040, 360 + scene.height / 2);
  ctx.rotate(Math.PI);
  ctx.drawImage(scene, 0, 0);
  ctx.setTransform(1, 0, 0, 1, 0, 0); // quien anote encima (look) dibuja en píxeles de la foto
  return { canvas: c, camera: simCam };
}

/** Cuadro actual de una cámara como canvas (máx. 1280 px de ancho): es el marco de píxeles que ve el agente. */
async function camFrame(cameraId, maxWidth = 1280) {
  if (SIM && (!cameraId || cameraId === simCam.id)) return simFrame();
  const list = [...cams.values()];
  const cam = cameraId ? list.find((c) => c.id === cameraId) : cams.get(mainCam) || list[0];
  if (!cam) {
    throw new Error(
      cameraId
        ? `No existe la cámara ${cameraId}. Disponibles: ${list.map((c) => c.id).join(", ") || "ninguna"}.`
        : "No hay cámaras activas en la consola. Pídele a la persona que conecte una y dé permiso.",
    );
  }
  // ImageCapture saca el cuadro directo de la cámara: sirve aunque la pestaña de la consola esté tapada.
  let src = cam.video;
  if (cam.stream && "ImageCapture" in window) {
    const grab = new ImageCapture(cam.stream.getVideoTracks()[0]).grabFrame();
    src = (await Promise.race([grab, new Promise((r) => setTimeout(r, 1500))]).catch(() => null)) || cam.video;
  }
  const [vw, vh] = [src.videoWidth ?? src.width, src.videoHeight ?? src.height];
  if (!vw) throw new Error(`La cámara ${cam.id} todavía no entrega imagen; reintenta en un momento.`);
  const k = Math.min(1, maxWidth / vw);
  const c = Object.assign(document.createElement("canvas"), { width: Math.round(vw * k), height: Math.round(vh * k) });
  c.getContext("2d").drawImage(src, 0, 0, c.width, c.height);
  return { canvas: c, camera: device(cam) };
}

const device = (c) => ({
  id: c.id,
  kind: "camera",
  adapter: "browser",
  caps: ["frames"],
  status: "online",
  meta: { label: c.label, width: c.video.videoWidth, height: c.video.videoHeight, main: c.deviceId === mainCam, role: c.deviceId === mainCam ? "cenital" : "otra" },
});

// ------------------------------------------------------------------ nodo
const node = new BrowserNode(ses, {
  frame: camFrame,
  projectorOpen: () => SIM || projectorOpen(),
  textScale: () => textScale,
  showCalib(dots) {
    calibDots = dots;
    pushWorld();
    syncHtml(); // durante la calibración el html se esconde también en la vista previa
  },
  progress: (text) => ($("calib-estado").textContent = `Calibrando. ${text}`),
  saveCalib: (calib) => store.set("calib", { session_id: ses.session_id, calib }),
  screenOn: () => !!screenStream,
  presentUrl: async () => `${location.origin}/presentar/${await viewToken()}`,
});
const saved = store.get("calib");
if (saved?.session_id === ses.session_id) node.setCalib(saved.calib);

// ------------------------------------------------------------------ motor local (Rust, en este computador)
// Protocolo motor v0.1: la consola le manda calibración, escena y manos, apaga el proyector para la foto limpia,
// publica sus objetos y reenvía sus eventos al agente. Sin motor (o sin emparejar), todo sigue como antes.
const motorCfg = motorSettings();
const busWaits = new Map(); // "blanked:id" | "unblanked:id" → resolver
function busWait(key, ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => (busWaits.delete(key), resolve(null)), ms);
    busWaits.set(key, (v) => (clearTimeout(timer), busWaits.delete(key), resolve(v)));
  });
}
const motor = new Motor({
  node,
  url: motorCfg.url,
  token: motorCfg.token,
  sessionId: ses.session_id,
  hooks: {
    /** Negro total en la ventana del proyector (y en la vista previa). Resuelve cuando el proyector ya lo pintó. */
    async blank(id, on, maxMs) {
      motorBlank = on ? id : motorBlank === id ? null : motorBlank;
      if (projectorOpen()) {
        const reply = busWait(`${on ? "blanked" : "unblanked"}:${id}`, on ? maxMs : 1500);
        bus.postMessage({ t: "blank", id, on, max_ms: maxMs });
        return reply;
      }
      if (!SIM) return null; // sin ventana del proyector no hay cómo apagarlo ni confirmarlo
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); // la cámara simulada ve la vista previa
      return Date.now() * 1e6;
    },
    frame: () => camFrame(),
    note: (kind, text) => note(kind, text),
  },
});
node.hooks.motorLook = (args) => motor.look(args);

function syncDevices() {
  const open = projectorOpen();
  node.setDevices([
    ...(SIM ? [simCam] : []),
    ...[...cams.values()].map(device),
    {
      id: "proj-1",
      kind: "projector",
      adapter: "browser-canvas",
      caps: ["project", "touch"],
      status: open ? "online" : "offline",
      meta: open ? { width_px: proj.w, height_px: proj.h, fullscreen: proj.fullscreen } : {},
    },
  ]);
}

const STATES = {
  connecting: "Conectando…",
  online: `En línea · ${ses.node_id}`,
  offline: "Sin conexión con el gateway; reintentando…",
  replaced: "Esta sesión se abrió en otra pestaña",
};
node.addEventListener("status", async ({ detail }) => {
  if (detail === "invalid") {
    // El gateway ya no conoce la sesión (p. ej. base de datos nueva): se pide otra.
    localStorage.removeItem("pcx.session");
    return location.reload();
  }
  $("estado").dataset.state = detail;
  $("estado-texto").textContent = STATES[detail];
  $("retomar").hidden = detail !== "replaced";
});
$("retomar").onclick = () => node.connect();

// ------------------------------------------------------------------ proyector
let screens = null;
let chosen = null;

function paintScreens() {
  const box = $("pantallas");
  box.replaceChildren();
  if (!screens) return;
  const list = screens.screens;
  // La pantalla del proyector se recuerda por nombre; si no está, la primera que no sea la de la consola.
  if (!chosen || !list.includes(chosen))
    chosen = list.find((s) => s.label && s.label === store.get("screenLabel") && s !== screens.currentScreen) || list.find((s) => s !== screens.currentScreen) || list[0];
  for (const s of list) {
    const b = document.createElement("button");
    b.className = "pantalla";
    b.setAttribute("aria-pressed", s === chosen);
    const tags = [`${s.width}×${s.height}`, s.isInternal ? "integrada" : "externa", s === screens.currentScreen ? "aquí está la consola" : ""];
    b.innerHTML = `<strong></strong><small></small>`;
    b.querySelector("strong").textContent = s.label || "Pantalla";
    b.querySelector("small").textContent = tags.filter(Boolean).join(" · ");
    b.onclick = () => ((chosen = s), store.set("screenLabel", s.label || null), paintScreens());
    box.append(b);
  }
  $("proyector-nota").textContent =
    list.length > 1 ? "Elige la pantalla donde está el proyector." : "Solo veo una pantalla. Conecta el proyector y aparece sola.";
}

async function detectScreens() {
  try {
    screens = await window.getScreenDetails();
    screens.onscreenschange = paintScreens;
    screens.oncurrentscreenchange = paintScreens;
    $("detectar").hidden = true;
    paintScreens();
  } catch {
    $("detectar").hidden = false;
    $("proyector-nota").textContent = "Sin permiso para ver los monitores: se abre una ventana y la arrastras al proyector.";
  }
}

if ("getScreenDetails" in window) {
  const perm = await navigator.permissions.query({ name: "window-management" }).catch(() => null);
  if (perm?.state === "granted") detectScreens();
  else {
    $("detectar").hidden = false;
    $("proyector-nota").textContent = "Aprieta «Detectar monitores» para elegir la pantalla del proyector.";
  }
} else {
  $("proyector-nota").textContent =
    "Este navegador no lista monitores (usa Chrome o Edge). Igual puedes abrir la ventana y arrastrarla al proyector.";
}
$("detectar").onclick = detectScreens;

$("lanzar").onclick = async () => {
  if (!screens && "getScreenDetails" in window) await detectScreens();
  const s = chosen;
  const features = s
    ? `popup,left=${s.availLeft},top=${s.availTop},width=${s.availWidth},height=${s.availHeight},fullscreen`
    : "popup,width=1280,height=720";
  // Ventana nueva cada vez: si se reutiliza una abierta, el navegador ignora «fullscreen» y queda como ventana normal.
  bus.postMessage({ t: "close" });
  try {
    proj.win?.close();
  } catch {}
  const name = `pcx-projector-${Date.now()}`;
  store.set("projName", name);
  proj.win = window.open(`/projector.html?s=${ses.session_id}${s ? "&auto=1" : ""}`, name, features);
  if (!proj.win) $("proyector-estado").textContent = "El navegador bloqueó la ventana. Permite las ventanas emergentes para este sitio.";
};
$("cerrar").onclick = () => bus.postMessage({ t: "close" });
// El patrón de prueba se recuerda por sesión: si ya se apagó (a mano o porque el agente proyectó), sigue apagado al recargar.
const savedPattern = store.get("pattern");
if (savedPattern?.session_id === ses.session_id) $("patron").checked = savedPattern.on;
const keepPattern = () => store.set("pattern", { session_id: ses.session_id, on: $("patron").checked });
$("patron").onchange = () => (keepPattern(), pushWorld());
$("texto").value = String(textScale);
$("texto").onchange = () => {
  textScale = Number($("texto").value);
  store.set("textScale", textScale);
  pushWorld();
};
$("calibrar").onclick = async () => {
  $("calibrar").disabled = true;
  $("calib-aviso").hidden = true;
  $("calib-estado").textContent = "Calibrando: proyectando marcas verdes y buscándolas con la cámara (unos 20 s). Saca las manos de la mesa.";
  try {
    await node.calibrate({ mode: "quick" });
  } catch (err) {
    showCalibError(err);
  }
  $("calibrar").disabled = false;
  paintProjector();
};

// ------------------------------------------------------------------ mirar en vivo
const activityLog = []; // lo que hizo el agente, para quien llega a mirar
let streaming = null;
let encoding = false;
const shareCanvas = document.createElement("canvas");

// Interfaz propia de la mesa: franja de pestañas (escenas) y botón de voz, visibles en todas las escenas.
let voiceUi = { state: "idle", text: "" };
let uiPage = 0; // página de la barra de pestañas que se ve en la mesa
let uiSlide = null; // {from, at}: la barra se está deslizando desde esa página
const PAGE_DWELL_MS = 1200; // dedo quieto sobre «bajar» o «subir» para pasar de página (y sigue pasando si se queda)
const uiState = () => ({ enabled: node.ui.enabled, flip: node.ui.flip, scenes: node.scenes.map((s) => s.name), active: node.active, voice: voiceUi, page: uiPage, slide: uiSlide });
function setPage(to) {
  const last = uiPages(node.world.surface, node.scenes.map((s) => s.name)).pages.length - 1;
  to = Math.min(last, Math.max(0, to));
  if (to === uiPage) return;
  uiSlide = { from: uiPage, at: Date.now() };
  uiPage = to;
  pushWorld();
}
let uiBounds = new Map();
const uiAt = ([x, y]) => [...uiBounds].find(([, r]) => x >= r[0] && x <= r[0] + r[2] && y >= r[1] && y <= r[1] + r[3])?.[0];
// ------------------------------------------------------------------ botón de voz
// Mientras la mano (o el mouse) está sobre la esquina reservada, escucha; al salir, manda lo dicho al agente como
// evento flow_emitted{event: "voice_command", data: {text, scene}} (llega por events_wait).
// Umbrales del botón de voz con la mano (el clic no los usa: es deliberado).
const VOICE = {
  dwellMs: 600, // la mano quieta sobre el botón este tiempo antes de empezar a escuchar
  zone: 0.8, // radio activo como fracción del círculo dibujado (más chico que el dibujo)
  graceMs: 450, // cuadros sin la mano encima que se toleran antes de cortar
  minWords: 3, // con menos palabras no se manda
  minSeconds: 1.5, // con menos duración no se manda
};
const voice = new Voice((text) => ((voiceUi.text = text), pushWorld()), { forceAudio: new URLSearchParams(location.search).get("voz") === "audio" });
let voiceBusy = false;
async function voiceStart() {
  if (voiceBusy || voice.listening) return;
  voiceUi = { state: "listening", text: "" };
  pushWorld();
  await voice.start();
}
async function voiceStop() {
  if (!voice.listening || voiceBusy) return;
  voiceBusy = true;
  const r = await voice.stop();
  voiceBusy = false;
  const scene = node.active;
  const words = (r.text || "").trim().split(/\s+/).filter(Boolean).length;
  // Órdenes cortas permitidas («listo», «atrás»…): pasan aunque sean una palabra. La lista la fija el agente.
  const plain = (t) => t.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9ñ ]/g, "").replace(/\s+/g, " ").trim();
  const shortcut = !!r.text && node.voiceShortcuts.some((w) => plain(w) === plain(r.text));
  if (!r.error && !shortcut && voiceFrom === "hand" && (r.seconds < VOICE.minSeconds || (!r.audio && words < VOICE.minWords))) {
    // Una mano que pasó o una frase suelta de la conversación de al lado: no se le manda nada al agente.
    voiceUi = { state: "idle", text: "No alcancé a escucharte" };
    note("voz", `descartado (${r.seconds} s${r.text ? `, «${r.text}»` : ""}): muy corto para ser una orden`);
  } else if (r.error) {
    voiceUi = { state: "idle", text: r.error };
    note("voz", r.error);
  } else if (r.audio) {
    // Sin transcripción: el audio queda en esta consola y el evento avisa que está pendiente.
    node.emit({ type: "flow_emitted", event: "voice_command", data: { text: null, audio_pending: true, seconds: r.seconds, scene } });
    voiceUi = { state: "idle", text: `Audio de ${r.seconds} s guardado, sin transcribir` };
    const li = note("voz", `audio de ${r.seconds} s sin transcribir (este navegador no trae reconocimiento de voz)`);
    li.querySelector("small").replaceWith(Object.assign(document.createElement("a"), { href: URL.createObjectURL(r.audio), download: `voz-${Date.now()}.webm`, textContent: "audio" }));
  } else if (r.text) {
    node.emit({ type: "flow_emitted", event: "voice_command", data: { text: r.text, seconds: r.seconds, scene, ...(shortcut ? { shortcut: true } : {}) } });
    voiceUi = { state: "idle", text: `Enviado: ${r.text}` };
    note("voz", r.text);
  } else voiceUi = { state: "idle", text: r.seconds > 0.6 ? "No escuché nada" : "" };
  pushWorld();
  const shown = voiceUi;
  setTimeout(() => voiceUi === shown && ((voiceUi = { state: "idle", text: "" }), pushWorld()), 3500);
}
let voiceSeen = 0; // última vez que hubo una mano sobre el botón (los cuadros sin mano no cortan al tiro)
let voiceArm = 0; // desde cuándo la mano está quieta sobre el botón, antes de empezar a escuchar
/** Cuenta solo la punta del índice o el centro de la palma, dentro de un círculo más chico que el dibujado. */
function voiceOver(hands) {
  const r = uiBounds.get("pcx:voice");
  if (!r) return false;
  const [cx, cy, rad] = [r[0] + r[2] / 2, r[1] + r[3] / 2, (r[2] / 2 - 14) * VOICE.zone];
  const inside = ([x, y]) => Math.hypot(x - cx, y - cy) <= rad;
  return hands.some((h) => inside(h.mm[8]) || inside([(h.mm[0][0] + h.mm[9][0]) / 2, (h.mm[0][1] + h.mm[9][1]) / 2]));
}
function voiceHands(hands, now) {
  const over = voiceOver(hands);
  if (voice.listening) {
    if (over && voiceFrom === "hand") voiceSeen = now;
    else if (voiceFrom === "hand" && now - voiceSeen > VOICE.graceMs) voiceStop();
    return;
  }
  if (voiceBusy) return;
  if (over) {
    voiceSeen = now;
    if (!voiceArm) {
      voiceArm = now;
      voiceUi = { state: "arming", text: "", since: Date.now(), ms: VOICE.dwellMs }; // el anillo avanza solo en cada pantalla
      pushWorld();
    } else if (now - voiceArm >= VOICE.dwellMs) {
      voiceArm = 0;
      voiceFrom = "hand";
      voiceStart();
    }
  } else if (voiceArm && now - voiceSeen > 150) {
    voiceArm = 0; // se fue antes de tiempo: no pasó nada
    voiceUi = { state: "idle", text: "" };
    pushWorld();
  }
}
let voiceFrom = null;
setInterval(() => {
  const gone = performance.now() - voiceSeen;
  if (voice.listening && voiceFrom === "hand" && gone > 1200) voiceStop(); // la mano salió del cuadro
  if (voiceArm && gone > 600) ((voiceArm = 0), (voiceUi = { state: "idle", text: "" }), pushWorld());
}, 400);

function pressUi(id, by) {
  if (id === "pcx:voice") {
    // Clic (consola o ventana del proyector): un clic empieza a escuchar y otro manda.
    voiceFrom = "click";
    voiceArm = 0;
    return voice.listening ? voiceStop() : voiceStart();
  }
  if (id.startsWith("pcx:page:")) return setPage(uiPage + (id.endsWith("down") ? 1 : -1));
  if (id.startsWith("pcx:tab:")) {
    node.scenesTool({ action: "activate", name: id.slice(8) }, by);
    note("escena", `${id.slice(8)} (${by === "touch" ? "con el dedo" : by === "viewer" ? "desde el visor" : "desde la consola"})`);
  }
}

let streamCam = null; // deviceId elegido desde el visor; null = la cenital
const streamCamera = () => cams.get(streamCam) || cams.get(mainCam);
const viewState = () => ({
  cameras: SIM ? [{ id: simCam.id, label: simCam.meta.label, main: true }] : [...cams.values()].map((c) => ({ id: c.id, label: c.label, main: c.deviceId === mainCam })),
  stream_camera: SIM ? simCam.id : (streamCamera()?.id ?? null),
  world: node.world,
  pattern: $("patron").checked,
  textScale,
  calib: calibDots,
  ui: uiState(),
  // Para rectificar el video en el visor; solo si la calibración es de la cámara que se está mandando.
  homography: node.calib && (SIM || node.calib.camera_id === streamCamera()?.id) ? { matrix: node.calib.matrix, image_px: node.calib.image_px, lens: node.calib.lens ?? null, residual_mm: node.calib.residual_mm } : null,
});

// Calidad del video: parte en la mejor y baja un escalón solo si quien mira (o la subida) no da abasto.
const LEVELS = [[1280, 0.8], [960, 0.72], [640, 0.6]];
let level = 0;
let lastSlow = 0;
const floor = () => (node.viewers > 1 ? 1 : 0); // con varias personas mirando no se parte en 1280
function slower() {
  if (performance.now() - lastSlow < 3000) return;
  lastSlow = performance.now();
  level = Math.min(LEVELS.length - 1, level + 1);
}
node.addEventListener("slow", slower);

/** Video para quienes miran: la cenital a 640 px y ~9 cuadros/s, solo mientras haya alguien. */
function streamFrame() {
  if (encoding) return;
  const src = SIM ? simFrame().canvas : streamCamera()?.video;
  const [vw, vh] = [src?.videoWidth ?? src?.width, src?.videoHeight ?? src?.height];
  if (!vw) return;
  if (level > floor() && performance.now() - lastSlow > 15000) {
    level--; // 15 s sin atrasos: prueba un escalón mejor
    lastSlow = performance.now();
  }
  const [maxW, quality] = LEVELS[level];
  const w = Math.min(maxW, vw);
  Object.assign(shareCanvas, { width: w, height: Math.round((w * vh) / vw) });
  shareCanvas.getContext("2d").drawImage(src, 0, 0, shareCanvas.width, shareCanvas.height);
  encoding = true;
  shareCanvas.toBlob((blob) => ((encoding = false), blob && !node.sendFrame(blob) && slower()), "image/jpeg", quality);
}

node.addEventListener("viewers", ({ detail: n }) => {
  $("mirando").textContent = n
    ? `${n} ${n === 1 ? "persona está" : "personas están"} mirando en vivo.`
    : "Con el enlace para ver, otra persona mira en vivo la cámara, la proyección y lo que hace el agente. Solo mirar: no puede operar nada.";
  clearInterval(streaming);
  streaming = n ? setInterval(streamFrame, 110) : null;
  level = Math.max(level, floor());
  if (!n) {
    level = 0;
    streamCam = null;
  }
  if (n) {
    node.sendView("world", viewState()); // quien llega recibe el estado completo
    node.sendView("activity", { all: activityLog });
    sendDeck();
  }
});

// Quien mira pide una foto: cuadro actual de la cenital a resolución completa, sin anotar.
// Quien mira elige qué cámara ver (vale para todos los que miran; al quedar sin público vuelve a la cenital).
node.addEventListener("camera", ({ detail: id }) => {
  const cam = [...cams.values()].find((c) => c.id === id);
  streamCam = cam && cam.deviceId !== mainCam ? cam.deviceId : null;
  node.sendView("world", viewState());
});

node.addEventListener("photo", async () => {
  try {
    const { canvas } = SIM ? simFrame() : await camFrame(streamCamera()?.id, 4096);
    node.sendView("photo", { data: canvas.toDataURL("image/jpeg", 0.92), size: [canvas.width, canvas.height] });
  } catch (err) {
    node.sendView("photo", { error: err.message });
  }
});

/** Token de solo lectura de esta sesión (sirve para /ver y /presentar); se crea una vez. */
async function viewToken() {
  if (!ses.view_token) {
    const res = await fetch("/api/view-token", { method: "POST", headers: { Authorization: `Bearer ${ses.user_token}` } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    ses.view_token = (await res.json()).view_token;
    store.set("session", ses);
  }
  return ses.view_token;
}

$("compartir").onclick = async () => {
  try {
    await navigator.clipboard.writeText(`${location.origin}/ver/${await viewToken()}`);
    $("compartir").textContent = "Enlace copiado";
  } catch (err) {
    $("compartir").textContent = `No se pudo: ${err.message}`;
  }
  setTimeout(() => ($("compartir").textContent = "Copiar enlace para ver"), 1800);
};

function pushWorld() {
  node.sendView("world", viewState());
  bus.postMessage({ t: "world", world: node.world, pattern: $("patron").checked, calib: calibDots, textScale, ui: uiState() });
}

/** El motivo de un calibrate fallido queda a la vista hasta el próximo intento (también si lo pidió el agente). */
function showCalibError(err) {
  const marks = err.detail?.marks;
  $("calib-aviso").hidden = false;
  $("calib-aviso").textContent =
    `La calibración automática falló y sigue rigiendo la anterior. ${err.message}` +
    (marks?.length ? ` Marcas vistas (mm): ${marks.map((m) => m.mm.join(",")).join(" · ")}.` : "") +
    (err.detail?.unseen_mm?.length ? ` No vistas (mm): ${err.detail.unseen_mm.map((m) => m.join(",")).join(" · ")}.` : "");
}

function paintProjector() {
  const open = projectorOpen();
  $("cerrar").hidden = !open;
  $("lanzar").textContent = open ? "Relanzar" : "Lanzar proyección";
  $("proyector-estado").textContent = !open
    ? "Sin ventana de proyector. Mientras tanto, lo que proyecte el agente se ve en la vista previa."
    : proj.fullscreen
      ? `Proyectando a ${proj.w}×${proj.h} en pantalla completa.`
      : `Ventana abierta (${proj.window?.join("×")}) sobre una pantalla de ${proj.w}×${proj.h}.`;
  const warn = open && !proj.fullscreen;
  $("pantalla-aviso").hidden = !warn;
  if (warn)
    $("pantalla-aviso").textContent = proj.exact
      ? "La ventana del proyector no está en pantalla completa. Lo proyectado cae en su lugar, pero se corta lo que tapan la barra de menú y el Dock. Aprieta «Pantalla completa aquí» en esa ventana (o F)."
      : "La ventana del proyector no está en pantalla completa y no puedo medir dónde quedó: la calibración NO vale y todo cae corrido. Aprieta «Pantalla completa aquí» en esa ventana (o F).";
  const c = node.calib;
  if (c && !node.calibrating)
    $("calib-estado").textContent =
      c.source === "manual"
        ? `Calibración manual de 4 puntos sobre ${c.camera_id}. «Calibrar» la reemplaza por una automática.`
        : c.lens
          ? `Calibrada con ${c.camera_id} con modelo de lente: k1 ${c.lens.k1.toFixed(4)}, k2 ${c.lens.k2.toFixed(4)}, error ${c.residual_mm ?? "?"} mm${c.points ? `, ${c.points} puntos` : ""}.`
          : `Calibrada con ${c.camera_id}: ${c.points} de ${c.total ?? c.points} marcas, error ${c.residual_mm} mm. ${c.warning || "El agente ya puede apuntar en píxeles de la foto."}`;
  else if (!c && !node.calibrating)
    $("calib-estado").textContent = "Sin calibrar: el agente ve la foto, pero no sabe dónde cae cada punto de la mesa.";
  const { width_mm, height_mm } = node.world.surface;
  $("previa-mm").textContent = `· ${width_mm} × ${height_mm} mm`;
}

bus.onmessage = ({ data }) => {
  if (data.t === "alive") {
    const was = projectorOpen();
    Object.assign(proj, { alive: Date.now(), w: data.w, h: data.h, fullscreen: data.fullscreen, window: data.window, exact: data.exact });
    node.setSurface(data.w, data.h);
    if (!was) pushWorld();
    syncDevices();
    paintProjector();
    if (screenStream && !data.screen) giveScreen(); // ventana recién lanzada: se le pasa la pantalla compartida
  } else if (data.t === "closed") {
    proj.alive = 0;
    syncDevices();
    paintProjector();
  } else if (data.t === "blanked" || data.t === "unblanked") {
    busWaits.get(`${data.t}:${data.id}`)?.(data.t_ns ?? Date.now() * 1e6);
    if (data.t === "unblanked") {
      if (motorBlank === data.id) motorBlank = null;
      motor.projectorUnblanked(data.id); // si volvió sola por su tope de max_ms, se le avisa al motor
    }
  } else if (data.t === "audio-want") {
    audioWant.projector = data.n;
    syncAudio();
  } else if (data.t === "dead") {
    layer.kill(data.id, data.why); // el script se colgó en la ventana del proyector: se detiene también aquí
  } else if (data.t === "press") {
    if (data.id.startsWith("pcx:")) return pressUi(data.id, "console");
    const w = node.world.widgets.find((x) => x.id === data.id);
    if (w) node.press(w, data.point_mm, "api", data.value ?? undefined);
  }
};
setInterval(() => {
  if (proj.alive && !projectorOpen()) {
    proj.alive = 0;
    syncDevices();
  }
  paintProjector();
}, 2000);

// Vista previa: el mismo dibujo que la ventana del proyector.
const previa = $("previa");
const pctx = previa.getContext("2d");
const ptop = $("previa-top");
const tctx = ptop.getContext("2d");
// Los widgets con script corren una copia en cada pantalla (vista previa, proyector, visor): los eventos de wui.emit
// valen solo desde la ventana del proyector si está abierta, y si no desde esta vista previa.
const scriptErrors = new Map(); // widget → último aviso, para no inundar el registro
const layer = new HtmlLayer($("previa-html"), (w, name, pt, kind) => (kind === "emit" && projectorOpen() ? null : node.press(w, pt, "api", name)), {
  onDead(w, why) {
    note("script", `${w.id}: detenido (${why})`);
    node.emit({ type: "flow_emitted", event: "widget_killed", data: { widget_id: w.id, reason: why } });
    paintScripts();
  },
  onAudioWant: (n) => ((audioWant.preview = n), syncAudio()),
  onError(w, message) {
    if (performance.now() - (scriptErrors.get(w.id) ?? -1e9) < 3000) return;
    scriptErrors.set(w.id, performance.now());
    note("script", `${w.id}: ${message}`);
    node.emit({ type: "flow_emitted", event: "widget_error", data: { widget_id: w.id, message } });
  },
});
// ------------------------------------------------------------------ pantalla del computador en la mesa
// Un widget {type: "effect", shader: "screen", rect_mm} muestra en vivo la pantalla o ventana que la persona comparte
// aquí. El video va directo de esta página a la ventana del proyector: no viaja al gateway ni al visor.
let screenStream = null;
const screenVideo = Object.assign(document.createElement("video"), { muted: true, autoplay: true, playsInline: true });
function giveScreen() {
  try {
    if (proj.win && !proj.win.closed) proj.win.setScreen?.(screenStream);
  } catch (err) {
    console.warn("pantalla → proyector", err);
  }
}
function paintScreen(msg) {
  $("pantalla").textContent = screenStream ? "Dejar de compartir pantalla" : "Compartir pantalla";
  const wants = node.world.widgets.some((w) => w.type === "effect" && w.shader === "screen");
  $("pantalla-nota").textContent =
    msg ??
    (screenStream
      ? `Compartiendo ${screenVideo.videoWidth}×${screenVideo.videoHeight}. Se ve solo en esta consola y en la ventana del proyector: no viaja al servidor ni al enlace para ver.${wants ? "" : " El agente todavía no la ubicó en la mesa."}`
      : wants
        ? "El agente puso una pantalla en la mesa y está esperando: aprieta «Compartir pantalla» y elige qué mostrar."
        : "Muestra una pantalla o ventana de este computador sobre la mesa. Queda entre esta consola y el proyector: no viaja al servidor ni al enlace para ver.");
}
function stopScreen(by = "console") {
  if (!screenStream) return;
  screenStream.getTracks().forEach((t) => t.stop());
  screenStream = null;
  screenVideo.srcObject = null;
  giveScreen();
  node.emit({ type: "flow_emitted", event: "screen_share", data: { active: false, by } });
  note("pantalla", "dejó de compartirse");
  paintScreen();
}
$("pantalla").onclick = async () => {
  if (screenStream) return stopScreen();
  try {
    // Dentro del clic: recupera la ventana del proyector si esta consola se recargó (sin esto no hay cómo pasarle el video).
    if ((!proj.win || proj.win.closed) && projectorOpen()) proj.win = window.open("", store.get("projName") || "pcx-projector");
    screenStream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: { ideal: 30 } }, audio: false });
  } catch (err) {
    return paintScreen(err.name === "NotAllowedError" ? "No se compartió nada (se canceló o falta el permiso de grabación de pantalla del sistema)." : `No se pudo compartir: ${err.message}`);
  }
  screenStream.getVideoTracks()[0].onended = () => stopScreen("system"); // «Dejar de compartir» del navegador
  screenVideo.srcObject = screenStream;
  screenVideo.onloadedmetadata = () => paintScreen();
  await screenVideo.play().catch(() => {});
  giveScreen();
  const st = screenStream.getVideoTracks()[0].getSettings();
  node.emit({ type: "flow_emitted", event: "screen_share", data: { active: true, size: [st.width ?? 0, st.height ?? 0], surface: st.displaySurface ?? null } });
  note("pantalla", `compartiendo ${st.displaySurface ?? "pantalla"} ${st.width}×${st.height}`);
  paintScreen();
};

// Espectro del micrófono para los widgets que lo piden (wui.onAudio): se prende solo mientras alguno esté suscrito,
// aquí o en la ventana del proyector. Va de esta página al proyector; no pasa por el gateway ni llega al visor.
const audioWant = { preview: 0, projector: 0 };
const spectrum = new Spectrum(
  (frame) => (layer.audio(frame), audioWant.projector && bus.postMessage({ t: "audio", freq: frame.freq, level: frame.level, t_ms: frame.t, hz_per_band: frame.hz_per_band })),
  () => paintAudio(),
);
function syncAudio() {
  const on = audioWant.preview + (projectorOpen() ? audioWant.projector : 0) > 0;
  if (on && !spectrum.running) spectrum.start();
  if (!on && spectrum.running) spectrum.stop();
  paintAudio();
}
setInterval(syncAudio, 3000); // si se cerró la ventana del proyector, deja de pedirlo
function paintAudio() {
  const n = Math.max(audioWant.preview, audioWant.projector);
  $("audio-estado").hidden = !n && !spectrum.error;
  $("audio-estado").textContent = spectrum.error && n
    ? `Un widget pide el espectro del micrófono, pero ${spectrum.error}. Aprieta «Activar micrófono».`
    : `Micrófono abierto: ${n} ${n === 1 ? "widget recibe" : "widgets reciben"} el espectro (128 bandas y el nivel). El sonido no sale de este computador.`;
}

/** Lista de widgets con script en la consola, cada uno con su botón para matarlo. */
function paintScripts() {
  const live = node.world.widgets.filter(isLive);
  $("scripts").hidden = !live.length;
  $("scripts-lista").replaceChildren(
    ...live.map((w) => {
      const dead = layer.frames.get(w.id)?.dead;
      const b = Object.assign(document.createElement("button"), { className: "btn", textContent: dead ? `${w.id} · detenido · quitar` : `Matar ${w.id}` });
      b.onclick = () => {
        if (!dead) {
          layer.kill(w.id);
          bus.postMessage({ t: "kill", id: w.id });
        } else node.drop([w.id], node.global.widgets.some((g) => g.id === w.id) && !node.sceneOf().widgets.some((x) => x.id === w.id) ? "*" : undefined);
      };
      return b;
    }),
  );
}
const syncHtml = () =>
  (layer.hide(!!calibDots), layer.sync(node.world, previa.getBoundingClientRect().width / node.world.surface.width_mm));
new ResizeObserver(syncHtml).observe(previa);
let bounds = new Map();
function frame(t) {
  const { width_mm, height_mm } = node.world.surface;
  const h = Math.round((previa.width * height_mm) / width_mm);
  if (previa.height !== h) previa.height = h;
  bounds = drawScene(pctx, previa.width, previa.height, node.world, t, { pattern: $("patron").checked, calib: calibDots, textScale, screen: screenStream && screenVideo.videoWidth ? { source: screenVideo } : null });
  if (ptop.height !== h) ptop.height = h;
  tctx.clearRect(0, 0, ptop.width, ptop.height);
  uiBounds = calibDots ? new Map() : drawUi(tctx, ptop.width, node.world, uiState(), t);
  if (!calibDots && performance.now() - handView.at < 300)
    drawHands(tctx, ptop.width, node.world, handView, handTrails, new Map([...bounds, ...uiBounds]), performance.now());
  if (motorBlank) {
    tctx.fillStyle = "#000"; // foto limpia del motor: el proyector está en negro
    tctx.fillRect(0, 0, ptop.width, ptop.height);
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
previa.onclick = (e) => {
  const r = previa.getBoundingClientRect();
  const k = node.world.surface.width_mm / r.width;
  const pt = [(e.clientX - r.left) * k, (e.clientY - r.top) * k];
  const ui = uiAt(pt);
  if (ui) return pressUi(ui, "console");
  const w = hit(node.world, bounds, pt);
  if (w) node.press(w, pt);
};
node.addEventListener("world", () => (pushWorld(), paintProjector(), syncHands(), syncHtml(), paintScripts(), paintScreen()));

// ------------------------------------------------------------------ escenas (pestañas)
function paintScenes() {
  $("escenas").replaceChildren(
    ...node.scenes.map((s) => {
      const b = document.createElement("button");
      b.textContent = s.name;
      b.title = `${s.widgets.length} widgets`;
      b.classList.toggle("on", s.name === node.active);
      b.onclick = () => pressUi(`pcx:tab:${s.name}`, "console");
      return b;
    }),
  );
  $("escena-borrar").disabled = node.scenes.length < 2;
}
const sceneAction = (args) => {
  try {
    node.scenesTool(args, "console");
    $("escena-nombre").value = "";
    $("escena-error").textContent = "";
  } catch (err) {
    $("escena-error").textContent = err.message;
  }
};
$("escena-crear").onclick = () => sceneAction({ action: "create", name: $("escena-nombre").value });
$("escena-duplicar").onclick = () => sceneAction({ action: "duplicate", new_name: $("escena-nombre").value });
$("escena-borrar").onclick = () => sceneAction({ action: "delete" });
$("escena-nombre").onkeydown = (e) => e.key === "Enter" && $("escena-crear").click();
let uiActive = null;
node.addEventListener("scenes", () => {
  // Si cambió la escena activa (agente, consola o visor), la barra de la mesa muestra la página donde está.
  const names = node.scenes.map((s) => s.name);
  const last = uiPages(node.world.surface, names).pages.length - 1;
  if (node.active !== uiActive) ((uiActive = node.active), setPage(uiPageOf(node.world.surface, names, node.active)));
  if (uiPage > last) uiPage = last;
  paintScenes();
  pushWorld();
});
Object.assign(node.ui, store.get("ui") || {});
for (const [id, key] of [["ui-mesa", "enabled"], ["ui-girar", "flip"]]) {
  $(id).checked = node.ui[key];
  $(id).onchange = () => {
    node.ui[key] = $(id).checked;
    store.set("ui", { enabled: node.ui.enabled, flip: node.ui.flip });
    pushWorld();
    syncHands();
  };
}
paintScenes();
// Permiso del micrófono con un gesto de la persona, antes de que haga falta en la mesa.
const micReady = () => {
  $("voz-permiso").hidden = true;
  $("voz-estado").textContent = voice.transcribes
    ? "Micrófono listo. La mesa escucha mientras haya una mano sobre «Hablar» y transcribe con el reconocimiento de voz del navegador."
    : "Micrófono listo, pero este navegador no trae reconocimiento de voz: se graba el audio y el evento sale con audio pendiente. Usa Chrome para que transcriba.";
};
$("voz-permiso").onclick = async () => {
  try {
    (await navigator.mediaDevices.getUserMedia({ audio: true })).getTracks().forEach((t) => t.stop());
    micReady();
  } catch (err) {
    $("voz-estado").textContent = `No hay permiso para el micrófono (${err.name}). Dáselo en el candado de la barra de direcciones.`;
  }
};
$("voz-estado").textContent = voice.transcribes
  ? "Voz: aprieta «Activar micrófono» una vez para dar el permiso."
  : "Voz: este navegador no trae reconocimiento de voz; se grabará el audio sin transcribir. Usa Chrome para que transcriba.";
// Si el permiso del micrófono ya está dado (de una visita anterior), no hay que apretar nada al recargar.
navigator.permissions
  ?.query({ name: "microphone" })
  .then((p) => {
    if (p.state === "granted") micReady();
    p.onchange = () => (p.state === "granted" ? micReady() : ($("voz-permiso").hidden = false));
  })
  .catch(() => {});

// ------------------------------------------------------------------ calibración manual (por defecto)
// Medida a mano el 21-nov con la webcam cenital (foto de look a 1280×720). Vale hasta que corra calibrate.
const DEFAULT_PAIRS = "771, 602 > 100, 100\n322, 527 > 900, 100\n352, 318 > 900, 463\n803, 400 > 100, 463";
$("manual-pares").value = store.get("manualPairs") || DEFAULT_PAIRS;

function manualCalib(cameraId) {
  const rows = $("manual-pares").value.split("\n").map((l) => l.match(/-?\d+(\.\d+)?/g)?.map(Number)).filter((r) => r?.length === 4);
  const matrix = rows.length >= 4 && solveH(rows.map((r) => [r[0], r[1]]), rows.map((r) => [r[2], r[3]]));
  if (!matrix) throw new Error("Se necesitan 4 pares «px_x, px_y > mm_x, mm_y» que no estén en línea.");
  return { camera_id: cameraId, matrix, image_px: [1280, 720], residual_mm: 0, points: rows.length, version: (node.calib?.version ?? 0) + 1, source: "manual" };
}

/** Sin calibración automática, la manual se aplica a la cámara cenital (y la sigue si se elige otra). */
function ensureCalib(force = false) {
  const cam = cams.get(mainCam);
  if (SIM || !cam) return;
  const c = node.calib;
  if (!force && c && (c.source !== "manual" || c.camera_id === cam.id)) return;
  try {
    const calib = manualCalib(cam.id);
    node.setCalib(calib);
    store.set("calib", { session_id: ses.session_id, calib });
  } catch (err) {
    $("calib-estado").textContent = err.message;
  }
}
$("lente-cargar").onclick = () => {
  try {
    node.loadModel($("lente-json").value);
    $("calib-aviso").hidden = true;
  } catch (err) {
    showCalibError(err);
  }
  paintProjector();
};
$("calibrar-lente").onclick = async () => {
  $("calibrar-lente").disabled = $("calibrar").disabled = true;
  $("calib-aviso").hidden = true;
  $("calib-estado").textContent = "Calibrando con lente: primero las 9 marcas y después una grilla densa de puntos (unos 25 s). Saca las manos de la mesa.";
  try {
    const quick = await node.calibrate({ mode: "quick" });
    const r = await node.calibrateLens({}, quick);
    if (!r.saved) showCalibError(new Error(r.note));
  } catch (err) {
    showCalibError(err);
  }
  $("calibrar-lente").disabled = $("calibrar").disabled = false;
  paintProjector();
};
$("manual-aplicar").onclick = () => {
  store.set("manualPairs", $("manual-pares").value);
  ensureCalib(true);
  paintProjector();
};

// ------------------------------------------------------------------ manos
// Modo manos: lo prende un widget {type: "effect", shader: "hands"} (el agente con project, o la casilla «Manos»).
const DWELL_MS = 400;
const handTrails = [];
const smooth = [];
let handView = { hands: [], at: 0 };
let dwell = null;
let lastHandSnap = 0;
let lastHandView = 0;
const handsWidget = () => node.world.widgets.find((w) => w.type === "effect" && w.shader === "hands");
const mainVideo = () => cams.get(mainCam)?.video;

function onHands(found) {
  if (tracker.running) motor.hands(found.length > 0); // al motor local: solo los cambios de presencia
  const cfg = handsWidget();
  const c = node.calib;
  if ((!cfg && !node.ui.enabled && !node.world.widgets.some(isLive)) || !c) return;
  const [iw, ih] = c.image_px; // MediaPipe entrega 0..1: se escala al marco de la foto de look, sea cual sea su resolución
  smooth.length = found.length;
  const hands = found.map((h, i) => {
    const px = h.lm.map((p) => [p.x * iw, p.y * ih]);
    let mm = px.map((p) => pxToMm(c, p)); // lente + homografía
    if (smooth[i]) mm = mm.map((p, k) => [smooth[i][k][0] + 0.55 * (p[0] - smooth[i][k][0]), smooth[i][k][1] + 0.55 * (p[1] - smooth[i][k][1])]);
    smooth[i] = mm;
    const palm = Math.hypot(px[0][0] - px[9][0], px[0][1] - px[9][1]) || 1;
    return { px, mm, handed: h.handed, pinch: Math.hypot(px[4][0] - px[8][0], px[4][1] - px[8][1]) / palm < 0.3 };
  });
  const now = performance.now();
  if (node.ui.enabled) voiceHands(hands, now);
  let hover = null;
  let tip = null;
  let value; // nombre del data-press cuando el dedo está sobre un elemento de un widget html
  for (const h of hands) {
    const [x, y] = (tip = h.mm[8]);
    const ui = uiAt(tip); // la franja y el botón de voz mandan sobre lo que haya debajo
    if (ui && ui !== "pcx:voice") {
      hover = { id: ui, ui: true, on_press: ui.startsWith("pcx:page:") ? { dwell_ms: PAGE_DWELL_MS } : null };
      break;
    }
    if (!cfg) continue;
    const t = layer.targets().find(({ box: b }) => x >= b[0] && x <= b[0] + b[2] && y >= b[1] && y <= b[1] + b[3]);
    hover = t ? { ...t.widget, id: `${t.widget.id}#${t.name}`, on_press: null } : hit(node.world, bounds, tip);
    value = t?.name;
    if (hover) break;
  }
  if (!hover) dwell = null;
  else {
    if (dwell?.id !== hover.id) dwell = { id: hover.id, t0: now, fired: false };
    if (!dwell.fired && now - dwell.t0 >= (hover.on_press?.dwell_ms ?? DWELL_MS)) {
      dwell.fired = true; // un toque por visita: hay que sacar el dedo para repetir
      if (hover.ui) {
        pressUi(hover.id, "touch");
        if (hover.id.startsWith("pcx:page:")) dwell = { id: hover.id, t0: now, fired: false }; // con el dedo quieto sigue pasando páginas
      } else if (value === undefined) node.press(hover, tip.map(Math.round), "dwell");
      else node.press({ id: hover.id.split("#")[0] }, tip.map(Math.round), "dwell", value);
    }
  }
  handView = {
    hands: hands.map((h) => h.mm),
    pinch: hands.map((h) => h.pinch),
    handed: hands.map((h) => h.handed ?? null),
    hover: hover?.id ?? null,
    progress: hover ? Math.min(1, (now - dwell.t0) / (hover.on_press?.dwell_ms ?? DWELL_MS)) : 0,
    fired: !!dwell?.fired,
    opts: cfg ? cfg.params || {} : { skeleton: false, trail: false }, // sin modo manos solo se ve la punta del dedo
    at: now,
  };
  trackTrails(handTrails, handView, now);
  if (now - lastHandView > 66) {
    lastHandView = now; // a quienes miran, las manos les llegan a ~15 Hz
    node.sendView("hands", { hands: handView.hands.map((h) => h.map((p) => p.map(Math.round))), pinch: handView.pinch, handed: handView.handed, hover: handView.hover, progress: handView.progress, fired: handView.fired, opts: handView.opts });
  }
  layer.hover(handView.hover, handView.progress, handView.fired);
  layer.hands(handView.hands, handView);
  bus.postMessage({ t: "hands", ...handView });
  // scene (include_hands) se actualiza a 2 Hz: el dibujo va directo a la ventana del proyector, sin pasar por la nube.
  if (cfg && now - lastHandSnap > 500 && (hands.length || node.world.hands.length)) {
    lastHandSnap = now;
    const camera_id = cams.get(mainCam)?.id ?? c.camera_id;
    node.setHands(
      hands.map((h, i) => ({
        id: `hand-${i + 1}`,
        camera_id,
        handedness: { Left: "left", Right: "right" }[h.handed] ?? "unknown",
        landmarks_px: h.px.map((p) => p.map(Math.round)),
        tip_mm: h.mm[8].map(Math.round),
        pinch: h.pinch,
        t_capture_ns: Date.now() * 1e6,
      })),
    );
  }
}
window.pcx = { node, feedHands: onHands, tracker: () => tracker, ui: uiState, uiBounds: () => uiBounds }; // para depurar y probar sin cámara

const tracker = new HandTracker(mainVideo, onHands, (msg) => paintHands(msg));
function paintHands(msg) {
  const on = !!handsWidget();
  $("manos").checked = on;
  $("manos-estado").textContent = !on
    ? tracker.running
      ? `Dedo para las pestañas de la mesa: ${tracker.fps} cuadros/s. Deja esta pestaña a la vista.`
      : ""
    : (msg ??
      (!node.calib
        ? "Manos: falta calibrar para saber dónde cae cada dedo."
        : `Manos: ${tracker.fps} cuadros/s, ${handView.hands.length && performance.now() - handView.at < 500 ? handView.hands.length : 0} en la mesa. Deja esta pestaña a la vista.`));
}
function syncHands() {
  // Las manos: modo manos, la franja de pestañas (con calibración), un widget html con script (isLive) y el motor
  // local, que necesita saber cuándo se van las manos.
  const on =
    !!handsWidget() || ((node.ui.enabled || node.world.widgets.some(isLive)) && !!node.calib) || (motor.online && !!mainVideo());
  if (on && !tracker.running) tracker.start();
  if (!on && tracker.running) tracker.stop();
  if (on !== $("manos").checked) paintHands();
}
$("manos").onchange = () =>
  $("manos").checked ? node.put([{ type: "effect", id: "manos", shader: "hands", params: {} }]) : node.drop(node.world.widgets.filter((w) => w.shader === "hands").map((w) => w.id));

// ------------------------------------------------------------------ celulares como cámara
node.addEventListener("phones", ({ detail }) => setPhones(detail));
node.addEventListener("phoneframe", ({ detail }) => phoneFrame(detail));
$("celular").onclick = async () => {
  const box = $("celular-qr");
  if (!box.hidden) return (box.hidden = true);
  try {
    if (!ses.cam_token) {
      const res = await fetch("/api/cam-token", { method: "POST", headers: { Authorization: `Bearer ${ses.user_token}` } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      ses.cam_token = (await res.json()).cam_token;
      store.set("session", ses);
    }
    const url = `${location.origin}/cam/${ses.cam_token}`;
    $("celular-url").textContent = url;
    $("celular-copiar").onclick = () => navigator.clipboard.writeText(url).then(() => flash($("celular-copiar"), "Copiado", "Copiar enlace"));
    if (window.qrcode) {
      const qr = window.qrcode(0, "M");
      qr.addData(url);
      qr.make();
      $("celular-codigo").innerHTML = qr.createSvgTag({ cellSize: 5, margin: 3, scalable: true });
    }
    $("celular-aviso").textContent = /^(localhost|127\.)/.test(location.hostname)
      ? "Ojo: esta consola está en localhost y el celular no llega ahí. Usa la dirección pública de la mesa."
      : "Escanéalo con la cámara del celular y acepta el permiso. Aparece aquí como una cámara más (cel-1, cel-2…).";
    box.hidden = false;
  } catch (err) {
    flash($("celular"), `No se pudo: ${err.message}`, "Sumar un celular");
  }
};

// ------------------------------------------------------------------ presentación
const sendDeck = () => node.sendView("deck", { slides: node.deck.slides, index: node.deck.index });
function paintDeck() {
  const { slides, index } = node.deck;
  $("laminas-estado").textContent = slides.length ? `Lámina ${index + 1} de ${slides.length}` : "Sin láminas";
  $("lamina-prev").disabled = !slides.length || index === 0;
  $("lamina-next").disabled = !slides.length || index === slides.length - 1;
  $("laminas-lista").replaceChildren(
    ...slides.map((sl, i) => {
      const li = document.createElement("li");
      li.classList.toggle("on", i === index);
      li.innerHTML = "<b></b><span></span><small></small>";
      li.querySelector("b").textContent = i + 1;
      li.querySelector("span").textContent = sl.title || (sl.text || "").split("\n")[0] || (sl.live ? "En vivo" : "Imagen");
      li.querySelector("small").textContent = sl.live === "visor" ? "en vivo" : sl.live ? `cámara ${sl.camera ?? "cenital"}` : "";
      li.onclick = () => go(i + 1);
      return li;
    }),
  );
}
function go(to) {
  try {
    node.presentTool({ action: "go", to }, "console");
  } catch (err) {
    $("laminas-nota").textContent = err.message;
  }
}
node.addEventListener("deck", () => (paintDeck(), sendDeck()));
$("lamina-prev").onclick = () => go("prev");
$("lamina-next").onclick = () => go("next");
$("laminas-cargar").onclick = () => {
  const raw = $("laminas").value.trim();
  try {
    const json = /^[[{]/.test(raw) ? JSON.parse(raw) : null;
    const out = node.presentTool(json ? { action: "set", slides: Array.isArray(json) ? json : (json.slides ?? [json]) } : { action: "set", markdown: raw });
    $("laminas-nota").textContent = `${out.total} ${out.total === 1 ? "lámina cargada" : "láminas cargadas"}. Abre la presentación o copia el enlace.`;
    note("láminas", `${out.total} cargadas desde la consola`);
  } catch (err) {
    $("laminas-nota").textContent = `No se cargó: ${err.message}`;
  }
};
const flash = (b, text, back) => ((b.textContent = text), setTimeout(() => (b.textContent = back), 1800));
$("presentar-enlace").onclick = async () => {
  try {
    await navigator.clipboard.writeText(`${location.origin}/presentar/${await viewToken()}`);
    flash($("presentar-enlace"), "Enlace copiado", "Copiar enlace para presentar");
  } catch (err) {
    flash($("presentar-enlace"), `No se pudo: ${err.message}`, "Copiar enlace para presentar");
  }
};
$("presentar-abrir").onclick = async () => {
  const w = window.open("", "pcx-presentar"); // se abre dentro del clic para que no lo bloquee el navegador
  try {
    w.location = `/presentar/${await viewToken()}`;
  } catch (err) {
    w?.close();
    flash($("presentar-abrir"), `No se pudo: ${err.message}`, "Abrir presentación");
  }
};
paintDeck();
paintScreen();

// ------------------------------------------------------------------ agente
const PROMPT =
  "Estás conectado por MCP (servidor pcx) a WUI: una mesa con una cámara y un proyector reales, en vivo. " +
  "Parte con look para ver la escena y con scene para conocer la superficie proyectable en mm. " +
  "Después proyecta con project un saludo corto al centro y vuelve a mirar con look para confirmar que se ve. " +
  "Si look dice calibrated=false, llama a calibrate (unos 10 s, proyecta marcas). Ya calibrado, look trae una grilla en mm y " +
  "puedes anclar widgets a lo que ves con anchor.point_px (píxeles de la foto de look). " +
  "La mesa tiene un botón de voz: lo que la persona dice llega por events_wait como flow_emitted con event voice_command y data.text (escucha con types:['flow_emitted']). " +
  "La mesa tiene escenas (pestañas): scenes(action:list) dice cuál está activa; project, remove y scene aceptan scene:'nombre' para editar una que no está a la vista. " +
  "Con scene:'*' proyectas en la capa global, que se dibuja en todas las escenas (marcas de agua, avisos). " +
  "Para interfaces completas proyecta html: {type:'effect', id:'ui', shader:'html', rect_mm:[300,150,400,250], params:{html:'<div class=c><h1>Hola</h1><button data-press=ok>Listo</button></div>', css:'.c{padding:20px;background:#222;border-radius:16px}', rotate_deg:0}}; 1 px de CSS = 1 mm y cada data-press emite widget_pressed con ese nombre. " +
  "Para juegos con la mano agrega params.js a ese html: recibe las manos ~30 veces por segundo (wui.onHands) y te manda eventos (wui.emit); el protocolo viene en la nota effects de project. " +
  "Con present armas una presentación a pantalla completa (láminas y la mesa en vivo intercaladas) y la avanzas con action go. " +
  "Para manos en vivo proyecta un widget effect con shader hands: dibuja el esqueleto sobre la mano y un dedo quieto 0,4 s sobre un button dispara widget_pressed. " +
  "Cuéntame qué ves y pregúntame qué hacemos.";
const mcpUrl = `${location.origin}/mcp`;
const commands = (token) => ({
  claude:
    `claude mcp remove pcx 2>/dev/null; claude mcp add --transport http pcx ${mcpUrl} ` +
    `--header "Authorization: Bearer ${token}" && claude "${PROMPT}"`,
  codex:
    `export PCX_USER_TOKEN=${token}; codex mcp remove pcx 2>/dev/null; codex mcp add pcx --url ${mcpUrl} ` +
    `--bearer-token-env-var PCX_USER_TOKEN && codex "${PROMPT}"`,
  mcp: `URL: ${mcpUrl}\nTransporte: Streamable HTTP\nHeader: Authorization: Bearer ${token}\n\n${PROMPT}`,
});
let tab = "claude";
function paintCommand() {
  $("comando").textContent = commands(`${ses.user_token.slice(0, 6)}…`)[tab];
  for (const b of $("tabs").children) b.classList.toggle("on", b.dataset.tab === tab);
}
$("tabs").onclick = (e) => e.target.dataset.tab && ((tab = e.target.dataset.tab), paintCommand());
$("copiar").onclick = async () => {
  await navigator.clipboard.writeText(commands(ses.user_token)[tab]);
  $("copiar").textContent = "Copiado";
  setTimeout(() => ($("copiar").textContent = "Copiar"), 1600);
};
$("nueva").onclick = () => {
  node.stop();
  localStorage.removeItem("pcx.session");
  location.reload();
};
paintCommand();

let lastCall = 0;
/** Anota en el registro (consola y visor) algo que no fue una tool: cambio de escena con el dedo, voz. */
function note(tool, text) {
  const entry = { time: new Date().toLocaleTimeString("es-CL", { hour12: false }), tool, brief: text, ok: true, ms: 0 };
  activityLog.unshift(entry);
  activityLog.length = Math.min(activityLog.length, 40);
  node.sendView("activity", { entry });
  const li = document.createElement("li");
  li.innerHTML = `<time></time><b></b><span></span><small></small>`;
  li.querySelector("time").textContent = entry.time;
  li.querySelector("b").textContent = tool;
  li.querySelector("span").textContent = li.querySelector("span").title = text;
  $("actividad").prepend(li);
  return li;
}
const brief = (tool, args, data) =>
  !data || data.error
    ? data?.error || ""
    : tool === "project"
      ? (args.widgets || []).map((w) => `${w.type} ${w.id}`).join(", ")
      : tool === "remove"
        ? (data.removed || []).join(", ") || "nada que borrar"
        : tool === "ask"
          ? `${args.question} → ${data.timed_out ? "sin respuesta" : data.answer}`
          : tool === "scenes"
            ? args.action === "objects"
              ? `objects: ${data.objects.length ? data.objects.join(", ") : "ninguno"}`
              : `${args.action}${args.name ? ` ${args.name}` : ""} → activa: ${data.scene}`
          : tool === "look"
            ? `${data.camera_id} · ${data.image_px.join("×")}${data.via === "motor" ? " · motor" : ""}${data.clean ? ` · limpia${data.blanked_ms == null ? "" : ` (${data.blanked_ms} ms apagado)`}` : ""}`
            : tool === "calibrate"
              ? `${data.camera_id} · ${data.points_used ?? data.points_found}/${data.points_total} marcas · ${data.residual_mm} mm`
              : "";
node.addEventListener("call", ({ detail: d }) => {
  if (d.tool === "calibrate") {
    $("calib-aviso").hidden = d.ok;
    if (!d.ok) showCalibError(new Error(d.data.error));
  }
  lastCall = Date.now();
  if (d.tool === "project" && d.ok && $("patron").checked) {
    $("patron").checked = false; // el agente ya está proyectando: fuera el patrón
    keepPattern();
    pushWorld();
  }
  const entry = { time: new Date().toLocaleTimeString("es-CL", { hour12: false }), tool: d.tool, brief: brief(d.tool, d.args, d.data), ok: d.ok, ms: d.ms };
  activityLog.unshift(entry);
  activityLog.length = Math.min(activityLog.length, 40);
  node.sendView("activity", { entry });
  const li = document.createElement("li");
  li.className = d.ok ? "" : "mal";
  li.innerHTML = `<time></time><b></b><span></span>`;
  li.querySelector("time").textContent = new Date().toLocaleTimeString("es-CL", { hour12: false });
  li.querySelector("b").textContent = d.tool;
  li.querySelector("span").textContent = brief(d.tool, d.args, d.data);
  li.querySelector("span").title = li.querySelector("span").textContent;
  if (d.image) li.append(Object.assign(new Image(), { src: `data:${d.image.mime_type};base64,${d.image.data}`, alt: "Lo que vio el agente" }));
  else li.append(Object.assign(document.createElement("small"), { textContent: `${d.ms} ms` }));
  $("actividad").prepend(li);
  while ($("actividad").children.length > 60) $("actividad").lastChild.remove();
  paintAgent();
});
function paintAgent() {
  const live = lastCall && Date.now() - lastCall < 60000;
  $("agente-estado").classList.toggle("vivo", !!live);
  $("agente-estado").textContent = !lastCall
    ? "Esperando al agente…"
    : live
      ? "Agente operando"
      : `Última llamada hace ${Math.round((Date.now() - lastCall) / 60000)} min`;
}
setInterval(paintAgent, 5000);

// ------------------------------------------------------------------ motor local: panel e indicador
$("motor-url").value = motor.url;
$("motor-token").value = motorCfg.token;
function paintMotor() {
  $("motor-estado").dataset.state = motor.state;
  $("motor-texto").textContent = motor.summary();
  $("motor-detalle").textContent = motor.detail();
  $("motor-conectar").textContent = motor.wanted ? "Desconectar" : "Conectar";
  $("motor-percibir").disabled = !motor.online;
}
motor.addEventListener("state", () => (paintMotor(), syncHands()));
$("motor-conectar").onclick = () => {
  if (motor.wanted) {
    motor.disconnect();
    motorCfg.save({ on: false });
  } else {
    try {
      motor.configure({ url: $("motor-url").value, token: $("motor-token").value.trim() });
    } catch (err) {
      $("motor-detalle").textContent = err.message;
      return;
    }
    $("motor-url").value = motor.url;
    motorCfg.save({ url: motor.url, token: motor.token, on: true });
    motor.connect();
  }
  paintMotor();
};
$("motor-percibir").onclick = () => motor.perceive();
$("motor-olvidar").onclick = () => (motor.reset(), paintMotor());
window.pcx.motor = motor;

// ------------------------------------------------------------------ versión de la consola
// Si el servidor ya tiene otra versión de estos archivos (hubo deploy), se avisa aquí y en cada respuesta al agente:
// lo nuevo no funciona hasta recargar esta página y la ventana del proyector.
let loadedVersion = null;
async function checkVersion() {
  try {
    const v = (await (await fetch("/health", { cache: "no-store" })).json()).console_version;
    if (!v) return;
    loadedVersion ??= v;
    if (v === loadedVersion || node.outdated) return;
    node.outdated = `La consola de la mesa corre una versión anterior (${loadedVersion}; el servidor ya tiene ${v}). Lo nuevo no funciona hasta que la persona recargue la consola y relance la ventana del proyector.`;
    $("version-aviso").hidden = false;
    note("versión", "hay una versión nueva de la consola: recarga esta página y relanza el proyector");
  } catch {}
}
checkVersion();
setInterval(checkVersion, 30000);
$("version-aviso").onclick = () => location.reload();

// ------------------------------------------------------------------ arranque
navigator.mediaDevices?.addEventListener("devicechange", refreshCams);
$("camaras-activar").onclick = refreshCams;
node.connect();
paintMotor();
if (motorCfg.auto) motor.connect();
paintProjector();
if (navigator.mediaDevices) refreshCams();
else $("camaras-nota").textContent = "Este navegador no da acceso a cámaras desde esta dirección (se necesita HTTPS o localhost).";
