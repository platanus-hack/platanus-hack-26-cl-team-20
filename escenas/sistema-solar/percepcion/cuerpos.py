"""Bolas de plumavit y engranaje (el Sol) sobre la vista cenital en mm.

Cómo funciona, en una línea por paso:
1. Máscara de blanco liso: claro (L alto en Lab), sin color (croma bajo) y después de una apertura morfológica de
   11 mm, que borra el mimbre del canasto (claro pero delgado) y deja las superficies blancas grandes.
2. Transformada de distancia: el centro de cada disco blanco es un máximo local y su valor es el radio inscrito.
   Sirve aunque las bolas se toquen, porque cada una deja su propio máximo.
3. Para cada candidato se lanzan 180 rayos desde el centro hasta el borde de la máscara:
   - bola: casi todos los rayos miden lo mismo (redondez); el radio y el centro salen de ajustar un círculo al borde;
   - engranaje: el borde es dentado. Con una máscara más fina (apertura de 5 mm) los dientes aparecen como
     rugosidad del perfil de rayos: ~0,05 del radio en el engranaje, ≤ 0,025 en las bolas grandes;
   - lo demás (taza, caja, plasticina, papel) tiene perfiles irregulares y se descarta.

Medido sobre las 4 capturas del 21-nov: 9 de 9 bolas, el engranaje, y nada más.
"""

from __future__ import annotations

import cv2
import numpy as np

from .tipos import Cuerpo

PARAMS = {
    "l_min": 192.0,  # luminancia mínima (Lab, 0..255) para contar como blanco
    "l_sobre_mesa": 30.0,  # o, si la mesa es clara, al menos esto sobre la mediana de la foto
    "croma_max": 16.0,  # el canasto tiene croma ~30, el plumavit < 8
    "apertura_mm": 11,  # más ancho que una hebra de mimbre, más angosto que la bola más chica
    "apertura_fina_mm": 5,  # para ver los dientes del engranaje
    "radio_min_mm": 11.5,  # la bola más chica medida tiene 13 mm de radio; con menos aparecen fantasmas en bordes claros
    "radio_max_mm": 85.0,
    "rayos": 180,
    "redondez_min": 0.75,  # fracción de rayos a ±18 % de la mediana (bolas ≥ 0,79; taza y caja ≤ 0,66)
    "cobertura_min": 0.88,  # fracción del disco (0,85 r) que es blanca
    "contraste_min": 25.0,  # L del interior − percentil 30 de L en el anillo de afuera (bolas ≥ 54, mesa clara ~8)
    "engranaje_radio_min_mm": 28.0,
    "engranaje_rugosidad_min": 0.036,  # mediana de |rayo − rayo suavizado| / r
    # El engranaje con una bola encima pierde redondez (0,46-0,58 en vivo el 21-nov): un candidato grande con menos
    # redondez que una bola solo entra si además es dentado. La taza y la caja no tienen dientes.
    "engranaje_redondez_min": 0.42,
    # Histéresis: cerca de donde estaba el Sol (`sol_previo`) basta con menos evidencia para seguir viéndolo.
    "sol_previo_radio_mm": 45.0,
    "sol_previo_redondez_min": 0.35,
    "sol_previo_rugosidad": 0.75,  # fracción de engranaje_rugosidad_min
}


def mascara_blanca(img_mm: np.ndarray, p: dict = PARAMS, apertura: int | None = None) -> np.ndarray:
    """Blanco liso: la apertura morfológica borra lo claro y delgado (mimbre, letras, bordes)."""
    ap = int(apertura or p["apertura_mm"])
    lab = cv2.cvtColor(img_mm, cv2.COLOR_BGR2LAB)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ap, ap))
    lum = cv2.morphologyEx(lab[..., 0], cv2.MORPH_OPEN, k).astype(np.float32)
    croma = np.hypot(lab[..., 1].astype(np.float32) - 128.0, lab[..., 2].astype(np.float32) - 128.0)
    croma = cv2.dilate(croma, k)
    umbral = max(p["l_min"], float(np.median(lab[..., 0])) + p["l_sobre_mesa"])
    m = ((lum > umbral) & (croma < p["croma_max"])).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    # Rellena agujeros chicos (brillos o sombras dentro de una bola).
    cnts, _ = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        if cv2.contourArea(c) < 400:
            cv2.drawContours(m, [c], -1, 255, -1)
    return m


def _rayos(mask: np.ndarray, c: tuple[float, float], r_max: float, n: int) -> np.ndarray:
    """Largo de cada rayo desde `c` hasta el primer píxel fuera de la máscara (o `r_max`)."""
    h, w = mask.shape
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    pasos = np.arange(1, int(r_max) + 1, dtype=np.float32)
    xs = c[0] + np.cos(ang)[:, None] * pasos[None, :]
    ys = c[1] + np.sin(ang)[:, None] * pasos[None, :]
    xi = np.clip(np.round(xs).astype(int), 0, w - 1)
    yi = np.clip(np.round(ys).astype(int), 0, h - 1)
    fuera = (mask[yi, xi] == 0) | (xs < 0) | (ys < 0) | (xs >= w) | (ys >= h)
    primero = np.where(fuera.any(axis=1), fuera.argmax(axis=1), len(pasos) - 1)
    return pasos[primero]


