"""Prueba de presentación con vsync en VENTANA sobre la pantalla principal (no toca el proyector): abre PatternWindow
sin pantalla completa, presenta un patrón en movimiento durante N segundos y mide los flips por segundo. Sirve para
comprobar que SDL presenta sincronizado antes de volver a usar el proyector por HDMI (los kernel panics DCPEXT1 del
8-nov vinieron de presentar con immediate swap).

    .venv/bin/python scripts/prueba_vsync.py --segundos 300
"""

from __future__ import annotations

import argparse
import time

import numpy as np

from slscan.capture import PatternWindow


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segundos", type=float, default=300)
    ap.add_argument("--screen", default="0")
    a = ap.parse_args()
    screen = int(a.screen) if a.screen.isdigit() else a.screen
    win = PatternWindow((1920, 1080), screen=screen, fullscreen=False, name="prueba vsync (ventana)", allow_main=True)
    print(f"vsync={getattr(win, 'vsync', None)} refresco={getattr(win, 'refresh_hz', None)} tamaño={win.win_size}", flush=True)
    h, w = 540, 960
    yy, xx = np.mgrid[0:h, 0:w]
    t0 = time.time(); n = 0; t_rep = t0
    while time.time() - t0 < a.segundos:
        fase = (time.time() - t0) * 120
        img = (((xx + fase) // 40 + yy // 40) % 2 * 200 + 30).astype(np.uint8)
        win.show(img); n += 1
        if time.time() - t_rep >= 10:
            print(f"{time.time() - t0:6.0f}s  flips={n}  fps≈{n / (time.time() - t_rep):.1f}", flush=True); n = 0; t_rep = time.time()
    win.close()
    print("fin sin errores", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
