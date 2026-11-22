"""Qué está escrito en la página, qué es nuevo desde la última lectura y si se puede leer ahora (sin mano encima).

Trabaja sobre la libreta ya rectificada (ver `libreta.rectificar`), con el lado largo cerca de 900 px.
"""

from __future__ import annotations

import cv2
import numpy as np


def mascara_tinta(pagina: np.ndarray, *, umbral: float = 0.74, borde: float = 0.025) -> np.ndarray:
    """Píxeles de tinta o lápiz (255) sobre el papel (0).

    Compara el canal más brillante de cada píxel con el papel de alrededor (mediana grande). Así:
    - los renglones celestes y el margen rojo no cuentan: su canal más brillante sigue alto;
    - la luz que proyectamos encima suma brillo, nunca oscurece, y tampoco cuenta;
    - la tinta azul o negra y el lápiz quedan por debajo del umbral.
    """
    v = pagina.max(axis=2) if pagina.ndim == 3 else pagina
    alto, ancho = v.shape
    # papel de alrededor: mediana en una ventana de ~1/7 de la página (a media resolución), más ancha que una palabra
    chica = cv2.resize(v, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    fondo = cv2.medianBlur(chica, min(255, max(15, (min(alto, ancho) // 14) | 1)))
    fondo = cv2.resize(fondo, (ancho, alto), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    razon = v.astype(np.float32) / np.maximum(fondo, 1.0)
    tinta = ((razon < umbral) & (fondo > 60)).astype(np.uint8) * 255
    b = max(1, round(borde * min(alto, ancho)))
    tinta[:b] = tinta[-b:] = 0
    tinta[:, :b] = tinta[:, -b:] = 0
    # fuera las motas sueltas (ruido del sensor, polvo)
    n, etiquetas, stats, _ = cv2.connectedComponentsWithStats(tinta, connectivity=8)
    chicas = np.where(stats[:, cv2.CC_STAT_AREA] < 6)[0]
    if len(chicas) > 1:
        tinta[np.isin(etiquetas, chicas[chicas > 0])] = 0
    return tinta


def _desplazamiento(antes: np.ndarray, ahora: np.ndarray, max_px: int = 24) -> tuple[int, int]:
    """Cuánto se corrió la página entre dos lecturas (la libreta se empuja al escribir)."""
    a = cv2.GaussianBlur(antes.astype(np.float32), (0, 0), 3)
    b = cv2.GaussianBlur(ahora.astype(np.float32), (0, 0), 3)
    if a.sum() < 1 or b.sum() < 1:
        return 0, 0
    (dx, dy), respuesta = cv2.phaseCorrelate(a, b)
    if respuesta < 0.05 or abs(dx) > max_px or abs(dy) > max_px:
        return 0, 0
    return round(dx), round(dy)


def lo_nuevo(antes: np.ndarray | None, ahora: np.ndarray, *, tolerancia_px: int = 5, min_px: int = 60) -> dict:
    """Tinta que está ahora y no estaba en la lectura anterior.

    `antes` y `ahora` son máscaras de `mascara_tinta` del mismo tamaño. Devuelve:
    - `hay_nuevo`: si hay trazo nuevo suficiente para leer;
    - `caja`: rectángulo [x, y, w, h] normalizado (0..1 de la página) que junta todo lo nuevo;
    - `renglones`: una caja por renglón nuevo, de arriba abajo;
    - `pixeles` y `desplazamiento_px` (cuánto se corrió la página).
    """
    alto, ancho = ahora.shape
    dx = dy = 0
    if antes is None:
        nuevo = ahora.copy()
    else:
        dx, dy = _desplazamiento(antes, ahora)
        if dx or dy:
            antes = cv2.warpAffine(antes, np.float32([[1, 0, dx], [0, 1, dy]]), (ancho, alto))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * tolerancia_px + 1, 2 * tolerancia_px + 1))
        nuevo = cv2.bitwise_and(ahora, cv2.bitwise_not(cv2.dilate(antes, k)))
    nuevo = cv2.morphologyEx(nuevo, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    pixeles = int(np.count_nonzero(nuevo))
    vacio = {"hay_nuevo": False, "caja": None, "renglones": [], "pixeles": pixeles, "desplazamiento_px": [dx, dy]}
    if pixeles < min_px:
        return vacio
    # juntar trazos en renglones: se estira harto a lo ancho y poco a lo alto
    kx, ky = max(9, ancho // 22) | 1, max(3, alto // 90) | 1
    unido = cv2.dilate(nuevo, cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(unido, connectivity=8)
    cajas = []
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        dentro = nuevo[y : y + h, x : x + w]
        if np.count_nonzero(dentro) < min_px // 3:
            continue
        ys, xs = np.nonzero(dentro)
        cajas.append([x + xs.min(), y + ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1])
    if not cajas:
        return vacio
    cajas.sort(key=lambda c: (c[1], c[0]))
    x0 = min(c[0] for c in cajas)
    y0 = min(c[1] for c in cajas)
    x1 = max(c[0] + c[2] for c in cajas)
    y1 = max(c[1] + c[3] for c in cajas)
    norm = lambda c: [round(c[0] / ancho, 4), round(c[1] / alto, 4), round(c[2] / ancho, 4), round(c[3] / alto, 4)]  # noqa: E731
    return {
        "hay_nuevo": True,
        "caja": norm([x0, y0, x1 - x0, y1 - y0]),
        "renglones": [norm(c) for c in cajas],
        "pixeles": pixeles,
        "desplazamiento_px": [dx, dy],
    }


def recorte(pagina: np.ndarray, caja, *, margen: float = 0.03) -> np.ndarray:
    """El pedazo de la página que contiene `caja` (normalizada), con un margen, listo para mandárselo al LLM."""
    alto, ancho = pagina.shape[:2]
    x, y, w, h = caja
    x0 = max(0, round((x - margen) * ancho))
    y0 = max(0, round((y - margen) * alto))
    x1 = min(ancho, round((x + w + margen) * ancho))
    y1 = min(alto, round((y + h + margen) * alto))
    return pagina[y0:y1, x0:x1].copy()


def marcar(pagina: np.ndarray, caja, color=(40, 40, 230)) -> np.ndarray:
    """Copia de la página con un recuadro sobre lo nuevo: le da contexto al LLM sin perder qué hay que leer."""
    out = pagina.copy()
    alto, ancho = out.shape[:2]
    x, y, w, h = caja
    cv2.rectangle(out, (round(x * ancho) - 6, round(y * alto) - 6), (round((x + w) * ancho) + 6, round((y + h) * alto) + 6), color, 3)
    return out


def hay_mano(img: np.ndarray, quad=None, *, fraccion: float = 0.015) -> bool:
    """Si hay piel sobre la libreta (o en la foto, sin `quad`). Mientras la mano escribe no se lee."""
    ycc = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    piel = cv2.inRange(ycc, (40, 138, 80), (245, 178, 128))
    if quad is not None:
        zona = np.zeros(piel.shape, np.uint8)
        cv2.fillConvexPoly(zona, np.asarray(quad, dtype=np.int32).reshape(4, 2), 255)
        piel = cv2.bitwise_and(piel, zona)
        total = max(1, int(np.count_nonzero(zona)))
    else:
        total = piel.size
    piel = cv2.morphologyEx(piel, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return np.count_nonzero(piel) / total > fraccion


def movimiento(antes: np.ndarray, ahora: np.ndarray) -> float:
    """Diferencia media entre dos cuadros (0..255), en chico y suavizado. Bajo 4 es una escena quieta."""
    a = cv2.GaussianBlur(cv2.resize(cv2.cvtColor(antes, cv2.COLOR_BGR2GRAY), (160, 90)), (5, 5), 0)
    b = cv2.GaussianBlur(cv2.resize(cv2.cvtColor(ahora, cv2.COLOR_BGR2GRAY), (160, 90)), (5, 5), 0)
    return float(np.mean(cv2.absdiff(a, b)))
