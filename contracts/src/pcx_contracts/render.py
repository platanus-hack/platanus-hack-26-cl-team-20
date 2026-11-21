"""Protocolo core ↔ renderer (compositor). Evolución de PROTOCOLO-ESCENA v0.

Cambios respecto de v0:
- `homography` (9 floats) pasa a `transforms: [Transform]`; el renderer usa la de `table_mm → proj:<id>`.
  `homography` se mantiene opcional (legado) para los renderers v0.
- Además de la escena completa (`scene`) existe `scene_diff{rev, base_rev, upsert, remove, transforms?}`.
  Si `base_rev` no coincide con la última `rev` aplicada, el renderer pide (o espera) un `scene` completo.
- Widgets: label usa `anchor.point_mm: [x,y]` en vez de `anchor: {x_mm, y_mm}`; se agregan `z`, `ttl_s`,
  `on_press`, `image` y `effect`. Hacia el renderer la geometría va resuelta en mm absolutos; `ttl_s` y
  `on_press` los gestiona el core (el renderer los puede ignorar). `to_legacy()` produce el formato v0.
- `presented` y `stats` no cambian.
"""

from __future__ import annotations

from typing import Any, Union

from msgspec import Struct, to_builtins

from .geometry import TABLE_MM, Surface, Transform
from .widgets import Label, Widget


class SceneSnapshot(Struct, kw_only=True, tag_field="type", tag="scene"):
    rev: int
    t_send_ms: float | None = None
    surface: Surface
    transforms: list[Transform] = []
    widgets: list[Widget] = []
    homography: list[float] | None = None
    """Legado v0: igual a la `matrix` de `table_mm → proj:*`."""


class SceneDiff(Struct, kw_only=True, tag_field="type", tag="scene_diff"):
    rev: int
    base_rev: int
    t_send_ms: float | None = None
    upsert: list[Widget] = []
    remove: list[str] = []
    transforms: list[Transform] | None = None
    """Si viene, reemplaza la lista completa de transformaciones."""


class Presented(Struct, kw_only=True, tag_field="type", tag="presented"):
    """Una vez por `rev`, en el primer cuadro presentado que la contiene."""

    rev: int
    t_present_ms: float


class Stats(Struct, kw_only=True, tag_field="type", tag="stats"):
    """Cada 1 s."""

    fps: float
    frame_ms_p50: float
    frame_ms_p95: float
    dropped: int = 0


ToRenderer = Union[SceneSnapshot, SceneDiff]
FromRenderer = Union[Presented, Stats]


def projector_matrix(transforms: list[Transform]) -> list[float] | None:
    for t in transforms:
        if t.from_frame == TABLE_MM and t.to_frame.startswith("proj:") and t.matrix is not None:
            return list(t.matrix)
    return None


def to_legacy(snap: SceneSnapshot) -> dict[str, Any]:
    """Convierte a PROTOCOLO-ESCENA v0 (para los renderers del spike). `image`/`effect` se omiten."""
    widgets = []
    for w in snap.widgets:
        if w.__struct_config__.tag in ("image", "effect"):
            continue
        d = {k: v for k, v in to_builtins(w).items() if k not in ("anchor", "z", "ttl_s", "on_press") and v is not None}
        if isinstance(w, Label) and w.anchor and w.anchor.point_mm:
            p, off = w.anchor.point_mm, w.anchor.offset_mm
            d["anchor"] = {"x_mm": p.x + (off.x if off else 0.0), "y_mm": p.y + (off.y if off else 0.0)}
        widgets.append(d)
    return {
        "type": "scene",
        "rev": snap.rev,
        "t_send_ms": snap.t_send_ms,
        "surface": to_builtins(snap.surface),
        "homography": snap.homography or projector_matrix(snap.transforms),
        "widgets": widgets,
    }
