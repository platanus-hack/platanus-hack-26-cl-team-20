"""Dibujar sobre la mesa: el dedo deja un trazo de luz proyectado donde pasa.

- Persistencia: cada punto vive `hold_s` a pleno y se apaga en `fade_s` (0 = no se apaga nunca).
- Color: "velocidad" (lento = cian, rápido = magenta; el grosor también baja con la velocidad), "zona" (color por
  franja de la mesa, de izquierda a derecha) o "fijo" (`color`, RGB).
- Borrar: comando (`touchsynth clear`, `pg_touch clear`, o `set_param <id> clear true`) o gesto de dos manos:
  dos toques a la vez (de manos distintas si se sabe) quietos `clear_hold_s`, o que se separen rápido
  (`clear_spread_px` en menos de 0,6 s) como quien barre la mesa.
- Es el instrumento de menor prioridad: se queda con los toques que no caen en el cocodrilo ni en la marimba.
- `sound`: opcional, cada trazo nuevo toca una nota (canal 3) según la altura en la mesa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .instrument import Instrument, hsv_bgr, scale_bgr
from .music import scale_notes, velocity_of

W, H = 1920, 1080


@dataclass
class Stroke:
    id: str
    hand: str
    pts: list[tuple[float, float, float, float]] = field(default_factory=list)   # (x, y, t, velocidad px/s)
    t_end: float | None = None
    color: tuple[int, int, int] | None = None
    note: int | None = None


class Sketch(Instrument):
    kind = "dibujo"
    priority = 10
    DEFAULTS = {"fade_s": 8.0, "hold_s": 2.0, "color_mode": "velocidad", "color": [255, 255, 255], "width": 6,
                "clear": False, "gesture_clear": True, "clear_hold_s": 1.2, "clear_spread_px": 420, "sound": False,
                "channel": 3, "scale": "pentatonica_menor", "root": 60, "min_step_px": 2.0}

    def __init__(self, params: dict | None = None, size=(W, H)):
        super().__init__()
        self.size = size
        self.live: dict[str, Stroke] = {}
        self.done: list[Stroke] = []
        self.clears = 0
        self.last_clear: tuple[float, str] | None = None
        self._pair: dict | None = None
        self._cleared_key: tuple | None = None
        self.configure(params or {})

    def configure(self, params: dict) -> None:
        p = {**self.DEFAULTS, **{k: v for k, v in params.items() if k in self.DEFAULTS}}
        if p.get("clear"):
            self.clear(reason="parámetro")
            p["clear"] = False
        self.params = p

    def hit(self, x: float, y: float) -> bool:
        return True

    # ------------------------------------------------------------------ toques
    def _color_for(self, x: float, y: float) -> tuple[int, int, int] | None:
        mode = self.params["color_mode"]
        if mode == "fijo":
            r, g, b = (int(v) for v in self.params["color"][:3])
            return (b, g, r)
        if mode == "zona":
            band = int(np.clip(x / self.size[0] * 8, 0, 7))
            return hsv_bgr(band / 8.0, 0.85, 1.0)
        return None                                                   # por velocidad: color por segmento

    def handle(self, ev: dict, now: float) -> None:
        tid, state = str(ev.get("id")), ev.get("state")
        x, y = float(ev["x"]), float(ev["y"])
        t = float(ev.get("t") or now)
        if state == "down":
            st = Stroke(tid, str(ev.get("hand", "")), [(x, y, t, 0.0)], color=self._color_for(x, y))
            self.live[tid] = st
            if self.params["sound"]:
                notes = scale_notes(int(self.params["root"]), self.params["scale"], 8)
                st.note = notes[int(np.clip((1 - y / self.size[1]) * 8, 0, 7))]
                self.notes.on(st.note, velocity_of(ev.get("velocity"), 90), int(self.params["channel"]), now)
        elif state == "move" and tid in self.live:
            st = self.live[tid]
            px, py, pt, _ = st.pts[-1]
            d = math.hypot(x - px, y - py)
            if d >= float(self.params["min_step_px"]):
                v = d / max(t - pt, 1e-3)
                st.pts.append((x, y, t, v))
        elif state == "up" and tid in self.live:
            st = self.live.pop(tid)
            st.t_end = now
            if st.note is not None:
                self.notes.off(st.note, int(self.params["channel"]), now)
            self.done.append(st)
        if self.params["gesture_clear"]:
            self._gesture(now)

    def _gesture(self, now: float) -> None:
        """Dos manos: quietas `clear_hold_s` o separándose rápido → borra."""
        live = list(self.live.values())
        if len(live) < 2:
            self._pair = None
            return
        a, b = live[0], live[1]
        if a.hand and b.hand and a.hand == b.hand:
            self._pair = None
            return
        key = (a.id, b.id)
        if key == self._cleared_key:                                  # ya borró con este par: hay que soltar
            return
        pa, pb = a.pts[-1], b.pts[-1]
        dist = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
        if self._pair is None or self._pair["key"] != key:
            self._pair = {"key": key, "t0": now, "a0": pa[:2], "b0": pb[:2], "d0": dist, "hist": [(now, dist)]}
            return
        pr = self._pair
        pr["hist"] = [(t, d) for t, d in pr["hist"] if now - t <= 0.6] + [(now, dist)]
        still = max(math.hypot(pa[0] - pr["a0"][0], pa[1] - pr["a0"][1]),
                    math.hypot(pb[0] - pr["b0"][0], pb[1] - pr["b0"][1])) < 30
        if still and now - pr["t0"] >= float(self.params["clear_hold_s"]):
            self.clear(now, "dos manos quietas")
            self._cleared_key = key
        elif dist - min(d for _, d in pr["hist"]) >= float(self.params["clear_spread_px"]):
            self.clear(now, "dos manos se separan")
            self._cleared_key = key
        elif not still:
            pr["a0"], pr["b0"], pr["t0"] = pa[:2], pb[:2], now

    def clear(self, now: float | None = None, reason: str = "comando") -> None:
        self.done.clear()
        for st in self.live.values():                                 # los trazos en curso siguen desde aquí
            st.pts = st.pts[-1:]
        self.clears += 1
        self.last_clear = (now or 0.0, reason)
        self._pair = None

    # ------------------------------------------------------------------ tiempo y dibujo
    def _alpha(self, t_pt: float, now: float) -> float:
        fade = float(self.params["fade_s"])
        if fade <= 0:
            return 1.0
        age = now - t_pt - float(self.params["hold_s"])
        return 1.0 if age <= 0 else max(0.0, 1.0 - age / fade)

    def step(self, now: float) -> None:
        super().step(now)
        if float(self.params["fade_s"]) > 0:
            life = float(self.params["fade_s"]) + float(self.params["hold_s"])
            kept = []
            for st in self.done:
                st.pts = [p for p in st.pts if now - p[2] < life]
                if st.pts:
                    kept.append(st)
            self.done = kept
        if self.params["gesture_clear"] and len(self.live) >= 2:
            self._gesture(now)

    def render(self, img: np.ndarray, sx: float, sy: float, now: float) -> None:
        s = (sx + sy) / 2
        base_w = float(self.params["width"])
        for st in self.done + list(self.live.values()):
            pts = st.pts
            if len(pts) == 1:
                a = self._alpha(pts[0][2], now)
                c = st.color or hsv_bgr(0.5, 0.9, 1.0)
                cv2.circle(img, (int(pts[0][0] * sx), int(pts[0][1] * sy)), max(1, int(base_w * s / 2)), scale_bgr(c, a), -1, cv2.LINE_AA)
                continue
            for (x0, y0, t0, _), (x1, y1, t1, v) in zip(pts[:-1], pts[1:]):
                a = self._alpha(t1, now)
                if a <= 0.01:
                    continue
                if st.color is None:
                    k = float(np.clip(v / 1800.0, 0, 1))
                    c = hsv_bgr(0.5 + 0.42 * k, 0.9, 1.0)
                    w = base_w * (1.4 - 0.9 * k)
                else:
                    c, w = st.color, base_w
                cv2.line(img, (int(x0 * sx), int(y0 * sy)), (int(x1 * sx), int(y1 * sy)), scale_bgr(c, a),
                         max(1, int(round(w * s))), cv2.LINE_AA)

    def status(self) -> dict:
        return {"kind": self.kind, "trazos": len(self.done), "en_curso": len(self.live),
                "puntos": sum(len(s.pts) for s in self.done) + sum(len(s.pts) for s in self.live.values()),
                "color_mode": self.params["color_mode"], "fade_s": self.params["fade_s"], "borrados": self.clears,
                "ultimo_borrado": self.last_clear[1] if self.last_clear else None}
