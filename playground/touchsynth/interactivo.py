"""Interactividad genérica sobre los objetos de la escena (px del proyector, nada por nombre):

- `Onda` (toque_onda): cada toque lanza una onda que recorre la escena; al alcanzar un objeto ilumina el tramo de
  su contorno que cruza el frente (con el color del objeto), el objeto entero destella y "rebota" una onda hija desde
  el punto de contacto (una generación), con una nota suave por objeto.
- `ManoLuz` (mano_luz): cada mano (o toque) es una luz puntual sobre el relieve (height_proj del escaneo), calculada
  solo en la ventana de la luz; los objetos cercanos reaccionan con un halo que crece con la cercanía. Dos manos =
  dos luces.
- `Memoria` (juego_memoria): Simón con los objetos: se encienden en secuencia y hay que tocarlos en orden; cada
  ronda suma uno. Puntaje en el objeto HUD (el plano más chico: hoja, cuadro… o `hud`).
- `Mirada` (mirada): enfoca lo que se mira (`scene.gaze`, comando gaze): foco suave, el objeto mirado se enciende y,
  si la mirada se queda `dwell_s`, queda elegido (anillo de progreso).
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .instrument import Instrument, add_layer, scale_bgr
from .linterna import HeightField
from .music import scale_notes
from .objetos import ObjPx, order_quad, resample_closed, rgb_to_bgr, runs_of

W, H = 1920, 1080


def _pts(a: np.ndarray, sx: float, sy: float) -> np.ndarray:
    return np.round(np.asarray(a, np.float64) * (sx, sy)).astype(np.int32).reshape(-1, 1, 2)


def _outline(img, o: ObjPx, color_bgr, k: float, sx: float, sy: float, thick: float = 2.0) -> None:
    if k <= 0.01:
        return
    th = max(1, int(round(thick * (sx + sy) / 2)))
    cv2.polylines(img, [_pts(o.contour, sx, sy)], True, scale_bgr(color_bgr, k), th, cv2.LINE_AA)


def _fill_add(img, o: ObjPx, color_bgr, k: float, sx: float, sy: float) -> None:
    """Relleno aditivo del objeto (solo su caja)."""
    if k <= 0.01:
        return
    x, y, mk = o.mask_at(sx, sy)
    h, w = mk.shape
    H_, W_ = img.shape[:2]
    xa, ya, xb, yb = max(0, x), max(0, y), min(W_, x + w), min(H_, y + h)
    if xb <= xa or yb <= ya:
        return
    roi = img[ya:yb, xa:xb]
    c = scale_bgr(color_bgr, k)
    cv2.add(roi, (c[0], c[1], c[2], 0), dst=roi, mask=np.ascontiguousarray(mk[ya - y:yb - y, xa - x:xb - x]))


def _halo(img, o: ObjPx, color_bgr, k: float, sx: float, sy: float, gap: float = 10.0, rings: int = 2) -> None:
    """Halo: anillos concéntricos hacia afuera (radial desde el centro) que crecen con k (0..1)."""
    if k <= 0.02:
        return
    c = o.contour
    ctr = np.asarray(o.center, np.float32)
    d = c - ctr
    n = np.linalg.norm(d, axis=1, keepdims=True)
    n[n == 0] = 1
    u = d / n
    for j in range(1, rings + 1):
        off = c + u * (gap * j * (0.4 + 1.6 * k))
        a = k * (1 - j / (rings + 1))
        th = max(1, int(round((1 + 2 * k) * (sx + sy) / 2)))
        cv2.polylines(img, [_pts(off, sx, sy)], True, scale_bgr(color_bgr, a), th, cv2.LINE_AA)
    _outline(img, o, color_bgr, min(1.0, 0.3 + k), sx, sy, 2 + 2 * k)


# ====================================================================== toque_onda

@dataclass
class Wave:
    x: float
    y: float
    t0: float
    color: tuple            # BGR
    reach: float
    gen: int = 0
    src: str | None = None  # objeto del que rebotó (no se vuelve a pegar en él)
    hit: set = field(default_factory=set)
    dist: dict = field(default_factory=dict)       # objeto → distancias de sus puntos al origen


class Onda(Instrument):
    kind = "onda"
    priority = 0
    claims_touch = False
    observes_touch = True              # ve todos los toques sin quitárselos a los instrumentos
    DEFAULTS = {"velocidad": 700.0, "alcance": 1400.0, "grosor": 3, "banda": 34.0, "color": [170, 220, 255],
                "color_por_objeto": True, "rebote": True, "destello_s": 0.9, "max_ondas": 10, "arrastre_px": 0.0,
                "sonido": True, "channel": 3, "root": 60, "scale": "pentatonica_mayor", "velocity": 70}

    def __init__(self, params: dict | None = None):
        super().__init__()
        self.objs: list[ObjPx] = []
        self.pts: dict[str, np.ndarray] = {}
        self.waves: list[Wave] = []
        self.flash: dict[str, float] = {}
        self.last_xy: dict[str, tuple[float, float]] = {}
        self.hits = 0
        self.bounces = 0
        self._t = None
        self.configure(params or {})

    def configure(self, params: dict) -> None:
        self.params = {**self.DEFAULTS, **{k: v for k, v in params.items() if k in self.DEFAULTS}}

    def set_objects(self, objs: list[ObjPx]) -> None:
        self.objs = objs
        self.pts = {o.id: resample_closed(o.contour, 6.0) for o in objs}
        self.flash = {o.id: self.flash.get(o.id, 0.0) for o in objs}
        notes = scale_notes(int(self.params["root"]), self.params["scale"], max(1, len(objs)))
        self.note_of = {o.id: notes[i] for i, o in enumerate(sorted(objs, key=lambda q: q.center[0]))}

    def _obj_at(self, x: float, y: float) -> ObjPx | None:
        inside = [o for o in self.objs if o.inside(x, y)]
        return min(inside, key=lambda o: o.area) if inside else None

    def launch(self, x: float, y: float, now: float, color=None, gen: int = 0, src: str | None = None,
               reach: float | None = None) -> Wave:
        p = self.params
        if color is None:
            o = self._obj_at(x, y) if p["color_por_objeto"] else None
            color = rgb_to_bgr(o.color if o is not None else p["color"])
        w = Wave(float(x), float(y), now, tuple(color), float(reach or p["alcance"]), gen, src)
        for o in self.objs:
            pp = self.pts[o.id]
            w.dist[o.id] = np.hypot(pp[:, 0] - w.x, pp[:, 1] - w.y)
        self.waves.append(w)
        if len(self.waves) > int(p["max_ondas"]):
            self.waves = self.waves[-int(p["max_ondas"]):]
        return w

    def handle(self, ev: dict, now: float) -> None:
        tid, st = str(ev.get("id")), ev.get("state")
        x, y = float(ev["x"]), float(ev["y"])
        if st == "down":
            self.launch(x, y, now)
            self.last_xy[tid] = (x, y)
        elif st == "move" and float(self.params["arrastre_px"]) > 0 and tid in self.last_xy:
            lx, ly = self.last_xy[tid]
            if math.hypot(x - lx, y - ly) >= float(self.params["arrastre_px"]):
                self.launch(x, y, now)
                self.last_xy[tid] = (x, y)
        elif st == "up":
            self.last_xy.pop(tid, None)

    def step(self, now: float) -> None:
        super().step(now)
        p = self.params
        v = float(p["velocidad"])
        born = []
        for w in self.waves:
            r = v * (now - w.t0)
            for o in self.objs:
                if o.id in w.hit or o.id == w.src:
                    continue
                d = w.dist.get(o.id)
                if d is None or r < float(d.min()):
                    continue
                w.hit.add(o.id)
                self.hits += 1
                fade = max(0.0, 1 - r / w.reach)
                self.flash[o.id] = max(self.flash.get(o.id, 0.0), 0.35 + 0.65 * fade)
                if p["sonido"] and w.gen == 0 and fade > 0.05:
                    vel = int(max(1, min(127, float(p["velocity"]) * (0.4 + 0.6 * fade))))
                    self.notes.on(self.note_of.get(o.id, 60), vel, int(p["channel"]), now, dur=0.35)
                if p["rebote"] and w.gen == 0 and fade > 0.1:
                    i = int(np.argmin(d))
                    hx, hy = self.pts[o.id][i]
                    born.append((hx, hy, rgb_to_bgr(o.color), o.id, w.reach * 0.45 * fade + 60))
        for hx, hy, col, oid, reach in born:
            self.launch(hx, hy, now, col, 1, oid, reach)
            self.bounces += 1
        self.waves = [w for w in self.waves if v * (now - w.t0) < w.reach]
        dt = 0.0 if self._t is None else max(0.0, min(0.5, now - self._t))
        self._t = now
        k = math.exp(-dt / max(float(p["destello_s"]), 1e-3))
        for oid in self.flash:
            self.flash[oid] *= k

    def render(self, img: np.ndarray, sx: float, sy: float, now: float) -> None:
        p = self.params
        v, band = float(p["velocidad"]), float(p["banda"])
        s = (sx + sy) / 2
        lit: dict[str, np.ndarray] = {}
        for w in self.waves:
            r = v * (now - w.t0)
            fade = max(0.0, 1 - r / w.reach) ** 1.2
            if fade <= 0.01:
                continue
            c = (int(round(w.x * sx)), int(round(w.y * sy)))
            th = max(1, int(round(float(p["grosor"]) * s * (1.0 if w.gen == 0 else 0.6))))
            cv2.circle(img, c, max(1, int(r * s)), scale_bgr(w.color, fade), th, cv2.LINE_AA)
            if r > 30:
                cv2.circle(img, c, max(1, int((r - 22) * s)), scale_bgr(w.color, 0.35 * fade), max(1, th // 2), cv2.LINE_AA)
            for oid, d in w.dist.items():
                k = np.exp(-((d - r) / band) ** 2) * fade
                cur = lit.get(oid)
                lit[oid] = k if cur is None else np.maximum(cur, k)
        for o in self.objs:
            col = rgb_to_bgr(o.color)
            f = self.flash.get(o.id, 0.0)
            if f > 0.02:
                _fill_add(img, o, col, 0.25 * f, sx, sy)
                _outline(img, o, col, f, sx, sy, 2 + 3 * f)
            k = lit.get(o.id)
            if k is None:
                continue
            pp = self.pts[o.id]
            for lo, hi, gain, th in ((0.15, 0.45, 0.45, 3), (0.45, 2.0, 1.0, 5)):
                segs = [_pts(pp[a:b], sx, sy) for a, b in runs_of(k >= lo) if b - a >= 2]
                if segs:
                    cv2.polylines(img, segs, False, scale_bgr(col, gain), max(1, int(round(th * s))), cv2.LINE_AA)

    def status(self) -> dict:
        return {"kind": self.kind, "ondas": len(self.waves), "objetos": [o.id for o in self.objs],
                "alcances": self.hits, "rebotes": self.bounces, "destellos": {k: round(v, 2) for k, v in self.flash.items() if v > 0.02}}


# ====================================================================== mano_luz

@dataclass
class HandLight:
    x: float
    y: float
    z: float
    color: tuple            # BGR 0..1
    hid: str


class ManoLuz(Instrument):
    kind = "mano_luz"
    priority = 0
    claims_touch = False
    DEFAULTS = {"radio": 340.0, "intensidad": 1.25, "colores": [[255, 226, 180], [170, 210, 255]], "altura": 200.0,
                "relieve": 2.0, "suavizado": 2.0, "escala": 5, "alcance": 260.0, "halo": True, "halo_gap": 12.0,
                "max_luces": 2, "suave": 0.5, "manos_stale_s": 0.6, "fuente": "auto", "demo": False,
                "ambiente": 0.0, "scan": ""}

    def __init__(self, params: dict | None = None):
        super().__init__()
        self.scan = None
        self.hf: HeightField | None = None
        self.hf_key = None
        self.lights: dict[str, HandLight] = {}
        self.objs: list[ObjPx] = []
        self.near: dict[str, float] = {}
        self.source = "ninguna"
        self.configure(params or {})

    def configure(self, params: dict) -> None:
        p = {**self.DEFAULTS, **{k: v for k, v in params.items() if k in self.DEFAULTS}}
        self.params = p
        self._ensure_scan(p["scan"])
        key = (id(self.scan), float(p["relieve"]), float(p["suavizado"]), int(p["escala"]))
        if key != self.hf_key:
            height = self.scan.height if self.scan.height is not None else np.zeros((H, W), np.float32)
            self.hf = HeightField(height, key[3], key[2], key[1])
            self.hf_key = key

    def set_objects(self, objs: list[ObjPx]) -> None:
        self.objs = objs
        self.near = {o.id: self.near.get(o.id, 0.0) for o in objs}

    def _colors(self) -> list[tuple]:
        cs = self.params["colores"]
        if cs and isinstance(cs[0], (int, float)):
            cs = [cs]
        return [tuple(float(v) / 255.0 for v in c[:3][::-1]) for c in cs] or [(1.0, 1.0, 1.0)]

    def update_sources(self, hands: dict, touches: dict, now: float) -> None:
        p = self.params
        pts: dict[str, dict] = {}
        mode = str(p["fuente"])
        if mode in ("auto", "manos"):
            for k, h in (hands or {}).items():
                if now - float(h.get("t", now)) <= float(p["manos_stale_s"]):
                    pts[f"m{k}"] = h
        if not pts and mode in ("auto", "toques"):
            for k, t in (touches or {}).items():
                pts[f"t{k}"] = {**t, "height": t.get("height", 0.15)}
        self.source = "manos" if any(k.startswith("m") for k in pts) else ("toques" if pts else "ninguna")
        if not pts and p["demo"]:
            a = now * 0.5
            pts["demo0"] = {"x": 960 + 600 * math.cos(a), "y": 540 + 300 * math.sin(a * 1.3), "height": 0.4}
            pts["demo1"] = {"x": 960 - 500 * math.cos(a * 0.7), "y": 540 - 280 * math.sin(a), "height": 0.3}
            self.source = "demo"
        cols = self._colors()
        alpha = float(p["suave"])
        new = {}
        for i, (k, h) in enumerate(list(pts.items())[: int(p["max_luces"])]):
            z = float(p["altura"])
            if h.get("height") is not None:
                z = float(p["altura"]) * (0.35 + 1.3 * float(np.clip(h["height"], 0, 1)))
            x, y = float(h["x"]), float(h["y"])
            prev = self.lights.get(k)
            if prev is not None:
                x, y, z = (alpha * x + (1 - alpha) * prev.x, alpha * y + (1 - alpha) * prev.y, alpha * z + (1 - alpha) * prev.z)
            new[k] = HandLight(x, y, z, cols[i % len(cols)], k)
        self.lights = new
        # cercanía de cada objeto a la luz más próxima (0 lejos … 1 encima), suavizada
        R = max(1.0, float(p["alcance"]))
        for o in self.objs:
            k = 0.0
            for L in new.values():
                k = max(k, float(np.clip(1 - o.distance(L.x, L.y) / R, 0, 1)))
            self.near[o.id] = 0.6 * self.near.get(o.id, 0.0) + 0.4 * k

    def _shade_window(self, L: HandLight) -> tuple[np.ndarray, int, int] | None:
        """Luz de una mano solo en su ventana (radio): float32 BGR a la resolución del campo de alturas."""
        hf, p = self.hf, self.params
        s = hf.scale
        R = float(p["radio"])
        hh, ww = hf.shape
        i0, i1 = max(0, int((L.y - R) / s)), min(hh, int((L.y + R) / s) + 1)
        j0, j1 = max(0, int((L.x - R) / s)), min(ww, int((L.x + R) / s) + 1)
        if i1 <= i0 or j1 <= j0:
            return None
        f32 = np.float32
        dx = f32(L.x) - hf.X[i0:i1, j0:j1]
        dy = f32(L.y) - hf.Y[i0:i1, j0:j1]
        dz = f32(L.z) - hf.z[i0:i1, j0:j1]
        dxy2 = dx * dx + dy * dy
        lam = hf.nx[i0:i1, j0:j1] * dx
        lam += hf.ny[i0:i1, j0:j1] * dy
        lam += hf.nz[i0:i1, j0:j1] * dz
        lam /= np.sqrt(dxy2 + dz * dz) + f32(1e-6)
        np.maximum(lam, 0, out=lam)
        att = f32(1.0) - dxy2 * f32(1.0 / (R * R))
        np.maximum(att, 0, out=att)
        lam *= att
        lam *= att
        lam *= f32(255.0 * float(p["intensidad"]))
        out = cv2.merge([cv2.convertScaleAbs(lam, alpha=float(c)) for c in L.color])      # uint8 BGR, satura en 255
        return out, j0 * s, i0 * s

    def render(self, img: np.ndarray, sx: float, sy: float, now: float) -> None:
        p = self.params
        amb = float(p["ambiente"])
        if amb > 0:
            cv2.add(img, (int(255 * amb),) * 3 + (0,), dst=img)
        s = self.hf.scale
        for L in self.lights.values():
            r = self._shade_window(L)
            if r is None:
                continue
            lit8, x0, y0 = r
            h, w = lit8.shape[:2]
            tw, th = max(1, int(round(w * s * sx))), max(1, int(round(h * s * sy)))
            lit8 = cv2.resize(lit8, (tw, th), interpolation=cv2.INTER_LINEAR)
            add_layer(img, lit8, int(round(x0 * sx)), int(round(y0 * sy)))
        if p["halo"]:
            for o in self.objs:
                k = self.near.get(o.id, 0.0)
                if k > 0.02:
                    _halo(img, o, rgb_to_bgr(o.color), k, sx, sy, float(p["halo_gap"]))

    def status(self) -> dict:
        return {"kind": self.kind, "luces": {k: [round(l.x), round(l.y), round(l.z)] for k, l in self.lights.items()},
                "fuente": self.source, "relieve": self.scan.source if self.scan else None,
                "cerca": {k: round(v, 2) for k, v in self.near.items() if v > 0.02}}


# ====================================================================== juego_memoria

class Memoria(Instrument):
    kind = "memoria"
    priority = 40                      # mientras se juega, tocar un objeto del juego es jugar
    claims_touch = True
    DEFAULTS = {"inicio": 3, "paso_s": 0.75, "encendido": 0.7, "pausa_s": 0.9, "error_s": 2.2, "bien_s": 1.0,
                "base": 0.14, "margen_px": 18.0, "sonido": True, "channel": 4, "root": 60,
                "scale": "pentatonica_mayor", "velocity": 96, "semilla": None, "max_largo": 24}

    def __init__(self, params: dict | None = None):
        super().__init__()
        self.objs: list[ObjPx] = []
        self.hud: ObjPx | None = None
        self.hud_rect: np.ndarray | None = None
        self.seq: list[str] = []
        self.pos = 0
        self.state = "pausa"
        self.t_state = 0.0
        self.score = 0
        self.best = 0
        self.rounds = 0
        self.flash: dict[str, float] = {}
        self.message = ""
        self._hud_cache = None
        self.configure(params or {})
        self.rng = random.Random(self.params["semilla"])

    def configure(self, params: dict) -> None:
        self.params = {**self.DEFAULTS, **{k: v for k, v in params.items() if k in self.DEFAULTS}}

    def set_objects(self, objs: list[ObjPx], hud: ObjPx | None, hud_rect=None) -> None:
        ids_before = [o.id for o in self.objs]
        self.objs = objs
        self.hud = hud
        self.hud_rect = None if hud_rect is None else np.asarray(hud_rect, np.float32)
        notes = scale_notes(int(self.params["root"]), self.params["scale"], max(1, len(objs)))
        self.note_of = {o.id: notes[i] for i, o in enumerate(sorted(objs, key=lambda q: q.center[0]))}
        if [o.id for o in objs] != ids_before:
            self.seq = [s for s in self.seq if s in self.note_of]
            self._hud_cache = None

    def by_id(self, oid: str) -> ObjPx | None:
        return next((o for o in self.objs if o.id == oid), None)

    def _obj_at(self, x: float, y: float) -> ObjPx | None:
        m = float(self.params["margen_px"])
        inside = [o for o in self.objs if o.inside(x, y, m)]
        return min(inside, key=lambda o: o.area) if inside else None

    def _hud_hit(self, x: float, y: float) -> bool:
        return self.hud is not None and self.hud.inside(x, y, 10)

    def hit(self, x: float, y: float) -> bool:
        return self._obj_at(x, y) is not None or self._hud_hit(x, y)

    # ------------------------------------------------------------------ juego
    def _set(self, state: str, now: float, msg: str = "") -> None:
        self.state, self.t_state, self.message = state, now, msg
        self._hud_cache = None

    def _next(self) -> str:
        ids = [o.id for o in self.objs]
        cand = [i for i in ids if not self.seq or i != self.seq[-1]] or ids
        return self.rng.choice(cand)

    def start(self, now: float) -> None:
        if not self.objs:
            self._set("pausa", now, "sin objetos")
            return
        self.seq = [self._next() for _ in range(max(1, int(self.params["inicio"])))]
        for i in range(1, len(self.seq)):          # sin repetidos seguidos
            if self.seq[i] == self.seq[i - 1] and len(self.objs) > 1:
                self.seq[i] = self.rng.choice([o.id for o in self.objs if o.id != self.seq[i - 1]])
        self.score, self.pos = 0, 0
        self._set("mostrando", now, "mira")

    def _play(self, oid: str, now: float, vel: float = 1.0) -> None:
        if self.params["sonido"] and oid in self.note_of:
            self.notes.on(self.note_of[oid], int(float(self.params["velocity"]) * vel), int(self.params["channel"]), now,
                          dur=0.3)

    def handle(self, ev: dict, now: float) -> None:
        if ev.get("state") != "down":
            return
        x, y = float(ev["x"]), float(ev["y"])
        o = self._obj_at(x, y)
        if o is None:
            if self._hud_hit(x, y) and self.state in ("pausa", "error"):
                self.start(now)
            return
        if self.state == "pausa":
            self.start(now)
            return
        if self.state != "esperando":
            return
        self.flash[o.id] = 1.0
        if o.id == self.seq[self.pos]:
            self._play(o.id, now)
            self.pos += 1
            if self.pos >= len(self.seq):
                self.score += len(self.seq)
                self.rounds += 1
                self.best = max(self.best, self.score)
                self._set("bien", now, "bien!")
        else:
            self.best = max(self.best, self.score)
            if self.params["sonido"]:
                self.notes.on(int(self.params["root"]) - 13, 90, int(self.params["channel"]), now, dur=0.5)
            self._set("error", now, "uy!")

    def current(self, now: float) -> str | None:
        """Objeto encendido por la secuencia en este instante (en 'mostrando')."""
        if self.state != "mostrando":
            return None
        t = now - self.t_state - float(self.params["pausa_s"]) * 0.5
        if t < 0:
            return None
        i, frac = int(t // float(self.params["paso_s"])), (t % float(self.params["paso_s"])) / float(self.params["paso_s"])
        if i < len(self.seq) and frac < float(self.params["encendido"]):
            return self.seq[i]
        return None

    def step(self, now: float) -> None:
        super().step(now)
        p = self.params
        if self.state == "pausa" and self.objs and self.t_state == 0.0:
            self.start(now)                                   # arranca solo al aparecer
        if self.state == "mostrando":
            cur = self.current(now)
            if cur is not None and cur != getattr(self, "_last_shown", None):
                self._play(cur, now, 0.8)
            self._last_shown = cur
            if now - self.t_state > float(p["pausa_s"]) * 0.5 + len(self.seq) * float(p["paso_s"]) + 0.15:
                self.pos = 0
                self._set("esperando", now, "tu turno")
        elif self.state == "bien" and now - self.t_state > float(p["bien_s"]):
            if len(self.seq) < int(p["max_largo"]):
                self.seq.append(self._next())
            self._set("mostrando", now, "mira")
        elif self.state == "error" and now - self.t_state > float(p["error_s"]):
            self.start(now)
        for k in list(self.flash):
            self.flash[k] *= 0.85
            if self.flash[k] < 0.02:
                del self.flash[k]

    # ------------------------------------------------------------------ dibujo
    def _hud_image(self, w: int, h: int) -> np.ndarray:
        key = (w, h, self.state, self.score, self.best, len(self.seq), self.message)
        if self._hud_cache is not None and self._hud_cache[0] == key:
            return self._hud_cache[1]
        img = np.zeros((h, w, 3), np.uint8)
        lines = [(f"{self.score}", 0.40, (255, 255, 255)),
                 (f"nivel {max(0, len(self.seq) - int(self.params['inicio']) + 1)}", 0.13, (200, 220, 255)),
                 (f"record {self.best}", 0.11, (170, 190, 230)),
                 (self.message or "", 0.18, {"error": (60, 60, 255), "bien": (90, 255, 120)}.get(self.state, (255, 210, 120)))]
        y = int(h * 0.06)
        for txt, rel, col in lines:                  # cada línea en su franja (rel = fracción del alto)
            band = int(h * rel)
            if txt:
                (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_DUPLEX, 1.0, 2)
                fs = min(w * 0.84 / max(tw, 1), band * 0.78 / max(th, 1))
                (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_DUPLEX, fs, max(1, int(fs * 2)))
                cv2.putText(img, txt, ((w - tw) // 2, y + (band + th) // 2), cv2.FONT_HERSHEY_DUPLEX, fs, col,
                            max(1, int(fs * 2)), cv2.LINE_AA)
            y += band + int(h * 0.04)
        cv2.rectangle(img, (2, 2), (w - 3, h - 3), (120, 120, 120), 2)
        self._hud_cache = (key, img)
        return img

    def _draw_hud(self, img: np.ndarray, sx: float, sy: float) -> None:
        quad = self.hud_rect
        if quad is None and self.hud is not None:
            quad = self.hud.quad if self.hud.quad is not None else cv2.boxPoints(cv2.minAreaRect(self.hud.contour))
        if quad is None:
            quad = np.array([[40, 40], [400, 40], [400, 240], [40, 240]], np.float32)
        q = order_quad(quad) * np.array([sx, sy], np.float32)
        wq = float(max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3])))
        hq = float(max(np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1])))
        if wq < 8 or hq < 8:
            return
        tw, th = int(wq), int(hq)
        if th > tw * 1.15:                           # hoja vertical: el texto va derecho igual (lado largo vertical)
            pass
        src = self._hud_image(tw, th)
        x, y, bw, bh = cv2.boundingRect(np.round(q).astype(np.int32))
        M = cv2.getPerspectiveTransform(np.float32([[0, 0], [tw, 0], [tw, th], [0, th]]), q - np.float32([x, y]))
        warped = cv2.warpPerspective(src, M, (bw, bh))
        add_layer(img, warped, x, y)

    def render(self, img: np.ndarray, sx: float, sy: float, now: float) -> None:
        p = self.params
        cur = self.current(now)
        for o in self.objs:
            col = rgb_to_bgr(o.color)
            k = float(p["base"]) * (0.7 + 0.3 * math.sin(now * 2.0 + o.center[0] * 0.01))
            if self.state == "esperando":
                k *= 1.6
            if self.state == "error":
                col, k = (40, 40, 255), 0.5 * (1 if int((now - self.t_state) * 6) % 2 == 0 else 0.2)
            if self.state == "bien":
                k = 0.6
            if o.id == cur:
                _fill_add(img, o, col, 0.85, sx, sy)
                _outline(img, o, col, 1.0, sx, sy, 5)
                continue
            f = self.flash.get(o.id, 0.0)
            if f > 0.02:
                _fill_add(img, o, col, 0.7 * f, sx, sy)
            _outline(img, o, col, max(k, f), sx, sy, 2 + 3 * f)
        self._draw_hud(img, sx, sy)

    def resolve(self, index: int | None):
        """memoria:N = centro del N-ésimo de la secuencia (para jugar por comando); sin N = el que toca ahora."""
        if not self.seq:
            return None
        if index is None:
            index = min(self.pos, len(self.seq) - 1)
        if 0 <= index < len(self.seq):
            o = self.by_id(self.seq[index])
            return o.center if o is not None else None
        return None

    def status(self) -> dict:
        return {"kind": self.kind, "estado": self.state, "secuencia": list(self.seq), "pos": self.pos,
                "puntaje": self.score, "record": self.best, "rondas": self.rounds,
                "objetos": [o.id for o in self.objs], "hud": self.hud.id if self.hud else None}


# ====================================================================== mirada

class Mirada(Instrument):
    kind = "mirada"
    priority = 0
    claims_touch = False
    DEFAULTS = {"radio": 170.0, "color": [255, 244, 220], "intensidad": 0.55, "suave": 0.25, "margen_px": 40.0,
                "dwell_s": 1.2, "atenuar": 0.12, "stale_s": 2.0, "demo": False, "relleno": 0.3}

    def __init__(self, params: dict | None = None):
        super().__init__()
        self.objs: list[ObjPx] = []
        self.pt: tuple[float, float] | None = None
        self.focus: str | None = None
        self.focus_k: dict[str, float] = {}
        self.dwell = 0.0
        self.chosen: str | None = None
        self.chosen_t = 0.0
        self._t = None
        self._spot = None
        self._demo_i, self._demo_t = 0, 0.0
        self.configure(params or {})

    def configure(self, params: dict) -> None:
        p = {**self.DEFAULTS, **{k: v for k, v in params.items() if k in self.DEFAULTS}}
        if (self._spot is not None and (p["radio"], tuple(p["color"]), p["intensidad"]) != self._spot[0]):
            self._spot = None
        self.params = p

    def set_objects(self, objs: list[ObjPx]) -> None:
        self.objs = objs
        self.focus_k = {o.id: self.focus_k.get(o.id, 0.0) for o in objs}

    def _demo_gaze(self, now: float) -> dict | None:
        if not self.objs:
            return None
        if now - self._demo_t > 2.2:
            self._demo_i, self._demo_t = (self._demo_i + 1) % len(self.objs), now
        o = self.objs[self._demo_i]
        return {"x": o.center[0] + 12 * math.sin(now * 3.1), "y": o.center[1] + 9 * math.cos(now * 2.3), "t": now}

    def update_gaze(self, gaze: dict | None, now: float) -> None:
        p = self.params
        dt = 0.0 if self._t is None else max(0.0, min(0.2, now - self._t))
        self._t = now
        g = gaze if gaze and "x" in gaze and now - float(gaze.get("t", now)) <= float(p["stale_s"]) else None
        if g is None and p["demo"]:
            g = self._demo_gaze(now)
        if g is None:
            self.pt = None
            target = None
        else:
            gx, gy = float(g["x"]), float(g["y"])
            a = float(np.clip(p["suave"], 0.02, 1.0))
            self.pt = (gx, gy) if self.pt is None else (self.pt[0] + a * (gx - self.pt[0]), self.pt[1] + a * (gy - self.pt[1]))
            m = float(p["margen_px"])
            cand = [(o.distance(*self.pt), o.area, o) for o in self.objs]
            cand = [c for c in cand if c[0] <= m]
            target = min(cand, key=lambda c: (c[0], c[1]))[2].id if cand else None
        if target != self.focus:
            self.focus, self.dwell = target, 0.0
        elif target is not None:
            self.dwell += dt
            if self.dwell >= float(p["dwell_s"]) and self.chosen != target:
                self.chosen, self.chosen_t = target, now
        for oid in self.focus_k:
            goal = 1.0 if oid == self.focus else 0.0
            self.focus_k[oid] += (goal - self.focus_k[oid]) * min(1.0, dt * 6.0)

    def _spot_sprite(self) -> np.ndarray:
        p = self.params
        key = (p["radio"], tuple(p["color"]), p["intensidad"])
        if self._spot is None or self._spot[0] != key:
            r = int(max(8, float(p["radio"])))
            yy, xx = np.mgrid[-r:r + 1, -r:r + 1].astype(np.float32)
            d = np.sqrt(xx * xx + yy * yy) / r
            k = np.clip(1 - d * d, 0, 1) ** 2 * float(p["intensidad"])
            col = np.asarray(rgb_to_bgr(p["color"]), np.float32)
            self._spot = (key, np.clip(k[..., None] * col, 0, 255).astype(np.uint8))
        return self._spot[1]

    def render(self, img: np.ndarray, sx: float, sy: float, now: float) -> None:
        p = self.params
        for o in self.objs:
            k = self.focus_k.get(o.id, 0.0)
            col = rgb_to_bgr(o.color)
            if k > 0.02:
                _fill_add(img, o, col, float(p["relleno"]) * k, sx, sy)
                _outline(img, o, col, 0.4 + 0.6 * k, sx, sy, 2 + 3 * k)
            elif float(p["atenuar"]) > 0 and self.pt is not None:
                _outline(img, o, col, float(p["atenuar"]), sx, sy, 1)
            if o.id == self.chosen and now - self.chosen_t < 1.2:
                a = 1 - (now - self.chosen_t) / 1.2
                _halo(img, o, col, a, sx, sy, 14.0)
        if self.pt is not None:
            spr = self._spot_sprite()
            if abs(sx - 1) > 1e-3 or abs(sy - 1) > 1e-3:
                spr = cv2.resize(spr, (max(1, int(spr.shape[1] * sx)), max(1, int(spr.shape[0] * sy))))
            h, w = spr.shape[:2]
            cx, cy = int(round(self.pt[0] * sx)), int(round(self.pt[1] * sy))
            add_layer(img, spr, cx - w // 2, cy - h // 2)
            if self.focus is not None:
                frac = min(1.0, self.dwell / max(float(p["dwell_s"]), 1e-3))
                r = int(0.35 * float(p["radio"]) * (sx + sy) / 2)
                cv2.ellipse(img, (cx, cy), (r, r), -90, 0, 360 * frac, (255, 255, 255), 2, cv2.LINE_AA)

    def status(self) -> dict:
        return {"kind": self.kind, "punto": None if self.pt is None else [round(v, 1) for v in self.pt],
                "foco": self.focus, "dwell_s": round(self.dwell, 2), "elegido": self.chosen,
                "objetos": [o.id for o in self.objs]}


def gaze_of(scene) -> dict | None:
    g = getattr(scene, "gaze", None)
    return dict(g) if g else None


def pick_hud(objs: list[ObjPx], hud: str = "", scene=None) -> ObjPx | None:
    """El objeto donde va el puntaje: `hud` (id/alias) o el plano más chico (kind plane/screen/frame, o con quad)."""
    if hud:
        o = scene.find(hud) if scene is not None else None
        if o is not None:
            m = next((q for q in objs if q.id == o.id), None)
            if m is not None:
                return m
            from .objetos import to_objpx
            return to_objpx(scene, o)
    planes = [o for o in objs if o.kind in ("plane", "screen", "frame") or o.quad is not None]
    return min(planes, key=lambda o: o.area) if planes else None
