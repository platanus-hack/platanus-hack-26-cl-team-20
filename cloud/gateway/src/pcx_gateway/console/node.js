// El navegador como nodo de pcx: habla por WSS con el gateway el mismo protocolo que el nodo Rust
// (contracts/src/pcx_contracts/cloud.py) y resuelve las tools con la cámara y la ventana del proyector.

const CONTRACT = 2;
const now_ns = () => Date.now() * 1e6;
const uuid = () => crypto.randomUUID();

export class BrowserNode extends EventTarget {
  /** hooks: { capture(cameraId) → {data, mime, w, h, camera}, projectorOpen() → bool } */
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
  press(widget, point_mm) {
    const m = /^pcx:ask:(.+):(\d+)$/.exec(widget.id);
    if (m && this.asks.has(m[1])) return this.asks.get(m[1]).resolve(Number(m[2]));
    this.emit({
      type: "widget_pressed",
      widget_id: widget.id,
      value: widget.on_press?.value ?? null,
      trigger: "api",
      point_mm,
    });
  }

  put(widgets) {
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
      if (!fn) throw new Error(`El nodo navegador todavía no soporta «${tool}». Disponibles: look, project, remove, ask.`);
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
      const shot = await this.hooks.capture(args.camera_id);
      const { surface, widgets } = this.world;
      return {
        images: [{ type: "image", data: shot.data, mime_type: shot.mime }],
        data: {
          camera_id: shot.camera.id,
          camera: shot.camera.meta.label,
          image_px: [shot.w, shot.h],
          surface_mm: [surface.width_mm, surface.height_mm],
          widgets: widgets.map((w) => w.id),
          projector_open: this.hooks.projectorOpen(),
          summary:
            "Cuadro en vivo de la cámara, sin anotar. Este nodo corre en un navegador: no hay calibración " +
            "cámara↔proyección ni detección de objetos. Para ubicar algo, proyecta una marca, vuelve a mirar y corrige.",
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
          ...(open ? {} : { warning: "La ventana del proyector está cerrada: solo se ve en la vista previa de la consola." }),
        },
      };
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
