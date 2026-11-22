"""Mezclador entre el vocabulario WUI y los `params` de los efectos.

No reescribe los efectos ni cambia su firma. Expone constructores de defaults para cada
forma canónica (Punto, Línea, Arco, Contorno) y una función `aplicar_wui` que inyecta
color, grosor y ritmo según la forma y el estado del paso. Los `params` propios del efecto
(audio, beat_sync, segmentación) quedan intactos a menos que el llamador los sobreescriba.
"""

from __future__ import annotations

from typing import Any

from .wui_motion import DURACION_TINTA, EstadoPaso, envolvente_estado
from .wui_tokens import (
    ESPEC_POR_FORMA,
    FORMA_ARCO,
    FORMA_CONTORNO,
    FORMA_LINEA,
    FORMA_PUNTO,
    Forma,
    SEÑAL_POR_ESTADO,
)

Params = dict[str, Any]


# --- presets por forma ---------------------------------------------------


def preset_punto(estado: EstadoPaso = "apareciendo") -> Params:
    """Default para efectos de Punto: `touch_marks`, `bass_ripple`, `linterna`."""

    espec = FORMA_PUNTO
    return {
        "color": list(SEÑAL_POR_ESTADO["activa" if estado == "apareciendo" else _mapa_estado(estado)]),
        "radius_mm": espec.radio_mm,
        "glow_mm": espec.halo_mm,
        "speed": 1.0 / DURACION_TINTA,
    }


def preset_linea() -> Params:
    """Default para efectos tipo Línea: pista que conecta objeto con mensaje."""

    espec = FORMA_LINEA
    return {
        "color": list(SEÑAL_POR_ESTADO["guia"]),
        "width_mm": espec.grosor_mm,
        "glow_mm": espec.halo_mm,
        "speed": 1.0 / DURACION_TINTA,
    }


def preset_arco(estado: EstadoPaso = "apareciendo") -> Params:
    """Default para efectos tipo Arco: `control_glow`, `marco_respira`."""

    espec = FORMA_ARCO
    return {
        "color": list(SEÑAL_POR_ESTADO[_mapa_estado(estado)]),
        "radius_mm": espec.radio_mm,
        "width_mm": espec.grosor_mm,
        "glow_mm": espec.halo_mm,
        "fade_s": DURACION_TINTA,
    }


def preset_contorno(estado: EstadoPaso = "apareciendo") -> Params:
    """Default para efectos tipo Contorno: `contour_glow`, `touch_marks` sobre objeto."""

    espec = FORMA_CONTORNO
    return {
        "color": list(SEÑAL_POR_ESTADO[_mapa_estado(estado)]),
        "width_mm": espec.grosor_mm * 20.0,  # width_mm de contour_glow mide el alcance del halo, no el trazo
        "layers": 4,
        "speed": 1.0 / DURACION_TINTA,
        "beat_sync": False,
    }


PRESETS: dict[str, Any] = {
    "punto": preset_punto,
    "linea": preset_linea,
    "arco": preset_arco,
    "contorno": preset_contorno,
}


# --- aplicación ----------------------------------------------------------


def aplicar_wui(
    params_efecto: Params,
    forma: Forma,
    estado: EstadoPaso = "apareciendo",
    *,
    override: Params | None = None,
) -> Params:
    """Devuelve un dict nuevo con los defaults del preset mezclados con los del efecto.

    Orden de prioridad (de menor a mayor):
    1. `params` propios del efecto (los que define su clase).
    2. preset de la forma solicitada.
    3. `override` explícito del llamador.

    Nunca muta `params_efecto`. Si una clave del preset no existe en el efecto, se
    descarta: así un `preset_contorno` no inyecta `radius_mm` en un efecto de Contorno
    que no lo entiende.
    """

    preset = PRESETS[forma](estado) if forma in ("punto", "arco", "contorno") else PRESETS[forma]()
    mezcla: Params = dict(params_efecto)
    for k, v in preset.items():
        if k in params_efecto:
            mezcla[k] = v
    if override:
        for k, v in override.items():
            if k in mezcla or k in params_efecto:
                mezcla[k] = v
    return mezcla


def amplitud_estado(t: float, estado: EstadoPaso) -> float:
    """Reexporta la envolvente de `wui_motion` para que un efecto no importe los dos módulos."""

    return envolvente_estado(t, estado)


# --- compatibilidad declarativa ------------------------------------------
#
# Lista inicial de efectos que ya tienen defaults coherentes con una forma canónica. Solo se
# incluyen efectos cuya reinterpretación con tokens WUI no rompe su función. Crece a medida
# que mapeamos más efectos.
COMPATIBILIDAD: dict[str, tuple[Forma, ...]] = {
    "contour_glow": ("contorno", "linea"),
    "control_glow": ("arco",),
    "marco_respira": ("contorno",),
    "touch_marks": ("punto", "contorno"),
    "bass_ripple": ("punto",),
    "linterna": ("punto",),
}


def _mapa_estado(estado: EstadoPaso) -> str:
    """Traduce estado de paso a nombre semántico de token."""

    return {
        "apareciendo": "guia",
        "activo": "activa",
        "recuperacion": "recuperacion",
        "completado": "resultado",
    }[estado]
