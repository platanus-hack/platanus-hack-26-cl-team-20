// Tests de la consola con el motor local (protocolo motor v0). Corren con `node --test` (los lanza test_console_js.py)
// y no usan DOM, cámaras ni ventanas: el WebSocket, el proyector y las imágenes son falsos.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { Motor, MOTOR_DEFAULT, mixedContent, motorSettings, motorUrl } from "../../src/pcx_gateway/console/motor.js";
import { BrowserNode } from "../../src/pcx_gateway/console/node.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class FakeWS {
  static all = [];
  constructor(url) {
    Object.assign(this, { url, sent: [], readyState: 0 });
    FakeWS.all.push(this);
    queueMicrotask(() => {
      this.readyState = 1;
      this.onopen?.();
    });
  }
  send(text) {
    this.sent.push(JSON.parse(text));
  }
  close(code = 1000) {
    if (this.readyState === 3) return;
    this.readyState = 3;
    queueMicrotask(() => this.onclose?.({ code }));
  }
  // Lo que hace el motor.
  push(msg) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
  drop(code = 1006) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
  of(type) {
    return this.sent.filter((m) => m.type === type);
  }
}

const CALIB = { camera_id: "cam-1", matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1], image_px: [1280, 720], residual_mm: 1, version: 1 };
const WELCOME = { type: "welcome", v: 0, motor: "falso", camera: { id: "0", name: "cenital", px: [1920, 1080] }, models: { sam: true }, needs_frames: false };

const motors = [];
afterEach(() => {
  for (const m of motors.splice(0)) m.disconnect(); // que ningún motor siga reconectando en el test siguiente
});

function setup({ hooks = {}, calib = true, pageProtocol = "http:", url = "ws://127.0.0.1:9230" } = {}) {
  for (const m of motors.splice(0)) m.disconnect();
  FakeWS.all = [];
  const node = new BrowserNode({ node_id: "n1", node_token: "t" }, {});
  if (calib) node.setCalib({ ...CALIB });
  const motor = new Motor({ node, hooks, url, token: "tok", sessionId: "ses", WS: FakeWS, retryMs: 20, pageProtocol });
  motors.push(motor);
  const events = (type) => node.log.map((e) => e.payload).filter((p) => p.type === type);
  return { node, motor, events };
}

async function online(motor, welcome = WELCOME) {
  motor.connect();
  await sleep(0);
  const ws = FakeWS.all.at(-1);
  ws.push(welcome);
  return ws;
}

const square = (x, y, s = 60) => [[x, y], [x + s, y], [x + s, y + s], [x, y + s]];
const obj = (id, x, y) => ({ id, label: id, polygon_mm: square(x, y), center_mm: [x + 30, y + 30], confidence: 0.9 });

test("URL del motor: la del enlace de pcx up, sin esquema, http(s) y contenido mixto", () => {
  assert.equal(motorUrl(""), MOTOR_DEFAULT);
  assert.equal(motorUrl("ws://127.0.0.1:9230"), "ws://127.0.0.1:9230/v1/ws");
  assert.equal(motorUrl("127.0.0.1:9230"), "ws://127.0.0.1:9230/v1/ws");
  assert.equal(motorUrl("http://localhost:9230/"), "ws://localhost:9230/v1/ws");
  assert.equal(motorUrl("https://motor.example/x/ws"), "wss://motor.example/x/ws");
  assert.throws(() => motorUrl("ftp://motor.example"));
  assert.equal(mixedContent("ws://127.0.0.1:9230/v1/ws", "https:"), false);
  assert.equal(mixedContent("ws://localhost:9230/v1/ws", "https:"), false);
  assert.equal(mixedContent("ws://192.168.1.5:9230/v1/ws", "https:"), true);
  assert.equal(mixedContent("wss://motor.example/v1/ws", "https:"), false);
  assert.equal(mixedContent("ws://192.168.1.5:9230/v1/ws", "http:"), false);
});

test("motorSettings: ?motor y ?motor_token se guardan y salen de la barra; sin emparejar no se conecta solo", () => {
  const mem = () => {
    const m = new Map();
    return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
  };
  const local = mem();
  const session = mem();
  const hist = { state: null, replaceState(_s, _t, u) { this.url = u; } };
  const loc = { search: "?sim=1&motor=ws://127.0.0.1:9230&motor_token=abc", pathname: "/", hash: "" };
  const a = motorSettings({ loc, hist, local, session });
  assert.deepEqual([a.url, a.token, a.auto, hist.url], ["ws://127.0.0.1:9230/v1/ws", "abc", true, "/?sim=1"]);
  const b = motorSettings({ loc: { search: "", pathname: "/", hash: "" }, hist, local, session });
  assert.deepEqual([b.token, b.auto], ["abc", true]); // recarga: el token sigue en la pestaña
  b.save({ on: false });
  assert.equal(motorSettings({ loc: { search: "", pathname: "/", hash: "" }, hist, local, session }).auto, false);
  const fresh = motorSettings({ loc: { search: "", pathname: "/", hash: "" }, hist, local: mem(), session: mem() });
  assert.deepEqual([fresh.url, fresh.token, fresh.auto], [MOTOR_DEFAULT, "", false]);
});

