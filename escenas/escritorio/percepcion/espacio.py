"""Dónde puede escribir el agente: un hueco sin tinta en la página, cerca de lo que escribió la persona."""

from __future__ import annotations

import cv2
import numpy as np


def ocupacion(tinta: np.ndarray, *, celda: float = 0.02, holgura: float = 0.025, ocupado=(), margen: float = 0.05) -> np.ndarray:
    """Grilla de la página (True = ocupado): tinta con holgura, márgenes del papel y rectángulos ya usados."""
    alto, ancho = tinta.shape
    paso = max(4, round(celda * max(alto, ancho)))
    filas, cols = int(np.ceil(alto / paso)), int(np.ceil(ancho / paso))
    h = max(1, round(holgura * max(alto, ancho)))
    con_holgura = cv2.dilate(tinta, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * h + 1, 2 * h + 1)))
    grande = np.zeros((filas * paso, cols * paso), np.uint8)
    grande[:alto, :ancho] = con_holgura
    grilla = grande.reshape(filas, paso, cols, paso).max(axis=(1, 3)) > 0
    # lo que cae fuera de la página (relleno) y los márgenes del papel también cuentan como ocupado
    grilla[:, int(ancho // paso) :] |= ancho % paso != 0
    grilla[int(alto // paso) :, :] |= alto % paso != 0
    mx, my = int(np.ceil(margen * cols)), int(np.ceil(margen * filas))
    grilla[:my], grilla[filas - my :] = True, True
    grilla[:, :mx], grilla[:, cols - mx :] = True, True
    for x, y, w, hh in ocupado:
        grilla[int(y * filas) : int(np.ceil((y + hh) * filas)), int(x * cols) : int(np.ceil((x + w) * cols))] = True
    return grilla


def espacio_libre(
    tinta: np.ndarray,
    tamano=(0.6, 0.18),
    *,
    cerca_de=None,
    ocupado=(),
    achicar_hasta: float = 0.6,
    celda: float = 0.02,
) -> dict | None:
    """El mejor rectángulo libre de `tamano` (ancho, alto normalizados) para la respuesta del agente.

    - `cerca_de`: caja [x, y, w, h] de lo que se escribió; se prefiere justo debajo, después a la derecha, después
      arriba. Sin ella, lo más arriba a la izquierda posible (como quien sigue escribiendo).
    - `ocupado`: rectángulos donde ya hay respuestas proyectadas, para no escribir encima.
    - Si no cabe, achica el tamaño de a 10 % hasta `achicar_hasta`; si igual no cabe, devuelve None.

    Devuelve {rect: [x, y, w, h], escala, lado} en coordenadas de la página (0..1).
    """
    grilla = ocupacion(tinta, celda=celda, ocupado=ocupado)
    filas, cols = grilla.shape
    integral = cv2.integral(grilla.astype(np.uint8)).astype(np.int64)
    escala = 1.0
    while escala >= achicar_hasta - 1e-9:
        wc = max(1, round(tamano[0] * escala * cols))
        hc = max(1, round(tamano[1] * escala * filas))
        if wc <= cols and hc <= filas:
            # suma de ocupados de cada ventana wc×hc con la integral: 0 = cabe entera
            s = integral[hc:, wc:] - integral[:-hc, wc:] - integral[hc:, :-wc] + integral[:-hc, :-wc]
            ys, xs = np.nonzero(s == 0)
            if len(ys):
                rx, ry = xs / cols, ys / filas
                rw, rh = wc / cols, hc / filas
                costo, lado = _costo(rx, ry, rw, rh, cerca_de)
                i = int(np.argmin(costo))
                return {
                    "rect": [round(float(rx[i]), 4), round(float(ry[i]), 4), round(rw, 4), round(rh, 4)],
                    "escala": round(escala, 2),
                    "lado": str(lado[i]),
                }
        escala -= 0.1
    return None


def _costo(rx, ry, rw, rh, cerca_de):
    if cerca_de is None:
        return ry * 2 + rx, np.array(["arriba"] * len(rx))
    x, y, w, h = cerca_de
    # punto ideal de cada lado y su castigo: debajo y alineado a la izquierda es como seguir la conversación
    opciones = {
        "debajo": ((x, y + h + 0.02), 0.0),
        "derecha": ((x + w + 0.03, y), 0.12),
        "arriba": ((x, y - rh - 0.02), 0.25),
    }
    costos, lados = [], []
    for nombre, ((px, py), castigo) in opciones.items():
        costos.append(np.hypot(rx - px, (ry - py) * 1.3) + castigo)
        lados.append(nombre)
    costos = np.stack(costos)
    k = np.argmin(costos, axis=0)
    return costos[k, np.arange(len(rx))], np.array(lados)[k]
