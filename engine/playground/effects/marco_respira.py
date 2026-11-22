"""Marco que respira: solo la banda del borde del objeto (N px por dentro del contorno) se enciende.

Gradientes de color que recorren el perímetro (dos colores configurables, `vueltas` repeticiones), brillo que respira
lento y pulsa al beat (o al bajo con `audio='bass'`), y un brillo suave por fuera opcional. El perímetro se mide por
longitud de arco del contorno real (no por ángulo): la luz corre pareja por cualquier forma. Cualquier objeto de la
escena viva; sin objeto, el cuadro más grande.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .base import Effect, Canvas
from .. import cuadros as cq
from ..audio.features import audio_level, audio_mode


class MarcoRespira(Effect):
    name = "marco_respira"
    label = "Marco que respira"
    aliases = ("marco", "marco respira", "enciende el marco", "borde del cuadro", "marco de luz", "contorno de luz",
               "ilumina el marco")
    description = ("Marco que respira: solo una banda de `ancho` px por dentro del contorno, con gradientes entre "
                   "color y color2 que recorren el perímetro (vueltas, velocidad en vueltas/s), respiración lenta y "
                   "pulso al beat (audio='bass' para el bajo). brillo_exterior suma un resplandor por fuera. capa 5 "
                   "(encima de la foto). Sirve en cualquier objeto; sin objeto, el cuadro más grande.")
    needs = ("contours", "beat")
    targets = ("*",)
    params = {"capa": 5, "ancho": 26, "color": [255, 170, 60], "color2": [80, 140, 255], "vueltas": 2,
              "velocidad": 0.12, "pulso": 0.6, "respiracion": 0.35, "intensidad": 1.0, "brillo_exterior": 0.5,
              "audio": "", "audio_gain": 1.0}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self.prep = None
        self._key = None
        self.lvl = None
        self.beat = 0.0

    def _prepare(self, scene, obj):
        ancho = max(2.0, float(self.p["ancho"]))
        pt = cq.prepare(scene, obj, feather=0.0, pad=24 if float(self.p["brillo_exterior"]) > 0 else 0, exclude=False)
        if pt is None:
            return None
        x0, y0, w, h = pt.roi
        full = pt.extra["full"]
        # parámetro de perímetro por longitud de arco: cada píxel toma el del punto del contorno más cercano
        ring = cq._resample(np.asarray(pt.poly, float), 1.0)
        arc = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(np.vstack([ring, ring[:1]]), axis=0).T))])
        total = max(1.0, arc[-1])
        s_ring = arc[:-1] / total
        src = np.ones((h, w), np.uint8)
        xi = np.clip(np.round(ring[:, 0]).astype(int), 0, w - 1)
        yi = np.clip(np.round(ring[:, 1]).astype(int), 0, h - 1)
        simg = np.zeros((h, w), np.float32)
        order = np.argsort(s_ring)[::-1]                                      # el primero gana en píxeles repetidos
        simg[yi[order], xi[order]] = s_ring[order]
        src[yi, xi] = 0
        dist, labels = cv2.distanceTransformWithLabels(src, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
        zy, zx = np.nonzero(src == 0)                                         # etiquetas en orden de barrido
        lut = np.concatenate([[0.0], simg[zy, zx]]).astype(np.float32)
        s = lut[np.clip(labels, 0, len(lut) - 1)]
        band = np.clip(1.0 - dist / ancho, 0, 1) ** 1.4 * full                # brilla en el borde y se apaga hacia adentro
        vueltas = max(1, int(round(float(self.p["vueltas"]))))
        s8 = (np.mod(s * vueltas, 1.0) * 256).astype(np.int32).clip(0, 255).astype(np.uint8)
        a8 = (band * 255).astype(np.uint8)
        glow = None
        if float(self.p["brillo_exterior"]) > 0:
            outside = np.clip(1.0 - dist / 22.0, 0, 1) ** 2 * (~full)
            glow = (outside * 255).astype(np.uint8)
        return {"pt": pt, "s8": cv2.merge([s8, s8, s8]), "a8": cv2.merge([a8, a8, a8]),
                "glow8": None if glow is None else cv2.merge([glow, glow, glow])}

    def update(self, dt, scene):
        super().update(dt, scene)
        obj = cq.pick(scene, self.target)
        if obj is None:
            self.prep = None
            return
        key = cq.obj_key(scene, obj, float(self.p["ancho"]), int(round(float(self.p["vueltas"]))),
                         float(self.p["brillo_exterior"]) > 0)
        if key != self._key:
            self.prep, self._key = self._prepare(scene, obj), key
        mode = audio_mode(self.p.get("audio"))
        self.lvl = None if mode is None else audio_level(getattr(scene, "audio", None), mode, self.p.get("audio_gain", 1.0))
        self.beat = cq.beat_env(scene, self.t)

    def _palette(self, k: float) -> np.ndarray:
        """Tabla de 256 colores BGR: color → color2 → color (cíclica), con el brillo k."""
        c1, c2 = cq.rgb01(self.p["color"]), cq.rgb01(self.p["color2"])
        x = np.arange(256, dtype=np.float32) / 256.0
        m = (0.5 - 0.5 * np.cos(2 * np.pi * x))[:, None]
        shine = (0.55 + 0.45 * np.cos(2 * np.pi * x) ** 2)[:, None]           # dos crestas brillantes por ciclo
        pal = (c1 * (1 - m) + c2 * m) * shine * 255.0 * k
        return np.clip(pal, 0, 255).astype(np.uint8).reshape(256, 1, 3)

    def draw(self, canvas: Canvas):
        pr = self.prep
        if pr is None:
            return
        pt = pr["pt"]
        breath = 0.5 + 0.5 * math.sin(2 * math.pi * 0.16 * self.t)
        pulse = self.lvl if self.lvl is not None else self.beat
        k = float(self.p["intensidad"]) * (1.0 - float(self.p["respiracion"]) * (1 - breath)) * (
            0.7 + float(self.p["pulso"]) * 0.6 * pulse)
        off = np.uint8(int(self.t * float(self.p["velocidad"]) * 256) % 256)
        idx = pr["s8"] - off                                                   # uint8: da la vuelta sola
        col = cv2.LUT(idx, self._palette(min(2.0, k)))
        out = cv2.multiply(col, pr["a8"], scale=1.0 / 255)
        if pr["glow8"] is not None:
            g = cv2.multiply(col, pr["glow8"], scale=float(self.p["brillo_exterior"]) / 255)
            out = cv2.add(out, g)
        cq.blit(canvas, pt, out)
