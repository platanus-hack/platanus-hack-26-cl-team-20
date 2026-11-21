"""Comandos hacia el nodo (unión discriminada por `type`). Idempotentes con `request_id`.

`RunFlow` lleva un flujo (máquina de estados) que el nodo ejecuta localmente: cada estado tiene acciones de
entrada y transiciones disparadas por eventos (filtro por `type` + igualdad de campos) o por tiempo (`after_s`).
Las acciones son comandos: project, update, remove, clear, emit, home_call.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from msgspec import Meta, Struct

from .geometry import Transform
from .widgets import Anchor, Widget


class CommandBase(Struct, kw_only=True, tag_field="type"):
    request_id: str | None = None


class Project(CommandBase, kw_only=True, tag="project"):
    """Crea o reemplaza widgets (por `id`)."""

    widgets: list[Widget]


class Update(CommandBase, kw_only=True, tag="update"):
    """Cambia solo algunos campos de un widget existente (merge superficial)."""

    id: str
    set: dict[str, Any]


class Remove(CommandBase, kw_only=True, tag="remove"):
    ids: list[str]


class Clear(CommandBase, kw_only=True, tag="clear"):
    pass


class Ask(CommandBase, kw_only=True, tag="ask"):
    """Proyecta una pregunta con un botón por opción; responde con el evento `ask_answered` o vence."""

    question: str
    options: Annotated[list[str], Meta(min_length=1, max_length=6)]
    timeout_s: Annotated[float, Meta(ge=1, le=600)] = 60.0
    anchor: Anchor | None = None


class Emit(CommandBase, kw_only=True, tag="emit"):
    """Emite un evento propio (`flow_emitted`), p. ej. para avisar al agente que un flujo terminó."""

    event: str
    data: dict[str, Any] = {}


class HomeCall(CommandBase, kw_only=True, tag="home_call"):
    """Llamada a un servicio de Home Assistant (`light.turn_on`, etc.)."""

    domain: str
    service: str
    data: dict[str, Any] = {}
    requires_confirmation: bool = True


class EventFilter(Struct, kw_only=True):
    """Coincide con un evento si `event` == su `type` y cada par de `where` es igual al campo (rutas con punto,
    p. ej. `{"widget_id": "next"}` o `{"touch.widget_id": "next"}`)."""

    event: str
    where: dict[str, Any] = {}


Action = Union[Project, Update, Remove, Clear, Emit, HomeCall]


class Transition(Struct, kw_only=True):
    """Se dispara por `on` (evento) o por `after_s` (segundos en el estado). Ejecuta `actions` y pasa a `to`."""

    on: EventFilter | None = None
    after_s: float | None = None
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
    states: list[FlowState]
    cleanup: bool = True
    """Al terminar o detenerse, borra los widgets que creó el flujo."""
    timeout_s: float | None = None


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


Command = Union[Project, Update, Remove, Clear, Ask, Emit, RunFlow, StopFlow, Calibrate, SetTransform, HomeCall]


def check_flow(flow: Flow) -> list[str]:
    """Errores de coherencia del flujo (vacío = válido)."""
    ids = [s.id for s in flow.states]
    errs = [f"estado duplicado: {i}" for i in set(ids) if ids.count(i) > 1]
    if flow.initial not in ids:
        errs.append(f"initial {flow.initial!r} no existe")
    for s in flow.states:
        for t in s.transitions:
            if t.to not in ids:
                errs.append(f"{s.id}: transición a {t.to!r} que no existe")
            if t.on is None and t.after_s is None:
                errs.append(f"{s.id}: transición a {t.to!r} sin `on` ni `after_s`")
    return errs
