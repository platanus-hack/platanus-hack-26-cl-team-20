// Motor local de pcx (Rust, en este computador): cámara → segmentación → objetos en mm de la mesa.
// La consola se conecta por WebSocket con el protocolo motor v0.1 (hello/welcome, calib, scene, blank/unblank, hands,
// objects, event, look) y hace de puente: le manda la calibración, la escena activa y la presencia de manos, apaga el
// proyector cuando el motor pide una foto limpia, publica en el mundo los objetos que encuentra (fuente «motor») y
// reenvía al agente los eventos de sus módulos de escena. Sin motor, nada cambia.

export const MOTOR_DEFAULT = "ws://127.0.0.1:9230/v1/ws";
const V = 0;
const BLANK_MS = 3000; // si el motor no dice max_ms
const BLANK_MAX_MS = 10000; // tope duro: nunca más de 10 s a oscuras
const HANDS_GONE_MS = 120; // un cuadro sin mano no es que la mano se fue
const now_ns = () => Date.now() * 1e6;
const LOOPBACK = /^(127\.\d+\.\d+\.\d+|localhost|\[::1\])$/i;

/** Normaliza la URL del motor: sin esquema → ws://, http(s) → ws(s), sin ruta → /v1/ws (como la que imprime `pcx up`). */
export function motorUrl(raw) {
  let s = String(raw ?? "").trim();
  if (!s) return MOTOR_DEFAULT;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = `ws://${s}`;
  const u = new URL(s);
  if (u.protocol === "http:") u.protocol = "ws:";
  else if (u.protocol === "https:") u.protocol = "wss:";
  if (u.protocol !== "ws:" && u.protocol !== "wss:") throw new Error("La URL del motor debe ser ws:// o wss://.");
  if (u.pathname === "/" || u.pathname === "") u.pathname = "/v1/ws";
  u.hash = "";
  return u.toString();
}

/** Desde una página https solo se puede abrir ws:// hacia este mismo computador (127.0.0.1 o localhost). */
const RESERVED_EVENTS = new Set(["voice_command", "scene_changed", "slide_changed", "widget_killed", "widget_error", "widget_pressed", "screen_share"]);

export function mixedContent(url, pageProtocol) {
  const u = new URL(url);
  return pageProtocol === "https:" && u.protocol === "ws:" && !LOOPBACK.test(u.hostname);
}

/** URL y token del motor. `?motor=` y `?motor_token=` mandan y se sacan de la barra de direcciones; la URL queda en
 *  localStorage y el token solo en sessionStorage (se olvida al cerrar la pestaña). `auto`: conectar al cargar. */
export function motorSettings({ loc = location, hist = history, local = localStorage, session = sessionStorage } = {}) {
  const q = new URLSearchParams(loc.search);
  const url = q.get("motor");
  const token = q.get("motor_token");
  let urlRejected = false;
  if (url) {
    try {
      // Solo loopback por enlace: un `?motor=wss://ajeno` conectaría la mesa (cámara, calibración, proyector) a un
      // servidor de otro. Un motor en otra máquina se escribe a mano en el panel.
      const m = motorUrl(url);
      if (LOOPBACK.test(new URL(m).hostname)) local.setItem("pcx.motorUrl", m);
      else urlRejected = true;
    } catch {
      /* URL inválida: queda la anterior */
    }
  }
  if (token && !urlRejected) session.setItem("pcx.motorToken", token);
  if (url !== null || token !== null) {
    q.delete("motor");
    q.delete("motor_token");
    const rest = q.toString();
    hist.replaceState(hist.state, "", `${loc.pathname}${rest ? `?${rest}` : ""}${loc.hash}`);
  }
  const on = session.getItem("pcx.motorOn"); // "1" o "0" si la persona apretó Conectar o Desconectar
  const saved = session.getItem("pcx.motorToken") || "";
  return {
    url: local.getItem("pcx.motorUrl") || MOTOR_DEFAULT,
    token: saved,
    // Solo se conecta sola si hay emparejamiento (token o ?motor=): así una visita sin motor no ve el permiso de red local.
    auto: on === "1" || (on === null && !urlRejected && !!(url || saved)),
    save({ url: u, token: t, on: o } = {}) {
      if (u !== undefined) local.setItem("pcx.motorUrl", u);
      if (t !== undefined) (t ? session.setItem("pcx.motorToken", t) : session.removeItem("pcx.motorToken"));
      if (o !== undefined) session.setItem("pcx.motorOn", o ? "1" : "0");
    },
  };
}

