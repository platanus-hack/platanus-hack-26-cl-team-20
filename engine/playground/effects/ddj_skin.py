"""Skin de la DDJ: la controladora vestida a full detalle sobre su superficie real.

Sale de la foto del escaneo (`scan_geom.ddj_layout`, px del proyector): los dos platos como elipses exactas (giran
con marcas, anillo y punto de cue), los pads (zonas claras bajo cada plato) que se encienden como secuenciador con
el beat, las perillas del mixer que laten con el audio y dos rieles de fader con su perilla. El cuerpo se pinta con
la propia foto en dos tonos (oscuro → color de base, claro → color de detalle) y sus bordes, así cada botón real
queda en su sitio. Si llega MIDI de la DDJ (`scene.controls`, dispositivo con 'ddj' en el id) los platos siguen al
jog, los pads a los pads y los faders a los faders.
"""

from __future__ import annotations

import math
import re

import cv2
import numpy as np

from .base import Effect, Canvas, bgr
from .. import scan_geom as sg
from ..audio.features import audio_level


def _c(rgb, k: float = 1.0) -> tuple[float, float, float]:
    """[r, g, b] 0-255 → color BGR 0..1 escalado por k."""
    b, g, r = bgr(rgb)
    return (b / 255.0 * k, g / 255.0 * k, r / 255.0 * k)


def _c8(rgb, k: float = 1.0) -> tuple[int, int, int]:
    """[r, g, b] 0-255 → color BGR 0-255 escalado por k (para dibujar en la capa uint8)."""
    b, g, r = bgr(rgb)
    k = max(0.0, min(1.0, k))
    return (int(b * k), int(g * k), int(r * k))


def _epoint(j, theta: float, r: float) -> tuple[float, float]:
    """Punto del plato j en el ángulo theta (rad, en el disco) a fracción r del radio, en px."""
    a, b = j["axes"][0] / 2 * r, j["axes"][1] / 2 * r
    ca, sa = math.cos(math.radians(j["angle"])), math.sin(math.radians(j["angle"]))
    ex, ey = a * math.cos(theta), b * math.sin(theta)
    return j["c"][0] + ex * ca - ey * sa, j["c"][1] + ex * sa + ey * ca


