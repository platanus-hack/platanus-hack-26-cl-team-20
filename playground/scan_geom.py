"""Geometría fina de lo real sacada de la foto del escaneo, en px del proyector (1 px = 1 px del T749).

`scene_pov.png` es la mesa vista desde el proyector con luz blanca: cada píxel es la textura de lo que ese píxel del
proyector ilumina. De ahí salen contornos más precisos que los de `segments.json` (que usaba rectángulos de área
mínima y se comían el teclado o la pantalla):

- `buda_outline`: el tapiz del Buda (colores saturados de la pintura, sin el cocodrilo ni los botones del Korg) →
  casco convexo y su cuadrilátero; `buda_head`: centro de la cabeza (para el halo).
- `papel_quad`: la hoja carta blanca sobre la pantalla negra (Otsu dentro de su bbox) → 4 esquinas TL, TR, BR, BL.
- `ddj_layout`: los dos platos como elipses (plantilla de la elipse más limpia buscada por correlación), pads (zonas
  claras bajo cada plato), perillas del mixer (zonas claras entre platos), dos rieles de fader y el cuerpo.
- `torre_faces`: contorno de la torre y el corte entre sus dos caras (`workbench.json`).

Todo se cachea por carpeta y mtime de los archivos; `load()` devuelve None si la carpeta no tiene escaneo (escena
demo o sintética) y los efectos caen a los contornos de la escena.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

W, H = 1920, 1080


@dataclass
class ScanData:
    path: Path
    pov: np.ndarray | None                 # BGR uint8 (H, W, 3) en px del proyector
    height: np.ndarray | None              # relieve relativo float32 (H, W)
    seg: dict
    wb: dict
    keys: dict
    memo: dict = field(default_factory=dict)


_cache: dict[str, ScanData] = {}


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load(scan: str | Path | None) -> ScanData | None:
    """Datos del escaneo (foto, relieve, json) cacheados; None si no hay foto ni segmentos."""
    if not scan:
        return None
    p = Path(scan)
    from .appearance import texture_path
    files = [texture_path(p), p / "segments.json", p / "workbench.json", p / "height_proj.npy"]
    if not files[0].exists() and not files[1].exists():
        return None
    stamp = tuple((str(f), f.stat().st_mtime_ns, f.stat().st_size) if f.exists() else None for f in files)
    key = str(p.resolve())
    sd = _cache.get(key)
    if sd is not None and sd.memo.get("_stamp") == stamp:
        return sd
    pov = cv2.imread(str(files[0]), cv2.IMREAD_COLOR) if files[0].exists() else None
    height = None
    if files[3].exists():
        try:
            height = np.load(files[3]).astype(np.float32)
        except (OSError, ValueError):
            height = None
    sd = ScanData(p, pov, height, _read_json(files[1]), _read_json(files[2]), _read_json(p / "keys.json"))
    sd.memo["_stamp"] = stamp
    _cache[key] = sd
    return sd


def _memo(sd: ScanData, name: str, fn):
    if name not in sd.memo:
        try:
            sd.memo[name] = fn()
        except (cv2.error, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
            sd.memo[name] = None             # foto rara: el efecto cae a los contornos de la escena
    return sd.memo[name]


def _rect(b) -> list[list[float]]:
    x, y, w, h = [float(v) for v in b[:4]]
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def poly_mask(shape, poly, dilate: int = 0) -> np.ndarray:
    m = np.zeros(shape[:2], np.uint8)
    if poly is not None and len(poly) >= 3:
        cv2.fillPoly(m, [np.asarray(poly, float).round().astype(np.int32).reshape(-1, 1, 2)], 1)
        if dilate > 0:
            m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate + 1, 2 * dilate + 1)))
    return m > 0


def order_quad(q) -> list[list[float]]:
    """4 puntos → TL, TR, BR, BL (y hacia abajo)."""
    q = np.asarray(q, float).reshape(-1, 2)
    s, d = q.sum(axis=1), q[:, 0] - q[:, 1]
    return [q[np.argmin(s)].tolist(), q[np.argmax(d)].tolist(), q[np.argmax(s)].tolist(), q[np.argmin(d)].tolist()]


def approx_quad(points) -> list[list[float]] | None:
    """Casco convexo de unos puntos → cuadrilátero (approxPolyDP con epsilon creciente), ordenado."""
    hull = cv2.convexHull(np.asarray(points, np.float32).reshape(-1, 1, 2))
    eps = 0.005 * cv2.arcLength(hull, True)
    for _ in range(80):
        ap = cv2.approxPolyDP(hull, eps, True)
        if len(ap) <= 4:
            break
        eps *= 1.1
    if len(ap) != 4:
        ap = cv2.boxPoints(cv2.minAreaRect(hull))
    return order_quad(np.asarray(ap, float).reshape(-1, 2))


def croc_contour(sd: ScanData):
    c = sd.wb.get("cocodrilo") or sd.seg.get("cocodrilo") or {}
    return c.get("contour")


# ---------------------------------------------------------------------- Buda

def buda_outline(sd: ScanData) -> dict | None:
    """{'outline': casco convexo visible (N vértices), 'quad': 4 esquinas TL TR BR BL} del tapiz del Buda."""
    def calc():
        b = sd.wb.get("buda") or sd.seg.get("buda")
        if sd.pov is None or not b or not b.get("bbox"):
            return None
        bx, by, bw, bh = [int(v) for v in b["bbox"]]
        hsv = cv2.cvtColor(sd.pov, cv2.COLOR_BGR2HSV)
        s, v = hsv[..., 1], hsv[..., 2]
        h_, w_ = s.shape
        win = np.zeros((h_, w_), bool)
        win[max(0, by - 60):min(h_, by + bh), max(0, bx - 80):min(w_, bx + bw + 120)] = True
        croc = croc_contour(sd)
        sat = (s > 80) & (v > 50) & win & ~poly_mask((h_, w_), croc, dilate=7)
        m = cv2.morphologyEx(sat.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        n, lab, st, cen = cv2.connectedComponentsWithStats(m)
        # bajo la mitad del cocodrilo están los botones rojos del Korg: ahí solo cuentan manchas grandes (la túnica)
        cut = by + bh
        if croc:
            cy = np.asarray(croc, float)[:, 1]
            cut = cy.min() + 0.45 * (cy.max() - cy.min())
        keep = [i for i in range(1, n) if st[i, 4] > 400 and (cen[i, 1] < cut or st[i, 4] > 6000)]
        if not keep:
            return None
        ys, xs = np.nonzero(np.isin(lab, keep))
        pts = np.column_stack([xs, ys]).astype(np.int32)
        hull = cv2.convexHull(pts)
        if cv2.contourArea(hull) < 0.25 * bw * bh:
            return None
        outline = cv2.approxPolyDP(hull, 0.004 * cv2.arcLength(hull, True), True).reshape(-1, 2).astype(float)
        return {"outline": outline.tolist(), "quad": approx_quad(hull)}
    return _memo(sd, "buda_outline", calc)


def buda_head(sd: ScanData, poly) -> tuple[float, float] | None:
    """Centro de la cabeza del Buda: lo más claro y saturado de la mitad superior del tapiz (en px del proyector)."""
    if sd.pov is None or poly is None:
        return None
    p = np.asarray(poly, float)
    x0, y0 = np.floor(p.min(axis=0)).astype(int)
    x1, y1 = np.ceil(p.max(axis=0)).astype(int)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(sd.pov.shape[1], x1), min(sd.pov.shape[0], y1)
    if x1 - x0 < 10 or y1 - y0 < 10:
        return None
    hsv = cv2.cvtColor(sd.pov[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    m = poly_mask((y1 - y0, x1 - x0), p - [x0, y0])
    m[(y1 - y0) // 2:, :] = False
    score = hsv[..., 2].astype(np.float32) * (hsv[..., 1] > 40) * m
    if score.max() <= 0:
        return (float((x0 + x1) / 2), float(y0 + (y1 - y0) * 0.35))
    ys, xs = np.nonzero(score >= np.percentile(score[m], 97))
    return (float(xs.mean() + x0), float(ys.mean() + y0))


# ---------------------------------------------------------------------- hoja carta

def papel_quad(sd: ScanData) -> list[list[float]] | None:
    """Las 4 esquinas (TL, TR, BR, BL) de la hoja blanca dentro de la caja de `segments.json`."""
    def calc():
        p = sd.seg.get("papel")
        if sd.pov is None or not p or not p.get("bbox"):
            return None
        x, y, w, h = [int(v) for v in p["bbox"]]
        pad = 20
        x0, y0, x1, y1 = max(0, x - pad), max(0, y - pad), min(sd.pov.shape[1], x + w + pad), min(sd.pov.shape[0], y + h + pad)
        roi = sd.pov[y0:y1, x0:x1]
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        s = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)[..., 1]
        thr, _ = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        wm = ((g > max(thr, 100)) & (s < 70)).astype(np.uint8)
        wm = cv2.morphologyEx(wm, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        wm = cv2.morphologyEx(wm, cv2.MORPH_OPEN, np.ones((15, 15), np.uint8))   # corta los blancos sueltos pegados
        n, lab, st, _ = cv2.connectedComponentsWithStats(wm)
        if n < 2:
            return None
        i = 1 + int(np.argmax(st[1:, 4]))
        if st[i, 4] < 0.12 * w * h:
            return None
        ys, xs = np.nonzero(lab == i)
        q = np.asarray(approx_quad(np.column_stack([xs, ys])), float) + [x0, y0]
        return q.tolist()
    return _memo(sd, "papel_quad", calc)


# ---------------------------------------------------------------------- DDJ

def ellipse_points(cx, cy, a, b, ang_deg, n: int = 48, scale: float = 1.0) -> np.ndarray:
    """Puntos de la elipse de cv2 ((cx, cy), (a, b) ejes completos, ángulo en grados)."""
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ca, sa = math.cos(math.radians(ang_deg)), math.sin(math.radians(ang_deg))
    ex, ey = a / 2 * scale * np.cos(t), b / 2 * scale * np.sin(t)
    return np.column_stack([cx + ex * ca - ey * sa, cy + ex * sa + ey * ca])


def _half_extent_y(a, b, ang_deg) -> float:
    th = math.radians(ang_deg)
    return math.sqrt((a / 2 * math.sin(th)) ** 2 + (b / 2 * math.cos(th)) ** 2)


def _find_jogs(m: np.ndarray, cnts) -> tuple[float, list]:
    """Plantilla = la mancha clara más elíptica (un plato limpio); se busca por correlación (adentro +, anillo -) a
    5 escalas y se quedan los 2 mejores picos separados. Devuelve (puntaje total, [(pico, (a, b, ángulo), (x, y))])."""
    tpl = None
    for c in cnts:
        a = cv2.contourArea(c); _, _, cw, ch = cv2.boundingRect(c)
        if 5000 < a < 45000 and 80 < cw < 300 and 50 < ch < 240 and len(c) >= 5:
            hull = cv2.convexHull(c); el = cv2.fitEllipse(hull)
            fill = cv2.contourArea(hull) / max(1.0, math.pi * el[1][0] * el[1][1] / 4)
            if tpl is None or fill > tpl[0]:
                tpl = (fill, el)
    if tpl is None or tpl[0] < 0.8:
        return 0.0, []
    (_, _), (A, B), ang = tpl[1]
    mf = m.astype(np.float32); cands = []
    for sc in (0.9, 0.95, 1.0, 1.05, 1.1):
        a_, b_ = A * sc, B * sc
        R = int(max(a_, b_) * 0.65) + 2
        inn = np.zeros((2 * R + 1, 2 * R + 1), np.float32); cv2.ellipse(inn, ((R, R), (a_, b_), ang), 1, -1)
        out = np.zeros_like(inn); cv2.ellipse(out, ((R, R), (a_ * 1.25, b_ * 1.25), ang), 1, -1)
        ring = out - inn
        r = cv2.filter2D(mf, -1, inn / inn.sum() - ring / max(1.0, ring.sum()), borderType=cv2.BORDER_CONSTANT)
        for _ in range(3):
            _, mv, _, ml = cv2.minMaxLoc(r)
            cands.append((mv, (a_, b_, ang), ml)); cv2.circle(r, ml, int(min(a_, b_) * 0.6), -9.0, -1)
    cands.sort(key=lambda f: -f[0])
    found = []
    big = max(A, B)
    for mv, axes, ml in cands:
        if mv < 0.25:
            break
        if not found:
            found.append((mv, axes, ml))
            continue
        f = found[0]            # el par: a la misma altura y lado a lado, sin traslaparse (es una DDJ de dos platos)
        if abs(ml[1] - f[2][1]) < 0.35 * big and abs(ml[0] - f[2][0]) > 1.15 * big:
            found.append((mv, axes, ml))
            break
    if len(found) < 2:
        return 0.0, found[:1]
    return (found[0][0] + found[1][0]) * tpl[0], found


def ddj_layout(sd: ScanData) -> dict | None:
    """Platos (elipses), pads, perillas, rieles de fader y cuerpo de la DDJ, desde la foto (px del proyector).

    jogs: [{'c': [x, y], 'axes': [a, b], 'angle': deg}] de izquierda a derecha (a, b = ejes completos de cv2).
    pads / knobs: [{'poly': [[x, y]…], 'deck': 0|1, 'col': 0..3}] · faders: [[[x0, y0], [x1, y1]], …] · body: polígono.
    """
    def calc():
        d = sd.seg.get("ddj")
        if sd.pov is None or not d or not d.get("bbox"):
            return None
        bx, by, bw, bh = [int(v) for v in d["bbox"]]
        x0, y0 = max(0, bx), max(0, by)
        x1, y1 = min(sd.pov.shape[1], bx + bw), min(sd.pov.shape[0], by + bh)
        g = cv2.GaussianBlur(cv2.cvtColor(sd.pov[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY), (0, 0), 2)
        best = None                              # (puntaje, umbral, máscara, contornos, platos)
        for thr in (80.0, 90.0, 100.0, 110.0):
            m = (g > thr).astype(np.uint8)
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
            cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            score, found = _find_jogs(m, cnts)
            if best is None or score > best[0]:
                best = (score, thr, m, cnts, found)
        score, thr, m, cnts, found = best
        jogs = [{"c": [float(ml[0] + x0), float(ml[1] + y0)], "axes": [float(a_), float(b_)], "angle": float(ang)}
                for mv, (a_, b_, ang), ml in found]
        if len(jogs) < 2:                       # respaldo: los círculos de segments.json
            jogs = [{"c": [float(j[0]), float(j[1])], "axes": [2.0 * j[2], 2.0 * j[2]], "angle": 0.0} for j in d.get("jogs", [])[:2]]
        if not jogs:
            return None
        jogs.sort(key=lambda j: j["c"][0])
        # zonas claras: pads bajo cada plato, perillas entre platos
        pads, knobs = [], []
        for c in cnts:
            a = cv2.contourArea(c)
            if not (30 < a < 4000):
                continue
            mo = cv2.moments(c)
            if mo["m00"] <= 0:
                continue
            cx, cy = mo["m10"] / mo["m00"] + x0, mo["m01"] / mo["m00"] + y0
            inside = False
            for j in jogs:
                e = cv2.ellipse2Poly((int(j["c"][0]), int(j["c"][1])), (int(j["axes"][0] * 0.58), int(j["axes"][1] * 0.58)), int(j["angle"]), 0, 360, 10)
                if cv2.pointPolygonTest(e.reshape(-1, 1, 2).astype(np.float32), (cx, cy), False) >= 0:
                    inside = True
            if inside:
                continue
            poly = (cv2.approxPolyDP(c, 1.5, True).reshape(-1, 2) + [x0, y0]).astype(float).tolist()
            near = min(range(len(jogs)), key=lambda i: abs(jogs[i]["c"][0] - cx))
            j = jogs[near]
            below = cy > j["c"][1] + 0.75 * _half_extent_y(j["axes"][0], j["axes"][1], j["angle"])
            if below and abs(cx - j["c"][0]) < 0.8 * max(j["axes"]) and a > 80:
                pads.append({"poly": poly, "deck": near, "c": [cx, cy], "col": 0})
        if len(jogs) == 2:                         # perillas del mixer: marcas claras finas entre los platos
            kx0 = int(jogs[0]["c"][0] + 0.45 * max(jogs[0]["axes"]) - x0)
            kx1 = int(jogs[1]["c"][0] - 0.45 * max(jogs[1]["axes"]) - x0)
            if kx1 - kx0 > 20:
                fine = np.zeros_like(m)
                fine[:, kx0:kx1] = (g[:, kx0:kx1] > thr - 10).astype(np.uint8)
                fine[:max(0, int(min(j["c"][1] for j in jogs) - y0 - 0.8 * max(jogs[0]["axes"]) / 2)), :] = 0
                fine = cv2.morphologyEx(fine, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
                for c in cv2.findContours(fine, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
                    a = cv2.contourArea(c)
                    mo = cv2.moments(c)
                    if 12 < a < 900 and mo["m00"] > 0:
                        knobs.append({"poly": (c.reshape(-1, 2) + [x0, y0]).astype(float).tolist(),
                                      "c": [mo["m10"] / mo["m00"] + x0, mo["m01"] / mo["m00"] + y0]})
        for deck in range(len(jogs)):              # columnas 0..3 por posición x dentro de cada lado
            ps = [p for p in pads if p["deck"] == deck]
            if ps:
                xs = [p["c"][0] for p in ps]; lo, hi = min(xs), max(xs)
                for p in ps:
                    p["col"] = int(min(3, (p["c"][0] - lo) / max(1.0, hi - lo) * 4)) if hi > lo else 0
        # rieles de fader: el eje del mixer es la orientación dominante de los bordes entre los platos (tensor de
        # estructura: las líneas largas del mixer); dos rieles paralelos desde un poco bajo los platos hasta el borde
        faders, axis = [], np.array([0.0, 1.0])
        if len(jogs) == 2:
            L, R = np.asarray(jogs[0]["c"]), np.asarray(jogs[1]["c"])
            zx0 = int(max(0, L[0] + 0.5 * max(jogs[0]["axes"]) - x0)); zx1 = int(min(g.shape[1], R[0] - 0.5 * max(jogs[1]["axes"]) - x0))
            zy0 = int(max(0, min(L[1], R[1]) - y0 - 0.3 * max(jogs[0]["axes"])))
            if zx1 - zx0 > 20 and g.shape[0] - zy0 > 20:
                z = g[zy0:, zx0:zx1].astype(np.float32)
                gx_, gy_ = cv2.Sobel(z, cv2.CV_32F, 1, 0, ksize=5), cv2.Sobel(z, cv2.CV_32F, 0, 1, ksize=5)
                jxx, jyy, jxy = float((gx_ * gx_).sum()), float((gy_ * gy_).sum()), float((gx_ * gy_).sum())
                th = 0.5 * math.atan2(2 * jxy, jxx - jyy)       # dirección del gradiente dominante
                axis = np.array([-math.sin(th), math.cos(th)])   # las líneas van perpendiculares al gradiente
                if axis[1] < 0:
                    axis = -axis
                if axis[1] < 0.5:                                 # casi horizontal: no es el mixer, vertical
                    axis = np.array([0.0, 1.0])
            mid = (L + R) / 2
            ext = 0.55 * max(_half_extent_y(*jogs[0]["axes"], jogs[0]["angle"]), _half_extent_y(*jogs[1]["axes"], jogs[1]["angle"]))
            start = mid + axis * (ext / axis[1])
            end_t = (y1 - 6 - start[1]) / axis[1]
            perp = np.array([-axis[1], axis[0]])
            gap = 0.09 * abs(R[0] - L[0])
            if end_t > 15:
                for s_ in (-1, 1):
                    p0 = start + perp * gap * s_
                    faders.append([p0.tolist(), (p0 + axis * end_t).tolist()])
        pts = np.vstack([ellipse_points(j["c"][0], j["c"][1], j["axes"][0], j["axes"][1], j["angle"], 36, 1.3) for j in jogs]
                        + [np.asarray(p["poly"], float) for p in pads] + [np.asarray(k["poly"], float) for k in knobs])
        pts[:, 0] = np.clip(pts[:, 0], x0, x1); pts[:, 1] = np.clip(pts[:, 1], y0, y1)
        body = cv2.convexHull(pts.astype(np.float32).reshape(-1, 1, 2)).reshape(-1, 2).astype(float).tolist()
        return {"jogs": jogs, "pads": pads, "knobs": knobs, "faders": faders, "body": body,
                "axis": axis.tolist(), "roi": [x0, y0, x1 - x0, y1 - y0], "thr": thr}
    return _memo(sd, "ddj_layout", calc)


# ---------------------------------------------------------------------- torre

CARAS_TORRE = Path(__file__).parent / "torre_caras.json"     # caras medidas con la cámara, por escaneo


def torre_faces(sd: ScanData) -> dict | None:
    """{'contour', 'split_y', 'faces'}: si `playground/torre_caras.json` trae las caras reales del PC para este escaneo
    (marcadas en la Osmo y llevadas al proyector), faces = {'arriba', 'lado', 'frente': polígono}; si no, el contorno
    y el corte de `workbench.json` (faces = None, el efecto parte por split_y)."""
    def calc():
        over = _read_json(CARAS_TORRE).get(sd.path.name) or {}
        faces = {k: [[float(x), float(y)] for x, y in v] for k, v in (over.get("caras") or {}).items() if len(v) >= 3} or None
        if faces:
            allp = np.vstack([np.asarray(v, np.float32) for v in faces.values()])
            hull = cv2.convexHull(allp.reshape(-1, 1, 2)).reshape(-1, 2).astype(float)
            top = np.asarray(faces.get("arriba") or hull, float)
            return {"contour": hull.tolist(), "split_y": float(top[:, 1].max()), "faces": faces}
        t = sd.wb.get("torre") or {}
        contour = t.get("contour")
        if not contour:
            s = sd.seg.get("torre") or t
            if not s.get("bbox"):
                return None
            contour = _rect(s["bbox"])
        c = np.asarray(contour, float)
        split = t.get("split_y")
        if split is None:
            split = float(c[:, 1].min() + 0.25 * (c[:, 1].max() - c[:, 1].min()))
        return {"contour": c.tolist(), "split_y": float(split), "faces": None}
    return _memo(sd, "torre_faces", calc)


def relief(sd: ScanData | None, x0: int, y0: int, w: int, h: int, mask: np.ndarray | None = None, blur: float = 25.0) -> np.ndarray:
    """Relieve suave 0..1 de una región: plano robusto (sin las manchas sueltas del gray code, fuera de 3 MAD) más el
    residuo difuminado (curvatura grande). Así las ondas siguen la forma de la cara y no el ruido del escaneo."""
    if sd is None or sd.height is None:
        return np.repeat(np.linspace(0, 1, h, dtype=np.float32)[:, None], w, axis=1)
    hh = np.zeros((h, w), np.float32)
    a0, b0 = max(0, x0), max(0, y0)
    a1, b1 = min(sd.height.shape[1], x0 + w), min(sd.height.shape[0], y0 + h)
    if a1 > a0 and b1 > b0:
        hh[b0 - y0:b1 - y0, a0 - x0:a1 - x0] = sd.height[b0:b1, a0:a1]
    m = mask if mask is not None and mask.any() else np.ones((h, w), bool)
    vals = hh[m]
    med = float(np.median(vals)); mad = float(np.median(np.abs(vals - med))) * 1.4826 + 1e-3
    good = m & (np.abs(hh - med) < 3 * mad)
    if good.sum() < 20:
        good = m
    ys, xs = np.nonzero(good)
    step = max(1, len(xs) // 5000)
    A = np.column_stack([xs[::step], ys[::step], np.ones(len(xs[::step]))]).astype(np.float64)
    coef, *_ = np.linalg.lstsq(A, hh[ys[::step], xs[::step]].astype(np.float64), rcond=None)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    plane = (coef[0] * xx + coef[1] * yy + coef[2]).astype(np.float32)
    gf = good.astype(np.float32)
    res = cv2.GaussianBlur((hh - plane) * gf, (0, 0), blur) / np.maximum(cv2.GaussianBlur(gf, (0, 0), blur), 1e-3)
    surf = plane + res
    sv = surf[m]
    lo, hi = np.percentile(sv, 2), np.percentile(sv, 98)
    return np.clip((surf - lo) / max(1e-3, hi - lo), 0, 1).astype(np.float32)


# ---------------------------------------------------------------------- ayudas para efectos

def scene_scan(scene) -> ScanData | None:
    """El escaneo de una escena en px del proyector (scene_from_scan); None en la demo en mm o sin foto."""
    if not getattr(scene, "projector_px", False):
        return None
    return load(getattr(scene, "scan_dir", None))


def offset_of(obj) -> tuple[float, float]:
    """Ajuste fino (nudge) acumulado de un objeto: lo real quedó (dx, dy) px corrido respecto del escaneo."""
    a = (getattr(obj, "extra", None) or {}).get("ajuste") or (0.0, 0.0)
    return float(a[0]), float(a[1])


def shifted(poly, dx: float, dy: float) -> list[list[float]]:
    return [[float(x) + dx, float(y) + dy] for x, y in poly]


def roi_of(poly, margin: float, size=(W, H)) -> tuple[int, int, int, int] | None:
    """Caja (x0, y0, w, h) entera de un polígono con margen, recortada al lienzo del escaneo."""
    p = np.asarray(poly, float)
    x0, y0 = np.floor(p.min(axis=0) - margin).astype(int)
    x1, y1 = np.ceil(p.max(axis=0) + margin).astype(int)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(size[0], x1), min(size[1], y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


def texture(sd: ScanData | None, x0: int, y0: int, w: int, h: int, dx: float = 0.0, dy: float = 0.0) -> np.ndarray | None:
    """Recorte de la foto (BGR uint8) para dibujar en (x0, y0, w, h): si el objeto se corrió (dx, dy) con nudge, la
    textura se toma de donde estaba en el escaneo. Bordes fuera de la foto se rellenan replicando."""
    if sd is None or sd.pov is None:
        return None
    sx, sy = int(round(x0 - dx)), int(round(y0 - dy))
    ph, pw = sd.pov.shape[:2]
    a0, b0, a1, b1 = max(0, sx), max(0, sy), min(pw, sx + w), min(ph, sy + h)
    if a1 <= a0 or b1 <= b0:
        return None
    crop = sd.pov[b0:b1, a0:a1]
    if crop.shape[:2] != (h, w):
        crop = cv2.copyMakeBorder(crop, b0 - sy, sy + h - b1, a0 - sx, sx + w - a1, cv2.BORDER_REPLICATE)
    return crop


def feather(mask: np.ndarray, px: float) -> np.ndarray:
    """Máscara booleana → alfa float32 0..1 con borde suave de ~px hacia adentro (no se sale del contorno)."""
    m = mask.astype(np.float32)
    if px <= 0.5:
        return m
    k = int(round(px))
    inner = cv2.erode(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    return np.minimum(m, cv2.GaussianBlur(inner.astype(np.float32), (0, 0), 0.6 * px))


def blit(canvas, x0: float, y0: float, layer: np.ndarray, alpha: np.ndarray | None = None, mode: str = "add") -> None:
    """Pega una capa calculada en px de la escena (esquina x0, y0) en el lienzo: uint8 BGR o float32 0..1, alfa
    opcional (h, w) 0..1. mode 'add' (luz que se suma, lo normal al proyectar), 'over' (tapa) o 'max'.
    Con el lienzo del runtime real (1 px = 1 px) es un pegado directo; en lienzos chicos (tests) se escala."""
    h, w = layer.shape[:2]
    if h == 0 or w == 0:
        return
    (cx0, cy0), (cx1, cy1) = canvas.project(x0, y0), canvas.project(x0 + w, y0 + h)
    if cy1 < cy0:                                  # lienzo con y hacia arriba (demo en mm)
        cy0, cy1 = cy1, cy0
        layer = layer[::-1]; alpha = alpha[::-1] if alpha is not None else None
    dw, dh = cx1 - cx0, cy1 - cy0
    if dw <= 0 or dh <= 0:
        return
    if (dw, dh) != (w, h):
        layer = cv2.resize(np.ascontiguousarray(layer), (dw, dh), interpolation=cv2.INTER_AREA if dw < w else cv2.INTER_LINEAR)
        if alpha is not None:
            alpha = cv2.resize(np.ascontiguousarray(alpha), (dw, dh), interpolation=cv2.INTER_LINEAR)
    ih, iw = canvas.img.shape[:2]
    ax0, ay0, ax1, ay1 = max(0, cx0), max(0, cy0), min(iw, cx0 + dw), min(ih, cy0 + dh)
    if ax1 <= ax0 or ay1 <= ay0:
        return
    sub = layer[ay0 - cy0:ay1 - cy0, ax0 - cx0:ax1 - cx0]
    a = alpha[ay0 - cy0:ay1 - cy0, ax0 - cx0:ax1 - cx0] if alpha is not None else None
    dst = canvas.img[ay0:ay1, ax0:ax1]
    if sub.dtype != np.uint8:
        f = sub if a is None else sub * a[..., None]
        if mode == "over":
            dst[:] = np.clip(dst.astype(np.float32) * (1 - (a[..., None] if a is not None else 1.0)) + f * 255, 0, 255).astype(np.uint8)
            return
        sub8 = np.clip(f * 255, 0, 255).astype(np.uint8)
    else:
        sub8 = sub if a is None else (sub.astype(np.float32) * a[..., None]).astype(np.uint8)
        if mode == "over":
            af = a[..., None] if a is not None else 1.0
            dst[:] = (dst.astype(np.float32) * (1 - af) + sub.astype(np.float32) * af).astype(np.uint8)
            return
    if mode == "max":
        np.maximum(dst, sub8, out=dst)
    else:
        dst[:] = cv2.add(dst, sub8)
