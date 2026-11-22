"""Percepción de la escena Escritorio: libreta, tinta nueva y espacio libre. Funciones puras (imagen → resultado).

Uso típico, una lectura de la cámara secundaria:

    from percepcion import ubicar_libreta, rectificar, recorte_pagina, mascara_tinta, lo_nuevo, espacio_libre
    lib = ubicar_libreta(foto)                       # dónde está y cuántas páginas muestra
    hoja = rectificar(foto, lib["quad_px"])          # la libreta de frente
    tinta = mascara_tinta(recorte_pagina(hoja, lib["paginas"][-1]))
    nuevo = lo_nuevo(tinta_anterior, tinta)          # qué se escribió desde la última lectura
    hueco = espacio_libre(tinta, (0.6, 0.18), cerca_de=nuevo["caja"])

`Lector` (en lector.py) junta todo eso y recuerda la lectura anterior.
"""

from .espacio import espacio_libre, ocupacion
from .geometria import caja_rotada, girar_esquinas, homografia, ordenar_esquinas, px_a_mm, rect_en
from .libreta import pagina_activa, paginas, recorte_pagina, rectificar, ubicar_libreta
from .lector import Lector
from .tinta import hay_mano, lo_nuevo, marcar, mascara_tinta, movimiento, recorte

__all__ = [
    "Lector",
    "caja_rotada",
    "espacio_libre",
    "girar_esquinas",
    "hay_mano",
    "homografia",
    "lo_nuevo",
    "marcar",
    "mascara_tinta",
    "movimiento",
    "ocupacion",
    "ordenar_esquinas",
    "pagina_activa",
    "paginas",
    "px_a_mm",
    "recorte",
    "recorte_pagina",
    "rect_en",
    "rectificar",
    "ubicar_libreta",
]
