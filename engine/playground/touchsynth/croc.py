"""Cocodrilo sintetizador: el contorno exacto del cocodrilo (workbench.json, px del proyector) se divide en zonas a
lo largo del cuerpo, de la cabeza a la cola; cada zona es una nota de la escala y se proyecta con su color.

- Eje del cuerpo: componente principal de la silueta. Cabeza: la punta más ancha (la cola se afina), o la que diga
  `head` (izquierda | derecha | arriba | abajo).
- Zonas: franjas perpendiculares al eje, de igual largo (`split="largo"`, como teclas) o de igual área
  (`split="area"`). Un margen (`margin_px`) alrededor del contorno también cuenta: el dedo en el borde toca.
- Tocar = note on (canal 1 por defecto) + pulso en el lugar; mantener = sostener (la zona queda encendida);
  deslizar a otra zona = glissando (`glide`); la posición a lo ancho del cuerpo manda un CC (`cc`, 1 = modulación).
  Soltar = note off (con largo mínimo, un tap no queda mudo).
- Nota por zona: zona 0 (cabeza) la más grave; `invert` lo da vuelta.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .instrument import Instrument, Pulse, add_layer, palette, scale_bgr, to_canvas
from .music import note_name, scale_notes, velocity_of
from .scan import ScanData

HEAD_DIRS = {"derecha": (1, 0), "right": (1, 0), "izquierda": (-1, 0), "left": (-1, 0),
             "arriba": (0, -1), "top": (0, -1), "abajo": (0, 1), "bottom": (0, 1)}


class CrocZones:
    """Geometría de las zonas (todo en px del proyector)."""

    def __init__(self, contour, n: int = 10, head: str = "auto", split: str = "largo", margin: int = 14):
        if not 1 <= int(n) <= 64:
            raise ValueError("zones entre 1 y 64")
        self.n = int(n)
        self.contour = np.asarray(contour, np.float32).reshape(-1, 2)
        x, y, w, h = cv2.boundingRect(self.contour)
        pad = int(margin) + 2
        self.x0, self.y0 = x - pad, y - pad
        self.w, self.h = w + 2 * pad, h + 2 * pad
        local = np.round(self.contour - (self.x0, self.y0)).astype(np.int32).reshape(-1, 1, 2)
        mask = np.zeros((self.h, self.w), np.uint8)
        cv2.fillPoly(mask, [local], 1)
        self.mask = mask > 0
        hitm = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin + 1, 2 * margin + 1))) if margin > 0 else mask
        self.hitmask = hitm > 0
        ys, xs = np.nonzero(self.mask)
        if len(xs) < 10:
            raise ValueError("contorno del cocodrilo vacío o degenerado")
        pts = np.stack([xs, ys], axis=1).astype(np.float64)
        self.center = pts.mean(axis=0)
        _, _, vt = np.linalg.svd(pts - self.center, full_matrices=False)
        u = vt[0] / np.linalg.norm(vt[0])
        s = (pts - self.center) @ u
        lo, hi = float(s.min()), float(s.max())
        head_key = str(head).lower()
        if head_key in HEAD_DIRS:
            head_at_hi = float(np.dot(u, HEAD_DIRS[head_key])) > 0
            self.head_rule = head_key
        else:
            t = (pts - self.center) @ np.array([-u[1], u[0]])
            L = hi - lo
            ends = []
            for sel in (s < lo + 0.18 * L, s > hi - 0.18 * L):
                tt = t[sel]
                ends.append(float(np.percentile(tt, 95) - np.percentile(tt, 5)) if len(tt) > 5 else 0.0)
            head_at_hi = ends[1] > ends[0]                     # la cabeza es la punta más ancha
            self.head_rule = f"auto (ancho cola {min(ends):.0f} px, cabeza {max(ends):.0f} px)"
        if head_at_hi:
            u = -u
        self.u = u
        self.v = np.array([-u[1], u[0]])
        s = (pts - self.center) @ u
        self.s_lo, self.s_hi = float(s.min()), float(s.max())
        if split == "area":
            edges = np.quantile(s, np.linspace(0, 1, self.n + 1))
        else:
            edges = np.linspace(self.s_lo, self.s_hi, self.n + 1)
        self.edges = edges
        self.split = split
        # etiqueta por píxel dentro del margen: índice de franja (255 = fuera)
        hy, hx = np.nonzero(self.hitmask)
        hs = (np.stack([hx, hy], axis=1) - self.center) @ u
        idx = np.clip(np.searchsorted(edges, hs, side="right") - 1, 0, self.n - 1)
        self.label = np.full((self.h, self.w), 255, np.uint8)
        self.label[hy, hx] = idx.astype(np.uint8)
        self.polys: list[list[np.ndarray]] = []
        self.centers: list[tuple[float, float]] = []
        self.t_range: list[tuple[float, float]] = []
        for i in range(self.n):
            zm = ((self.label == i) & self.mask).astype(np.uint8)
            cnts, _ = cv2.findContours(zm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            polys = [c.reshape(-1, 2).astype(np.float32) + (self.x0, self.y0) for c in cnts if cv2.contourArea(c) >= 4]
            self.polys.append(polys)
            zy, zx = np.nonzero(zm if zm.any() else (self.label == i))
            if len(zx):
                cx, cy = float(zx.mean()) + self.x0, float(zy.mean()) + self.y0
                tt = (np.stack([zx, zy], axis=1) - self.center) @ self.v
                self.t_range.append((float(np.percentile(tt, 3)), float(np.percentile(tt, 97))))
            else:
                mid = (edges[i] + edges[i + 1]) / 2
                cx, cy = (self.center + mid * u + (self.x0, self.y0)).tolist()
                self.t_range.append((-1.0, 1.0))
            self.centers.append((cx, cy))

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.x0, self.y0, self.w, self.h

    def zone_at(self, x: float, y: float) -> int | None:
        lx, ly = int(round(x)) - self.x0, int(round(y)) - self.y0
        if 0 <= lx < self.w and 0 <= ly < self.h:
            z = int(self.label[ly, lx])
            return None if z == 255 else z
        return None

    def across(self, x: float, y: float, zone: int) -> float:
        """Posición a lo ancho del cuerpo en la zona: 0 (un costado) … 1 (el otro)."""
        t = float((np.array([x - self.x0, y - self.y0]) - self.center) @ self.v)
        a, b = self.t_range[zone]
        return float(np.clip((t - a) / max(b - a, 1e-6), 0, 1))

    def length_px(self) -> float:
        return self.s_hi - self.s_lo


class CrocSynth(Instrument):
    kind = "cocodrilo"
    priority = 30
    DEFAULTS = {"zones": 10, "scale": "pentatonica_mayor", "root": 48, "channel": 1, "head": "auto",
                "split": "largo", "invert": False, "margin_px": 14, "decay_s": 0.9, "base": 0.18, "hold": 0.7,
                "labels": True, "palette": "arcoiris", "velocity": 100, "glide": True, "cc": 1, "scan": "",
                "visual": True}

    def __init__(self, scan: ScanData | None = None, params: dict | None = None):
        super().__init__()
        self.scan = scan
        self.zones: CrocZones | None = None
        self.geo_key = None
        self.note_list: list[int] = []
        self.colors: list[tuple[int, int, int]] = []
        self.glow: list[float] = []
        self.held: dict[str, int] = {}
        self.last_cc: dict[str, int] = {}
        self.pulses: list[Pulse] = []
        self.hits = 0
        self._t = None
        self._canvas_cache: tuple | None = None
        self.configure(params or {})
        self.notes.midi.prepare()                         # empieza a buscar la mesa (PC) ya, no con la 1ª nota

    # ------------------------------------------------------------------ configuración
    def set_contour(self, contour) -> None:
        """Contorno vivo de la escena del runtime (scene_from_scan, nudge, reload_scene): manda sobre el archivo."""
        c = np.asarray(contour, np.float32).reshape(-1, 2)
        key = hash(np.round(c, 1).tobytes())
        if key != getattr(self, "_ext_key", None):
            self._ext, self._ext_key = c, key
            self.configure(self.params)

    def configure(self, params: dict) -> None:
        p = {**self.DEFAULTS, **{k: v for k, v in params.items() if k in self.DEFAULTS}}
        self.params = p
        self._ensure_scan(p["scan"], need_height=False)
        ext = getattr(self, "_ext", None)
        src_key = ("escena", self._ext_key) if ext is not None else id(self.scan)
        key = (int(p["zones"]), str(p["head"]), str(p["split"]), int(p["margin_px"]), src_key)
        if key != self.geo_key:
            self.close()
            self.zones = CrocZones(ext if ext is not None else self.scan.croc, key[0], key[1], key[2], key[3])
            self.geo_key = key
            self.glow = [float(p["base"])] * self.zones.n
            self._canvas_cache = None
        n = self.zones.n
        notes = scale_notes(int(p["root"]), p["scale"], n)
        self.note_list = notes[::-1] if p["invert"] else notes
        self.colors = palette(str(p["palette"]), n)

    # ------------------------------------------------------------------ toques
    def hit(self, x: float, y: float) -> bool:
        return self.zones is not None and self.zones.zone_at(x, y) is not None

    def _attack(self, z: int, ev: dict, now: float) -> None:
        p = self.params
        self.notes.on(self.note_list[z], velocity_of(ev.get("velocity"), p["velocity"]), int(p["channel"]), now)
        self.glow[z] = 1.0
        self.hits += 1
        self.pulses.append(Pulse(float(ev["x"]), float(ev["y"]), now, self.colors[z], life=0.7, r0=8, r1=70))

    def handle(self, ev: dict, now: float) -> None:
        tid, state = str(ev.get("id")), ev.get("state")
        p = self.params
        if state == "down":
            z = self.zones.zone_at(ev["x"], ev["y"])
            if z is None:
                return
            if tid in self.held:                                          # down repetido: se trata como move
                self.notes.off(self.note_list[self.held[tid]], int(p["channel"]), now)
            self.held[tid] = z
            self._attack(z, ev, now)
            self._send_cc(tid, z, ev)
        elif state == "move" and tid in self.held:
            z = self.zones.zone_at(ev["x"], ev["y"])
            if z is not None and z != self.held[tid] and p["glide"]:
                self.notes.off(self.note_list[self.held[tid]], int(p["channel"]), now)
                self.held[tid] = z
                self._attack(z, ev, now)
            self._send_cc(tid, self.held[tid], ev)
        elif state == "up" and tid in self.held:
            z = self.held.pop(tid)
            self.notes.off(self.note_list[z], int(p["channel"]), now)
            self.last_cc.pop(tid, None)

    def _send_cc(self, tid: str, z: int, ev: dict) -> None:
        cc = int(self.params.get("cc") or 0)
        if not cc:
            return
        v = int(round(127 * self.zones.across(ev["x"], ev["y"], z)))
        if abs(v - self.last_cc.get(tid, -10)) >= 2:
            self.notes.midi.cc(cc, v, int(self.params["channel"]))
            self.last_cc[tid] = v

    # ------------------------------------------------------------------ tiempo y dibujo
    def _watch_contour(self, now: float) -> None:
        """Si alguien afina el contorno en workbench.json (croc_refine, a mano), se recarga solo (cada 2 s)."""
        if getattr(self, "_ext", None) is not None or self.scan is None or self.scan.folder is None \
                or now - getattr(self, "_t_watch", 0.0) < 2.0:
            return
        self._t_watch = now
        mt = self.scan.json_mtime()
        if getattr(self, "_mt", None) is None:
            self._mt = mt
        elif mt != self._mt:
            self._mt = mt
            self.scan = ScanData.load(self.scan.folder, need_height=False)
            self.reloads = getattr(self, "reloads", 0) + 1
            self.configure(self.params)                               # rehace las zonas (suelta las notas)

    def step(self, now: float) -> None:
        super().step(now)
        self._watch_contour(now)
        dt = 0.0 if self._t is None else max(0.0, now - self._t)
        self._t = now
        p = self.params
        held = set(self.held.values())
        k = math.exp(-dt / max(float(p["decay_s"]), 1e-3))
        for i in range(len(self.glow)):
            target = float(p["hold"]) if i in held else float(p["base"])
            g = self.glow[i]
            self.glow[i] = target + (g - target) * k if g > target else target
        self.pulses = [q for q in self.pulses if q.alive(now)]

    def _scaled(self, sx: float, sy: float):
        key = (round(sx, 4), round(sy, 4))
        if self._canvas_cache is None or self._canvas_cache[0] != key:
            x0, y0 = int(math.floor(self.zones.x0 * sx)), int(math.floor(self.zones.y0 * sy))
            w, h = int(math.ceil(self.zones.w * sx)) + 2, int(math.ceil(self.zones.h * sy)) + 2
            polys = [[to_canvas(pp, sx, sy) - (x0, y0) for pp in zone] for zone in self.zones.polys]
            self._canvas_cache = (key, x0, y0, w, h, polys)
        return self._canvas_cache[1:]

    def render(self, img: np.ndarray, sx: float, sy: float, now: float) -> None:
        if self.zones is None or not self.params.get("visual", True):          # visual=false: solo sonido
            return
        x0, y0, w, h, polys = self._scaled(sx, sy)
        layer = np.zeros((h, w, 3), np.uint8)
        thick = max(1, int(round(2 * (sx + sy) / 2)))
        for i, zone in enumerate(polys):
            if not zone:
                continue
            g = self.glow[i]
            cv2.fillPoly(layer, zone, scale_bgr(self.colors[i], g))
            cv2.polylines(layer, zone, True, scale_bgr(self.colors[i], min(1.0, g + 0.35)), thick, cv2.LINE_AA)
        if self.params["labels"] and sx >= 0.3:
            fs = 0.45 * (sx + sy) / 2
            for i, (cx, cy) in enumerate(self.zones.centers):
                txt = note_name(self.note_list[i])
                (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)
                org = (int(cx * sx) - x0 - tw // 2, int(cy * sy) - y0 + th // 2)
                cv2.putText(layer, txt, org, cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 1, cv2.LINE_AA)
        add_layer(img, layer, x0, y0)
        for q in self.pulses:
            q.draw(img, sx, sy, now)

    def resolve(self, index: int | None) -> tuple[float, float] | None:
        if self.zones is None:
            return None
        if index is None:
            return tuple(float(v) for v in (self.zones.center + (self.zones.x0, self.zones.y0)))
        if 0 <= index < self.zones.n:
            return self.zones.centers[index]
        return None

    def status(self) -> dict:
        z = self.zones
        return {"kind": self.kind, "zones": z.n if z else 0, "head": z.head_rule if z else None,
                "split": self.params.get("split"), "notes": [note_name(n) for n in self.note_list],
                "channel": self.params.get("channel"), "held": {k: v for k, v in self.held.items()},
                "hits": self.hits, "scan": self.scan.source if self.scan else None,
                "bbox": list(z.bbox) if z else None, "largo_px": round(z.length_px(), 1) if z else None,
                "contorno": self.scan.croc_source if self.scan else None, "recargas": getattr(self, "reloads", 0)}

    def close(self) -> None:
        super().close()
        self.held.clear()
