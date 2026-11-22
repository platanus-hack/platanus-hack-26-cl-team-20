"use strict";
const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v; else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined) n.append(c.nodeType ? c : String(c));
  return n;
};
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let d = null; try { d = await r.json(); } catch (_) { }
  if (!r.ok) throw new Error((d && (d.detail || d.error)) || ("HTTP " + r.status));
  return d;
}

// ------------------------------------------------------------------ Nueva vista (dale)
const PASOS = [
  ["inicio", "Inicio"], ["liberar", "Liberar proyector"], ["guia", "Proyector quieto"], ["visor", "Visor pipeline"],
  ["calib", "Calibración"], ["scan", "Escaneo"], ["escena2d", "Escena 2D"], ["pipeline", "Pipeline P6"],
  ["escena3d", "Escena 3D"], ["runtime", "Runtime"], ["efectos", "Efectos"], ["fin", "Fin"],
];
const ESPERA_TXT = {
  proyector_quieto: "Coloca el proyector apuntando a la escena y déjalo quieto",
  move_camera: "Mueve la cámara a la siguiente posición (sin tocar el proyector) y déjala quieta",
};
let S = null;
function resetDale() {
  S = { pasos: {}, orden: PASOS.map((p) => p[0]), actual: null, espera: null, running: false, fin: null, log: [], etapas: null };
  for (const [id, nom] of PASOS) S.pasos[id] = { id, nom, visto: false, ok: true, msg: "" };
}
resetDale();

function onDale(ev) {
  if (ev.kind === "log") { S.log.push(ev.msg); }
  else if (ev.kind === "proc") {
    if (ev.estado === "inicio" || ev.estado === "externo") { S.log.push("$ " + ev.cmd); S.running = true; }
    if (ev.estado === "fin") { S.fin = ev; S.running = false; S.espera = null; S.log.push(`(proceso terminó: código ${ev.rc}, ${ev.seg} s)`); }
  } else if (ev.kind === "evento") {
    let p = S.pasos[ev.paso];
    if (!p) { p = S.pasos[ev.paso] = { id: ev.paso, nom: ev.paso, visto: false, ok: true, msg: "" }; S.orden.splice(S.orden.length - 1, 0, ev.paso); }
    p.visto = true; p.ok = ev.ok !== false; p.msg = ev.msg || "";
    if (ev.cola && ev.cola.length && !p.ok) p.msg += " · " + ev.cola.join(" | ");
    S.actual = ev.paso;
    if (ESPERAS.includes(ev.step)) S.espera = { paso: ev.paso, step: ev.step, msg: ev.msg, view: ev.view };
    else S.espera = null;
    if (ev.paso === "calib") {
      if (typeof ev.progress === "number") p.progress = ev.progress;
      if (ev.view) p.view = ev.view;
      if (ev.step) p.step = ev.step;
    }
    if (ev.paso === "pipeline" && ev.msg === "etapas") {
      p.etapas = Object.entries(ev).filter(([k]) => !["t", "paso", "ok", "msg", "kind", "seq", "run"].includes(k)).map(([id, state]) => ({ id, label: id, state }));
    }
    if (ev.paso === "visor") setTimeout(recargarVisor, 1500);
    if (ev.paso === "fin") { S.running = S.running && true; }
    S.log.push(`[${ev.paso}] ${ev.ok === false ? "✗ " : ""}${ev.msg || ""}`);
  }
  if (S.log.length > 400) S.log.splice(0, S.log.length - 400);
}
const ESPERAS = ["proyector_quieto", "move_camera"];

function estadoPaso(p) {
  if (S.espera && S.espera.paso === p.id) return "wait";
  if (!p.visto) return "pend";
  if (S.running && S.actual === p.id && p.id !== "fin") return p.ok ? "run" : "err";
  return p.ok ? "ok" : "err";
}

