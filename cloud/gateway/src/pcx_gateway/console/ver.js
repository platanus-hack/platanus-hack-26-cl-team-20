// Visor de solo lectura. Una sola vista de la mesa: el video de la cámara cenital rectificado a la superficie
// proyectable (homografía de calibrate, deformado aquí con WebGL) y, encima, lo proyectado con el mismo render.js.
// Todo llega por un WebSocket (/view) que el gateway alimenta con lo que manda el nodo navegador.
import { invert } from "/calib.js";
import { HtmlLayer } from "/html-layer.js";
import { drawHands, drawScene, drawUi, trackTrails } from "/render.js";
import { loadWuiFont } from "./wui-brand.js";

loadWuiFont();

const $ = (id) => document.getElementById(id);
const token = location.pathname.split("/").pop();
const previa = $("previa");
const pctx = previa.getContext("2d");
const ptop = $("previa-top");
const tctx = ptop.getContext("2d");
const cruda = $("cruda");
const cctx = cruda.getContext("2d");
const layer = new HtmlLayer($("previa-html"), () => {});
const trails = [];
let view = null; // {world, pattern, textScale, calib, homography}
let hands = { hands: [], at: 0 };
let frames = 0;
let lastFrame = null; // ImageBitmap del último cuadro (uno solo en memoria)
let lastCall = 0;

function status(state, text) {
  $("estado").dataset.state = state;
  $("estado-texto").textContent = text;
}

// ------------------------------------------------------------------ cámara rectificada (WebGL)
const gl = $("rect").getContext("webgl", { antialias: false, preserveDrawingBuffer: false });
let warp = null;
if (gl) {
  const shader = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return s;
  };
  const prog = gl.createProgram();
  gl.attachShader(prog, shader(gl.VERTEX_SHADER, "attribute vec2 p; varying vec2 v; void main(){ v = p * 0.5 + 0.5; gl_Position = vec4(p, 0.0, 1.0); }"));
  gl.attachShader(
    prog,
    shader(
      gl.FRAGMENT_SHADER,
      // Por cada píxel de la mesa (mm) busca de qué píxel de la foto viene; lo que cae fuera de la foto queda negro.
      // Con modelo de lente, el píxel corregido se lleva al píxel real de la foto con la fórmula radial (k1, k2).
      `precision highp float; uniform sampler2D t; uniform mat3 H; uniform vec2 mm; uniform vec2 img;
       uniform vec3 lens; uniform vec2 kk; varying vec2 v;
       void main(){ vec3 q = H * vec3(v.x * mm.x, (1.0 - v.y) * mm.y, 1.0); vec2 p = q.xy / q.z;
         if (lens.x > 0.0) { vec2 n = (p - lens.yz) / lens.x; float r2 = dot(n, n); p = n * (1.0 + kk.x * r2 + kk.y * r2 * r2) * lens.x + lens.yz; }
         vec2 uv = p / img;
         gl_FragColor = (uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) ? vec4(0.0, 0.0, 0.0, 1.0) : texture2D(t, uv); }`,
    ),
  );
  gl.linkProgram(prog);
  gl.useProgram(prog);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, "p");
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  gl.bindTexture(gl.TEXTURE_2D, gl.createTexture());
  for (const [k, val] of [[gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE], [gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR]])
    gl.texParameteri(gl.TEXTURE_2D, k, val);
  warp = Object.fromEntries(["H", "mm", "img", "lens", "kk"].map((n) => [n, gl.getUniformLocation(prog, n)]));
}

// Embebido en /presentar: sin controles, a pantalla completa, y la página de afuera dice qué vista mostrar.
const embed = new URLSearchParams(location.search).has("embebido");
let mode = { modo: "visor", cam: null };
let asked = 0;
/** La vista embebida pide la cámara que le toca: la cenital para el visor, la que diga la lámina para «cámara». */
function wantCamera() {
  if (!embed || !view || socket?.readyState !== WebSocket.OPEN) return;
  const cams = view.cameras ?? [];
  const want = (mode.modo === "camara" && mode.cam && cams.find((c) => c.id === mode.cam)) || cams.find((c) => c.main);
  if (!want || want.id === view.stream_camera || performance.now() - asked < 800) return;
  asked = performance.now();
  socket.send(JSON.stringify({ type: "camera", id: want.id }));
}
if (embed) {
  document.body.classList.add("embebido");
  addEventListener("message", (e) => {
    if (e.origin !== location.origin || !e.data?.pcxModo) return;
    mode = { modo: e.data.pcxModo, cam: e.data.cam ?? null };
    $("rectificar").checked = mode.modo === "visor";
    wantCamera();
    layout();
  });
  setInterval(wantCamera, 1000);
}

const canRectify = () => !!(warp && view?.homography);
const rectified = () => $("rectificar").checked && canRectify();

