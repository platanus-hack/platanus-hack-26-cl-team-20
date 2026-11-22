// El celular como cámara de la mesa: manda cuadros JPEG por un WebSocket (/cam) y el gateway se los pasa a la
// consola. Igual que el video del visor: nunca se encola; si la subida va atrasada, el cuadro se salta.
const $ = (id) => document.getElementById(id);
const token = location.pathname.split("/").pop();
const video = $("video");
const canvas = document.createElement("canvas");
const MAX_W = 1280;
const QUALITY = 0.72;
const EVERY_MS = 140; // ~7 cuadros/s
let socket = null;
let slot = null;
let facing = "environment";
let stream = null;
let encoding = false;
let sent = 0;
let skipped = 0;
let lock = null;

function status(state, text) {
  $("estado").dataset.state = state;
  $("estado-texto").textContent = text;
}

async function start() {
  stream?.getTracks().forEach((t) => t.stop());
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: facing }, width: { ideal: 1920 }, height: { ideal: 1080 } } });
  } catch (err) {
    $("nota").textContent =
      err.name === "NotAllowedError"
        ? "El navegador no tiene permiso para la cámara. Dáselo en los ajustes del sitio y vuelve a apretar."
        : !navigator.mediaDevices
          ? "Este navegador no deja usar la cámara aquí (hace falta https)."
          : `No se pudo abrir la cámara: ${err.message}`;
    $("activar").hidden = false;
    return;
  }
  video.srcObject = stream;
  $("activar").hidden = true;
  $("girar").hidden = false;
  $("nota").textContent = "Apunta el teléfono y déjalo fijo. Mantén esta página abierta y la pantalla encendida.";
  // Que la pantalla no se apague mientras transmite (si el navegador lo permite).
  lock = await navigator.wakeLock?.request("screen").catch(() => null);
}
$("activar").onclick = start;
$("girar").onclick = () => ((facing = facing === "environment" ? "user" : "environment"), start());
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState === "visible" && stream && lock?.released !== false) lock = await navigator.wakeLock?.request("screen").catch(() => null);
});

function tick() {
  if (encoding || !video.videoWidth || socket?.readyState !== WebSocket.OPEN) return;
  if (socket.bufferedAmount > 300_000) return void skipped++; // la subida va atrasada
  const w = Math.min(MAX_W, video.videoWidth);
  Object.assign(canvas, { width: w, height: Math.round((w * video.videoHeight) / video.videoWidth) });
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  encoding = true;
  canvas.toBlob(
    (blob) => {
      encoding = false;
      if (blob && socket?.readyState === WebSocket.OPEN) (socket.send(blob), sent++);
    },
    "image/jpeg",
    QUALITY,
  );
}
setInterval(tick, EVERY_MS);
setInterval(() => {
  $("cifras").textContent = video.videoWidth && slot ? `cel-${slot} · ${canvas.width}×${canvas.height} · ${sent} cuadros/s${skipped ? ` · ${skipped} saltados` : ""}` : "";
  sent = skipped = 0;
}, 1000);

const label = () => {
  const ua = navigator.userAgent;
  return /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android" : "Navegador";
};
let delay = 500;
function connect() {
  const ws = (socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/cam`));
  ws.onopen = () => (ws.send(JSON.stringify({ token, label: label() })), (delay = 500));
  ws.onmessage = ({ data }) => {
    const m = JSON.parse(data);
    slot = m.slot;
    status(m.mesa ? "online" : "connecting", m.mesa ? `Conectado como cel-${slot}` : `cel-${slot}: la mesa no está abierta`);
  };
  ws.onclose = (e) => {
    slot = null;
    if (e.code === 4401) return status("offline", "Este enlace no es válido o ya venció.");
    if (e.code === 4429) return status("offline", "Ya hay 4 celulares en esta mesa.");
    status("offline", "Sin conexión; reintentando…");
    setTimeout(connect, delay);
    delay = Math.min(delay * 2, 8000);
  };
}
connect();
// Si el permiso ya estaba dado, parte sola; si no, espera el botón (iOS exige un toque).
navigator.permissions?.query({ name: "camera" }).then((p) => p.state === "granted" && start()).catch(() => {});
