/* Generado desde contracts/schema/pcx.draft07.json. No editar: corre contracts/ts/generate.sh */

/**
 * Punto en mm del marco `table_mm`. En el cable: `[x, y]`.
 *
 * @minItems 2
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "PointMM".
 */
export type PointMM = [number, number];
/**
 * Rectángulo alineado a ejes en mm. En el cable: `[x, y, w, h]` (x, y = esquina superior izquierda).
 *
 * @minItems 4
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "RectMM".
 */
export type RectMM = [number, number, number, number];
/**
 * Punto en px de una imagen (cámara o proyector). En el cable: `[x, y]`.
 *
 * @minItems 2
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "PointPX".
 */
export type PointPX = [number, number];

/**
 * Contratos pcx v1
 */
export interface Pcx {
  widget?: Label | Halo | Arrow | Panel | Button | Image | Effect;
  command?: Project | Update | Remove | Clear | Ask | Emit | RunFlow | StopFlow | Calibrate | SetTransform | HomeCall;
  event?:
    | ObjectAppeared
    | ObjectMoved
    | ObjectRemoved
    | TouchDown
    | TouchMove
    | TouchUp
    | WidgetPressed
    | Dwell
    | AskAnswered
    | CalibrationDrift
    | FlowStateChanged
    | FlowEmitted
    | FrameMeta
    | DeviceChanged;
  envelope?: Envelope;
  to_renderer?: SceneSnapshot | SceneDiff;
  from_renderer?: Presented | Stats;
  cloud?: Hello | Welcome | StateSnapshot | EventBatch | ToolCall | ToolResult | Heartbeat;
  world?: WorldState;
  flow?: Flow;
}
/**
 * Texto centrado en `anchor` (admite `\n` y Unicode).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Label".
 */
export interface Label {
  type: "label";
  id: string;
  anchor?: Anchor | null;
  z?: number;
  ttl_s?: number | null;
  on_press?: OnPress | null;
  text?: string;
  size_mm?: number;
  color?: string;
  bg?: string | null;
}
/**
 * Dónde se ancla un widget: a un objeto (lo sigue si se mueve) o a un punto fijo, más un desplazamiento.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Anchor".
 */
export interface Anchor {
  object_id?: string | null;
  point_mm?: PointMM | null;
  offset_mm?: PointMM | null;
}
/**
 * Hace tocable al widget: el core emite `widget_pressed{widget_id, value}` al confirmarse el toque.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "OnPress".
 */
export interface OnPress {
  value?: string | null;
  triggers?: ("api" | "dwell" | "pinch" | "shadow")[];
  dwell_ms?: number;
}
/**
 * Borde luminoso animado. Sin `polygon_mm`, el core usa el polígono del objeto anclado.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Halo".
 */
