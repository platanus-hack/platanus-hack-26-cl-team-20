"""Eventos que publica el nodo (unión discriminada por `type`). Viajan dentro de `envelope.Envelope`."""

from __future__ import annotations

from typing import Any, Union

from msgspec import Struct

from .devices import Device
from .geometry import PointMM, PolygonMM
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
    progress: float
    widget_id: str | None = None
    object_id: str | None = None


class AskAnswered(EventBase, kw_only=True, tag="ask_answered"):
    request_id: str | None = None
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
    from_state: str | None = None
    to_state: str | None = None
    """None si el flujo terminó o se detuvo."""
    cause: str | None = None


class FlowEmitted(EventBase, kw_only=True, tag="flow_emitted"):
    flow_id: str | None = None
    event: str
    data: dict[str, Any] = {}


class FrameMeta(EventBase, kw_only=True, tag="frame_meta"):
    camera_id: str
    seq: int
    t_capture_ns: int
    w: int
    h: int


class DeviceChanged(EventBase, kw_only=True, tag="device_changed"):
    device: Device


Event = Union[
    ObjectAppeared, ObjectMoved, ObjectRemoved, TouchDown, TouchMove, TouchUp, WidgetPressed, Dwell, AskAnswered,
    CalibrationDrift, FlowStateChanged, FlowEmitted, FrameMeta, DeviceChanged,
]
