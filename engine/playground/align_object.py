"""Protocolo de alineación por bordes para cualquier objeto con textura (Buda, DDJ, OHM, hoja…): «proyecta la imagen
invertida o los edges, corre un poquito y compara cuál queda mejor», como pidió Max.

1) Foto con luz blanca (overlay) → bordes reales del objeto en la cámara (Canny dentro de su zona).
2) Para cada corrimiento (dx, dy) de una grilla gruesa y luego fina: proyecta SOLO los bordes de la foto del objeto
   (de scene_pov, en píxeles del proyector) corridos; captura; lo iluminado se compara con los bordes reales:
   puntaje = fracción de píxeles iluminados que caen sobre bordes reales (dilatados 3 px).
3) El mejor corrimiento se aplica con `nudge` (queda en ajustes.json) y se muestra 6 s la foto alineada.

    python -m playground.align_object --objeto buda                # grilla ±30 paso 10, fina paso 3
    python -m playground.align_object --objeto ddj --radio 20 --paso 5 --sin-aplicar
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from playground.croc_refine import RuntimeWindow, nudge_runtime
from playground.croc_resegment import camera_drift

W, H = 1920, 1080
URL = "http://127.0.0.1:8790"


def scene_object(name: str) -> dict:
    with urllib.request.urlopen(URL + "/api/state", timeout=5) as r:
        st = json.loads(r.read())
    o = st["scene"]["objects"].get(name)
    if o is None:
        raise SystemExit(f"no hay objeto {name}; hay {list(st['scene']['objects'])}")
    return o


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--objeto", default="buda"); p.add_argument("--scan", default="captures/scan5")
    p.add_argument("--radio", type=int, default=30); p.add_argument("--paso", type=int, default=10); p.add_argument("--fino", type=int, default=3)
    p.add_argument("--sin-aplicar", action="store_true"); p.add_argument("--guardar", action="store_true"); p.add_argument("--rotate", type=int, default=270)
    a = p.parse_args(argv)
    scan = Path(a.scan); out = Path("reports/_crit"); out.mkdir(parents=True, exist_ok=True)
    obj = scene_object(a.objeto); adj = obj["extra"].get("ajuste") or [0.0, 0.0]
    # geometría SIN el ajuste actual (la foto del escaneo está en coordenadas sin ajustar): se busca el ajuste total
    poly = np.array(obj["contour"], np.float32) - np.array(adj, np.float32)
    pov = cv2.imread(str(scan / "scene_pov.png")); z = np.load(scan / "proj_to_cam.npz"); mapx, mapy, valid = z["mapx"], z["mapy"], z["valid"]
    ref = cv2.imread(str(scan / "osmo" / "color.png"))
    mask = np.zeros((H, W), np.uint8); cv2.fillPoly(mask, [np.round(poly).astype(np.int32)], 255)
    gray = cv2.cvtColor(pov, cv2.COLOR_BGR2GRAY); edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 60, 140); edges[mask == 0] = 0
    edges = cv2.dilate(edges, np.ones((2, 2), np.uint8))
    x, y, w, h = cv2.boundingRect(np.round(poly).astype(np.int32))
    from slscan.capture import Camera
    win = RuntimeWindow(); cam = Camera("osmo", size="3840x2160", rotate=a.rotate, downscale=2)
    rep = {"objeto": a.objeto, "ajuste_previo": adj, "pruebas": []}
    try:
        win.show(np.full((H, W, 3), 255, np.uint8), 30); time.sleep(2.0); cam.flush(12); white = cam.read()
        win.show(np.zeros((H, W, 3), np.uint8), 30); time.sleep(1.3); cam.flush(10); dark = cam.read()
        if white.mean() - dark.mean() < 15:
            print("la proyección no se ve en la cámara: no se toca nada"); return 1
        # ROI en cámara del objeto (+ margen) y bordes reales con luz blanca
        pi = np.round(poly).astype(int); px_ = np.clip(pi[:, 0], 0, W - 1); py_ = np.clip(pi[:, 1], 0, H - 1); ok = valid[py_, px_]
        cx, cy = mapx[py_[ok], px_[ok]], mapy[py_[ok], px_[ok]]
        hh, ww = white.shape[:2]; m = 80
        roi = (int(max(0, cx.min() - m)), int(max(0, cy.min() - m)), int(min(ww, cx.max() + m)), int(min(hh, cy.max() + m)))
        g = cv2.cvtColor(white, cv2.COLOR_BGR2GRAY); real = cv2.Canny(cv2.GaussianBlur(g, (5, 5), 0), 50, 120)
        rm = np.zeros_like(real); rm[roi[1]:roi[3], roi[0]:roi[2]] = 255; real[rm == 0] = 0
        real_d = cv2.dilate(real, np.ones((5, 5), np.uint8)) > 0
        rep["roi_cam"] = roi; rep["bordes_reales_px"] = int((real > 0).sum())
        cv2.imwrite(str(out / "bordes_reales.png"), real[roi[1]:roi[3], roi[0]:roi[2]])
        dark_g = cv2.cvtColor(dark, cv2.COLOR_BGR2GRAY).astype(np.int16)

        def score_at(dx, dy):
            canvas = np.zeros((H, W, 3), np.uint8)
            M = np.float32([[1, 0, dx], [0, 1, dy]]); e = cv2.warpAffine(edges, M, (W, H))
            canvas[e > 0] = (255, 255, 255)
            win.show(canvas, 30); time.sleep(1.1); cam.flush(8); f = cam.read()
            lit = (cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.int16) - dark_g) > 40
            lit[rm == 0] = False
            n = int(lit.sum()); s = float((lit & real_d).sum()) / n if n > 200 else 0.0
            rep["pruebas"].append({"dx": dx, "dy": dy, "puntaje": round(s, 4), "iluminados": n}); print(f"dx {dx:+d} dy {dy:+d}  puntaje {s:.3f}  ({n} px)")
            return s

        # la grilla se centra en el ajuste actual (si ya hubo una pasada, se afina alrededor de ella)
        best = (-1.0, 0, 0); r, s_ = a.radio, a.paso; c0x, c0y = int(round(adj[0])), int(round(adj[1]))
        for dy in range(c0y - r, c0y + r + 1, s_):
            for dx in range(c0x - r, c0x + r + 1, s_):
                sc = score_at(dx, dy)
                if sc > best[0]:
                    best = (sc, dx, dy)
        bx, by = best[1], best[2]
        for dy in range(by - s_ + a.fino, by + s_, a.fino):
            for dx in range(bx - s_ + a.fino, bx + s_, a.fino):
                if (dx, dy) == (bx, by):
                    continue
                sc = score_at(dx, dy)
                if sc > best[0]:
                    best = (sc, dx, dy)
        rep["mejor"] = {"puntaje": round(best[0], 4), "dx": best[1], "dy": best[2]}
        print("mejor corrimiento (total, sobre la geometría del escaneo):", rep["mejor"], "| ajuste previo:", adj)
        # mostrar 6 s la foto alineada con el mejor corrimiento
        M = np.float32([[1, 0, best[1]], [0, 1, best[2]]]); shown = cv2.warpAffine(pov, M, (W, H)); mk = cv2.warpAffine(mask, M, (W, H))
        canvas = np.zeros((H, W, 3), np.uint8); canvas[mk > 0] = shown[mk > 0]; win.show(canvas, 6); time.sleep(5)
    finally:
        win.close(); cam.close()
    (scan / f"align_{a.objeto}.json").write_text(json.dumps(rep, indent=1))
    if not a.sin_aplicar and best[0] > 0:
        ddx, ddy = best[1] - adj[0], best[2] - adj[1]
        print("nudge runtime (delta sobre el ajuste previo):", ddx, ddy, nudge_runtime(a.objeto, ddx, ddy))
    if not a.guardar:
        shutil.rmtree(out, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
