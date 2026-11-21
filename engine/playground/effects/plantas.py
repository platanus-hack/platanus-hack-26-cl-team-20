"""Plantas: luz que crece desde la base de cada objeto irregular (plantas, libros apilados, figuras) hacia arriba.

Para cada objeto: una coordenada de crecimiento 0 (base) → 1 (punta) dentro de su contorno, del eje vertical de la
escena (abajo → arriba) mezclado con el relieve del escaneo si hay (lo bajo es base). Un frente de luz sube por esa
coordenada cada `crecer_s` segundos, deja el objeto encendido detrás y se apaga al llegar arriba; el "viento" ondula
el patrón de lado (más arriba, más se mueve) sin salirse del contorno y del frente saltan chispas que suben.
Objetos: `objetos` ('' = los de kind planta/flor/libros… o de contorno irregular), color por objeto (`colores`).
"""

from __future__ import annotations

import math
import time

import cv2
import numpy as np

from .base import Effect, Canvas
from .. import ambiente as amb
from .. import scan_geom as sg

PLANT_KINDS = ("planta", "plantas", "plant", "plants", "flor", "flores", "flower", "arbol", "árbol", "tree", "libros",
               "books", "pila", "maceta", "pot", "cactus", "helecho")
PLANT_COLORS = [[90, 255, 120], [255, 210, 90], [140, 255, 230], [255, 140, 210], [200, 255, 90], [120, 170, 255]]


def _default(o, poly) -> bool:
    k = str(o.kind).lower()
    if k in PLANT_KINDS or any(w in str(o.name).lower() for w in PLANT_KINDS):
        return True
    return k not in ("table", "screen", "wall", "pared") and amb.solidity(poly) < 0.86


