"""Personas reiluminadas desde los dedos (pedido 046): DAViD da la normal y el recorte de cada persona que ve la
cámara; cada índice es un foco que la ilumina según la normal. Lógica en playground/people/ (capa en live.py,
sombreado en relight.py); acá solo se elige la salida y se dibuja."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import cv2
import numpy as np

from ..people import relight as R
from ..people.live import get_layer
from .base import Canvas, Effect

R = importlib.reload(R)     # guardar este archivo recarga también el sombreado (live.py no: el modelo sigue cargado)


def _lin_bgr(rgb) -> tuple[float, float, float]:
    r, g, b = (max(0.0, min(255.0, float(v))) / 255.0 for v in rgb[:3])
    return (b * b, g * g, r * r)


class RelightPeople(Effect):
    name = "relight_people"
    label = "Luces desde los dedos (personas)"
    description = ("DAViD (Microsoft) estima la normal y el recorte de cada persona que ve la cámara; cada dedo índice "
                   "es un foco que la ilumina como si fuera 3D. salida=pantalla: la cámara reiluminada en un recuadro "
                   "(x, y, w, h; por defecto todo el lienzo). salida=proyector: solo la luz, llevada al proyector con la "
                   "calibración cámara->proyector (calib). Color por mano (color, color2), intensidad, apertura del foco "
                   "en grados (360 = omni), radio (fracción del ancho), beat (cuánto late con el pulso). fuente: "
                   "video:<archivo> | url:<jpg> | cam:<n>.")
    aliases = ("luces desde los dedos", "luz en los dedos", "dedos linterna en la persona", "reilumina a la persona",
               "normales de personas", "david")
    needs = ("camera", "people", "beat")
    targets = ("*",)
    params = {"fuente": "video:captures/david_test/bharatanatyam_960.mp4", "salida": "pantalla",
              "x": 0, "y": 0, "w": 1920, "h": 1080,
              "color": [255, 190, 140], "color2": [120, 200, 255], "intensidad": 3.5, "apertura": 300,
              "radio": 0.32, "ambiente": 0.04, "fondo": 0.025, "exposicion": 2.5, "destello": 1.0, "beat": 0.0,
              "tamano": 384, "profundidad": False, "recorte_cada": 3, "ancho": 640,
              "calib": "captures/brio_calib.json", "capa": 0}

    _img = None
    _seq = -1
    _H = None
    _H_key = None

    def update(self, dt, scene):
        super().update(dt, scene)
        lay = get_layer(str(self.p["fuente"]), int(self.p["tamano"]), bool(self.p["profundidad"]),
                        work_w=int(self.p["ancho"]), alpha_every=int(self.p["recorte_cada"]))
        self._layer = lay
        b = lay.latest()
        if b is None:
            self._img = None
            return
        beat = float(self.p.get("beat") or 0)
        if b["seq"] == self._seq and beat <= 0 and not getattr(self, "_dirty", False):
            return                                            # mismo cuadro y sin pulso: no hay nada nuevo
        self._seq, self._dirty = b["seq"], False
        w, h = b["size"]
        gain = R.beat_gain(getattr(scene, "beat", None), beat)
        z = R.depth_to_z(b["depth"], b["alpha"], (h, w), relief_px=w * 0.12)
        colors = [_lin_bgr(self.p["color"]), _lin_bgr(self.p.get("color2") or self.p["color"])]
        lights = []
        for k, hd in enumerate(b["hands"]):
            lights += R.finger_lights([hd], z, color=colors[k % 2], intensity=float(self.p["intensidad"]) * gain,
                                      lift_px=w * 0.03, cone_deg=float(self.p["apertura"]),
                                      radius_px=w * float(self.p["radio"]))
        shade = R.light_map(R.to_image_frame(b["normal"]), z, lights, scale=0.5)
        if self.p["salida"] == "proyector":
            self._img = R.compose_projector(b["alpha"], shade, exposure=float(self.p["exposicion"]))
        else:
            self._img = R.compose_screen(b["frame"], b["alpha"], shade, lights, ambient=float(self.p["ambiente"]),
                                         background=float(self.p["fondo"]), exposure=float(self.p["exposicion"]),
                                         glow=float(self.p["destello"]))
        self._n_lights = len(lights)
        self._img_n = getattr(self, "_img_n", 0) + 1      # versión de la imagen (cache del dibujo)

    def set(self, key, value):
        super().set(key, value)
        self._dirty = True

    def _homography(self, w: int) -> np.ndarray | None:
        path = str(self.p.get("calib") or "")
        key = (path, w)
        if key == self._H_key:
            return self._H
        self._H_key, self._H = key, None
        try:
            d = json.loads(Path(path).read_text())
            H = np.array(d["H_brio_to_proj"], np.float64)
            cam_w = float((d.get("brio_size") or [1920, 1080])[0])
            S = np.diag([cam_w / w, cam_w / w, 1.0])             # px de trabajo -> px de la cámara calibrada
            self._H = H @ S
        except Exception:  # noqa: BLE001  (sin calibración: se dibuja en el recuadro)
            self._H = None
        return self._H

    def draw(self, canvas: Canvas):
        H_, W_ = canvas.img.shape[:2]
        x, y, w, h = (int(self.p[k]) for k in ("x", "y", "w", "h"))
        x, y = max(0, min(x, W_ - 2)), max(0, min(y, H_ - 2)); w, h = min(w, W_ - x), min(h, H_ - y)
        img = self._img
        if img is None:
            lay = getattr(self, "_layer", None)
            msg = "DAViD: " + (lay.error or lay.status if lay else "sin capa")
            cv2.putText(canvas.img, msg[:90], (x + 16, y + 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (180, 180, 180), 2, cv2.LINE_AA)
            return
        key = (getattr(self, "_img_n", 0), x, y, w, h, W_, H_, self.p["salida"])   # se escala/deforma una vez por resultado, no por tick
        cached = getattr(self, "_drawn", None)
        if self.p["salida"] == "proyector":
            Hm = self._homography(img.shape[1])
            if Hm is not None:
                if cached is None or cached[0] != key:
                    cached = self._drawn = (key, cv2.warpPerspective(img, Hm, (W_, H_), flags=cv2.INTER_LINEAR))
                cv2.add(canvas.img, cached[1], dst=canvas.img)    # luz: se suma a lo que haya debajo
                return
        ih, iw = img.shape[:2]                                   # cabe en el recuadro sin deformar
        s = min(w / iw, h / ih)
        tw, th = max(2, int(iw * s)), max(2, int(ih * s))
        ox, oy = x + (w - tw) // 2, y + (h - th) // 2
        roi = canvas.img[oy:oy + th, ox:ox + tw]
        if cached is None or cached[0] != key:
            cached = self._drawn = (key, cv2.resize(img, (tw, th), interpolation=cv2.INTER_LINEAR))
        r = cached[1]
        if self.p["salida"] == "proyector":
            cv2.add(roi, r, dst=roi)
        else:
            roi[:] = r

    def state(self) -> dict:
        st = super().state()
        lay = getattr(self, "_layer", None)
        if lay is not None:
            st["people"] = {**lay.info(), "lights": getattr(self, "_n_lights", 0)}
        return st
