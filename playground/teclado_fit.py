"""Ajuste fino del teclado: rectángulo del cuerpo (máscara SAM o cuadrilátero dado) → plantilla ANSI → refinación por
bordes de teclas (busca el desplazamiento/escala/inset que maximiza el gradiente de la imagen bajo los contornos de
las teclas) → teclado.json + teclado_ref.jpg (referencia del tracker) + recortes de control con 30 px de sangrado.

    python -m playground.teclado_fit captures/dale-XXXX --lienzo captures/dale-XXXX/lienzo_teclado.jpg \
        --mask captures/dale-XXXX/items/<fecha>/ag01_teclado_mask.png [--rot 180] [--save]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from . import teclado_grid as tg


def quad_from_mask(mask: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(mask > 127)
    (cx, cy), (w, h), ang = cv2.minAreaRect(np.stack([xs, ys], 1).astype(np.float32))
    box = cv2.boxPoints(((cx, cy), (w, h), ang)); s_, d_ = box.sum(1), box[:, 0] - box[:, 1]
    return np.array([box[s_.argmin()], box[d_.argmax()], box[s_.argmax()], box[d_.argmin()]], np.float32)


def edge_score(gradmag: np.ndarray, grid: dict, scale: float) -> float:
    """Suma del gradiente bajo los contornos de las teclas (imagen reducida por `scale`)."""
    canvas = np.zeros(gradmag.shape, np.uint8)
    for k in grid["keys"]:
        pts = (np.array(k["poly_lienzo"]) * scale).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(canvas, [pts], True, 255, 1)
    n = max(1, int((canvas > 0).sum()))
    return float(gradmag[canvas > 0].sum()) / n


def adjust_quad(q: np.ndarray, dx: float, dy: float, sx: float, sy: float) -> np.ndarray:
    c = q.mean(0); u = (q[1] - q[0]) / np.linalg.norm(q[1] - q[0]); v = (q[3] - q[0]) / np.linalg.norm(q[3] - q[0])
    d = q - c
    return c + np.outer(d @ u, u) * sx + np.outer(d @ v, v) * sy + u * dx + v * dy


def refine(img: np.ndarray, quad: np.ndarray, Hlp, mmpx: float, rot: int, inset0: float = 0.012) -> tuple[np.ndarray, dict, dict]:
    """Búsqueda en rejilla gruesa→fina de (dx, dy, sx, sy, inset) maximizando el score de bordes."""
    scale = 0.5
    g = cv2.cvtColor(cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    gradmag = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    gradmag = cv2.GaussianBlur(gradmag, (0, 0), 1.2)
    best = (edge_score(gradmag, tg.fit(quad, None, mmpx, rot=rot, inset=inset0), scale), (0.0, 0.0, 1.0, 1.0, inset0))
    base_q = quad.copy()
    for step in (8.0, 3.0, 1.0):
        improved = True
        while improved:
            improved = False
            dx0, dy0, sx0, sy0, in0 = best[1]
            cands = []
            for ddx in (-step, 0, step):
                for ddy in (-step, 0, step):
                    for dsx in (-0.01, 0, 0.01):
                        for dsy in (-0.01, 0, 0.01):
                            cands.append((dx0 + ddx, dy0 + ddy, sx0 + dsx, sy0 + dsy, in0))
            for din in (-0.006, 0.006):
                cands.append((dx0, dy0, sx0, sy0, max(0.0, min(0.05, in0 + din))))
            for c in cands:
                q = adjust_quad(base_q, c[0], c[1], c[2], c[3])
                sc = edge_score(gradmag, tg.fit(q, None, mmpx, rot=rot, inset=c[4]), scale)
                if sc > best[0] + 1e-6:
                    best = (sc, c); improved = True
    dx, dy, sx, sy, ins = best[1]
    qf = adjust_quad(base_q, dx, dy, sx, sy)
    grid = tg.fit(qf, Hlp, mmpx, rot=rot, inset=ins)
    return qf, grid, {"score": round(best[0], 2), "dx": dx, "dy": dy, "sx": sx, "sy": sy, "inset": ins}


def crop_debug(img: np.ndarray, grid: dict, quad: np.ndarray, bleed: int = 30, solid: bool = True) -> np.ndarray:
    x0, y0 = np.floor(quad.min(0)).astype(int) - bleed; x1, y1 = np.ceil(quad.max(0)).astype(int) + bleed
    x0, y0 = max(0, x0), max(0, y0)
    crop = img[y0:y1, x0:x1].copy()
    over = crop.copy()
    for k in grid["keys"]:
        pts = (np.array(k["poly_lienzo"]) - [x0, y0]).astype(np.int32).reshape(-1, 1, 2)
        if solid:
            cv2.fillPoly(over, [pts], (200, 200, 200))
        cv2.polylines(over, [pts], True, (0, 0, 255), 1, cv2.LINE_AA)
        if k["name"] in ("Esc", "Space", "Up", "Left", "Right", "Down", "KP+", "KPEnter", "Enter", "Backspace", "KP0"):
            c = pts.reshape(-1, 2).mean(0).astype(int)
            cv2.putText(over, k["name"], (c[0] - 12, c[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1, cv2.LINE_AA)
    out = cv2.addWeighted(over, 0.45, crop, 0.55, 0)
    cv2.polylines(out, [(quad - [x0, y0]).astype(np.int32)], True, (0, 255, 0), 1)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("scan"); p.add_argument("--lienzo", required=True); p.add_argument("--mask", default=None); p.add_argument("--quad", default=None)
    p.add_argument("--rot", type=int, default=180); p.add_argument("--save", action="store_true"); p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    scan = Path(a.scan); sup = json.loads((scan / "superficie.json").read_text())
    Hlp = np.array(sup["H_lienzo_to_proj"], np.float64); mmpx = float(sup.get("mm_por_px") or 0)
    img = cv2.imread(a.lienzo)
    if a.quad:
        v = [float(t) for t in a.quad.split(",")]; quad = np.array(v, np.float32).reshape(4, 2)
    else:
        quad = quad_from_mask(cv2.imread(a.mask, cv2.IMREAD_GRAYSCALE))
    out = Path(a.out or (scan / "teclado_fit")); out.mkdir(exist_ok=True)
    g0 = tg.fit(quad, Hlp, mmpx, rot=a.rot)
    cv2.imwrite(str(out / "antes.jpg"), crop_debug(img, g0, quad))
    qf, grid, info = refine(img, quad, Hlp, mmpx, a.rot)
    cv2.imwrite(str(out / "despues.jpg"), crop_debug(img, grid, qf))
    print(json.dumps({"quad_sam": quad.round(1).tolist(), "quad_fit": qf.round(1).tolist(), "refino": info, "unit_mm": grid["unit_mm_medido"], "antes": str(out / "antes.jpg"), "despues": str(out / "despues.jpg")}))
    if a.save:
        (scan / "teclado.json").write_text(json.dumps(grid, indent=1))
        cv2.imwrite(str(scan / "teclado_ref.jpg"), img)
        print("guardado teclado.json + teclado_ref.jpg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
