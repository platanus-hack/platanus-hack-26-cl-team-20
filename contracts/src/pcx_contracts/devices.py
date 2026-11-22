"""Dispositivos que un nodo expone (cámaras, proyectores, celulares, Home Assistant, MIDI)."""

from __future__ import annotations

from typing import Any, Literal

from msgspec import Struct

DeviceKind = Literal["camera", "projector", "phone", "home_assistant", "midi"]
DeviceStatus = Literal["online", "offline", "degraded", "unknown"]


class Device(Struct, kw_only=True):
    id: str
    kind: DeviceKind
    adapter: str
    """Adaptador que lo maneja, p. ej. `uvc`, `webrtc-phone`, `wgpu-compositor`, `ha-ws`."""
    caps: list[str] = []
    """Capacidades, p. ej. `frames`, `depth`, `project`, `touch`, `services`."""
    status: DeviceStatus = "unknown"
    meta: dict[str, Any] = {}
