"""Mapa de teclas en vivo: dibuja cada tecla del teclado (polígono exacto de keys.json que carga la escena del
escaneo) con su nombre, blancas y negras con colores distintos, y marca las que suenan por MIDI si el runtime las
tiene en scene.controls. Es la 'calibración de cada tecla' que Max quiere ver proyectada mientras se trabaja."""

from __future__ import annotations

import cv2
import numpy as np

from .base import Effect, Canvas, bgr


class KeysMap(Effect):
    name = "keys_map"
    label = "Mapa de teclas"
    description = ("Contorno y nombre de cada tecla del teclado real (keys.json del escaneo). Blancas y negras en "
                   "colores distintos; rellena las que estén sonando. Para verificar la calibración tecla por tecla.")
    needs = ("contours",)
    targets = ("instrument",)
    params = {"color_blancas": [0, 200, 255], "color_negras": [255, 80, 80], "color_activa": [255, 255, 255],   # RGB
              "nombres": True, "relleno": 0.12, "grosor": 2, "solo_cada": 1}

    def update(self, dt, scene):
        super().update(dt, scene)
        self.obj = self.object(scene)
        self.keys = (self.obj.extra.get("keys") if self.obj is not None else None) or []
        self.active = set()
        for dev in (getattr(scene, "controls", None) or {}).values():
            for cid, c in dev.items():
                if str(cid).startswith("note") and c.get("state") in ("on", "down", 1, True):
                    try:
                        self.active.add(int(str(cid).split("note")[-1].strip(" _:")))
                    except ValueError:
                        pass

    def draw(self, canvas: Canvas):
        if self.obj is None:
            return
        if not self.keys:                         # sin keys.json: solo el contorno del teclado
            canvas.polygon(self.obj.contour, bgr(self.p["color_blancas"]), thickness=self.p["grosor"])
            return
        step = max(1, int(self.p["solo_cada"]))
        # rellenos de todas las teclas en UNA capa y una sola mezcla (61 mezclas por tick bajaban el runtime a 9 fps)
        layer = None; alpha = float(self.p["relleno"])
        for i, k in enumerate(self.keys):
            if i % step:
                continue
            poly = k.get("poligono") or k.get("contorno")
            if not poly:
                continue
            pts = np.array([canvas.project(float(x), float(y)) for x, y in poly], np.int32).reshape(-1, 1, 2)
            col = bgr(self.p["color_negras"] if k.get("color") == "negra" else self.p["color_blancas"])
            if k.get("nota") in self.active:
                cv2.fillPoly(canvas.img, [pts], bgr(self.p["color_activa"]))
            elif alpha > 0:
                if layer is None:
                    layer = canvas.img.copy()
                cv2.fillPoly(layer, [pts], col)
        if layer is not None:
            cv2.addWeighted(layer, alpha, canvas.img, 1.0 - alpha, 0, dst=canvas.img)
        for i, k in enumerate(self.keys):
            if i % step:
                continue
            poly = k.get("poligono") or k.get("contorno")
            if not poly:
                continue
            col = bgr(self.p["color_negras"] if k.get("color") == "negra" else self.p["color_blancas"])
            canvas.polygon(poly, col, thickness=int(self.p["grosor"]))
            if self.p["nombres"] and k.get("nombre"):
                c = k.get("punto_toque") or k.get("centro")
                if c:
                    x, y = canvas.project(float(c[0]), float(c[1]))
                    cv2.putText(canvas.img, str(k["nombre"]), (x - 9, y + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.32, col, 1, cv2.LINE_AA)
