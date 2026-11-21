"""Canales → escena: cada parte (Buda, teclas, platos, cocodrilo, Circuit…) sigue a su canal de la mezcladora.

El mapeo vive en `playground/audio/mapping.json` (se recarga al guardar) o va como param `mapping` (ruta, dict o JSON).
Por defecto: canal 1 (L) → halo del Buda · canal 2 (R) → teclas · bajo → platos de la DDJ (anillos en cada golpe)
· agudos → cocodrilo · medios → Circuit Tracks. Formas en px del proyector (`playground/stage.py`); una parte que no
está en el escaneo se busca entre los objetos de la escena (mm → px).
"""

from __future__ import annotations

import numpy as np

from .base import Effect, Canvas, bgr
from .. import fxdraw
from ..audio.features import audio_level, audio_source
from ..audio.mapping import load_mapping
from ..stage import stage


class ChannelReact(Effect):
    name = "channel_react"
    label = "Canales → escena"
    description = ("Cada parte de la escena sigue a un canal de la mezcladora (audio.ch[i]) o a una banda de la mezcla, "
                   "según playground/audio/mapping.json: canal 1 → Buda, canal 2 → teclas, bajo → platos, agudos → "
                   "cocodrilo, medios → Circuit. only='buda,keys' limita las partes.")
    needs = ("audio", "geometry")
    targets = ("*",)
    params = {"mapping": "", "gain": 1.0, "idle": 0.08, "flash": 0.6, "only": "", "reach_px": 70,
              "speed_px_s": 520, "life_s": 1.1, "scan": ""}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self.parts: list[dict] = []
        self.error: str | None = None
        self.origin = ""
        self.levels: dict[int, float] = {}
        self.beats: dict[int, float] = {}
        self.on: dict[int, bool] = {}
        self.ripples: dict[int, list[tuple[float, float]]] = {}
        self.last_onsets: dict[int, int] = {}
        self.objs: dict[str, object] = {}

    def update(self, dt, scene):
        super().update(dt, scene)
        self.parts, self.error, self.origin = load_mapping(self.p["mapping"])
        only = {s.strip().lower() for s in str(self.p["only"]).split(",") if s.strip()}
        a = getattr(scene, "audio", None) or {}
        self.levels, self.beats = {}, {}
        self.on = {i: not only or part["part"] in only for i, part in enumerate(self.parts)}   # (el mapeo es compartido)
        life = float(self.p["life_s"])
        for i, part in enumerate(self.parts):
            src = audio_source(a, part["source"])
            self.levels[i] = audio_level(a, part["key"], float(self.p["gain"]) * part["gain"])
            self.beats[i] = float(src.get("beat", 0.0)) * float(self.p["flash"])
            n = int(src.get("onsets", 0) or 0)
            if part["ripples"] and i in self.last_onsets and n > self.last_onsets[i]:
                self.ripples.setdefault(i, []).append((self.t, max(0.35, self.levels[i], self.beats[i])))
            self.last_onsets[i] = n
            self.ripples[i] = [r for r in self.ripples.get(i, []) if self.t - r[0] < life][-24:]
            if part["part"] not in self.objs:                      # respaldo: objeto de la escena con ese nombre
                self.objs[part["part"]] = scene.find(part["part"])
            else:
                o = self.objs[part["part"]]
                self.objs[part["part"]] = scene.objects.get(o.id, o) if o is not None else None

    def _shape(self, part: str, st: dict, canvas: Canvas):
        v = st.get(part)
        if v:
            return v
        o = self.objs.get(part)
        if o is not None and len(o.contour) >= 3:
            return [list(canvas.project(x, y)) for x, y in o.contour]
        return None

    def draw(self, canvas: Canvas):
        st = stage(self.p["scan"] or None, canvas.size)
        L = fxdraw.Layers(canvas.img)
        idle, sx = float(self.p["idle"]), canvas.size[0] / 1920.0
        speed, life = float(self.p["speed_px_s"]) * sx, float(self.p["life_s"])
        for i, part in enumerate(self.parts):
            if not self.on.get(i, True):
                continue
            shape = self._shape(part["part"], st, canvas)
            if not shape:
                continue
            col = fxdraw.mix(bgr(part["color"]), (255, 255, 255), self.beats.get(i, 0.0) * 0.5)
            e, beat = max(idle, self.levels.get(i, 0.0)), self.beats.get(i, 0.0)
            rip = [(self.t - t0, s) for t0, s in self.ripples.get(i, [])]
            style = part["style"]
            if part["part"] == "jogs":                             # lista de círculos (cx, cy, r)
                fxdraw.rings(L, shape, col, e, rip if part["ripples"] else (), speed, life)
            elif part["part"] in ("keys", "black_keys"):           # lista de teclas
                if style == "keys":
                    fxdraw.keys(L, shape, col, self.levels.get(i, 0.0), beat, self.t, idle)
                else:
                    for k in shape:
                        fxdraw.outline(L, k, col, e, beat, 1)
            else:
                poly = np.asarray(shape, float)
                if style == "halo":
                    fxdraw.halo(L, poly, col, e, beat, float(self.p["reach_px"]) * sx)
                elif style == "fill":
                    fxdraw.fill(L, poly, col, e, beat)
                else:
                    fxdraw.outline(L, poly, col, e, beat)
                if part["ripples"]:
                    fxdraw.ripple_poly(L, poly, col, rip, speed, life)
        L.composite()

    def state(self) -> dict:
        d = super().state()
        d["mapping"] = {"origin": self.origin, "error": self.error,
                        "parts": [{"label": p["label"], "level": round(self.levels.get(i, 0.0), 2)}
                                  for i, p in enumerate(self.parts)]}
        return d