function renderDale() {
  const ol = $("pasos"); ol.textContent = "";
  for (const id of S.orden) {
    const p = S.pasos[id]; const st = estadoPaso(p);
    const sub = el("div", { class: "msg" }, p.msg);
    if (id === "calib" && (p.progress !== undefined || p.view)) {
      sub.append(el("div", { class: "sub" }, p.view ? el("span", { class: "etapa" }, "vista " + p.view) : null, p.step ? el("span", { class: "etapa" }, p.step) : null));
      if (p.progress !== undefined) sub.append(el("div", { class: "barra" }, el("div", { style: `width:${Math.round(p.progress * 100)}%` })));
    }
    const etapas = id === "pipeline" ? (S.etapas || p.etapas) : null;
    if (etapas && etapas.length) sub.append(el("div", { class: "sub" }, etapas.map((e) => el("span", { class: "etapa " + e.state, title: e.id }, `${e.label}: ${e.state}`))));
    ol.append(el("li", { class: st }, el("span", { class: "ic" }), el("span", { class: "nom" }, p.nom), sub));
  }
  // espera grande + Continuar
  const box = $("espera");
  if (S.espera && S.running) {
    box.hidden = false;
    $("espera-txt").textContent = ESPERA_TXT[S.espera.step] || S.espera.msg;
    $("espera-sub").textContent = (S.espera.view ? `Vista ${S.espera.view}. ` : "") + (S.espera.msg || "");
  } else box.hidden = true;
  $("btn-dale").disabled = S.running;
  $("btn-cancelar").disabled = !S.running;
  const pill = $("estado-proc");
  if (S.running) { pill.textContent = S.espera ? "esperando a Max" : "dale corriendo · " + (S.pasos[S.actual]?.nom || "…"); pill.className = "pill run"; }
  else if (S.fin) { const ok = S.fin.rc === 0 && S.pasos.fin.visto; pill.textContent = ok ? "dale listo" : `dale terminó (código ${S.fin.rc})`; pill.className = "pill " + (ok ? "on" : "bad"); }
  else { pill.textContent = S.actual ? "último dale: " + (S.pasos[S.actual]?.nom || S.actual) : "sin dale"; pill.className = "pill"; }
  const log = $("log"); const abajo = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  log.textContent = S.log.join("\n"); if (abajo) log.scrollTop = log.scrollHeight;
}

let renderPend = false;
function pedirRender() { if (!renderPend) { renderPend = true; requestAnimationFrame(() => { renderPend = false; renderDale(); }); } }

function conectarSSE() {
  const es = new EventSource("/api/dale/events");
  es.addEventListener("reset", (m) => { resetDale(); const st = JSON.parse(m.data); S.running = st.running; pedirRender(); });
  es.addEventListener("dale", (m) => { onDale(JSON.parse(m.data)); pedirRender(); });
  es.addEventListener("status", (m) => {
    const st = JSON.parse(m.data);
    if (S.running !== st.running) { S.running = st.running; if (!st.running) S.espera = null; pedirRender(); }
  });
  es.onerror = () => { $("estado-proc").textContent = "dashboard desconectado…"; $("estado-proc").className = "pill bad"; };
}

$("btn-dale").onclick = async () => {
  $("dale-err").textContent = "";
  const body = { views: +$("o-views").value, scale: $("o-scale").value.trim(), synthetic: $("o-synthetic").checked, no_runtime: $("o-noruntime").checked };
  if ($("o-escena").value) body.escena_efectos = $("o-escena").value;
  try { await api("/api/dale", body); } catch (e) { $("dale-err").textContent = e.message; }
};
$("btn-cancelar").onclick = async () => { if (confirm("¿Cancelar el dale en curso?")) { try { await api("/api/cancelar", {}); } catch (e) { $("dale-err").textContent = e.message; } } };
$("btn-continuar").onclick = async () => {
  $("btn-continuar").disabled = true;
  try { await api("/api/continuar", {}); $("espera-sub").textContent = "Confirmado. Sigo…"; } catch (e) { $("dale-err").textContent = e.message; }
  setTimeout(() => { $("btn-continuar").disabled = false; }, 2500);
};

// pipeline en vivo (mientras corre el paso pipeline o después)
async function pollPipeline() {
  if (S.pasos.pipeline && S.pasos.pipeline.visto) {
    try { const d = await api("/api/pipeline"); if (d.ok && d.stages.length) { S.etapas = d.stages; pedirRender(); } } catch (_) { }
  }
}
function recargarVisor() { const f = $("visor"); f.src = "http://127.0.0.1:8796/?t=" + Date.now(); }
$("btn-visor").onclick = recargarVisor;