test("hello primero; con welcome, calib (y de nuevo solo si cambia la calibración o la superficie)", async () => {
  const { motor, node } = setup();
  const ws = await online(motor);
  assert.deepEqual(ws.sent[0], { type: "hello", v: 0, token: "tok", session_id: "ses", app: "pcx-console" });
  assert.equal(motor.state, "online");
  assert.deepEqual(ws.sent[1], { type: "calib", camera_px: [1280, 720], matrix_px_to_mm: CALIB.matrix, lens: null, surface_mm: [1000, 562] });
  node.setHands([]); // otro cambio del mundo: no repite calib
  assert.equal(ws.of("calib").length, 1);
  node.setCalib({ ...CALIB, lens: { f: 900, cx: 640, cy: 360, k1: -0.1, k2: 0.01 }, version: 2 });
  assert.deepEqual(ws.of("calib")[1].lens, { f: 900, cx: 640, cy: 360, k1: -0.1, k2: 0.01 });
  node.setSurface(1920, 1200);
  assert.deepEqual(ws.of("calib")[2].surface_mm, [1000, 625]);
  assert.match(motor.summary(), /^Motor local: conectado · sin ciclos todavía$/);
  ws.push({ type: "status", state: "idle", last_cycle_ms: 182.4, stages_ms: { blank: 40 } });
  assert.equal(motor.summary(), "Motor local: conectado · último ciclo 182 ms");
});

test("blank: blanked solo cuando el proyector confirmó; unblank vuelve y avisa", async () => {
  const calls = [];
  let confirm;
  const { motor } = setup({
    hooks: {
      blank: (id, on, ms) => {
        calls.push([id, on, ms]);
        return on ? new Promise((r) => (confirm = r)) : Promise.resolve(1);
      },
    },
  });
  const ws = await online(motor);
  ws.push({ type: "blank", id: "b1", max_ms: 800 });
  assert.deepEqual(calls[0], ["b1", true, 800]);
  await sleep(5);
  assert.equal(ws.of("blanked").length, 0); // el proyector todavía no pinta
  confirm(1234);
  await sleep(0);
  assert.deepEqual(ws.of("blanked"), [{ type: "blanked", id: "b1", t_ns: 1234 }]);
  ws.push({ type: "unblank", id: "b1" });
  await sleep(0);
  assert.deepEqual(calls[1], ["b1", false, 0]);
  assert.deepEqual(ws.of("unblanked"), [{ type: "unblanked", id: "b1" }]);
});

test("blank sin ventana del proyector: no se confirma; tope de max_ms: la escena vuelve sola", async () => {
  const calls = [];
  const { motor } = setup({ hooks: { blank: async (id, on) => (calls.push([id, on]), null) } });
  const ws = await online(motor);
  ws.push({ type: "blank", id: "b2", max_ms: 60 });
  await sleep(10);
  assert.equal(ws.of("blanked").length, 0);
  assert.match(motor.detail(), /ventana del proyector/);
  await sleep(400); // 60 ms + margen de 250 ms
  assert.deepEqual(calls, [["b2", true], ["b2", false]]);
  assert.deepEqual(ws.of("unblanked"), [{ type: "unblanked", id: "b2" }]);
});

test("el proyector volvió solo (su tope): se avisa unblanked una vez; si se corta, nada queda a oscuras", async () => {
  const calls = [];
  const { motor } = setup({ hooks: { blank: async (id, on) => (calls.push([id, on]), 1) } });
  let ws = await online(motor);
  ws.push({ type: "blank", id: "b3", max_ms: 5000 });
  await sleep(0);
  motor.projectorUnblanked("b3");
  motor.projectorUnblanked("b3");
  assert.equal(ws.of("unblanked").length, 1);
  ws.push({ type: "blank", id: "b4", max_ms: 5000 });
  await sleep(0);
  ws.drop();
  assert.deepEqual(calls.at(-1), ["b4", false]);
  assert.equal(motor.blank, null);
  await sleep(40);
  ws = FakeWS.all.at(-1);
  assert.notEqual(ws, null);
});

