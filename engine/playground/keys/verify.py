"""Imágenes para verificar la detección de teclas.

- ``imagen_verificacion``: la foto con los polígonos y los nombres encima (blancas verdes, negras rojas, ocultas en
  naranjo, cuadrante de toque relleno), más un acercamiento de la banda del teclado ×3 debajo para leer los nombres.
- ``imagen_proyeccion``: solo contornos sobre negro, del tamaño del proyector, para proyectarla con el banco de
  trabajo y ver si cada contorno cae exactamente sobre su tecla. Los Do llevan su nombre en el frente.
"""

from __future__ import annotations

import cv2
import numpy as np

VERDE = (0, 230, 0)
ROJO = (60, 60, 255)
NARANJO = (0, 160, 255)
GRIS = (150, 150, 150)
CIAN = (255, 255, 0)


def _color(t) -> tuple:
    if t["estado"] != "visible":
        return NARANJO if t["estado"] == "oculta" else GRIS
    return VERDE if t["color"] == "blanca" else ROJO


def _pts(lista, escala=1.0, origen=(0, 0)):
    P = (np.asarray(lista, float) - origen) * escala
    return np.round(P * 16).astype(np.int32).reshape(-1, 1, 2)


def _dibujar(img, teclado, escala=1.0, origen=(0, 0), texto=0.35, todos_los_nombres=True):
    capa = img.copy()
    for t in teclado["teclas"]:
        if t["estado"] == "fuera":
            continue
        cv2.fillPoly(capa, [_pts(t["cuadrante"], escala, origen)], _color(t), cv2.LINE_AA, 4)
    img = cv2.addWeighted(capa, 0.28, img, 0.72, 0)
    grosor = 1 if escala < 2 else 2
    for t in sorted(teclado["teclas"], key=lambda t: t["color"] == "negra"):     # negras encima
        if t["estado"] == "fuera":
            continue
        forma = t["contorno"] if t["color"] == "blanca" else t["poligono"]
        cv2.polylines(img, [_pts(forma, escala, origen)], True, _color(t), grosor, cv2.LINE_AA, 4)
        if todos_los_nombres or t["nombre"].startswith("C") and "#" not in t["nombre"]:
            P = np.asarray(t["poligono"], float)
            if t["color"] == "blanca":
                x, y = (P[2] + P[3]) / 2                     # frente de la tecla
                dy = -4 * escala
            else:
                x, y = (P[0] + P[1]) / 2                     # fondo de la negra
                dy = 12 * escala
            (tw, th), _ = cv2.getTextSize(t["nombre"], cv2.FONT_HERSHEY_SIMPLEX, texto, 1)
            org = (int((x - origen[0]) * escala - tw / 2), int((y - origen[1]) * escala + dy))
            cv2.putText(img, t["nombre"], org, cv2.FONT_HERSHEY_SIMPLEX, texto, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(img, t["nombre"], org, cv2.FONT_HERSHEY_SIMPLEX, texto, _color(t), 1, cv2.LINE_AA)
    return img


def imagen_verificacion(img: np.ndarray, teclado: dict, zoom: float = 3.0) -> np.ndarray:
    """Foto con polígonos y nombres + acercamiento de la banda del teclado debajo."""
    arriba = _dibujar(img, teclado, texto=0.3, todos_los_nombres=False)
    vis = [t for t in teclado["teclas"] if t["estado"] != "fuera"]
    if not vis:
        return arriba
    P = np.concatenate([np.asarray(t["poligono"], float) for t in vis])
    x0, y0 = np.floor(P.min(axis=0)).astype(int) - 12; x1, y1 = np.ceil(P.max(axis=0)).astype(int) + 12
    x0, y0 = max(0, x0), max(0, y0); x1, y1 = min(img.shape[1], x1), min(img.shape[0], y1)
    recorte = cv2.resize(img[y0:y1, x0:x1], None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)
    zoom_img = _dibujar(recorte, teclado, escala=zoom, origen=(x0, y0), texto=0.5)
    # el acercamiento se parte en trozos del ancho de la foto y se apila debajo
    W = img.shape[1]; trozos = []
    for a in range(0, zoom_img.shape[1], W):
        trozo = np.zeros((zoom_img.shape[0], W, 3), np.uint8); parte = zoom_img[:, a:a + W]
        trozo[:, :parte.shape[1]] = parte; trozos.append(trozo)
    r = teclado.get("primera_visible") or {}
    resumen = (f"{teclado['n_teclas']} teclas ({teclado['n_visibles']} visibles, {teclado['n_ocultas']} ocultas)  "
               f"primera visible {r.get('nombre', '-')}  paso {teclado['paso_px']:.2f} px  "
               f"confianza {teclado['confianza_media']:.2f}  anclaje {teclado['patron']['anclaje']}")
    barra = np.zeros((44, W, 3), np.uint8)
    cv2.putText(barra, resumen, (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([arriba, barra] + trozos)


def imagen_proyeccion(teclado: dict, tam=(1920, 1080), grosor: int = 1, nombres: bool = True) -> np.ndarray:
    """Contornos sobre negro, en píxeles del proyector: blancas verdes, negras rojas, ocultas naranjas,
    línea cian donde empieza el tercio frontal (dónde tocar). Los Do llevan su nombre."""
    W, H = tam
    img = np.zeros((H, W, 3), np.uint8)
    for t in sorted(teclado["teclas"], key=lambda t: t["color"] == "negra"):
        if t["estado"] == "fuera":
            continue
        forma = t["contorno"] if t["color"] == "blanca" else t["poligono"]
        cv2.polylines(img, [_pts(forma)], True, _color(t), grosor, cv2.LINE_AA, 4)
        Q = np.asarray(t["cuadrante"], float)
        cv2.line(img, tuple(np.round(Q[0] * 16).astype(int)), tuple(np.round(Q[1] * 16).astype(int)), CIAN, 1, cv2.LINE_AA, 4)
        if nombres and t["color"] == "blanca" and t["nombre"].startswith("C"):
            x, y = np.asarray(t["punto_toque"], float)
            (tw, th), _ = cv2.getTextSize(t["nombre"], cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
            cv2.putText(img, t["nombre"], (int(x - tw / 2), int(y + th / 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                        (255, 255, 255), 1, cv2.LINE_AA)
    return img
