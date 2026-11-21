"""Estado del mundo que mantiene el nodo (dueño) y refleja la nube. Tiempos: ns de época (int)."""

from __future__ import annotations

from typing import Literal

from msgspec import Struct

from .devices import Device
from .geometry import PointMM, PointPX, PolygonMM, RectMM, Surface, Transform
from .widgets import Trigger, Widget


class ObjectState(Struct, kw_only=True):
    id: str
    label: str
    confidence: float
    polygon_mm: PolygonMM
    center_mm: PointMM
    bbox_mm: RectMM | None = None
    since_ns: int
    """ns de época en que apareció."""
    last_seen_ns: int
    """ns de época de la última detección."""


class HandState(Struct, kw_only=True):
    id: str
    camera_id: str
    handedness: Literal["left", "right", "unknown"] = "unknown"
    landmarks_px: list[PointPX] = []
    """21 landmarks de MediaPipe en px de la cámara `camera_id`."""
    tip_mm: PointMM | None = None
    """Punta del índice proyectada a `table_mm` (None si no hay transformación)."""
    pinch: bool = False
    t_capture_ns: int


TouchPhase = Literal["down", "move", "up"]


class Touch(Struct, kw_only=True):
    id: str
    phase: TouchPhase
    point_mm: PointMM
    widget_id: str | None = None
    object_id: str | None = None
    trigger: Trigger = "dwell"


class WorldState(Struct, kw_only=True):
    """Snapshot completo del nodo (lo que se manda a la nube al conectar o reconectar)."""

    rev: int
    surface: Surface
    objects: list[ObjectState] = []
    hands: list[HandState] = []
    touches: list[Touch] = []
    widgets: list[Widget] = []
    devices: list[Device] = []
    transforms: list[Transform] = []
    active_flows: list[str] = []