class DdjSkin(Effect):
    name = "ddj_skin"
    label = "Skin de la DDJ (platos, pads y faders)"
    aliases = ("skin de la ddj", "skin", "ddj", "platos", "viste la ddj", "skin del dj", "controladora dj")
    description = ("Skin a full detalle de la DDJ sobre la real: platos que giran (elipses exactas de la foto, con marcas y "
                   "cue), pads que se encienden como secuenciador con el beat, perillas que laten con el audio y faders en "
                   "su sitio; el cuerpo en dos tonos sacados de la propia foto. Sigue el MIDI de la DDJ si llega (jog, "
                   "pads, faders). Params: color_base, color_detalle, color_platos, color_pads, color_faders, giro, "
                   "intensidad, detalle, marcas.")
    needs = ("textures", "contours", "beat")
    targets = ("instrument",)
    default_target = "ddj"
    params = {"color_base": [90, 0, 200], "color_detalle": [0, 230, 255], "color_platos": [255, 0, 200],
              "color_pads": [0, 255, 140], "color_faders": [255, 200, 40], "giro": 0.5, "intensidad": 1.0,
              "detalle": 0.8, "bordes": 0.7, "marcas": 12, "pads": True, "faders": True, "audio_gain": 1.0}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self._key = None
        self.prep = None
        self.ctrl: dict = {}
        self.spin = [0.0, 0.0]

    # ------------------------------------------------------------------ preparación
    def _prepare(self, scene, obj):
        sd = sg.scene_scan(scene)
        dx, dy = sg.offset_of(obj)
        lay = sg.ddj_layout(sd) if (sd is not None and obj.id == "ddj") else None
        if lay is not None:
            ell = obj.extra.get("jog_ellipses")            # escena viva (ya con nudge) si los trae; si no, foto + ajuste
            if ell and len(ell) >= 2:
                jogs = [{"c": [float(e[0]), float(e[1])], "axes": [float(e[2]), float(e[3])], "angle": float(e[4])} for e in ell[:2]]
            else:
                jogs = [{"c": [j["c"][0] + dx, j["c"][1] + dy], "axes": j["axes"], "angle": j["angle"]} for j in lay["jogs"]]
            pads = [{**p, "poly": sg.shifted(p["poly"], dx, dy)} for p in lay["pads"]]
            knobs = [{**k, "poly": sg.shifted(k["poly"], dx, dy)} for k in lay["knobs"]]
            faders = [sg.shifted(f, dx, dy) for f in lay["faders"]]
            body = sg.shifted(lay["body"], dx, dy)
        else:                                         # sin foto: platos de extra['jogs'] (círculos) dentro del contorno
            js = obj.extra.get("jogs") or []
            jogs = [{"c": [float(j[0]), float(j[1])], "axes": [2.0 * j[2], 2.0 * j[2]], "angle": 0.0} for j in js[:2]]
            c = np.asarray(obj.contour, float)
            if not jogs:
                (x0_, y0_), (x1_, y1_) = c.min(axis=0), c.max(axis=0)
                r = 0.22 * min(x1_ - x0_, (y1_ - y0_) * 2)
                jogs = [{"c": [x0_ + (x1_ - x0_) * f, (y0_ + y1_) / 2], "axes": [2 * r, 2 * r], "angle": 0.0} for f in (0.27, 0.73)]
            pads, knobs, faders, body = [], [], [], c.tolist()
        bounds = (int(scene.table_mm[0]), int(scene.table_mm[1])) if scene.projector_px else (10 ** 6, 10 ** 6)
        roi = sg.roi_of(body, 6, bounds)
        if roi is None:
            return None
        x0, y0, w, h = roi
        mask = sg.poly_mask((h, w), np.asarray(body, float) - [x0, y0])
        keys = scene.objects.get("keys")
        if keys is not None and keys.contour and obj.id == "ddj":     # el borde del teclado no es DDJ
            mask &= ~sg.poly_mask((h, w), np.asarray(keys.contour, float) - [x0, y0], dilate=4)
        tex8 = sg.texture(sd, x0, y0, w, h, dx, dy)
        if tex8 is not None:
            lum = cv2.cvtColor(tex8, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
            edges = cv2.Canny(cv2.GaussianBlur(tex8, (0, 0), 1.0), 50, 120)
            edges = cv2.GaussianBlur(edges.astype(np.float32) / 255.0, (0, 0), 0.8)
        else:
            lum = np.full((h, w), 0.2, np.float32); edges = np.zeros((h, w), np.float32)
        detail = np.clip((lum - 0.28) / 0.45, 0, 1)
        a8 = (sg.feather(mask, 3.0) * 255).astype(np.uint8)
        return {"roi": roi, "alpha3": cv2.merge([a8, a8, a8]), "detail": (detail * mask).astype(np.float32), "edges": (edges * mask).astype(np.float32),
                "dark": ((1.0 - detail) * mask).astype(np.float32), "jogs": jogs, "pads": pads, "knobs": knobs, "faders": faders,
                "xs": np.arange(w, dtype=np.float32), "photo": tex8 is not None}

    # ------------------------------------------------------------------ MIDI de la DDJ (si llega)
    def _read_controls(self, scene) -> dict:
        out = {"jog": [None, None], "pads": set(), "fader": [None, None]}
        for dev, ctrls in (getattr(scene, "controls", None) or {}).items():
            if "ddj" not in str(dev).lower():
                continue
            for cid, c in ctrls.items():
                n = str(cid).lower()
                toks = re.split(r"[^a-z0-9]+", n)
                deck = 1 if any(k in toks for k in ("r", "right", "der", "derecho", "derecha", "2", "b", "deck2")) else 0
                if "jog" in n:
                    out["jog"][deck] = float(c.get("value", 0.0))
                elif "pad" in n and c.get("state") in ("on", "down", 1, True):
                    digits = "".join(ch for ch in n if ch.isdigit())
                    if digits:
                        out["pads"].add((deck, int(digits[-1]) % 4))
                elif "fader" in n and "cross" not in n:
                    out["fader"][deck] = float(c.get("value", 0.0))
        return out

    # ------------------------------------------------------------------ ciclo
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
        self.ctrl = self._read_controls(scene)
        g = float(self.p["giro"])
        for i in range(2):
            jog = self.ctrl["jog"][i]
            self.spin[i] = (jog * 2 * math.pi * 4) if jog is not None else self.spin[i] + dt * g * 2 * math.pi * (1 if i == 0 else 0.83)
        a = getattr(scene, "audio", None) or {}
        gain = float(self.p["audio_gain"])
        self.bass, self.mid = audio_level(a, "bass", gain), audio_level(a, "mid", gain)
        b = scene.beat
        self.step = (scene.t * b.get("bpm", 120.0) / 60.0) if b.get("playing") else self.t * 2.0

    def draw(self, canvas: Canvas):
        pr = self.prep
        if pr is None:
            return
        x0, y0, w, h = pr["roi"]
        k = float(self.p["intensidad"])
        t = self.t
        # cuerpo en uint8 con OpenCV: base (zonas oscuras) + detalle claro con una luz que barre + bordes de la foto
        sweep = (0.7 + 0.3 * np.sin(pr["xs"] * 0.025 - t * 1.6)).astype(np.float32)
        base = np.array(_c(self.p["color_base"]), np.float32) * 0.22 * 255
        det = np.array(_c(self.p["color_detalle"]), np.float32) * 255
        dk = float(self.p["detalle"]) * (0.7 + 0.6 * self.mid)
        ek = float(self.p["bordes"]) * (0.5 + 0.5 * self.bass)
        dsw = pr["detail"] * sweep[None, :]
        chans = [cv2.add(cv2.add(cv2.convertScaleAbs(pr["dark"], alpha=float(base[i])), cv2.convertScaleAbs(dsw, alpha=float(det[i] * dk))),
                         cv2.convertScaleAbs(pr["edges"], alpha=float(det[i] * ek))) for i in range(3)]
        layer = cv2.merge(chans)
        off = np.array([x0, y0], float)

        def P(pt):
            return (int(round(pt[0] - off[0])), int(round(pt[1] - off[1])))

        # platos: anillo exterior (late con el bajo), etiqueta, marcas que giran y punto de cue
        cp = _c8(self.p["color_platos"], 1.0)
        n_marks = max(3, int(self.p["marcas"]))
        for i, j in enumerate(pr["jogs"][:2]):
            ring = sg.ellipse_points(j["c"][0], j["c"][1], *j["axes"], j["angle"], 72) - off
            cv2.polylines(layer, [ring.round().astype(np.int32).reshape(-1, 1, 2)], True, _c8(self.p["color_platos"], 0.6 + 0.4 * self.bass), 3, cv2.LINE_AA)
            lab = sg.ellipse_points(j["c"][0], j["c"][1], *j["axes"], j["angle"], 48, 0.36) - off
            cv2.polylines(layer, [lab.round().astype(np.int32).reshape(-1, 1, 2)], True, _c8(self.p["color_detalle"], 0.7), 2, cv2.LINE_AA)
            sp = self.spin[i] if i < len(self.spin) else 0.0
            for m in range(n_marks):
                th = sp + m * 2 * math.pi / n_marks
                cv2.line(layer, P(_epoint(j, th, 0.62)), P(_epoint(j, th, 0.93)), cp if m else (255, 255, 255), 2 if m else 4, cv2.LINE_AA)
            cv2.circle(layer, P(_epoint(j, sp, 0.78)), 6, (255, 255, 255), -1, cv2.LINE_AA)
        # pads: secuenciador de 4 columnas con el beat (o los pads que llegan por MIDI)
        if self.p["pads"]:
            step = int(self.step) % 4
            for pd in pr["pads"]:
                on = (pd["deck"], pd["col"]) in self.ctrl["pads"] or (not self.ctrl["pads"] and pd["col"] == (step + 2 * pd["deck"]) % 4)
                pts = (np.asarray(pd["poly"], float) - off).round().astype(np.int32).reshape(-1, 1, 2)
                cv2.fillPoly(layer, [pts], _c8(self.p["color_pads"], 1.0 if on else 0.18))
            for kn in pr["knobs"]:
                pts = (np.asarray(kn["poly"], float) - off).round().astype(np.int32).reshape(-1, 1, 2)
                cv2.fillPoly(layer, [pts], _c8(self.p["color_detalle"], 0.35 + 0.65 * max(self.mid, 0.5 + 0.5 * math.sin(t * 3 + kn["c"][1] * 0.05))))
        # faders: riel tenue y perilla brillante (posición del MIDI o un vaivén lento)
        if self.p["faders"]:
            for i, (p0, p1) in enumerate(pr["faders"][:2]):
                cv2.line(layer, P(p0), P(p1), _c8(self.p["color_faders"], 0.35), 3, cv2.LINE_AA)
                v = self.ctrl["fader"][i] if self.ctrl["fader"][i] is not None else 0.5 + 0.4 * math.sin(t * 0.7 + i * 2.1)
                u = 1.0 - max(0.0, min(1.0, v))                       # arriba = volumen alto
                cx, cy = p0[0] + (p1[0] - p0[0]) * u, p0[1] + (p1[1] - p0[1]) * u
                cv2.rectangle(layer, (int(cx - off[0]) - 9, int(cy - off[1]) - 4), (int(cx - off[0]) + 9, int(cy - off[1]) + 4),
                              _c8(self.p["color_faders"]), -1, cv2.LINE_AA)
        if k != 1.0:
            layer = cv2.convertScaleAbs(layer, alpha=k)
        sg.blit(canvas, x0, y0, cv2.multiply(layer, pr["alpha3"], scale=1.0 / 255), None, mode="add")
