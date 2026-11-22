"""El canasto: la zona con textura de mimbre más grande de la mesa.

El color no sirve (la mesa también es amarillenta y la luz de la ventana lava medio canasto), pero la textura sí: el
mimbre tiene agujeros cada 5 mm y la mesa es lisa. Se toma la desviación local de luminancia, se cierra para que el
canasto quede como una sola mancha (las bolas blancas adentro quedan cubiertas por la envolvente convexa) y gana la
mancha más grande con forma de rectángulo.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np


@dataclass
class Canasto:
    centro_mm: tuple[float, float]
    tam_mm: tuple[float, float]
    angulo_deg: float  # de cv2.minAreaRect: el canasto es casi cuadrado, así que el giro vale módulo 90°
    poligono_mm: list[tuple[float, float]]  # envolvente convexa

    def dict(self) -> dict:
        return asdict(self)

    def zona(self, margen_mm: float = 60) -> list[float]:
        """Rectángulo [x, y, w, h] que contiene el canasto más un margen (las bolas que se caen quedan al lado)."""
        xs = [p[0] for p in self.poligono_mm]
        ys = [p[1] for p in self.poligono_mm]
        return [min(xs) - margen_mm, min(ys) - margen_mm, max(xs) - min(xs) + 2 * margen_mm, max(ys) - min(ys) + 2 * margen_mm]


def ubicar_canasto(img_mm: np.ndarray, area_min_mm2: float = 25000) -> Canasto | None:
    lum = cv2.cvtColor(img_mm, cv2.COLOR_BGR2LAB)[..., 0].astype(np.float32)
    mu = cv2.blur(lum, (7, 7))
    sd = np.sqrt(np.maximum(cv2.blur(lum * lum, (7, 7)) - mu * mu, 0))
    m = (sd > 18).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    mejor, mejor_a = None, 0.0
    for c in cnts:
        hull = cv2.convexHull(c)
        a = cv2.contourArea(hull)
        if a < area_min_mm2:
            continue
        (cx, cy), (w, h), ang = cv2.minAreaRect(hull)
        rect = w * h
        if rect <= 0 or a / rect < 0.75 or max(w, h) / max(min(w, h), 1) > 1.8:
            continue
        if a > mejor_a:
            mejor, mejor_a = (hull, (cx, cy), (w, h), ang), a
    if mejor is None:
        return None
    hull, c, tam, ang = mejor
    return Canasto(
        (round(c[0], 1), round(c[1], 1)),
        (round(tam[0], 1), round(tam[1], 1)),
        round(float(ang), 1),
        [(float(p[0][0]), float(p[0][1])) for p in hull],
    )
