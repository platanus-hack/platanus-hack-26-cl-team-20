// El navegador como nodo de pcx: habla por WSS con el gateway el mismo protocolo que el nodo Rust
// (contracts/src/pcx_contracts/cloud.py) y resuelve las tools con la cámara y la ventana del proyector.

import { annotate, apply, diff, findDot, invert, litArea, ransacH, signal } from "./calib.js";

const CONTRACT = 2;
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
    if (msg.type === "viewers") {
      this.viewers = msg.count;
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
  }

  // Para quienes miran en vivo (fuera del contrato; el gateway lo reenvía tal cual y solo si hay alguien mirando).
  sendView(kind, data) {
    if (this.viewers && this.ready) this.ws.send(JSON.stringify({ type: "view", kind, ...data }));
  }

  /** Cuadro JPEG de la cenital. Si la subida va atrasada, se salta el cuadro en vez de encolarlo. */
  sendFrame(blob) {
    if (this.viewers && this.ready && this.ws.bufferedAmount < 300_000) this.ws.send(blob);
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
    return apply(c.matrix, p).map((v) => round(v));
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
    const [nx, ny] = mode === "precise" ? [5, 4] : [3, 3];
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
      const settle = Math.min(1500, Math.max(300, lag * 1.5 + 150));
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
    if (found.length < 4)
      throw fail(`Solo vi ${found.length} de ${total} marcas en la cámara ${camera_id}; con menos de 4 no se puede calcular.`, { marks });
    const px = found.map((f) => f.px);
    const mm = found.map((f) => f.mm);
    if (mode === "verify") {
      const e = px.map((p, i) => Math.hypot(...apply(this.calib.matrix, p).map((v, a) => v - mm[i][a])));
      const rms = Math.sqrt(e.reduce((a, v) => a + v * v, 0) / e.length);
      return { camera_id, mode, points_found: px.length, points_total: total, residual_mm: round(rms), max_error_mm: round(Math.max(...e)), ok: rms < 12 };
    }
    const fit = ransacH(px, mm);
    const good = fit?.inliers.length ?? 0;
    if (good < need)
      throw fail(
        `Vi ${found.length} de ${total} marcas, pero solo ${good} calzan entre sí (necesito ${need}); el resto son reflejos o cosas que se movieron.`,
        { marks },
      );
    const residual_mm = round(fit.residual);
    if (residual_mm > 12) throw fail(`${good} marcas calzan, pero con ${residual_mm} mm de error: no la guardé.`, { marks });
    const M = fit.H;
    const toPx = invert(M);
    const corners = [[0, 0], [W, 0], [W, H], [0, H]].map((p) => apply(toPx, p).map((v) => Math.round(v)));
    const calib = { camera_id, matrix: M, image_px, residual_mm, points: good, total, version: (this.calib?.version ?? 0) + 1 };
    this.setCalib(calib);
    this.hooks.saveCalib(calib);
    return {
      camera_id,
      mode,
      points_found: found.length,
      points_used: good,
      points_total: total,
      residual_mm,
      image_px,
      matrix_px_to_mm: M.map((v) => round(v, 6)),
      surface_corners_px: corners,
      rotation_deg: Math.round((Math.atan2(corners[1][1] - corners[0][1], corners[1][0] - corners[0][0]) * 180) / Math.PI),
      note:
        "Homografía guardada: píxeles de la foto de look → mm de la mesa. look ahora trae la grilla en mm; en project puedes usar " +
        "anchor.point_px (y polygon_px en halo, from_px/to_px en arrow) con píxeles de esa foto. Si mueven la cámara o el proyector, calibra de nuevo.",
    };
  }

  /** Manos vistas por la cámara cenital (para `scene` con include_hands). */
  setHands(hands) {
    this.world.hands = hands;
    this.changed();
  }

  put(widgets) {
    widgets = widgets.map((w) => this.resolve(w));
    const byId = new Map(this.world.widgets.map((w) => [w.id, w]));
    for (const w of widgets) {
      byId.set(w.id, w);
      clearTimeout(this.ttl.get(w.id));
      if (w.ttl_s) this.ttl.set(w.id, setTimeout(() => this.drop([w.id]), w.ttl_s * 1000));
    }
    this.world.widgets = [...byId.values()];
    this.changed();
  }

  drop(ids) {
    const gone = this.world.widgets.filter((w) => ids.includes(w.id)).map((w) => w.id);
    if (!gone.length) return gone;
    this.world.widgets = this.world.widgets.filter((w) => !ids.includes(w.id));
    this.changed();
    return gone;
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
      if (!fn) throw new Error(`El nodo navegador todavía no soporta «${tool}». Disponibles: look, project, remove, ask, calibrate.`);
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
      if (calibrated && args.annotate !== false) annotate(canvas.getContext("2d"), this.calib.matrix, surface);
      return {
        images: [{ type: "image", data: canvas.toDataURL("image/jpeg", 0.82).split(",")[1], mime_type: "image/jpeg" }],
        data: {
          camera_id: camera.id,
          camera: camera.meta.label,
          image_px: [canvas.width, canvas.height],
          surface_mm: [surface.width_mm, surface.height_mm],
          widgets: widgets.map((w) => w.id),
          projector_open: this.hooks.projectorOpen(),
          calibrated,
          ...(calibrated
            ? {
                matrix_px_to_mm: this.calib.matrix.map((v) => round(v, 6)),
                residual_mm: this.calib.residual_mm,
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

    async project(args) {
      this.put(args.widgets);
      const open = this.hooks.projectorOpen();
      return {
        data: {
          ids: args.widgets.map((w) => w.id),
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
      return { data: await this.calibrate(args) };
    },

    async remove(args) {
      return { data: { removed: this.drop(args.ids) } };
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
