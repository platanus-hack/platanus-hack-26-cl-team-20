#!/usr/bin/env python3
"""Comprueba que lo que el renderer del casco manda al proyector (comando `autoprueba`, archivos
files/prueba_*.png) es pixel a pixel el patrón Gray de slscan/graycode.py.

    tools/cmd.sh autoprueba && sleep 4 && tools/cmd.sh traer
    ../.venv/bin/python tools/verificar_salida.py descargas/files
"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from slscan import graycode  # noqa: E402

CASOS = {"prueba_gray_x9.png": (0, 9, False), "prueba_gray_x1.png": (0, 1, False), "prueba_gray_y3i.png": (1, 3, True)}


def main() -> int:
    d = Path(sys.argv[1] if len(sys.argv) > 1 else "descargas/files")
    ok = True
    for nombre, (eje, bit, inv) in CASOS.items():
        f = d / nombre
        if not f.exists():
            print(f"{nombre}: no está"); ok = False; continue
        img = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
        ref = graycode.pattern((1920, 1080), eje, bit, inv)
        dif = int((img.astype(int) != ref.astype(int)).sum())
        print(f"{nombre}: {img.shape[1]}x{img.shape[0]}, píxeles distintos de slscan.graycode.pattern = {dif}")
        ok &= dif == 0 and img.shape == ref.shape
    p = d / "prueba_prueba.png"
    if p.exists():
        c = cv2.imread(str(p))
        # esquinas: arriba-izq rojo, arriba-der verde, abajo-izq azul, abajo-der blanco (BGR)
        esquinas = {"arriba-izq": c[60, 60], "arriba-der": c[60, 1860], "abajo-izq": c[1020, 60], "abajo-der": c[1020, 1860]}
        print("carta de prueba, esquinas BGR:", {k: v.tolist() for k, v in esquinas.items()})
    print("OK" if ok else "DIFERENCIAS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