test("manos: presencia al tiro, ausencia confirmada a los 120 ms, sin repetir ni parpadeos", async () => {
  const { motor } = setup();
  const ws = await online(motor);
  motor.hands(true);
  motor.hands(true);
  assert.equal(ws.of("hands").length, 1);
  assert.equal(ws.of("hands")[0].present, true);
  motor.hands(false);
  await sleep(40);
  motor.hands(true); // un cuadro sin mano: no cuenta
  await sleep(150);
  assert.equal(ws.of("hands").length, 1);
  const before = Date.now() * 1e6;
  motor.hands(false);
  motor.hands(false);
  await sleep(160);
  const gone = ws.of("hands");
  assert.equal(gone.length, 2);
  assert.equal(gone[1].present, false);
  assert.ok(gone[1].t_ns >= before && gone[1].t_ns < before + 100e6); // desde el primer cuadro sin manos
});

test("objetos: el motor y los clientes MCP no se borran entre sí; eventos y umbral de 10 mm", async () => {
  const { motor, node, events } = setup();
  const ws = await online(motor);
  node.setObjects([obj("cup", 100, 100)]);
  ws.push({ type: "objects", rev: 1, upsert: [obj("taza", 300, 200), obj("libro", 600, 200)], remove: [] });
  const byId = () => Object.fromEntries(node.world.objects.map((o) => [o.id, o]));
  assert.deepEqual(Object.keys(byId()).sort(), ["cup", "libro", "taza"]);
  assert.deepEqual([byId().cup.source, byId().taza.source], ["mcp", "motor"]);
  assert.deepEqual(events("object_appeared").map((e) => e.object.id), ["cup", "taza", "libro"]);
  // El cliente MCP reemplaza los suyos: los del motor quedan.
  const r = node.setObjects([]);
  assert.deepEqual(r.objects, []);
  assert.deepEqual(Object.keys(byId()).sort(), ["libro", "taza"]);
  assert.deepEqual(events("object_removed").map((e) => e.object_id), ["cup"]);
  node.setObjects([obj("cup", 100, 100)]);
  // El motor no borra ni pisa lo de otra fuente; un objeto inválido se salta sin perder el resto.
  ws.push({ type: "objects", rev: 2, upsert: [obj("cup", 0, 0), { id: "mal", polygon_mm: [[0, 0], [1, 1]] }, obj("taza", 305, 200)], remove: ["cup"] });
  assert.equal(byId().cup.source, "mcp");
  assert.deepEqual(byId().cup.center_mm, [130, 130]);
  assert.equal(byId().mal, undefined);
  assert.equal(events("object_moved").length, 0); // 5 mm: bajo el umbral
  ws.push({ type: "objects", rev: 3, upsert: [obj("taza", 340, 200)], remove: [] });
  assert.deepEqual(events("object_moved").map((e) => [e.object_id, e.center_mm]), [["taza", [370, 230]]]);
  ws.push({ type: "objects", rev: 4, upsert: [], remove: ["libro"] });
  assert.equal(byId().libro, undefined);
  assert.deepEqual(events("object_removed").map((e) => e.object_id), ["cup", "libro"]);
  assert.throws(() => node.setObjects([obj("taza", 0, 0)]), /otra fuente \(motor\)/);
  // Olvidar: el motor y sus objetos; los del cliente MCP siguen.
  motor.reset();
  assert.equal(ws.of("reset").length, 1);
  assert.deepEqual(Object.keys(byId()), ["cup"]);
});

