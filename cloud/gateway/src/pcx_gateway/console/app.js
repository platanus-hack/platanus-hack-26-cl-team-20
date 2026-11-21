// Consola de pcx: detecta cámaras y monitores, lanza la ventana del proyector y entrega el comando para un agente.
import { BrowserNode } from "./node.js";
import { apply, solveH } from "./calib.js";
import { HandTracker } from "./hands.js";
import { drawHands, drawScene, hit, trackTrails } from "./render.js";

const $ = (id) => document.getElementById(id);
const store = {
  get: (k) => JSON.parse(localStorage.getItem(`pcx.${k}`) || "null"),
  set: (k, v) => localStorage.setItem(`pcx.${k}`, JSON.stringify(v)),
};

// ------------------------------------------------------------------ sesión
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

function addCam(deviceId, label, stream) {
  const video = Object.assign(document.createElement("video"), { autoplay: true, muted: true, playsInline: true, srcObject: stream });
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
  stream.getVideoTracks()[0].onended = () => refreshCams();
  $("camaras").append(el);
  cams.set(deviceId, { id: camId(deviceId), deviceId, label: label || "Cámara", stream, video, el });
}

function paintCams() {
  if (!cams.has(mainCam)) camChosen = false;
  if (!camChosen) mainCam = [...cams.values()].sort((a, b) => camRank(a.label) - camRank(b.label))[0]?.deviceId;
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
    if (!cams.size) {
      // La primera vez pide el permiso; con eso el navegador entrega nombres e ids reales.
      const first = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1920 }, height: { ideal: 1080 } } });
      const track = first.getVideoTracks()[0];
      addCam(track.getSettings().deviceId, track.label, first);
    }
    const found = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput" && d.deviceId);
    for (const [deviceId, c] of cams) {
      if (found.some((d) => d.deviceId === deviceId) && c.stream.active) continue;
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
let calibDots = null;

function simFrame() {
  const { width_mm, height_mm } = node.world.surface;
  const scene = Object.assign(document.createElement("canvas"), { width: 800, height: Math.round((800 * height_mm) / width_mm) });
  drawScene(scene.getContext("2d"), scene.width, scene.height, node.world, performance.now(), { calib: calibDots });
  const c = Object.assign(document.createElement("canvas"), { width: 1280, height: 720 });
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#2a2a2a";
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.translate(1040, 360 + scene.height / 2);
  ctx.rotate(Math.PI);
  ctx.drawImage(scene, 0, 0);
  return { canvas: c, camera: simCam };
}

/** Cuadro actual de una cámara como canvas (máx. 1280 px de ancho): es el marco de píxeles que ve el agente. */
async function camFrame(cameraId) {
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
  if ("ImageCapture" in window) {
    const grab = new ImageCapture(cam.stream.getVideoTracks()[0]).grabFrame();
    src = (await Promise.race([grab, new Promise((r) => setTimeout(r, 1500))]).catch(() => null)) || cam.video;
  }
  const [vw, vh] = [src.videoWidth ?? src.width, src.videoHeight ?? src.height];
  if (!vw) throw new Error(`La cámara ${cam.id} todavía no entrega imagen; reintenta en un momento.`);
  const k = Math.min(1, 1280 / vw);
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
  showCalib(dots) {
    calibDots = dots;
    pushWorld();
  },
  saveCalib: (calib) => store.set("calib", { session_id: ses.session_id, calib }),
});
const saved = store.get("calib");
if (saved?.session_id === ses.session_id) node.setCalib(saved.calib);

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
  if (!chosen || !list.includes(chosen)) chosen = list.find((s) => s !== screens.currentScreen) || list[0];
  for (const s of list) {
    const b = document.createElement("button");
    b.className = "pantalla";
    b.setAttribute("aria-pressed", s === chosen);
    const tags = [`${s.width}×${s.height}`, s.isInternal ? "integrada" : "externa", s === screens.currentScreen ? "aquí está la consola" : ""];
    b.innerHTML = `<strong></strong><small></small>`;
    b.querySelector("strong").textContent = s.label || "Pantalla";
    b.querySelector("small").textContent = tags.filter(Boolean).join(" · ");
    b.onclick = () => ((chosen = s), paintScreens());
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
  proj.win = window.open(`projector.html?s=${ses.session_id}${s ? "&auto=1" : ""}`, "pcx-projector", features);
  if (!proj.win) $("proyector-estado").textContent = "El navegador bloqueó la ventana. Permite las ventanas emergentes para este sitio.";
};
$("cerrar").onclick = () => bus.postMessage({ t: "close" });
$("patron").onchange = pushWorld;
$("calibrar").onclick = async () => {
  $("calibrar").disabled = true;
  $("calib-estado").textContent = "Calibrando: proyectando marcas y buscándolas con la cámara…";
  try {
    await node.calibrate({ mode: "quick" });
  } catch (err) {
    $("calib-estado").textContent = err.message;
  }
  $("calibrar").disabled = false;
  paintProjector();
};

