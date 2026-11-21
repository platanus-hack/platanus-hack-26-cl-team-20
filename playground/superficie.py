"""Superficie proyectable rectificada: el rectángulo verdadero más grande (en el plano de la mesa, con la métrica del
mousepad) que cabe dentro de lo que el proyector alcanza, y un damero de cuadrados que lo llena.

    python -m playground.superficie captures/dale-XXXX [--show] [--cells 9] [--margin 0.01]

Entrada: <scan>/pad_quad.json (esquinas del pad en px del proyector, ajustadas por Max, y su proporción).
Salida: <scan>/superficie.json con el rectángulo en coordenadas del plano (unidades: alto del pad = 1) y la homografía
lienzo rectificado → proyector (lienzo de `lienzo_px` ancho x alto, cuadrados de verdad sobre la mesa), más
<scan>/superficie_checker.png (1920x1080 para el proyector) y superficie_debug.png (plano: proyector, pad, rectángulo).
Con --show lo proyecta por el runtime (overlay) o en una ventana propia.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from . import pad_checker as pc
from .legibilidad import largest_rect

W, H = 1920, 1080


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("scan"); p.add_argument("--cells", type=int, default=9, help="cuadrados a lo alto del rectángulo")
    p.add_argument("--margin", type=float, default=0.01, help="margen relativo que se deja hacia el borde del proyector")
    p.add_argument("--lienzo", type=int, default=1920, help="ancho en px del lienzo rectificado")
    p.add_argument("--show", action="store_true"); p.add_argument("--seconds", type=float, default=3600)
    p.add_argument("--runtime", default="http://127.0.0.1:8790"); p.add_argument("--aspect", type=float, default=None)
    a = p.parse_args(argv)
    scan = Path(a.scan)
    data = json.loads((scan / "pad_quad.json").read_text())
    quad_proj = np.array(data.get("quad_proj_adjusted") or data["quad_proj_tl_tr_br_bl"], np.float64)
    aspect = float(np.clip(a.aspect or data.get("aspect_w_over_h") or 2.25, 0.5, 5.0))
    pad_rect = np.array([[0, 0], [aspect, 0], [aspect, 1], [0, 1]], np.float64)          # plano: alto del pad = 1
    Hpp = cv2.getPerspectiveTransform(pad_rect.astype(np.float32), quad_proj.astype(np.float32)).astype(np.float64)
    Hinv = np.linalg.inv(Hpp)
    # el marco del proyector en el plano
    frame = np.array([[0, 0], [W, 0], [W, H], [0, H]], np.float64)
    frame_plane = cv2.perspectiveTransform(frame.reshape(-1, 1, 2), Hinv).reshape(4, 2)
    # el plano solo vale donde el proyector apunta hacia la mesa (w > 0); si algún punto cruza el horizonte, se recorta
    hom = (Hinv @ np.c_[frame, np.ones(4)].T).T
    if np.any(hom[:, 2] <= 0):
        raise SystemExit("una esquina del proyector cae más allá del horizonte del plano: revisar el cuadrilátero")
    x0, y0 = frame_plane.min(0); x1, y1 = frame_plane.max(0)
    gw = 720; gh = int(round(gw * (y1 - y0) / (x1 - x0)))
    xs, ys = np.meshgrid(x0 + (np.arange(gw) + 0.5) / gw * (x1 - x0), y0 + (np.arange(gh) + 0.5) / gh * (y1 - y0))
    pts = np.stack([xs.ravel(), ys.ravel()], 1)
    pp = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), Hpp).reshape(-1, 2)
    m = a.margin
    inside = (pp[:, 0] >= m * W) & (pp[:, 0] <= (1 - m) * W) & (pp[:, 1] >= m * H) & (pp[:, 1] <= (1 - m) * H)
    mask = inside.reshape(gh, gw).astype(np.uint8)
    rx, ry, rw, rh = largest_rect(mask)
    if rw < 5 or rh < 5:
        raise SystemExit("no encontré un rectángulo dentro del área proyectada")
    sx, sy = (x1 - x0) / gw, (y1 - y0) / gh
    rect = [x0 + rx * sx, y0 + ry * sy, x0 + (rx + rw) * sx, y0 + (ry + rh) * sy]            # plano, [xa, ya, xb, yb]
    rw_p, rh_p = rect[2] - rect[0], rect[3] - rect[1]
    lienzo_w = a.lienzo; lienzo_h = int(round(lienzo_w * rh_p / rw_p))
    S = np.array([[rw_p / lienzo_w, 0, rect[0]], [0, rh_p / lienzo_h, rect[1]], [0, 0, 1]], np.float64)
    Hlp = Hpp @ S                                                                          # lienzo → proyector
    rect_proj = cv2.perspectiveTransform(np.array([[0, 0], [lienzo_w, 0], [lienzo_w, lienzo_h], [0, lienzo_h]], np.float64).reshape(-1, 1, 2), Hlp).reshape(4, 2)
    # área del proyector aprovechada
    poly = rect_proj.astype(np.int32); used = np.zeros((H, W), np.uint8); cv2.fillPoly(used, [poly], 1)
    frac = float(used.mean())
    # damero de cuadrados en el lienzo
    cell = lienzo_h / a.cells
    chk = np.zeros((lienzo_h, lienzo_w, 3), np.uint8)
    yy, xx = np.mgrid[0:lienzo_h, 0:lienzo_w]
    chk[((xx // cell).astype(int) + (yy // cell).astype(int)) % 2 == 0] = 255
    cv2.rectangle(chk, (0, 0), (lienzo_w - 1, lienzo_h - 1), (0, 200, 255), 8)
    proj = cv2.warpPerspective(chk, Hlp, (W, H), flags=cv2.INTER_AREA)
    cv2.imwrite(str(scan / "superficie_checker.png"), proj)
    # debug en el plano
    dbg = np.zeros((gh, gw, 3), np.uint8); dbg[mask > 0] = (60, 60, 60)
    def to_g(p): return ((p[0] - x0) / sx, (p[1] - y0) / sy)
    cv2.polylines(dbg, [np.array([to_g(p) for p in frame_plane], np.int32)], True, (255, 160, 0), 2)
    cv2.polylines(dbg, [np.array([to_g(p) for p in pad_rect], np.int32)], True, (0, 0, 255), 2)
    cv2.rectangle(dbg, (rx, ry), (rx + rw, ry + rh), (0, 255, 0), 2)
    cv2.imwrite(str(scan / "superficie_debug.png"), dbg)
    out = {"aspect_pad": aspect, "quad_proj_pad": quad_proj.round(1).tolist(), "H_pad_to_proj": Hpp.tolist(),
           "proyector_en_plano": frame_plane.round(4).tolist(), "rect_plano_xa_ya_xb_yb": [round(v, 4) for v in rect],
           "rect_en_pad_alto_1": {"ancho": round(rw_p, 4), "alto": round(rh_p, 4), "aspect": round(rw_p / rh_p, 4)},
           "lienzo_px": [lienzo_w, lienzo_h], "H_lienzo_to_proj": Hlp.tolist(), "rect_proj_tl_tr_br_bl": rect_proj.round(1).tolist(),
           "fraccion_proyector_usada": round(frac, 3), "cuadrados_alto": a.cells, "cuadrado_lienzo_px": round(cell, 1),
           "checker": str(scan / "superficie_checker.png"), "creado": time.strftime("%Y-%m-%d %H:%M:%S")}
    (scan / "superficie.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if not k.startswith("H_")}, ensure_ascii=False), flush=True)
    if not a.show:
        return 0
    if pc.runtime_alive(a.runtime):
        r = pc.runtime_cmd(a.runtime, "overlay", path=str((scan / "superficie_checker.png").resolve()), seconds=a.seconds)
        print("overlay:", r.get("result") or r, flush=True)
    else:
        from slscan.capture import PatternWindow
        win = PatternWindow((W, H), screen="1920x1080", name="superficie"); win.show(proj)
        try:
            time.sleep(a.seconds)
        except KeyboardInterrupt:
            pass
        finally:
            win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
