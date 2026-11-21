"""Parallax: la foto del objeto se corre sutilmente según una "cabeza virtual", como si tuviera profundidad.

Cada píxel de la textura se desplaza en proporción a su profundidad (lo cercano se mueve más que lo lejano) en la
dirección contraria a la cabeza, y se sombrea como si una luz viniera desde ella. La profundidad sale del relieve del
escaneo (`height_proj.npy`, vía `scan_geom.relief`) si el objeto tiene relieve de verdad; en un cuadro plano se usa
una profundidad aparente (lo claro adelante, un abombado suave hacia el centro). La cabeza: param `cabeza` [x, y]
en -1..1, si no la primera mano o toque sobre la mesa (`touchsynth.registry.touch_state`), si no un vaivén lento.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .base import Effect, Canvas
from .. import cuadros as cq
from .. import scan_geom as sg


class Parallax(Effect):
    name = "parallax"
    label = "Parallax (profundidad que sigue a la cabeza)"
    aliases = ("parallax", "paralaje", "profundidad", "3d", "que se mueva con la mano", "relieve", "cabeza")
    description = ("Parallax: la foto del objeto se desplaza sutilmente según una cabeza virtual, como si tuviera "
                   "profundidad (relieve del escaneo o, en cuadros planos, profundidad aparente por luz). La cabeza: "
                   "param cabeza [x, y] en -1..1, si no la mano/toque sobre la mesa, si no un vaivén lento. Params: "
                   "desplazamiento (px), sombra, profundidad ('auto' | 'relieve' | 'luz'), suavizado, textura, "
                   "intensidad. capa -10. Sin objeto: el cuadro más grande.")
    needs = ("textures", "geometry", "people")
    targets = ("*",)
    params = {"capa": -10, "desplazamiento": 14.0, "sombra": 0.35, "profundidad": "auto", "cabeza": None,
              "suavizado": 3.0, "textura": 0.9, "intensidad": 1.0, "vaiven_s": 9.0, "borde_suave": 6}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self.prep = None
        self._key = None
        self.head = np.zeros(2, np.float32)
        self.source = "vaiven"

    def _depth(self, scene, pt) -> tuple[np.ndarray, str]:
        x0, y0, w, h = pt.roi
        sd = sg.scene_scan(scene)
        mode = str(self.p.get("profundidad") or "auto").lower()
        if mode in ("auto", "relieve") and sd is not None and sd.height is not None:
            dx, dy = sg.offset_of(scene.objects.get(pt.obj_id)) if scene.objects.get(pt.obj_id) else (0.0, 0.0)
            hh = sd.height[max(0, int(y0 - dy)):int(y0 - dy) + h, max(0, int(x0 - dx)):int(x0 - dx) + w]
            vals = hh[pt.mask[:hh.shape[0], :hh.shape[1]]] if hh.size else np.zeros(0)
            iqr = float(np.subtract(*np.percentile(vals, [75, 25]))) if vals.size > 50 else 0.0
            if mode == "relieve" or iqr > 6.0:                                   # relieve de verdad (no ruido)
                return sg.relief(sd, int(x0 - dx), int(y0 - dy), w, h, pt.mask, blur=12.0), "relieve"
        gray = cv2.cvtColor(pt.tex8, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        lum = cv2.GaussianBlur(gray, (0, 0), 5)
        m = lum[pt.mask]
        if m.size:
            lo, hi = np.percentile(m, 3), np.percentile(m, 97)
            lum = np.clip((lum - lo) / max(1e-3, hi - lo), 0, 1)
        bulge = np.clip(1.0 - pt.rn ** 2, 0, 1)
        return (0.6 * lum + 0.4 * bulge).astype(np.float32), "luz"

    def _prepare(self, scene, obj):
        pt = cq.prepare(scene, obj, feather=float(self.p["borde_suave"]))
        if pt is None:
            return None
        depth, kind = self._depth(scene, pt)
        x0, y0, w, h = pt.roi
        sc = 0.25
        ws, hs = max(2, int(w * sc)), max(2, int(h * sc))
        d_s = cv2.resize(depth, (ws, hs), interpolation=cv2.INTER_AREA) - 0.5    # medio = quieto
        gx = cv2.Sobel(d_s, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        gy = cv2.Sobel(d_s, cv2.CV_32F, 0, 1, ksize=3) / 8.0
        X = ((np.arange(ws, dtype=np.float32) + 0.5) * (w / ws) - 0.5)[None, :].repeat(hs, 0)
        Y = ((np.arange(hs, dtype=np.float32) + 0.5) * (h / hs) - 0.5)[:, None].repeat(ws, 1)
        gn = max(1e-3, float(np.percentile(np.abs(np.concatenate([gx.ravel(), gy.ravel()])), 98)))
        return {"pt": pt, "d_s": d_s.astype(np.float32), "gx": (gx / gn).astype(np.float32),
                "gy": (gy / gn).astype(np.float32), "X": X, "Y": Y, "kind": kind, "size": (w, h)}

    def update(self, dt, scene):
        super().update(dt, scene)
        obj = cq.pick(scene, self.target)
        if obj is None:
            self.prep = None
            return
        key = cq.obj_key(scene, obj, str(self.p.get("profundidad")), float(self.p["borde_suave"]))
        if key != self._key:
            self.prep, self._key = self._prepare(scene, obj), key
        if self.prep is None:
            return
        pt = self.prep["pt"]
        target, self.source = None, "vaiven"
        cab = self.p.get("cabeza")
        if isinstance(cab, (list, tuple)) and len(cab) >= 2:
            target, self.source = np.array([float(cab[0]), float(cab[1])], np.float32), "param"
        else:
            ptr = cq.pointer(scene)
            if ptr is not None:
                x0, y0, w, h = pt.roi
                cx, cy = x0 + pt.center[0], y0 + pt.center[1]
                R = float(np.median(pt.extra["R"]))
                target = np.clip(np.array([(ptr[0] - cx) / R, (ptr[1] - cy) / R], np.float32), -1.5, 1.5)
                self.source = "mano"
        if target is None:
            per = max(1.0, float(self.p["vaiven_s"]))
            u = 2 * math.pi * self.t / per
            target = np.array([0.8 * math.sin(u), 0.45 * math.sin(0.63 * u + 1.0)], np.float32)
        a = 1.0 - math.exp(-dt * max(0.1, float(self.p["suavizado"])))
        self.head += (np.clip(target, -1.5, 1.5) - self.head) * a

    def draw(self, canvas: Canvas):
        pr = self.prep
        if pr is None:
            return
        pt = pr["pt"]
        hx, hy = float(self.head[0]), float(self.head[1])
        amp = float(self.p["desplazamiento"])
        D = pr["d_s"]
        # lo cercano se corre al revés de la cabeza (muestreamos hacia la cabeza)
        mx = (pr["X"] + D * (hx * amp)).astype(np.float32)
        my = (pr["Y"] + D * (hy * amp)).astype(np.float32)
        w, h = pr["size"]
        MX = cv2.resize(mx, (w, h), interpolation=cv2.INTER_LINEAR)
        MY = cv2.resize(my, (w, h), interpolation=cv2.INTER_LINEAR)
        tex = cv2.remap(pt.tex8, MX, MY, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        # sombreado: luz desde la cabeza sobre la pendiente de la profundidad
        shade = 1.0 + float(self.p["sombra"]) * (pr["gx"] * (-hx) + pr["gy"] * (-hy))
        sh8 = cv2.resize(np.clip(shade * 128.0, 0, 255).astype(np.uint8), (w, h), interpolation=cv2.INTER_LINEAR)
        k = float(self.p["textura"]) * float(self.p["intensidad"])
        a = cv2.multiply(pt.alpha8, cv2.merge([sh8, sh8, sh8]), scale=1.0 / 128)
        out = cv2.multiply(tex, a, scale=k / 255)
        cq.blit(canvas, pt, out)

    def state(self):
        return {**super().state(), "cabeza": [round(float(v), 3) for v in self.head], "fuente": self.source,
                "profundidad": self.prep["kind"] if self.prep else None}
