"""Salida al proyector EN VENTANA NORMAL (con barra de título, sin pantalla completa, sin NOFRAME, sin presentación
directa): tres kernel panics (8 y 9-nov, DCPEXT1 isr_immediate_swap_failed) coincidieron con ventanas sin bordes que
cubrían exacto la pantalla externa; la hipótesis es que macOS pasa a direct scanout y el firmware del DCP falla con este
proyector. Esta ventana deja márgenes y barra de título a propósito, presenta con vsync y nunca más de un flip por refresco.

    python -m playground.ventana_proyector --display 0,1080 --size 1900x1040 --port 8792

El "espacio del proyector" para la calibración es el contenido de esta ventana (ancho x alto de --size), no el display.
HTTP:
  GET  /estado                     → tamaño del contenido, posición, fps, imagen actual
  GET  /mostrar?path=/abs/img.png  → muestra esa imagen (se reescala al contenido si no calza; mejor mandarla del tamaño exacto)
  GET  /gris?v=128                 → gris uniforme (0-255)      GET /negro   GET /blanco
  POST /imagen  (cuerpo: PNG/JPG)  → muestra la imagen recibida
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--display", default="0,1080", help="origen x,y del display del proyector (puntos macOS)")
    ap.add_argument("--size", default="1900x1040", help="tamaño del CONTENIDO de la ventana")
    ap.add_argument("--margen", default="10,36", help="desplazamiento de la ventana dentro del display (x,y); deja ver la barra de título")
    ap.add_argument("--port", type=int, default=8792)
    ap.add_argument("--fps", type=float, default=30.0)
    a = ap.parse_args(argv)
    dx, dy = (int(v) for v in a.display.split(","))
    mx, my = (int(v) for v in a.margen.split(","))
    W, H = (int(v) for v in a.size.lower().split("x"))

    os.environ["SDL_VIDEO_WINDOW_POS"] = f"{dx + mx},{dy + my}"
    os.environ["SDL_RENDER_VSYNC"] = "1"
    os.environ["SDL_VIDEO_DOUBLE_BUFFER"] = "1"
    os.environ["SDL_VIDEO_MAC_FULLSCREEN_SPACES"] = "0"
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame
    pygame.display.init()
    try:
        win = pygame.display.set_mode((W, H), pygame.SCALED, vsync=1)   # ventana NORMAL: con marco, sin FULLSCREEN ni NOFRAME
        vsync = True
    except Exception as e:  # noqa: BLE001
        print(f"vsync explícito no disponible ({e}); sigo con el hint", flush=True)
        win = pygame.display.set_mode((W, H)); vsync = False
    pygame.display.set_caption("proyector (ventana)")

    state = {"img": np.zeros((H, W, 3), np.uint8), "nombre": "negro", "seq": 0, "fps": 0.0, "flips": 0, "t0": time.time(), "vsync": vsync}
    lock = threading.Lock()

    def poner(img: np.ndarray, nombre: str) -> None:
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        if (img.shape[1], img.shape[0]) != (W, H):
            img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA if img.shape[1] > W else cv2.INTER_NEAREST)
        with lock:
            state["img"] = np.ascontiguousarray(img); state["nombre"] = nombre; state["seq"] += 1

    class H_(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silencio
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path == "/estado":
                with lock:
                    return self._json({"size": [W, H], "display": [dx, dy], "pos": [dx + mx, dy + my], "nombre": state["nombre"], "seq": state["seq"],
                                       "fps": round(state["fps"], 1), "vsync": state["vsync"]})
            if u.path == "/mostrar":
                p = q.get("path", [""])[0]
                img = cv2.imread(p, cv2.IMREAD_COLOR)
                if img is None:
                    return self._json({"ok": False, "error": f"no pude leer {p}"}, 400)
                poner(img, os.path.basename(p)); return self._json({"ok": True, "nombre": state["nombre"], "seq": state["seq"]})
            if u.path == "/gris":
                v = int(float(q.get("v", ["128"])[0])); poner(np.full((H, W, 3), np.clip(v, 0, 255), np.uint8), f"gris{v}"); return self._json({"ok": True, "seq": state["seq"]})
            if u.path == "/negro":
                poner(np.zeros((H, W, 3), np.uint8), "negro"); return self._json({"ok": True, "seq": state["seq"]})
            if u.path == "/blanco":
                poner(np.full((H, W, 3), 255, np.uint8), "blanco"); return self._json({"ok": True, "seq": state["seq"]})
            return self._json({"error": "ruta desconocida"}, 404)

        def do_POST(self):
            if urlparse(self.path).path != "/imagen":
                return self._json({"error": "ruta desconocida"}, 404)
            n = int(self.headers.get("Content-Length", "0")); data = self.rfile.read(n)
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return self._json({"ok": False, "error": "imagen inválida"}, 400)
            poner(img, "post"); return self._json({"ok": True, "seq": state["seq"]})

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H_)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(json.dumps({"ventana": [W, H], "pos": [dx + mx, dy + my], "port": a.port, "vsync": vsync}), flush=True)

    periodo = 1.0 / max(1.0, a.fps); ultimo_seq = -1; t_flip = 0.0; n = 0; t_fps = time.time()
    try:
        while True:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    raise KeyboardInterrupt
            with lock:
                seq = state["seq"]; img = state["img"]
            if seq != ultimo_seq:
                rgb = np.ascontiguousarray(img[..., ::-1])
                surf = pygame.surfarray.make_surface(rgb.swapaxes(0, 1))
                win.blit(surf, (0, 0)); ultimo_seq = seq
            espera = (t_flip + periodo) - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            pygame.display.flip(); t_flip = time.monotonic(); n += 1
            if time.time() - t_fps >= 5:
                state["fps"] = n / (time.time() - t_fps); n = 0; t_fps = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        pygame.display.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
