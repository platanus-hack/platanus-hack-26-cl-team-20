"""Objetos ajenos en la mesa del laboratorio («objeto contaminado»), sobre todo un celular.

Compara la vista cenital rectificada de ahora con una de referencia tomada al partir (la mesa preparada). Lo que cambió,
fuera de las zonas que se ignoran (lo que proyecta la escena, la gradilla, el vaso), y tiene tamaño de objeto, es un
intruso. Si además es un rectángulo lleno del tamaño de un teléfono, es un «celular».

No necesita foto limpia: el proyector solo agrega luz, nunca oscurece. Por eso (modo por defecto) solo cuenta lo que
quedó más oscuro que la referencia, como un celular sobre la mesa beige; lo que la escena proyecta encima no molesta.
Un objeto claro (un celular blanco boca abajo) no se ve en ese modo.
Funciones puras: numpy + OpenCV.
"""

from __future__ import annotations

import cv2
import numpy as np

# Un teléfono visto desde arriba: ~65–90 mm de ancho y ~130–175 mm de largo (con funda, un poco más).
CELULAR_CORTO_MM = (45, 100)
CELULAR_LARGO_MM = (105, 195)


def _lab(img):
    return cv2.cvtColor(cv2.GaussianBlur(img, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.float32)


def detectar(
    ref_mm: np.ndarray,
    actual_mm: np.ndarray,
    ignorar_rects_mm=(),
    ignorar_poligonos_mm=(),
    mm_por_px: float = 1.0,
    umbral: float = 28.0,
    solo_mas_oscuro: bool = True,
    area_min_mm2: float = 2500.0,
    area_max_mm2: float = 60000.0,
) -> list[dict]:
    """Intrusos: [{tipo: «celular»|«objeto», poligono_mm, centro_mm, area_mm2, toca_borde}]."""
    if ref_mm is None or actual_mm is None or ref_mm.shape != actual_mm.shape:
        return []
    esc = 1.0 / mm_por_px
    a, b = _lab(ref_mm), _lab(actual_mm)
    if solo_mas_oscuro:
        m = ((a[..., 0] - b[..., 0]) > umbral).astype(np.uint8)  # L de OpenCV (0–255): cuánto más oscuro quedó
    else:
        m = (np.linalg.norm(a - b, axis=2) > umbral).astype(np.uint8)
    for x, y, w, h in ignorar_rects_mm:
        x0, y0 = max(0, int((x - 10) * esc)), max(0, int((y - 10) * esc))
        m[y0 : int((y + h + 10) * esc), x0 : int((x + w + 10) * esc)] = 0
    for poly in ignorar_poligonos_mm:
        cv2.fillPoly(m, [np.int32(np.asarray(poly, float) * esc)], 0)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    alto, ancho = m.shape
    out = []
    for i in range(1, n):
        area = st[i, cv2.CC_STAT_AREA] / esc**2
        if not area_min_mm2 <= area <= area_max_mm2:
            continue
        x, y, w, h = st[i, :4]
        toca_borde = x <= 2 or y <= 2 or x + w >= ancho - 2 or y + h >= alto - 2
        pts = np.column_stack(np.where(lab == i))[:, ::-1].astype(np.float32)
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(pts)
        largo, corto = max(rw, rh) * mm_por_px, min(rw, rh) * mm_por_px
        lleno = area / max(1.0, largo * corto)
        es_celular = (
            not toca_borde
            and CELULAR_CORTO_MM[0] <= corto <= CELULAR_CORTO_MM[1]
            and CELULAR_LARGO_MM[0] <= largo <= CELULAR_LARGO_MM[1]
            and lleno >= 0.75
        )
        caja = cv2.boxPoints(((cx, cy), (rw, rh), ang)) * mm_por_px
        out.append(
            {
                "tipo": "celular" if es_celular else "objeto",
                "poligono_mm": [[round(float(a), 1), round(float(b), 1)] for a, b in caja],
                "centro_mm": [round(float(cx * mm_por_px), 1), round(float(cy * mm_por_px), 1)],
                "area_mm2": round(float(area)),
                "toca_borde": bool(toca_borde),
            }
        )
    return sorted(out, key=lambda o: -o["area_mm2"])


def poner_celular(img_mm: np.ndarray, centro_mm, angulo_deg: float = 20, tam_mm=(72, 150), semilla: int = 0) -> np.ndarray:
    """Para pruebas y para la demo simulada: un celular oscuro (pantalla negra con marco) sobre la vista cenital."""
    rng = np.random.default_rng(semilla)
    out = img_mm.copy()
    rect = (tuple(map(float, centro_mm)), (float(tam_mm[0]), float(tam_mm[1])), float(angulo_deg))
    caja = np.int32(cv2.boxPoints(rect))
    sombra = np.int32(cv2.boxPoints(((rect[0][0] + 4, rect[0][1] + 5), rect[1], rect[2])))
    cv2.fillPoly(out, [sombra], (70, 75, 80))
    cv2.fillPoly(out, [caja], (38, 36, 40))
    pantalla = np.int32(cv2.boxPoints((rect[0], (tam_mm[0] - 8, tam_mm[1] - 14), rect[2])))
    cv2.fillPoly(out, [pantalla], (18, 18, 22))
    ruido = rng.normal(0, 3, out.shape)
    return np.clip(out.astype(np.float32) + ruido, 0, 255).astype(np.uint8)
