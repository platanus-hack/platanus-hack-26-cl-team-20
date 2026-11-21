"""Dibujo aditivo en px del proyector para efectos que iluminan lo real (halo, contorno, relleno, teclas, anillos).

Todo va a una capa nítida y a una capa de brillo a 1/4 de resolución (difuminada y sumada al final): sobre un fondo
negro proyectado, sumar color escalado equivale a mezclar con alfa y cuesta un solo `cv2.add`. Colores en BGR (los
efectos convierten sus params RGB con `bgr()` antes de llamar).
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .effects.base import offset_contour


def scaled(color_bgr, k: float) -> tuple[int, int, int]:
    k = max(0.0, min(1.0, k))
    return tuple(int(c * k) for c in color_bgr)


def mix(a, b, t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(x * (1 - t) + y * t) for x, y in zip(a, b))


def _pts(poly, s: float = 1.0) -> np.ndarray:
    return (np.asarray(poly, float) / s).astype(np.int32).reshape(-1, 1, 2)


class Layers:
    def __init__(self, img: np.ndarray, s: int = 4):
        self.img, self.s = img, s
        h, w = img.shape[:2]
        self.h, self.w = h, w
        self.sharp = np.zeros_like(img)
        self.sh, self.sw = -(-h // s), -(-w // s)
        self.glow = np.zeros((self.sh, self.sw, 3), np.uint8)
        self.glow_used = False

    def composite(self, blur: float = 3.0, gain: float = 2.0) -> None:
        cv2.add(self.img, self.sharp, dst=self.img)
        if self.glow_used:
            g = cv2.convertScaleAbs(cv2.GaussianBlur(self.glow, (0, 0), blur), alpha=gain)
            big = cv2.resize(g, (self.sw * self.s, self.sh * self.s), interpolation=cv2.INTER_LINEAR)[:self.h, :self.w]
            cv2.add(self.img, big, dst=self.img)


def halo(L: Layers, poly, color, e: float, beat: float = 0.0, reach: float = 70.0, layers: int = 4) -> None:
    """Capas concéntricas difuminadas alrededor del polígono; alcance e intensidad con `e` (0..1)."""
    poly = np.asarray(poly, float)
    r = reach * (0.3 + 0.9 * e + 0.5 * beat)
    for k in range(layers, 0, -1):
        inten = (0.25 + 0.75 * e + beat) * (1 - (k - 1) / layers)
        cv2.polylines(L.glow, [_pts(offset_contour(poly, r * k / layers), L.s)], True, scaled(color, inten),
                      max(1, int((10 + 14 * e) / L.s)), cv2.LINE_AA)
    L.glow_used = True
    outline(L, poly, color, e, beat)


def outline(L: Layers, poly, color, e: float, beat: float = 0.0, thick: float = 2.0) -> None:
    cv2.polylines(L.sharp, [_pts(poly)], True, scaled(color, 0.3 + 0.7 * e + beat), max(1, int(thick + 4 * e)),
                  cv2.LINE_AA)
    cv2.polylines(L.glow, [_pts(poly, L.s)], True, scaled(color, 0.6 * e + beat), max(1, int(6 * e)), cv2.LINE_AA)
    L.glow_used = True


def fill(L: Layers, poly, color, e: float, beat: float = 0.0) -> None:
    cv2.fillPoly(L.sharp, [_pts(poly)], scaled(color, 0.8 * e + beat), cv2.LINE_AA)


def keys(L: Layers, polys, color, e: float, beat: float = 0.0, t: float = 0.0, idle: float = 0.07) -> None:
    n = len(polys)
    for i, key in enumerate(polys):
        u = i / max(1, n - 1)
        wave = 0.8 + 0.2 * math.sin(2 * math.pi * (1.5 * u - 0.6 * t))    # leve ola para que respire
        cv2.fillConvexPoly(L.sharp, _pts(key).reshape(-1, 2), scaled(color, max(idle, e * wave) + beat), cv2.LINE_AA)


def rings(L: Layers, circles, color, e: float, ripples=(), speed: float = 520.0, life: float = 1.1,
          width: float = 5.0) -> None:
    """Borde de cada círculo (cx, cy, r) con `e` y anillos que salen hacia afuera: ripples = [(edad_s, fuerza)]."""
    for cx, cy, r in circles:
        cv2.circle(L.sharp, (int(cx), int(cy)), int(r), scaled(color, 0.3 + 0.7 * e), max(1, int(2 + 6 * e)),
                   cv2.LINE_AA)
        for age, s in ripples:
            fade = s * (1 - age / life) ** 1.5
            cv2.circle(L.sharp, (int(cx), int(cy)), int(r + speed * age), scaled(color, fade),
                       max(1, int(width * (0.6 + s))), cv2.LINE_AA)


def ripple_poly(L: Layers, poly, color, ripples=(), speed: float = 520.0, life: float = 1.1, width: float = 5.0) -> None:
    """Ondas que salen de un polígono (su contorno expandido)."""
    for age, s in ripples:
        fade = s * (1 - age / life) ** 1.5
        cv2.polylines(L.sharp, [_pts(offset_contour(np.asarray(poly, float), speed * age))], True,
                      scaled(color, fade), max(1, int(width * (0.6 + s))), cv2.LINE_AA)
