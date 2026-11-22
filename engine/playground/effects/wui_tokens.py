"""Tokens de la identidad WUI para los efectos proyectados.

El vocabulario visual de WUI ("El mundo es tu interfaz") se expresa en el motor como paleta
monocromática, cuatro formas canónicas (Punto, Línea, Arco, Contorno) y una tipografía única.
Este módulo expone valores constantes para que `wui_preset` los mezcle con los `params` de
cada efecto sin tocar su firma. Los colores salen hacia afuera en RGB 0-255, coherente con la
convención del resto de `effects/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# --- paleta ---------------------------------------------------------------

Rgb = tuple[int, int, int]

TINTA: Rgb = (0x17, 0x17, 0x17)
GRAFITO: Rgb = (0x24, 0x24, 0x24)
GRIS_MEDIO: Rgb = (0x62, 0x62, 0x60)
NIEBLA: Rgb = (0xF4, 0xF4, 0xF2)
LUZ: Rgb = (0xFF, 0xFF, 0xFF)

# Semántica proyectada. El sistema trabaja sobre fondo negro proyectado; la "señal" suma luz,
# no quita. "latente" da presencia sin reclamar atención; "activa" llama al gesto; "guia"
# acompaña al objeto durante el paso; "resultado" confirma; "recuperacion" aparece cuando
# WUI pierde el objeto y necesita que vuelva.
SEÑAL_LATENTE: Rgb = GRIS_MEDIO
SEÑAL_GUIA: Rgb = NIEBLA
SEÑAL_ACTIVA: Rgb = LUZ
SEÑAL_RESULTADO: Rgb = NIEBLA
SEÑAL_RECUPERACION: Rgb = GRIS_MEDIO

Estado = Literal["latente", "guia", "activa", "resultado", "recuperacion"]
SEÑAL_POR_ESTADO: dict[str, Rgb] = {
    "latente": SEÑAL_LATENTE,
    "guia": SEÑAL_GUIA,
    "activa": SEÑAL_ACTIVA,
    "resultado": SEÑAL_RESULTADO,
    "recuperacion": SEÑAL_RECUPERACION,
}


# --- formas canónicas -----------------------------------------------------

Forma = Literal["punto", "linea", "arco", "contorno"]


@dataclass(frozen=True)
class EspecForma:
    """Valores base para una forma de la lámina 06. Los milímetros son unidades de mesa."""

    radio_mm: float
    grosor_mm: float
    halo_mm: float
    dash: tuple[int, int] | None = None


FORMA_PUNTO = EspecForma(radio_mm=6.0, grosor_mm=0.0, halo_mm=18.0)
FORMA_LINEA = EspecForma(radio_mm=0.0, grosor_mm=1.4, halo_mm=12.0)
FORMA_ARCO = EspecForma(radio_mm=28.0, grosor_mm=1.4, halo_mm=14.0)
FORMA_CONTORNO = EspecForma(radio_mm=0.0, grosor_mm=1.4, halo_mm=20.0, dash=None)

ESPEC_POR_FORMA: dict[str, EspecForma] = {
    "punto": FORMA_PUNTO,
    "linea": FORMA_LINEA,
    "arco": FORMA_ARCO,
    "contorno": FORMA_CONTORNO,
}


# --- tipografía proyectada ------------------------------------------------


@dataclass(frozen=True)
class EspecTipo:
    """Referencias tipográficas en milímetros de mesa.

    El motor hoy no renderiza texto en proyección directa; estas especificaciones viven
    como contrato para cuando un efecto pinte instrucciones sobre la mesa y para que el
    dashboard del operador use los mismos valores que la lámina 05.
    """

    familia: str
    peso: int
    tamano_mm: float
    interlineado_mm: float


TIPO_INSTRUCCION = EspecTipo(familia="Instrument Sans", peso=400, tamano_mm=18.0, interlineado_mm=23.0)
TIPO_CONFIRMACION = EspecTipo(familia="Instrument Sans", peso=600, tamano_mm=12.0, interlineado_mm=15.0)
TIPO_ETIQUETA = EspecTipo(familia="Instrument Sans", peso=600, tamano_mm=9.0, interlineado_mm=11.0)


# --- ancho de línea del wordmark -----------------------------------------
# Referencia para que la ondulación del trazo en la pista no supere el grosor del logotipo.
ANCHO_LINEA_WORDMARK_MM: float = 2.6


def es_token_marca(rgb: Rgb) -> bool:
    """Devuelve True si un color pertenece a la paleta monocromática de la marca."""

    return rgb in (TINTA, GRAFITO, GRIS_MEDIO, NIEBLA, LUZ)
