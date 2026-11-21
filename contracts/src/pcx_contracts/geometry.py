"""Geometría. Marco canónico `table_mm`: x a la derecha, y hacia abajo, origen arriba a la izquierda, en mm.

Puntos y rectángulos viajan como arrays de largo exacto (`[x, y]`, `[x, y, w, h]`), igual que en PROTOCOLO-ESCENA v0.
Marcos con nombre: `table_mm`, `cam:<id>` (px de cámara), `proj:<id>` (px de salida del proyector).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from msgspec import Meta, Struct

TABLE_MM = "table_mm"


class PointMM(Struct, array_like=True, forbid_unknown_fields=True):
    """Punto en mm del marco `table_mm`. En el cable: `[x, y]` (exactamente 2 números)."""

    x: float
    y: float


class PointPX(Struct, array_like=True, forbid_unknown_fields=True):
    """Punto en px de una imagen (cámara o proyector). En el cable: `[x, y]` (exactamente 2 números)."""

    x: float
    y: float


class RectMM(Struct, array_like=True, forbid_unknown_fields=True):
    """Rectángulo alineado a ejes en mm. En el cable: `[x, y, w, h]` (x, y = esquina superior izquierda)."""

    x: float
    y: float
    w: float
    h: float


PolygonMM = Annotated[list[PointMM], Meta(min_length=3, description="Polígono cerrado en mm: [[x,y],...] (≥ 3)")]
Matrix9 = Annotated[list[float], Meta(min_length=9, max_length=9,
                                      description="Homografía 3x3 en fila mayor (exactamente 9 números)")]
Sha256 = Annotated[str, Meta(pattern=r"^[0-9a-f]{64}$")]


class Surface(Struct, kw_only=True):
    """Superficie útil de la mesa en mm."""

    width_mm: float
    height_mm: float


class TransformBase(Struct, kw_only=True, tag_field="kind"):
    from_frame: str
    to_frame: str
    version: int = 1
    residual_mm: float | None = None
    """Error medido en la última verificación."""


class HomographyTransform(TransformBase, kw_only=True, tag="homography"):
    """`matrix` lleva puntos de `from_frame` a `to_frame` (coordenadas homogéneas, fila mayor)."""

    matrix: Matrix9


class LutDescriptor(Struct, kw_only=True):
    """LUT densa en un archivo `.npy`.

    - `format`: `npy_f32_hw2` = array float32 de forma `(height, width, 2)`, C-order.
    - Índice: `lut[v, u]` corresponde a la muestra del marco de entrada (`from_frame`) cuyo **centro** está en las
      coordenadas enteras `(u * grid_step, v * grid_step)` (convención `integer_centers`: el centro del px (0,0) es
      (0,0), no (0.5,0.5)). `grid_step` está en unidades de `from_frame` (px, o mm si es `table_mm`).
    - Valor: `(x, y)` en **px de salida** (`to_frame`, que debe ser `proj:*` o `cam:*`). NaN = sin dato.
    - `path`: ruta local o `shm:` del nodo; `sha256`: del archivo `.npy` completo (verificar antes de usar).
    """

    format: Literal["npy_f32_hw2"] = "npy_f32_hw2"
    width: Annotated[int, Meta(ge=1)]
    height: Annotated[int, Meta(ge=1)]
    units: Literal["output_px"] = "output_px"
    sampling: Literal["integer_centers"] = "integer_centers"
    grid_step: Annotated[float, Meta(gt=0)] = 1.0
    path: str
    sha256: Sha256


class LutTransform(TransformBase, kw_only=True, tag="lut"):
    """LUT precisa (gray code). `approx_matrix`: homografía aproximada opcional para consumidores sin LUT."""

    lut: LutDescriptor
    approx_matrix: Matrix9 | None = None


Transform = Union[HomographyTransform, LutTransform]
TransformKind = Literal["homography", "lut"]


def select_projector_transform(transforms: list[Transform], projector_id: str) -> Transform | None:
    """Transformación `table_mm → proj:<projector_id>` que debe usar el renderer.

    Prioridad: LUT > homografía; dentro de cada tipo, la de mayor `version` (empate: la primera de la lista).
    Misma regla que `pcx_contracts::select_projector_transform` (Rust); casos en `fixtures/selection_cases.json`.
    """
    target = f"proj:{projector_id}"
    cands = [t for t in transforms if t.from_frame == TABLE_MM and t.to_frame == target]
    for kind in (LutTransform, HomographyTransform):
        best: Transform | None = None
        for t in cands:
            if isinstance(t, kind) and (best is None or t.version > best.version):
                best = t
        if best is not None:
            return best
    return None


def projector_matrix(transforms: list[Transform], projector_id: str) -> list[float] | None:
    """Homografía para renderers sin LUT: la seleccionada si es homografía; si es LUT, su `approx_matrix`; si esa
    LUT no trae aproximación, la mejor homografía disponible (fallback); si no hay ninguna, None."""
    sel = select_projector_transform(transforms, projector_id)
    if isinstance(sel, HomographyTransform):
        return list(sel.matrix)
    if isinstance(sel, LutTransform):
        if sel.approx_matrix is not None:
            return list(sel.approx_matrix)
        fb = select_projector_transform([t for t in transforms if isinstance(t, HomographyTransform)], projector_id)
        return list(fb.matrix) if fb is not None else None
    return None
