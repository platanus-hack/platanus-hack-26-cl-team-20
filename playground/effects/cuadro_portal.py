"""Portal: el cuadro se vuelve una puerta a otro lado.

Borde luminoso que respira (y un halo por fuera), adentro un túnel con la forma del propio cuadro: anillos que vienen
hacia uno, niebla que gira con parallax lento, estrellas que salen del fondo y un núcleo de luz. Se abre y se cierra
con un iris (también con la forma del cuadro) por el parámetro `abierto` (0..1) o tocándolo. Funciona con cualquier
objeto de la escena viva (cuadros, planos, pantallas); sin objeto, el cuadro más grande.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .base import Effect, Canvas
from .. import cuadros as cq
from ..audio.features import audio_level, audio_mode


def _noise(seed: int = 7, n: int = 128) -> np.ndarray:
    """Ruido suave y periódico (se puede leer con BORDER_WRAP sin costuras), 0..1 float32."""
    rng = np.random.default_rng(seed)
    base = rng.random((n // 8, n // 8)).astype(np.float32)
    big = cv2.resize(np.tile(base, (3, 3)), (3 * n, 3 * n), interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (0, 0), n / 24)
    out = big[n:2 * n, n:2 * n]
    return ((out - out.min()) / max(1e-6, np.ptp(out))).astype(np.float32)


RN_SCALE = 200.0                                    # rn8 = rn × 200 (1.0 = borde del cuadro = 200)
_x = np.arange(256, dtype=np.float32) / 256.0
RING_LUT = ((0.5 + 0.5 * np.cos(2 * np.pi * _x)) ** 6 * 255).astype(np.uint8)
FOG_LUT = (np.clip(np.arange(256, dtype=np.float32) / 255.0, 0, 1) ** 1.6 * 255).astype(np.uint8)


class CuadroPortal(Effect):
    name = "cuadro_portal"
    label = "Portal (el cuadro se abre)"
    aliases = ("portal", "cuadro portal", "abre el cuadro", "abre el portal", "cierra el portal", "túnel", "tunel",
               "agujero de gusano", "puerta", "estrellas en el cuadro")
    description = ("Portal: el cuadro se vuelve una puerta. Borde luminoso que respira con halo por fuera; adentro un "
                   "túnel con la forma del cuadro (anillos que avanzan, niebla que gira, estrellas, núcleo de luz). "
                   "Se abre/cierra con un iris: param abierto (1 abierto, 0 cerrado) o tocando el cuadro. Params: "
                   "color_borde, color_tunel, color_fondo, estrellas, velocidad, intensidad, halo_px, velocidad_iris; "
                   "audio='bass' late con el bajo. capa -5. Sirve en cualquier cuadro/plano; sin objeto, el más grande.")
    needs = ("contours", "beat")
    targets = ("*",)
    params = {"capa": -5, "abierto": 1.0, "velocidad_iris": 0.6, "color_borde": [120, 220, 255],
              "color_tunel": [90, 160, 255], "color_fondo": [70, 20, 140], "estrellas": 90, "velocidad": 1.0,
              "intensidad": 1.0, "halo_px": 36, "respiracion": 1.0, "toque": True, "audio": "", "audio_gain": 1.0,
              "resolucion": 0.35}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self.prep = None
        self._key = None
        self.aper = 0.0                       # apertura actual del iris (arranca cerrado y se abre: entrada)
        self.lvl = None
        self._seq = None
        self.dt = 1 / 30
        self.rng = np.random.default_rng(11)
        self.noise8 = (_noise() * 255).astype(np.uint8)
        self.stars = None

    # ------------------------------------------------------------------ preparación
    def _prepare(self, scene, obj):
        """Mapas estáticos a baja resolución en uint8: fase de los anillos, profundidad, núcleo, borde y halo; el
        radio normalizado cuantizado (rn8, 255 fuera del cuadro) sirve para el iris con una tabla por cuadro."""
        pad = float(self.p["halo_px"])
        pt = cq.prepare(scene, obj, feather=3.0, pad=pad)
        if pt is None:
            return None
        sc = float(min(1.0, max(0.15, self.p["resolucion"])))
        rn, th, size = cq.polar_grid(pt, sc)
        mask_s = cq.small(pt.mask.astype(np.float32), size)
        out_s = np.clip(1.0 - cq.small(pt.extra["full"].astype(np.float32), size), 0, 1)
        z = 1.0 / (rn + 0.07)
        halo_n = max(0.02, pad / max(1.0, float(np.median(pt.extra["R"]))))
        depth = np.clip(rn * 1.15, 0, 1) ** 0.7                                 # el centro es lejos: más oscuro
        rim = np.exp(-((1.0 - rn) / 0.035) ** 2) * mask_s * 0.9 + np.exp(-np.maximum(rn - 1.0, 0) / halo_n) * out_s * 0.55
        u8 = lambda f: np.clip(f * 255.0, 0, 255).astype(np.uint8)
        rn8 = np.clip(rn * RN_SCALE, 0, 254).astype(np.uint8)
        rn8[mask_s < 0.5] = 255                                                  # fuera del cuadro: nunca hay iris
        fs = (max(2, size[0] // 2), max(2, size[1] // 2))                        # la niebla es suave: a media resolución
        return {"pt": pt, "size": size, "rn8": rn8,
                "zph8": (np.mod(z * 2.4 / (2 * np.pi), 1.0) * 256).astype(np.uint8),   # fase de los anillos
                "u": cv2.resize(((th + np.pi) / (2 * np.pi) * 128 * 3).astype(np.float32), fs, interpolation=cv2.INTER_NEAREST),
                "v": cv2.resize((z * 18.0).astype(np.float32), fs, interpolation=cv2.INTER_AREA),
                "depth8": u8(depth), "fogdepth8": u8(0.35 + 0.65 * depth), "core8": u8(np.exp(-(rn / 0.13) ** 2)),
                "rim8": u8(rim)}

    def _iris_lut(self, a: float) -> np.ndarray:
        rn = np.arange(256, dtype=np.float32) / RN_SCALE
        lut = np.clip((a - rn) * 30.0 + 0.5, 0, 1)
        lut[255] = 0.0
        return (lut * 255).astype(np.uint8)

    def _lip_lut(self, a: float) -> np.ndarray:
        rn = np.arange(256, dtype=np.float32) / RN_SCALE
        lut = np.exp(-((rn - a) / 0.018) ** 2)
        lut[255] = 0.0
        return (lut * 255).astype(np.uint8)

    def _stars(self, n):
        n = int(max(0, min(600, n)))
        return {"a": self.rng.uniform(-np.pi, np.pi, n), "d": self.rng.uniform(0.05, 1.0, n),
                "v": self.rng.uniform(0.6, 1.4, n)}

    # ------------------------------------------------------------------ ciclo
    def update(self, dt, scene):
        super().update(dt, scene)
        self.dt = dt
        obj = cq.pick(scene, self.target)
        if obj is None:
            self.prep = None
            return
        key = cq.obj_key(scene, obj, float(self.p["halo_px"]), float(self.p["resolucion"]))
        if key != self._key:
            self.prep, self._key = self._prepare(scene, obj), key
        if self.prep is None:
            return
        if self._seq is None:
            _, self._seq = cq.touches_inside(scene, self.prep["pt"], 10 ** 12)
        if self.p.get("toque", True):
            evs, self._seq = cq.touches_inside(scene, self.prep["pt"], self._seq)
            for _ in evs:                                                     # tocar el cuadro abre / cierra
                self.p["abierto"] = 0.0 if float(self.p["abierto"]) >= 0.5 else 1.0
        target = float(max(0.0, min(1.0, float(self.p["abierto"]))))
        step = float(self.p["velocidad_iris"]) * dt
        self.aper += max(-step, min(step, target - self.aper))
        mode = audio_mode(self.p.get("audio"))
        self.lvl = None if mode is None else audio_level(getattr(scene, "audio", None), mode, self.p.get("audio_gain", 1.0))
        self.beat = cq.beat_env(scene, self.t)
        n = int(self.p["estrellas"])
        if self.stars is None or len(self.stars["a"]) != max(0, min(600, n)):
            self.stars = self._stars(n)

    def draw(self, canvas: Canvas):
        """Todo en uint8 a baja resolución con tablas (LUT) y OpenCV; se escala una vez y las estrellas van a 1:1."""
        pr = self.prep
        if pr is None:
            return
        pt = pr["pt"]
        t = self.t * float(self.p["velocidad"])
        k = float(self.p["intensidad"])
        lvl = self.lvl
        breath = 0.5 + 0.5 * math.sin(2 * math.pi * 0.18 * self.t)
        pump = (0.25 + 0.75 * lvl) if lvl is not None else 0.35 * self.beat
        a = self.aper
        cb, ct, cf = cq.rgb01(self.p["color_borde"]), cq.rgb01(self.p["color_tunel"]), cq.rgb01(self.p["color_fondo"])
        # anillos que avanzan: la fase estática + un corrimiento por cuadro (suma uint8 que da la vuelta) y una tabla
        off = np.uint8(int(-t * 3.2 / (2 * np.pi) * 256) % 256)
        ring8 = cv2.LUT(pr["zph8"] + off, RING_LUT)
        tunnel8 = cv2.multiply(ring8, pr["depth8"], scale=1.0 / 255)
        # niebla: dos capas de ruido que giran y avanzan a distinta velocidad (parallax lento)
        fog1 = cv2.remap(self.noise8, pr["u"] + np.float32(t * 9.0), pr["v"] - np.float32(t * 22.0), cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_WRAP)
        fog2 = cv2.remap(self.noise8, pr["u"] * np.float32(0.5) - np.float32(t * 5.0),
                         pr["v"] * np.float32(0.45) + np.float32(40 - t * 9.9), cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
        fog = cv2.resize(cv2.addWeighted(fog1, 0.55, fog2, 0.45, 0), pr["size"], interpolation=cv2.INTER_LINEAR)
        fog8 = cv2.multiply(fog, pr["fogdepth8"], scale=1.0 / 255)
        fog8 = cv2.LUT(fog8, FOG_LUT)
        inner = cv2.add(cq.tint(fog8, cf, 1.2), cq.tint(tunnel8, ct, 1.2 * (0.55 + 0.45 * pump)))
        inner = cv2.add(inner, cq.tint(pr["core8"], 0.6 * ct + 0.4, 1.2 * (0.5 + 0.5 * breath + 0.6 * pump)))
        iris8 = cv2.LUT(pr["rn8"], self._iris_lut(a))
        inner = cv2.multiply(inner, cv2.merge([iris8, iris8, iris8]), scale=1.0 / 255)
        rim_k = 0.45 + 0.4 * breath * float(self.p["respiracion"]) + 0.5 * pump
        img = cv2.add(inner, cq.tint(pr["rim8"], cb, rim_k))
        if 0.01 < a < 0.995:                                                     # filo luminoso del iris
            img = cv2.add(img, cq.tint(cv2.LUT(pr["rn8"], self._lip_lut(a)), cb, 1.4))
        x0, y0, w, h = pt.roi
        out = cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)
        if k != 1.0:
            out = cv2.convertScaleAbs(out, alpha=k)

        # estrellas a resolución completa: nacen en el fondo y vienen hacia uno (con estela)
        st = self.stars
        if a > 0.02 and st is not None and len(st["a"]):
            speed = 0.22 * float(self.p["velocidad"]) * (1.0 + 1.5 * pump)
            st["d"] -= self.dt * speed * st["v"]
            dead = st["d"] < 0.04
            if dead.any():
                st["d"][dead] = self.rng.uniform(0.7, 1.0, dead.sum())
                st["a"][dead] = self.rng.uniform(-np.pi, np.pi, dead.sum())
            R = pt.extra["R"]
            idx = ((st["a"] + np.pi) / (2 * np.pi) * len(R)).astype(int) % len(R)
            cx, cy = pt.center
            for ai, di, Ri in zip(st["a"], st["d"], R[idx]):
                r1 = 0.05 / di
                if r1 >= a * 0.97:
                    continue
                r0 = r1 * (0.9 - 0.25 * min(1.0, pump))                    # estela corta (más larga con el bajo)
                ca, sa = math.cos(ai), math.sin(ai)
                p1 = (int(cx + ca * r1 * Ri), int(cy + sa * r1 * Ri))
                p0 = (int(cx + ca * r0 * Ri), int(cy + sa * r0 * Ri))
                b = int(255 * k * min(1.0, 0.25 + 1.2 * (1 - di)))
                cv2.line(out, p0, p1, (b, b, b), 1 + int(di < 0.25), cv2.LINE_AA)
        cq.blit(canvas, pt, out)

    def state(self):
        return {**super().state(), "apertura": round(self.aper, 3)}