test("look: con motor y cámara usa look_frame; sin motor o si falla, la cámara de la consola", async () => {
  let size = { width: 1280, height: 720 };
  const decoded = [];
  const { motor, node } = setup({ hooks: { decode: async (b64) => (decoded.push(b64), { ...size, toDataURL: () => "data:image/jpeg;base64,TU9U" }) } });
  assert.equal(await motor.look({}), null); // sin conexión
  const ws = await online(motor);
  const pending = motor.look({ clean: true });
  await sleep(0);
  const ask = ws.of("look")[0];
  assert.deepEqual(Object.keys(ask).sort(), ["clean", "id", "type"]);
  assert.equal(ask.clean, true);
  ws.push({ type: "look_frame", id: ask.id, jpeg_b64: "QUJD", t_ns: 5, w: 1920, h: 1080 });
  const shot = await pending;
  assert.deepEqual([decoded[0], shot.via, shot.camera.id], ["QUJD", "motor", "cam-1"]); // misma proporción: la calibrada
  size = { width: 640, height: 480 };
  const other = motor.look({});
  await sleep(0);
  ws.push({ type: "look_frame", id: ws.of("look")[1].id, jpeg_b64: "QUJD", t_ns: 6, w: 640, h: 480 });
  assert.equal((await other).camera.id, "motor:0"); // otra proporción: otra cámara, sin calibrar
  assert.equal(await motor.look({ camera_id: "cam-9" }), null); // pidió otra cámara de la consola
  // Por el nodo: la foto del motor, y si el motor falla, la de la consola con una nota.
  size = { width: 1280, height: 720 };
  node.hooks.motorLook = (args) => motor.look(args);
  node.hooks.projectorOpen = () => true;
  node.hooks.frame = async () => ({ canvas: { width: 1280, height: 720, toDataURL: () => "data:image/jpeg;base64,TE9D" }, camera: { id: "cam-1", meta: { label: "webcam" } } });
  const look = node.tools.look.call(node, { annotate: false });
  await sleep(0);
  ws.push({ type: "look_frame", id: ws.of("look")[2].id, jpeg_b64: "QUJD", t_ns: 7, w: 1280, h: 720 });
  const out = await look;
  assert.deepEqual([out.data.via, out.data.camera_id, out.data.calibrated, out.images[0].data], ["motor", "cam-1", true, "TU9U"]);
  const fallback = node.tools.look.call(node, { annotate: false });
  await sleep(0);
  ws.drop();
  const local = await fallback;
  assert.equal(local.images[0].data, "TE9D");
  assert.match(local.data.motor_note, /se cortó la conexión/);
  assert.equal(local.data.via, undefined);
});

test("need_frame: el motor sin cámara recibe un cuadro de la cenital", async () => {
  const canvas = { width: 2, height: 1 };
  const { motor } = setup({ hooks: { frame: async () => ({ canvas }), encode: (c) => (c === canvas ? "QUJD" : "") } });
  const ws = await online(motor, { ...WELCOME, camera: null, needs_frames: true });
  const before = Date.now() * 1e6;
  ws.push({ type: "need_frame", id: "f1" });
  await sleep(0);
  const [frame] = ws.of("frame");
  assert.deepEqual({ ...frame, t_ns: 0 }, { type: "frame", id: "f1", jpeg_b64: "QUJD", w: 2, h: 1, t_ns: 0 });
  assert.ok(frame.t_ns >= before);
  assert.equal(await motor.look({}), null); // sin cámara, look sigue en la consola
});

