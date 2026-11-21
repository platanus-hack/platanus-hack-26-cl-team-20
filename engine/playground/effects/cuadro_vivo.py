"""Cuadro vivo: la pintura cobra vida en su lugar exacto.

La foto del cuadro (recorte de scene_pov por su contorno, 1 px = 1 px del proyector) vuelve a caer sobre sí misma y se
mueve: las zonas lisas (cielo, agua, fondos) ondulan como agua y viento y las zonas con detalle quedan firmes; todo
respira ±1,5 % alrededor de su centro. Los bordes de la pintura (Canny) y el contorno se encienden con una luz que
recorre el cuadro dando la vuelta, y de las zonas claras salen partículas que suben. `audio='bass'` lo hace latir con
el bajo del runtime (`scene.audio`). Cualquier objeto de la escena viva; sin objeto, el cuadro más grande.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .base import Effect, Canvas
from .. import cuadros as cq
from ..audio.features import audio_level, audio_mode

_x = np.arange(256, dtype=np.float32) / 256.0
WAVE_LUT = (0.18 + 0.82 * (0.5 + 0.5 * np.cos(2 * np.pi * _x)) ** 10)


class CuadroVivo(Effect):
    name = "cuadro_vivo"
    label = "Cuadro vivo (la pintura se mueve)"
    aliases = ("cuadro vivo", "pintura viva", "que cobre vida", "dale vida al cuadro", "vivo", "ondula", "agua",
               "viento en el cuadro", "anima el cuadro", "anima la pintura")
    description = ("Cuadro vivo: la foto del cuadro proyectada encima en su lugar exacto, con las zonas lisas "
                   "ondulando como agua/viento, respiración ±1,5 %, bordes de la pintura y contorno que se encienden "
                   "con una luz que da la vuelta, y partículas que salen de las zonas claras. Params: ondulacion (px), "
                   "viento, respiracion (0.015), velocidad, textura, bordes, color_bordes, particulas (por segundo), "
                   "color_particulas, intensidad; audio='bass' late con el bajo. capa -10. Sin objeto: el cuadro más grande.")
    needs = ("textures", "contours", "beat")
    targets = ("*",)
    params = {"capa": -10, "ondulacion": 7.0, "viento": 1.0, "respiracion": 0.015, "velocidad": 1.0, "textura": 0.85,
              "bordes": 1.0, "color_bordes": [255, 210, 140], "vuelta_s": 5.0, "particulas": 30,
              "color_particulas": [255, 230, 170], "intensidad": 1.0, "audio": "", "audio_gain": 1.0, "borde_suave": 6}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self.prep = None
        self._key = None
        self.lvl = None
        self.dt = 1 / 30
        self.rng = np.random.default_rng(5)
        self.parts = np.zeros((0, 7), np.float32)       # x y vx vy vida vida0 brillo
        self._acc = 0.0
        self._layer = None

    def _prepare(self, scene, obj):
        pt = cq.prepare(scene, obj, feather=float(self.p["borde_suave"]))
        if pt is None:
            return None
        x0, y0, w, h = pt.roi
        tex8 = pt.tex8
        gray = cv2.cvtColor(tex8, cv2.COLOR_BGR2GRAY)
        # zonas: lo liso se mueve (agua, cielo, fondos), lo que tiene detalle queda firme
        gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        g = cv2.GaussianBlur(cv2.magnitude(gx, gy), (0, 0), 6)
        gm = g[pt.mask]
        hi = float(np.percentile(gm, 90)) if gm.size else 1.0
        flat = np.clip(1.0 - g / max(1e-3, hi), 0, 1) ** 1.5 * pt.alpha
        sc = 0.25
        ws, hs = max(2, int(w * sc)), max(2, int(h * sc))
        X = ((np.arange(ws, dtype=np.float32) + 0.5) * (w / ws) - 0.5)[None, :].repeat(hs, 0)
        Y = ((np.arange(hs, dtype=np.float32) + 0.5) * (h / hs) - 0.5)[:, None].repeat(ws, 1)
        edges = cv2.Canny(cv2.GaussianBlur(tex8, (0, 0), 1.2), 60, 140)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
        edges = (cv2.GaussianBlur(edges, (0, 0), 1.2).astype(np.float32) * pt.alpha).astype(np.uint8)
        bgra = cv2.merge([*cv2.split(tex8), edges])                       # foto + bordes: un solo remap por cuadro
        # contorno: banda de ~5 px por dentro del borde (la luz que recorre el cuadro)
        inner = cv2.distanceTransform(pt.extra["full"].astype(np.uint8), cv2.DIST_L2, 3)
        rim8 = (np.clip(1.0 - inner / 5.0, 0, 1) * pt.extra["full"] * 255).astype(np.uint8)
        theta8 = ((pt.theta + np.pi) / (2 * np.pi) * 256).astype(np.int32).clip(0, 255).astype(np.uint8)
        # fuentes de partículas: lo más claro de la pintura
        hsv = cv2.cvtColor(tex8, cv2.COLOR_BGR2HSV)
        v = hsv[..., 2].astype(np.float32) * pt.mask
        src = np.zeros((0, 2), np.float32)
        if pt.mask.any():
            thr = np.percentile(v[pt.mask], 92)
            ys, xs = np.nonzero(v >= max(thr, 1))
            if len(xs):
                pick = self.rng.choice(len(xs), min(1500, len(xs)), replace=False)
                src = np.column_stack([xs[pick], ys[pick]]).astype(np.float32)
        return {"pt": pt, "bgra": bgra, "flat_s": cv2.resize(flat, (ws, hs), interpolation=cv2.INTER_AREA),
                "X": X, "Y": Y, "rim8": rim8, "theta8": theta8, "src": src, "size": (w, h)}

    def update(self, dt, scene):
        super().update(dt, scene)
        self.dt = dt
        obj = cq.pick(scene, self.target)
        if obj is None:
            self.prep = None
            return
        key = cq.obj_key(scene, obj, float(self.p["borde_suave"]))
        if key != self._key:
            self.prep, self._key = self._prepare(scene, obj), key
            self.parts = np.zeros((0, 7), np.float32)
        mode = audio_mode(self.p.get("audio"))
        self.lvl = None if mode is None else audio_level(getattr(scene, "audio", None), mode, self.p.get("audio_gain", 1.0))
        self.beat = cq.beat_env(scene, self.t)

    def _particles(self, out, pump):
        pr = self.prep
        dt = self.dt
        P = self.parts
        if len(P):
            P[:, 4] -= dt
            P = P[P[:, 4] > 0]
            sway = np.sin(P[:, 1] * 0.03 + self.t * 2.0).astype(np.float32) * 6.0
            P[:, 0] += (P[:, 2] + sway) * dt
            P[:, 1] += P[:, 3] * dt
        rate = float(self.p["particulas"]) * (1.0 + 3.0 * pump)
        self._acc += rate * dt
        n = int(self._acc)
        self._acc -= n
        if n > 0 and len(pr["src"]) and len(P) < 260:
            n = min(n, 260 - len(P))
            s = pr["src"][self.rng.integers(0, len(pr["src"]), n)]
            life = self.rng.uniform(1.4, 3.2, n).astype(np.float32)
            new = np.column_stack([s[:, 0], s[:, 1], self.rng.normal(0, 6, n), -self.rng.uniform(18, 55, n),
                                   life, life, self.rng.uniform(0.5, 1.0, n)]).astype(np.float32)
            P = np.vstack([P, new])
        self.parts = P
        col = cq.rgb01(self.p["color_particulas"]) * 255 * float(self.p["intensidad"])
        lay = self._layer
        if lay is None or lay.shape != out.shape:
            lay = self._layer = np.zeros_like(out)
        else:
            lay.fill(0)
        for x, y, _, _, life, life0, br in P:
            f = br * math.sin(math.pi * life / life0)                    # aparece y se apaga
            c = (int(col[0] * f), int(col[1] * f), int(col[2] * f))
            cv2.circle(lay, (int(x), int(y)), 1 if br < 0.8 else 2, c, -1, cv2.LINE_AA)
        cv2.add(out, lay, dst=out)                                       # luz que se suma: nunca tapa la pintura

    def draw(self, canvas: Canvas):
        pr = self.prep
        if pr is None:
            return
        pt = pr["pt"]
        t = self.t * float(self.p["velocidad"])
        k = float(self.p["intensidad"])
        lvl = self.lvl
        pump = lvl if lvl is not None else 0.0
        ph = math.sin(2 * math.pi * 0.17 * t)                                    # ~6 s por respiración
        breath = 1.0 + float(self.p["respiracion"]) * (ph + 0.8 * pump)
        cx, cy = pt.center
        X, Y, F = pr["X"], pr["Y"], pr["flat_s"]
        amp = float(self.p["ondulacion"]) * (1.0 + 0.8 * pump)
        wind = float(self.p["viento"])
        gust = 0.6 + 0.4 * np.sin(X * 0.006 - 0.6 * t)                         # ráfagas que cruzan el cuadro
        dx = amp * F * (0.6 * np.sin(Y * 0.045 + 1.7 * t) + 0.4 * wind * gust * np.sin(X * 0.013 + Y * 0.02 - 0.9 * t))
        dy = amp * 0.5 * F * np.sin(X * 0.03 - 1.3 * t)
        mx = (cx + (X - cx) / breath + dx).astype(np.float32)
        my = (cy + (Y - cy) / breath + dy).astype(np.float32)
        w, h = pr["size"]
        MX = cv2.resize(mx, (w, h), interpolation=cv2.INTER_LINEAR)
        MY = cv2.resize(my, (w, h), interpolation=cv2.INTER_LINEAR)
        moved = cv2.remap(pr["bgra"], MX, MY, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        tex = cv2.cvtColor(moved, cv2.COLOR_BGRA2BGR)                          # copias contiguas (rápidas)
        edges = cv2.extractChannel(moved, 3)
        # luz que da la vuelta: bordes de la pintura + contorno, por una tabla angular que gira
        vuelta = max(0.5, float(self.p["vuelta_s"]))
        off = np.uint8(int(self.t / vuelta * 256) % 256)
        wave8 = cv2.LUT(pr["theta8"] + off, (WAVE_LUT * 255).astype(np.uint8))
        light = cv2.add(cv2.multiply(edges, wave8, scale=1.0 / 255), cv2.multiply(pr["rim8"], wave8, scale=1.0 / 255))
        e_k = float(self.p["bordes"]) * k * (0.55 + 0.45 * self.beat if lvl is None else 0.35 + 1.3 * lvl)
        tk = float(self.p["textura"]) * k * (0.86 + 0.14 * ph + 0.2 * pump)
        out = cv2.multiply(tex, pt.alpha8, scale=tk / 255)                      # foto × borde suave × brillo, una pasada
        out = cv2.add(out, cq.tint(light, cq.rgb01(self.p["color_bordes"]), e_k))
        if float(self.p["particulas"]) > 0:
            self._particles(out, pump)
        cq.blit(canvas, pt, out)
