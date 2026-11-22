"""Dónde está la libreta, cuántas páginas muestra y cuál es la activa.

Funciones puras: entra una imagen BGR (numpy, como la entrega OpenCV) y sale un diccionario con números simples.
"""

from __future__ import annotations

import cv2
import numpy as np

from .geometria import girar_esquinas, lados, ordenar_esquinas

LADO_RECTIFICADO = 900  # px del lado largo de la libreta rectificada: los umbrales de tinta están pensados para esto


def _candidatos(img: np.ndarray, escala: float, area_min: float) -> list[dict]:
    """Regiones claras y poco saturadas (papel) con forma de cuadrilátero."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    L = cv2.GaussianBlur(lab[:, :, 0], (5, 5), 0)
    sat = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 1]
    _, claro = cv2.threshold(L, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    papel = cv2.bitwise_and(claro, (sat < 110).astype(np.uint8) * 255)
    # cierre chico: la tinta y los renglones quedan como agujeros, que no importan porque solo se usa el contorno
    # externo; un cierre grande pegaría la libreta con lo que tenga al lado
    papel = cv2.morphologyEx(papel, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    papel = cv2.morphologyEx(papel, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    contornos, _ = cv2.findContours(papel, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    total = img.shape[0] * img.shape[1]
    # si algo blanco toca la libreta, se intenta separarlos con una apertura más fuerte
    separados = []
    for c in contornos:
        if cv2.contourArea(c) < area_min * total:
            continue
        r = cv2.minAreaRect(c)
        if cv2.contourArea(c) / max(r[1][0] * r[1][1], 1.0) >= 0.72:
            continue
        solo = np.zeros(papel.shape, np.uint8)
        cv2.drawContours(solo, [c], -1, 255, -1)
        solo = cv2.morphologyEx(solo, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)))
        separados += cv2.findContours(solo, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    piezas = []
    for c in list(contornos) + separados:
        area = cv2.contourArea(c)
        if area < area_min * total:
            continue
        casco = cv2.convexHull(c)
        rect = cv2.minAreaRect(casco)
        rectangular = area / max(rect[1][0] * rect[1][1], 1.0)
        if rectangular < 0.72:
            continue
        # el papel tiene que resaltar contra lo que lo rodea (una mesa con luz despareja no es una libreta)
        dentro = np.zeros(L.shape, np.uint8)
        cv2.drawContours(dentro, [casco], -1, 255, -1)
        anillo = cv2.subtract(cv2.dilate(dentro, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41))), dentro)
        l_dentro = float(cv2.mean(L, dentro)[0])
        if l_dentro < 140:
            continue
        if np.count_nonzero(anillo) > 0.02 * area and l_dentro - float(cv2.mean(L, anillo)[0]) < 22:
            continue
        piezas.append((casco, area, rectangular, rect))
    # libreta abierta: la sombra del lomo puede partirla en dos páginas; si juntas forman un rectángulo, son una
    for i in range(len(piezas)):
        for j in range(i + 1, len(piezas)):
            casco = cv2.convexHull(np.vstack([piezas[i][0], piezas[j][0]]))
            rect = cv2.minAreaRect(casco)
            area = piezas[i][1] + piezas[j][1]
            rectangular = area / max(rect[1][0] * rect[1][1], 1.0)
            if rectangular >= 0.85:
                piezas.append((casco, area, rectangular, rect))
    out = []
    for casco, area, rectangular, rect in piezas:
        quad = None
        peri = cv2.arcLength(casco, True)
        for eps in (0.01, 0.02, 0.03, 0.04, 0.05):
            aprox = cv2.approxPolyDP(casco, eps * peri, True)
            if len(aprox) == 4:
                quad = aprox.reshape(4, 2).astype(np.float32)
                break
        if quad is None:
            quad = cv2.boxPoints(rect).astype(np.float32)
        out.append(
            {
                "quad": ordenar_esquinas(quad / escala),
                "area": area / total,
                "rectangular": rectangular,
                "centro": np.asarray(rect[0], dtype=np.float32) / escala,
            }
        )
    return out


def ubicar_libreta(img: np.ndarray, *, cerca_de=None, area_min: float = 0.03, girar: int = 0) -> dict | None:
    """Busca la libreta (el papel claro más grande y rectangular) en una foto.

    - `cerca_de`: [x, y] en píxeles; si se da, gana el candidato más cercano (útil en la cenital, donde hay otras cosas
      blancas como la caja).
    - `girar`: cuartos de vuelta para que la primera esquina quede arriba a la izquierda tal como lee la persona.

    Devuelve {quad_px, area, rectangular, aspecto, paginas: [[x, y, w, h], ...]} o None. `paginas` va en coordenadas
    de la libreta (0..1); son dos si la libreta está abierta en doble página.
    """
    alto, ancho = img.shape[:2]
    escala = min(1.0, 800 / max(alto, ancho))
    chica = cv2.resize(img, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA) if escala < 1 else img
    cands = _candidatos(chica, escala, area_min)
    if not cands:
        return None
    if cerca_de is not None:
        p = np.asarray(cerca_de, dtype=np.float32)
        elegido = min(cands, key=lambda c: float(np.linalg.norm(c["centro"] - p)))
    else:
        elegido = max(cands, key=lambda c: c["area"] * c["rectangular"])
    quad = girar_esquinas(elegido["quad"], girar)
    w, h = lados(quad)
    rect = rectificar(img, quad)
    return {
        "quad_px": quad.round(1).tolist(),
        "area": round(float(elegido["area"]), 4),
        "rectangular": round(float(elegido["rectangular"]), 3),
        "aspecto": round(w / max(h, 1e-6), 3),
        "paginas": paginas(rect),
    }


def tamano_rectificado(quad, lado: int = LADO_RECTIFICADO) -> tuple[int, int]:
    w, h = lados(quad)
    if w >= h:
        return lado, max(8, round(lado * h / max(w, 1e-6)))
    return max(8, round(lado * w / max(h, 1e-6))), lado


def rectificar(img: np.ndarray, quad, tamano: tuple[int, int] | None = None) -> np.ndarray:
    """La libreta vista de frente: recorte con la perspectiva corregida, orientado como lo lee la persona."""
    ancho, alto = tamano or tamano_rectificado(quad)
    destino = np.float32([[0, 0], [ancho, 0], [ancho, alto], [0, alto]])
    M = cv2.getPerspectiveTransform(np.asarray(quad, dtype=np.float32).reshape(4, 2), destino)
    return cv2.warpPerspective(img, M, (ancho, alto), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def paginas(rectificada: np.ndarray) -> list[list[float]]:
    """Una o dos páginas, en coordenadas de la libreta. Dos si es apaisada y tiene un lomo oscuro cerca del centro."""
    alto, ancho = rectificada.shape[:2]
    if ancho / alto < 1.15:
        return [[0.0, 0.0, 1.0, 1.0]]
    gris = rectificada.max(axis=2)  # el canal más brillante: el margen rojo y los renglones celestes no hacen valle
    banda = gris[int(alto * 0.08) : int(alto * 0.92)].astype(np.float32)
    perfil = cv2.GaussianBlur(banda.mean(axis=0).reshape(1, -1), (0, 0), sigmaX=max(2, ancho // 300)).ravel()
    # el lomo es un valle angosto: se mide contra el papel de los costados, no contra toda la hoja (la luz no es pareja)
    ventana = max(9, (ancho // 12) | 1)
    base = cv2.dilate(perfil.reshape(1, -1), np.ones((1, ventana), np.float32)).ravel()
    i0, i1 = int(ancho * 0.35), int(ancho * 0.65)
    hondura = (base[i0:i1] - perfil[i0:i1]) / np.maximum(base[i0:i1], 1.0)
    k = int(np.argmax(hondura))
    if hondura[k] < 0.06:  # sin lomo visible: es una sola página apaisada
        return [[0.0, 0.0, 1.0, 1.0]]
    corte = (i0 + k) / ancho
    return [[0.0, 0.0, round(corte, 4), 1.0], [round(corte, 4), 0.0, round(1 - corte, 4), 1.0]]


def recorte_pagina(rectificada: np.ndarray, pagina) -> np.ndarray:
    alto, ancho = rectificada.shape[:2]
    x, y, w, h = pagina
    return rectificada[round(y * alto) : round((y + h) * alto), round(x * ancho) : round((x + w) * ancho)]


def pagina_activa(paginas_: list, nuevo_por_pagina: list[int], anterior: int | None = None) -> int:
    """La página donde apareció tinta nueva; si en ninguna, la de la última lectura; si no hay, la de la derecha."""
    if nuevo_por_pagina and max(nuevo_por_pagina) > 0:
        return int(np.argmax(nuevo_por_pagina))
    if anterior is not None and anterior < len(paginas_):
        return anterior
    return len(paginas_) - 1
