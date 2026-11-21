// Visor de solo lectura. Una sola vista de la mesa: el video de la cámara cenital rectificado a la superficie
// proyectable (homografía de calibrate, deformado aquí con WebGL) y, encima, lo proyectado con el mismo render.js.
// Todo llega por un WebSocket (/view) que el gateway alimenta con lo que manda el nodo navegador.
import { invert } from "/calib.js";
import { HtmlLayer } from "/html-layer.js";
import { drawHands, drawScene, trackTrails } from "/render.js";

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
      `precision highp float; uniform sampler2D t; uniform mat3 H; uniform vec2 mm; uniform vec2 img; varying vec2 v;
       void main(){ vec3 q = H * vec3(v.x * mm.x, (1.0 - v.y) * mm.y, 1.0); vec2 uv = q.xy / q.z / img;
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
  warp = { H: gl.getUniformLocation(prog, "H"), mm: gl.getUniformLocation(prog, "mm"), img: gl.getUniformLocation(prog, "img") };
}

const canRectify = () => !!(warp && view?.homography);
const rectified = () => $("rectificar").checked && canRectify();

function drawCamera() {
  if (!lastFrame) return;
  if (rectified()) {
    const { matrix, image_px } = view.homography;
    const m = invert(matrix); // mm → píxeles de la foto de look
    gl.viewport(0, 0, gl.canvas.width, gl.canvas.height);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, lastFrame);
    gl.uniformMatrix3fv(warp.H, false, [m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]]); // por columnas
    gl.uniform2f(warp.mm, view.world.surface.width_mm, view.world.surface.height_mm);
    gl.uniform2f(warp.img, image_px[0], image_px[1]); // el video puede venir a otra resolución: se usa en 0..1
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  } else {
    if (cruda.width !== lastFrame.width || cruda.height !== lastFrame.height) Object.assign(cruda, { width: lastFrame.width, height: lastFrame.height });
    cctx.drawImage(lastFrame, 0, 0);
  }
}

// ------------------------------------------------------------------ mezcla y modos
function layout() {
  const p = Number($("mezcla").value) / 100;
  const mix = rectified();
  // Calibrado y rectificando: una sola vista. Si no, la cámara tal cual y, aparte, lo proyectado.
  $("fig-cruda").hidden = mix;
  $("fig-mesa").hidden = !mix && $("rectificar").checked === false && canRectify();
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
        : "Lo que sale por el proyector (la mesa no está calibrada: no se puede combinar con la cámara)";
  syncHtml();
  drawCamera();
}
$("mezcla").oninput = layout;
$("rectificar").onchange = layout;

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
    const h = Math.round((previa.width * height_mm) / width_mm);
    for (const c of [previa, ptop, $("rect")]) if (c.height !== h) c.height = h;
    layout();
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
    const bounds = drawScene(pctx, previa.width, previa.height, view.world, t, view);
    tctx.clearRect(0, 0, ptop.width, ptop.height);
    if (!view.calib && performance.now() - hands.at < 300) drawHands(tctx, ptop.width, view.world, hands, trails, bounds, performance.now());
  }
  requestAnimationFrame(frame);
}

setInterval(() => {
  $("video-nota").textContent = frames && lastFrame ? `Video: ${lastFrame.width}×${lastFrame.height}, ${frames} cuadros/s.` : "Sin video: la consola de la mesa no está mandando cuadros.";
  frames = 0;
}, 1000);
layout();
connect();
requestAnimationFrame(frame);