function drawCamera() {
  if (!lastFrame) return;
  if (rectified()) {
    const { matrix, image_px, lens } = view.homography;
    gl.uniform3f(warp.lens, lens?.f ?? 0, lens?.cx ?? 0, lens?.cy ?? 0);
    gl.uniform2f(warp.kk, lens?.k1 ?? 0, lens?.k2 ?? 0);
    const m = invert(matrix); // mm → píxeles de la foto de look
    gl.viewport(0, 0, gl.canvas.width, gl.canvas.height);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, lastFrame);
    gl.uniformMatrix3fv(warp.H, false, [m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]]); // por columnas
    gl.uniform2f(warp.mm, view.world.surface.width_mm, view.world.surface.height_mm);
    gl.uniform2f(warp.img, image_px[0], image_px[1]); // el video puede venir a otra resolución: se usa en 0..1
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  } else {
    if (cruda.width !== lastFrame.width || cruda.height !== lastFrame.height) {
      Object.assign(cruda, { width: lastFrame.width, height: lastFrame.height });
      document.body.style.setProperty("--arc", lastFrame.width / lastFrame.height);
    }
    cctx.drawImage(lastFrame, 0, 0);
  }
}

// ------------------------------------------------------------------ mezcla y modos
function layout() {
  const p = Number($("mezcla").value) / 100;
  const mix = rectified();
  // Calibrado y rectificando: una sola vista. Si no, la cámara tal cual y, aparte, lo proyectado.
  $("fig-cruda").hidden = mix;
  $("fig-mesa").hidden = embed ? !mix : !mix && $("rectificar").checked === false && canRectify();
  $("rect").style.opacity = mix ? String(Math.min(1, 2 * (1 - p))) : "0";
  $("proy").style.opacity = mix ? String(Math.min(1, 2 * p)) : "1";
  $("mezcla").disabled = !mix;
  $("mezcla-valor").textContent = `${Math.round(p * 100)} %`;
  $("rectificar").disabled = !canRectify();
  $("mesa-nota").textContent = !view
    ? "La mesa"
    : mix
      ? `La mesa, ${view.world.surface.width_mm} × ${view.world.surface.height_mm} mm: cámara rectificada + lo proyectado`
      : canRectify()
        ? "Lo que sale por el proyector"
        : "Lo que sale por el proyector (esta cámara no está calibrada: no se puede combinar con ella)";
  syncHtml();
  drawCamera();
}
$("mezcla").oninput = layout;
$("camara-sel").onchange = () => socket?.readyState === WebSocket.OPEN && socket.send(JSON.stringify({ type: "camera", id: $("camara-sel").value }));
$("rectificar").onchange = layout;

const syncHtml = () =>
  view && (layer.hide(!!view.calib), layer.sync(view.world, previa.getBoundingClientRect().width / view.world.surface.width_mm));
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
    const h = Math.round((previa.width * height_mm) / width_mm);
    for (const c of [previa, ptop, $("rect")]) if (c.height !== h) c.height = h;
    document.body.style.setProperty("--ar", width_mm / height_mm);
    wantCamera();
    // Pestañas de escenas: clic para activar (lo único que el visor puede cambiar en la mesa).
    $("escenas").replaceChildren(
      ...(m.ui?.scenes ?? []).map((name) => {
        const b = document.createElement("button");
        b.textContent = name;
        b.classList.toggle("on", name === m.ui.active);
        b.onclick = () => socket?.readyState === WebSocket.OPEN && socket.send(JSON.stringify({ type: "scene", name }));
        return b;
      }),
    );
    // Selector de cámara: la cenital calibrada se puede rectificar; las demás se ven tal cual.
    const sel = $("camara-sel");
    const key = JSON.stringify(m.cameras ?? []);
    if (sel.dataset.key !== key) {
      sel.dataset.key = key;
      sel.replaceChildren(...(m.cameras ?? []).map((c) => new Option(`${c.id} · ${c.label}${c.main ? " (cenital)" : ""}`, c.id)));
    }
    if (m.stream_camera) sel.value = m.stream_camera;
    layout();
  },
  hands(m) {
    hands = { ...m, at: performance.now() };
    trackTrails(trails, hands, hands.at);
    layer.hover(m.hover, m.progress, m.fired);
    layer.hands(m.hands, m);
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

let decoding = false;
async function showFrame(blob) {
  if (decoding) return; // si este equipo no alcanza a decodificar, se salta el cuadro
  decoding = true;
  try {
    const img = await createImageBitmap(blob);
    lastFrame?.close();
    lastFrame = img;
    drawCamera();
    frames++;
  } finally {
    decoding = false;
  }
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
    const bounds = drawScene(pctx, previa.width, previa.height, view.world, t, { ...view, screen: { hint: "Pantalla del computador: se ve solo en la mesa, no viaja al visor" } });
    tctx.clearRect(0, 0, ptop.width, ptop.height);
    const uiBounds = view.calib ? new Map() : drawUi(tctx, ptop.width, view.world, view.ui, t);
    if (!view.calib && performance.now() - hands.at < 300)
      drawHands(tctx, ptop.width, view.world, hands, trails, new Map([...bounds, ...uiBounds]), performance.now());
  }
  requestAnimationFrame(frame);
}

setInterval(() => {
  const L = view?.homography?.lens;
  const lente = L ? ` Lente: k1 ${L.k1.toFixed(4)}, k2 ${L.k2.toFixed(4)}, error ${view.homography.residual_mm ?? "?"} mm.` : view?.homography ? " Sin modelo de lente (solo homografía)." : "";
  $("video-nota").textContent = (frames && lastFrame ? `Video: ${lastFrame.width}×${lastFrame.height}, ${frames} cuadros/s.` : "Sin video: la consola de la mesa no está mandando cuadros.") + lente;
  frames = 0;
}, 1000);
layout();
connect();
requestAnimationFrame(frame);
