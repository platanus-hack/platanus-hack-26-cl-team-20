"""Fotos reales de la mesa (21-nov, 14:23): vista cenital rectificada a 1 px = 1 mm, en `data/mesa/2026-11-21/`."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

RAIZ_ESCENA = Path(__file__).resolve().parents[2]
REPO = RAIZ_ESCENA.parents[1]
FOTOS = REPO / "data" / "mesa" / "2026-11-21"
sys.path.insert(0, str(RAIZ_ESCENA))

# Medido a mano sobre mesa-cenital-mm-1.jpg (centro x, y y radio, en mm). El engranaje es el Sol.
ENGRANAJE = (386, 127, 54)
BOLAS = [
    (292, 236, 47),
    (211, 287, 38),
    (214, 210, 34),
    (164, 331, 23),
    (213, 131, 18),
    (318, 142, 15),
    (290, 353, 15),
    (232, 154, 14),
    (355, 231, 13),
]


def foto(n: int) -> np.ndarray:
    img = cv2.imread(str(FOTOS / f"mesa-cenital-mm-{n}.jpg"))
    assert img is not None, f"falta la foto {n} en {FOTOS}"
    return img


@pytest.fixture(params=[1, 2, 3, 4], ids=lambda n: f"foto{n}")
def foto_real(request) -> np.ndarray:
    return foto(request.param)


def mover_mesa(img: np.ndarray, theta_deg: float, t_mm: tuple[float, float], centro: tuple[float, float]) -> np.ndarray:
    """Simula mover todo lo que hay en la foto: gira `theta_deg` alrededor de `centro` y traslada `t_mm`.
    Lo que queda descubierto se rellena con el color de la mesa."""
    m = cv2.getRotationMatrix2D(centro, -theta_deg, 1.0)  # OpenCV gira antihorario con y hacia arriba
    m[:, 2] += t_mm
    mesa = tuple(int(v) for v in np.median(img.reshape(-1, 3), axis=0))
    return cv2.warpAffine(img, m, (img.shape[1], img.shape[0]), flags=cv2.INTER_LINEAR, borderValue=mesa)
