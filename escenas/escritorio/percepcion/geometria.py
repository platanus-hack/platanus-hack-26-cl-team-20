"""Geometría compartida: esquinas, homografías, coordenadas de la libreta y conversión píxel → mm.

Convenciones:
- Un cuadrilátero son 4 puntos [x, y] en orden: arriba-izquierda, arriba-derecha, abajo-derecha, abajo-izquierda,
  tal como los lee la persona sentada frente a la libreta.
- Las coordenadas de la libreta van de 0 a 1 en cada eje (u a lo ancho, v a lo alto). Sirven igual en la cámara
  secundaria, en la cenital y en la mesa en mm: es el marco común entre las dos cámaras.
- Un rectángulo normalizado es [x, y, ancho, alto] en esas coordenadas.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

CUADRADO = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])


def ordenar_esquinas(puntos) -> np.ndarray:
    """Ordena 4 puntos como arriba-izq, arriba-der, abajo-der, abajo-izq según su posición en la imagen."""
    p = np.asarray(puntos, dtype=np.float32).reshape(4, 2)
    centro = p.mean(axis=0)
    ang = np.arctan2(p[:, 1] - centro[1], p[:, 0] - centro[0])
    p = p[np.argsort(ang)]  # sentido horario en coordenadas de imagen (y hacia abajo)
    # que empiece por el de menor x + y (arriba a la izquierda)
    k = int(np.argmin(p.sum(axis=1)))
    return np.roll(p, -k, axis=0)


def girar_esquinas(quad, cuartos: int) -> np.ndarray:
    """Gira el orden de las esquinas en cuartos de vuelta (para cámaras que ven la libreta de lado o al revés)."""
    return np.roll(np.asarray(quad, dtype=np.float32).reshape(4, 2), -int(cuartos) % 4, axis=0)


def homografia(quad) -> np.ndarray:
    """Matriz que lleva coordenadas de la libreta (0..1) a los puntos del cuadrilátero."""
    return cv2.getPerspectiveTransform(CUADRADO, np.asarray(quad, dtype=np.float32).reshape(4, 2))


def aplicar(H, puntos) -> np.ndarray:
    p = np.asarray(puntos, dtype=np.float64).reshape(-1, 2)
    q = np.c_[p, np.ones(len(p))] @ np.asarray(H, dtype=np.float64).reshape(3, 3).T
    return q[:, :2] / q[:, 2:3]


def rect_a_quad(rect) -> np.ndarray:
    x, y, w, h = rect
    return np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])


def rect_en(quad, rect) -> np.ndarray:
    """Las 4 esquinas de un rectángulo normalizado de la libreta, dentro de `quad` (píxeles o mm)."""
    return aplicar(homografia(quad), rect_a_quad(rect))


def lados(quad) -> tuple[float, float]:
    """Ancho y alto medios del cuadrilátero."""
    q = np.asarray(quad, dtype=np.float64).reshape(4, 2)
    ancho = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    alto = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    return float(ancho), float(alto)


def caja_rotada(quad) -> dict:
    """Rectángulo girado que mejor cubre un cuadrilátero en mm: lo que necesita un widget html (rect_mm + rotate_deg).

    El nodo gira el iframe alrededor del centro de rect_mm, así que basta el centro, el tamaño y el ángulo del lado de
    arriba.
    """
    q = np.asarray(quad, dtype=np.float64).reshape(4, 2)
    ancho, alto = lados(q)
    centro = q.mean(axis=0)
    arriba = (q[1] - q[0]) + (q[2] - q[3])
    angulo = math.degrees(math.atan2(arriba[1], arriba[0]))
    return {
        "rect_mm": [round(centro[0] - ancho / 2, 1), round(centro[1] - alto / 2, 1), round(ancho, 1), round(alto, 1)],
        "rotate_deg": round(angulo, 2),
        "centro_mm": [round(centro[0], 1), round(centro[1], 1)],
    }


# ------------------------------------------------------------------ calibración de la consola (port de calib.js)


def _sin_distorsion(lente: dict, p) -> tuple[float, float]:
    xd = (p[0] - lente["cx"]) / lente["f"]
    yd = (p[1] - lente["cy"]) / lente["f"]
    x, y = xd, yd
    for _ in range(12):
        r2 = x * x + y * y
        k = 1 + lente["k1"] * r2 + lente["k2"] * r2 * r2
        x, y = xd / k, yd / k
    return x * lente["f"] + lente["cx"], y * lente["f"] + lente["cy"]


def px_a_mm(calib: dict, puntos, image_px=None) -> np.ndarray:
    """Píxeles de una foto de `look` → mm de la mesa, igual que `pxToMm` de la consola.

    `calib` es lo que devuelve `look` calibrado: {matrix_px_to_mm, image_px, lens?}. Si la foto tiene otro tamaño que
    la calibración, se escala como lo hace el nodo.
    """
    M = np.asarray(calib.get("matrix_px_to_mm") or calib["matrix"], dtype=np.float64).reshape(3, 3)
    lente = calib.get("lens")
    base = calib.get("image_px")
    out = []
    for p in np.asarray(puntos, dtype=np.float64).reshape(-1, 2):
        if image_px and base and tuple(image_px) != tuple(base):
            p = (p[0] * base[0] / image_px[0], p[1] * base[1] / image_px[1])
        if lente:
            p = _sin_distorsion(lente, p)
        out.append(aplicar(M, [p])[0])
    return np.asarray(out)