async function decodeJpeg(b64, maxWidth = 1280) {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const bmp = await createImageBitmap(new Blob([bytes], { type: "image/jpeg" }));
  const k = Math.min(1, maxWidth / bmp.width);
  const c = Object.assign(document.createElement("canvas"), { width: Math.round(bmp.width * k), height: Math.round(bmp.height * k) });
  c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
  bmp.close?.();
  return c;
}

const encodeJpeg = (canvas) => canvas.toDataURL("image/jpeg", 0.9).split(",")[1];

export class Motor extends EventTarget {
  /**
   * node: BrowserNode (mundo, calibración, objetos). hooks:
   *  - blank(id, on, maxMs) → Promise<t_ns | null>: negro total en el proyector (on) o vuelta a la escena; resuelve
   *    cuando la ventana del proyector confirmó que ya pintó, o null si no hay cómo confirmarlo.
   *  - frame() → Promise<{canvas}>: cuadro de la cenital, para `need_frame` (motor sin cámara propia).
   *  - note(kind, text): registro de la consola. decode/encode: JPEG ↔ canvas (se reemplazan en los tests).
   */
  constructor({ node, hooks = {}, url = MOTOR_DEFAULT, token = "", sessionId = null, app = "pcx-console", WS = globalThis.WebSocket, retryMs = 500, pageProtocol = globalThis.location?.protocol }) {
    super();
    Object.assign(this, { node, url: motorUrl(url), token, sessionId, app, WS, retryMs, pageProtocol });
    this.hooks = { decode: decodeJpeg, encode: encodeJpeg, note: () => {}, blank: async () => null, frame: null, ...hooks };
    this.state = "off"; // off | connecting | online | offline | rejected
    this.wanted = false;
    this.delay = retryMs;
    this.ws = null;
    this.welcome = null;
    this.status = null; // último `status` del motor
    this.problem = null; // texto del último error
    this.fails = 0;
    this.pending = new Map(); // "look_frame:id" → {resolve, reject, timer}
    this.blank = null; // {id, timer, t0}
    this.lastBlank = null; // {id, t0, t1}: para decir cuánto estuvo apagado en un look limpio
    this.handsState = null; // última presencia de manos conocida (null: no se sabe)
    this.handsGone = null;
    this.sentCalib = null;
    this.sentScene = undefined; // última escena mandada (null = ninguna); undefined = todavía no se mandó
    this.fullNext = false; // v0.1: el primer `objects` después de welcome trae todos los objetos vigentes
    this.rev = null;
    node.addEventListener("world", () => (this.syncCalib(), this.syncScene()));
    node.addEventListener("scenes", () => this.syncScene());
  }

  get online() {
    return this.state === "online";
  }

  // ------------------------------------------------------------------ enlace
  configure({ url, token } = {}) {
    if (url !== undefined) this.url = motorUrl(url);
    if (token !== undefined) this.token = token;
  }

  connect() {
    this.wanted = true;
    this.delay = this.retryMs;
    this.fails = 0;
    clearTimeout(this.retry);
    this.open();
  }

  disconnect() {
    this.wanted = false;
    clearTimeout(this.retry);
    const ws = this.ws;
    this.ws = null;
    ws?.close(1000);
    this.dropped();
    this.problem = null;
    this.setState("off");
  }