test("reconexión con backoff; token rechazado no reintenta; ws:// remoto desde https no se intenta", async () => {
  const { motor } = setup();
  let ws = await online(motor);
  ws.drop();
  assert.equal(motor.state, "offline");
  assert.equal(motor.summary(), "Motor local: sin motor");
  await sleep(40);
  ws = FakeWS.all.at(-1);
  assert.equal(FakeWS.all.length, 2);
  await sleep(0);
  assert.equal(ws.sent[0].type, "hello");
  ws.push(WELCOME);
  assert.equal(ws.of("calib").length, 1); // la calibración se manda de nuevo en cada conexión

  const second = setup();
  second.motor.connect();
  await sleep(0);
  const bad = FakeWS.all.at(-1);
  bad.push({ type: "error", code: "auth", message: "Token inválido." });
  bad.drop(4401);
  await sleep(60);
  assert.equal(second.motor.state, "rejected");
  assert.equal(FakeWS.all.length, 1);

  const third = setup({ pageProtocol: "https:", url: "ws://192.168.1.5:9230" });
  third.motor.connect();
  assert.equal(third.motor.state, "rejected");
  assert.equal(FakeWS.all.length, 0);
  assert.match(third.motor.detail(), /wss:\/\//);
});

const V01 = {
  ...WELCOME,
  scenes: ["lab", "solar", "desk"],
  cameras: [{ id: "0", role: "cenital", name: "cenital" }, { id: "1", role: "lateral", name: "lateral USB" }],
};

test("v0.1 scene: al conectar y al cambiar de pestaña, con el nombre del módulo (o null)", async () => {
  const { motor, node } = setup();
  const ws = await online(motor, V01);
  assert.deepEqual(ws.of("scene"), [{ type: "scene", name: null }]); // «Principal» no es un módulo del motor
  node.scenesTool({ action: "create", name: "Lab" });
  assert.deepEqual(ws.of("scene").at(-1), { type: "scene", name: "lab" });
  node.setHands([]);
  assert.equal(ws.of("scene").length, 2); // sin cambio de escena no se repite
  node.scenesTool({ action: "create", name: "solar" });
  node.scenesTool({ action: "activate", name: "Principal" });
  assert.deepEqual(ws.of("scene").map((m) => m.name), [null, "lab", "solar", null]);
  assert.match(motor.detail(), /Cámaras del motor: cenital cenital, lateral lateral USB/);
  assert.match(motor.detail(), /Escenas del motor: lab, solar, desk/);
  // Un motor v0 (welcome sin scenes ni cameras) no recibe scene.
  const v0 = setup();
  const ws0 = await online(v0.motor);
  v0.node.scenesTool({ action: "create", name: "lab" });
  assert.equal(ws0.of("scene").length, 0);
});

test("v0.1 event: va al gateway como flow_emitted {event, data} y al registro de la consola", async () => {
  const notes = [];
  const { motor, events } = setup({ hooks: { note: (kind, text) => notes.push([kind, text]) } });
  const ws = await online(motor, V01);
  const data = { vaso: "taza", ml: 120, objetivo_ml: 200, estado: "llenando" };
  ws.push({ type: "event", scene: "lab", event: "nivel", data });
  ws.push({ type: "event", scene: "lab", event: "" }); // sin nombre: se ignora
  assert.deepEqual(events("flow_emitted").map((e) => [e.event, e.data]), [["nivel", data]]);
  assert.deepEqual(notes.at(-1), ["motor · lab", `nivel ${JSON.stringify(data)}`]);
});

test("v0.1: el primer objects después de welcome trae todo y reemplaza los del motor que quedaron", async () => {
  const { motor, node } = setup();
  let ws = await online(motor, V01);
  ws.push({ type: "objects", rev: 1, upsert: [obj("taza", 300, 200), obj("viejo", 0, 0)], remove: [] });
  node.setObjects([obj("cup", 100, 100)]);
  ws.drop();
  await sleep(40);
  ws = FakeWS.all.at(-1);
  ws.push(V01);
  ws.push({ type: "objects", rev: 5, upsert: [obj("taza", 300, 200)], remove: [] }); // todos los vigentes
  assert.deepEqual(node.world.objects.map((o) => o.id).sort(), ["cup", "taza"]);
  ws.push({ type: "objects", rev: 6, upsert: [obj("libro", 600, 200)], remove: [] }); // después: incremental
  assert.deepEqual(node.world.objects.map((o) => o.id).sort(), ["cup", "libro", "taza"]);
});

// Con el motor falso de Python de verdad (lo levanta test_console_js.py): WebSocket real, ciclo completo.
const REAL = process.env.PCX_FAKE_MOTOR_URL;
test("de punta a punta con tools/fake_motor.py", { skip: !REAL || typeof WebSocket !== "function" }, async () => {
  const blanks = [];
  const node = new BrowserNode({ node_id: "n1", node_token: "t" }, {});
  node.setCalib({ ...CALIB });
  const motor = new Motor({
    node,
    url: REAL,
    token: process.env.PCX_FAKE_MOTOR_TOKEN,
    sessionId: "ses",
    pageProtocol: "http:",
    hooks: { blank: async (id, on) => (blanks.push([id, on]), Date.now() * 1e6), decode: async () => ({ width: 160, height: 90 }) },
  });
  const until = async (check, ms = 4000) => {
    const t0 = Date.now();
    while (!check()) {
      if (Date.now() - t0 > ms) throw new Error("no pasó a tiempo");
      await sleep(10);
    }
  };
  motor.connect();
  await until(() => motor.online);
  assert.equal(motor.welcome.camera.id, "fake-0");
  motor.hands(true);
  motor.hands(false); // se van las manos: calma, blank, objetos
  await until(() => node.world.objects.length === 2);
  assert.deepEqual(node.world.objects.map((o) => [o.id, o.source]).sort(), [["libro", "motor"], ["taza", "motor"]]);
  await until(() => blanks.length >= 2);
  assert.deepEqual(blanks.slice(0, 2).map((b) => b[1]), [true, false]);
  await until(() => Number.isFinite(motor.status?.last_cycle_ms) && motor.status.state === "idle");
  assert.match(motor.summary(), /^Motor local: conectado · último ciclo \d+ ms$/);
  const shot = await motor.look({});
  assert.equal(shot.via, "motor");
  assert.deepEqual(motor.welcome.cameras.map((c) => c.role), ["cenital", "lateral"]);
  node.scenesTool({ action: "create", name: "Lab" }); // el módulo lab del motor falso manda «nivel»
  await until(() => node.log.some((e) => e.payload.type === "flow_emitted" && e.payload.event === "nivel"));
  motor.disconnect();
  assert.equal(motor.state, "off");
});