// ------------------------------------------------------------------ efectos
let E = null, firma = "";
const tieneObjetos = () => E && E.ok;

function editorParam(inst, key, defv) {
  const cur = inst.params && key in inst.params ? inst.params[key] : defv;
  const send = (value) => api("/api/param", { id: inst.id, key, value }).catch((e) => { $("rt-msg").textContent = e.message; });
  if (typeof defv === "boolean") {
    const c = el("input", { type: "checkbox" }); c.checked = !!cur; c.onchange = () => send(c.checked);
    return [el("span", {}, key), c, el("span")];
  }
  if (typeof defv === "number") {
    const v = Number(cur) || 0, d = Math.abs(Number(defv) || 0);
    let min = 0, max, step;
    if (Number.isInteger(defv) && d > 1) { max = Math.max(d * 3, 10); step = 1; }
    else if (d <= 1) { max = Math.max(1, Math.abs(v)); step = 0.01; }
    else { max = Math.max(d * 3, 5); step = 0.1; }
    if (defv < 0 || v < 0) min = -max;
    const val = el("span", { class: "val" }, String(v));
    const r = el("input", { type: "range", min, max, step, value: v });
    let t = 0;
    r.oninput = () => { val.textContent = r.value; const now = Date.now(); if (now - t > 90) { t = now; send(Number(r.value)); } };
    r.onchange = () => send(Number(r.value));
    return [el("span", {}, key), r, val];
  }
  if (Array.isArray(defv) && defv.length === 3 && defv.every((x) => typeof x === "number")) {
    const rgb = (Array.isArray(cur) ? cur : defv).map((x) => Math.max(0, Math.min(255, Math.round(x))));
    const hex = "#" + rgb.map((x) => x.toString(16).padStart(2, "0")).join("");
    const c = el("input", { type: "color", value: hex });
    let t = 0;
    const toRgb = () => [1, 3, 5].map((i) => parseInt(c.value.slice(i, i + 2), 16));
    c.oninput = () => { const now = Date.now(); if (now - t > 90) { t = now; send(toRgb()); } };
    c.onchange = () => send(toRgb());
    return [el("span", {}, key), c, el("span")];
  }
  if (typeof defv === "string") {
    const i = el("input", { type: "text", value: String(cur ?? "") }); i.onchange = () => send(i.value);
    return [el("span", {}, key), i, el("span")];
  }
  return null;
}

function bloqueFx(g, target, vivos) {
  const box = el("div", { class: "fx" + (vivos.length ? " vivo-fx" : "") });
  const top = el("div", { class: "fx-top" }, el("span", { class: "lbl" }, g.label || g.name), el("code", {}, g.name), el("span", { class: "desc" }, g.description || ""));
  top.append(el("button", { class: "chico", onclick: () => prender(g.name, target) }, vivos.length ? "+ otro" : "Prender"));
  box.append(top);
  for (const inst of vivos) {
    const fila = el("div", { class: "fx-top" }, el("code", {}, inst.id), el("button", { class: "chico rojo", onclick: () => apagar(inst.id) }, "Apagar"));
    box.append(fila);
    const ps = el("div", { class: "params" });
    for (const [k, dv] of Object.entries(g.params || {})) { const ed = editorParam(inst, k, dv); if (ed) ps.append(...ed); }
    if (ps.childElementCount) box.append(ps);
  }
  return box;
}

