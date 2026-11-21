"""Segmentación heurística de la escena en el espacio del proyector (sin SAM todavía).

    python -m playground.segment_pov captures/scan2

Entrada: <scan>/scene_pov.png, <scan>/proj_to_cam.npz, <scan>/osmo/color.png, <scan>/osmo/map.npz
Salida: <scan>/segments.json, <scan>/segments_overlay.png, <scan>/height_proj.npy

Objetos: buda (cuadro, tonos cálidos → cuadrilátero), torre (blanco + altura, tercio izquierdo), papel (hoja carta
blanca), teclado (banda de bordes verticales → teclas por periodicidad, largo real), controladores, ddj (jogs por
círculos), circuit, cocodrilo (GrabCut en la foto de la cámara, inicializado por color en la franja sobre las teclas).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.interpolate import griddata

from slscan.graycode import Decoded
from slscan import recon

W, H = 1920, 1080


def biggest(mask, min_area=2000, k=15):
    m = cv2.morphologyEx(mask.astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(m)
    if n < 2:
        return None, None
    k0 = int(np.argsort(-st[1:, cv2.CC_STAT_AREA])[0] + 1)
    if st[k0, cv2.CC_STAT_AREA] < min_area:
        return None, None
    return lab == k0, st[k0]


def quad_of(mask):
    cnt = max(cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
    return cv2.boxPoints(cv2.minAreaRect(cnt)).tolist()


def height_map(scan: Path):
    dec = Decoded.load(scan / "osmo" / "map.npz"); m = recon.coalesce(dec)
    pro, cam = m.pro.astype(np.float64), m.cam.astype(np.float64)
    Hm, mask = cv2.findHomography(pro.astype(np.float32), cam.astype(np.float32), cv2.RANSAC, 3.0)
    pred = cv2.perspectiveTransform(pro.reshape(-1, 1, 2).astype(np.float32), Hm).reshape(-1, 2)
    res = cam - pred; inl = mask.ravel() == 1
    u, s, vt = np.linalg.svd(res[~inl] - res[~inl].mean(axis=0), full_matrices=False) if (~inl).sum() > 10 else (None, None, np.eye(2))
    par = res @ vt[0]; par -= np.median(par[inl])
    if np.percentile(par, 95) < -np.percentile(par, 5):
        par = -par
    gx, gy = np.meshgrid(np.arange(W), np.arange(H))
    hmap = np.nan_to_num(griddata(pro, par, (gx, gy), method="linear"), nan=0.0).astype(np.float32)
    hmap = cv2.GaussianBlur(hmap, (0, 0), 4)
    np.save(scan / "height_proj.npy", hmap)
    return hmap, m


def segment(scan: Path) -> dict:
    pov = cv2.imread(str(scan / "scene_pov.png"))
    hsv = cv2.cvtColor(pov, cv2.COLOR_BGR2HSV); hval, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    gray = cv2.cvtColor(pov, cv2.COLOR_BGR2GRAY)
    valid = pov.sum(axis=2) > 0
    height, m = height_map(scan)
    seg = {}
    # Buda
    warm = ((hval < 25) | (hval > 160)) & (sat > 70) & (val > 60) & valid
    mk, st = biggest(warm, 20000, 41)
    if mk is not None:
        seg["buda"] = {"bbox": [int(v) for v in st[:4]], "quad": quad_of(mk)}
    # torre
    white = (sat < 60) & (val > 170) & valid
    left = np.zeros_like(white); left[150:, 120: W // 3] = True
    mm = cv2.morphologyEx((white & left).astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((15, 15), np.uint8))
    mm = cv2.morphologyEx(mm, cv2.MORPH_CLOSE, np.ones((31, 31), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(mm)
    cands = [i for i in range(1, n) if st[i, 3] > 350 and st[i, 2] > 120 and st[i, 3] > 1.2 * st[i, 2]]
    if cands:
        i = max(cands, key=lambda j: st[j, 4]); seg["torre"] = {"bbox": [int(v) for v in st[i, :4]]}
    # papel: blanco, rectángulo vertical, ventana a la derecha del Buda
    bx = seg["buda"]["bbox"][0] + seg["buda"]["bbox"][2] if "buda" in seg else W // 2
    win = np.zeros((H, W), bool); win[100:620, bx + 60: min(W, bx + 700)] = True
    # la hoja es un rectángulo blanco LLENO (relleno > 0,85 de su bbox) de proporción carta (alto/ancho ~1,29);
    # con umbral laxo y cierre grande se pegaba al monitor y a la pared (salía de 391x506 en scan5)
    pw = (sat < 60) & (val > 190) & win       # la hoja sale con tinte cian del proyector: sat 40-55, val 200-240
    mm = cv2.morphologyEx(pw.astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mm = cv2.morphologyEx(mm, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(mm)
    best, best_score = None, 0.0
    for i in range(1, n):
        x, y, w, h, a = st[i]
        fill = a / float(max(w * h, 1))
        if a > 8000 and w > 80 and h > 100 and 1.0 < h / w < 1.6 and fill > 0.6:
            score = fill * min(1.0, a / 60000.0)
            if score > best_score:
                best, best_score = i, score
    if best is not None:
        seg["papel"] = {"bbox": [int(v) for v in st[best, :4]], "quad": quad_of(lab == best), "mm": [215.9, 279.4]}
    # teclado: banda de bordes verticales densos
    sob = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)); edges = (sob > 60).astype(np.float32)
    rowscore = cv2.blur(edges, (61, 21)); band = rowscore > np.percentile(rowscore, 97)
    mk, st = biggest(band, 20000, 25)
    if mk is not None:
        kx, ky, kw, kh = [int(v) for v in st[:4]]
        # extender a lo ancho: columnas con alternancia en la fila central de la banda
        row = gray[ky:ky + kh, :]; prof = (row[kh // 2:, :] > 140).astype(np.float32).mean(axis=0)
        dens = cv2.blur(prof.reshape(1, -1), (1, 101)).ravel()
        cols = np.nonzero(dens > 0.3)[0]; cols = cols[(cols > kx - 60) & (cols < kx + kw + 60)]
        kx0, kx1 = (int(cols.min()), int(cols.max())) if len(cols) else (kx, kx + kw)
        p = prof[kx0:kx1] - prof[kx0:kx1].mean(); F = np.abs(np.fft.rfft(p)); F[:5] = 0
        k = int(np.argmax(F)); pitch = (kx1 - kx0) / max(1, k)
        if not (18 < pitch < 70):
            pitch = 34.0; k = int((kx1 - kx0) / pitch)
        best_o = max(range(int(pitch)), key=lambda o: sum(prof[kx0 + int(o + i * pitch):kx0 + int(o + i * pitch + pitch * 0.55)].mean() for i in range(k) if kx0 + o + (i + 0.55) * pitch < kx1))
        keys = [(int(kx0 + best_o + i * pitch), ky, int(pitch * 0.88), kh) for i in range(k + 1) if kx0 + best_o + (i + 0.88) * pitch <= kx1 + 4]
        top = gray[ky:ky + kh // 2, kx0:kx1]; bk = (top < 75).astype(np.uint8); n2, lab2, st2, _ = cv2.connectedComponentsWithStats(bk)
        blacks = sorted([(int(st2[i, 0] + kx0), ky, int(st2[i, 2]), int(st2[i, 3])) for i in range(1, n2) if st2[i, 3] > kh * 0.25 and 4 < st2[i, 2] < pitch])
        seg["teclado"] = {"bbox": [kx0, ky, kx1 - kx0, kh], "pitch_px": round(float(pitch), 2), "teclas_blancas": keys, "n_blancas": len(keys), "teclas_negras": blacks, "n_negras": len(blacks)}
    # controladores: oscuro abajo
    dark = (val < 70) & valid; bottom = np.zeros_like(dark); bottom[int(H * 0.55):, :] = True
    mk, st = biggest(dark & bottom, 30000, 31)
    if mk is not None:
        seg["controladores"] = {"bbox": [int(v) for v in st[:4]]}
    # DDJ: jogs (círculos) a la derecha del centro, bajo el teclado
    if "teclado" in seg and "controladores" in seg:
        kx0, ky, kw, kh = seg["teclado"]["bbox"]; cx0, cy0, cw, ch = seg["controladores"]["bbox"]
        # la DDJ puede estar en cualquier parte bajo el teclado (Max la mueve): se buscan círculos en todo el ancho
        # de los controladores y se elige el par de radios parecidos, a la misma altura y separados entre 1,8 y 4 radios
        rx0, ry0 = cx0, ky + kh
        roi = cv2.medianBlur(gray[ry0:cy0 + ch, rx0:cx0 + cw], 5)
        circles = cv2.HoughCircles(roi, cv2.HOUGH_GRADIENT, dp=1.2, minDist=150, param1=110, param2=35, minRadius=55, maxRadius=130)
        jogs = [] if circles is None else [[int(c[0] + rx0), int(c[1] + ry0), int(c[2])] for c in circles[0][:10]]
        pair = None; best_s = 1e9
        for i in range(len(jogs)):
            for j in range(i + 1, len(jogs)):
                a, b = jogs[i], jogs[j]; r = (a[2] + b[2]) / 2; d = abs(a[0] - b[0])
                if 1.8 * r < d < 4.0 * r and abs(a[2] - b[2]) < 0.25 * r:
                    sc = abs(a[1] - b[1]) + 3 * abs(a[2] - b[2]) - 0.5 * (a[2] + b[2])
                    if sc < best_s:
                        best_s = sc; pair = (a, b)
        if pair:
            jl, jr = sorted(pair); rr = int(1.6 * max(jl[2], jr[2]))
            rx0 = max(cx0, jl[0] - rr); rx1 = min(cx0 + cw, jr[0] + rr)
            seg["ddj"] = {"jogs": [jl, jr], "bbox": [rx0, ry0, rx1 - rx0, cy0 + ch - ry0]}
        else:
            rx0 = max(cx0, W // 2 + 40)
            seg["ddj"] = {"jogs": jogs[:2], "bbox": [rx0, ry0, cx0 + cw - rx0, cy0 + ch - ry0]}
        # a la izquierda de la DDJ está el OHM RGB (grilla gris con faders); el Circuit Tracks es el cuadrado chico
        # oscuro a la derecha de la DDJ, un componente aparte del bloque grande de controladores
        if rx0 - cx0 > 120:
            seg["ohm"] = {"bbox": [cx0, ry0, rx0 - cx0, cy0 + ch - ry0]}
        # el Circuit suele quedar unido al bloque por cables y la mezcladora: se erosiona la máscara oscura a la
        # derecha del segundo plato y se busca el componente más cuadrado
        jx = max((j[0] + j[2] for j in seg["ddj"]["jogs"]), default=rx0) + 40
        rest = (dark & bottom).astype(np.uint8); rest[:, :jx] = 0; rest[:ky, :] = 0
        rest = cv2.erode(rest, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17)))
        n3, lab3, st3, _ = cv2.connectedComponentsWithStats(rest, 8)
        cands = []
        for i in range(1, n3):
            x, y, w, h, area = [int(v) for v in st3[i]]
            if 8000 < area < 120000 and 0.6 < w / max(h, 1) < 1.7:
                cands.append((abs(w / h - 1.0), [x - 8, y - 8, w + 16, h + 16]))
        if cands:
            seg["circuit"] = {"bbox": sorted(cands)[0][1]}
    # cocodrilo: GrabCut en la foto de la cámara, inicializado por color en la franja sobre las teclas (en POV)
    if "teclado" in seg:
        kx0, ky, kw, kh = seg["teclado"]["bbox"]
        # franja sobre las teclas: en el EK-50 el cocodrilo queda sobre el panel trasero, más arriba que en el teclado
        # anterior (hasta ~290 px sobre las teclas); el panel con botones (bajo) se excluye con el margen inferior
        zone = np.zeros((H, W), bool); zone[max(0, ky - 290):ky - 40, max(0, kx0 - 80):min(W, kx0 + kw + 80)] = True
        # el cocodrilo es verde-cian desaturado (hue 60–120, sat 25–100, val 105–220 en scan5); el panel del Korg es
        # magenta oscuro (hue ~160, val ~100) y las partes claras del Buda tienen hue ~145 y sat < 30
        croc = (hval >= 60) & (hval <= 120) & (sat >= 25) & (sat <= 100) & (val >= 105) & (val <= 220) & zone
        mm = cv2.morphologyEx(croc.astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        mm = cv2.morphologyEx(mm, cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8))
        n, lab, st, cen = cv2.connectedComponentsWithStats(mm)
        cands = [i for i in range(1, n) if st[i, 4] > 4000 and st[i, 2] > 1.8 * st[i, 3]]
        if cands:
            i = max(cands, key=lambda j: st[j, 4]); init = (lab == i)
            # GrabCut en POV (la textura está ahí): probable fg = init dilatado, fg seguro = init erosionado
            gc = np.full((H, W), cv2.GC_BGD, np.uint8)
            x, y, w, h = [int(v) for v in st[i, :4]]; pad = 40
            gc[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad] = cv2.GC_PR_BGD
            gc[cv2.dilate(init.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0] = cv2.GC_PR_FGD
            gc[cv2.erode(init.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0] = cv2.GC_FGD
            # fondo seguro: colores saturados (túnica del Buda, botones) y lo oscuro (panel); el resultado no puede
            # salirse de la vecindad de la máscara de color (GrabCut tendía a comerse la túnica y el panel)
            gc[((sat > 120) | (val < 80)) & (gc != cv2.GC_FGD)] = cv2.GC_BGD
            near = cv2.dilate(init.astype(np.uint8), np.ones((31, 31), np.uint8)) > 0
            bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
            try:
                cv2.grabCut(pov, gc, None, bgd, fgd, 4, cv2.GC_INIT_WITH_MASK)
                fg = ((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)) & near
                fg = cv2.morphologyEx(fg.astype(np.uint8), cv2.MORPH_OPEN, np.ones((7, 7), np.uint8)) > 0
                fg = cv2.morphologyEx(fg.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8)) > 0
                mk2, st2 = biggest(fg, 3000, 9)
                if mk2 is not None:
                    init = mk2; st_i = st2
                else:
                    st_i = st[i]
            except cv2.error:
                st_i = st[i]
            # misma franja que la zona de búsqueda (antes se recortaba a 150 px y perdía el lomo del cocodrilo)
            strip = np.zeros((H, W), bool); strip[max(0, ky - 290):ky - 30, max(0, kx0 - 40):kx0 + kw + 40] = True
            init = init & strip
            mk3, st3 = biggest(init, 2000, 9)
            if mk3 is not None:
                cnt = max(cv2.findContours(mk3.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
                cnt = cv2.approxPolyDP(cnt, 2.5, True).reshape(-1, 2)
                seg["cocodrilo"] = {"bbox": [int(v) for v in st3[:4]], "contour": cnt.tolist()}
    json.dump(seg, open(scan / "segments.json", "w"), indent=1)
    over = pov.copy()
    cols = {"buda": (0, 140, 255), "torre": (255, 200, 0), "papel": (255, 255, 255), "teclado": (0, 255, 0), "controladores": (255, 0, 255), "ddj": (255, 0, 255), "circuit": (0, 200, 200), "ohm": (200, 120, 255), "cocodrilo": (0, 255, 255)}
    for k, v in seg.items():
        x, y, w, h = v["bbox"]; cv2.rectangle(over, (x, y), (x + w, y + h), cols[k], 2); cv2.putText(over, k, (x + 5, y - 8 if y > 30 else y + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, cols[k], 2)
        if "quad" in v:
            cv2.polylines(over, [np.array(v["quad"], np.int32).reshape(-1, 1, 2)], True, cols[k], 2)
        if "contour" in v:
            cv2.polylines(over, [np.array(v["contour"], np.int32).reshape(-1, 1, 2)], True, cols[k], 3)
        for kb in v.get("teclas_blancas", []):
            cv2.rectangle(over, (kb[0], kb[1]), (kb[0] + kb[2], kb[1] + kb[3]), (0, 255, 0), 1)
        for kb in v.get("teclas_negras", []):
            cv2.rectangle(over, (kb[0], kb[1]), (kb[0] + kb[2], kb[1] + kb[3]), (0, 0, 255), 2)
        for cx, cy, r in v.get("jogs", []):
            cv2.circle(over, (cx, cy), r, (255, 0, 255), 3)
    cv2.imwrite(str(scan / "segments_overlay.png"), over)
    return seg


if __name__ == "__main__":
    scan = Path(sys.argv[1] if len(sys.argv) > 1 else "captures/scan5")
    s = segment(scan)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk not in ("teclas_blancas", "teclas_negras", "contour", "quad")} for k, v in s.items()}))
