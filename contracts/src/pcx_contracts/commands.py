"""Comandos hacia el nodo (unión discriminada por `type`).

Idempotencia: todo comando lleva `request_id` **obligatorio**, elegido por quien llama y estable entre reintentos
(el mismo `request_id` = la misma operación lógica). El nodo deduplica por `(node_id, request_id)` durante
`IDEMPOTENCY_WINDOW_S` y devuelve el resultado ya calculado. `ToolCall.call_id` es otra cosa: identifica un
intento de transporte (una llamada MCP concreta, lo genera el gateway) y sirve para correlacionar `tool_result`;
un reintento del agente produce otro `call_id` con el mismo `request_id`. El `request_id` se propaga a
`ToolResult.request_id`, `AskAnswered`, `HomeCallResult`, `ConfirmationRequested/Resolved`, `HomeCallDone`,
`FlowStateChanged` y `Blanked`.

`RunFlow` lleva un flujo (máquina de estados) que el nodo ejecuta localmente. Las acciones de un flujo
(`*Action`) son los mismos comandos sin `request_id`: el nodo les deriva `"<request_id del run_flow>/<estado>/<i>"`.

Semántica del flujo (también en el README):
- Una transición tiene **exactamente uno** de `on` (evento) o `after_s` (segundos desde que se entró al estado).
- Precedencia: al llegar un evento se prueban las transiciones `on` en orden de lista y gana la primera que
  coincide. Los temporizadores vencen después: si un evento y un `after_s` caen en el mismo tick, **gana el evento**.
  Entre temporizadores gana el menor `after_s` (empate: orden de lista).
- `after_s: 0` es una transición inmediata. Se permiten a lo más `MAX_IMMEDIATE_TRANSITIONS` seguidas; al pasar el
  límite el flujo se detiene con `cause: "immediate_loop"`. `check_flow` rechaza ciclos hechos solo de `after_s: 0`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from msgspec import Meta, Struct

from .geometry import Transform
from .widgets import Anchor, Widget

RequestId = Annotated[str, Meta(min_length=1, max_length=128, description="Clave de idempotencia (obligatoria)")]
IDEMPOTENCY_WINDOW_S = 600
MAX_IMMEDIATE_TRANSITIONS = 8
RESERVED_WIDGET_PREFIX = "pcx:"
"""Ids reservados para el nodo (p. ej. botones de confirmación); los comandos no pueden tocarlos."""


def _check_ids(ids: list[str]) -> None:
    bad = [i for i in ids if i.startswith(RESERVED_WIDGET_PREFIX)]
    if bad:
        raise ValueError(f"ids reservados del nodo ({RESERVED_WIDGET_PREFIX}*): {bad}")


# ---------------------------------------------------------------- acciones (comandos sin request_id)

class ActionBase(Struct, kw_only=True, tag_field="type"):
    pass


class ProjectAction(ActionBase, kw_only=True, tag="project"):
    """Crea o reemplaza widgets (por `id`)."""

    widgets: list[Widget]

    def __post_init__(self) -> None:
        _check_ids([w.id for w in self.widgets])


class UpdateAction(ActionBase, kw_only=True, tag="update"):
    """Cambia solo algunos campos de un widget existente (merge superficial)."""

    id: str
    set: dict[str, Any]

    def __post_init__(self) -> None:
        _check_ids([self.id])


class RemoveAction(ActionBase, kw_only=True, tag="remove"):
    ids: list[str]

    def __post_init__(self) -> None:
        _check_ids(self.ids)


class ClearAction(ActionBase, kw_only=True, tag="clear"):
    pass


class EmitAction(ActionBase, kw_only=True, tag="emit"):
    """Emite un evento propio (`flow_emitted`), p. ej. para avisar al agente que un flujo terminó."""

    event: str
    data: dict[str, Any] = {}


class HomeCallAction(ActionBase, kw_only=True, tag="home_call", forbid_unknown_fields=True):
    """Llamada a un servicio de Home Assistant (`light.turn_on`, etc.).

    Quien llama **no** decide si se confirma: lo decide la `HomePolicy` del nodo (ver `home.py`). Si requiere
    confirmación, el resultado es `pending{confirmation_id}` y la acción se ejecuta solo tras un toque físico o la
    consola. Campos desconocidos (p. ej. el antiguo `requires_confirmation`) se rechazan.
    """

    domain: Annotated[str, Meta(pattern=r"^[a-z0-9_]+$")]
    service: Annotated[str, Meta(pattern=r"^[a-z0-9_]+$")]
    data: dict[str, Any] = {}


Action = Union[ProjectAction, UpdateAction, RemoveAction, ClearAction, EmitAction, HomeCallAction]


# ---------------------------------------------------------------- flujos

class EventFilter(Struct, kw_only=True):
    """Coincide con un evento si `event` == su `type` y cada par de `where` es igual al campo (rutas con punto,
    p. ej. `{"widget_id": "next"}` o `{"touch.widget_id": "next"}`)."""

    event: str
    where: dict[str, Any] = {}


class Transition(Struct, kw_only=True):
    """Exactamente uno de `on` (evento) o `after_s` (segundos en el estado, ≥ 0). Ejecuta `actions` y pasa a `to`."""

    on: EventFilter | None = None
    after_s: Annotated[float, Meta(ge=0, le=86400)] | None = None
    to: str
    actions: list[Action] = []


class FlowState(Struct, kw_only=True):
    id: str
    on_enter: list[Action] = []
    transitions: list[Transition] = []
    final: bool = False


class Flow(Struct, kw_only=True):
    id: str
    initial: str
    states: Annotated[list[FlowState], Meta(min_length=1)]
    cleanup: bool = True
    """Al terminar o detenerse, borra los widgets que creó el flujo."""
    timeout_s: Annotated[float, Meta(gt=0, le=86400)] | None = None

    def __post_init__(self) -> None:
        errs = check_flow(self)
        if errs:
            raise ValueError("flujo inválido: " + "; ".join(errs))


def check_flow(flow: Flow) -> list[str]:
    """Errores de coherencia del flujo (vacío = válido). Se ejecuta en todo decode de `Flow`."""
    errs: list[str] = []
    ids = [s.id for s in flow.states]
    if not ids:
        errs.append("el flujo no tiene estados")
    errs += [f"estado duplicado: {i}" for i in sorted(set(ids)) if ids.count(i) > 1]
    if flow.initial not in ids:
        errs.append(f"initial {flow.initial!r} no existe")
    immediate: dict[str, list[str]] = {}
    for s in flow.states:
        if s.final and s.transitions:
            errs.append(f"{s.id}: un estado final no puede tener transiciones")
        for t in s.transitions:
            if t.to not in ids:
                errs.append(f"{s.id}: transición a {t.to!r} que no existe")
            if t.on is None and t.after_s is None:
                errs.append(f"{s.id}: transición a {t.to!r} sin `on` ni `after_s`")
            if t.on is not None and t.after_s is not None:
                errs.append(f"{s.id}: transición a {t.to!r} con `on` y `after_s` a la vez (ambiguo)")
            if t.after_s is not None and t.after_s < 0:
                errs.append(f"{s.id}: after_s negativo")
            if t.on is None and t.after_s == 0:
                immediate.setdefault(s.id, []).append(t.to)
    # ciclos formados solo por transiciones inmediatas
    seen: set[str] = set()

    def cyc(node: str, stack: list[str]) -> bool:
        if node in stack:
            return True
        if node in seen:
            return False
        seen.add(node)
        return any(cyc(n, stack + [node]) for n in immediate.get(node, []))

    if any(cyc(n, []) for n in list(immediate)):
        errs.append("ciclo de transiciones inmediatas (after_s: 0)")
    return errs


def flow_is_destructive(flow: Flow) -> bool:
    """True si el flujo puede actuar sobre el mundo físico (contiene `home_call`)."""
    acts = [a for s in flow.states for a in s.on_enter] + [
        a for s in flow.states for t in s.transitions for a in t.actions]
    return any(isinstance(a, HomeCallAction) for a in acts)


# ---------------------------------------------------------------- comandos (con request_id obligatorio)

class CommandBase(Struct, kw_only=True, tag_field="type"):
    request_id: RequestId


class Project(ProjectAction, kw_only=True, tag="project"):
    request_id: RequestId


class Update(UpdateAction, kw_only=True, tag="update"):
    request_id: RequestId


class Remove(RemoveAction, kw_only=True, tag="remove"):
    request_id: RequestId


class Clear(ClearAction, kw_only=True, tag="clear"):
    request_id: RequestId


class Emit(EmitAction, kw_only=True, tag="emit"):
    request_id: RequestId


class HomeCall(HomeCallAction, kw_only=True, tag="home_call"):
    request_id: RequestId


class Ask(CommandBase, kw_only=True, tag="ask"):
    """Proyecta una pregunta con un botón por opción; responde con `ask_answered{request_id}` o vence."""

    question: str
    options: Annotated[list[str], Meta(min_length=1, max_length=6)]
    timeout_s: Annotated[float, Meta(ge=1, le=600)] = 60.0
    anchor: Anchor | None = None


class RunFlow(CommandBase, kw_only=True, tag="run_flow"):
    flow: Flow


class StopFlow(CommandBase, kw_only=True, tag="stop_flow"):
    flow_id: str


class Calibrate(CommandBase, kw_only=True, tag="calibrate"):
    mode: Literal["quick", "precise", "verify"] = "quick"
    camera_id: str | None = None
    projector_id: str | None = None


class SetTransform(CommandBase, kw_only=True, tag="set_transform"):
    transform: Transform


class Blank(CommandBase, kw_only=True, tag="blank"):
    """Apaga la salida del proyector `ms` milisegundos (p. ej. ~300 ms para una «foto limpia» de la cámara).

    El renderer responde `blanked{request_id, t_start_ms, t_end_ms}` con el intervalo realmente oscuro; el nodo lo
    republica como evento `blanked` para que la percepción elija cuadros con `t_capture` dentro del intervalo.
    """

    ms: Annotated[int, Meta(ge=1, le=2000)]
    reason: str
    projector_id: str | None = None
    """None = todos los proyectores."""


Command = Union[Project, Update, Remove, Clear, Ask, Emit, RunFlow, StopFlow, Calibrate, SetTransform, HomeCall, Blank]
