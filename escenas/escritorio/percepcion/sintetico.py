"""Libretas sintéticas para probar la percepción sin cámara: papel con renglones, letra a mano, perspectiva y luz.

`foto(...)` devuelve la imagen y la verdad (esquinas en px y cajas de cada renglón en coordenadas de la libreta).
"""

from __future__ import annotations

import cv2
import numpy as np

PAPEL = (226, 236, 242)  # BGR, papel algo cálido
RENGLON = (226, 200, 168)  # celeste
MARGEN = (110, 110, 225)  # rojo
TINTA = (110, 45, 25)  # azul lápiz pasta


def pagina(ancho=620, alto=880, *, renglon=34, margen_x=86, espiral=False) -> np.ndarray:
    img = np.full((alto, ancho, 3), PAPEL, np.uint8)
    for y in range(renglon * 3, alto - renglon // 2, renglon):
        cv2.line(img, (0, y), (ancho, y), RENGLON, 1, cv2.LINE_AA)
    cv2.line(img, (margen_x, 0), (margen_x, alto), MARGEN, 2, cv2.LINE_AA)
    if espiral:
        for y in range(24, alto - 10, 28):
            cv2.circle(img, (10, y), 6, (60, 60, 60), 2, cv2.LINE_AA)
    return img


def escribir(img: np.ndarray, lineas: list[str], *, desde=3, renglon=34, x=100, color=TINTA, escala=1.0, grosor=2):
    """Escribe a mano (Hershey script) sobre los renglones; devuelve la caja en px de cada línea."""
    cajas = []
    for i, texto in enumerate(lineas):
        y = renglon * (desde + i) - 6
        (w, h), base = cv2.getTextSize(texto, cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, escala, grosor)
        cv2.putText(img, texto, (x, y), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, escala, color, grosor, cv2.LINE_AA)
        cajas.append([x, y - h, w, h + base])
    return cajas


def doble(izq: np.ndarray, der: np.ndarray, lomo=14) -> np.ndarray:
    alto = izq.shape[0]
    centro = np.zeros((alto, lomo, 3), np.uint8)
    for j in range(lomo):  # sombra del lomo: más oscuro al medio
        t = 1 - abs(j - lomo / 2) / (lomo / 2)
        centro[:, j] = (np.array(PAPEL) * (1 - 0.55 * t)).astype(np.uint8)
    return np.hstack([izq, centro, der])


def mesa(ancho=1280, alto=720, semilla=0) -> np.ndarray:
    rng = np.random.default_rng(semilla)
    base = np.full((alto, ancho, 3), (118, 126, 132), np.float32)
    gx = np.linspace(-1, 1, ancho)[None, :, None]
    gy = np.linspace(-1, 1, alto)[:, None, None]
    base *= 1 + 0.08 * gx - 0.06 * gy
    base += rng.normal(0, 4, base.shape)
    return np.clip(base, 0, 255).astype(np.uint8)


def poner(fondo: np.ndarray, libreta: np.ndarray, quad) -> np.ndarray:
    """Pega la libreta en la foto con perspectiva (esquinas TL, TR, BR, BL) y una sombra suave."""
    alto, ancho = libreta.shape[:2]
    src = np.float32([[0, 0], [ancho, 0], [ancho, alto], [0, alto]])
    M = cv2.getPerspectiveTransform(src, np.float32(quad))
    H, W = fondo.shape[:2]
    warp = cv2.warpPerspective(libreta, M, (W, H), flags=cv2.INTER_LINEAR)
    mascara = cv2.warpPerspective(np.full((alto, ancho), 255, np.uint8), M, (W, H))
    sombra = cv2.GaussianBlur(cv2.warpPerspective(np.full((alto, ancho), 255, np.uint8), M, (W, H)), (0, 0), 9)
    sombra = np.roll(sombra, (8, 6), axis=(0, 1)).astype(np.float32) / 255
    out = fondo.astype(np.float32) * (1 - 0.35 * sombra[..., None])
    m = mascara.astype(np.float32)[..., None] / 255
    out = out * (1 - m) + warp.astype(np.float32) * m
    return np.clip(out, 0, 255).astype(np.uint8)


def luz(img: np.ndarray, semilla=0, *, ruido=3.0, desenfoque=0.8, viñeta=0.12) -> np.ndarray:
    rng = np.random.default_rng(semilla)
    H, W = img.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    cx, cy = W * (0.4 + 0.2 * rng.random()), H * (0.4 + 0.2 * rng.random())
    r = np.hypot((xx - cx) / W, (yy - cy) / H)
    out = img.astype(np.float32) * (1 - viñeta * r[..., None] * 2)
    out += rng.normal(0, ruido, out.shape)
    out = np.clip(out, 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(out, (0, 0), desenfoque) if desenfoque else out


def proyectar_luz(img: np.ndarray, texto: str, origen, *, color=(255, 170, 60), escala=1.1) -> np.ndarray:
    """Simula la respuesta proyectada: luz de color que se suma a la foto (nunca oscurece)."""
    capa = np.zeros_like(img)
    cv2.putText(capa, texto, origen, cv2.FONT_HERSHEY_SIMPLEX, escala, color, 3, cv2.LINE_AA)
    capa = cv2.GaussianBlur(capa, (0, 0), 1.2)
    return cv2.add(img, (capa * 0.8).astype(np.uint8))


def foto(lineas, *, quad=None, semilla=0, doble_pagina=False, lineas_izq=(), lado=0, fondo=None, mano=False) -> dict:
    """Arma una foto de cámara secundaria con la libreta y devuelve la verdad.

    - `lineas`: texto de la página activa (la derecha si es doble página).
    - `quad`: esquinas de la libreta en la foto; por defecto una perspectiva suave.
    - `lado`: cuartos de vuelta de la libreta en la foto (para probar `girar`).
    """
    if doble_pagina:
        izq = pagina()
        escribir(izq, list(lineas_izq))
        der = pagina()
        cajas_der = escribir(der, list(lineas))
        hoja = doble(izq, der)
        dx = izq.shape[1] + 14
        cajas = [[x + dx, y, w, h] for x, y, w, h in cajas_der]
    else:
        hoja = pagina()
        cajas = escribir(hoja, list(lineas))
    alto, ancho = hoja.shape[:2]
    cajas_norm = [[x / ancho, y / alto, w / ancho, h / alto] for x, y, w, h in cajas]
    fondo = mesa(semilla=semilla) if fondo is None else fondo.copy()
    H, W = fondo.shape[:2]
    if quad is None:
        if doble_pagina:
            quad = [[W * 0.14, H * 0.12], [W * 0.88, H * 0.15], [W * 0.86, H * 0.9], [W * 0.12, H * 0.86]]
        else:
            quad = [[W * 0.33, H * 0.08], [W * 0.68, H * 0.1], [W * 0.7, H * 0.93], [W * 0.31, H * 0.9]]
    quad = np.float32(quad)
    en_foto = np.roll(quad, lado, axis=0)  # la libreta girada: su esquina TL cae en otra esquina de la foto
    img = poner(fondo, hoja, en_foto)
    if mano:
        c = quad.mean(axis=0).astype(int)
        cv2.ellipse(img, (int(c[0]), int(c[1])), (90, 60), 30, 0, 360, (120, 150, 205), -1, cv2.LINE_AA)
    img = luz(img, semilla)
    return {"img": img, "quad": quad, "cajas": cajas_norm, "tamano_hoja": (ancho, alto)}
