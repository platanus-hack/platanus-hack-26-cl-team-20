#!/usr/bin/env python3
"""Mide la detección ArUco de la app en vivo y guarda una captura de la cámara con las marcas dibujadas.

    .venv/bin/python tools/medir_aruco.py                       # configuración actual, 12 s
    .venv/bin/python tools/medir_aruco.py --comparar            # fábrica vs ajustado vs ajustado rápido (perímetro 0,02, paso 10)

Salida: una línea por configuración (media/máx/mín de marcadores por cuadro en 10 s, ms de detección,
acumulados, descartados, plano, homografía, PnP de la mesa) y descargas/aruco_medicion/cam_marcas.jpg.
"""
import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent.parent


def get(url, binario=False, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return (r.read(), dict(r.headers)) if binario else json.loads(r.read())


def cmd(h, c):
    return get(f"{h}/cmd?c={urllib.parse.quote(c)}").get("r")


def resumen(h, etiqueta):
    d = get(f"{h}/estado.json"); a = d.get("aruco", {}); p = a.get("por_cuadro_10s") or {}
    pl = a.get("plano") or {}; q = a.get("pnp") or {}
    print(f"{etiqueta:>22}: {a.get('detector')} | por cuadro media {p.get('media', 0):.1f} máx {p.get('max')} mín {p.get('min')} "
          f"({p.get('cuadros')} cuadros) | det {a.get('ms_det')} ms | acumulados {a.get('n_acumulados')} descartados {a.get('n_descartados')} | "
          f"plano {pl.get('rms_mm', float('nan')):.1f} mm | H {a.get('homografia_rms_mm') or float('nan'):.1f} mm | PnP {q.get('info', '—')} | "
          f"cam {d['camara']['estado']} foco={d['app']['foco']} proyector={d['calibracion'].get('proyector_activo')}")
    return d


def captura(h, salida):
    img, cab = get(f"{h}/cam_full.jpg", binario=True)
    d = get(f"{h}/estado.json"); a = d.get("aruco", {})
    im = cv2.cvtColor(cv2.imdecode(np.frombuffer(img, np.uint8), cv2.IMREAD_GRAYSCALE), cv2.COLOR_GRAY2BGR)
    for m in a.get("marcas", []):
        e = np.array(m["esq_cam"], float).reshape(4, 2)
        ok = all(p is not None for p in m["esq_mundo"])
        cv2.polylines(im, [e.astype(np.int32)], True, (0, 220, 0) if ok else (0, 200, 255), 3)
        cv2.circle(im, tuple(e[0].astype(int)), 6, (255, 0, 255), -1)
        cv2.putText(im, str(m["id"]), tuple(e.mean(0).astype(int) - [10, -10]), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 3)
    cv2.putText(im, f"{len(a.get('marcas', []))} en el cuadro ({a.get('detector')}) ts {cab.get('X-Ts-Ns')}", (20, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
    salida.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(salida), im)
    print("captura:", salida, "| cabeceras:", {k: v for k, v in cab.items() if k.startswith("X-")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="http://192.168.4.161:8787"); ap.add_argument("--segundos", type=float, default=12)
    ap.add_argument("--comparar", action="store_true")
    a = ap.parse_args()
    h = a.app
    if not get(f"{h}/estado.json").get("aruco", {}).get("activo"):
        print(cmd(h, "aruco on")); time.sleep(3)
    if a.comparar:
        for etiqueta, cs in [("fábrica", ["aruco aruco3 off", "aruco params fabrica"]),
                             ("ajustado (pedido)", ["aruco params ajustado", "aruco perimetro 0.01", "aruco paso 4"]),
                             ("ajustado rápido", ["aruco perimetro 0.02", "aruco paso 10"]),
                             ("ajustado+aruco3", ["aruco perimetro 0.01", "aruco paso 4", "aruco aruco3 on"])]:
            for c in cs: cmd(h, c)
            time.sleep(a.segundos)
            resumen(h, etiqueta)
        cmd(h, "aruco aruco3 off")
    else:
        time.sleep(a.segundos); resumen(h, "actual")
    captura(h, AQUI / "descargas" / "aruco_medicion" / "cam_marcas.jpg")


if __name__ == "__main__":
    main()
