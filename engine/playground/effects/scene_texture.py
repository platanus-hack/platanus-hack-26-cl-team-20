"""La foto completa de la mesa reproyectada sobre sí misma, realzada: el modo «skin a full detalle».

`scene_pov.png` es lo que cada píxel del proyector ilumina, visto con luz blanca. Proyectarla de vuelta es darle a
cada objeto su propia textura con más color: se realza (saturación, contraste local, nitidez), los bordes de la foto
laten encima y una «viñeta por objeto» deja los objetos de la escena a pleno y el fondo (paredes, cables) tenue.
Spawneado sobre un objeto, solo ilumina ese objeto. Todo lo pesado se calcula una vez; por cuadro es una suma.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .base import Effect, Canvas, bgr
from .. import scan_geom as sg
from ..appearance import install_scene_regions, mask_for_object, texture_path, stamp


class SceneTexture(Effect):
    name = "scene_texture"
    label = "Foto de la escena a full detalle"
    aliases = ("foto de la escena", "skin a full detalle", "full detalle", "textura completa", "la foto", "reproyecta la foto")
    description = ("La foto del escaneo (scene_pov) reproyectada sobre todo lo real, realzada: más saturación y contraste, "
                   "bordes que laten y viñeta por objeto (objetos a pleno, fondo tenue). Sobre un objeto, solo ese objeto. "
                   "Params: intensidad, saturacion, bordes, color_bordes, fondo (0..1), velocidad, objetos ('buda,ddj,...' "
                   "o '' = todos).")
    needs = ("textures", "contours")
    targets = ("*",)
    params = {"intensidad": 0.8, "saturacion": 1.5, "contraste": 1.0, "bordes": 0.6, "color_bordes": [255, 255, 255],
              "fondo": 0.25, "velocidad": 0.5, "objetos": ""}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self._key = None
        self.prep = None

    def _prepare(self, scene, obj):
        sd = sg.scene_scan(scene)
        if sd is None or sd.pov is None:
            return None
        pov = sd.pov
        if scene.scan_dir:
            prepared_path = texture_path(scene.scan_dir)
            if prepared_path.name != "scene_pov.png":
                pov = cv2.imread(str(prepared_path))
                if pov is None:
                    raise ValueError("Unreadable prepared appearance")
        h, w = pov.shape[:2]
        hsv = cv2.cvtColor(pov, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 1] = np.clip(hsv[..., 1] * float(self.p["saturacion"]), 0, 255)
        enh = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        if float(self.p["contraste"]) > 0:              # contraste local (CLAHE en la luminancia) + nitidez
            lab = cv2.cvtColor(enh, cv2.COLOR_BGR2LAB)
            clahe = cv2.createCLAHE(clipLimit=1.5 + float(self.p["contraste"]), tileGridSize=(16, 9))
            lab[..., 0] = clahe.apply(lab[..., 0])
            enh = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        enh = cv2.addWeighted(enh, 1.6, cv2.GaussianBlur(enh, (0, 0), 2.0), -0.6, 0)
        edges = cv2.Canny(cv2.GaussianBlur(pov, (0, 0), 1.2), 50, 130)
        edges = cv2.GaussianBlur(edges, (0, 0), 0.8)
        # viñeta por objeto: objetos de la escena a pleno, el resto a `fondo`
        sel = [s.strip() for s in str(self.p.get("objetos") or "").split(",") if s.strip()]
        objs = [obj] if obj is not None else [o for o in scene.objects.values() if o.kind != "table"
                                              and (not sel or o.id in sel or any(scene.find(s) is o for s in sel))]
        m = np.zeros((h, w), np.uint8)
        exact = False
        for o in objs:
            pixels = mask_for_object(scene, o)
            if pixels is not None:
                m[pixels] = 255
                exact = True
                continue
            poly = o.extra.get("outline") or o.contour
            if poly and len(poly) >= 3:
                cv2.fillPoly(m, [np.asarray(poly, float).round().astype(np.int32).reshape(-1, 1, 2)], 255)
        wgt = m.astype(np.float32) / 255.0
        if not exact:
            wgt = cv2.GaussianBlur(wgt, (0, 0), 12)
        fondo = 0.0 if obj is not None else float(self.p["fondo"])
        wgt = fondo + (1.0 - fondo) * wgt
        wgt[~np.any(pov, axis=2)] = 0
        if obj is not None:                              # solo ese objeto: recorte a su caja
            roi = cv2.boundingRect(m) if exact else sg.roi_of(obj.extra.get("outline") or obj.contour, 16, (w, h))
        else:
            roi = (0, 0, w, h)
        if roi is None:
            return None
        x0, y0, rw, rh = roi
        base = (enh[y0:y0 + rh, x0:x0 + rw].astype(np.float32) * wgt[y0:y0 + rh, x0:x0 + rw, None]).astype(np.uint8)
        e = (edges[y0:y0 + rh, x0:x0 + rw].astype(np.float32) * wgt[y0:y0 + rh, x0:x0 + rw]).astype(np.uint8)
        col = np.array(bgr(self.p["color_bordes"]), np.float32) / 255.0
        edge3 = (e[..., None].astype(np.float32) * col).astype(np.uint8)
        return {"roi": roi, "base": base, "edges": edge3}

    def update(self, dt, scene):
        super().update(dt, scene)
        install_scene_regions(scene)
        obj = self.object(scene)
        self.obj = obj
        key = (id(sg.scene_scan(scene)), obj.id if obj else None, float(self.p["saturacion"]), float(self.p["contraste"]),
               float(self.p["fondo"]), str(self.p.get("objetos")), str(self.p["color_bordes"]),
               tuple(sorted((o.id, len(o.contour), tuple(o.contour[0]) if o.contour else ()) for o in scene.objects.values())))
        if scene.scan_dir:
            path = texture_path(scene.scan_dir)
            self.texture_source = str(path)
            key += (str(path), stamp(path))
        if key != self._key:
            self.prep, self._key = self._prepare(scene, obj), key

    def draw(self, canvas: Canvas):
        pr = self.prep
        if pr is None:
            return
        x0, y0, w, h = pr["roi"]
        k = float(self.p["intensidad"])
        pulse = 0.5 + 0.5 * math.sin(self.t * 2 * math.pi * 0.25 * float(self.p["velocidad"]))
        ek = float(self.p["bordes"]) * (0.3 + 0.7 * pulse)
        ih, iw = canvas.img.shape[:2]
        if canvas.px_per_mm == 1.0 and not canvas.flip_y and x0 + w <= iw and y0 + h <= ih:
            dst = canvas.img[y0:y0 + h, x0:x0 + w]       # lienzo real: dos sumas pesadas, sin flotantes
            dst[:] = cv2.addWeighted(dst, 1.0, pr["base"], k, 0)
            if ek > 0:
                dst[:] = cv2.addWeighted(dst, 1.0, pr["edges"], ek * k, 0)
            return
        layer = pr["base"].astype(np.float32) * (k / 255.0) + pr["edges"].astype(np.float32) * (ek * k / 255.0)
        sg.blit(canvas, x0, y0, layer, None, mode="add")

    def state(self):
        return {**super().state(),"texture_source":getattr(self,"texture_source",None)}