export interface Halo {
  type: "halo";
  id: string;
  anchor?: Anchor | null;
  z?: number;
  ttl_s?: number | null;
  on_press?: OnPress | null;
  polygon_mm?: [PointMM, PointMM, PointMM, ...PointMM[]] | null;
  color?: string;
  width_mm?: number;
  pulse_hz?: number;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Arrow".
 */
export interface Arrow {
  type: "arrow";
  id: string;
  anchor?: Anchor | null;
  z?: number;
  ttl_s?: number | null;
  on_press?: OnPress | null;
  from_mm?: PointMM | null;
  to_mm?: PointMM | null;
  color?: string;
  width_mm?: number;
}
/**
 * Título + párrafo con ajuste de línea.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Panel".
 */
export interface Panel {
  type: "panel";
  id: string;
  anchor?: Anchor | null;
  z?: number;
  ttl_s?: number | null;
  on_press?: OnPress | null;
  rect_mm?: RectMM | null;
  title?: string;
  body?: string;
  color?: string;
}
/**
 * Botón tocable; `progress` (0..1) dibuja el anillo de permanencia.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Button".
 */
export interface Button {
  type: "button";
  id: string;
  anchor?: Anchor | null;
  z?: number;
  ttl_s?: number | null;
  on_press?: OnPress | null;
  rect_mm?: RectMM | null;
  label?: string;
  progress?: number;
  color?: string;
}
/**
 * Imagen en un rectángulo. `src`: `asset:<id>`, `data:<mime>;base64,...` o `https://...`.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Image".
 */
export interface Image {
  type: "image";
  id: string;
  anchor?: Anchor | null;
  z?: number;
  ttl_s?: number | null;
  on_press?: OnPress | null;
  rect_mm?: RectMM | null;
  src: string;
  opacity?: number;
}
/**
 * Efecto de shader WGSL por nombre (catálogo del compositor), con parámetros numéricos o vectores.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Effect".
 */
export interface Effect {
  type: "effect";
  id: string;
  anchor?: Anchor | null;
  z?: number;
  ttl_s?: number | null;
  on_press?: OnPress | null;
  shader: string;
  params?: {
    [k: string]: unknown;
  };
  rect_mm?: RectMM | null;
  polygon_mm?: [PointMM, PointMM, PointMM, ...PointMM[]] | null;
}
/**
 * Crea o reemplaza widgets (por `id`).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Project".
 */
export interface Project {
  type: "project";
  request_id?: string | null;
  widgets: (Label | Halo | Arrow | Panel | Button | Image | Effect)[];
}
/**
 * Cambia solo algunos campos de un widget existente (merge superficial).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Update".
 */
export interface Update {
  type: "update";
  request_id?: string | null;
  id: string;
  set: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Remove".
 */
export interface Remove {
  type: "remove";
  request_id?: string | null;
  ids: string[];
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Clear".
 */
export interface Clear {
  type: "clear";
  request_id?: string | null;
}
/**
 * Proyecta una pregunta con un botón por opción; responde con el evento `ask_answered` o vence.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Ask".
 */
export interface Ask {
  type: "ask";
  request_id?: string | null;
  question: string;
  /**
   * @minItems 1
   * @maxItems 6
   */
  options:
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string];
  timeout_s?: number;
  anchor?: Anchor | null;
}
/**
 * Emite un evento propio (`flow_emitted`), p. ej. para avisar al agente que un flujo terminó.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Emit".
 */
export interface Emit {
  type: "emit";
  request_id?: string | null;
  event: string;
  data?: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "RunFlow".
 */
export interface RunFlow {
  type: "run_flow";
  request_id?: string | null;
  flow: Flow;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Flow".
 */
export interface Flow {
  id: string;
  initial: string;
  states: FlowState[];
  cleanup?: boolean;
  timeout_s?: number | null;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "FlowState".
 */
export interface FlowState {
  id: string;
  on_enter?: (Project | Update | Remove | Clear | Emit | HomeCall)[];
  transitions?: Transition[];
  final?: boolean;
}
/**
 * Llamada a un servicio de Home Assistant (`light.turn_on`, etc.).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "HomeCall".
 */
export interface HomeCall {
  type: "home_call";
  request_id?: string | null;
  domain: string;
  service: string;
  data?: {
    [k: string]: unknown;
  };
  requires_confirmation?: boolean;
}
/**
 * Se dispara por `on` (evento) o por `after_s` (segundos en el estado). Ejecuta `actions` y pasa a `to`.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Transition".
 */
export interface Transition {
  on?: EventFilter | null;
  after_s?: number | null;
  to: string;
  actions?: (Project | Update | Remove | Clear | Emit | HomeCall)[];
}
/**
 * Coincide con un evento si `event` == su `type` y cada par de `where` es igual al campo (rutas con punto,
 *     p. ej. `{"widget_id": "next"}` o `{"touch.widget_id": "next"}`).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "EventFilter".
 */
export interface EventFilter {
  event: string;
  where?: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "StopFlow".
 */
export interface StopFlow {
  type: "stop_flow";
  request_id?: string | null;
  flow_id: string;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Calibrate".
 */
export interface Calibrate {
  type: "calibrate";
  request_id?: string | null;
  mode?: "precise" | "quick" | "verify";
  camera_id?: string | null;
  projector_id?: string | null;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "SetTransform".
 */
export interface SetTransform {
  type: "set_transform";
  request_id?: string | null;
  transform: Transform;
}
/**
 * Transformación con nombre y versión entre dos marcos.
 *
 *     `homography`: `matrix` = 9 floats en fila mayor (lleva puntos de `from_frame` a `to_frame`).
 *     `lut`: `lut_ref` apunta a la LUT (p. ej. `shm:lut/proj:main/v3` o una ruta de asset); `matrix` es opcional
 *     (aproximación). `residual_mm`: error medido en la última verificación.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Transform".
 */
export interface Transform {
  from_frame: string;
  to_frame: string;
  kind?: "homography" | "lut";
  matrix?: [number, number, number, number, number, number, number, number, number] | null;
  lut_ref?: string | null;
  version?: number;
  residual_mm?: number | null;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "ObjectAppeared".
 */
export interface ObjectAppeared {
  type: "object_appeared";
  object: ObjectState;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "ObjectState".
 */
export interface ObjectState {
  id: string;
  label: string;
  confidence: number;
  /**
   * Polígono cerrado en mm: [[x,y],...]
   *
   * @minItems 3
   */
  polygon_mm: [PointMM, PointMM, PointMM, ...PointMM[]];
  center_mm: PointMM;
  bbox_mm?: RectMM | null;
  since: number;
  last_seen: number;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "ObjectMoved".
 */
export interface ObjectMoved {
  type: "object_moved";
  object_id: string;
  center_mm: PointMM;
  polygon_mm?: [PointMM, PointMM, PointMM, ...PointMM[]] | null;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "ObjectRemoved".
 */
export interface ObjectRemoved {
  type: "object_removed";
  object_id: string;
  label?: string | null;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "TouchDown".
 */
export interface TouchDown {
  type: "touch_down";
  touch: Touch;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Touch".
 */
export interface Touch {
  id: string;
  phase: "down" | "move" | "up";
  point_mm: PointMM;
  widget_id?: string | null;
  object_id?: string | null;
  trigger?: "api" | "dwell" | "pinch" | "shadow";
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "TouchMove".
 */
export interface TouchMove {
  type: "touch_move";
  touch: Touch;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "TouchUp".
 */
export interface TouchUp {
  type: "touch_up";
  touch: Touch;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "WidgetPressed".
 */
export interface WidgetPressed {
  type: "widget_pressed";
  widget_id: string;
  value?: string | null;
  trigger?: "api" | "dwell" | "pinch" | "shadow";
  point_mm?: PointMM | null;
}
/**
 * Progreso de permanencia sobre un punto (para animar el anillo); `progress` = 1 confirma.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Dwell".
 */
export interface Dwell {
  type: "dwell";
  point_mm: PointMM;
  progress: number;
  widget_id?: string | null;
  object_id?: string | null;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "AskAnswered".
 */
export interface AskAnswered {
  type: "ask_answered";
  request_id?: string | null;
  answer?: string | null;
  index?: number | null;
  timed_out?: boolean;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "CalibrationDrift".
 */
export interface CalibrationDrift {
  type: "calibration_drift";
  from_frame: string;
  to_frame: string;
  residual_mm: number;
  threshold_mm: number;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "FlowStateChanged".
 */
export interface FlowStateChanged {
  type: "flow_state_changed";
  flow_id: string;
  from_state?: string | null;
  to_state?: string | null;
  cause?: string | null;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "FlowEmitted".
 */
export interface FlowEmitted {
  type: "flow_emitted";
  flow_id?: string | null;
  event: string;
  data?: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "FrameMeta".
 */
export interface FrameMeta {
  type: "frame_meta";
  camera_id: string;
  seq: number;
  t_capture_ns: number;
  w: number;
  h: number;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "DeviceChanged".
 */
export interface DeviceChanged {
  type: "device_changed";
  device: Device;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Device".
 */
export interface Device {
  id: string;
  kind: "camera" | "home_assistant" | "midi" | "phone" | "projector";
  adapter: string;
  caps?: string[];
  status?: "degraded" | "offline" | "online" | "unknown";
  meta?: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Envelope".
 */
export interface Envelope {
  type: string;
  v?: number;
  seq: number;
  t_capture_ns?: number | null;
  t_pub_ns: number;
  source: string;
  node_id: string;
  payload:
    | Project
    | Update
    | Remove
    | Clear
    | Ask
    | Emit
    | RunFlow
    | StopFlow
    | Calibrate
    | SetTransform
    | HomeCall
    | ObjectAppeared
    | ObjectMoved
    | ObjectRemoved
    | TouchDown
    | TouchMove
    | TouchUp
    | WidgetPressed
    | Dwell
    | AskAnswered
    | CalibrationDrift
    | FlowStateChanged
    | FlowEmitted
    | FrameMeta
    | DeviceChanged;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "SceneSnapshot".
 */
export interface SceneSnapshot {
  type: "scene";
  rev: number;
  t_send_ms?: number | null;
  surface: Surface;
  transforms?: Transform[];
  widgets?: (Label | Halo | Arrow | Panel | Button | Image | Effect)[];
  homography?: number[] | null;
}
/**
 * Superficie útil de la mesa en mm.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Surface".
 */
export interface Surface {
  width_mm: number;
  height_mm: number;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "SceneDiff".
 */
export interface SceneDiff {
  type: "scene_diff";
  rev: number;
  base_rev: number;
  t_send_ms?: number | null;
  upsert?: (Label | Halo | Arrow | Panel | Button | Image | Effect)[];
  remove?: string[];
  transforms?: Transform[] | null;
}
/**
 * Una vez por `rev`, en el primer cuadro presentado que la contiene.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Presented".
 */
export interface Presented {
  type: "presented";
  rev: number;
  t_present_ms: number;
}
/**
 * Cada 1 s.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Stats".
 */
export interface Stats {
  type: "stats";
  fps: number;
  frame_ms_p50: number;
  frame_ms_p95: number;
  dropped?: number;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Hello".
 */
export interface Hello {
  type: "hello";
  node_id: string;
  token: string;
  capabilities?: string[];
  version: string;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Welcome".
 */
export interface Welcome {
  type: "welcome";
  session_id: string;
  last_cursor?: number | null;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "StateSnapshot".
 */
export interface StateSnapshot {
  type: "state_snapshot";
  cursor: number;
  world: WorldState;
}
/**
 * Snapshot completo del nodo (lo que se manda a la nube al conectar o reconectar).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "WorldState".
 */
export interface WorldState {
  rev: number;
  surface: Surface;
  objects?: ObjectState[];
  hands?: HandState[];
  touches?: Touch[];
  widgets?: (Label | Halo | Arrow | Panel | Button | Image | Effect)[];
  devices?: Device[];
  transforms?: Transform[];
  active_flows?: string[];
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "HandState".
 */
export interface HandState {
  id: string;
  camera_id: string;
  handedness?: "left" | "right" | "unknown";
  landmarks_px?: PointPX[];
  tip_mm?: PointMM | null;
  pinch?: boolean;
  t_capture_ns: number;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "EventBatch".
 */
export interface EventBatch {
  type: "event_batch";
  cursor: number;
  events: Envelope[];
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "ToolCall".
 */
export interface ToolCall {
  type: "tool_call";
  call_id: string;
  tool: string;
  args?: {
    [k: string]: unknown;
  };
  timeout_s?: number | null;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "ToolResult".
 */
export interface ToolResult {
  type: "tool_result";
  call_id: string;
  ok: boolean;
  content?: (TextContent | ImageContent)[];
  structured?: {
    [k: string]: unknown;
  } | null;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "TextContent".
 */
export interface TextContent {
  type: "text";
  text: string;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "ImageContent".
 */
export interface ImageContent {
  type: "image";
  data: string;
  mime_type?: string;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Heartbeat".
 */
export interface Heartbeat {
  type: "heartbeat";
  t_ms: number;
  cursor?: number | null;
}
