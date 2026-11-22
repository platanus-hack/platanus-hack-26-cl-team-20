"""Utilidades de la mesa: dónde cabe algo sin tapar objetos, y si algo se está moviendo."""

from __future__ import annotations

import cv2
import numpy as np


def ocupado(img_mm: np.ndarray, margen_mm: int = 12) -> np.ndarray:
    """Máscara de lo que no es mesa lisa: bordes (textura) o colores lejos del de la mesa."""
    gris = cv2.cvtColor(img_mm, cv2.COLOR_BGR2GRAY)
    bordes = cv2.Canny(cv2.GaussianBlur(gris, (5, 5), 0), 40, 110)
    lab = cv2.cvtColor(img_mm, cv2.COLOR_BGR2LAB).astype(np.float32)
    mesa = np.median(lab.reshape(-1, 3), axis=0)
    lejos = np.linalg.norm(lab - mesa, axis=2) > 28
    m = (bordes > 0) | lejos
    m = cv2.dilate(m.astype(np.uint8) * 255, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margen_mm + 1,) * 2))
    return m > 0


def espacio_libre(
    img_mm: np.ndarray,
    tam_mm: tuple[int, int],
    evitar_mm: list[list[float]] | None = None,
    cerca_de_mm: tuple[float, float] | None = None,
    paso: int = 10,
    forzar: bool = False,
) -> list[float] | None:
    """Rectángulo [x, y, w, h] en mm donde cabe `tam_mm` sin pisar objetos ni las zonas `evitar_mm`.

    Si hay varios, elige el más cercano a `cerca_de_mm` (o el más libre). Si no cabe en ninguna parte: None, o con
    `forzar` el lugar menos ocupado (una mesa real rara vez tiene un hueco perfecto para un panel grande).
    """
    occ = ocupado(img_mm).astype(np.uint8)
    prohibido = np.zeros_like(occ)
    for x, y, w, h in evitar_mm or []:  # zonas a evitar: nunca se pisan, ni siquiera con `forzar`
        prohibido[int(max(y, 0)) : int(max(y + h, 0)), int(max(x, 0)) : int(max(x + w, 0))] = 1
    hh, ww = occ.shape
    w, h = tam_mm
    if w > ww or h > hh:
        return None
    integ = cv2.integral(np.maximum(occ, prohibido))
    integ_p = cv2.integral(prohibido)
    mejor, mejor_d = None, float("inf")
    menos, menos_s = None, float("inf")
    for y in range(0, hh - h + 1, paso):
        for x in range(0, ww - w + 1, paso):
            if integ_p[y + h, x + w] - integ_p[y, x + w] - integ_p[y + h, x] + integ_p[y, x] > 0:
                continue
            s = integ[y + h, x + w] - integ[y, x + w] - integ[y + h, x] + integ[y, x]
            if s < menos_s:
                menos, menos_s = [float(x), float(y), float(w), float(h)], s
            if s > 0.01 * w * h:
                continue
            c = (x + w / 2, y + h / 2)
            d = np.hypot(c[0] - cerca_de_mm[0], c[1] - cerca_de_mm[1]) if cerca_de_mm else s
            if d < mejor_d:
                mejor, mejor_d = [float(x), float(y), float(w), float(h)], d
    return mejor if mejor is not None or not forzar else menos


def movimiento(a: np.ndarray, b: np.ndarray, zona_mm=None, umbral: int = 28) -> float:
    """Fracción de píxeles que cambiaron entre dos vistas (0..1). Barato: sirve para saber cuándo mirar de nuevo."""
    if zona_mm is not None:
        x, y, w, h = (int(round(v)) for v in zona_mm)
        a = a[max(y, 0) : y + h, max(x, 0) : x + w]
        b = b[max(y, 0) : y + h, max(x, 0) : x + w]
    ga = cv2.GaussianBlur(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (9, 9), 0)
    gb = cv2.GaussianBlur(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (9, 9), 0)
    return float((cv2.absdiff(ga, gb) > umbral).mean())