class Plantas(Effect):
    name = "plantas"
    label = "Plantas que crecen"
    aliases = ("plantas", "que crezcan las plantas", "crece la planta", "luz que crece", "enciende las plantas",
               "chispas que suben", "viento", "jardín de luz", "jardin de luz")
    description = ("Luz que crece desde la base hacia arriba de cada objeto irregular (plantas, libros apilados), con "
                   "chispas que suben y viento (ondulación lateral suave). objetos ('' = plantas/libros o contorno "
                   "irregular; ids, kinds o '-id'), colores (lista RGB por objeto, '' = verde/oro/turquesa…), "
                   "crecer_s, intensidad, chispas (0..2), viento (0..2), base auto|abajo|relieve, banda de audio.")
    needs = ("contours", "geometry", "audio")
    targets = ("*",)
    params = {"objetos": "", "colores": "", "crecer_s": 4.5, "intensidad": 0.85, "chispas": 1.0, "viento": 0.6,
              "brillo_frente": 1.0, "base": "auto", "banda": "mid", "capa": 6}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self.items: list[dict] = []
        self._key = None
        self.pulse = amb.Pulse()
        self.sparks = np.zeros((0, 7), np.float32)       # x, y, vx, vy, edad, vida, i
        self.rng = np.random.default_rng(3)
        self.up = -1.0
        self.ms = 0.0

    def _select(self, scene):
        objs = amb.select(scene, self.p["objetos"], _default, target=self.target)
        if not objs and not self.p["objetos"] and self.target is None:      # escena sin nada irregular: todos
            objs = amb.select(scene)
        return objs

    def _prepare(self, scene, objs):
        W, H = amb.scene_wh(scene)
        sd = sg.scene_scan(scene)
        cols = self.p["colores"] or PLANT_COLORS
        items = []
        for i, (o, poly) in enumerate(objs):
            roi = sg.roi_of(poly, 4, (W, H))
            if roi is None:
                continue
            x0, y0, w, h = roi
            s = max(1, int(math.ceil(math.sqrt(w * h / 22000.0))))
            sw, sh = max(2, w // s), max(2, h // s)
            local = (poly - np.float32([x0, y0])) * np.float32([sw / w, sh / h])
            m = np.zeros((sh, sw), np.uint8)
            cv2.fillPoly(m, [np.round(local).astype(np.int32).reshape(-1, 1, 2)], 1)
            if m.sum() < 4:
                continue
            alpha = sg.feather(m > 0, max(1.0, 3.0 / s))
            ys, xs = np.nonzero(m)
            Y = np.repeat(np.arange(sh, dtype=np.float32)[:, None], sw, 1)
            X = np.repeat(np.arange(sw, dtype=np.float32)[None, :], sh, 0)
            ymin, ymax = float(ys.min()), float(ys.max())
            gv = (ymax - Y) / max(1.0, ymax - ymin) if self.up < 0 else (Y - ymin) / max(1.0, ymax - ymin)
            g = gv
            mode = str(self.p["base"]).lower()
            if mode in ("auto", "relieve") and sd is not None and sd.height is not None:
                rel = sg.relief(sd, x0, y0, w, h, sg.poly_mask((h, w), poly - np.float32([x0, y0])), blur=8.0)
                rel = cv2.resize(rel, (sw, sh), interpolation=cv2.INTER_AREA)
                g = rel if mode == "relieve" else 0.6 * gv + 0.4 * rel
            g = np.clip(g, 0, 1).astype(np.float32)
            pick = self.rng.choice(len(xs), size=min(400, len(xs)), replace=False)
            pts = np.stack([x0 + (xs[pick] + 0.5) * w / sw, y0 + (ys[pick] + 0.5) * h / sh, g[ys[pick], xs[pick]]], 1)
            items.append({"o": o, "roi": roi, "g": g, "alpha": alpha.astype(np.float32), "X": X, "Y": Y, "s": s,
                          "col": np.float32(amb.color_of(i, o.id, cols)), "ph": (i * 0.37) % 1.0, "pts": pts.astype(np.float32),
                          "L": 0.0})
        return items

    def update(self, dt, scene):
        super().update(dt, scene)
        self.up = amb.up_sign(scene)
        objs = self._select(scene)
        key = (amb.shape_key(objs), str(self.p["objetos"]), str(self.p["colores"]), str(self.p["base"]),
               getattr(scene, "scan_dir", None), amb.scene_wh(scene))
        if key != self._key:
            self.items, self._key = self._prepare(scene, objs), key
            self.sparks = self.sparks[:0]
        self.pulse.update(dt, scene, str(self.p["banda"]))
        lvl = self.pulse.level
        per = max(0.5, float(self.p["crecer_s"]))
        new = []
        for i, it in enumerate(self.items):
            it["L"] = (((self.t / per) + it["ph"]) % 1.0) * 1.3 + 0.25 * lvl
            rate = 16.0 * float(self.p["chispas"]) * (1 + 2 * lvl) * (1 if it["L"] <= 1.05 else 0.2)
            n = self.rng.poisson(rate * dt)
            if n <= 0:
                continue
            pts = it["pts"]
            near = np.nonzero(np.abs(pts[:, 2] - min(it["L"], 1.0)) < 0.08)[0]
            idx = self.rng.choice(near if len(near) else np.arange(len(pts)), size=n)
            sp = np.zeros((n, 7), np.float32)
            sp[:, 0], sp[:, 1] = pts[idx, 0], pts[idx, 1]
            sp[:, 2] = self.rng.normal(0, 12, n) + 25 * float(self.p["viento"])
            sp[:, 3] = self.up * self.rng.uniform(45, 110, n)
            sp[:, 5] = self.rng.uniform(0.8, 1.8, n)
            sp[:, 6] = i
            new.append(sp)
        sp = np.concatenate([self.sparks, *new]) if new else self.sparks
        if len(sp):
            sp[:, 0] += sp[:, 2] * dt + np.sin(sp[:, 4] * 6 + sp[:, 6]) * 0.6
            sp[:, 1] += sp[:, 3] * dt
            sp[:, 4] += dt
            sp = sp[sp[:, 4] < sp[:, 5]][-500:]
        self.sparks = sp

    def draw(self, canvas: Canvas):
        t0 = time.perf_counter()
        k_int = float(self.p["intensidad"])
        wind = float(self.p["viento"])
        bf = float(self.p["brillo_frente"])
        for it in self.items:
            L = it["L"]
            fade = 1.0 - min(1.0, max(0.0, (L - 1.0) / 0.3))
            if fade <= 0:
                continue
            g = it["g"]
            if wind > 0:                                       # viento: el patrón se mece de lado, más arriba más
                dx = (wind * 9.0 / it["s"]) * (g ** 1.5) * np.sin(2 * math.pi * 0.35 * self.t + it["Y"] * (0.02 * it["s"]) + it["ph"] * 6)
                g = cv2.remap(g, it["X"] + dx.astype(np.float32), it["Y"], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            Lc = min(L, 1.0)
            lit = np.clip((Lc - g) * (1 / 0.12), 0, 1) * (0.2 + 0.6 * g)
            front = np.exp(-((g - Lc) * (1 / 0.035)) ** 2) * (1.0 if L <= 1.0 else 0.0)
            veins = 0.85 + 0.15 * np.sin(g * 40 - self.t * 3)
            v = (lit * veins + front * bf) * (fade * k_int) * it["alpha"]
            layer = v[..., None] * it["col"] + (front * (0.35 * fade * k_int) * it["alpha"])[..., None]
            x0, y0, w, h = it["roi"]
            amb.to_canvas(canvas, np.clip(layer * 255, 0, 255).astype(np.uint8), x0, y0, w, h)
        sp = self.sparks
        if len(sp):
            P = amb.project_pts(canvas, sp[:, :2])
            life = 1.0 - sp[:, 4] / sp[:, 5]
            r_px = max(1, int(round(2 * canvas.px_per_mm)))
            for (px, py), lf, i in zip(P, life, sp[:, 6].astype(int)):
                if i >= len(self.items):
                    continue
                b, g_, r = self.items[i]["col"] * (255 * lf * k_int) + 60 * lf
                cv2.circle(canvas.img, (int(px), int(py)), r_px, (int(min(255, b)), int(min(255, g_)), int(min(255, r))), -1, cv2.LINE_AA)
        dt_ms = (time.perf_counter() - t0) * 1000
        self.ms = 0.9 * self.ms + 0.1 * dt_ms if self.ms else dt_ms

    def state(self) -> dict:
        return {**super().state(), "ms": round(self.ms, 2), "objetos_vivos": [it["o"].id for it in self.items],
                "chispas_vivas": int(len(self.sparks))}
