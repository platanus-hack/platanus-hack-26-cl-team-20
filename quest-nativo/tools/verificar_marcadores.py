#!/usr/bin/env python3
"""Decodifica la grilla de marcadores QR con OpenCV y la compara con el layout esperado.

    .venv/bin/python tools/verificar_marcadores.py app/build/sintetico/marcadores_4x3.pgm app/build/sintetico/marcadores_4x3.csv
    .venv/bin/python tools/verificar_marcadores.py descargas/monitor/prueba_marcadores.png descargas/monitor/marcadores.json

Imprime cuántos QR se decodifican, si el ID calza con su posición y el error de las esquinas (px).
Con --escala 0.25 --ruido 8 simula una cámara que ve el patrón más chico y con ruido.
"""
import argparse
import csv
import json
import sys

import cv2
import numpy as np


def leer_layout(ruta):
    if ruta.endswith(".json"):
        d = json.load(open(ruta))
        return {it["payload"]: (np.array(it["centro"], float), np.array(it["esquinas"], float).reshape(4, 2)) for it in d["items"]}
    out = {}
    for r in csv.DictReader(open(ruta)):
        es = np.array([float(r[k]) for k in ("tlx", "tly", "trx", "try", "brx", "bry", "blx", "bly")]).reshape(4, 2)
        out[r["payload"]] = (np.array([float(r["cx"]), float(r["cy"])]), es)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("imagen"); ap.add_argument("layout")
    ap.add_argument("--escala", type=float, default=1.0); ap.add_argument("--ruido", type=float, default=0.0)
    a = ap.parse_args()
    img = cv2.imread(a.imagen, cv2.IMREAD_GRAYSCALE)
    if img is None:
        sys.exit(f"no se pudo leer {a.imagen}")
    lay = leer_layout(a.layout)
    if a.escala != 1.0:
        img = cv2.resize(img, None, fx=a.escala, fy=a.escala, interpolation=cv2.INTER_AREA)
    if a.ruido > 0:
        img = np.clip(img.astype(float) + np.random.default_rng(1).normal(0, a.ruido, img.shape), 0, 255).astype(np.uint8)
    ok, textos, puntos, _ = cv2.QRCodeDetector().detectAndDecodeMulti(img)
    textos = list(textos) if ok else []
    errs, bien, mal = [], 0, 0
    # además, uno por uno (recorte alrededor de cada QR): el detector múltiple de OpenCV se salta algunos
    det1 = cv2.QRCodeDetector()
    uno = 0
    for t, (c, es) in lay.items():
        m = (es[1, 0] - es[0, 0]) * 0.35
        x0, y0 = (es.min(0) - m) * a.escala; x1, y1 = (es.max(0) + m) * a.escala
        rec = img[max(0, int(y0)):int(y1), max(0, int(x0)):int(x1)]
        tt, _, _ = det1.detectAndDecode(rec)
        uno += tt == t
    print(f"uno por uno: {uno}/{len(lay)} QR decodificados con su ID")
    for t, p in zip(textos, puntos if ok else []):
        if not t:
            continue
        if t not in lay:
            mal += 1
            continue
        c, es = lay[t]
        p = p.reshape(4, 2) / a.escala
        # OpenCV devuelve las esquinas en orden TL, TR, BR, BL del símbolo
        e = np.linalg.norm(p - es, axis=1)
        errs.extend(e.tolist())
        bien += 1
    print(f"{a.imagen}: {bien}/{len(lay)} QR decodificados con ID correcto, {mal} con texto ajeno; "
          f"error de esquinas mediana {np.median(errs) if errs else float('nan'):.2f} px, máx {max(errs) if errs else float('nan'):.2f} px")
    return 0 if uno == len(lay) else 1


if __name__ == "__main__":
    sys.exit(main())