def _circulo(puntos: np.ndarray) -> tuple[float, float, float]:
    """Ajuste algebraico de círculo (Kåsa)."""
    x, y = puntos[:, 0], puntos[:, 1]
    a = np.column_stack([x, y, np.ones_like(x)])
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    cx, cy = sol[0] / 2, sol[1] / 2
    return float(cx), float(cy), float(np.sqrt(max(sol[2] + cx * cx + cy * cy, 1e-6)))


def _rugosidad(mask_fina: np.ndarray, c: tuple[float, float], r: float) -> tuple[float, int]:
    """Qué tan dentado es el borde: mediana de |rayo − rayo suavizado| / r, solo en rayos que no se escapan a un vecino.
    Devuelve también una estimación de la cantidad de dientes (cruces por el borde suavizado / 2)."""
    largos = _rayos(mask_fina, c, r * 2.5, 360)
    ok = (largos > 0.75 * r) & (largos < 1.3 * r)
    if ok.mean() < 0.5:
        return 0.0, 0
    sm = np.convolve(np.r_[largos[-10:], largos, largos[:10]], np.ones(21) / 21, "valid")
    dev = (largos - sm) / r
    signo = np.sign(dev[ok])
    cruces = int(np.count_nonzero(np.diff(signo) != 0))
    return float(np.median(np.abs(dev[ok]))), int(round(cruces / 2 / ok.mean()))