function pushWorld() {
  bus.postMessage({ t: "world", world: node.world, pattern: $("patron").checked, calib: calibDots });
}

function paintProjector() {
  const open = projectorOpen();
  $("cerrar").hidden = !open;
  $("lanzar").textContent = open ? "Relanzar" : "Lanzar proyección";
  $("proyector-estado").textContent = !open
    ? "Sin ventana de proyector. Mientras tanto, lo que proyecte el agente se ve en la vista previa."
    : proj.fullscreen
      ? `Proyectando a ${proj.w}×${proj.h} en pantalla completa.`
      : `Ventana abierta a ${proj.w}×${proj.h}. Falta la pantalla completa: aprieta el botón de esa ventana (o F).`;
  const c = node.calib;
  if (c && !$("calibrar").disabled)
    $("calib-estado").textContent =
      c.source === "manual"
        ? `Calibración manual de 4 puntos sobre ${c.camera_id}. «Calibrar» la reemplaza por una automática.`
        : `Calibrada con ${c.camera_id}: ${c.points} marcas, error ${c.residual_mm} mm. El agente ya puede apuntar en píxeles de la foto.`;
  else if (!c && !$("calib-estado").textContent)
    $("calib-estado").textContent = "Sin calibrar: el agente ve la foto, pero no sabe dónde cae cada punto de la mesa.";
  const { width_mm, height_mm } = node.world.surface;
  $("previa-mm").textContent = `· ${width_mm} × ${height_mm} mm`;
}

