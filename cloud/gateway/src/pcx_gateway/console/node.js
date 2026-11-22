// El navegador como nodo de pcx: habla por WSS con el gateway el mismo protocolo que el nodo Rust
// (contracts/src/pcx_contracts/cloud.py) y resuelve las tools con la cámara y la ventana del proyector.

import { annotate, apply, components, diff, findDot, fitLens, invert, litArea, mmToPx, pxToMm, ransacH, signal, solveH } from "./calib.js";

import { htmlRect, isHtml } from "./html-layer.js";
import { uiRects } from "./render.js";

const CONTRACT = 2;
const SCENE_NAME = /^[^\s#:][^#:]{0,39}$/;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const round = (v, d = 1) => Math.round(v * 10 ** d) / 10 ** d;
const now_ns = () => Date.now() * 1e6;
const uuid = () => crypto.randomUUID();

export class BrowserNode extends EventTarget {
  /** hooks: { frame(cameraId) → {canvas, camera}, projectorOpen() → bool, showCalib(dots|null), saveCalib(calib) } */
  constructor(session, hooks) {
    super();
    this.session = session;
    this.hooks = hooks;
    this.streamId = uuid();
    this.seq = 0;
    this.log = [];
    this.ws = null;
    this.ready = false;
    this.stopped = false;
    this.delay = 500;
    this.asks = new Map(); // request_id → {resolve, options, ids}
    this.ttl = new Map();
    this.viewers = 0;
    // Escenas (pestañas): cada una con sus widgets; world.widgets es siempre la lista de la activa.
    this.scenes = [{ name: "Principal", widgets: [] }];
    this.voiceShortcuts = ["listo", "atrás", "repite", "siguiente", "anterior"]; // órdenes de voz de una o dos palabras que sí se mandan
    this.emitted = []; // instantes de los últimos scenes action emit (tope de frecuencia)
    this.global = { name: "*", widgets: [] }; // capa global: se dibuja en todas las escenas (marcas de agua, avisos)
    this.active = "Principal";
    this.deck = { slides: [], index: 0, pristine: true }; // modo presentación
    this.pristine = true; // sin cambios locales: se pueden recuperar las escenas guardadas en el gateway
    this.ui = { enabled: true, flip: false }; // franja de pestañas y botón de voz proyectados
    this.calib = null; // {camera_id, matrix (px de look → mm), image_px, residual_mm, points, version}
    this.world = {
      rev: 1,
      surface: { width_mm: 1000, height_mm: 562 },
      objects: [],
      hands: [],
      touches: [],
      widgets: [],
      devices: [],
      transforms: [],
      active_flows: [],
    };
  }

  // ------------------------------------------------------------------ enlace
  connect() {
    this.stopped = false;
    this.status("connecting");
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/node`;
    const ws = (this.ws = new WebSocket(url));
    ws.onopen = () =>
      this.send({
        type: "hello",
        node_id: this.session.node_id,
        token: this.session.node_token,
        capabilities: ["browser", "look", "project", "ask"],
        version: "web-0.1",
        contract_versions: [CONTRACT],
        stream_id: this.streamId,
        log_first_cursor: this.log.length ? this.log[0].seq : 0,
        log_last_cursor: this.seq,
      });
    ws.binaryType = "arraybuffer";
    ws.onmessage = (e) => {
      if (typeof e.data === "string") return this.receive(JSON.parse(e.data));
      // Cuadro JPEG de un celular: el primer byte dice de cuál.
      this.dispatchEvent(new CustomEvent("phoneframe", { detail: { slot: new Uint8Array(e.data, 0, 1)[0], blob: new Blob([e.data.slice(1)], { type: "image/jpeg" }) } }));
    };
    ws.onclose = (e) => {
      clearInterval(this.beat);
      this.ready = false;
      this.viewers = 0;
      this.dispatchEvent(new CustomEvent("viewers", { detail: 0 }));
      if (ws !== this.ws) return;
      if (e.code === 4401) return this.status("invalid"); // la sesión ya no existe en el gateway
      if (e.code === 4001) return this.status("replaced"); // otra pestaña tomó la sesión
      this.status("offline");
      if (!this.stopped) setTimeout(() => this.connect(), this.delay);
      this.delay = Math.min(this.delay * 2, 8000);
    };
  }

  stop() {
    this.stopped = true;
    this.ws?.close();
  }

  status(state) {
    this.state = state;
    this.dispatchEvent(new CustomEvent("status", { detail: state }));
  }

  send(msg) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  receive(msg) {
    if (msg.type === "welcome") return this.resume(msg);
    if (msg.type === "hello_rejected") return this.status(msg.reason === "auth" ? "invalid" : "offline");
    if (msg.type === "tool_call") return this.call(msg);
    if (msg.type === "photo") this.dispatchEvent(new CustomEvent("photo"));
    if (msg.type === "scene") this.scenesTool({ action: "activate", name: msg.name }, "viewer");
    if (msg.type === "slide") {
      try {
        this.presentTool({ action: "go", to: msg.to }, "presenter");
      } catch {} // un salto fuera de rango desde quien presenta no es un error que mostrar
    }
    if (msg.type === "state" && this.deck.pristine && Array.isArray(msg.deck?.slides) && msg.deck.slides.length) {
      this.deck = { slides: msg.deck.slides, index: Math.min(msg.deck.index | 0, msg.deck.slides.length - 1), pristine: false };
      this.dispatchEvent(new CustomEvent("deck"));
    }
    if (msg.type === "state" && this.pristine && msg.scenes?.list?.length) {
      this.scenes = msg.scenes.list.map((s) => ({ name: String(s.name), widgets: s.widgets || [] }));
      this.active = this.scenes.some((s) => s.name === msg.scenes.active) ? msg.scenes.active : this.scenes[0].name;
      this.global.widgets = Array.isArray(msg.scenes.global) ? msg.scenes.global : [];
      if (Array.isArray(msg.scenes.shortcuts)) this.voiceShortcuts = msg.scenes.shortcuts.map(String);
      this.world.widgets = this.liveWidgets();
      this.changed();
      this.dispatchEvent(new CustomEvent("scenes"));
    }
    if (msg.type === "phones") this.dispatchEvent(new CustomEvent("phones", { detail: msg.phones }));
    if (msg.type === "camera") this.dispatchEvent(new CustomEvent("camera", { detail: msg.id }));
    if (msg.type === "viewers") {
      this.viewers = msg.count;
      if (msg.slow) this.dispatchEvent(new CustomEvent("slow")); // alguien que mira está perdiendo cuadros
      this.dispatchEvent(new CustomEvent("viewers", { detail: msg.count }));
    }
  }

  resume(welcome) {
    this.snapshot();
    let last = welcome.last_cursor || 0;
    let complete = true;
    if (welcome.last_stream_id && welcome.last_stream_id !== this.streamId) {
      this.send({ type: "history_gap", stream_id: welcome.last_stream_id, from: last + 1, to: null, reason: "stream_reset" });
      last = 0;
      complete = false;
    }
    const replay = this.log.filter((e) => e.seq > last);
    if (replay.length)
      this.send({ type: "event_batch", stream_id: this.streamId, cursor: replay.at(-1).seq, events: replay, replay: true });
    this.send({ type: "replay_done", stream_id: this.streamId, cursor: this.seq, history_complete: complete });
    this.beat = setInterval(() => this.send({ type: "heartbeat", t_ms: Date.now(), cursor: this.seq }), 10000);
    this.ready = true;
    this.delay = 500;
    this.status("online");
    if (!this.pristine) this.persist(); // cambios hechos sin conexión
  }

  // Para quienes miran en vivo (fuera del contrato; el gateway lo reenvía tal cual y solo si hay alguien mirando).
  sendView(kind, data) {
    if (this.viewers && this.ready) this.ws.send(JSON.stringify({ type: "view", kind, ...data }));
  }

  /** Cuadro JPEG de la cenital. Si la subida va atrasada, se salta el cuadro en vez de encolarlo. */
  sendFrame(blob) {
    if (!this.viewers || !this.ready) return true;
    if (this.ws.bufferedAmount > 400_000) return false; // la subida va atrasada
    this.ws.send(blob);
    return true;
  }

  snapshot() {
    this.send({ type: "state_snapshot", stream_id: this.streamId, cursor: this.seq, world: this.world });
  }

  emit(payload) {
    const event = {
      type: payload.type,
      v: CONTRACT,
      seq: ++this.seq,
      t_capture_ns: null,
      t_pub_ns: now_ns(),
      source: "web-node",
      node_id: this.session.node_id,
      payload,
    };
    this.log.push(event);
    if (this.log.length > 500) this.log.shift();
    if (this.ready) this.send({ type: "event_batch", stream_id: this.streamId, cursor: event.seq, events: [event], replay: false });
  }

  /** El mundo cambió: avisa a la consola y al gateway. */
  changed() {
    this.world.rev += 1;
    if (this.ready) this.snapshot();
    this.dispatchEvent(new CustomEvent("world"));
  }

  // ------------------------------------------------------------------ estado local
  setDevices(devices) {
    const before = JSON.stringify(this.world.devices);
    if (before === JSON.stringify(devices)) return;
    const old = new Map(this.world.devices.map((d) => [d.id, d.status]));
    this.world.devices = devices;
    this.changed();
    // Evento solo si un dispositivo aparece o cambia de estado (no por cada cambio de metadatos).
    for (const d of devices) if (old.get(d.id) !== d.status) this.emit({ type: "device_changed", device: d });
  }

  setSurface(wPx, hPx) {
    const height = Math.round((1000 * hPx) / wPx);
    if (height === this.world.surface.height_mm) return;
    this.world.surface = { width_mm: 1000, height_mm: height };
    this.changed();
  }

  /** Toque sobre un widget (clic en la ventana del proyector o en la vista previa). */
  press(widget, point_mm, trigger = "api", value = widget.on_press?.value ?? null) {
    const m = /^pcx:ask:(.+):(\d+)$/.exec(widget.id);
    if (m && this.asks.has(m[1])) return this.asks.get(m[1]).resolve(Number(m[2]));
    this.emit({
      type: "widget_pressed",
      widget_id: widget.id,
      value,
      trigger,
      point_mm,
    });
  }

  // ------------------------------------------------------------------ calibración cámara↔mesa
  setCalib(calib) {
    this.calib = calib;
    this.world.transforms = calib
      ? [
          {
            kind: "homography",
            from_frame: `cam:${calib.camera_id}`,
            to_frame: "table_mm",
            version: calib.version,
            residual_mm: calib.residual_mm,
            matrix: calib.matrix,
          },
        ]
      : [];
    this.changed();
  }

  /** Píxeles de la foto de `look` → mm de la mesa. */
  toMm(p, cameraId) {
    const c = this.calib;
    if (!c) throw new Error("Sin calibración: llama a calibrate antes de usar coordenadas en píxeles de cámara.");
    if (cameraId && cameraId !== c.camera_id)
      throw new Error(`La calibración es de ${c.camera_id}, no de ${cameraId}. Calibra esa cámara o usa los píxeles de ${c.camera_id}.`);
    // Los píxeles son los de la foto de look; si esa foto no mide lo mismo que la de la calibración, se escalan.
    const k = this.lookSize ? c.image_px[0] / this.lookSize[0] : 1;
    return pxToMm(c, [p[0] * k, p[1] * k]).map((v) => round(v));
  }

  /** Acepta geometría en píxeles de cámara (anchor.point_px, polygon_px, from_px, to_px) y la deja en mm. */
  resolve(w) {
    const out = { ...w };
    const cam = w.anchor?.camera_id ?? w.camera_id;
    if (w.anchor?.point_px) out.anchor = { ...w.anchor, point_mm: this.toMm(w.anchor.point_px, cam) };
    if (w.polygon_px) out.polygon_mm = w.polygon_px.map((p) => this.toMm(p, cam));
    if (w.from_px) out.from_mm = this.toMm(w.from_px, cam);
    if (w.to_px) out.to_mm = this.toMm(w.to_px, cam);
    return out;
  }

  async calibrate(args = {}) {
    if (this.calibrating) throw new Error("Ya hay una calibración en curso.");
    if (!this.hooks.projectorOpen())
      throw new Error("La ventana del proyector está cerrada: pídele a la persona que apriete «Lanzar proyección» en la consola.");
    const mode = args.mode ?? "quick";
    if (mode === "verify" && !this.calib) throw new Error("No hay calibración que verificar: llama a calibrate con mode quick.");
    const { width_mm: W, height_mm: H } = this.world.surface;
    const [nx, ny] = [3, 3];
    const R = 30; // radio de la marca en mm
    const targets = [];
    for (let j = 0; j < ny; j++)
      for (let i = 0; i < nx; i++) targets.push([W * (0.12 + (0.76 * i) / (nx - 1)), H * (0.12 + (0.76 * j) / (ny - 1))]);
    const say = (text) => this.hooks.progress?.(text);
    let camera;
    let image_px;
    const shot = async () => {
      const f = await this.hooks.frame(args.camera_id);
      camera = f.camera;
      image_px = [f.canvas.width, f.canvas.height];
      return signal(f.canvas);
    };
    const fail = (why, extra = {}) =>
      Object.assign(
        new Error(
          `${why} Revisa que ${camera?.id ?? "la cámara cenital"} mire la proyección completa, saca las manos de la mesa y baja la luz ambiente si puedes.`,
        ),
        { detail: extra },
      );
    this.calibrating = true;
    const found = []; // {mm, px, z} por marca vista
    try {
      // 1. Todo apagado y todo encendido: mide la demora proyector→cámara y qué zona de la foto ilumina el proyector.
      say("Midiendo la proyección…");
      this.hooks.showCalib({ dots: [] });
      await sleep(900);
      const dark = [await shot(), await shot()];
      this.hooks.showCalib({ fill: true });
      const t0 = performance.now();
      let lit = null;
      let lag = null;
      let last = 0;
      while (performance.now() - t0 < 2500) {
        const f = await shot();
        const a = litArea(diff([f], dark));
        if (a.area > f.g.length * 0.01) {
          lag ??= performance.now() - t0;
          if (lit && Math.abs(a.area - last) < a.area * 0.05) {
            lit = a;
            break;
          }
          lit = a;
          last = a.area;
        }
        await sleep(60);
      }
      if (!lit) throw fail("La cámara no ve la proyección: encendí todo el proyector en verde y la foto no cambió.");
      const settle = (this.settle = Math.min(1500, Math.max(300, lag * 1.5 + 150)));
      // Diámetro esperado de la marca en la foto, por regla de tres con el área iluminada.
      const win = 2 * Math.sqrt((lit.area * (Math.PI * R * R)) / (W * H) / Math.PI);
      // 2. Cada marca: dos fotos con ella y dos sin ella, intercaladas; así un objeto o una mano quieta se cancelan.
      for (const [n, t] of targets.entries()) {
        say(`Marca ${n + 1} de ${targets.length} (vistas: ${found.length})…`);
        const on = [];
        const off = [];
        for (let c = 0; c < 2; c++) {
          this.hooks.showCalib({ dots: [[t[0], t[1], R]] });
          await sleep(settle);
          on.push(await shot());
          this.hooks.showCalib({ dots: [] });
          await sleep(settle);
          off.push(await shot());
        }
        const dot = findDot(diff(on, off), lit.mask, win);
        if (dot) found.push({ mm: t, px: [dot.x, dot.y], z: dot.z });
      }
    } finally {
      this.hooks.showCalib(null);
      this.calibrating = false;
    }
    const camera_id = camera.id;
    const total = targets.length;
    const need = Math.ceil(total * (2 / 3)); // 6 de 9
    const marks = found.map((f) => ({ mm: f.mm.map(Math.round), px: f.px.map(Math.round), z: f.z }));
    // Zona de la superficie donde cae una marca, en palabras («arriba» es y = 0, donde el patrón de prueba dice 0,0).
    const zone = ([x, y]) => {
      const col = x < W / 3 ? "izquierda" : x > (2 * W) / 3 ? "derecha" : "";
      const row = y < H / 3 ? "superior" : y > (2 * H) / 3 ? "inferior" : "";
      if (col && row) return `la esquina ${row} ${col}`;
      if (row) return `el borde ${row}`;
      if (col) return `el borde ${col === "izquierda" ? "izquierdo" : "derecho"}`;
      return "el centro";
    };
    const zones = (list) => [...new Set(list.map(zone))].join(", ");
    const px = found.map((f) => f.px);
    const mm = found.map((f) => f.mm);
    const unseen = targets.filter((t) => !found.some((f) => f.mm === t));
    const fit = mode === "verify" || found.length < 4 ? null : ransacH(px, mm);
    const odd = fit ? found.filter((_, k) => !fit.inliers.includes(k)).map((f) => f.mm) : [];
    // Qué faltó y hacia dónde corregir: va en el error, en el resultado y en la consola.
    const missing = [
      unseen.length ? `No vi ${unseen.length === 1 ? "la marca" : "las marcas"} de ${zones(unseen)}: mueve o abre la cámara para que esa zona entre en la foto.` : "",
      odd.length ? `Vi algo en ${zones(odd)} pero no calza con el resto (reflejo, objeto brillante o algo que se movió).` : "",
    ]
      .filter(Boolean)
      .join(" ");
    const detail = { marks, unseen_mm: unseen.map((t) => t.map(Math.round)), inconsistent_mm: odd.map((t) => t.map(Math.round)) };
    if (found.length < 4)
      throw fail(`Solo vi ${found.length} de ${total} marcas en la cámara ${camera_id}; con menos de 4 no se puede calcular. ${missing}`, detail);
    if (mode === "verify") {
      const e = px.map((p, i) => Math.hypot(...pxToMm(this.calib, p).map((v, a) => v - mm[i][a])));
      const rms = Math.sqrt(e.reduce((a, v) => a + v * v, 0) / e.length);
      return { camera_id, mode, points_found: px.length, points_total: total, residual_mm: round(rms), max_error_mm: round(Math.max(...e)), ok: rms < 12, missing };
    }
    const good = fit?.inliers.length ?? 0;
    const residual_mm = fit ? round(fit.residual) : null;
    // Una homografía se define con 4 puntos: con 5 que calcen bien (≤ 8 mm) se acepta, avisando que es de calidad reducida.
    const reduced = good < need && good >= 5 && residual_mm <= 8;
    if (good < need && !reduced)
      throw fail(
        `Vi ${found.length} de ${total} marcas, pero solo ${good} calzan entre sí${good >= 5 ? ` y con ${residual_mm} mm de error (con 5 acepto hasta 8 mm)` : ` (necesito ${need}, o 5 con error bajo)`}. ${missing}`,
        detail,
      );
    if (residual_mm > 12) throw fail(`${good} marcas calzan, pero con ${residual_mm} mm de error: no la guardé. ${missing}`, detail);
    const M = fit.H;
    const toPx = invert(M);
    const corners = [[0, 0], [W, 0], [W, H], [0, H]].map((p) => apply(toPx, p).map((v) => Math.round(v)));
    const warning = reduced
      ? `Calidad reducida: solo ${good} de ${total} marcas. Vale cerca de las marcas vistas y pierde precisión hacia la zona que falta. ${missing}`
      : missing;
    const calib = { camera_id, matrix: M, image_px, residual_mm, points: good, total, reduced, warning, version: (this.calib?.version ?? 0) + 1 };
    this.setCalib(calib);
    this.hooks.saveCalib(calib);
    return {
      camera_id,
      mode,
      points_found: found.length,
      points_used: good,
      points_total: total,
      residual_mm,
      quality: reduced ? "reducida" : "buena",
      ...(warning ? { warning } : {}),
      unseen_mm: detail.unseen_mm,
      inconsistent_mm: detail.inconsistent_mm,
      image_px,
      matrix_px_to_mm: M.map((v) => round(v, 6)),
      surface_corners_px: corners,
      rotation_deg: Math.round((Math.atan2(corners[1][1] - corners[0][1], corners[1][0] - corners[0][0]) * 180) / Math.PI),
      note:
        "Homografía guardada: píxeles de la foto de look → mm de la mesa. look ahora trae la grilla en mm; en project puedes usar " +
        "anchor.point_px (y polygon_px en halo, from_px/to_px en arrow) con píxeles de esa foto. Si mueven la cámara o el proyector, calibra de nuevo.",
    };
  }

  /** Carga un modelo de lente medido afuera (el JSON de tools/mesa-agente/lente.py o el que devuelve `precise`). */
  loadModel(m) {
    if (typeof m === "string") m = JSON.parse(m);
    const lens = m.lens ?? { f: m.f_px, cx: m.cx, cy: m.cy, k1: m.k1, k2: m.k2 ?? 0 };
    const matrix = m.matrix ?? m.H_px_sin_distorsion_a_mm;
    const image_px = m.image_px ?? m.imagen;
    const nums = [lens.f, lens.cx, lens.cy, lens.k1, lens.k2, ...(matrix ?? []), ...(image_px ?? [])];
    if (matrix?.length !== 9 || image_px?.length !== 2 || !nums.every(Number.isFinite) || !(lens.f > 0))
      throw new Error("Modelo de lente incompleto: se necesitan f_px, cx, cy, k1, k2, H_px_sin_distorsion_a_mm (9 números) e imagen [ancho, alto].");
    const camera_id = m.camera_id ?? this.calib?.camera_id;
    if (!camera_id) throw new Error("El modelo no dice de qué cámara es (camera_id).");
    const calib = {
      camera_id,
      matrix,
      image_px,
      lens,
      residual_mm: m.residual_mm ?? m.error_mm?.mediana ?? null,
      points: m.points ?? m.puntos ?? null,
      total: m.total ?? m.puntos ?? null,
      source: "modelo",
      version: (this.calib?.version ?? 0) + 1,
    };
    this.setCalib(calib);
    this.hooks.saveCalib(calib);
    return {
      camera_id,
      loaded: true,
      lens,
      image_px,
      residual_mm: calib.residual_mm,
      note: "Modelo de lente cargado: look, anchor.point_px, polygon_px y las manos pasan por lente + homografía. Otro calibrate lo reemplaza.",
    };
  }

  /** `precise`: después de quick, proyecta una grilla densa de puntos verdes y ajusta la distorsión de la lente. */
  async calibrateLens(args, quick) {
    const { width_mm: W, height_mm: H } = this.world.surface;
    const grid = [];
    for (let y = 41; y < H - 20; y += 60) for (let x = 50; x < W - 20; x += 60) grid.push([x, y]);
    const settle = this.settle ?? 500;
    this.calibrating = true;
    this.hooks.progress?.(`Lente: ${grid.length} puntos…`);
    const on = [];
    const off = [];
    let size;
    try {
      for (let c = 0; c < 3; c++) {
        for (const [list, dots] of [[on, grid.map(([x, y]) => [x, y, 9])], [off, []]]) {
          this.hooks.showCalib({ dots });
          await sleep(settle);
          const f = await this.hooks.frame(args.camera_id);
          size = [f.canvas.width, f.canvas.height];
          list.push(signal(f.canvas, f.canvas.width)); // a resolución completa: los puntos son chicos
        }
      }
    } finally {
      this.hooks.showCalib(null);
      this.calibrating = false;
    }
    const blobs = components(diff(on, off));
    // Empareja cada mancha con el punto que debería estar ahí según la calibración vigente (uno a uno, el más cercano).
    const match = (calib, tol) => {
      const inv = invert(calib.matrix);
      const pairs = [];
      grid.forEach((mm, g) => {
        const e = mmToPx(calib, mm, inv);
        blobs.forEach((b, n) => {
          const dist = Math.hypot(b.x - e[0], b.y - e[1]);
          if (dist < tol) pairs.push({ g, n, dist });
        });
      });
      const usedG = new Set();
      const usedN = new Set();
      const out = [];
      for (const p of pairs.sort((a, b) => a.dist - b.dist)) {
        if (usedG.has(p.g) || usedN.has(p.n)) continue;
        usedG.add(p.g);
        usedN.add(p.n);
        out.push({ px: [blobs[p.n].x, blobs[p.n].y], mm: grid[p.g] });
      }
      return out;
    };
    const tol = (22 * size[0]) / 1280;
    let pairs = match(this.calib, tol);
    const why = `Quedó guardada la calibración sin lente (${quick.points_used} marcas, ${quick.residual_mm} mm).`;
    if (pairs.length < 30) throw new Error(`Lente: solo ubiqué ${pairs.length} de ${grid.length} puntos de la grilla densa; necesito 30. ${why}`);
    let fit = fitLens(pairs.map((p) => p.px), pairs.map((p) => p.mm), size);
    if (fit) {
      // Segunda pasada: con el modelo ya ajustado se recuperan puntos de los bordes que la homografía sola no alcanzaba.
      const again = match({ matrix: fit.matrix, lens: fit.lens }, tol / 2);
      if (again.length > pairs.length) {
        pairs = again;
        fit = fitLens(pairs.map((p) => p.px), pairs.map((p) => p.mm), size) ?? fit;
      }
    }
    if (!fit || fit.used.length < 30) throw new Error(`Lente: el ajuste no convergió con ${pairs.length} puntos. ${why}`);
    // Comparación con solo homografía sobre los mismos puntos: el modelo se guarda solo si mejora.
    const plain = solveH(fit.used.map((i) => pairs[i].px), fit.used.map((i) => pairs[i].mm));
    const flat = fit.used.map((i) => Math.hypot(...apply(plain, pairs[i].px).map((v, a) => v - pairs[i].mm[a]))).sort((a, b) => a - b);
    const flatMedian = flat[flat.length >> 1];
    const result = {
      camera_id: quick.camera_id,
      mode: "precise",
      image_px: size,
      f_px: round(fit.lens.f),
      cx: fit.lens.cx,
      cy: fit.lens.cy,
      k1: round(fit.lens.k1, 5),
      k2: round(fit.lens.k2, 5),
      rms_px: round(fit.rms_px, 2),
      error_mm: { median: round(fit.err_mm.median), p95: round(fit.err_mm.p95), max: round(fit.err_mm.max) },
      error_mm_homography_only: round(flatMedian),
      points_total: grid.length,
      points_found: pairs.length,
      points_used: fit.used.length,
      points_discarded: pairs.length - fit.used.length,
      matrix_undistorted_px_to_mm: fit.matrix.map((v) => round(v, 6)),
    };
    if (fit.err_mm.median > flatMedian * 0.95)
      return { ...result, saved: false, note: `El modelo de lente no mejora a la homografía sola (${round(fit.err_mm.median)} contra ${round(flatMedian)} mm): no lo guardé. ${why}` };
    const calib = {
      camera_id: quick.camera_id,
      matrix: fit.matrix,
      image_px: size,
      lens: fit.lens,
      residual_mm: round(fit.err_mm.median),
      points: fit.used.length,
      total: grid.length,
      source: "lente",
      version: (this.calib?.version ?? 0) + 1,
    };
    this.setCalib(calib);
    this.hooks.saveCalib(calib);
    return {
      ...result,
      saved: true,
      note:
        "Modelo de lente guardado (focal nominal: con una vista plana no queda determinada; k1 y k2 sí). look, anchor.point_px, " +
        "polygon_px y las manos pasan por lente + homografía. La matriz vale para píxeles ya corregidos.",
    };
  }

  /** Manos vistas por la cámara cenital (para `scene` con include_hands). */
  setHands(hands) {
    this.world.hands = hands;
    this.changed();
  }

  // ------------------------------------------------------------------ escenas
  /** Lo que se proyecta: la escena activa más la capa global (si un id se repite, manda el de la escena). */
  liveWidgets() {
    const mine = this.sceneOf().widgets;
    if (!this.global.widgets.length) return mine;
    const ids = new Set(mine.map((w) => w.id));
    return [...mine, ...this.global.widgets.filter((w) => !ids.has(w.id))];
  }

  sceneOf(name) {
    if (name === "*") return this.global;
    const wanted = name ?? this.active;
    const scene = this.scenes.find((s) => s.name === wanted);
    if (!scene) throw new Error(`No existe la escena «${wanted}». Hay: ${this.scenes.map((s) => s.name).join(", ")}.`);
    return scene;
  }

  /** Lo que el agente necesita para saber con qué escena está trabajando. */
  sceneState() {
    return { scene: this.active, scenes: this.scenes.map((s) => ({ name: s.name, widgets: s.widgets.length, active: s.name === this.active })), global_widgets: this.global.widgets.length };
  }

  /** Guarda las escenas en el gateway (por sesión), sin apurarse: un mensaje fuera del contrato. */
  persist() {
    this.pristine = false;
    clearTimeout(this.saving);
    const save = () => ((this.saved = performance.now()), this.send({ type: "state", scenes: { active: this.active, list: this.scenes, global: this.global.widgets, shortcuts: this.voiceShortcuts }, deck: { slides: this.deck.slides, index: this.deck.index } }));
    if (performance.now() - (this.saved ?? 0) > 400) save(); // al tiro; si vienen cambios seguidos, el último sale al final
    else this.saving = setTimeout(save, 400);
    this.dispatchEvent(new CustomEvent("scenes"));
  }

  // ------------------------------------------------------------------ presentación
  /** Láminas desde Markdown: se separan con `---`; `# ` título, `![](url)` imagen, `@vivo` o `@camara id` vista en vivo. */
  static slidesFromMarkdown(md) {
    return String(md)
      .split(/^\s*---+\s*$/m)
      .map((part) => {
        const slide = {};
        const text = [];
        for (const line of part.split("\n")) {
          const t = line.trim();
          let m;
          if (!slide.title && (m = /^#{1,3}\s+(.+)$/.exec(t))) slide.title = m[1];
          else if ((m = /^!\[[^\]]*\]\((\S+?)\)$/.exec(t))) slide.image = m[1];
          else if ((m = /^@(vivo|visor)\b/i.exec(t))) slide.live = "visor";
          else if ((m = /^@c[aá]mara\b\s*(\S+)?/i.exec(t))) Object.assign(slide, { live: "camara" }, m[1] ? { camera: m[1] } : {});
          else text.push(line);
        }
        const body = text.join("\n").trim();
        if (body) slide.text = body;
        return slide;
      })
      .filter((s) => Object.keys(s).length);
  }

  deckState() {
    const { slides, index } = this.deck;
    return {
      slide: slides.length ? index + 1 : 0,
      total: slides.length,
      slides: slides.map((s, i) => ({ n: i + 1, title: s.title ?? null, ...(s.live ? { live: s.live } : {}), ...(s.camera ? { camera: s.camera } : {}) })),
    };
  }

  presentTool(args, by = "agent") {
    const action = args.action ?? "get";
    if (action === "set") {
      const raw = args.slides ?? (args.markdown != null ? BrowserNode.slidesFromMarkdown(args.markdown) : null);
      if (!Array.isArray(raw) || !raw.length) throw new Error("set necesita slides (lista) o markdown con al menos una lámina.");
      if (raw.length > 80) throw new Error("Máximo 80 láminas.");
      const slides = raw.map((r, i) => {
        const where = `slides[${i}]`;
        if (!r || typeof r !== "object" || Array.isArray(r)) throw new Error(`${where} debe ser un objeto.`);
        const out = {};
        for (const k of ["title", "text", "image", "html", "camera", "notes"]) {
          if (r[k] == null) continue;
          if (typeof r[k] !== "string") throw new Error(`${where}.${k} debe ser texto.`);
          out[k] = r[k];
        }
        if (r.live != null) {
          if (!["visor", "camara"].includes(r.live)) throw new Error(`${where}.live debe ser 'visor' o 'camara'.`);
          out.live = r.live;
        }
        if (out.image && !/^(https?:\/\/|data:image\/|\/)/.test(out.image)) throw new Error(`${where}.image debe ser una URL http(s) o un data:image.`);
        if (!Object.keys(out).length) throw new Error(`${where} está vacía: lleva title, text, image, html o live.`);
        return out;
      });
      if (JSON.stringify(slides).length > 1_200_000) throw new Error("La presentación pesa más de 1,2 MB: usa URLs en vez de imágenes incrustadas.");
      this.deck = { slides, index: 0, pristine: false };
    } else if (action === "clear") {
      this.deck = { slides: [], index: 0, pristine: false };
    } else if (action === "go") {
      const { slides, index } = this.deck;
      if (!slides.length) throw new Error("No hay láminas: usa action set primero.");
      const to = args.to === "next" ? index + 2 : args.to === "prev" ? index : Number(args.to);
      if (!Number.isInteger(to)) throw new Error("to debe ser un número de lámina (desde 1), 'next' o 'prev'.");
      if ((args.to === "next" || args.to === "prev") && (to < 1 || to > slides.length)) return this.deckState(); // ya está en el borde
      if (to < 1 || to > slides.length) throw new Error(`to fuera de rango: hay ${slides.length} láminas.`);
      if (to - 1 === index) return this.deckState();
      this.deck.index = to - 1;
      this.deck.pristine = false;
      if (by !== "agent") this.emit({ type: "flow_emitted", event: "slide_changed", data: { slide: to, total: slides.length, title: slides[to - 1].title ?? null, by } });
    } else if (action !== "get") throw new Error("action debe ser get, set, go o clear.");
    if (action !== "get") {
      this.persist();
      this.dispatchEvent(new CustomEvent("deck"));
    }
    return this.deckState();
  }

  /** Un cliente MCP emite un evento de flujo para los demás (scenes action emit). Nunca puede pasar por algo que
   *  hizo una persona: los nombres de la mesa están reservados y `by: "agent"` y `scene` se ponen al final. */
  emitFlow(event, data) {
    const RESERVED = ["voice_command", "scene_changed", "slide_changed", "widget_killed", "widget_error", "home_call_done", "dwell", "press", "pressed"];
    const PREFIXES = ["widget_", "object_", "hand_", "touch", "confirm", "home_", "voice_", "pcx", "wui"];
    if (typeof event !== "string" || !/^[a-z][a-z0-9_.:-]{0,39}$/.test(event))
      throw new Error("event debe ser un nombre de 1 a 40 caracteres: minúsculas, números y _ . : - (empieza con letra).");
    if (RESERVED.includes(event) || PREFIXES.some((p) => event.startsWith(p)))
      throw new Error(`«${event}» está reservado para lo que pasa físicamente en la mesa (toques, voz, confirmaciones, cambios de escena): elige otro nombre.`);
    if (data != null && (typeof data !== "object" || Array.isArray(data))) throw new Error("data debe ser un objeto.");
    if (JSON.stringify(data ?? {}).length > 2000) throw new Error("data pesa más de 2000 caracteres.");
    const now = performance.now();
    this.emitted = this.emitted.filter((t) => now - t < 1000);
    if (this.emitted.length >= 5) throw new Error("Demasiados eventos: máximo 5 por segundo.");
    this.emitted.push(now);
    const payload = { ...(data ?? {}), by: "agent", scene: this.active }; // al final: no se pueden sobreescribir
    this.emit({ type: "flow_emitted", event, data: payload });
    return { emitted: { event, data: payload } };
  }

  scenesTool(args, by = "agent") {
    const action = args.action ?? "list";
    const name = args.name?.trim();
    if (name === "*" || args.new_name?.trim() === "*")
      throw new Error("«*» es la capa global: no es una escena. Se usa solo como scene: '*' en project, remove y scene.");
    const fresh = (n) => {
      if (!n || !SCENE_NAME.test(n)) throw new Error("El nombre de la escena debe tener de 1 a 40 caracteres, sin «#» ni «:».");
      if (this.scenes.some((s) => s.name === n)) throw new Error(`Ya existe una escena «${n}».`);
      if (this.scenes.length >= 40) throw new Error("Máximo 40 escenas por mesa.");
      return n;
    };
    const activate = (n) => {
      if (n === this.active) return;
      this.active = n;
      this.world.widgets = this.liveWidgets();
      this.changed();
      this.emit({ type: "flow_emitted", event: "scene_changed", data: { scene: n, by } });
    };
    if (action === "create") {
      this.scenes.push({ name: fresh(name), widgets: [] });
      activate(name);
    } else if (action === "activate") {
      activate(this.sceneOf(name).name);
    } else if (action === "duplicate") {
      const from = this.sceneOf(name);
      const to = fresh(args.new_name?.trim());
      this.scenes.push({ name: to, widgets: structuredClone(from.widgets) });
    } else if (action === "rename") {
      const scene = this.sceneOf(name);
      const to = fresh(args.new_name?.trim());
      if (this.active === scene.name) this.active = to;
      scene.name = to;
      this.changed();
    } else if (action === "delete") {
      const scene = this.sceneOf(name);
      if (this.scenes.length === 1) throw new Error("No se puede borrar la única escena.");
      this.scenes = this.scenes.filter((s) => s !== scene);
      if (this.active === scene.name) activate(this.scenes[0].name);
    } else if (action === "objects") {
      return { ...this.setObjects(args.objects), ...this.sceneState() };
    } else if (action === "emit") {
      return { ...this.emitFlow(args.event, args.data), ...this.sceneState() };
    } else if (action === "voice_shortcuts") {
      if (args.words != null) {
        if (!Array.isArray(args.words) || args.words.length > 30) throw new Error("words debe ser una lista de hasta 30 órdenes cortas (vacía para no permitir ninguna).");
        const words = args.words.map((w) => String(w).trim()).filter(Boolean);
        if (words.some((w) => w.length > 30 || w.split(/\s+/).length > 3)) throw new Error("Cada orden corta lleva hasta 3 palabras y 30 caracteres.");
        this.voiceShortcuts = [...new Set(words)];
        this.persist();
      }
      return { voice_shortcuts: this.voiceShortcuts, note: "Estas órdenes se mandan como voice_command aunque sean cortas, si la mano cumplió la permanencia sobre el botón.", ...this.sceneState() };
    } else if (action !== "list") throw new Error("action debe ser list, create, activate, duplicate, delete, rename, objects, emit o voice_shortcuts.");
    if (action !== "list") this.persist();
    return this.sceneState();
  }

  /** Un cliente MCP que hace la percepción afuera publica sus objetos (scenes action objects): reemplaza los que
   *  publicó antes ese cliente, sin tocar los del motor local. Es por sesión (no por escena). Todo o nada. */
  setObjects(list) {
    if (!Array.isArray(list)) throw new Error("objects debe ser una lista (vacía para borrar los que publicaste).");
    if (list.length > 50) throw new Error("Máximo 50 objetos.");
    const r = this.mergeObjects("mcp", { upsert: list, replace: true, strict: true });
    return {
      objects: r.objects,
      rev: this.world.rev,
      note: "Objetos de los clientes MCP reemplazados (por sesión). Los que publica el motor local (source: motor) no se tocan.",
    };
  }

  /** Valida un objeto con la forma del ObjectState del contrato y lo normaliza (centro y caja si faltan). */
  objectFrom(o, where, was, now) {
    const pt = (p) => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite);
    if (typeof o?.id !== "string" || !o.id || o.id.length > 64) throw new Error(`${where}.id debe ser un texto de 1 a 64 caracteres.`);
    const poly = o.polygon_mm;
    if (!Array.isArray(poly) || poly.length < 3 || poly.length > 64 || !poly.every(pt))
      throw new Error(`${where}.polygon_mm debe tener de 3 a 64 puntos [x, y] en mm.`);
    if (o.center_mm != null && !pt(o.center_mm)) throw new Error(`${where}.center_mm debe ser [x, y].`);
    if (o.bbox_mm != null && !(Array.isArray(o.bbox_mm) && o.bbox_mm.length === 4 && o.bbox_mm.every(Number.isFinite)))
      throw new Error(`${where}.bbox_mm debe ser [x, y, ancho, alto].`);
    const xs = poly.map((p) => p[0]);
    const ys = poly.map((p) => p[1]);
    return {
      id: o.id,
      label: String(o.label ?? "").slice(0, 80),
      confidence: Number.isFinite(o.confidence) ? Math.min(1, Math.max(0, o.confidence)) : 1,
      polygon_mm: poly.map((p) => [round(p[0]), round(p[1])]),
      center_mm: (o.center_mm ?? [xs.reduce((a, v) => a + v, 0) / xs.length, ys.reduce((a, v) => a + v, 0) / ys.length]).map((v) => round(v)),
      bbox_mm: o.bbox_mm ?? [Math.min(...xs), Math.min(...ys), Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)].map((v) => round(v)),
      since_ns: was?.since_ns ?? now,
      last_seen_ns: now,
    };
  }

  /** Objetos del mundo por fuente: «mcp» (scenes action objects) y «motor» (motor local). Cada fuente agrega, mueve
   *  y quita solo los suyos: ninguna borra los de otra, y un id de otra fuente no se pisa. `replace` quita los de la
   *  fuente que no vienen en `upsert`. `strict`: un objeto inválido rechaza todo; si no, se salta y se informa.
   *  Máximo 50 por fuente. Emite object_appeared / object_moved (más de 10 mm) / object_removed. */
  mergeObjects(source, { upsert = [], remove = [], replace = false, strict = false } = {}) {
    if (!Array.isArray(upsert) || !Array.isArray(remove)) throw new Error("upsert y remove deben ser listas.");
    const now = Date.now() * 1e6;
    const old = new Map(this.world.objects.map((o) => [o.id, o]));
    const mine = (o) => (o.source ?? "mcp") === source;
    const skipped = [];
    const fresh = new Map(); // id → objeto normalizado de esta fuente
    upsert.forEach((o, i) => {
      const where = `objects[${i}]`;
      try {
        const obj = this.objectFrom(o, where, old.get(o?.id), now);
        if (fresh.has(obj.id)) throw new Error(`${where}.id «${obj.id}» está repetido.`);
        const was = old.get(obj.id);
        if (was && !mine(was)) throw new Error(`${where}.id «${obj.id}» ya lo publica otra fuente (${was.source ?? "mcp"}).`);
        fresh.set(obj.id, { ...obj, source });
      } catch (err) {
        if (strict) throw err;
        skipped.push(err.message);
      }
    });
    const drop = new Set(remove.filter((id) => old.has(id) && mine(old.get(id)) && !fresh.has(id)));
    if (replace) for (const o of old.values()) if (mine(o) && !fresh.has(o.id)) drop.add(o.id);
    // Tope por fuente: los que ya estaban se actualizan; los nuevos entran mientras quepan.
    let count = [...old.values()].filter((o) => mine(o) && !drop.has(o.id)).length;
    for (const [id] of fresh) {
      if (old.has(id)) continue;
      if (count < 50) count++;
      else {
        if (strict) throw new Error("Máximo 50 objetos.");
        fresh.delete(id);
        skipped.push(`«${id}» no cabe: máximo 50 objetos por fuente.`);
      }
    }
    const next = this.world.objects.filter((o) => !drop.has(o.id)).map((o) => fresh.get(o.id) ?? o);
    for (const [id, o] of fresh) if (!old.has(id)) next.push(o);
    const added = [];
    const moved = [];
    if (fresh.size || drop.size) {
      this.world.objects = next;
      this.changed(); // sube rev y manda el snapshot: scene los trae y los widgets anclados los siguen
      // Eventos para quien espera con events_wait: aparece, se mueve más de 10 mm, desaparece.
      for (const [id, o] of fresh) {
        const was = old.get(id);
        if (!was) (added.push(id), this.emit({ type: "object_appeared", object: o }));
        else if (Math.hypot(o.center_mm[0] - was.center_mm[0], o.center_mm[1] - was.center_mm[1]) > 10) {
          moved.push(id);
          this.emit({ type: "object_moved", object_id: id, center_mm: o.center_mm, polygon_mm: o.polygon_mm });
        }
      }
      for (const id of drop) this.emit({ type: "object_removed", object_id: id, label: old.get(id).label });
    }
    return { objects: next.filter(mine).map((o) => o.id), added, moved, removed: [...drop], skipped };
  }

  put(widgets, sceneName) {
    widgets = widgets.map((w) => this.resolve(w));
    const scene = this.sceneOf(sceneName);
    const live = scene.name === this.active || scene === this.global;
    const byId = new Map(scene.widgets.map((w) => [w.id, w]));
    for (const w of widgets) {
      byId.set(w.id, w);
      if (!live) continue;
      clearTimeout(this.ttl.get(w.id));
      if (w.ttl_s) this.ttl.set(w.id, setTimeout(() => this.drop([w.id]), w.ttl_s * 1000));
    }
    scene.widgets = [...byId.values()];
    if (live) this.world.widgets = this.liveWidgets();
    this.changed();
    if (!widgets.every((w) => w.id.startsWith("pcx:"))) this.persist(); // las preguntas de ask no se guardan
  }

  drop(ids, sceneName) {
    const scene = this.sceneOf(sceneName);
    const gone = scene.widgets.filter((w) => ids.includes(w.id)).map((w) => w.id);
    if (!gone.length) return gone;
    scene.widgets = scene.widgets.filter((w) => !ids.includes(w.id));
    if (scene.name === this.active || scene === this.global) this.world.widgets = this.liveWidgets();
    this.changed();
    if (!gone.every((id) => id.startsWith("pcx:"))) this.persist();
    return gone;
  }

  /** Aviso si un widget del agente pisa la zona reservada (barra de pestañas a la izquierda o esquina de voz). */
  reservedHit(widgets) {
    const zones = uiRects(this.world.surface, { ...this.ui, scenes: this.scenes.map((s) => s.name), voice: this.ui.voice ?? {} }).filter((z) =>
      ["pcx:strip", "pcx:voice"].includes(z.id),
    );
    const box = (w) => {
      if (w.shader === "hands") return null;
      if (isHtml(w)) return htmlRect(w, this.world);
      if (w.rect_mm) return w.rect_mm;
      const poly = w.polygon_mm;
      if (poly) {
        const xs = poly.map((q) => q[0]);
        const ys = poly.map((q) => q[1]);
        return [Math.min(...xs), Math.min(...ys), Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)];
      }
      const p = w.anchor?.point_mm;
      if (!p) return null;
      const r = w.type === "halo" ? (w.radius_mm ?? 60) : w.type === "label" ? (w.size_mm ?? 28) : 40;
      return [p[0] - r, p[1] - r, 2 * r, 2 * r];
    };
    const hits = [];
    for (const w of widgets) {
      const b = box(w);
      if (!b) continue;
      for (const z of zones) {
        const r = z.rect;
        if (b[0] < r[0] + r[2] && b[0] + b[2] > r[0] && b[1] < r[1] + r[3] && b[1] + b[3] > r[1])
          hits.push(`«${w.id}» pisa ${z.id === "pcx:strip" ? "la barra de pestañas (lado izquierdo)" : "la esquina del botón de voz"} [${r.map(Math.round).join(", ")}] mm`);
      }
    }
    return hits.length ? { reserved: `Zona reservada de la mesa: ${hits.join("; ")}. Se dibuja igual, pero la interfaz de la mesa queda encima.` } : {};
  }

  // ------------------------------------------------------------------ tools
  async call({ call_id, tool, args }) {
    const t0 = performance.now();
    const request_id = args?.request_id ?? null;
    let ok = true;
    let data;
    let images = [];
    try {
      const fn = this.tools[tool];
      if (!fn) throw new Error(`El nodo navegador todavía no soporta «${tool}». Disponibles: look, project, remove, ask, calibrate, scenes.`);
      const out = await fn.call(this, args || {});
      data = out.data;
      images = out.images || [];
      if (this.outdated && data && typeof data === "object") data.console_outdated = this.outdated; // la consola corre código viejo
    } catch (err) {
      ok = false;
      data = { error: String(err.message || err) };
    }
    this.send({
      type: "tool_result",
      call_id,
      request_id,
      ok,
      structured: data,
      content: [...images, { type: "text", text: JSON.stringify(data) }],
    });
    this.dispatchEvent(
      new CustomEvent("call", { detail: { tool, args, ok, data, image: images[0], ms: Math.round(performance.now() - t0) } }),
    );
  }

  tools = {
    async look(args) {
      // Con el motor local conectado y con cámara, la foto (también la limpia) la toma el motor. Si no hay motor o
      // falla, sigue como siempre con la cámara de la consola.
      let motor = null;
      let motor_note;
      try {
        motor = (await this.hooks.motorLook?.(args)) ?? null;
      } catch (err) {
        motor_note = `El motor local no entregó la foto (${err.message}): va la de la cámara de la consola.`;
      }
      // clean: true → foto limpia: se apaga lo proyectado, se espera la demora proyector→cámara y se repone.
      let blanked_ms = motor?.blanked_ms ?? null;
      let shot = motor;
      if (!shot && args.clean && !this.calibrating) {
        const t0 = performance.now();
        this.hooks.showCalib({ dots: [] }); // todo negro, también el html y la interfaz de la mesa
        try {
          await sleep(this.settle ? Math.min(1500, this.settle) : 350);
          shot = await this.hooks.frame(args.camera_id);
        } finally {
          this.hooks.showCalib(null);
        }
        blanked_ms = Math.round(performance.now() - t0);
      } else if (!shot) shot = await this.hooks.frame(args.camera_id);
      const { canvas, camera } = shot;
      const { surface, widgets } = this.world;
      const calibrated = this.calib?.camera_id === camera.id;
      this.lookSize = [canvas.width, canvas.height];
      if (calibrated && args.annotate !== false) annotate(canvas.getContext("2d"), this.calib, surface);
      return {
        images: [{ type: "image", data: canvas.toDataURL("image/jpeg", 0.82).split(",")[1], mime_type: "image/jpeg" }],
        data: {
          camera_id: camera.id,
          camera: camera.meta.label,
          scene: this.active,
          ...(motor ? { via: "motor" } : {}),
          ...(motor_note ? { motor_note } : {}),
          ...(args.clean
            ? motor
              ? { clean: true, ...(blanked_ms === null ? {} : { blanked_ms }), clean_note: "Foto limpia del motor local: apagó el proyector (blank/unblank) y usó cuadros posteriores." }
              : blanked_ms === null
                ? { clean: false, clean_note: "Hay una calibración en curso: la foto no es limpia." }
                : { clean: true, blanked_ms, clean_note: this.settle ? "Espera según la demora proyector→cámara medida por calibrate." : "Sin calibrate previo: espera fija de 350 ms." }
            : {}),
          image_px: [canvas.width, canvas.height],
          surface_mm: [surface.width_mm, surface.height_mm],
          widgets: widgets.map((w) => w.id),
          projector_open: this.hooks.projectorOpen(),
          calibrated,
          ...(calibrated
            ? {
                matrix_px_to_mm: this.calib.matrix.map((v) => round(v, 6)),
                residual_mm: this.calib.residual_mm,
                ...(this.calib.lens
                  ? {
                      lens: this.calib.lens,
                      lens_note:
                        "Hay modelo de lente: matrix_px_to_mm vale para píxeles ya corregidos de distorsión. No conviertas a mano: " +
                        "manda píxeles de esta foto en anchor.point_px y el nodo aplica lente + homografía.",
                    }
                  : {}),
                summary:
                  "Cuadro en vivo con la superficie proyectable y una grilla cada 100 mm (annotate=false la quita). Para apuntar a " +
                  "algo que ves, usa sus píxeles en esta foto: anchor.point_px en project (polygon_px en halo, from_px/to_px en arrow).",
              }
            : {
                summary:
                  "Cuadro en vivo, sin calibrar: no hay relación entre píxeles y mm. Llama a calibrate (quick, unos 10 s) y vuelve " +
                  "a mirar; este nodo corre en un navegador y no detecta objetos.",
              }),
        },
      };
    },

    // `scene` con escenas: la activa por defecto, o cualquier otra con scene:"nombre" aunque no esté a la vista.
    async scene(args) {
      const target = this.sceneOf(args.scene);
      const { hands, devices, ...world } = this.world;
      return {
        data: {
          ...world,
          widgets: target.widgets,
          ...(target !== this.global && this.global.widgets.length ? { global_layer: this.global.widgets } : {}), // lo que además se dibuja encima
          ...(args.include_hands ? { hands } : {}),
          ...(args.include_devices ? { devices } : {}),
          ...this.sceneState(),
          scene: target.name,
          scene_active: target.name === this.active || target === this.global,
        },
      };
    },

    async scenes(args) {
      return { data: this.scenesTool(args) };
    },

    async present(args) {
      const data = this.presentTool(args);
      const url = await this.hooks.presentUrl?.().catch(() => null);
      return { data: { ...data, ...(url ? { url } : { url_note: "No se pudo crear el enlace; en la mesa está «Copiar enlace para presentar»." }) } };
    },

    async project(args) {
      this.put(args.widgets, args.scene);
      const open = this.hooks.projectorOpen();
      const target = this.sceneOf(args.scene).name;
      return {
        data: {
          ids: args.widgets.map((w) => w.id),
          ...this.sceneState(),
          scene: target,
          scene_active: target === this.active || target === "*",
          ...(target === "*"
            ? { scene_note: "Quedó en la capa global: se dibuja en todas las escenas. Se quita con remove y scene: '*'." }
            : target === this.active
              ? {}
              : { scene_note: `Quedó en la escena «${target}», que no está a la vista (la activa es «${this.active}»).` }),
          ...(this.ui.enabled ? this.reservedHit(args.widgets) : {}),
          surface_mm: [this.world.surface.width_mm, this.world.surface.height_mm],
          projector_open: open,
          ...(args.widgets.some((w) => ["label", "panel", "button"].includes(w.type))
            ? { text: `label.size_mm fija la altura del texto (por defecto 28 mm; para leerlo desde la cámara usa 30 o más). Escala de la consola: ×${this.hooks.textScale()}.` }
            : {}),
          ...(args.widgets.some((w) => w.type === "effect")
            ? {
                effects:
                  "El nodo navegador no corre los shaders WGSL del compositor. Tres shaders son especiales. «screen»: muestra en rect_mm, en vivo, la pantalla o ventana del computador " +
                  "que la persona comparte con el botón «Compartir pantalla» de la consola (params.fit: contain o cover; hasta entonces se ve un aviso; flow_emitted screen_share avisa cuando empieza o termina; " +
                  "no viaja al servidor ni al visor, y look la ve solo a través de la cámara). «html»: monta params.html (+ params.css, " +
                  "params.rotate_deg) como página aislada dentro de rect_mm, con 1 px de CSS = 1 mm; los elementos con data-press=\"nombre\" " +
                  "emiten widget_pressed{widget_id, value: nombre} con el dedo o el clic; sin red, salvo imágenes https. " +
                  "Interactivo (juegos con la mano): agrega params.js, un script que corre dentro de esa página aislada (origen opaco, sin red de ningún tipo; " +
                  "los <script> dentro de params.html no corren). Protocolo, por postMessage y envuelto en el objeto wui: " +
                  "wui.onHands((hands) => …) llega ~30 veces por segundo con hands: [{tip:[x,y], palm:[x,y], pinch:bool, inside:bool, handed, points:[[x,y]×21]}] " +
                  "en mm relativos al rect del widget (= px de CSS, origen arriba a la izquierda; tip es la punta del índice, points los 21 puntos de MediaPipe); " +
                  "wui.emit(valor) te manda widget_pressed{widget_id, value: valor} (texto o JSON, máx. 500 caracteres y 10 por segundo); wui.size es [ancho, alto]. " +
                  "Ejemplo mínimo, una pelota que sigue el dedo: {type:'effect', id:'juego', shader:'html', rect_mm:[300,100,400,300], params:{" +
                  "html:'<div id=b></div>', css:'#b{position:absolute;width:40px;height:40px;margin:-20px;border-radius:50%;background:#ff5b24}', " +
                  "js:'const b=document.getElementById(\"b\");let dentro=false;wui.onHands(h=>{if(!h[0])return;const[x,y]=h[0].tip;b.style.left=x+\"px\";b.style.top=y+\"px\";" +
                  "if(h[0].inside!==dentro){dentro=h[0].inside;wui.emit(dentro?\"entra\":\"sale\")}})'}}. " +
                  "Sonido: wui.onAudio(({freq, level, hz_per_band}) => …) entrega ~30 veces por segundo el espectro del micrófono del computador (freq: 128 bandas de 0 a 255, " +
                  "de graves a agudos, cada una de hz_per_band Hz; level: volumen de 0 a 1). Solo llega el espectro, nunca el audio, y el micrófono se abre solo mientras un widget esté suscrito. " +
                  "Ejemplo, barras de espectro: params:{html:'<canvas id=c></canvas>', js:'const c=document.getElementById(\"c\");c.width=wui.size[0];c.height=wui.size[1];const g=c.getContext(\"2d\");" +
                  "wui.onAudio(({freq})=>{g.clearRect(0,0,c.width,c.height);g.fillStyle=\"#ff5b24\";const w=c.width/freq.length;freq.forEach((v,i)=>g.fillRect(i*w,c.height,w-1,-v/255*c.height))})'}. " +
                  "Las manos necesitan calibrate hecho. Cada pantalla (proyector, consola, visor) corre su copia del script; solo la del proyector manda eventos. " +
                  "Si el script deja de responder 4 s se detiene solo (flow_emitted widget_killed) y sus errores llegan como flow_emitted widget_error. «hands»: modo manos " +
                  "(esqueleto sobre la mano, estela del índice y toque por permanencia; params: skeleton, trail, color). " +
                  "Cualquier otro shader se ve igual: anillos de luz que pulsan dentro de rect_mm o polygon_mm, con params.color.",
              }
            : {}),
          ...(args.widgets.some((w) => w.type === "effect" && w.shader === "screen") && !this.hooks.screenOn?.()
            ? { screen: "Nadie está compartiendo pantalla todavía: pídele a la persona que apriete «Compartir pantalla» en la consola y elija qué mostrar. El widget muestra un aviso mientras tanto." }
            : {}),
          ...(open ? {} : { warning: "La ventana del proyector está cerrada: solo se ve en la vista previa de la consola." }),
        },
      };
    },

    async calibrate(args) {
      // `model`: carga un modelo ya medido (lente + homografía) sin proyectar nada. Fuera del contrato: va como campo extra.
      if (args.model) return { data: this.loadModel(args.model) };
      const quick = await this.calibrate({ ...args, mode: args.mode === "precise" ? "quick" : args.mode });
      return { data: args.mode === "precise" ? await this.calibrateLens(args, quick) : quick };
    },

    async remove(args) {
      const target = this.sceneOf(args.scene).name;
      const removed = this.drop(args.ids, args.scene);
      const inGlobal = target === "*" ? [] : args.ids.filter((id) => !removed.includes(id) && this.global.widgets.some((w) => w.id === id));
      return {
        data: {
          removed,
          scene: target,
          scene_active: target === this.active || target === "*",
          ...(inGlobal.length ? { global_note: `${inGlobal.join(", ")} ${inGlobal.length === 1 ? "está" : "están"} en la capa global: quítalo con scene: '*'.` } : {}),
        },
      };
    },

    async ask(args) {
      const id = args.request_id;
      const { width_mm: W, height_mm: H } = this.world.surface;
      const c = args.anchor?.point_mm || [W / 2, H / 2];
      const n = args.options.length;
      const bw = Math.min(200, (W - 80) / n - 20);
      const widgets = [
        { type: "label", id: `pcx:ask:${id}:q`, text: args.question, size_mm: 26, color: "#FFFFFF", z: 900, anchor: { point_mm: [c[0], c[1] - 70] } },
        ...args.options.map((label, i) => ({
          type: "button",
          id: `pcx:ask:${id}:${i}`,
          label,
          color: "#FF5B24",
          z: 900,
          rect_mm: [c[0] - (n * (bw + 20) - 20) / 2 + i * (bw + 20), c[1] - 10, bw, 80],
        })),
      ];
      this.put(widgets);
      const index = await new Promise((resolve) => {
        this.asks.set(id, { resolve });
        setTimeout(() => resolve(null), (args.timeout_s ?? 60) * 1000);
      });
      this.asks.delete(id);
      this.drop(widgets.map((w) => w.id));
      const answer = index === null ? null : args.options[index];
      const result = { answer, index, timed_out: index === null };
      this.emit({ type: "ask_answered", request_id: id, ...result });
      return { data: result };
    },
  };
}
