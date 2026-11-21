"""Pared viva: las superficies grandes (mesa, pared, planos) respiran con ondas de luz que siguen el relieve.

Zona por defecto: toda la escena menos los objetos (con un margen suave), o solo los planos (`zona=planos`), o una
lista de objetos. Tres capas a baja resolución, una sola subida al lienzo por cuadro:
- ondas que nacen de los objetos (distancia a sus contornos) y se doblan con el relieve (height_proj o sintético);
- lluvia de luz: gotas que caen por columnas sueltas, con estela;
- respiración: la sala entera sube y baja (seno lento en calma; golpe del audio/beat en fiesta).
`modo=fiesta`: más rápido, paleta que corre por la sala y anillos en cada golpe.

Rendimiento: la fase de la onda se guarda en uint8 (256 pasos por ciclo) y por cuadro es una suma que da la vuelta
sola + una LUT; nada de senos ni exponenciales en flotante a resolución completa.
"""

from __future__ import annotations

import math
import time

import cv2
import numpy as np

from .base import Effect, Canvas
from .. import ambiente as amb

PLANE_KINDS = ("table", "wall", "pared", "plane", "plano", "floor", "piso", "suelo", "screen", "techo")


class ParedViva(Effect):
    name = "pared_viva"
    label = "Pared viva"
    aliases = ("pared viva", "la pared respira", "sala viva", "respira la sala", "ondas en la pared", "lluvia de luz",
               "ambiente", "fondo vivo", "que respire la pared")
    description = ("Ondas de luz que nacen de los objetos y siguen el relieve, lluvia de luz y respiración de la sala "
                   "sobre las superficies grandes (toda la escena menos los objetos). modo calma|fiesta; paleta "
                   "auto|oceano|aurora|fuego|neon|bosque|atardecer|oro o lista RGB; zona fondo|planos|ids; ondas, "
                   "lluvia, respiracion 0..1; velocidad; longitud (px de onda); relieve (cuánto lo dobla el relieve).")
    needs = ("geometry", "planes", "contours", "audio", "beat")
    targets = ("*",)
    params = {"modo": "calma", "paleta": "auto", "zona": "fondo", "intensidad": 0.65, "ondas": 0.8, "lluvia": 0.5,
              "respiracion": 0.6, "velocidad": 1.0, "longitud": 150.0, "relieve": 1.0, "margen": 14.0,
              "banda": "bass", "escala": 4, "capa": -10}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self.prep = None
        self._key = None
        self._lut_key = None
        self.pulse = amb.Pulse()
        self.rings: list[tuple[float, float]] = []
        self.ms = 0.0

    # ------------------------------------------------------------------ preparación (una vez por escena/params)
    def _prepare(self, scene):
        W, H = amb.scene_wh(scene)
        s = float(max(1, int(self.p["escala"])))
        objs = amb.select(scene)
        z, _ = amb.height01(scene, s)
        h, w = z.shape
        zona = str(self.p["zona"]).strip().lower()
        polys = [p for _, p in objs]
        if self.target is not None:
            area = amb.mask_small((W, H), [amb.poly_of(self.target)], s).astype(np.float32)
        elif zona == "planos":
            planes = [amb.poly_of(o) for o in scene.objects.values()
                      if (o.plane is not None or str(o.kind).lower() in PLANE_KINDS) and amb.poly_of(o) is not None]
            area = amb.mask_small((W, H), planes, s).astype(np.float32) if planes else np.ones((h, w), np.float32)
            area *= 1 - amb.mask_small((W, H), polys, s, float(self.p["margen"]))
        elif zona in ("", "fondo", "todo", "escena"):
            area = 1.0 - amb.mask_small((W, H), polys, s, float(self.p["margen"])).astype(np.float32)
        else:
            area = amb.mask_small((W, H), [p for _, p in amb.select(scene, zona)], s).astype(np.float32)
        area = cv2.GaussianBlur(area, (0, 0), 1.5)
        occ = amb.mask_small((W, H), polys, s)
        if occ.any():
            dist = cv2.distanceTransform((1 - occ).astype(np.uint8), cv2.DIST_L2, 3) * s
        else:                                      # sin objetos: ondas desde el centro de la escena
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            dist = np.hypot(xx - w / 2, yy - h / 2) * s
        lam = max(20.0, float(self.p["longitud"]))
        phase = dist / lam + z * float(self.p["relieve"])               # ciclos
        rng = np.random.default_rng(7)
        cols = np.nonzero(rng.random(w) < 0.22)[0]
        return {"W": W, "H": H, "s": s, "area8": np.clip(area * 255, 0, 255).astype(np.uint8),
                "ph8": (np.mod(phase, 1.0) * 256).astype(np.int32).astype(np.uint8),
                "ph8f": (np.mod(phase * (1 / 0.7), 1.0) * 256).astype(np.int32).astype(np.uint8),
                "hue8": (np.mod(z * 0.4 + dist / 1200.0, 1.0) * 256).astype(np.int32).astype(np.uint8),
                "relief8": np.clip(z * 0.18 * 255, 0, 255).astype(np.uint8),
                "dist4": np.clip(dist / 4.0, 0, 30000).astype(np.int16),
                "cols": cols, "coff": rng.random(len(cols)).astype(np.float32) * H,
                "cspd": (0.6 + 0.8 * rng.random(len(cols))).astype(np.float32),
                "Y": (np.arange(h, dtype=np.float32) * s)[:, None]}

    def update(self, dt, scene):
        super().update(dt, scene)
        objs = amb.select(scene)
        key = (amb.shape_key(objs), str(self.p["zona"]), int(self.p["escala"]), float(self.p["margen"]),
               float(self.p["longitud"]), float(self.p["relieve"]), getattr(scene, "scan_dir", None),
               amb.scene_wh(scene), self.target.id if self.target else None)
        if key != self._key:
            self.prep, self._key = self._prepare(scene), key
        self.pulse.update(dt, scene, str(self.p["banda"]))
        if self.pulse.hit():
            self.rings = (self.rings + [(self.t, max(0.5, self.pulse.level))])[-6:]
        self.rings = [r for r in self.rings if self.t - r[0] < 3.0]

    def _luts(self, fiesta: bool):
        key = (fiesta, float(self.p["ondas"]), str(self.p["paleta"]))
        if key != self._lut_key:
            c = 0.5 + 0.5 * np.cos(np.arange(256) * (2 * math.pi / 256))
            self.wave_lut = np.clip(c ** 4 * float(self.p["ondas"]) * 0.7 * 255, 0, 255).astype(np.uint8)
            d = np.arange(256, dtype=np.float32)                         # anillo: gaussiana de |dist − r| (pasos de 4)
            self.ring_lut = np.clip(np.exp(-(d * 4 / 40.0) ** 2) * 255, 0, 255).astype(np.float32)
            pal = self.p["paleta"]
            self.pal = amb.palette_lut(("neon" if fiesta else "oceano") if pal in ("auto", "", None) else pal)
            self._lut_key = key

    # ------------------------------------------------------------------ dibujo
    def draw(self, canvas: Canvas):
        t0 = time.perf_counter()
        pr = self.prep
        if pr is None:
            return
        fiesta = str(self.p["modo"]).lower().startswith("fi")
        self._luts(fiesta)
        v = float(self.p["velocidad"]) * (2.2 if fiesta else 1.0)
        t = self.t
        off = np.uint8(int(-t * v * 0.33 * 256) % 256)                  # ondas hacia afuera de los objetos
        ph = (pr["ph8f"] if fiesta else pr["ph8"]) + off               # uint8: da la vuelta solo
        val = cv2.LUT(ph, self.wave_lut)
        cv2.add(val, pr["relief8"], dst=val)
        for t_r, st in self.rings:                                      # golpes: anillos que corren por la sala
            age = t - t_r
            r4 = int(age * 520.0 * v / 4.0)
            k = np.clip(np.abs(pr["dist4"] - np.int16(r4)), 0, 255).astype(np.uint8)
            ring = cv2.LUT(k, self.ring_lut.astype(np.uint8))
            cv2.addWeighted(val, 1.0, ring, float(st * (1 - age / 3.0) ** 2 * 0.9), 0, dst=val)
        rain = float(self.p["lluvia"])
        if rain > 0 and len(pr["cols"]):
            trail = 140.0 if fiesta else 90.0
            m = np.mod(pr["Y"] - (t * 260.0 * v * pr["cspd"] + pr["coff"]), pr["H"] + trail)
            drops = np.clip(1.0 - m * (1 / trail), 0, 1)
            sub = val[:, pr["cols"]]
            val[:, pr["cols"]] = cv2.add(sub, np.clip(drops * drops * (rain * 255), 0, 255).astype(np.uint8))
        if fiesta:
            breath = 0.45 + 0.55 * max(self.pulse.env, self.pulse.level)
        else:
            breath = 0.35 + 0.65 * max(0.5 + 0.5 * math.sin(2 * math.pi * 0.11 * t), self.pulse.level)
        resp = float(self.p["respiracion"])
        gain = float(self.p["intensidad"]) * (1 - resp + resp * breath)
        lum = cv2.multiply(val, pr["area8"], scale=gain / 255.0)
        if fiesta:                                                      # la paleta corre por la sala
            hue = pr["hue8"] + np.uint8(int(t * 0.12 * 256) % 256)
            col = cv2.LUT(cv2.merge([hue, hue, hue]), self.pal.reshape(256, 1, 3))
            layer = cv2.multiply(col, cv2.merge([lum, lum, lum]), scale=1 / 255.0)
        else:
            layer = cv2.LUT(cv2.merge([lum, lum, lum]), self.pal.reshape(256, 1, 3))
        amb.to_canvas(canvas, layer, 0, 0, layer.shape[1] * pr["s"], layer.shape[0] * pr["s"])
        dt_ms = (time.perf_counter() - t0) * 1000
        self.ms = 0.9 * self.ms + 0.1 * dt_ms if self.ms else dt_ms

    def state(self) -> dict:
        return {**super().state(), "ms": round(self.ms, 2)}
