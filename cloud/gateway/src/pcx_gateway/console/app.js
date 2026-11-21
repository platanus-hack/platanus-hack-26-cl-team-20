// Consola de pcx: detecta cámaras y monitores, lanza la ventana del proyector y entrega el comando para un agente.
import { BrowserNode } from "./node.js";
import { drawScene, hit } from "./render.js";

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
let mainCam = store.get("mainCam");

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
    store.set("mainCam", mainCam);
    paintCams();
  };
  video.onloadedmetadata = paintCams;
  stream.getVideoTracks()[0].onended = () => refreshCams();
  $("camaras").append(el);
  cams.set(deviceId, { id: camId(deviceId), deviceId, label: label || "Cámara", stream, video, el });
}

function paintCams() {
  if (!cams.has(mainCam)) mainCam = cams.keys().next().value;
  for (const c of cams.values()) {
    const main = c.deviceId === mainCam;
    c.el.classList.toggle("principal", main);
    c.el.querySelector("small").textContent = `${c.id} · ${c.video.videoWidth || "–"}×${c.video.videoHeight || "–"}`;
    c.el.querySelector("button").textContent = main ? "Principal" : "Usar como principal";
  }
  const n = cams.size;
  $("camaras-nota").textContent = n
    ? `${n} ${n === 1 ? "cámara conectada" : "cámaras conectadas"}. El agente mira por la principal si no pide otra.`
    : "No hay cámaras conectadas. Enchufa una y aparece sola.";
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

async function capture(cameraId) {
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
    src = await Promise.race([grab, new Promise((r) => setTimeout(r, 1500))]).catch(() => null) || cam.video;
  }
  const [vw, vh] = [src.videoWidth ?? src.width, src.videoHeight ?? src.height];
  if (!vw) throw new Error(`La cámara ${cam.id} todavía no entrega imagen; reintenta en un momento.`);
  const k = Math.min(1, 1280 / vw);
  const c = Object.assign(document.createElement("canvas"), { width: Math.round(vw * k), height: Math.round(vh * k) });
  c.getContext("2d").drawImage(src, 0, 0, c.width, c.height);
  return { data: c.toDataURL("image/jpeg", 0.82).split(",")[1], mime: "image/jpeg", w: c.width, h: c.height, camera: device(cam) };
}

const device = (c) => ({
  id: c.id,
  kind: "camera",
  adapter: "browser",
  caps: ["frames"],
  status: "online",
  meta: { label: c.label, width: c.video.videoWidth, height: c.video.videoHeight, main: c.deviceId === mainCam },
});

// ------------------------------------------------------------------ nodo
const node = new BrowserNode(ses, { capture, projectorOpen });

function syncDevices() {
  const open = projectorOpen();
  node.setDevices([
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

function pushWorld() {
  bus.postMessage({ t: "world", world: node.world, pattern: $("patron").checked });
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
  bounds = drawScene(pctx, previa.width, previa.height, node.world, t, { pattern: $("patron").checked });
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
node.addEventListener("world", () => (pushWorld(), paintProjector()));

// ------------------------------------------------------------------ agente
const PROMPT =
  "Estás conectado por MCP (servidor pcx) a una cámara y un proyector reales, en vivo. " +
  "Parte con look para ver la escena y con scene para conocer la superficie proyectable en mm. " +
  "Después proyecta con project un saludo corto al centro y vuelve a mirar con look para confirmar que se ve. " +
  "No hay calibración automática: para ubicar algo, proyecta una marca, mira dónde cayó y corrige. " +
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
