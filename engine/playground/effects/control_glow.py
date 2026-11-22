"""Controles activos: ilumina sobre el controlador (OHM) los pads apretados y las perillas o faders que se mueven.
Con `audio` (bass, sub, low, kick, mid, high, rms, beat) el brillo y el tamaño de los anillos siguen ese rasgo.

Defaults en lenguaje WUI (Arco · señal activa). Para recuperar el color saturado original pasar
`color` explícito desde el dashboard."""

from __future__ import annotations

import time

import cv2

from .base import Effect, Canvas, bgr
from .wui_preset import aplicar_wui
from .wui_tokens import SEÑAL_POR_ESTADO
from ..audio.features import audio_level, audio_mode
from ..audio.hooks import fire as fire_cue


class ControlGlow(Effect):
    name = "control_glow"
    label = "Controles activos"
    description = ("Sobre el OHM: pad apretado = cuadrado lleno; perilla/fader movido hace < fade_s = anillo que se apaga; el "
                   "valor del fader se dibuja como barra. audio='bass' (u otro rasgo) modula brillo y tamaño de los anillos. "
                   "Defaults WUI (Arco · señal activa); pásale `color` explícito para actividades coloridas.")
    needs = ("geometry", "controls")
    targets = ("instrument",)
    params = aplicar_wui(
        {"color": list(SEÑAL_POR_ESTADO["activa"]), "fade_s": 1.72, "show_values": True,
         "audio": "", "audio_gain": 1.0},
        forma="arco",
    )

    def update(self, dt, scene):
        super().update(dt, scene)
        self.obj = self.object(scene)
        self.controls = (self.obj.extra.get("controls") if self.obj else None) or {}
        self.live = scene.controls.get(self.obj.extra.get("device", ""), {}) if self.obj else {}
        mode = audio_mode(self.p.get("audio"))
        self.lvl = None if mode is None else audio_level(getattr(scene, "audio", None), mode, self.p.get("audio_gain", 1.0))

    def draw(self, canvas: Canvas):
        if not self.controls:
            return
        color = bgr(self.p["color"])
        lvl = getattr(self, "lvl", None)
        grow = 1.0
        if lvl is not None:                       # audio: brillo 35–100 % y anillos hasta 1,6x con el rasgo
            color = tuple(int(c * (0.35 + 0.65 * lvl)) for c in color)
            grow = 1.0 + 0.6 * lvl
        now = time.time()
        last_down = getattr(self, "_last_down", None)
        if last_down is None:
            last_down = {}
            self._last_down = last_down
        for cid, geo in self.controls.items():
            st = self.live.get(cid)
            if not st:
                continue
            x, y = canvas.project(*geo["pos_mm"])
            w = max(4, int(geo["size_mm"][0] * canvas.px_per_mm)); h = max(4, int(geo["size_mm"][1] * canvas.px_per_mm))
            age = now - st.get("t", 0)
            if geo["type"] in ("pad", "button"):
                down = st.get("state") == "down"
                if down and not last_down.get(cid, False):
                    # Hook sonoro: Confirmación · pad/button entra a estado down.
                    fire_cue("confirmacion", f"control_glow:{self.instance_id}:{cid}")
                last_down[cid] = down
                if down:
                    cv2.rectangle(canvas.img, (x - w // 2, y - h // 2), (x + w // 2, y + h // 2), color, -1)
            elif age < self.p["fade_s"]:
                a = 1 - age / self.p["fade_s"]
                cv2.circle(canvas.img, (x, y), int(max(w, h) * (0.6 + 0.6 * a) * grow), tuple(int(c * a) for c in color), 2, cv2.LINE_AA)
            if self.p["show_values"] and geo["type"] in ("fader", "knob", "crossfader"):
                v = float(st.get("value", 0))
                cv2.line(canvas.img, (x - w // 2, y + h // 2), (x - w // 2, y + h // 2 - int(h * v)), color, 3)
