"""Widgets proyectados: unión discriminada por `type`.

Todas las medidas en mm de `table_mm`; colores `#RRGGBB` o `#RRGGBBAA`.
Hacia el agente (comando `project`), la geometría es opcional si hay `anchor.object_id`: el core la resuelve.
Hacia el renderer (`render.SceneSnapshot/SceneDiff`), el core manda la geometría ya resuelta en mm absolutos.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from msgspec import Meta, Struct, field

from .geometry import PointMM, PolygonMM, RectMM

Color = Annotated[str, Meta(pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")]
Trigger = Literal["dwell", "pinch", "shadow", "api"]


class Anchor(Struct, kw_only=True):
    """Dónde se ancla un widget: a un objeto (lo sigue si se mueve) o a un punto fijo, más un desplazamiento."""

    object_id: str | None = None
    point_mm: PointMM | None = None
    offset_mm: PointMM | None = None


class OnPress(Struct, kw_only=True):
    """Hace tocable al widget: el core emite `widget_pressed{widget_id, value}` al confirmarse el toque."""

    value: str | None = None
    triggers: list[Trigger] = field(default_factory=lambda: ["dwell", "pinch", "api"])
    dwell_ms: int = 800


class WidgetBase(Struct, kw_only=True, tag_field="type"):
    id: str
    anchor: Anchor | None = None
    z: int = 0
    ttl_s: float | None = None
    on_press: OnPress | None = None


class Label(WidgetBase, kw_only=True, tag="label"):
    """Texto centrado en `anchor` (admite `\\n` y Unicode)."""

    text: str = ""
    size_mm: float = 12.0
    color: Color = "#FFFFFF"
    bg: Color | None = None


class Halo(WidgetBase, kw_only=True, tag="halo"):
    """Borde luminoso animado. Sin `polygon_mm`, el core usa el polígono del objeto anclado."""

    polygon_mm: PolygonMM | None = None
    color: Color = "#00E5FF"
    width_mm: float = 4.0
    pulse_hz: float = 1.0


class Arrow(WidgetBase, kw_only=True, tag="arrow"):
    from_mm: PointMM | None = None
    to_mm: PointMM | None = None
    color: Color = "#FFD400"
    width_mm: float = 5.0


class Panel(WidgetBase, kw_only=True, tag="panel"):
    """Título + párrafo con ajuste de línea."""

    rect_mm: RectMM | None = None
    title: str = ""
    body: str = ""
    color: Color = "#FFFFFF"


class Button(WidgetBase, kw_only=True, tag="button"):
    """Botón tocable; `progress` (0..1) dibuja el anillo de permanencia."""

    rect_mm: RectMM | None = None
    label: str = ""
    progress: Annotated[float, Meta(ge=0.0, le=1.0)] = 0.0
    color: Color = "#00E676"


class Image(WidgetBase, kw_only=True, tag="image"):
    """Imagen en un rectángulo. `src`: `asset:<id>`, `data:<mime>;base64,...` o `https://...`."""

    rect_mm: RectMM | None = None
    src: str
    opacity: Annotated[float, Meta(ge=0.0, le=1.0)] = 1.0


class Effect(WidgetBase, kw_only=True, tag="effect"):
    """Efecto de shader WGSL por nombre (catálogo del compositor), con parámetros numéricos o vectores."""

    shader: str
    params: dict[str, Any] = {}
    rect_mm: RectMM | None = None
    polygon_mm: PolygonMM | None = None


Widget = Union[Label, Halo, Arrow, Panel, Button, Image, Effect]
WIDGET_TYPES: tuple[type, ...] = (Label, Halo, Arrow, Panel, Button, Image, Effect)
