"""Sombras largas: una lámpara virtual orbita la sala con luz rasante y cada objeto arrastra una sombra falsa.

La altura sale del relieve del escaneo (height_proj, solo dentro de los objetos con `fuente=objetos`, o todo el
relieve con `fuente=relieve`) o, sin escaneo, de la altura de bbox de cada objeto. La luz es direccional desde la
lámpara (azimut que gira cada `vuelta_s`, `elevacion` en grados: baja = sombras largas): se marcha el campo de
alturas hacia la lámpara con desplazamientos enteros (barato) y lo que queda tapado es sombra. Encima, Lambert con las
normales del relieve, una mancha de luz que cae lejos de la lámpara y el brillo de la propia lámpara.
"""

from __future__ import annotations

import math
import time

import cv2
import numpy as np

from .base import Effect, Canvas
from .. import ambiente as amb


class SombrasLargas(Effect):
    name = "sombras_largas"
    label = "Sombras largas"
    aliases = ("sombras largas", "lámpara que gira", "lampara que gira", "luz rasante", "sombras falsas", "atardecer",
               "sombras que giran", "lámpara virtual", "lampara virtual")
    description = ("Lámpara virtual que orbita con luz rasante: los objetos proyectan sombras largas falsas según su "
                   "altura (height_proj o bbox). vuelta_s (s por vuelta), elevacion (grados; baja = sombras largas), "
                   "azimut (grados fijos, '' = gira), altura (px por unidad de relieve), color, intensidad, ambiente, "
                   "sombra (0..1 qué tan negra), mancha (caída lejos de la lámpara), lampara (dibujar la lámpara), "
                   "objetos ('' = todos), fuente objetos|relieve.")
    needs = ("geometry", "contours")
    targets = ("*",)
    params = {"vuelta_s": 18.0, "elevacion": 16.0, "azimut": "", "altura": 160.0, "color": [255, 205, 140],
              "intensidad": 0.6, "ambiente": 0.04, "sombra": 0.92, "suavidad": 1.2, "pasos": 18, "mancha": 0.55,
              "lampara": True, "objetos": "", "fuente": "objetos", "escala": 8, "capa": -5}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self.prep = None
        self._key = None
        self.ms = 0.0
        self.lamp = (0.0, 0.0)

    def _prepare(self, scene):
        W, H = amb.scene_wh(scene)
        s = float(max(2, int(self.p["escala"])))
        z01, from_scan = amb.height01(scene, s)
        objs = amb.select(scene, self.p["objetos"], target=self.target)
        if from_scan and str(self.p["fuente"]).lower().startswith("obj") or (not from_scan and (self.p["objetos"] or self.target)):
            m = amb.mask_small((W, H), [p for _, p in objs], s, 2 * s).astype(np.float32)
            z01 = z01 * cv2.GaussianBlur(m, (0, 0), 0.8)
        zu = (z01 * float(self.p["altura"])).astype(np.float32)
        gy, gx = np.gradient(zu, s)
        inv = 1.0 / np.sqrt(gx * gx + gy * gy + 1.0)
        h, w = zu.shape
        X = ((np.arange(w, dtype=np.float32) + 0.5) * s)[None, :].repeat(h, 0)
        Y = ((np.arange(h, dtype=np.float32) + 0.5) * s)[:, None].repeat(w, 1)
        elev = math.radians(max(4.0, min(80.0, float(self.p["elevacion"]))))
        maxlen = min(float(self.p["altura"]) / math.tan(elev), 0.6 * max(W, H))
        M = int(math.ceil(maxlen / s)) + 2
        return {"W": W, "H": H, "s": s, "zu": zu, "zp": cv2.copyMakeBorder(zu, M, M, M, M, cv2.BORDER_CONSTANT, value=0),
                "M": M, "maxlen": maxlen, "tanE": math.tan(elev), "sinE": math.sin(elev), "cosE": math.cos(elev),
                "nx": (-gx * inv).astype(np.float32), "ny": (-gy * inv).astype(np.float32), "nz": inv.astype(np.float32),
                "X": X, "Y": Y, "occ": np.empty_like(zu), "tmp": np.empty_like(zu)}

    def update(self, dt, scene):
        super().update(dt, scene)
        objs = amb.select(scene, self.p["objetos"], target=self.target)
        key = (amb.shape_key(objs), str(self.p["objetos"]), str(self.p["fuente"]), int(self.p["escala"]),
               float(self.p["altura"]), float(self.p["elevacion"]), getattr(scene, "scan_dir", None), amb.scene_wh(scene))
        if key != self._key:
            self.prep, self._key = self._prepare(scene), key

    def azimuth(self) -> float:
        az = self.p.get("azimut")
        if az not in (None, ""):
            return math.radians(float(az))
        return 2 * math.pi * self.t / max(1.0, float(self.p["vuelta_s"]))

    def draw(self, canvas: Canvas):
        t0 = time.perf_counter()
        pr = self.prep
        if pr is None:
            return
        az = self.azimuth()
        ca, sa = math.cos(az), math.sin(az)
        W, H, s, M = pr["W"], pr["H"], pr["s"], pr["M"]
        zu, zp, occ, tmp = pr["zu"], pr["zp"], pr["occ"], pr["tmp"]
        h, w = zu.shape
        occ.fill(0.0)
        n = max(2, int(self.p["pasos"]))
        last = None
        for k in range(1, n + 1):                      # marcha hacia la lámpara: ¿algo más alto que el rayo?
            d = pr["maxlen"] * k / n
            dx, dy = int(round(d * ca / s)), int(round(d * sa / s))
            if (dx, dy) == last:
                continue
            last = (dx, dy)
            np.subtract(zp[M + dy:M + dy + h, M + dx:M + dx + w], zu, out=tmp)
            tmp -= d * pr["tanE"]
            np.maximum(occ, tmp, out=occ)
        shadow = np.clip(occ * (1 / 6.0), 0, 1)
        if float(self.p["suavidad"]) > 0:
            shadow = cv2.GaussianBlur(shadow, (0, 0), float(self.p["suavidad"]))
        lx, ly, lz = pr["cosE"] * ca, pr["cosE"] * sa, pr["sinE"]
        lam = pr["nx"] * lx + pr["ny"] * ly + pr["nz"] * lz
        rel = np.clip(lam * (1.0 / max(pr["sinE"], 0.25)), 0, 2)
        light = (0.55 + 0.45 * rel) * float(self.p["intensidad"])
        cx, cy = W / 2 + 0.46 * W * ca, H / 2 + 0.46 * H * sa      # la lámpara, en el borde de la sala
        self.lamp = (cx, cy)
        d2 = (pr["X"] - cx) ** 2 + (pr["Y"] - cy) ** 2
        fall = 1.0 - float(self.p["mancha"]) * np.clip(d2 * (1.0 / (max(W, H) ** 2)), 0, 1)
        total = light * (1.0 - float(self.p["sombra"]) * shadow) * fall + float(self.p["ambiente"])
        if self.p["lampara"]:
            total += np.exp(-d2 * (1.0 / (2 * 45.0 ** 2))) * 1.2
        lum = np.clip(total * 255.0, 0, 255).astype(np.uint8)
        r, g, b = (float(v) for v in self.p["color"][:3])
        layer = cv2.multiply(cv2.merge([lum, lum, lum]), (b, g, r, 0), scale=1 / 255.0)
        amb.to_canvas(canvas, layer, 0, 0, w * s, h * s)
        dt_ms = (time.perf_counter() - t0) * 1000
        self.ms = 0.9 * self.ms + 0.1 * dt_ms if self.ms else dt_ms

    def state(self) -> dict:
        return {**super().state(), "ms": round(self.ms, 2), "lampara_xy": [round(v, 1) for v in self.lamp]}
