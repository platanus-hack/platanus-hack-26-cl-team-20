"""Calibración a mano de las 4 esquinas de la superficie proyectable (rectángulo real sobre la mesa), pedido de Max 2-nov.

    python -m playground.superficie_manual captures/dale-XXXX [--corner 1] [--fondo blanco|negro]

El proyector muestra un fondo blanco con una cruz negra en la esquina activa (y puntos chicos en las otras). Max la
mueve con teclas hasta donde en el mundo real está la esquina del rectángulo; luego pasa a la siguiente. Al guardar se
rehace la homografía lienzo→proyector de superficie.json con esas 4 esquinas (TL, TR, BR, BL del lienzo) y el damero.
Teclas (ventanita en el monitor de Max): 1 2 3 4 esquina · flechas / h j k l 2 px · H J K L 10 px · c damero 4 s ·
w fondo blanco/negro · r volver · s guardar · q guardar y salir.
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

W, H = 1920, 1080
NAMES = ["TL", "TR", "BR", "BL"]


def proj_image(corners: np.ndarray, sel: int, fondo: int) -> np.ndarray:
    """fondo=255: la luz blanca ES el cuadrilátero (negro afuera, blanco adentro) y la cruz negra se ve dentro; así se
    ve en la mesa la forma real de la luz mientras se mueven las esquinas. fondo=0: todo blanco con cruz negra."""
    poly = corners.astype(np.int32).reshape(-1, 1, 2)
    if fondo > 127:
        img = np.zeros((H, W, 3), np.uint8)
        cv2.fillPoly(img, [poly], (255, 255, 255), cv2.LINE_AA)
        ink, soft = (0, 0, 0), (120, 120, 120)
        cv2.polylines(img, [poly], True, soft, 1, cv2.LINE_AA)          # borde gris, visible desde afuera
    else:
        img = np.full((H, W, 3), 255, np.uint8)
        ink, soft = (0, 0, 0), (150, 150, 150)
        cv2.polylines(img, [poly], True, soft, 1, cv2.LINE_AA)
    for i, (x, y) in enumerate(corners):
        x, y = int(round(x)), int(round(y))
        if i == sel:
            cv2.line(img, (0, y), (W, y), ink, 2); cv2.line(img, (x, 0), (x, H), ink, 2)
            cv2.circle(img, (x, y), 18, ink, 2, cv2.LINE_AA)
            cv2.circle(img, (x, y), 22, (255, 255, 255), 2, cv2.LINE_AA)   # anillo blanco para verla también fuera de la luz
        else:
            cv2.circle(img, (x, y), 7, soft, 2, cv2.LINE_AA)
        cv2.putText(img, str(i + 1), (min(x + 14, W - 40), max(y - 14, 30)), cv2.FONT_HERSHEY_SIMPLEX, 1.0, ink if i == sel else soft, 2, cv2.LINE_AA)
    return img


def checker_from(corners: np.ndarray, lienzo: tuple[int, int], cells: int) -> tuple[np.ndarray, np.ndarray]:
    lw, lh = lienzo
    src = np.array([[0, 0], [lw, 0], [lw, lh], [0, lh]], np.float32)
    Hlp = cv2.getPerspectiveTransform(src, corners.astype(np.float32))
    cell = lh / cells
    yy, xx = np.mgrid[0:lh, 0:lw]
    chk = np.zeros((lh, lw, 3), np.uint8)
    chk[((xx // cell).astype(int) + (yy // cell).astype(int)) % 2 == 0] = 255
    cv2.rectangle(chk, (0, 0), (lw - 1, lh - 1), (0, 200, 255), 8)
    return Hlp.astype(np.float64), cv2.warpPerspective(chk, Hlp, (W, H), flags=cv2.INTER_AREA)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("scan"); p.add_argument("--corner", type=int, default=1); p.add_argument("--fondo", default="blanco")
    p.add_argument("--runtime", default="http://127.0.0.1:8790"); p.add_argument("--aspect", type=float, default=None,
                                                                                help="ancho/alto real del rectángulo (si se sabe); si no, el del lienzo actual")
    a = p.parse_args(argv)
    scan = Path(a.scan)
    spath = scan / "superficie.json"
    sup = json.loads(spath.read_text())
    corners0 = np.array(sup.get("rect_proj_manual") or sup["rect_proj_tl_tr_br_bl"], np.float64)
    corners = corners0.copy()
    lw, lh = (int(v) for v in sup["lienzo_px"])
    if a.aspect:
        lh = int(round(lw / a.aspect))
    cells = int(sup.get("cuadrados_alto", 9))
    sel = int(np.clip(a.corner - 1, 0, 3)); fondo = 255 if a.fondo == "blanco" else 0
    use_rt = pc.runtime_alive(a.runtime)
    win = None
    if not use_rt:
        from slscan.capture import PatternWindow
        win = PatternWindow((W, H), screen="1920x1080", name="superficie_manual")
    tmp = scan / "superficie_manual.png"

    def show(img: np.ndarray, seconds: float = 3600):
        if use_rt:
            cv2.imwrite(str(tmp), img)
            try:
                pc.runtime_cmd(a.runtime, "overlay", path=str(tmp.resolve()), seconds=seconds)
            except Exception as e:  # noqa: BLE001
                print("overlay falló:", e, flush=True)
        else:
            win.show(img)

    def cursor():
        show(proj_image(corners, sel, fondo))

    def save():
        Hlp, chk = checker_from(corners, (lw, lh), cells)
        cv2.imwrite(str(scan / "superficie_checker.png"), chk)
        sup.update({"rect_proj_manual": corners.round(1).tolist(), "rect_proj_auto": sup.get("rect_proj_auto") or sup["rect_proj_tl_tr_br_bl"],
                    "rect_proj_tl_tr_br_bl": corners.round(1).tolist(), "ajuste_manual_px": {NAMES[i]: (corners[i] - corners0[i]).round(1).tolist() for i in range(4)},
                    "lienzo_px": [lw, lh], "H_lienzo_to_proj": Hlp.tolist(), "cuadrado_lienzo_px": round(lh / cells, 1),
                    "origen": "manual (Max, cruz sobre fondo blanco)", "ajustado": time.strftime("%Y-%m-%d %H:%M:%S")})
        spath.write_text(json.dumps(sup, indent=1))
        print(json.dumps({"guardado": str(spath), "esquinas": sup["rect_proj_tl_tr_br_bl"], "ajuste_px": sup["ajuste_manual_px"]}), flush=True)

    name = "superficie: 1-4 esquina · flechas/hjkl 2px · HJKL 10px · c damero · w fondo · s guardar · q salir"
    cv2.namedWindow(name, cv2.WINDOW_AUTOSIZE); cv2.moveWindow(name, 80, 80)
    cursor()
    steps = {"h": (-2, 0), "l": (2, 0), "k": (0, -2), "j": (0, 2), "H": (-10, 0), "L": (10, 0), "K": (0, -10), "J": (0, 10)}
    arrows = {63234: (-2, 0), 63235: (2, 0), 63232: (0, -2), 63233: (0, 2), 2: (-2, 0), 3: (2, 0), 0: (0, -2), 1: (0, 2)}
    try:
        while True:
            canvas = np.zeros((420, 760, 3), np.uint8)
            sc = 0.35
            poly = (corners * sc + (20, 20)).astype(np.int32)
            cv2.rectangle(canvas, (20, 20), (20 + int(W * sc), 20 + int(H * sc)), (90, 90, 90), 1)
            cv2.polylines(canvas, [poly.reshape(-1, 1, 2)], True, (0, 200, 0), 1)
            for i, (x, y) in enumerate(poly):
                cv2.circle(canvas, (int(x), int(y)), 7 if i == sel else 4, (0, 255, 255) if i == sel else (0, 200, 0), 2)
                cv2.putText(canvas, str(i + 1), (int(x) + 8, int(y) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
            d = corners[sel] - corners0[sel]
            cv2.putText(canvas, f"esquina {sel + 1} {NAMES[sel]} en ({corners[sel][0]:.0f}, {corners[sel][1]:.0f})  ajuste {d[0]:+.0f},{d[1]:+.0f}", (12, 410),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.imshow(name, canvas)
            k = cv2.waitKeyEx(50)
            if k == -1:
                continue
            ch = chr(k) if 0 <= k < 256 else ""
            if ch and ch in "1234":
                sel = int(ch) - 1; cursor()
            elif (ch and ch in steps) or k in arrows:
                dx, dy = steps.get(ch) or arrows[k]
                corners[sel] = np.clip(corners[sel] + (dx, dy), (0, 0), (W - 1, H - 1)); cursor()
            elif ch == "c":
                _, chk = checker_from(corners, (lw, lh), cells); show(chk, 4.0); time.sleep(4.2); cursor()
            elif ch == "w":
                fondo = 255 - fondo; cursor()
            elif ch == "r":
                corners = corners0.copy(); cursor()
            elif ch == "s":
                save()
            elif ch == "q" or k == 27:
                save(); break
    finally:
        cv2.destroyAllWindows()
        if use_rt:
            try:
                pc.runtime_cmd(a.runtime, "overlay", path=str((scan / "superficie_checker.png").resolve()), seconds=3600)
            except Exception:  # noqa: BLE001
                pass
        if win is not None:
            win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
