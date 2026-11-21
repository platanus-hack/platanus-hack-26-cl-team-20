/* Generado desde contracts/schema/pcx.ts.json. No editar: corre contracts/ts/generate.sh */

/**
 * Punto en mm del marco `table_mm`. En el cable: `[x, y]` (exactamente 2 números).
 *
 * @minItems 2
 * @maxItems 2
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "PointMM".
 */
export type PointMM = [number, number];
/**
 * Rectángulo alineado a ejes en mm. En el cable: `[x, y, w, h]` (x, y = esquina superior izquierda).
 *
 * @minItems 4
 * @maxItems 4
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "RectMM".
 */
export type RectMM = [number, number, number, number];
/**
 * Punto en px de una imagen (cámara o proyector). En el cable: `[x, y]` (exactamente 2 números).
 *
 * @minItems 2
 * @maxItems 2
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "PointPX".
 */
export type PointPX = [number, number];

/**
 * Contratos pcx v2
 */
export interface Pcx {
  widget?: Label | Halo | Arrow | Panel | Button | Image | Effect;
  command?:
    Project | Update | Remove | Clear | Ask | Emit | RunFlow | StopFlow | Calibrate | SetTransform | HomeCall | Blank;
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
    | DeviceChanged
    | ConfirmationRequested
    | ConfirmationResolved
    | HomeCallDone
    | Blanked;
  envelope?: Envelope;
  to_renderer?: SceneSnapshot | SceneDiff | RenderBlank;
  from_renderer?: Presented | Stats | RenderBlanked;
  cloud?:
    | Hello
    | Welcome
    | HelloRejected
    | StateSnapshot
    | HistoryGap
    | EventBatch
    | ReplayDone
    | ToolCall
    | ToolResult
    | Heartbeat;
  world?: WorldState;
  flow?: Flow;
  transform?: HomographyTransform | LutTransform;
  home_policy?: HomePolicy;
  home_call_result?: HomeCallResult;
  confirm_action?: ConfirmAction;
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
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Project".
 */
export interface Project {
  type: "project";
  widgets: (Label | Halo | Arrow | Panel | Button | Image | Effect)[];
  /**
   * Clave de idempotencia (obligatoria)
   */
  request_id: string;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Update".
 */
export interface Update {
  type: "update";
  id: string;
  set: {
    [k: string]: unknown;
  };
  /**
   * Clave de idempotencia (obligatoria)
   */
  request_id: string;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Remove".
 */
export interface Remove {
  type: "remove";
  ids: string[];
  /**
   * Clave de idempotencia (obligatoria)
   */
  request_id: string;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Clear".
 */
export interface Clear {
  type: "clear";
  /**
   * Clave de idempotencia (obligatoria)
   */
  request_id: string;
}
/**
 * Proyecta una pregunta con un botón por opción; responde con `ask_answered{request_id}` o vence.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Ask".
 */
export interface Ask {
  type: "ask";
  /**
   * Clave de idempotencia (obligatoria)
   */
  request_id: string;
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
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Emit".
 */
export interface Emit {
  type: "emit";
  event: string;
  data?: {
    [k: string]: unknown;
  };
  /**
   * Clave de idempotencia (obligatoria)
   */
  request_id: string;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "RunFlow".
 */
export interface RunFlow {
  type: "run_flow";
  /**
   * Clave de idempotencia (obligatoria)
   */
  request_id: string;
  flow: Flow;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Flow".
 */
export interface Flow {
  id: string;
  initial: string;
  /**
   * @minItems 1
   */
  states: [FlowState, ...FlowState[]];
  cleanup?: boolean;
  timeout_s?: number | null;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "FlowState".
 */
export interface FlowState {
  id: string;
  on_enter?: (ProjectAction | UpdateAction | RemoveAction | ClearAction | EmitAction | HomeCallAction)[];
  transitions?: Transition[];
  final?: boolean;
}
/**
 * Crea o reemplaza widgets (por `id`).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "ProjectAction".
 */
export interface ProjectAction {
  type: "project";
  widgets: (Label | Halo | Arrow | Panel | Button | Image | Effect)[];
}
/**
 * Cambia solo algunos campos de un widget existente (merge superficial).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "UpdateAction".
 */
export interface UpdateAction {
  type: "update";
  id: string;
  set: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "RemoveAction".
 */
export interface RemoveAction {
  type: "remove";
  ids: string[];
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "ClearAction".
 */
export interface ClearAction {
  type: "clear";
}
/**
 * Emite un evento propio (`flow_emitted`), p. ej. para avisar al agente que un flujo terminó.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "EmitAction".
 */
export interface EmitAction {
  type: "emit";
  event: string;
  data?: {
    [k: string]: unknown;
  };
}
/**
 * Llamada a un servicio de Home Assistant (`light.turn_on`, etc.).
 *
 *     Quien llama **no** decide si se confirma: lo decide la `HomePolicy` del nodo (ver `home.py`). Si requiere
 *     confirmación, el resultado es `pending{confirmation_id}` y la acción se ejecuta solo tras un toque físico o la
 *     consola. Campos desconocidos (p. ej. el antiguo `requires_confirmation`) se rechazan.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "HomeCallAction".
 */
export interface HomeCallAction {
  type: "home_call";
  domain: string;
  service: string;
  data?: {
    [k: string]: unknown;
  };
}
/**
 * Exactamente uno de `on` (evento) o `after_s` (segundos en el estado, ≥ 0). Ejecuta `actions` y pasa a `to`.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Transition".
 */
export interface Transition {
  on?: EventFilter | null;
  after_s?: number | null;
  to: string;
  actions?: (ProjectAction | UpdateAction | RemoveAction | ClearAction | EmitAction | HomeCallAction)[];
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
  /**
   * Clave de idempotencia (obligatoria)
   */
  request_id: string;
  flow_id: string;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Calibrate".
 */
export interface Calibrate {
  type: "calibrate";
  /**
   * Clave de idempotencia (obligatoria)
   */
  request_id: string;
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
  /**
   * Clave de idempotencia (obligatoria)
   */
  request_id: string;
  transform: HomographyTransform | LutTransform;
}
/**
 * `matrix` lleva puntos de `from_frame` a `to_frame` (coordenadas homogéneas, fila mayor).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "HomographyTransform".
 */
export interface HomographyTransform {
  kind: "homography";
  from_frame: string;
  to_frame: string;
  version?: number;
  residual_mm?: number | null;
  /**
   * Homografía 3x3 en fila mayor (exactamente 9 números)
   *
   * @minItems 9
   * @maxItems 9
   */
  matrix: [number, number, number, number, number, number, number, number, number];
}
/**
 * LUT precisa (gray code). `approx_matrix`: homografía aproximada opcional para consumidores sin LUT.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "LutTransform".
 */
export interface LutTransform {
  kind: "lut";
  from_frame: string;
  to_frame: string;
  version?: number;
  residual_mm?: number | null;
  lut: LutDescriptor;
  approx_matrix?: [number, number, number, number, number, number, number, number, number] | null;
}
/**
 * LUT densa en un archivo `.npy`.
 *
 *     - `format`: `npy_f32_hw2` = array float32 de forma `(height, width, 2)`, C-order.
 *     - Índice: `lut[v, u]` corresponde a la muestra del marco de entrada (`from_frame`) cuyo **centro** está en las
 *       coordenadas enteras `(u * grid_step, v * grid_step)` (convención `integer_centers`: el centro del px (0,0) es
 *       (0,0), no (0.5,0.5)). `grid_step` está en unidades de `from_frame` (px, o mm si es `table_mm`).
 *     - Valor: `(x, y)` en **px de salida** (`to_frame`, que debe ser `proj:*` o `cam:*`). NaN = sin dato.
 *     - `path`: ruta local o `shm:` del nodo; `sha256`: del archivo `.npy` completo (verificar antes de usar).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "LutDescriptor".
 */
export interface LutDescriptor {
  format?: "npy_f32_hw2";
  width: number;
  height: number;
  units?: "output_px";
  sampling?: "integer_centers";
  grid_step?: number;
  path: string;
  sha256: string;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "HomeCall".
 */
export interface HomeCall {
  type: "home_call";
  domain: string;
  service: string;
  data?: {
    [k: string]: unknown;
  };
  /**
   * Clave de idempotencia (obligatoria)
   */
  request_id: string;
}
/**
 * Apaga la salida del proyector `ms` milisegundos (p. ej. ~300 ms para una «foto limpia» de la cámara).
 *
 *     El renderer responde `blanked{request_id, t_start_ms, t_end_ms}` con el intervalo realmente oscuro; el nodo lo
 *     republica como evento `blanked` para que la percepción elija cuadros con `t_capture` dentro del intervalo.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Blank".
 */
export interface Blank {
  type: "blank";
  /**
   * Clave de idempotencia (obligatoria)
   */
  request_id: string;
  ms: number;
  reason: string;
  projector_id?: string | null;
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
   * Polígono cerrado en mm: [[x,y],...] (≥ 3)
   *
   * @minItems 3
   */
  polygon_mm: [PointMM, PointMM, PointMM, ...PointMM[]];
  center_mm: PointMM;
  bbox_mm?: RectMM | null;
  /**
   * (ns como cadena decimal; usar BigInt)
   */
  since_ns: string;
  /**
   * (ns como cadena decimal; usar BigInt)
   */
  last_seen_ns: string;
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
  request_id: string;
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
  request_id: string;
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
  request_id?: string | null;
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
  /**
   * (ns como cadena decimal; usar BigInt)
   */
  t_capture_ns: string;
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
 * El nodo espera confirmación física o de consola para una acción (ver `home.py`).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "ConfirmationRequested".
 */
export interface ConfirmationRequested {
  type: "confirmation_requested";
  confirmation_id: string;
  request_id: string;
  action_digest: string;
  summary: string;
  via: ("console" | "touch")[];
  /**
   * (ns como cadena decimal; usar BigInt)
   */
  expires_ns: string;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "ConfirmationResolved".
 */
export interface ConfirmationResolved {
  type: "confirmation_resolved";
  confirmation_id: string;
  request_id: string;
  action_digest: string;
  approved: boolean;
  via: "console" | "timeout" | "touch";
}
/**
 * Desenlace final de un `home_call` (inmediato o tras la confirmación).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "HomeCallDone".
 */
export interface HomeCallDone {
  type: "home_call_done";
  request_id: string;
  confirmation_id?: string | null;
  status: "denied" | "done" | "expired" | "failed" | "rejected";
  error?: string | null;
}
/**
 * El proyector estuvo oscuro en [t_start_ms, t_end_ms] (ms de época, reportado por el renderer).
 *     Un cuadro de cámara es limpio si su `t_capture` cae dentro, descontando la latencia proyector→cámara.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Blanked".
 */
export interface Blanked {
  type: "blanked";
  request_id: string;
  projector_id: string;
  t_start_ms: number;
  t_end_ms: number;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Envelope".
 */
export interface Envelope {
  type: string;
  v: number;
  seq: number;
  t_capture_ns?: string | null;
  /**
   * (ns como cadena decimal; usar BigInt)
   */
  t_pub_ns: string;
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
    | Blank
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
    | DeviceChanged
    | ConfirmationRequested
    | ConfirmationResolved
    | HomeCallDone
    | Blanked;
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
  transforms?: (HomographyTransform | LutTransform)[];
  widgets?: (Label | Halo | Arrow | Panel | Button | Image | Effect)[];
  homography?: [number, number, number, number, number, number, number, number, number] | null;
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
  transforms?: (HomographyTransform | LutTransform)[] | null;
}
/**
 * Salida completamente negra durante `ms` desde el próximo vsync (encima de la escena, que sigue viva).
 *     Un `blank` nuevo mientras hay uno activo lo reemplaza (extiende o acorta desde ese momento).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "RenderBlank".
 */
export interface RenderBlank {
  type: "blank";
  request_id: string;
  ms: number;
  reason: string;
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
 * Intervalo en que la salida estuvo realmente negra: `t_start_ms` = presentación del primer cuadro negro,
 *     `t_end_ms` = presentación del primer cuadro con escena otra vez (ms de época, como `presented`).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "RenderBlanked".
 */
export interface RenderBlanked {
  type: "blanked";
  request_id: string;
  t_start_ms: number;
  t_end_ms: number;
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
  /**
   * @minItems 1
   */
  contract_versions: [number, ...number[]];
  stream_id: string;
  log_first_cursor: number;
  log_last_cursor: number;
  retention?: LogRetention;
}
/**
 * Cuánto historial retiene el nodo para replay (se descarta lo que exceda cualquiera de los dos).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "LogRetention".
 */
export interface LogRetention {
  max_events?: number;
  max_age_s?: number;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "Welcome".
 */
export interface Welcome {
  type: "welcome";
  session_id: string;
  contract_version: number;
  last_stream_id?: string | null;
  last_cursor?: number | null;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "HelloRejected".
 */
export interface HelloRejected {
  type: "hello_rejected";
  reason: "auth" | "other" | "version";
  supported_versions?: number[];
  detail?: string | null;
}
/**
 * Estado recuperado. No implica historial completo (ver `replay_done`).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "StateSnapshot".
 */
export interface StateSnapshot {
  type: "state_snapshot";
  stream_id: string;
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
  transforms?: (HomographyTransform | LutTransform)[];
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
  /**
   * (ns como cadena decimal; usar BigInt)
   */
  t_capture_ns: string;
}
/**
 * Eventos `seq` en [from, to] (inclusive) de `stream_id` que no se pueden reenviar.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "HistoryGap".
 */
export interface HistoryGap {
  type: "history_gap";
  stream_id: string;
  from: number;
  to: number | null;
  reason: "retention" | "stream_reset";
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "EventBatch".
 */
export interface EventBatch {
  type: "event_batch";
  stream_id: string;
  cursor: number;
  events: Envelope[];
  replay?: boolean;
}
/**
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "ReplayDone".
 */
export interface ReplayDone {
  type: "replay_done";
  stream_id: string;
  cursor: number;
  history_complete: boolean;
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
  request_id?: string | null;
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
/**
 * Política del nodo. Patrones `domain.service` con comodines (`light.*`, `*`).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "HomePolicy".
 */
export interface HomePolicy {
  allow?: string[];
  confirm?: string[];
  confirm_via?: ("console" | "touch")[];
  confirm_timeout_s?: number;
}
/**
 * Resultado inmediato de un `home_call` (va en `ToolResult.structured` de la tool `home`).
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "HomeCallResult".
 */
export interface HomeCallResult {
  request_id: string;
  status: "denied" | "done" | "failed" | "pending";
  confirmation_id?: string | null;
  action_digest?: string | null;
  expires_ns?: string | null;
  error?: string | null;
}
/**
 * Consola local → nodo. Aprueba o rechaza una confirmación pendiente; debe repetir el `action_digest`.
 *
 * This interface was referenced by `Pcx`'s JSON-Schema
 * via the `definition` "ConfirmAction".
 */
export interface ConfirmAction {
  type: "confirm_action";
  confirmation_id: string;
  action_digest: string;
  approve: boolean;
  operator: string;
}
