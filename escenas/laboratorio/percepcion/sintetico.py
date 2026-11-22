"""Imágenes sintéticas para probar la percepción sin la mesa: el vaso de lado y el pastillero desde arriba.

Todavía no hay fotos del vaso. Estas imágenes imitan lo que verá la cámara secundaria: un fondo de mesón con algo de
textura, un vaso de vidrio con marcas impresas, el líquido (de color o casi transparente) con menisco, ruido y desenfoque.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# Geometría del vaso sintético (px en un cuadro de 640×480): interior y escala de las marcas.
VASO_X, VASO_ANCHO = 220, 200
VASO_FONDO = 420  # fila del fondo interior
PX_POR_ML = 1.2  # 250 ml ocupan 300 px
MARCAS_ML = (50, 100, 150, 200, 250)


@dataclass
class VasoSintetico:
    cuadro: np.ndarray
    vacio: np.ndarray
    fila_superficie: float  # verdad: fila de la superficie
    roi_px: list[int]  # interior del vaso
    marcas_px: dict[int, float]  # {ml: fila}


def fila_de_ml(ml: float) -> float:
    return VASO_FONDO - ml * PX_POR_ML


def _fondo(ancho, alto, rng) -> np.ndarray:
    """Mesón gris azulado con una franja más clara (azulejo) y textura suave."""
    y = np.linspace(0, 1, alto)[:, None]
    base = np.zeros((alto, ancho, 3), np.float32)
    base[...] = (150, 155, 160)
    base += (y * 25)[..., None]
    base[:, :, :] += rng.normal(0, 4, (alto, ancho, 1)).astype(np.float32)
    for x in range(0, ancho, 80):  # juntas de azulejo: dan textura para que el agua las deforme
        cv2.line(base, (x, 0), (x, alto), (120, 125, 130), 2)
    for yy in range(0, alto, 80):
        cv2.line(base, (0, yy), (ancho, yy), (120, 125, 130), 2)
    return base


def vaso(
    ml: float,
    color_bgr=(60, 40, 200),  # rojo de colorante
    alfa: float = 0.55,  # 0 = agua transparente, 1 = opaco
    ruido: float = 3.0,
    desenfoque: int = 3,
    semilla: int = 0,
    ancho: int = 640,
    alto: int = 480,
) -> VasoSintetico:
    rng = np.random.default_rng(semilla)
    fondo = _fondo(ancho, alto, rng)
    x0, x1 = VASO_X, VASO_X + VASO_ANCHO
    tope = int(fila_de_ml(270))

    def con_vaso(img: np.ndarray) -> np.ndarray:
        img = img.copy()
        # Vidrio: un poco más claro, paredes gruesas y brillantes, base.
        img[tope:VASO_FONDO, x0:x1] = img[tope:VASO_FONDO, x0:x1] * 0.92 + 18
        cv2.rectangle(img, (x0 - 6, tope - 10), (x0, VASO_FONDO + 8), (215, 220, 225), -1)
        cv2.rectangle(img, (x1, tope - 10), (x1 + 6, VASO_FONDO + 8), (215, 220, 225), -1)
        cv2.rectangle(img, (x0 - 6, VASO_FONDO), (x1 + 6, VASO_FONDO + 10), (205, 210, 215), -1)
        for m in MARCAS_ML:
            y = int(round(fila_de_ml(m)))
            cv2.line(img, (x0 + 4, y), (x0 + 34, y), (40, 40, 40), 2)
            cv2.putText(img, str(m), (x0 + 38, y + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (40, 40, 40), 1, cv2.LINE_AA)
        for m in range(10, 260, 10):
            y = int(round(fila_de_ml(m)))
            cv2.line(img, (x0 + 4, y), (x0 + 18, y), (60, 60, 60), 1)
        return img

    vacio = con_vaso(fondo)
    lleno = fondo.copy()
    sup = fila_de_ml(ml)
    if ml > 0:
        ys = int(np.floor(sup))
        # El agua deforma el fondo (refracción): se ve el mesón estirado y desplazado.
        detras = cv2.resize(fondo[:, x0:x1], None, fx=1.15, fy=1.0)[:, 12 : 12 + (x1 - x0)]
        lleno[ys:VASO_FONDO, x0:x1] = detras[ys:VASO_FONDO]
        tinte = np.array(color_bgr, np.float32)
        lleno[ys:VASO_FONDO, x0:x1] = lleno[ys:VASO_FONDO, x0:x1] * (1 - alfa) + tinte * alfa
        # Fila parcial (posición subpíxel de la superficie).
        frac = 1 - (sup - ys)
        lleno[ys, x0:x1] = fondo[ys, x0:x1] * (1 - frac) + lleno[ys, x0:x1] * frac
        # Menisco: una línea oscura que sube en los bordes.
        pts = np.array([[x, sup - 6 * ((x - (x0 + x1) / 2) / ((x1 - x0) / 2)) ** 4] for x in range(x0, x1)], np.int32)
        cv2.polylines(lleno, [pts], False, (70, 70, 80), 2, cv2.LINE_AA)
    cuadro = con_vaso(lleno)

    def terminar(img):
        img = img + rng.normal(0, ruido, img.shape).astype(np.float32)
        if desenfoque > 1:
            img = cv2.GaussianBlur(img, (desenfoque | 1, desenfoque | 1), 0)
        return np.clip(img, 0, 255).astype(np.uint8)

    return VasoSintetico(
        cuadro=terminar(cuadro),
        vacio=terminar(vacio),
        fila_superficie=float(sup),
        roi_px=[x0 + 8, tope, VASO_ANCHO - 16, VASO_FONDO - tope],
        marcas_px={m: fila_de_ml(m) for m in MARCAS_ML},
    )


# Colores BGR aproximados de las columnas del pastillero de la demo (medidos en capturas/mesa-cenital-mm-1.jpg).
COLORES_DEMO_BGR = [(216, 205, 190), (220, 90, 110), (90, 170, 160), (176, 130, 100), (160, 186, 150), (200, 60, 215), (180, 150, 205)]


def pastillero(
    centro_mm=(500, 280),
    angulo_deg: float = -28,
    paso_mm: float = 22,
    colores_bgr=COLORES_DEMO_BGR,
    ancho_mm: int = 1000,
    alto_mm: int = 563,
    semilla: int = 0,
) -> tuple[np.ndarray, dict]:
    """Vista cenital a 1 px = 1 mm con un pastillero 7×4 girado. Devuelve la imagen y la verdad (centro de A1, ejes)."""
    rng = np.random.default_rng(semilla)
    img = np.full((alto_mm, ancho_mm, 3), (150, 160, 170), np.float32)  # mesa beige-gris
    img += rng.normal(0, 3, img.shape).astype(np.float32)
    t = np.deg2rad(angulo_deg)
    u = np.array([np.cos(t), np.sin(t)])
    v = np.array([-u[1], u[0]])
    c = np.array(centro_mm, float)
    origen = c - u * 3.5 * paso_mm - v * 2 * paso_mm
    # Caja de plástico (marco claro) un poco más grande que la grilla, y la tapa abierta del lado de la fila D.
    marco = [origen - u * 3 - v * 3, origen + u * (7 * paso_mm + 3) - v * 3, origen + u * (7 * paso_mm + 3) + v * (4 * paso_mm + 3), origen - u * 3 + v * (4 * paso_mm + 3)]
    cv2.fillPoly(img, [np.int32(marco)], (200, 205, 210))
    tapa = [marco[3], marco[2], marco[2] + v * 4 * paso_mm, marco[3] + v * 4 * paso_mm]
    cv2.polylines(img, [np.int32(tapa)], True, (210, 215, 220), 2)
    for f in range(4):
        for col in range(7):
            cc = origen + u * (col + 0.5) * paso_mm + v * (f + 0.5) * paso_mm
            lado = paso_mm * 0.42
            esq = [cc + (a * u + b * v) * lado for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            cv2.fillPoly(img, [np.int32(np.round(esq))], colores_bgr[col])
    img = cv2.GaussianBlur(img, (3, 3), 0)
    verdad = {"A1": (origen + (u + v) * 0.5 * paso_mm).tolist(), "D7": (origen + u * 6.5 * paso_mm + v * 3.5 * paso_mm).tolist(), "u": u.tolist(), "v": v.tolist()}
    return np.clip(img, 0, 255).astype(np.uint8), verdad
