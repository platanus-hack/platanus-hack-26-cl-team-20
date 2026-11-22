"""Ondas al ritmo: en cada golpe del bajo salen anillos desde los platos de la DDJ y desde el Circuit Tracks.

Los anillos de los platos son círculos; los del Circuit siguen su forma (el contorno se expande). El borde de cada
plato brilla con `audio.bass` entre golpes. Disparo: `trigger` = auto (golpes del audio si hay audio vivo; si no,
el beat del playground / Ableton), onset o beat. Formas en px del proyector (`playground/stage.py`).
"""

from __future__ import annotations

import cv2
import numpy as np

from .base import Effect, Canvas, bgr, offset_contour
from ..audio.features import audio_level, audio_source, parse_key
from ..stage import stage


def _scaled(color_bgr, k: float) -> tuple[int, int, int]:
    k = max(0.0, min(1.0, k))
    return tuple(int(c * k) for c in color_bgr)


class BassRipple(Effect):
    name = "bass_ripple"
    label = "Ondas del bajo"
    description = ("En cada golpe del bajo salen ondas desde los platos de la DDJ y el Circuit Tracks; el borde de los "
                   "platos brilla con audio.bass. sources: 'jogs,circuit' (o solo uno). Sin audio, sigue el beat.")
    needs = ("audio", "beat", "geometry")
    targets = ("*",)
    params = {"color": [0, 160, 255], "circuit_color": [255, 60, 180], "rim_color": [80, 220, 255],   # RGB
              "band": "bass", "gain": 1.0, "speed_px_s": 520, "life_s": 1.1, "width_px": 5, "max_rings": 30,
              "sources": "jogs,circuit", "trigger": "auto", "idle": 0.1, "scan": ""}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self.rings: list[dict] = []
        self.last_onsets: int | None = None
        self.last_phase = 0.0
        self.lvl = 0.0

    def _fire(self, strength: float) -> None:
        self.rings.append({"t0": self.t, "s": max(0.35, min(1.0, strength))})
        self.rings = self.rings[-int(self.p["max_rings"]):]

    def update(self, dt, scene):
        super().update(dt, scene)
        a = getattr(scene, "audio", None) or {}
        self.lvl = audio_level(a, self.p["band"], self.p["gain"])
        trig = self.p["trigger"]
        src = audio_source(a, (parse_key(self.p["band"]) or ("mix", ""))[0])   # band 'ch1.bass': golpes del canal 1
        onsets = int(src.get("onsets", 0) or 0)
        use_audio = trig == "onset" or (trig == "auto" and a.get("live"))
        if use_audio:
            if self.last_onsets is not None and onsets > self.last_onsets:
                self._fire(max(self.lvl, float(src.get("beat", 0.0))))
        elif trig in ("auto", "beat") and scene.beat.get("playing"):
            ph = float(scene.beat.get("phase", 0.0))
            if ph < self.last_phase:                                  # vuelta de fase = negra
                self._fire(0.7)
            self.last_phase = ph
        self.last_onsets = onsets
        life = float(self.p["life_s"])
        self.rings = [r for r in self.rings if self.t - r["t0"] < life]

    def draw(self, canvas: Canvas):
        st = stage(self.p["scan"] or None, canvas.size)
        src = {s.strip() for s in str(self.p["sources"]).split(",")}
        sx = canvas.size[0] / 1920.0
        layer = np.zeros_like(canvas.img)
        col, ccol, rim = bgr(self.p["color"]), bgr(self.p["circuit_color"]), bgr(self.p["rim_color"])
        life, speed, wpx = float(self.p["life_s"]), float(self.p["speed_px_s"]) * sx, float(self.p["width_px"])
        jogs = (st.get("jogs") or []) if "jogs" in src else []
        circ = np.asarray(st["circuit"], float) if "circuit" in src and st.get("circuit") else None
        e = max(float(self.p["idle"]), self.lvl)

        for cx, cy, r in jogs:                                        # borde del plato con el bajo
            cv2.circle(layer, (int(cx), int(cy)), int(r), _scaled(rim, 0.3 + 0.7 * e), max(1, int(2 + 6 * e)),
                       cv2.LINE_AA)
        if circ is not None:
            cv2.polylines(layer, [circ.astype(np.int32).reshape(-1, 1, 2)], True, _scaled(ccol, 0.3 + 0.7 * e),
                          max(1, int(2 + 4 * e)), cv2.LINE_AA)
        for ring in self.rings:
            age = self.t - ring["t0"]
            fade = ring["s"] * (1 - age / life) ** 1.5
            grow = speed * age
            th = max(1, int(wpx * (0.6 + ring["s"])))
            for cx, cy, r in jogs:
                cv2.circle(layer, (int(cx), int(cy)), int(r + grow), _scaled(col, fade), th, cv2.LINE_AA)
            if circ is not None:
                pts = np.asarray(offset_contour(circ, grow), float).astype(np.int32).reshape(-1, 1, 2)
                cv2.polylines(layer, [pts], True, _scaled(ccol, fade), th, cv2.LINE_AA)
        cv2.add(canvas.img, layer, dst=canvas.img)
