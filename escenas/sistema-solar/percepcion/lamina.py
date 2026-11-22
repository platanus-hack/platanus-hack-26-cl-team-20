"""La lámina impresa del sistema solar: dónde está, hacia dónde mira y dónde está dibujado cada planeta.

1. La lámina es un rectángulo oscuro (fondo de espacio) de ~17×12 cm sobre una mesa clara: umbral de luminancia,
   componentes, `minAreaRect` y filtro por tamaño y proporción. Hay dos copias en la mesa: salen las dos.
2. Orientación: el Sol está impreso en un extremo del lado largo, grande y amarillo-naranjo. El extremo con más
   amarillo saturado define el eje `u` (hacia el Sol); `v` es `u` girado +90° en la imagen (hacia la franja azul).
3. Los planetas impresos salen de una plantilla en coordenadas de la lámina (u, v entre 0 y 1), medida con la foto
   del 21-nov (`plantilla_lamina.json`). Si cambia la lámina, se vuelve a medir con `medir_plantilla`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np

from .tipos import Lamina

PLANTILLA = Path(__file__).resolve().parent.parent / "plantilla_lamina.json"

PARAMS = {
    "l_max": 95,  # fondo de la lámina: L < 95 (la mesa está sobre 140)
    "largo_mm": (120, 260),
    "proporcion": (1.15, 1.9),
    "relleno_min": 0.6,  # área del contorno / área del rectángulo (0,66 si la corta el borde de lo que ve la cámara)
}


def cargar_plantilla(ruta: str | Path = PLANTILLA) -> dict:
    return json.loads(Path(ruta).read_text(encoding="utf-8"))


def _amarillo(img_mm: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(img_mm, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return ((h >= 8) & (h <= 35) & (s > 110) & (v > 140)).astype(np.uint8)


def ubicar_laminas(img_mm: np.ndarray, params: dict | None = None) -> list[Lamina]:
    """Todas las láminas visibles, de la más segura a la menos."""
    p = {**PARAMS, **(params or {})}
    lum = cv2.cvtColor(img_mm, cv2.COLOR_BGR2LAB)[..., 0]
    m = (cv2.GaussianBlur(lum, (5, 5), 0) < p["l_max"]).astype(np.uint8) * 255
    # Lo que la cámara no ve queda negro puro en la vista en mm: no es lámina (si no, se funde con ella).
    fuera = cv2.dilate((img_mm.max(axis=2) <= 8).astype(np.uint8), np.ones((7, 7), np.uint8))
    m[fuera > 0] = 0
    # Los planetas impresos son claros: se cierran para que la lámina quede como un bloque oscuro.
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    amarillo = _amarillo(img_mm)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    salida = []
    for c in cnts:
        (cx, cy), (w, h), ang = cv2.minAreaRect(c)
        largo, ancho = max(w, h), min(w, h)
        if not (p["largo_mm"][0] <= largo <= p["largo_mm"][1]) or ancho < 1:
            continue
        prop = largo / ancho
        if not (p["proporcion"][0] <= prop <= p["proporcion"][1]):
            continue
        relleno = cv2.contourArea(c) / (w * h)
        if relleno < p["relleno_min"]:
            continue
        # Eje largo de la lámina como vector unitario.
        a = math.radians(ang if w >= h else ang + 90)
        u = np.array([math.cos(a), math.sin(a)])
        # El Sol: compara el amarillo en el último sexto de cada extremo del eje largo.
        ys, xs = np.nonzero(amarillo)
        sol_mas = sol_menos = 0
        if len(xs):
            rel = np.column_stack([xs - cx, ys - cy])
            pu = rel @ u
            pv = rel @ np.array([-u[1], u[0]])
            dentro = (np.abs(pu) <= largo / 2) & (np.abs(pv) <= ancho / 2)
            sol_mas = int(np.count_nonzero(dentro & (pu > largo / 3)))
            sol_menos = int(np.count_nonzero(dentro & (pu < -largo / 3)))
        if sol_menos > sol_mas:
            u = -u
        seguridad = abs(sol_mas - sol_menos) / max(sol_mas + sol_menos, 1)
        v = np.array([-u[1], u[0]])  # u girado +90° (en la imagen, y hacia abajo)
        c0 = np.array([cx, cy])
        esquinas = [tuple(np.round(c0 + su * u * largo / 2 + sv * v * ancho / 2, 1)) for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        salida.append(
            Lamina(
                (round(cx, 1), round(cy, 1)),
                (round(largo, 1), round(ancho, 1)),
                round(math.degrees(math.atan2(u[1], u[0])), 1),
                [(float(x), float(y)) for x, y in esquinas],
                round(min(1.0, relleno * (0.5 + 0.5 * seguridad)), 3),
            )
        )
    salida.sort(key=lambda l: -l.puntaje)
    return salida


def lamina_a_mesa(lamina: Lamina, uv) -> np.ndarray:
    """(u, v) de la lámina (0..1, origen en la esquina lejos del Sol y del lado de -v) a mm de la mesa."""
    a = math.radians(lamina.angulo_deg)
    u = np.array([math.cos(a), math.sin(a)])
    v = np.array([-u[1], u[0]])
    largo, ancho = lamina.tam_mm
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    return np.asarray(lamina.centro_mm) + (uv[:, :1] - 0.5) * largo * u + (uv[:, 1:] - 0.5) * ancho * v


def mesa_a_lamina(lamina: Lamina, pts_mm) -> np.ndarray:
    a = math.radians(lamina.angulo_deg)
    u = np.array([math.cos(a), math.sin(a)])
    v = np.array([-u[1], u[0]])
    largo, ancho = lamina.tam_mm
    rel = np.asarray(pts_mm, dtype=np.float64).reshape(-1, 2) - np.asarray(lamina.centro_mm)
    return np.column_stack([rel @ u / largo + 0.5, rel @ v / ancho + 0.5])


def planetas_en_lamina(lamina: Lamina, plantilla: dict | None = None) -> dict[str, dict]:
    """Dónde está dibujado cada planeta en la mesa: {id: {centro_mm, radio_mm}}."""
    plantilla = plantilla or cargar_plantilla()
    largo = lamina.tam_mm[0]
    salida = {}
    for pid, d in plantilla["planetas"].items():
        c = lamina_a_mesa(lamina, [d["uv"]])[0]
        salida[pid] = {"centro_mm": (round(float(c[0]), 1), round(float(c[1]), 1)), "radio_mm": round(d["r"] * largo, 1)}
    return salida


def manchas_en_lamina(img_mm: np.ndarray, lamina: Lamina) -> list[dict]:
    """Planetas impresos detectados (manchas claras sobre el fondo oscuro), en coordenadas de la lámina.

    Sirve para medir una plantilla nueva y para comprobar que la plantilla calza con la lámina real.
    """
    largo, ancho = (int(round(x)) for x in lamina.tam_mm)
    a = math.radians(lamina.angulo_deg)
    u = np.array([math.cos(a), math.sin(a)])
    v = np.array([-u[1], u[0]])
    c0 = np.asarray(lamina.centro_mm)
    src = np.float32([c0 - u * largo / 2 - v * ancho / 2, c0 + u * largo / 2 - v * ancho / 2, c0 + u * largo / 2 + v * ancho / 2])
    dst = np.float32([[0, 0], [largo, 0], [largo, ancho]])
    rect = cv2.warpAffine(img_mm, cv2.getAffineTransform(src, dst), (largo, ancho))
    hsv = cv2.cvtColor(rect, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 2] > 110) & (hsv[..., 1] > 40)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(m)
    out = []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 12:
            continue
        x, y = cents[i]
        out.append({"uv": (round(x / largo, 3), round(y / ancho, 3)), "r": round(math.sqrt(stats[i, cv2.CC_STAT_AREA] / math.pi) / largo, 4), "area": int(stats[i, cv2.CC_STAT_AREA])})
    return sorted(out, key=lambda d: d["uv"][0])