bus.onmessage = ({ data }) => {
  if (data.t === "alive") {
    const was = projectorOpen();
    Object.assign(proj, { alive: Date.now(), w: data.w, h: data.h, fullscreen: data.fullscreen });
    node.setSurface(data.w, data.h);
    if (!was) pushWorld();
    syncDevices();
    paintProjector();
  } else if (data.t === "closed") {
    proj.alive = 0;
    syncDevices();
    paintProjector();
  } else if (data.t === "press") {
    const w = node.world.widgets.find((x) => x.id === data.id);
    if (w) node.press(w, data.point_mm);
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
let bounds = new Map();
function frame(t) {
  const { width_mm, height_mm } = node.world.surface;
  const h = Math.round((previa.width * height_mm) / width_mm);
  if (previa.height !== h) previa.height = h;
  bounds = drawScene(pctx, previa.width, previa.height, node.world, t, { pattern: $("patron").checked, calib: calibDots });
  if (!calibDots && performance.now() - handView.at < 300)
    drawHands(pctx, previa.width, node.world, handView, handTrails, bounds, performance.now());
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
previa.onclick = (e) => {
  const r = previa.getBoundingClientRect();
  const k = node.world.surface.width_mm / r.width;
  const pt = [(e.clientX - r.left) * k, (e.clientY - r.top) * k];
  const w = hit(node.world, bounds, pt);
  if (w) node.press(w, pt);
};
node.addEventListener("world", () => (pushWorld(), paintProjector(), syncHands()));

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
const handsWidget = () => node.world.widgets.find((w) => w.type === "effect" && w.shader === "hands");
const mainVideo = () => cams.get(mainCam)?.video;

function onHands(found) {
  const cfg = handsWidget();
  const c = node.calib;
  if (!cfg || !c) return;
  const [iw, ih] = c.image_px; // MediaPipe entrega 0..1: se escala al marco de la foto de look, sea cual sea su resolución
  smooth.length = found.length;
  const hands = found.map((h, i) => {
    const px = h.lm.map((p) => [p.x * iw, p.y * ih]);
    let mm = px.map((p) => apply(c.matrix, p));
    if (smooth[i]) mm = mm.map((p, k) => [smooth[i][k][0] + 0.55 * (p[0] - smooth[i][k][0]), smooth[i][k][1] + 0.55 * (p[1] - smooth[i][k][1])]);
    smooth[i] = mm;
    const palm = Math.hypot(px[0][0] - px[9][0], px[0][1] - px[9][1]) || 1;
    return { px, mm, handed: h.handed, pinch: Math.hypot(px[4][0] - px[8][0], px[4][1] - px[8][1]) / palm < 0.3 };
  });
  const now = performance.now();
  let hover = null;
  let tip = null;
  for (const h of hands) {
    hover = hit(node.world, bounds, h.mm[8]);
    if (hover) {
      tip = h.mm[8];
      break;
    }
  }
  if (!hover) dwell = null;
  else {
    if (dwell?.id !== hover.id) dwell = { id: hover.id, t0: now, fired: false };
    if (!dwell.fired && now - dwell.t0 >= (hover.on_press?.dwell_ms ?? DWELL_MS)) {
      dwell.fired = true; // un toque por visita: hay que sacar el dedo para repetir
      node.press(hover, tip.map(Math.round), "dwell");
    }
  }
  handView = {
    hands: hands.map((h) => h.mm),
    hover: hover?.id ?? null,
    progress: hover ? Math.min(1, (now - dwell.t0) / (hover.on_press?.dwell_ms ?? DWELL_MS)) : 0,
    fired: !!dwell?.fired,
    opts: cfg.params || {},
    at: now,
  };
  trackTrails(handTrails, handView, now);
  bus.postMessage({ t: "hands", ...handView });
  // scene (include_hands) se actualiza a 2 Hz: el dibujo va directo a la ventana del proyector, sin pasar por la nube.
  if (now - lastHandSnap > 500 && (hands.length || node.world.hands.length)) {
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
window.pcx = { node, feedHands: onHands, tracker: () => tracker }; // para depurar y probar sin cámara

const tracker = new HandTracker(mainVideo, onHands, (msg) => paintHands(msg));
function paintHands(msg) {
  const on = !!handsWidget();
  $("manos").checked = on;
  $("manos-estado").textContent = !on
    ? ""
    : (msg ??
      (!node.calib
        ? "Manos: falta calibrar para saber dónde cae cada dedo."
        : `Manos: ${tracker.fps} cuadros/s, ${handView.hands.length && performance.now() - handView.at < 500 ? handView.hands.length : 0} en la mesa. Deja esta pestaña a la vista.`));
}
function syncHands() {
  const on = !!handsWidget();
  if (on && !tracker.running) tracker.start();
  if (!on && tracker.running) tracker.stop();
  if (on !== $("manos").checked) paintHands();
}
$("manos").onchange = () =>
  $("manos").checked ? node.put([{ type: "effect", id: "manos", shader: "hands", params: {} }]) : node.drop(node.world.widgets.filter((w) => w.shader === "hands").map((w) => w.id));

// ------------------------------------------------------------------ agente
const PROMPT =
  "Estás conectado por MCP (servidor pcx) a una cámara y un proyector reales, en vivo. " +
  "Parte con look para ver la escena y con scene para conocer la superficie proyectable en mm. " +
  "Después proyecta con project un saludo corto al centro y vuelve a mirar con look para confirmar que se ve. " +
  "Si look dice calibrated=false, llama a calibrate (unos 10 s, proyecta marcas). Ya calibrado, look trae una grilla en mm y " +
  "puedes anclar widgets a lo que ves con anchor.point_px (píxeles de la foto de look). " +
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
const brief = (tool, args, data) =>
  !data || data.error
    ? data?.error || ""
    : tool === "project"
      ? (args.widgets || []).map((w) => `${w.type} ${w.id}`).join(", ")
      : tool === "remove"
        ? (data.removed || []).join(", ") || "nada que borrar"
        : tool === "ask"
          ? `${args.question} → ${data.timed_out ? "sin respuesta" : data.answer}`
          : tool === "look"
            ? `${data.camera_id} · ${data.image_px.join("×")}`
            : tool === "calibrate"
              ? `${data.camera_id} · ${data.points_found}/${data.points_total} marcas · ${data.residual_mm} mm`
              : "";
node.addEventListener("call", ({ detail: d }) => {
  lastCall = Date.now();
  if (d.tool === "project" && d.ok && $("patron").checked) {
    $("patron").checked = false; // el agente ya está proyectando: fuera el patrón
    pushWorld();
  }
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

// ------------------------------------------------------------------ arranque
navigator.mediaDevices?.addEventListener("devicechange", refreshCams);
$("camaras-activar").onclick = refreshCams;
node.connect();
paintProjector();
if (navigator.mediaDevices) refreshCams();
else $("camaras-nota").textContent = "Este navegador no da acceso a cámaras desde esta dirección (se necesita HTTPS o localhost).";
