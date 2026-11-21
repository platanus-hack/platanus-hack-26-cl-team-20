"""Vista de cámara proyectada (PIP): muestra en una zona del proyector la última imagen de una carpeta (p. ej. la vista
de la Osmo con las máscaras del tracker, `touchsynth live --debug-dir reports/_track`), refrescada cada pocos
décimas. Sirve para que Max vea en la pared qué está detectando el tracking de manos. Privado: la carpeta vive en
reports/ y se borra después."""

from __future__ import annotations

import glob
import os
import time

import cv2
import numpy as np

from .base import Effect, Canvas


class CamView(Effect):
    name = "cam_view"
    label = "Vista de cámara (tracking)"
    description = ("Proyecta la última imagen de una carpeta (vista de la Osmo con máscaras del tracker) en un recuadro: "
                   "para comprobar a ojo que el hand tracking ve la mano. x, y, w, h en px del proyector.")
    needs = ()
    targets = ("*",)
    params = {"carpeta": "reports/_track", "patron": "osmo_pov_*.png", "url": "", "x": 1180, "y": 0, "w": 740, "h": 416,
              "cada_s": 0.3, "borde": [255, 255, 255], "titulo": "tracking Osmo", "capa": 50}

    def update(self, dt, scene):
        super().update(dt, scene)
        now = time.time()
        if now - getattr(self, "_t", 0) < float(self.p["cada_s"]):
            return
        self._t = now
        url = str(self.p.get("url") or "")
        if url:                                   # feed por HTTP (p. ej. la Brio del PC: /frame.jpg?w=480)
            try:
                import urllib.request
                with urllib.request.urlopen(url, timeout=1.5) as r:
                    im = cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR)
                self.img = im if im is not None else getattr(self, "img", None)
            except Exception:  # noqa: BLE001
                pass
            return
        files = glob.glob(os.path.join(str(self.p["carpeta"]), str(self.p["patron"])))
        if not files:
            self.img = None; return
        latest = max(files, key=os.path.getmtime)
        im = cv2.imread(latest)
        self.img = im if im is not None else getattr(self, "img", None)

    def draw(self, canvas: Canvas):
        x, y, w, h = (int(self.p[k]) for k in ("x", "y", "w", "h"))
        H, W = canvas.img.shape[:2]
        x, y = max(0, min(x, W - 2)), max(0, min(y, H - 2)); w, h = min(w, W - x), min(h, H - y)
        im = getattr(self, "img", None)
        if im is None:
            cv2.rectangle(canvas.img, (x, y), (x + w, y + h), (80, 80, 80), 2)
            cv2.putText(canvas.img, "sin imagen del tracker", (x + 12, y + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
            return
        canvas.img[y:y + h, x:x + w] = cv2.resize(im, (w, h), interpolation=cv2.INTER_AREA)
        b = tuple(int(c) for c in self.p["borde"][::-1])
        cv2.rectangle(canvas.img, (x, y), (x + w - 1, y + h - 1), b, 2)
        cv2.putText(canvas.img, str(self.p["titulo"]), (x + 10, y + h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.7, b, 2, cv2.LINE_AA)
