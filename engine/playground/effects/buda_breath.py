"""Respira el Buda: la foto del tapiz (recorte de scene_pov.png) proyectada sobre sí misma, respirando en su lugar.

Visual v3 de `firstlook.py` portado al runtime: la textura del Buda vuelve a caer exacta sobre la pintura (1 px de la
foto = 1 px del proyector), escala ±1,5 % alrededor de su centro sin moverse, bordes Canny de su propia pintura que
suben como luz y laten, y un halo cálido en la cabeza. El contorno es el del tapiz visto en la foto
(`scan_geom.buda_outline`), sin el cocodrilo encima. Sirve para cualquier objeto con foto (el cocodrilo, la hoja);
sin escaneo dibuja el contorno con un relleno que respira.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .base import Effect, Canvas, bgr
from .. import scan_geom as sg
from ..audio.features import audio_level, audio_mode


def _rgb01(c) -> np.ndarray:
    """[r, g, b] 0-255 (params) → BGR float32 0..1 para mezclar en numpy."""
    b, g, r = bgr(c)
    return np.array([b, g, r], np.float32) / 255.0


class BudaBreath(Effect):
    name = "buda_breath"
    label = "Respira el Buda (foto que respira)"
    aliases = ("respira el buda", "respira", "respiración", "buda respira", "que respire el cuadro", "foto del buda")
    description = ("Respira el Buda: su propia foto proyectada encima, respirando lento en su lugar (escala ±1,5 %), "
                   "bordes de la pintura (Canny) que suben como luz y laten, y halo cálido en la cabeza. Params: "
                   "respiracion (0.015), velocidad, intensidad, textura, bordes, color_bordes, halo, color_halo; "
                   "audio='bass' hace latir los bordes con el bajo. Sin marco: solo la pintura, sin el cocodrilo. capa -10 "
                   "(debajo de lo demás, p. ej. del cocodrilo). Sin objeto va sobre el Buda.")
    needs = ("textures", "contours", "beat")
    targets = ("frame", "prop", "instrument", "screen")
    default_target = "buda"
    params = {"capa": -10, "respiracion": 0.015, "velocidad": 1.0, "intensidad": 1.0, "textura": 0.85, "bordes": 1.0,
              "color_bordes": [80, 200, 255], "halo": True, "color_halo": [255, 170, 60], "audio": "", "audio_gain": 1.0,
              "borde_suave": 6}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self._key = None
        self.prep = None
        self.lvl = None

    # ------------------------------------------------------------------ preparación (una vez por objeto/ajuste)
    def _prepare(self, scene, obj):
        sd = sg.scene_scan(scene)
        dx, dy = sg.offset_of(obj)
        # geometría: la de la escena viva (scene_from_scan ya trae el contorno fino del tapiz, con los ajustes finos);
        # si la escena es de antes (solo el quad de segments.json), el contorno fino de la foto + el ajuste del objeto
        poly = obj.extra.get("outline")
        if poly is None and sd is not None and obj.id == "buda":
            ol = sg.buda_outline(sd)
            poly = sg.shifted(ol["outline"], dx, dy) if ol else None
        if poly is None:
            poly = obj.contour
        head = None
        if sd is not None and obj.id == "buda":
            head = sg.buda_head(sd, sg.shifted(poly, -dx, -dy))   # en coordenadas del escaneo (sin ajuste)
        margin = math.ceil(0.03 * max(np.ptp(np.asarray(poly, float), axis=0))) + 4
        roi = sg.roi_of(poly, margin, (int(scene.table_mm[0]), int(scene.table_mm[1])) if scene.projector_px else (10 ** 6, 10 ** 6))
        if roi is None:
            return None
        x0, y0, w, h = roi
        local = np.asarray(poly, float) - [x0, y0]
        mask = sg.poly_mask((h, w), local)
        if sd is not None and obj.id == "buda":       # el cocodrilo encima del tapiz no es Buda
            croc = scene.objects.get("croc")
            if croc is not None and croc.contour:
                mask &= ~sg.poly_mask((h, w), np.asarray(croc.contour, float) - [x0, y0], dilate=5)
        tex8 = sg.texture(sd, x0, y0, w, h, dx, dy)
        if tex8 is None:                               # sin foto: el albedo del objeto con un degradé suave
            alb = np.array(obj.albedo[::-1], np.float32)
            yy = np.linspace(0.6, 1.0, h, dtype=np.float32)[:, None, None]
            tex = np.broadcast_to(alb * yy, (h, w, 3)).astype(np.float32).copy()
            tex8 = (tex * 255).astype(np.uint8)
        tex = tex8.astype(np.float32) / 255.0
        edges = cv2.Canny(cv2.GaussianBlur(tex8, (0, 0), 1.2), 60, 140)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
        edges = cv2.GaussianBlur(edges.astype(np.float32) / 255.0, (0, 0), 1.5) * mask
        if head is not None:
            hx, hy = head[0] + dx - x0, head[1] + dy - y0
        else:
            hx, hy = local[:, 0].mean(), local[:, 1].min() + 0.3 * np.ptp(local[:, 1])
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        bw, bh = max(1.0, np.ptp(local[:, 0])), max(1.0, np.ptp(local[:, 1]))
        rr = np.sqrt(((xx - hx) / bw) ** 2 + ((yy - hy) / bh) ** 2)
        halo = np.clip(0.55 - rr * 1.5, 0, 1) ** 1.6
        ys = local[:, 1]
        yn = np.clip((np.arange(h, dtype=np.float32) - ys.min()) / max(1.0, np.ptp(ys)), 0, 1)
        alpha = sg.feather(mask, float(self.p["borde_suave"]))
        a8 = (alpha * 255).astype(np.uint8)
        return {"roi": roi, "mask": mask, "tex8": tex8, "edges8": (np.clip(edges, 0, 1) * 255).astype(np.uint8),
                "halo8": (np.clip(halo, 0, 1) * 255).astype(np.uint8), "alpha3": cv2.merge([a8, a8, a8]),
                "center": (float(local[:, 0].mean()), float(local[:, 1].mean())), "yn": yn,
                "alpha": alpha, "poly": poly, "from_photo": sd is not None}

    # ------------------------------------------------------------------ ciclo
    def update(self, dt, scene):
        super().update(dt, scene)
        obj = self.object(scene) or scene.find(self.default_target)
        self.obj = obj
        if obj is None:
            self.prep = None
            return
        key = (id(sg.scene_scan(scene)), obj.id, tuple(sg.offset_of(obj)), len(obj.contour), tuple(obj.contour[0]),
               float(self.p["borde_suave"]))
        if key != self._key:
            self.prep, self._key = self._prepare(scene, obj), key
        mode = audio_mode(self.p.get("audio"))
        self.lvl = None if mode is None else audio_level(getattr(scene, "audio", None), mode, self.p.get("audio_gain", 1.0))
        self.beat = scene.beat.get("phase", 0.0) if scene.beat.get("playing") else None

    def draw(self, canvas: Canvas):
        """Todo en uint8 con OpenCV (la foto entera del tapiz a 1:1 cuesta pocos ms): textura respirando + bordes
        coloreados que suben + halo, por la máscara suave, sumado a lo que haya debajo."""
        pr = self.prep
        if pr is None:
            return
        x0, y0, w, h = pr["roi"]
        t = self.t * float(self.p["velocidad"])
        ph = math.sin(2 * math.pi * 0.17 * t)                     # ~6 s por respiración
        breath = 1.0 + float(self.p["respiracion"]) * ph
        M = cv2.getRotationMatrix2D(pr["center"], 0, breath)
        tex = cv2.warpAffine(pr["tex8"], M, (w, h), borderMode=cv2.BORDER_REFLECT)
        edg = cv2.warpAffine(pr["edges8"], M, (w, h), borderMode=cv2.BORDER_CONSTANT)
        lvl = self.lvl
        beat = 0.5 + 0.5 * math.cos(2 * math.pi * self.beat) if self.beat is not None else 0.5 + 0.5 * math.sin(t * 2.0)
        wave = (0.35 + 0.65 * (0.5 + 0.5 * np.sin(pr["yn"] * 14.0 + t * 2.2))).astype(np.float32)   # luz que sube
        e_k = float(self.p["bordes"]) * (0.45 + 0.55 * beat if lvl is None else 0.25 + 1.4 * lvl)
        k = float(self.p["intensidad"])
        out = cv2.convertScaleAbs(tex, alpha=float(self.p["textura"]) * (0.82 + 0.18 * ph) * k)
        ew = edg.astype(np.float32) * wave[:, None]
        cb = _rgb01(self.p["color_bordes"]) * (e_k * k)
        out = cv2.add(out, cv2.merge([cv2.convertScaleAbs(ew, alpha=float(c)) for c in cb]))
        if self.p["halo"]:
            hk = 0.45 * (0.6 + 0.4 * ph) * (1.0 if lvl is None else 0.6 + 0.8 * lvl) * k
            ch = _rgb01(self.p["color_halo"]) * hk
            out = cv2.add(out, cv2.merge([cv2.convertScaleAbs(pr["halo8"], alpha=float(c)) for c in ch]))
        out = cv2.multiply(out, pr["alpha3"], scale=1.0 / 255)
        sg.blit(canvas, x0, y0, out, None, mode="add")
