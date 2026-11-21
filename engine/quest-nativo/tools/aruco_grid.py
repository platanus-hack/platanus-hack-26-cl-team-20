#!/usr/bin/env python3
"""Genera una grilla ArUco (DICT_4X4_50) para el proyector de la mesa, la proyecta por el runtime
(:8790, comando `overlay`) y le pasa el JSON a la app del casco (files/aruco_grid.json + `aruco grilla`).

    .venv/bin/python tools/aruco_grid.py 4 3 300            # 12 marcadores de 300 px
    .venv/bin/python tools/aruco_grid.py 6 4 167 --solo-generar

Mismo formato que captures/dale-20261102-1511/aruco_grid.json: fondo negro, cada marcador sobre un
cuadro blanco (zona de silencio), esquinas TL, TR, BR, BL en píxeles del cuadro 1920x1080.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent.parent
ADB = os.environ.get("ADB", "/Users/max/Library/Android/sdk/platform-tools/adb")
SERIAL = os.environ.get("ANDROID_SERIAL", "192.168.4.161:5555")
APP = "com.smartup.proyector"


def generar(cols, filas, tam, ancho=1920, alto=1080, margen=None, borde=20):
    """Grilla con huecos iguales entre cuadros blancos y hasta el borde (>= `borde` px de negro: si un cuadro
    blanco toca el borde de la imagen OpenCV deja de detectar esa fila). Zona blanca ~1 módulo (tam/6) si cabe."""
    dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    img = np.zeros((alto, ancho), np.uint8)
    if margen is None:
        lim = min((ancho - (cols + 1) * borde) / cols, (alto - (filas + 1) * borde) / filas)   # lado máximo del cuadro blanco
        margen = int(min(round(tam / 6), (lim - tam) // 2))
    pad = tam + 2 * margen
    gx = (ancho - cols * pad) / (cols + 1); gy = (alto - filas * pad) / (filas + 1)
    if margen < 10 or gx < borde - 1 or gy < borde - 1:
        sys.exit(f"{cols}x{filas} de {tam} px no cabe con zona blanca (margen {margen}, huecos {gx:.0f}/{gy:.0f}): baja el tamaño")
    marcas = []
    for f in range(filas):
        for c in range(cols):
            i = f * cols + c
            px = int(round(gx + c * (pad + gx))); py = int(round(gy + f * (pad + gy)))
            img[py:py + pad, px:px + pad] = 255
            x0, y0 = px + margen, py + margen
            img[y0:y0 + tam, x0:x0 + tam] = cv2.aruco.generateImageMarker(dic, i, tam)
            marcas.append({"id": i, "esquinas_px": [[x0, y0], [x0 + tam, y0], [x0 + tam, y0 + tam], [x0, y0 + tam]]})
    meta = {"dict": "DICT_4X4_50", "cols": cols, "rows": filas, "marker_px": tam, "margen_px": int(margen), "size": [ancho, alto], "markers": marcas}
    return img, meta


def runtime_overlay(url, path, seconds):
    body = json.dumps({"cmd": "overlay", "args": {"path": str(path), "seconds": seconds}}).encode()
    req = urllib.request.Request(url + "/api/command", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cols", type=int); ap.add_argument("filas", type=int); ap.add_argument("tam", type=int, nargs="?", default=0)
    ap.add_argument("--runtime", default="http://127.0.0.1:8790"); ap.add_argument("--segundos", type=float, default=1800)
    ap.add_argument("--solo-generar", action="store_true"); ap.add_argument("--app", default=f"http://{SERIAL.split(':')[0]}:8787")
    a = ap.parse_args(argv)
    tam = a.tam or int(min((1920 - (a.cols + 1) * 20) / a.cols, (1080 - (a.filas + 1) * 20) / a.filas) * 0.75)
    img, meta = generar(a.cols, a.filas, tam)
    d = AQUI / "descargas" / "aruco"; d.mkdir(parents=True, exist_ok=True)
    png = d / f"aruco_grid_{a.cols}x{a.filas}_{tam}.png"; js = png.with_suffix(".json")
    cv2.imwrite(str(png), img); js.write_text(json.dumps(meta, indent=1))
    # autoverificación con los mismos parámetros que la app, a 1/3 de escala (≈ como lo ve la cámara del casco)
    pr = cv2.aruco.DetectorParameters()
    pr.adaptiveThreshWinSizeMin, pr.adaptiveThreshWinSizeMax, pr.adaptiveThreshWinSizeStep = 3, 53, 4
    pr.minMarkerPerimeterRate, pr.polygonalApproxAccuracyRate, pr.errorCorrectionRate = 0.01, 0.05, 0.8
    pr.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), pr)
    n = len(det.detectMarkers(cv2.resize(img, None, fx=1 / 3, fy=1 / 3, interpolation=cv2.INTER_AREA))[0])
    out = {"png": str(png), "json": str(js), "marcadores": len(meta["markers"]), "tam_px": tam, "margen_px": meta["margen_px"],
           "autoverificacion": f"{n}/{len(meta['markers'])}"}
    if not a.solo_generar:
        try:
            r = runtime_overlay(a.runtime, png, a.segundos)
            out["runtime"] = r.get("result") or r
        except Exception as ex:  # noqa: BLE001
            out["runtime"] = f"error: {ex}"
        env = dict(os.environ, ANDROID_SERIAL=SERIAL)
        p = subprocess.run([ADB, "push", str(js), f"/sdcard/Android/data/{APP}/files/aruco_grid.json"], capture_output=True, text=True, env=env)
        out["adb_push"] = (p.stdout or p.stderr).strip().splitlines()[-1:] if (p.stdout or p.stderr) else "ok"
        try:
            with urllib.request.urlopen(f"{a.app}/cmd?c=aruco%20grilla", timeout=4) as r:
                out["app"] = json.loads(r.read()).get("r")
        except Exception as ex:  # noqa: BLE001
            p = subprocess.run([ADB, "shell", "am", "broadcast", "-a", f"{APP}.CMD", "-p", APP, "--es", "c", "'aruco grilla'"], capture_output=True, text=True, env=env)
            out["app"] = f"http :8787 no respondió ({ex}); broadcast enviado"
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
