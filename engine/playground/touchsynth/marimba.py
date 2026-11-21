"""Marimba proyectada en una zona libre de la mesa (la mesa menos los objetos del escaneo).

- Zona: el rectángulo libre más grande de `ScanData.free_mask` (o `rect` = [x, y, w, h] a mano).
- Barras: `bars` barras a lo largo del lado largo, graves a la izquierda (o arriba), más largas las graves como en
  una marimba de verdad. Colores de madera; la barra golpeada destella y suelta una onda.
- Golpe = nota con duración fija (`dur_s`, la marimba no sostiene), velocidad del toque si viene (la fusión la saca
  de qué tan rápido bajó el dedo) o `velocity`. Arrastrar el dedo por las barras las va tocando (redoble), canal 2.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .instrument import Instrument, Pulse, hsv_bgr, scale_bgr
from .music import note_name, scale_notes, velocity_of
from .scan import ScanData

FALLBACK_RECT = (1300, 120, 520, 220)


class Marimba(Instrument):
    kind = "marimba"
    priority = 20
    DEFAULTS = {"bars": 8, "scale": "pentatonica_mayor", "root": 60, "channel": 2, "dur_s": 0.35, "velocity": 100,
                "rect": None, "margin_px": 30, "gap_px": 10, "taper": 0.35, "decay_s": 0.5, "base": 0.22,
                "labels": True, "scan": "", "roll": True}

    def __init__(self, scan: ScanData | None = None, params: dict | None = None):
        super().__init__()
        self.scan = scan
        self.rect: tuple[int, int, int, int] | None = None
        self.rect_source = ""
        self.bars: list[np.ndarray] = []           # polígonos (4, 2) px de proyector
        self.note_list: list[int] = []
        self.glow: list[float] = []
        self.held: dict[str, int] = {}
        self.pulses: list[Pulse] = []
        self.hits = 0
        self.geo_key = None
        self._t = None
        self.configure(params or {})
        self.notes.midi.prepare()

    def configure(self, params: dict) -> None:
        p = {**self.DEFAULTS, **{k: v for k, v in params.items() if k in self.DEFAULTS}}
        self.params = p
        rect_param = tuple(int(v) for v in p["rect"]) if p["rect"] else None
        key = (int(p["bars"]), rect_param, int(p["margin_px"]), int(p["gap_px"]), float(p["taper"]), str(p["scan"]))
        if key != self.geo_key:
            self.close()
            if rect_param:
                self.rect, self.rect_source = rect_param, "a mano"
            else:
                self._ensure_scan(p["scan"])
                r, why = self.scan.free_rect(margin=int(p["margin_px"]))
                if r is None:
                    self.rect, self.rect_source = FALLBACK_RECT, "respaldo (no hay zona libre de 240x90 px)"
                else:
                    self.rect, self.rect_source = r, f"{why} ({self.scan.source})"
            self.bars = self._layout(self.rect, int(p["bars"]), int(p["gap_px"]), float(p["taper"]))
            self.glow = [float(p["base"])] * len(self.bars)
            self.geo_key = key
        self.note_list = scale_notes(int(p["root"]), p["scale"], len(self.bars))

    @staticmethod
    def _layout(rect, n: int, gap: int, taper: float) -> list[np.ndarray]:
        x, y, w, h = rect
        horizontal = w >= h                         # barras una al lado de la otra a lo largo del lado largo
        L, S = (w, h) if horizontal else (h, w)
        bw = (L - gap * (n - 1)) / n
        out = []
        for i in range(n):
            frac = 1.0 - taper * i / max(1, n - 1)       # graves más largas
            bl = S * frac
            a0 = i * (bw + gap)
            off = (S - bl) / 2
            if horizontal:
                pts = [(x + a0, y + off), (x + a0 + bw, y + off), (x + a0 + bw, y + off + bl), (x + a0, y + off + bl)]
            else:
                pts = [(x + off, y + a0), (x + off + bl, y + a0), (x + off + bl, y + a0 + bw), (x + off, y + a0 + bw)]
            out.append(np.array(pts, np.float32))
        return out

    # ------------------------------------------------------------------ toques
    def bar_at(self, x: float, y: float, slack: float = 6.0) -> int | None:
        for i, b in enumerate(self.bars):
            if cv2.pointPolygonTest(b.reshape(-1, 1, 2), (float(x), float(y)), True) >= -slack:
                return i
        return None

    def hit(self, x: float, y: float) -> bool:
        if self.rect is None:
            return False
        rx, ry, rw, rh = self.rect
        return rx - 6 <= x <= rx + rw + 6 and ry - 6 <= y <= ry + rh + 6

    def _strike(self, i: int, ev: dict, now: float) -> None:
        p = self.params
        self.notes.on(self.note_list[i], velocity_of(ev.get("velocity"), p["velocity"]), int(p["channel"]), now,
                      dur=float(p["dur_s"]))
        self.glow[i] = 1.0
        self.hits += 1
        self.pulses.append(Pulse(float(ev["x"]), float(ev["y"]), now, hsv_bgr(0.09, 0.5, 1.0), life=0.6, r0=6, r1=60))

    def handle(self, ev: dict, now: float) -> None:
        tid, state = str(ev.get("id")), ev.get("state")
        if state == "down":
            i = self.bar_at(ev["x"], ev["y"])
            self.held[tid] = -1 if i is None else i
            if i is not None:
                self._strike(i, ev, now)
        elif state == "move" and tid in self.held and self.params["roll"]:
            i = self.bar_at(ev["x"], ev["y"])
            if i is not None and i != self.held[tid]:
                self.held[tid] = i
                self._strike(i, ev, now)
        elif state == "up":
            self.held.pop(tid, None)

    # ------------------------------------------------------------------ tiempo y dibujo
    def step(self, now: float) -> None:
        super().step(now)
        dt = 0.0 if self._t is None else max(0.0, now - self._t)
        self._t = now
        k = math.exp(-dt / max(float(self.params["decay_s"]), 1e-3))
        base = float(self.params["base"])
        self.glow = [base + (g - base) * k if g > base else base for g in self.glow]
        self.pulses = [q for q in self.pulses if q.alive(now)]

    def render(self, img: np.ndarray, sx: float, sy: float, now: float) -> None:
        s = (sx + sy) / 2
        n = len(self.bars)
        for i, b in enumerate(self.bars):
            wood = hsv_bgr(0.07 + 0.05 * i / max(1, n - 1), 0.75, 1.0)
            pts = np.round(b * (sx, sy)).astype(np.int32).reshape(-1, 1, 2)
            g = self.glow[i]
            cv2.fillPoly(img, [pts], scale_bgr(wood, g))
            cv2.polylines(img, [pts], True, scale_bgr(wood, min(1.0, g + 0.4)), max(1, int(2 * s)), cv2.LINE_AA)
            if self.params["labels"] and s >= 0.3:
                c = b.mean(axis=0)
                cv2.putText(img, note_name(self.note_list[i]), (int(c[0] * sx) - int(14 * s), int(c[1] * sy) + int(5 * s)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45 * s, scale_bgr((255, 255, 255), 0.7), 1, cv2.LINE_AA)
        for q in self.pulses:
            q.draw(img, sx, sy, now)

    def resolve(self, index: int | None) -> tuple[float, float] | None:
        if not self.bars:
            return None
        if index is None:
            x, y, w, h = self.rect
            return (x + w / 2, y + h / 2)
        if 0 <= index < len(self.bars):
            c = self.bars[index].mean(axis=0)
            return (float(c[0]), float(c[1]))
        return None

    def status(self) -> dict:
        return {"kind": self.kind, "rect": list(self.rect) if self.rect else None, "rect_source": self.rect_source,
                "bars": len(self.bars), "notes": [note_name(n) for n in self.note_list],
                "channel": self.params["channel"], "hits": self.hits}
