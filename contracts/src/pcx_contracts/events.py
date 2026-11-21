"""Eventos que publica el nodo (unión discriminada por `type`). Viajan dentro de `envelope.Envelope`."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from msgspec import Meta, Struct

from .devices import Device
from .home import ConfirmVia
from .geometry import PointMM, PolygonMM, Sha256
from .world import ObjectState, Touch, Trigger


class EventBase(Struct, kw_only=True, tag_field="type"):
    pass


class ObjectAppeared(EventBase, kw_only=True, tag="object_appeared"):
    object: ObjectState


class ObjectMoved(EventBase, kw_only=True, tag="object_moved"):
    object_id: str
    center_mm: PointMM
    polygon_mm: PolygonMM | None = None


class ObjectRemoved(EventBase, kw_only=True, tag="object_removed"):
    object_id: str
    label: str | None = None


class TouchDown(EventBase, kw_only=True, tag="touch_down"):
    touch: Touch


class TouchMove(EventBase, kw_only=True, tag="touch_move"):
    touch: Touch


class TouchUp(EventBase, kw_only=True, tag="touch_up"):
    touch: Touch


class WidgetPressed(EventBase, kw_only=True, tag="widget_pressed"):
    widget_id: str
    value: str | None = None
    trigger: Trigger = "dwell"
    point_mm: PointMM | None = None


class Dwell(EventBase, kw_only=True, tag="dwell"):
    """Progreso de permanencia sobre un punto (para animar el anillo); `progress` = 1 confirma."""

    point_mm: PointMM
    progress: Annotated[float, Meta(ge=0.0, le=1.0)]
    widget_id: str | None = None
    object_id: str | None = None


class AskAnswered(EventBase, kw_only=True, tag="ask_answered"):
    request_id: str
    """El del comando `ask` (obligatorio)."""
    answer: str | None = None
    """Opción elegida; None si venció el plazo."""
    index: int | None = None
    timed_out: bool = False


class CalibrationDrift(EventBase, kw_only=True, tag="calibration_drift"):
    from_frame: str
    to_frame: str
    residual_mm: float
    threshold_mm: float


class FlowStateChanged(EventBase, kw_only=True, tag="flow_state_changed"):
    flow_id: str
    request_id: str
    """El del `run_flow` que lo inició."""
    from_state: str | None = None
    to_state: str | None = None
    """None si el flujo terminó o se detuvo."""
    cause: str | None = None


class FlowEmitted(EventBase, kw_only=True, tag="flow_emitted"):
    flow_id: str | None = None
    request_id: str | None = None
    event: str
    data: dict[str, Any] = {}


class FrameMeta(EventBase, kw_only=True, tag="frame_meta"):
    camera_id: str
    seq: int
    t_capture_ns: int
    w: int
    h: int


class ConfirmationRequested(EventBase, kw_only=True, tag="confirmation_requested"):
    """El nodo espera confirmación física o de consola para una acción (ver `home.py`)."""

    confirmation_id: str
    request_id: str
    action_digest: Sha256
    summary: str
    """Texto legible, p. ej. `lock.unlock lock.puerta`."""
    via: list[ConfirmVia]
    expires_ns: int


class ConfirmationResolved(EventBase, kw_only=True, tag="confirmation_resolved"):
    confirmation_id: str
    request_id: str
    action_digest: Sha256
    approved: bool
    via: Literal["touch", "console", "timeout"]


class HomeCallDone(EventBase, kw_only=True, tag="home_call_done"):
    """Desenlace final de un `home_call` (inmediato o tras la confirmación)."""

    request_id: str
    confirmation_id: str | None = None
    status: Literal["done", "failed", "denied", "expired", "rejected"]
    error: str | None = None


class Blanked(EventBase, kw_only=True, tag="blanked"):
    """El proyector estuvo oscuro en [t_start_ms, t_end_ms] (ms de época, reportado por el renderer).
    Un cuadro de cámara es limpio si su `t_capture` cae dentro, descontando la latencia proyector→cámara."""

    request_id: str
    projector_id: str
    t_start_ms: float
    t_end_ms: float


class DeviceChanged(EventBase, kw_only=True, tag="device_changed"):
    device: Device


Event = Union[
    ObjectAppeared, ObjectMoved, ObjectRemoved, TouchDown, TouchMove, TouchUp, WidgetPressed, Dwell, AskAnswered,
    CalibrationDrift, FlowStateChanged, FlowEmitted, FrameMeta, DeviceChanged, ConfirmationRequested,
    ConfirmationResolved, HomeCallDone, Blanked,
]