  open() {
    if (this.ws) {
      const old = this.ws;
      this.ws = null;
      old.close(1000);
    }
    if (mixedContent(this.url, this.pageProtocol)) {
      this.problem = "Desde https solo se puede usar ws:// hacia este computador (127.0.0.1 o localhost); para un motor remoto usa wss://.";
      return this.setState("rejected");
    }
    let ws;
    try {
      ws = this.ws = new this.WS(this.url);
    } catch (err) {
      this.problem = `No se pudo abrir ${this.url}: ${err.message}`;
      return this.setState("rejected");
    }
    this.setState("connecting");
    ws.onopen = () => {
      if (ws === this.ws) this.send({ type: "hello", v: V, token: this.token, session_id: this.sessionId, app: this.app });
    };
    ws.onmessage = (e) => {
      if (ws !== this.ws) return;
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg && typeof msg.type === "string") this.receive(msg);
    };
    ws.onerror = () => {}; // siempre llega onclose después
    ws.onclose = (e) => {
      if (ws !== this.ws) return;
      this.ws = null;
      const wasOnline = this.online;
      this.dropped();
      // 4401/4403/1008: token u origen rechazados. No se reintenta solo (otro intento daría lo mismo).
      if (this.rejected || [4401, 4403, 1008].includes(e?.code)) {
        this.problem ??= "El motor rechazó la conexión (token u origen).";
        this.rejected = false;
        return this.setState("rejected");
      }
      if (!this.wanted) return this.setState("off");
      if (wasOnline) this.hooks.note("motor", "se perdió la conexión con el motor local; reintentando");
      this.fails += 1;
      this.setState("offline");
      this.retry = setTimeout(() => this.wanted && this.open(), this.delay);
      this.delay = Math.min(this.delay * 2, 10000);
    };
  }

  /** Se cortó: nada queda a oscuras y las fotos pedidas fallan (look vuelve a la cámara de la consola). */
  dropped() {
    this.welcome = null;
    this.sentCalib = null;
    this.sentScene = undefined;
    for (const [key, p] of this.pending) {
      clearTimeout(p.timer);
      p.reject(new Error("se cortó la conexión con el motor"));
      this.pending.delete(key);
    }
    if (this.blank) {
      const { id } = this.blank;
      clearTimeout(this.blank.timer);
      this.blank = null;
      this.hooks.blank(id, false, 0);
    }
  }

  setState(state) {
    this.state = state;
    this.dispatchEvent(new CustomEvent("state", { detail: state }));
  }

  send(msg) {
    if (this.ws?.readyState !== 1) return false; // 1 = OPEN
    this.ws.send(JSON.stringify(msg));
    return true;
  }

  receive(msg) {
    switch (msg.type) {
      case "welcome":
        this.welcome = msg;
        this.delay = this.retryMs;
        this.fails = 0;
        this.problem = msg.v === V ? null : `El motor habla el protocolo v${msg.v}; esta consola, v${V}.`;
        this.rev = null;
        // v0.1 (trae scenes o cameras): justo después manda todos sus objetos vigentes, que reemplazan a los que
        // quedaron de antes (motor reiniciado o consola recargada). Un motor v0 solo manda cambios: se fusionan.
        this.v01 = Array.isArray(msg.scenes) || Array.isArray(msg.cameras);
        this.fullNext = this.v01;
        this.setState("online");
        this.syncCalib();
        this.syncScene();
        if (this.handsState !== null) this.send({ type: "hands", present: this.handsState, t_ns: now_ns() });
        break;
      case "error":
        this.problem = `${msg.code ?? "error"}: ${msg.message ?? ""}`;
        if (!this.welcome && /auth|token|origin|forbidden|unauthorized/i.test(String(msg.code))) this.rejected = true;
        this.hooks.note("motor", `error del motor: ${this.problem}`);
        this.dispatchEvent(new CustomEvent("state", { detail: this.state }));
        break;
      case "status":
        this.status = msg;
        this.dispatchEvent(new CustomEvent("state", { detail: this.state }));
        break;
      case "blank":
        this.onBlank(msg);
        break;
      case "unblank":
        this.onUnblank(msg);
        break;
      case "objects":
        this.onObjects(msg);
        break;
      case "event":
        this.onEvent(msg);
        break;
      case "need_frame":
        this.onNeedFrame(msg);
        break;
      case "look_frame": {
        const p = this.pending.get(`look_frame:${msg.id}`);
        if (p) (clearTimeout(p.timer), this.pending.delete(`look_frame:${msg.id}`), p.resolve(msg));
        break;
      }
    }
    this.dispatchEvent(new CustomEvent("message", { detail: msg }));
  }

  // ------------------------------------------------------------------ calibración
  /** Lo que el motor necesita para pasar sus píxeles a los mm del mundo (los mismos que usa la consola). */
  calibMessage() {
    const c = this.node.calib;
    if (!c?.matrix || !c.image_px) return null;
    const { width_mm, height_mm } = this.node.world.surface;
    const L = c.lens;
    return {
      type: "calib",
      camera_px: Array.from(c.image_px, Number),
      matrix_px_to_mm: Array.from(c.matrix, Number),
      lens: L ? { f: L.f, cx: L.cx, cy: L.cy, k1: L.k1, k2: L.k2 } : null,
      surface_mm: [width_mm, height_mm],
    };
  }

  /** Al conectar y cada vez que cambia la calibración (o la superficie): solo si cambió algo. */
  syncCalib() {
    if (!this.online) return;
    const msg = this.calibMessage();
    const key = msg && JSON.stringify(msg);
    if (!msg || key === this.sentCalib) return;
    if (this.send(msg)) this.sentCalib = key;
  }

  // ------------------------------------------------------------------ foto limpia (blank / unblank)
  onBlank({ id, max_ms }) {
    const ms = Math.min(BLANK_MAX_MS, Math.max(50, Number.isFinite(max_ms) ? max_ms : BLANK_MS));
    if (this.blank) clearTimeout(this.blank.timer);
    // Tope de seguridad: si el motor no manda unblank a tiempo, la escena vuelve sola.
    this.blank = { id, t0: performance.now(), timer: setTimeout(() => this.endBlank(id, "tope"), ms + 250) };
    this.lastBlank = { id, t0: this.blank.t0, t1: null };
    Promise.resolve(this.hooks.blank(id, true, ms)).then((t_ns) => {
      if (this.blank?.id !== id) return; // ya terminó o llegó otro
      if (t_ns == null) {
        this.problem = "El motor pidió apagar el proyector y no se pudo confirmar: ¿está abierta la ventana del proyector?";
        this.hooks.note("motor", "blank sin confirmar: no hay ventana del proyector o no alcanzó a pintar");
        return this.dispatchEvent(new CustomEvent("state", { detail: this.state }));
      }
      this.send({ type: "blanked", id, t_ns: Math.round(t_ns) });
    });
  }

  onUnblank({ id }) {
    if (this.blank?.id === id) return this.endBlank(id, "motor");
    if (!this.blank) this.send({ type: "unblanked", id }); // ya estaba con la escena: se confirma igual
  }

  async endBlank(id, why) {
    if (this.blank?.id !== id) return;
    clearTimeout(this.blank.timer);
    this.blank = null;
    if (this.lastBlank?.id === id) this.lastBlank.t1 = performance.now();
    if (why === "tope") this.hooks.note("motor", "foto limpia: el proyector volvió solo (pasó max_ms sin unblank)");
    await this.hooks.blank(id, false, 0);
    this.send({ type: "unblanked", id });
  }

  /** La ventana del proyector volvió a la escena por su cuenta (su propio tope de max_ms). */
  projectorUnblanked(id) {
    if (this.blank?.id !== id) return;
    clearTimeout(this.blank.timer);
    this.blank = null;
    if (this.lastBlank?.id === id) this.lastBlank.t1 = performance.now();
    this.send({ type: "unblanked", id });
  }

  // ------------------------------------------------------------------ manos
  /** Presencia de manos (MediaPipe de la consola). Se avisa solo al cambiar; la ausencia se confirma tras 120 ms. */
  hands(present) {
    present = !!present;
    if (present) {
      clearTimeout(this.handsTimer);
      this.handsTimer = null;
      this.handsGone = null;
      return this.setHands(true, now_ns());
    }
    if (this.handsState === false || this.handsTimer) return;
    this.handsGone = now_ns(); // la calma del motor se cuenta desde el primer cuadro sin manos
    this.handsTimer = setTimeout(() => {
      this.handsTimer = null;
      this.setHands(false, this.handsGone);
    }, HANDS_GONE_MS);
  }

  setHands(present, t_ns) {
    if (this.handsState === present) return;
    this.handsState = present;
    this.send({ type: "hands", present, t_ns });
  }

  // ------------------------------------------------------------------ objetos
  onObjects(msg) {
    if (Number.isFinite(msg.rev)) this.rev = msg.rev;
    const replace = this.fullNext;
    this.fullNext = false;
    const r = this.node.mergeObjects("motor", { upsert: msg.upsert ?? [], remove: msg.remove ?? [], replace });
    const parts = [
      r.added.length ? `+${r.added.join(", +")}` : "",
      r.moved.length ? `movidos ${r.moved.join(", ")}` : "",
      r.removed.length ? `−${r.removed.join(", −")}` : "",
      r.skipped.length ? `${r.skipped.length} descartados (${r.skipped[0]})` : "",
    ].filter(Boolean);
    if (parts.length) this.hooks.note("motor", `objetos rev ${msg.rev ?? "?"}: ${parts.join(" · ")}`);
    this.dispatchEvent(new CustomEvent("state", { detail: this.state }));
  }

  perceive() {
    return this.send({ type: "perceive" });
  }

  // ------------------------------------------------------------------ escenas (v0.1)
  /** Módulo de escena del motor para la pestaña activa: se llama igual que el preset (lab, solar, desk). Si el motor
   *  dice qué escenas tiene, se compara sin mayúsculas y una pestaña sin módulo es null; si no lo dice, va el nombre. */
  sceneName() {
    const active = this.node.active?.trim();
    if (!active) return null;
    const list = this.welcome?.scenes;
    if (!Array.isArray(list)) return active;
    return list.find((n) => String(n).toLowerCase() === active.toLowerCase()) ?? null;
  }

  /** Al conectar y cada vez que cambia la escena activa: solo si cambió, y solo a un motor v0.1 (uno v0 no la conoce). */
  syncScene() {
    if (!this.online || !this.v01) return;
    const name = this.sceneName();
    if (name === this.sentScene) return;
    if (!this.send({ type: "scene", name })) return;
    this.sentScene = name;
    this.dispatchEvent(new CustomEvent("state", { detail: this.state })); // el panel muestra la escena activa
  }

  /** Evento de un módulo de escena → al agente como flow_emitted {event, data} (lo ve con events_wait). */
  onEvent(msg) {
    const event = typeof msg.event === "string" ? msg.event.trim().slice(0, 64) : "";
    if (!event || RESERVED_EVENTS.has(event)) return; // un motor no puede fingir eventos de la consola (voz, widgets)
    const data = msg.data && typeof msg.data === "object" && !Array.isArray(msg.data) ? msg.data : { value: msg.data ?? null };
    this.node.emit({ type: "flow_emitted", event, data });
    const text = JSON.stringify(data);
    this.hooks.note(`motor · ${msg.scene ?? "escena"}`, `${event} ${text.length > 120 ? `${text.slice(0, 120)}…` : text}`);
  }

  /** Olvida los objetos: el motor y también los suyos en el mundo (los de otras fuentes no se tocan). */
  reset() {
    this.send({ type: "reset" });
    this.node.mergeObjects("motor", { replace: true });
  }

  // ------------------------------------------------------------------ fotos
  /** `look` con la cámara del motor. null si no hay motor con cámara (look sigue con la cámara de la consola). */
  async look(args = {}) {
    const cam = this.welcome?.camera;
    if (!this.online || !cam) return null;
    const c = this.node.calib;
    const motorCamId = `motor:${cam.id}`;
    if (args.camera_id && args.camera_id !== c?.camera_id && args.camera_id !== motorCamId) return null;
    const id = crypto.randomUUID();
    const t0 = performance.now();
    const clean = !!args.clean;
    const reply = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(`look_frame:${id}`);
        reject(new Error(`no respondió en ${clean ? 12 : 6} s`));
      }, clean ? 12000 : 6000);
      this.pending.set(`look_frame:${id}`, { resolve, reject, timer });
    });
    if (!this.send({ type: "look", id, clean })) {
      clearTimeout(this.pending.get(`look_frame:${id}`)?.timer);
      this.pending.delete(`look_frame:${id}`);
      return null;
    }
    const msg = await reply;
    if (typeof msg.jpeg_b64 !== "string" || !msg.jpeg_b64) throw new Error("look_frame sin imagen");
    const canvas = await this.hooks.decode(msg.jpeg_b64);
    // El motor recibió nuestra calibración (calib) y trata su cámara como la calibrada: si la foto tiene la misma
    // proporción, vale la grilla y anchor.point_px. Si no, es otra cámara y va sin calibrar.
    const same = c && Math.abs(canvas.width / canvas.height - c.image_px[0] / c.image_px[1]) < 0.02;
    const b = this.lastBlank;
    const blanked_ms = clean && b && b.t0 >= t0 ? Math.round((b.t1 ?? performance.now()) - b.t0) : null;
    return {
      canvas,
      camera: { id: same ? c.camera_id : motorCamId, meta: { label: `Motor local · ${cam.name ?? cam.id}` } },
      via: "motor",
      blanked_ms,
      t_ns: msg.t_ns ?? null,
    };
  }

  /** Respaldo: el motor no tiene cámara y pide un cuadro de la cenital de la consola. */
  async onNeedFrame({ id }) {
    if (!this.hooks.frame) return;
    try {
      const t_ns = now_ns(); // el cuadro se toma después de este instante
      const { canvas } = await this.hooks.frame();
      this.send({ type: "frame", id, jpeg_b64: this.hooks.encode(canvas), w: canvas.width, h: canvas.height, t_ns });
    } catch (err) {
      this.problem = `need_frame: ${err.message}`;
      this.hooks.note("motor", `no pude mandarle un cuadro al motor: ${err.message}`);
    }
  }

  // ------------------------------------------------------------------ para la consola
  /** «Motor local: conectado · último ciclo X ms» o «Motor local: sin motor». */
  summary() {
    if (this.state === "online") {
      const ms = this.status?.last_cycle_ms;
      return `Motor local: conectado · ${Number.isFinite(ms) ? `último ciclo ${Math.round(ms)} ms` : "sin ciclos todavía"}`;
    }
    if (this.state === "connecting") return "Motor local: conectando…";
    if (this.state === "rejected") return "Motor local: sin motor (conexión rechazada)";
    return "Motor local: sin motor";
  }

  detail() {
    const w = this.welcome;
    const s = this.status;
    const mine = this.node.world.objects.filter((o) => o.source === "motor").length;
    const lines = [];
    if (w) {
      const cam = w.camera ? `cámara ${w.camera.name ?? w.camera.id}${w.camera.px ? ` (${w.camera.px.join("×")})` : ""}` : "sin cámara propia (pide cuadros a la consola)";
      const models = Object.entries(w.models ?? {}).map(([k, v]) => `${k} ${v === true ? "sí" : v === false ? "no" : v}`).join(", ");
      lines.push(`${w.motor ?? "motor"} · ${cam}${models ? ` · modelos: ${models}` : ""}.`);
      if (Array.isArray(w.cameras) && w.cameras.length)
        lines.push(`Cámaras del motor: ${w.cameras.map((c) => `${c.role ?? "otra"} ${c.name ?? c.id}`).join(", ")}.`);
      if (Array.isArray(w.scenes))
        lines.push(`Escenas del motor: ${w.scenes.join(", ") || "ninguna"}; activa: ${this.sentScene ?? "ninguna"} (la pestaña se llama igual que la escena).`);
      lines.push(`Estado: ${s?.state ?? "?"}${s?.stages_ms && Object.keys(s.stages_ms).length ? ` · etapas ${Object.entries(s.stages_ms).map(([k, v]) => `${k} ${Math.round(v)} ms`).join(", ")}` : ""}. Objetos del motor en el mundo: ${mine}${this.rev !== null ? ` (rev ${this.rev})` : ""}.`);
      if (!this.sentCalib) lines.push("Sin calibración que mandarle: calibra (o usa la manual) para que sus objetos caigan en mm de la mesa.");
    } else if (this.state === "offline" || this.state === "connecting") {
      lines.push(`Buscando el motor en ${this.url}${this.fails ? ` (${this.fails} intentos)` : ""}. ¿Corre \`pcx up\` en este computador?`);
      if (this.fails >= 2 && this.pageProtocol === "https:")
        lines.push("Si Chrome preguntó por acceso a apps y servicios de este dispositivo (red local), hay que permitirlo en el candado de la barra de direcciones.");
    } else if (this.state === "off") lines.push(mine ? `Desconectado. Quedan ${mine} objetos del motor en el mundo («Olvidar objetos» los quita).` : "Desconectado.");
    if (this.problem) lines.push(this.problem);
    return lines.join(" ");
  }
}