def detectar_cuerpos(
    img_mm: np.ndarray, zona_mm=None, params: dict | None = None, traza: list | None = None, sol_previo=None
) -> list[Cuerpo]:
    """Bolas y engranaje en la vista cenital (1 px = 1 mm). `zona_mm` = [x, y, w, h] limita la búsqueda.
    `sol_previo` (x, y en mm): donde estaba el Sol; ahí se lo acepta con menos evidencia (histéresis).

    Devuelve primero el engranaje (si hay) y después las bolas de mayor a menor radio. Si se pasa `traza` (una lista),
    anota ahí cada candidato descartado y por qué, para afinar parámetros con fotos nuevas.
    """
    anota = traza.append if traza is not None else (lambda _x: None)
    p = {**PARAMS, **(params or {})}
    m = mascara_blanca(img_mm, p)
    if zona_mm is not None:
        x, y, w, h = (int(round(v)) for v in zona_mm)
        z = np.zeros_like(m)
        z[max(y, 0) : max(y + h, 0), max(x, 0) : max(x + w, 0)] = 255
        m = cv2.bitwise_and(m, z)
    fina = None
    lum = cv2.cvtColor(img_mm, cv2.COLOR_BGR2LAB)[..., 0]
    dist = cv2.distanceTransform(m, cv2.DIST_L2, 5)
    r_min = p["radio_min_mm"]
    k = 2 * int(r_min * 0.6) + 1  # ventana chica: dos bolas pegadas dejan dos máximos cercanos
    pico = (dist == cv2.dilate(dist, np.ones((k, k), np.uint8))) & (dist >= r_min * 0.8)
    ys, xs = np.nonzero(pico)
    orden = np.argsort(-dist[ys, xs])
    aceptados: list[tuple[float, float, float]] = []
    bolas: list[Cuerpo] = []
    engranajes: list[tuple[float, Cuerpo]] = []
    for i in orden:
        cx, cy, r0 = float(xs[i]), float(ys[i]), float(dist[ys[i], xs[i]])
        # Solo suprime un cuerpo ya aceptado (un candidato descartado no tapa a una bola chica pegada a él).
        if any(np.hypot(cx - a, cy - b) < ra * 0.9 for a, b, ra in aceptados):
            continue
        alcance = min(r0 * 2.6, p["radio_max_mm"] * 1.6)
        largos = _rayos(m, (cx, cy), alcance, p["rayos"])
        med = float(np.median(largos))
        # El máximo de la distancia puede quedar corrido (brillo o sombra de un lado): recentra con el borde y vuelve a medir.
        ang0 = np.linspace(0, 2 * np.pi, p["rayos"], endpoint=False)
        uso = largos < 1.4 * med
        if uso.sum() >= 12:
            nx, ny, _ = _circulo(np.column_stack([cx + np.cos(ang0[uso]) * largos[uso], cy + np.sin(ang0[uso]) * largos[uso]]))
            if np.hypot(nx - cx, ny - cy) < 0.6 * med and m[int(np.clip(ny, 0, m.shape[0] - 1)), int(np.clip(nx, 0, m.shape[1] - 1))]:
                cx, cy = nx, ny
                largos = _rayos(m, (cx, cy), alcance, p["rayos"])
                med = float(np.median(largos))
        if med < r_min or med > p["radio_max_mm"] * 1.15:
            anota({"centro": (round(cx), round(cy)), "motivo": "tamaño", "med": round(med, 1)})
            continue
        cerca = np.abs(largos - med) < max(0.18 * med, 3.5)  # en bolas chicas manda el redondeo a píxel
        redondez = float(cerca.mean())
        junto_al_previo = sol_previo is not None and np.hypot(cx - sol_previo[0], cy - sol_previo[1]) < p["sol_previo_radio_mm"]
        rug_min = p["engranaje_rugosidad_min"] * (p["sol_previo_rugosidad"] if junto_al_previo else 1.0)
        solo_engranaje = False  # poco redondo para bola: entra solo si resulta dentado
        if redondez < p["redondez_min"]:
            minimo = p["sol_previo_redondez_min"] if junto_al_previo else p["engranaje_redondez_min"]
            if med < p["engranaje_radio_min_mm"] or redondez < minimo:
                anota({"centro": (round(cx), round(cy)), "motivo": "redondez", "med": round(med, 1), "valor": round(redondez, 2)})
                continue
            solo_engranaje = True
        ang = np.linspace(0, 2 * np.pi, p["rayos"], endpoint=False)[cerca]
        borde = np.column_stack([cx + np.cos(ang) * largos[cerca], cy + np.sin(ang) * largos[cerca]])
        fx, fy, fr = _circulo(borde) if len(borde) >= 8 else (cx, cy, med)
        if np.hypot(fx - cx, fy - cy) > 0.5 * med or not (0.7 * med < fr < 1.3 * med):
            fx, fy, fr = cx, cy, med
        disco = np.zeros_like(m)
        cv2.circle(disco, (int(round(fx)), int(round(fy))), max(1, int(fr * 0.85)), 255, -1)
        cobertura = float((m[disco > 0] > 0).mean()) if disco.any() else 0.0
        if cobertura < p["cobertura_min"]:
            anota({"centro": (round(cx), round(cy)), "motivo": "cobertura", "med": round(med, 1), "valor": round(cobertura, 2)})
            continue
        anillo = np.zeros_like(m)
        cv2.circle(anillo, (int(round(fx)), int(round(fy))), int(fr * 1.6), 255, -1)
        cv2.circle(anillo, (int(round(fx)), int(round(fy))), int(fr * 1.15), 0, -1)
        contraste = float(np.median(lum[disco > 0]) - np.percentile(lum[anillo > 0], 30)) if anillo.any() else 0.0
        if contraste < p["contraste_min"]:
            anota({"centro": (round(fx), round(fy)), "motivo": "contraste", "med": round(med, 1), "valor": round(contraste, 1)})
            continue
        if any(np.hypot(fx - a, fy - b) < max(ra, fr) * 0.6 for a, b, ra in aceptados):
            anota({"centro": (round(fx), round(fy)), "motivo": "repetido", "med": round(med, 1)})
            continue  # el mismo cuerpo visto desde otro máximo
        cuerpo = Cuerpo("bola", (round(fx, 1), round(fy, 1)), round(fr, 1), round(redondez * cobertura, 3))
        if fr >= p["engranaje_radio_min_mm"] or solo_engranaje:
            if fina is None:
                fina = mascara_blanca(img_mm, p, p["apertura_fina_mm"])
            rug, dientes = _rugosidad(fina, (fx, fy), fr)
            if rug >= rug_min:
                # El borde exterior (punta de los dientes) es algo mayor que el círculo ajustado.
                r_ext = float(np.percentile(_rayos(fina, (fx, fy), fr * 1.3, 360), 75))
                cuerpo = Cuerpo("engranaje", cuerpo.centro_mm, round(max(r_ext, fr), 1), round(min(1.0, rug * 15), 3), dientes)
                aceptados.append((fx, fy, fr))
                engranajes.append((rug + (1.0 if junto_al_previo else 0.0), cuerpo))  # el de antes gana si sigue ahí
                continue
            if solo_engranaje:
                anota({"centro": (round(fx), round(fy)), "motivo": "redondez sin dientes", "med": round(med, 1), "valor": round(redondez, 2)})
                continue
        aceptados.append((fx, fy, fr))
        bolas.append(cuerpo)
    salida = bolas
    if engranajes:
        # Un solo Sol: el más dentado. Los otros candidatos dentados vuelven a ser bolas.
        engranajes.sort(key=lambda e: -e[0])
        sol = engranajes[0][1]
        salida = [sol] + bolas + [Cuerpo("bola", c.centro_mm, c.radio_mm, c.puntaje) for _, c in engranajes[1:]]
    salida.sort(key=lambda c: (c.tipo != "engranaje", -c.radio_mm))
    return salida