function renderEfectos() {
  const objDiv = $("objetos"), viv = $("vivos");
  objDiv.textContent = ""; viv.textContent = "";
  if (!tieneObjetos()) { $("rt-msg").textContent = E ? E.error : "consultando runtime…"; return; }
  $("rt-msg").textContent = Object.keys(E.errors || {}).length ? "errores de carga: " + Object.keys(E.errors).join(", ") : "";
  const gal = Object.fromEntries(E.gallery.map((g) => [g.name, g]));
  const vivosDe = (name, target) => E.effects.filter((x) => x.effect === name && (x.target || null) === (target || null));
  if (!E.effects.length) viv.append(el("span", { class: "ayuda" }, "ninguno"));
  for (const x of E.effects) viv.append(el("div", { class: "vivo" }, el("code", {}, x.id), el("b", {}, x.effect), "→ " + (x.target || "escena"),
    el("button", { class: "chico rojo", onclick: () => apagar(x.id) }, "Apagar")));
  if (!E.objects.length) objDiv.append(el("p", { class: "ayuda" }, "El runtime no tiene objetos detectados (corre una Nueva vista)."));
  for (const o of E.objects) {
    const vivos = E.effects.filter((x) => x.target === o.id);
    const det = el("details", { class: "obj", open: vivos.length > 0 || undefined });
    det.append(el("summary", {}, o.name, " ", el("span", { class: "kind" }, `${o.kind} · ${o.id}${vivos.length ? " · " + vivos.length + " vivo(s)" : ""}`)));
    const esp = o.compatibles.filter((n) => !(gal[n].targets || []).includes("*"));
    const gen = o.compatibles.filter((n) => (gal[n].targets || []).includes("*"));
    for (const n of [...esp, ...gen]) det.append(bloqueFx(gal[n], o.id, vivosDe(n, o.id)));
    objDiv.append(det);
  }
  const det = el("details", { class: "obj", open: E.effects.some((x) => !x.target) || undefined });
  det.append(el("summary", {}, "Escena completa ", el("span", { class: "kind" }, "efectos sin objeto")));
  for (const n of E.globales) det.append(bloqueFx(gal[n], null, vivosDe(n, null)));
  objDiv.append(det);
}

async function pollEscena(forzar) {
  try { E = await api("/api/escena"); } catch (e) { E = { ok: false, error: "dashboard: " + e.message }; }
  const pill = $("estado-rt");
  if (E.ok) { pill.textContent = `runtime ok · ${E.objects.length} objetos · ${E.effects.length} efectos`; pill.className = "pill on"; }
  else { pill.textContent = "runtime apagado"; pill.className = "pill bad"; }
  // re-render solo si cambian objetos o instancias (para no pisar sliders en uso)
  const f = E.ok ? JSON.stringify([E.objects.map((o) => o.id), E.effects.map((x) => x.id), E.gallery.length]) : "off:" + E.error;
  if (forzar || f !== firma) { firma = f; renderEfectos(); }
}
async function prender(effect, target) {
  try { await api("/api/spawn", { effect, target }); } catch (e) { $("rt-msg").textContent = e.message; }
  pollEscena(true);
}
async function apagar(id) {
  try { await api("/api/kill", { id }); } catch (e) { $("rt-msg").textContent = e.message; }
  pollEscena(true);
}
$("btn-apagar").onclick = async () => {
  try { await api("/api/kill", {}); $("showcase-msg").textContent = "todo apagado"; } catch (e) { $("showcase-msg").textContent = e.message; }
  pollEscena(true);
};

async function cargarEscenas() {
  try {
    const d = await api("/api/showcase");
    const box = $("escenas"); box.textContent = "";
    const sel = $("o-escena"); sel.textContent = ""; sel.append(el("option", { value: "" }, "(halos)"));
    for (const n of d.escenas) {
      box.append(el("button", { title: (d.detalle[n] || []).join(", "), onclick: () => correrEscena(n) }, n));
      sel.append(el("option", { value: n }, n));
    }
  } catch (e) { $("showcase-msg").textContent = e.message; }
}
async function correrEscena(n) {
  $("showcase-msg").textContent = "lanzando " + n + "…";
  try { const d = await api("/api/showcase", { escena: n }); $("showcase-msg").textContent = n + ": " + (d.salida || []).slice(-1).join(" "); }
  catch (e) { $("showcase-msg").textContent = n + ": " + e.message; }
  pollEscena(true);
}

// ------------------------------------------------------------------ miniatura
function pollFrame() {
  const img = $("frame");
  const nuevo = new Image();
  nuevo.onload = () => { img.src = nuevo.src; $("frame-off").hidden = true; setTimeout(pollFrame, 1000); };
  nuevo.onerror = () => { $("frame-off").hidden = false; setTimeout(pollFrame, 2000); };
  nuevo.src = "/api/frame.png?t=" + Date.now();
}

conectarSSE();
cargarEscenas();
pollEscena(true);
setInterval(pollEscena, 2000);
setInterval(pollPipeline, 2000);
pollFrame();
renderDale();
