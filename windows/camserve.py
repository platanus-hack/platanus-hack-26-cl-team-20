"""Servidor de cuadros de la webcam del PC (Logitech Brio 100) para que el Mac la use como segunda cámara.

Corre en el PC, en C:\\sl-scanner con su venv:

    .venv\\Scripts\\python.exe windows\\camserve.py                 # Brio índice 0 (DirectShow), 1920x1080, HTTP :8794
    .venv\\Scripts\\python.exe windows\\camserve.py --camera 1 --port 8794

Rutas (solo LAN y tailnet; otra IP recibe 403):
    GET /health      JSON: cámara abierta, tamaño, fps medidos, media de brillo (0.0 = tapa de privacidad cerrada)
    GET /frame.jpg   último cuadro (?q=85 calidad JPEG, ?w=960 ancho); cabecera X-Frame-Seq con el número de cuadro
    GET /frame.png   último cuadro sin pérdida (para gray code)

Mismo contrato que windows/hands_service.py (que además detecta manos): cuando ese servicio corra en el 8794,
este se apaga o se mueve de puerto. Es solo lectura: no guarda nada en disco.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

PRIVATE = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                             "100.64.0.0/10", "169.254.0.0/16", "::1/128", "fd00::/8", "fe80::/10")]


def allowed(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    m = getattr(a, "ipv4_mapped", None)      # solo IPv6 tiene ipv4_mapped
    if m is not None:
        a = m
    return any(a in n for n in PRIVATE)


class Grabber(threading.Thread):
    def __init__(self, index: int, w: int, h: int):
        super().__init__(daemon=True)
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        self.cap = cv2.VideoCapture(index, backend)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w); self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.lock = threading.Lock(); self.frame = None; self.seq = 0; self.fps = 0.0; self.mean = 0.0
        self.ok = self.cap.isOpened(); self.t_last = time.time()

    def run(self) -> None:
        n, t0 = 0, time.time()
        while True:
            ok, f = self.cap.read()
            if not ok or f is None:
                time.sleep(0.05); continue
            with self.lock:
                self.frame = f; self.seq += 1; self.t_last = time.time()
            n += 1
            if n % 30 == 0:
                t = time.time(); self.fps = 30 / (t - t0); t0 = t; self.mean = float(f.mean())

    def snapshot(self):
        with self.lock:
            return None if self.frame is None else (self.frame.copy(), self.seq)


def make_handler(g: Grabber):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silencioso
            pass

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, str(v))
            self.end_headers(); self.wfile.write(body)

        def do_GET(self) -> None:
            if not allowed(self.client_address[0]):
                return self._send(403, b"solo LAN", "text/plain")
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path in ("/", "/health"):
                snap = g.snapshot()
                info = {"camera_open": g.ok, "frame": None if snap is None else [snap[0].shape[1], snap[0].shape[0]],
                        "seq": g.seq, "fps": round(g.fps, 1), "mean": round(g.mean, 1), "age_s": round(time.time() - g.t_last, 2),
                        "hint": "tapa de privacidad cerrada o cámara sin luz" if g.mean < 2 else "ok"}
                return self._send(200, json.dumps(info).encode(), "application/json")
            if u.path in ("/frame.jpg", "/frame.png"):
                snap = g.snapshot()
                if snap is None:
                    return self._send(503, b"sin cuadros", "text/plain")
                f, seq = snap
                w = int(q.get("w", [0])[0] or 0)
                if w and w < f.shape[1]:
                    f = cv2.resize(f, (w, int(f.shape[0] * w / f.shape[1])), interpolation=cv2.INTER_AREA)
                if u.path == "/frame.jpg":
                    ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, int(q.get("q", [85])[0])])
                    return self._send(200, buf.tobytes(), "image/jpeg", {"X-Frame-Seq": seq})
                ok, buf = cv2.imencode(".png", f, [cv2.IMWRITE_PNG_COMPRESSION, 1])
                return self._send(200, buf.tobytes(), "image/png", {"X-Frame-Seq": seq})
            return self._send(404, b"rutas: /health /frame.jpg /frame.png", "text/plain")
    return H


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--camera", type=int, default=0); p.add_argument("--size", default="1920x1080")
    p.add_argument("--host", default="0.0.0.0"); p.add_argument("--port", type=int, default=8794)
    a = p.parse_args(argv)
    w, h = (int(v) for v in a.size.split("x"))
    g = Grabber(a.camera, w, h); g.start()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(g))
    print(f"camserve: cámara {a.camera} {'abierta' if g.ok else 'NO abre'} · http://{a.host}:{a.port}/health /frame.jpg", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
