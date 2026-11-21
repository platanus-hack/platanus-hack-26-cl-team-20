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
    this.active = "Principal";
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
    ws.onmessage = (e) => this.receive(JSON.parse(e.data));
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
    if (msg.type === "state" && this.pristine && msg.scenes?.list?.length) {
      this.scenes = msg.scenes.list.map((s) => ({ name: String(s.name), widgets: s.widgets || [] }));
      this.active = this.scenes.some((s) => s.name === msg.scenes.active) ? msg.scenes.active : this.scenes[0].name;
      this.world.widgets = this.sceneOf().widgets;
      this.changed();
      this.dispatchEvent(new CustomEvent("scenes"));
    }
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
  sceneOf(name) {
    const wanted = name ?? this.active;
    const scene = this.scenes.find((s) => s.name === wanted);
    if (!scene) throw new Error(`No existe la escena «${wanted}». Hay: ${this.scenes.map((s) => s.name).join(", ")}.`);
    return scene;
  }

  /** Lo que el agente necesita para saber con qué escena está trabajando. */
  sceneState() {
    return { scene: this.active, scenes: this.scenes.map((s) => ({ name: s.name, widgets: s.widgets.length, active: s.name === this.active })) };
  }

  /** Guarda las escenas en el gateway (por sesión), sin apurarse: un mensaje fuera del contrato. */
  persist() {
    this.pristine = false;
    clearTimeout(this.saving);
    const save = () => ((this.saved = performance.now()), this.send({ type: "state", scenes: { active: this.active, list: this.scenes } }));
    if (performance.now() - (this.saved ?? 0) > 400) save(); // al tiro; si vienen cambios seguidos, el último sale al final
    else this.saving = setTimeout(save, 400);
    this.dispatchEvent(new CustomEvent("scenes"));
  }

  scenesTool(args, by = "agent") {
    const action = args.action ?? "list";
    const name = args.name?.trim();
    const fresh = (n) => {
      if (!n || !SCENE_NAME.test(n)) throw new Error("El nombre de la escena debe tener de 1 a 40 caracteres, sin «#» ni «:».");
      if (this.scenes.some((s) => s.name === n)) throw new Error(`Ya existe una escena «${n}».`);
      if (this.scenes.length >= 8) throw new Error("Máximo 8 escenas por mesa (no caben más pestañas en la franja).");
      return n;
    };
    const activate = (n) => {
      if (n === this.active) return;
      this.active = n;
      this.world.widgets = this.sceneOf().widgets;
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
    } else if (action !== "list") throw new Error("action debe ser list, create, activate, duplicate, delete o rename.");
    if (action !== "list") this.persist();
    return this.sceneState();
  }

  put(widgets, sceneName) {
    widgets = widgets.map((w) => this.resolve(w));
    const scene = this.sceneOf(sceneName);
    const live = scene.name === this.active;
    const byId = new Map(scene.widgets.map((w) => [w.id, w]));
    for (const w of widgets) {
      byId.set(w.id, w);
      if (!live) continue;
      clearTimeout(this.ttl.get(w.id));
      if (w.ttl_s) this.ttl.set(w.id, setTimeout(() => this.drop([w.id]), w.ttl_s * 1000));
    }
    scene.widgets = [...byId.values()];
    if (live) this.world.widgets = scene.widgets;
    this.changed();
    if (!widgets.every((w) => w.id.startsWith("pcx:"))) this.persist(); // las preguntas de ask no se guardan
  }

  drop(ids, sceneName) {
    const scene = this.sceneOf(sceneName);
    const gone = scene.widgets.filter((w) => ids.includes(w.id)).map((w) => w.id);
    if (!gone.length) return gone;
    scene.widgets = scene.widgets.filter((w) => !ids.includes(w.id));
    if (scene.name === this.active) this.world.widgets = scene.widgets;
    this.changed();
    if (!gone.every((id) => id.startsWith("pcx:"))) this.persist();
    return gone;
  }

  /** Aviso si un widget del agente pisa la zona reservada (franja de pestañas o esquina de voz). */
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
          hits.push(`«${w.id}» pisa ${z.id === "pcx:strip" ? "la franja de pestañas" : "la esquina del botón de voz"} [${r.map(Math.round).join(", ")}] mm`);
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
      const { canvas, camera } = await this.hooks.frame(args.camera_id);
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
          ...(args.include_hands ? { hands } : {}),
          ...(args.include_devices ? { devices } : {}),
          ...this.sceneState(),
          scene: target.name,
          scene_active: target.name === this.active,
        },
      };
    },

    async scenes(args) {
      return { data: this.scenesTool(args) };
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
          scene_active: target === this.active,
          ...(target === this.active ? {} : { scene_note: `Quedó en la escena «${target}», que no está a la vista (la activa es «${this.active}»).` }),
          ...(this.ui.enabled ? this.reservedHit(args.widgets) : {}),
          surface_mm: [this.world.surface.width_mm, this.world.surface.height_mm],
          projector_open: open,
          ...(args.widgets.some((w) => ["label", "panel", "button"].includes(w.type))
            ? { text: `label.size_mm fija la altura del texto (por defecto 28 mm; para leerlo desde la cámara usa 30 o más). Escala de la consola: ×${this.hooks.textScale()}.` }
            : {}),
          ...(args.widgets.some((w) => w.type === "effect")
            ? {
                effects:
                  "El nodo navegador no corre los shaders WGSL del compositor. Dos shaders son especiales. «html»: monta params.html (+ params.css, " +
                  "params.rotate_deg) como página aislada dentro de rect_mm, con 1 px de CSS = 1 mm; los elementos con data-press=\"nombre\" " +
                  "emiten widget_pressed{widget_id, value: nombre} con el dedo o el clic; sin scripts ni red, salvo imágenes https. «hands»: modo manos " +
                  "(esqueleto sobre la mano, estela del índice y toque por permanencia; params: skeleton, trail, color). " +
                  "Cualquier otro shader se ve igual: anillos de luz que pulsan dentro de rect_mm o polygon_mm, con params.color.",
              }
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
      return { data: { removed: this.drop(args.ids, args.scene), scene: target, scene_active: target === this.active } };
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
