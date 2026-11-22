"""De la foto de la cámara a la vista cenital de la mesa, 1 px = 1 mm."""

from __future__ import annotations

import cv2
import numpy as np


def _sin_lente(px: np.ndarray, lens: dict | None) -> np.ndarray:
    """Quita la distorsión radial (modelo k1, k2 de la consola) a puntos en píxeles de la foto."""
    if not lens:
        return px
    f, cx, cy = lens["f"], lens["cx"], lens["cy"]
    k = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]], dtype=np.float64)
    d = np.array([lens.get("k1", 0.0), lens.get("k2", 0.0), 0, 0], dtype=np.float64)
    out = cv2.undistortPoints(px.reshape(-1, 1, 2).astype(np.float64), k, d, P=k)
    return out.reshape(-1, 2)


def px_a_mm(px, matrix_px_to_mm, lens: dict | None = None) -> np.ndarray:
    """Puntos de la foto (N×2) a mm de la mesa con la homografía que devuelve `look` (9 números, fila por fila)."""
    h = np.asarray(matrix_px_to_mm, dtype=np.float64).reshape(3, 3)
    p = _sin_lente(np.asarray(px, dtype=np.float64).reshape(-1, 2), lens)
    q = cv2.perspectiveTransform(p.reshape(-1, 1, 2), h)
    return q.reshape(-1, 2)


def rectificar(img: np.ndarray, matrix_px_to_mm, surface_mm=(1000, 563), lens: dict | None = None) -> np.ndarray:
    """Vista cenital en mm (ancho×alto de la superficie), lista para `detectar_cuerpos` y `ubicar_laminas`.

    `matrix_px_to_mm` y `lens` vienen tal cual en la respuesta de `look` (calibrado). Si hay lente, primero se quita la
    distorsión de la foto completa y después se aplica la homografía, igual que hace la consola.
    """
    h = np.asarray(matrix_px_to_mm, dtype=np.float64).reshape(3, 3)
    w_mm, h_mm = int(round(surface_mm[0])), int(round(surface_mm[1]))
    if lens:
        f, cx, cy = lens["f"], lens["cx"], lens["cy"]
        k = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]], dtype=np.float64)
        d = np.array([lens.get("k1", 0.0), lens.get("k2", 0.0), 0, 0], dtype=np.float64)
        img = cv2.undistort(img, k, d)
    return cv2.warpPerspective(img, h, (w_mm, h_mm), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
