"""Teclado con precisión de tecla: una plantilla ANSI de tamaño completo (104 teclas, unidades de 19,05 mm) se ajusta
al cuadrilátero del teclado en el lienzo y da el polígono de cada tecla (lienzo px, mm, proyector px). Con las puntas
de los dedos (vision_mesa.Hands) se sabe sobre qué tecla está cada dedo.

    python -m playground.teclado_grid captures/dale-XXXX [--quad x0,y0,x1,y1 | --auto] [--img lienzo.jpg]

--auto: caja del teclado desde la última segmentación SAM (reports/scene_regions/<scan>/auto-*/objects.json,
el objeto 'teclado') llevada al lienzo. El teclado de Max (NIBIO, full-size con numpad) está rotado 180° en el lienzo
(Max se sienta al otro lado de la cámara): --rot 180 (por defecto) invierte la plantilla.
Salida: <scan>/teclado.json {quad_lienzo, rot, keys: [{name, poly_lienzo, poly_proj, center_mm}], unit_px}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

U = 19.05  # mm por unidad de tecla

# Filas de la plantilla ANSI 104 (de arriba a abajo), cada tecla (nombre, ancho en U). Los huecos se marcan con ("", w).
ROWS = [
    [("Esc", 1), ("", 1), ("F1", 1), ("F2", 1), ("F3", 1), ("F4", 1), ("", 0.5), ("F5", 1), ("F6", 1), ("F7", 1), ("F8", 1), ("", 0.5),
     ("F9", 1), ("F10", 1), ("F11", 1), ("F12", 1), ("", 0.25), ("PrtSc", 1), ("ScrLk", 1), ("Pause", 1)],
    [("`", 1), ("1", 1), ("2", 1), ("3", 1), ("4", 1), ("5", 1), ("6", 1), ("7", 1), ("8", 1), ("9", 1), ("0", 1), ("-", 1), ("=", 1), ("Backspace", 2),
     ("", 0.25), ("Ins", 1), ("Home", 1), ("PgUp", 1), ("", 0.25), ("NumLk", 1), ("KP/", 1), ("KP*", 1), ("KP-", 1)],
    [("Tab", 1.5), ("Q", 1), ("W", 1), ("E", 1), ("R", 1), ("T", 1), ("Y", 1), ("U", 1), ("I", 1), ("O", 1), ("P", 1), ("[", 1), ("]", 1), ("\\", 1.5),
     ("", 0.25), ("Del", 1), ("End", 1), ("PgDn", 1), ("", 0.25), ("KP7", 1), ("KP8", 1), ("KP9", 1), ("KP+", 1)],
    [("Caps", 1.75), ("A", 1), ("S", 1), ("D", 1), ("F", 1), ("G", 1), ("H", 1), ("J", 1), ("K", 1), ("L", 1), (";", 1), ("'", 1), ("Enter", 2.25),
     ("", 3.5), ("KP4", 1), ("KP5", 1), ("KP6", 1), ("", 1)],
    [("LShift", 2.25), ("Z", 1), ("X", 1), ("C", 1), ("V", 1), ("B", 1), ("N", 1), ("M", 1), (",", 1), (".", 1), ("/", 1), ("RShift", 2.75),
     ("", 1.25), ("Up", 1), ("", 1.25), ("KP1", 1), ("KP2", 1), ("KP3", 1), ("KPEnter", 1)],
    [("LCtrl", 1.25), ("LWin", 1.25), ("LAlt", 1.25), ("Space", 6.25), ("RAlt", 1.25), ("RWin", 1.25), ("Menu", 1.25), ("RCtrl", 1.25),
     ("", 0.25), ("Left", 1), ("Down", 1), ("Right", 1), ("", 0.25), ("KP0", 2), ("KP.", 1), ("", 1)],
]
ROW_Y = [0.0, 1.5, 2.5, 3.5, 4.5, 5.5]        # fila F separada medio U
TOTAL_W, TOTAL_H = 22.5, 6.5                  # unidades


def layout_units() -> list[dict]:
    keys = []
    for r, row in enumerate(ROWS):
        x = 0.0
        for name, w in row:
            if name:
                keys.append({"name": name, "x": x, "y": ROW_Y[r], "w": w, "h": 1.0})
            x += w
    # teclas altas del numpad
    for k in keys:
        if k["name"] == "KP+":
            k["h"] = 2.0
        if k["name"] == "KPEnter":
            k["h"] = 2.0
    return keys


def fit(quad_lienzo: np.ndarray, Hlp: np.ndarray | None, mmpx: float, rot: int = 180, inset: float = 0.012) -> dict:
    """quad: TL TR BR BL del cuerpo del teclado en el lienzo. La plantilla (en U) se mapea con una homografía al
    cuadrilátero (con un pequeño inset: el borde del cuerpo no es tecla)."""
    q = np.asarray(quad_lienzo, np.float32).reshape(4, 2)
    c = q.mean(0); q = c + (q - c) * (1 - 2 * inset)
    if rot % 360 == 180:
        q = q[[2, 3, 0, 1]]
    src = np.array([[0, 0], [TOTAL_W, 0], [TOTAL_W, TOTAL_H], [0, TOTAL_H]], np.float32)
    Hu = cv2.getPerspectiveTransform(src, q)
    keys = []
    for k in layout_units():
        pts = np.array([[k["x"], k["y"]], [k["x"] + k["w"], k["y"]], [k["x"] + k["w"], k["y"] + k["h"]], [k["x"], k["y"] + k["h"]]], np.float32)
        pad = 0.06
        ctr = pts.mean(0); pts = ctr + (pts - ctr) * (1 - pad)
        pl = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), Hu).reshape(4, 2)
        d = {"name": k["name"], "poly_lienzo": pl.round(1).tolist(), "center_lienzo": pl.mean(0).round(1).tolist()}
        if mmpx:
            d["center_mm"] = (pl.mean(0) * mmpx).round(1).tolist()
        if Hlp is not None:
            d["poly_proj"] = cv2.perspectiveTransform(pl.reshape(-1, 1, 2).astype(np.float64), Hlp).reshape(4, 2).round(1).tolist()
        keys.append(d)
    unit_px = float(np.linalg.norm(q[1] - q[0]) / TOTAL_W)
    return {"quad_lienzo": np.asarray(quad_lienzo, float).round(1).tolist(), "rot": rot, "unit_px": round(unit_px, 2),
            "unit_mm_medido": round(unit_px * mmpx, 2) if mmpx else None, "inset": inset, "keys": keys}


def units_homography(quad_lienzo: np.ndarray, rot: int = 180, inset: float = 0.012) -> np.ndarray:
    """La misma homografía que usa `fit`: plantilla en U (origen en la esquina de Esc, x hacia F12, y hacia Space) →
    lienzo px. Su inversa lleva px del lienzo a coordenadas del teclado en U (para el modelo de objetos)."""
    q = np.asarray(quad_lienzo, np.float32).reshape(4, 2)
    c = q.mean(0); q = c + (q - c) * (1 - 2 * inset)
    if rot % 360 == 180:
        q = q[[2, 3, 0, 1]]
    src = np.array([[0, 0], [TOTAL_W, 0], [TOTAL_W, TOTAL_H], [0, TOTAL_H]], np.float32)
    return cv2.getPerspectiveTransform(src, q).astype(np.float64)


def key_at(grid: dict, x: float, y: float) -> str | None:
    """Tecla bajo un punto (px del lienzo)."""
    p = (float(x), float(y))
    for k in grid["keys"]:
        if cv2.pointPolygonTest(np.array(k["poly_lienzo"], np.float32), p, False) >= 0:
            return k["name"]
    return None


def draw(img: np.ndarray, grid: dict, scale: float = 1.0, highlight: set[str] | None = None) -> None:
    for k in grid["keys"]:
        pts = (np.array(k["poly_lienzo"]) * scale).astype(np.int32).reshape(-1, 1, 2)
        on = highlight and k["name"] in highlight
        cv2.polylines(img, [pts], True, (0, 255, 0) if on else (0, 200, 255), 2 if on else 1, cv2.LINE_AA)
        if on or len(k["name"]) <= 2:
            c = pts.reshape(-1, 2).mean(0).astype(int)
            cv2.putText(img, k["name"], (c[0] - 5, c[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1, cv2.LINE_AA)


def refine_quad(img: np.ndarray, prior: np.ndarray, grow: float = 0.25) -> np.ndarray | None:
    """Afina el cuadrilátero del teclado en el lienzo con la textura de las teclas (gradientes densos): dentro de la
    caja previa agrandada, la región texturada más grande → rectángulo rotado mínimo."""
    lh, lw = img.shape[:2]
    c = prior.mean(0); box = c + (prior - c) * (1 + grow)
    x0, y0 = np.clip(box.min(0), 0, [lw - 1, lh - 1]).astype(int); x1, y1 = np.clip(box.max(0), 0, [lw - 1, lh - 1]).astype(int)
    roi = img[y0:y1, x0:x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    # textura de las teclas (gradientes densos) sin la piel de las manos (tono rojizo, saturada, clara)
    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mag = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    tex = cv2.blur(mag, (31, 31))
    skin = (((hsv[..., 0] <= 25) | (hsv[..., 0] >= 170)) & (hsv[..., 1] >= 40) & (hsv[..., 2] >= 90)).astype(np.uint8)
    skin = cv2.dilate(skin, np.ones((31, 31), np.uint8))
    thr = max(12.0, float(np.percentile(tex, 55)))
    m = ((tex > thr) & (skin == 0)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((41, 41), np.uint8)); m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((21, 21), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    if n < 2:
        return None
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    ys, xs = np.nonzero(lab == i)
    (cx, cy), (w, h), ang = cv2.minAreaRect(np.stack([xs, ys], 1).astype(np.float32))
    if w * h < 0.2 * (x1 - x0) * (y1 - y0):
        return None
    long_, short_ = max(w, h), min(w, h)
    # proporción conocida del cuerpo (22,5 x 6,5 U más bisel): el lado largo manda; el corto se corrige si las manos
    # taparon o si se pegó algo
    short_exp = long_ * (6.5 / 22.5) * 1.0
    short_ = short_exp                      # el alto lo manda la proporción conocida del cuerpo
    w, h = (long_, short_) if w >= h else (short_, long_)
    bp = cv2.boxPoints(((cx + x0, cy + y0), (w, h), ang))
    s, dd = bp.sum(1), bp[:, 0] - bp[:, 1]
    return np.array([bp[s.argmin()], bp[dd.argmax()], bp[s.argmax()], bp[dd.argmin()]], np.float32)


def quad_from_sam(scan: Path, lw: int, lh: int, Hlp: np.ndarray) -> np.ndarray | None:
    base = Path("reports/scene_regions") / scan.name
    runs = sorted(base.glob("auto-*/objects.json"), key=lambda p: p.stat().st_mtime)
    if not runs:
        return None
    d = json.loads(runs[-1].read_text()); objs = d.get("objects", d) if isinstance(d, dict) else d
    objs = objs if isinstance(objs, list) else list(objs.values())
    Hinv = np.linalg.inv(Hlp)
    for o in objs:
        if o.get("type") == "keyboard" or "teclado" in str(o.get("name", "")):
            m = cv2.imread(str(runs[-1].parent / o["mask"]), cv2.IMREAD_GRAYSCALE)
            if m is None:
                continue
            ml = cv2.warpPerspective(m, Hinv, (lw, lh), flags=cv2.INTER_NEAREST)
            ys, xs = np.nonzero(ml > 127)
            if len(xs) < 1000:
                continue
            (cx, cy), (w, h), ang = cv2.minAreaRect(np.stack([xs, ys], 1).astype(np.float32))
            box = cv2.boxPoints(((cx, cy), (w, h), ang))
            s, dd = box.sum(1), box[:, 0] - box[:, 1]
            return np.array([box[s.argmin()], box[dd.argmax()], box[s.argmax()], box[dd.argmin()]], np.float32)
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("scan"); p.add_argument("--quad", default=None, help="x0,y0,x1,y1 (caja alineada) o 8 números (4 esquinas) en px del lienzo")
    p.add_argument("--auto", action="store_true"); p.add_argument("--rot", type=int, default=180)
    p.add_argument("--img", default=None, help="imagen del lienzo para dibujar la grilla (debug)")
    a = p.parse_args(argv)
    scan = Path(a.scan)
    sup = json.loads((scan / "superficie.json").read_text())
    lw, lh = (int(v) for v in sup["lienzo_px"]); Hlp = np.array(sup["H_lienzo_to_proj"], np.float64); mmpx = float(sup.get("mm_por_px") or 0)
    if a.quad:
        v = [float(t) for t in a.quad.split(",")]
        quad = np.array([[v[0], v[1]], [v[2], v[1]], [v[2], v[3]], [v[0], v[3]]], np.float32) if len(v) == 4 else np.array(v, np.float32).reshape(4, 2)
    else:
        quad = quad_from_sam(scan, lw, lh, Hlp)
        if quad is None:
            raise SystemExit("no encontré el teclado en la segmentación SAM; pasa --quad")
    img = cv2.imread(a.img) if a.img else None
    if img is not None and not a.quad:
        q2 = refine_quad(img, quad)
        if q2 is not None:
            print("cuadrilátero afinado con la textura de las teclas:", q2.round(0).tolist())
            quad = q2
    grid = fit(quad, Hlp, mmpx, rot=a.rot)
    (scan / "teclado.json").write_text(json.dumps(grid, indent=1))
    print(json.dumps({"quad": grid["quad_lienzo"], "unit_px": grid["unit_px"], "unit_mm": grid["unit_mm_medido"], "teclas": len(grid["keys"])}))
    if img is not None:
        draw(img, grid); cv2.imwrite(str(scan / "teclado_debug.jpg"), img); print("debug:", scan / "teclado_debug.jpg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
