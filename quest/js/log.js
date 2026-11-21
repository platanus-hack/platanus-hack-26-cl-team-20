// Log a consola + al servidor (POST /api/log → terminal de server.py y quest/logs/). Silencioso si no hay servidor.
const cola = [];
let timer = null, servidorOk = true, fallos = 0;
const oyentes = new Set();
export let ultimo = '';

export function onLog(fn) { oyentes.add(fn); return () => oyentes.delete(fn); }

export function log(msg, datos = null, nivel = 'info', origen = window.__origenLog || 'quest') {
  (nivel === 'error' ? console.error : nivel === 'warn' ? console.warn : console.log)(`[${origen}] ${msg}`, datos ?? '');
  ultimo = msg;
  oyentes.forEach((fn) => { try { fn(msg, nivel); } catch { /* nada */ } });
  if (!servidorOk) return;
  cola.push({ t: Date.now(), nivel, origen, msg, datos });
  if (!timer) timer = setTimeout(vaciar, 400);
}
export const warn = (m, d) => log(m, d, 'warn');
export const error = (m, d) => log(m, d, 'error');

async function vaciar() {
  timer = null;
  const lote = cola.splice(0);
  if (!lote.length || location.protocol === 'file:') return;
  try {
    const r = await fetch('/api/log', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(lote), keepalive: true });
    if (!r.ok) throw new Error(r.status);
    fallos = 0;
  } catch {
    if (++fallos >= 3) servidorOk = false; // alojado sin server.py: no insistir
  }
}

export function capturarErrores() {
  window.addEventListener('error', (e) => error(`error JS: ${e.message} @ ${(e.filename || '').split('/').pop()}:${e.lineno}`));
  window.addEventListener('unhandledrejection', (e) => error(`promesa rechazada: ${e.reason?.message || e.reason}`));
}

/** Qué soporta este navegador (se manda al servidor al cargar: así se ve desde el Mac qué tiene el casco). */
export async function capacidades() {
  const c = {
    ua: navigator.userAgent, seguro: window.isSecureContext,
    webgl2: !!document.createElement('canvas').getContext('webgl2'),
    trackGenerator: typeof window.MediaStreamTrackGenerator === 'function',
    videoFrame: typeof window.VideoFrame === 'function',
    captureStream: !!HTMLCanvasElement.prototype.captureStream,
    rtc: typeof window.RTCPeerConnection === 'function',
  };
  try { c.xrAR = navigator.xr ? await navigator.xr.isSessionSupported('immersive-ar') : false; } catch (e) { c.xrAR = `error: ${e.message}`; }
  try {
    const cs = window.RTCRtpSender?.getCapabilities?.('video')?.codecs || [];
    c.codecsEnvio = [...new Set(cs.map((x) => x.mimeType.replace('video/', '')))];
    c.h264Perfiles = cs.filter((x) => /h264/i.test(x.mimeType)).map((x) => (x.sdpFmtpLine || '').match(/profile-level-id=(\w+)/)?.[1]).filter(Boolean);
  } catch { /* nada */ }
  try {
    const q = await navigator.mediaCapabilities.encodingInfo({ type: 'webrtc', video: { contentType: 'video/h264', width: 1280, height: 720, bitrate: 8e6, framerate: 30 } });
    c.encH264 = { soportado: q.supported, fluido: q.smooth, eficiente: q.powerEfficient };
  } catch { c.encH264 = 'sin dato'; }
  return c;
}
