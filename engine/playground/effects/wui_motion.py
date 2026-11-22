"""Curvas y tiempos del lenguaje de interacción WUI.

Los tiempos derivan de la lámina 09 (La tinta toma forma) y se aplican tanto a la apertura
del logo como al revelado de una pista durante una tarea. La función `ease_tinta_asienta`
reproduce el `cubic-bezier(0.16, 1, 0.3, 1)` del brandbook. `ondulacion_trazo` entrega el
desplazamiento vertical acotado al ancho del wordmark.
"""

from __future__ import annotations

import math
from typing import Literal

from .wui_tokens import ANCHO_LINEA_WORDMARK_MM

# --- tiempos canónicos ---------------------------------------------------

TIEMPOS_TINTA: dict[str, float] = {
    "entra": 0.42,
    "ondula": 0.88,
    "forma": 1.28,
    "asienta": 1.72,
}

DURACION_TINTA: float = TIEMPOS_TINTA["asienta"]


EstadoPaso = Literal["apareciendo", "activo", "recuperacion", "completado"]


# --- easing ---------------------------------------------------------------


def _bezier(p1x: float, p1y: float, p2x: float, p2y: float, t: float, iter: int = 6) -> float:
    """Aprox. iterativa de un cubic-bezier con puntos de control (p1, p2) y extremos (0,0)-(1,1)."""

    t = max(0.0, min(1.0, t))
    u = t
    for _ in range(iter):
        x = 3 * (1 - u) ** 2 * u * p1x + 3 * (1 - u) * u ** 2 * p2x + u ** 3
        dx = 3 * (1 - u) ** 2 * (p1x) + 6 * (1 - u) * u * (p2x - p1x) + 3 * u ** 2 * (1 - p2x)
        if dx == 0:
            break
        u -= (x - t) / dx
        u = max(0.0, min(1.0, u))
    return 3 * (1 - u) ** 2 * u * p1y + 3 * (1 - u) * u ** 2 * p2y + u ** 3


def ease_tinta_asienta(t: float) -> float:
    """Curva del asentamiento de la tinta: `cubic-bezier(0.16, 1, 0.3, 1)` en [0, 1]."""

    return _bezier(0.16, 1.0, 0.3, 1.0, t)


def ease_tinta_entra(t: float) -> float:
    """Curva de entrada del trazo: arranque suave, final medio."""

    return _bezier(0.33, 0.0, 0.67, 1.0, t)


# --- envolvente por estado -----------------------------------------------


def envolvente_estado(t: float, estado: EstadoPaso) -> float:
    """Amplitud 0..1 que modula el halo según el estado del paso.

    - apareciendo: sube con `ease_tinta_asienta` hasta el final de `TIEMPOS_TINTA["asienta"]`.
    - activo: respiración lenta entre 0.75 y 1.0 para mantener presencia sin distraer.
    - recuperacion: pulso corto de 0.8 s entre 0.25 y 0.9.
    - completado: baja de 1.0 a 0.0 en el tiempo de `asienta` y se queda apagado.
    """

    if estado == "apareciendo":
        return ease_tinta_asienta(min(1.0, t / DURACION_TINTA))
    if estado == "activo":
        return 0.75 + 0.25 * 0.5 * (1 + math.sin(2 * math.pi * 0.35 * t))
    if estado == "recuperacion":
        return 0.25 + 0.65 * 0.5 * (1 + math.sin(2 * math.pi * 1.25 * t))
    if estado == "completado":
        return max(0.0, 1.0 - ease_tinta_asienta(min(1.0, t / DURACION_TINTA)))
    return 0.0


# --- ondulación del trazo ------------------------------------------------


def ondulacion_trazo(
    t: float,
    largo_mm: float,
    *,
    crestas: int = 2,
    amplitud_mm: float | None = None,
    duracion: float = DURACION_TINTA,
) -> list[float]:
    """Desplazamientos verticales (mm) a lo largo del trazo, uno cada mm.

    Durante `duracion` la onda recorre el trazo de izquierda a derecha y se amortigua. La
    amplitud se acota a `ANCHO_LINEA_WORDMARK_MM` por defecto para no competir con la
    lectura; el llamador puede subirla si el paso lo pide.
    """

    if largo_mm <= 0:
        return []
    amplitud = amplitud_mm if amplitud_mm is not None else ANCHO_LINEA_WORDMARK_MM
    # fase global que avanza con t y se apaga al asentar
    fase_t = max(0.0, min(1.0, t / duracion))
    envolvente = 1.0 - ease_tinta_asienta(fase_t)
    desp: list[float] = []
    n = int(largo_mm) + 1
    for i in range(n):
        u = i / max(1, n - 1)
        # la onda viaja: cresta se mueve con fase_t
        onda = math.sin(2 * math.pi * crestas * u - 2 * math.pi * fase_t)
        desp.append(amplitud * envolvente * onda)
    return desp
