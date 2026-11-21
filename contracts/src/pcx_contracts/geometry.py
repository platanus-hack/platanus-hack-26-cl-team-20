"""Geometría. Marco canónico `table_mm`: x a la derecha, y hacia abajo, origen arriba a la izquierda, en mm.

Puntos y rectángulos viajan como arrays (`[x, y]`, `[x, y, w, h]`), igual que en PROTOCOLO-ESCENA v0.
Marcos con nombre: `table_mm`, `cam:<id>` (px de cámara), `proj:<id>` (px de salida del proyector).
"""

from __future__ import annotations

from typing import Annotated, Literal

from msgspec import Meta, Struct

TABLE_MM = "table_mm"


class PointMM(Struct, array_like=True):
    """Punto en mm del marco `table_mm`. En el cable: `[x, y]`."""

    x: float
    y: float


class PointPX(Struct, array_like=True):
    """Punto en px de una imagen (cámara o proyector). En el cable: `[x, y]`."""

    x: float
    y: float


class RectMM(Struct, array_like=True):
    """Rectángulo alineado a ejes en mm. En el cable: `[x, y, w, h]` (x, y = esquina superior izquierda)."""

    x: float
    y: float
    w: float
    h: float


PolygonMM = Annotated[list[PointMM], Meta(min_length=3, description="Polígono cerrado en mm: [[x,y],...]")]


class Surface(Struct, kw_only=True):
    """Superficie útil de la mesa en mm."""

    width_mm: float
    height_mm: float


TransformKind = Literal["homography", "lut"]


class Transform(Struct, kw_only=True):
    """Transformación con nombre y versión entre dos marcos.

    `homography`: `matrix` = 9 floats en fila mayor (lleva puntos de `from_frame` a `to_frame`).
    `lut`: `lut_ref` apunta a la LUT (p. ej. `shm:lut/proj:main/v3` o una ruta de asset); `matrix` es opcional
    (aproximación). `residual_mm`: error medido en la última verificación.
    """

    from_frame: str
    to_frame: str
    kind: TransformKind = "homography"
    matrix: Annotated[list[float], Meta(min_length=9, max_length=9)] | None = None
    lut_ref: str | None = None
    version: int = 1
    residual_mm: float | None = None
