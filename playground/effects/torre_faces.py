"""Torre por caras: el PC blanco con un patrón distinto en cada cara, siguiendo el relieve del escaneo.

Caras (`scan_geom.torre_faces`): si `playground/torre_caras.json` trae las caras reales del PC para el escaneo
(arriba, lado y frente, marcadas con la Osmo y llevadas al proyector), se usan esas; si no, el contorno de
`workbench.json` partido en dos por `split_y` (arriba / lado). Con la escena nueva vienen en `extra['faces']` ya con
los ajustes finos. Patrones: `gradiente` (color que gira y se aclara con la altura), `ondas` (isolíneas del relieve
de `height_proj.npy` que corren) y `scan` (barrido de líneas). Se calcula a media resolución y se escala.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .base import Effect, Canvas, bgr
from .. import scan_geom as sg
from ..audio.features import audio_level

PATRONES = ("gradiente", "ondas", "scan")
DEFAULT_PATRON = {"arriba": "gradiente", "lado": "ondas", "frente": "scan"}


def _c(rgb) -> np.ndarray:
    b, g, r = bgr(rgb)
    return np.array([b, g, r], np.float32) / 255.0


class TorreFaces(Effect):
    name = "torre_faces"
    label = "Torre en dos caras"
    aliases = ("torre", "la torre", "pc", "parlante", "caras de la torre", "torre dos caras", "torre por caras")
    description = ("La torre (PC blanco) por caras: arriba un degradé que gira, el lado grande con ondas que siguen el "
                   "relieve del escaneo y el frente con barrido de líneas. Caras reales medidas con la cámara "
                   "(torre_caras.json) o, si no hay, el corte split_y de workbench. Params: patron_arriba / patron_lado / "
                   "patron_frente (gradiente, ondas, scan), color_arriba / color_lado / color_frente, velocidad, bandas, "
                   "intensidad; audio='bass' acelera las ondas con el bajo.")
    needs = ("geometry", "contours", "beat")
    targets = ("prop", "instrument", "frame", "screen")
    default_target = "torre"
    params = {"patron_arriba": "gradiente", "patron_lado": "ondas", "patron_frente": "scan",
              "color_arriba": [255, 120, 40], "color_lado": [0, 200, 255], "color_frente": [190, 80, 255],
              "velocidad": 1.0, "bandas": 6.0, "intensidad": 0.9, "audio": "", "borde": True}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self._key = None
        self.prep = None
        self.phase = 0.0
        self.lvl = 0.0

    def _faces(self, scene, obj, sd, dx, dy) -> dict:
        """{nombre: polígono} en coordenadas de la escena (con ajuste)."""
        if obj.extra.get("faces"):                          # escena viva: caras ya con los ajustes
            return {k: v for k, v in obj.extra["faces"].items()}
        tf = sg.torre_faces(sd) if (sd is not None and obj.id == "torre") else None
        if tf is not None and tf.get("faces"):
            return {k: sg.shifted(v, dx, dy) for k, v in tf["faces"].items()}
        if obj.extra.get("split_y") is not None:             # contorno + corte de la escena
            poly, split = obj.contour, float(obj.extra["split_y"])
        elif tf is not None:
            poly, split = sg.shifted(tf["contour"], dx, dy), tf["split_y"] + dy
        else:
            poly = obj.contour
            ys = np.asarray(poly, float)[:, 1]
            split = float(ys.min() + 0.3 * np.ptp(ys))
        return {"_split": (poly, split)}

    def _prepare(self, scene, obj):
        sd = sg.scene_scan(scene)
        dx, dy = sg.offset_of(obj)
        faces = self._faces(scene, obj, sd, dx, dy)
        bounds = (int(scene.table_mm[0]), int(scene.table_mm[1])) if scene.projector_px else (10 ** 6, 10 ** 6)
        allpts = faces["_split"][0] if "_split" in faces else np.vstack([np.asarray(v, float) for v in faces.values()])
        roi = sg.roi_of(allpts, 2, bounds)
        if roi is None:
            return None
        x0, y0, w, h = roi
        masks = {}
        if "_split" in faces:
            poly, split = faces["_split"]
            mask = sg.poly_mask((h, w), np.asarray(poly, float) - [x0, y0])
            rows = (np.arange(h) + y0)[:, None]
            up = rows < split if scene.projector_px else rows >= split   # demo en mm: y hacia arriba
            masks = {"arriba": mask & up, "lado": mask & ~up}
        else:
            for k, v in faces.items():
                masks[k] = sg.poly_mask((h, w), np.asarray(v, float) - [x0, y0])
            taken = np.zeros((h, w), bool)
            for k in ("arriba", "frente", "lado"):          # sin traslapes: cada píxel en una sola cara
                if k in masks:
                    masks[k] &= ~taken; taken |= masks[k]
        s = 2                                           # media resolución
        hw, hh = max(1, w // s), max(1, h // s)
        small = lambda a: cv2.resize(a.astype(np.float32), (hw, hh), interpolation=cv2.INTER_AREA)
        prep_faces = {}
        for k, m in masks.items():
            if not m.any():
                continue
            rel = sg.relief(sd, int(x0 - dx), int(y0 - dy), w, h, m) if sd is not None else sg.relief(None, 0, 0, w, h)
            alpha, edge = small(sg.feather(m, 3)), small(cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0)
            ys_, xs_ = np.nonzero((alpha > 0) | (edge > 0))
            if len(xs_) == 0:
                continue
            sl = (slice(int(ys_.min()), int(ys_.max()) + 1), slice(int(xs_.min()), int(xs_.max()) + 1))   # solo su caja
            prep_faces[k] = {"sl": sl, "alpha": alpha[sl], "rel": small(rel)[sl], "edge": edge[sl]}
        yy = np.mgrid[0:hh, 0:hw][0].astype(np.float32) / max(1, hh)
        return {"roi": roi, "faces": prep_faces, "ys": yy, "size": (hh, hw)}

    def update(self, dt, scene):
        super().update(dt, scene)
        obj = self.object(scene) or scene.find(self.default_target)
        self.obj = obj
        if obj is None:
            self.prep = None
            return
        key = (id(sg.scene_scan(scene)), obj.id, tuple(sg.offset_of(obj)), len(obj.contour), tuple(obj.contour[0]))
        if key != self._key:
            self.prep, self._key = self._prepare(scene, obj), key
        mode = str(self.p.get("audio") or "").strip().lower()
        self.lvl = audio_level(getattr(scene, "audio", None), mode, 1.0) if mode and mode != "off" else 0.0
        self.phase += dt * float(self.p["velocidad"]) * (1.0 + 2.0 * self.lvl)

    def _pattern(self, kind: str, rel: np.ndarray, color: np.ndarray, ys: np.ndarray) -> np.ndarray:
        t = self.phase
        if kind == "gradiente":            # degradé que gira de color y se aclara con la altura
            hue = rel * 0.8 + t * 0.05
            tint = np.stack([0.5 + 0.5 * np.cos(2 * np.pi * (hue + o)) for o in (0.0, 0.33, 0.67)], axis=2)
            val = (0.35 + 0.65 * rel) * (0.75 + 0.25 * math.sin(t * 1.3))
            return (0.55 * tint + 0.45 * color) * val[..., None]
        if kind == "scan":                 # barrido de líneas (firstlook) con brillo por altura
            scan = 0.5 + 0.5 * np.sin(ys * 120 - t * 6)
            return color * (scan * (0.4 + 0.6 * rel))[..., None]
        bands = float(self.p["bandas"])    # ondas: isolíneas del relieve que corren
        wv = (0.5 + 0.5 * np.sin(2 * np.pi * (rel * bands - t * 0.45))) ** 3
        return color * (0.12 + 0.88 * wv)[..., None] + np.float32(0.25) * (wv ** 4)[..., None]

    def draw(self, canvas: Canvas):
        pr = self.prep
        if pr is None or not pr["faces"]:
            return
        x0, y0, w, h = pr["roi"]
        layer = np.zeros((*pr["size"], 3), np.float32)
        for k, f in pr["faces"].items():
            pat = str(self.p.get(f"patron_{k}", DEFAULT_PATRON.get(k, "ondas")))
            pat = pat if pat in PATRONES else DEFAULT_PATRON.get(k, "ondas")
            col = _c(self.p.get(f"color_{k}", [255, 255, 255]))
            part = self._pattern(pat, f["rel"], col, pr["ys"][f["sl"]]) * f["alpha"][..., None]
            if self.p["borde"]:            # arista de cada cara: blanca, late lento
                part += (f["edge"] * (0.35 + 0.35 * math.sin(self.phase * 2)))[..., None]
            layer[f["sl"]] += part
        small8 = cv2.convertScaleAbs(layer, alpha=255.0 * float(self.p["intensidad"]))   # a uint8 antes de escalar
        sg.blit(canvas, x0, y0, cv2.resize(small8, (w, h), interpolation=cv2.INTER_LINEAR), None, mode="add")
