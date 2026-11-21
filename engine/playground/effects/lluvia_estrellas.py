"""Lluvia de estrellas: caen sobre toda la escena menos los objetos y, al chocar con uno, encienden su contorno.

Cada estrella cae en diagonal (`angulo`) con su estela y titila. La máscara de objetos (contornos de la escena viva,
un poco dilatados) es la zona de choque: al entrar, la estrella desaparece, salta un destello en el punto de impacto
y un arco de luz se abre por el contorno desde ahí (`arco` unidades a cada lado), además de cargar de energía todo el
contorno del objeto, que brilla tenue y se descarga solo. Objetos: `objetos` ('' = todos menos la mesa).
"""

from __future__ import annotations

import math
import time

import cv2
import numpy as np

from .base import Effect, Canvas, bgr
from .. import ambiente as amb


def _resample(poly: np.ndarray, step: float) -> np.ndarray:
    p = np.vstack([poly, poly[:1]]).astype(np.float32)
    seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(seg)])
    n = max(8, int(cum[-1] / max(step, 1.0)))
    t = np.linspace(0, cum[-1], n, endpoint=False)
    return np.stack([np.interp(t, cum, p[:, 0]), np.interp(t, cum, p[:, 1])], 1).astype(np.float32)


class LluviaEstrellas(Effect):
    name = "lluvia_estrellas"
    label = "Lluvia de estrellas"
    aliases = ("lluvia de estrellas", "estrellas", "estrellas fugaces", "que llueva", "lluvia", "meteoritos",
               "estrellas que chocan", "cielo estrellado")
    description = ("Estrellas que caen sobre toda la escena menos los objetos; al chocar con un objeto se encienden en "
                   "su contorno (destello + arco de luz que se abre + contorno cargado). densidad (estrellas), "
                   "velocidad, angulo (grados desde la vertical), estela, color, color_choque, brillo, vida_choque, "
                   "arco, objetos ('' = todos).")
    needs = ("contours",)
    targets = ("*",)
    params = {"densidad": 220, "velocidad": 240.0, "angulo": 20.0, "estela": 0.08, "color": [210, 225, 255],
              "color_choque": [255, 200, 110], "brillo": 1.0, "vida_choque": 1.4, "arco": 90.0, "objetos": "",
              "capa": 8}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self.rng = np.random.default_rng(11)
        self.st = np.zeros((0, 5), np.float32)          # x, y, rapidez, tamaño, fase
        self.objs: list[dict] = []
        self.label_img = None
        self.hits: list[list] = []                       # [obj, idx, t0, fuerza, x, y]
        self.total_hits = 0
        self._key = None
        self.wh = (1920, 1080)
        self.down = 1.0
        self.ms = 0.0

    def _prepare(self, scene, objs):
        W, H = amb.scene_wh(scene)
        self.wh = (W, H)
        self.scale = 2.0
        lab = np.zeros((int(math.ceil(H / self.scale)), int(math.ceil(W / self.scale))), np.uint8)
        self.objs = []
        for i, (o, poly) in enumerate(objs[:254]):
            cv2.fillPoly(lab, [np.round(poly / self.scale).astype(np.int32).reshape(-1, 1, 2)], i + 1)
            self.objs.append({"o": o, "pts": _resample(poly, 4.0), "e": 0.0})
        self.label_img = cv2.dilate(lab, np.ones((3, 3), np.uint8))
        self.hits = []

    def _spawn(self, n: int, spread: bool) -> np.ndarray:
        W, H = self.wh
        s = np.zeros((n, 5), np.float32)
        s[:, 0] = self.rng.uniform(-0.25 * W, 1.05 * W, n)
        y = self.rng.uniform(0, H, n) if spread else self.rng.uniform(-0.15 * H, 0, n)
        s[:, 1] = y if self.down > 0 else H - y
        s[:, 2] = self.rng.uniform(0.6, 1.4, n)
        s[:, 3] = self.rng.uniform(0.6, 1.6, n)
        s[:, 4] = self.rng.uniform(0, 2 * math.pi, n)
        return s

    def update(self, dt, scene):
        super().update(dt, scene)
        self.down = -amb.up_sign(scene)
        objs = amb.select(scene, self.p["objetos"], target=self.target)
        key = (amb.shape_key(objs), str(self.p["objetos"]), amb.scene_wh(scene))
        if key != self._key:
            self._prepare(scene, objs)
            self._key = key
        n = max(0, int(self.p["densidad"]))
        if len(self.st) < n:
            self.st = np.concatenate([self.st, self._spawn(n - len(self.st), spread=len(self.st) == 0)])
        elif len(self.st) > n:
            self.st = self.st[:n]
        if not len(self.st):
            return
        W, H = self.wh
        a = math.radians(float(self.p["angulo"]))
        v = float(self.p["velocidad"])
        st = self.st
        st[:, 0] += math.sin(a) * v * st[:, 2] * dt
        st[:, 1] += self.down * math.cos(a) * v * st[:, 2] * dt
        out = (st[:, 0] > W * 1.1) | ((st[:, 1] > H + 20) if self.down > 0 else (st[:, 1] < -20))
        lab = self.label_img
        ix = np.clip((st[:, 0] / self.scale).astype(np.int32), 0, lab.shape[1] - 1)
        iy = np.clip((st[:, 1] / self.scale).astype(np.int32), 0, lab.shape[0] - 1)
        inside = (st[:, 0] >= 0) & (st[:, 0] < W) & (st[:, 1] >= 0) & (st[:, 1] < H)
        hit_lab = np.where(inside, lab[iy, ix], 0)
        for j in np.nonzero(hit_lab)[0]:
            ob = self.objs[int(hit_lab[j]) - 1]
            d = np.sum((ob["pts"] - st[j, :2]) ** 2, axis=1)
            idx = int(np.argmin(d))
            self.hits.append([ob, idx, self.t, float(st[j, 3]) / 1.6, float(ob["pts"][idx, 0]), float(ob["pts"][idx, 1])])
            ob["e"] = min(1.0, ob["e"] + 0.18)
            self.total_hits += 1
        dead = out | (hit_lab > 0)
        if dead.any():
            st[dead] = self._spawn(int(dead.sum()), spread=False)
        life = float(self.p["vida_choque"])
        self.hits = [h for h in self.hits if self.t - h[2] < life][-90:]
        k = math.exp(-dt / 1.6)
        for ob in self.objs:
            ob["e"] *= k

    def draw(self, canvas: Canvas):
        t0 = time.perf_counter()
        br = float(self.p["brillo"])
        col = np.float32(bgr(self.p["color"]))
        cc = np.float32(bgr(self.p["color_choque"]))
        k = canvas.px_per_mm
        th = max(1, int(round(1.5 * k)))
        proj = {}
        for ob in self.objs:                                     # contorno cargado por los choques
            proj[id(ob)] = P = amb.project_pts(canvas, ob["pts"])
            if ob["e"] > 0.03:
                c = tuple(int(v) for v in np.clip(cc * ob["e"] * 0.6 * br, 0, 255))
                cv2.polylines(canvas.img, [P.reshape(-1, 1, 2)], True, c, th, cv2.LINE_8)
        life = float(self.p["vida_choque"])
        arco = float(self.p["arco"]) / 4.0
        arcs: dict[int, list] = {}                               # por balde de brillo: una sola llamada
        for ob, idx, tt, s, hx, hy in self.hits:
            age = self.t - tt
            fade = (1 - age / life) ** 1.5 * (0.5 + 0.5 * s)
            span = int(arco * (0.25 + 0.75 * min(1.0, age / 0.35)))
            P = proj[id(ob)]
            ii = np.arange(idx - span, idx + span + 1) % len(P)
            arcs.setdefault(min(5, int(fade * 6)), []).append(P[ii].reshape(-1, 1, 2))
        for b, lst in arcs.items():
            c = tuple(int(v) for v in np.clip(cc * ((b + 0.5) / 6 * br), 0, 255))
            cv2.polylines(canvas.img, lst, False, c, th + 1, cv2.LINE_8)
        for ob, idx, tt, s, hx, hy in self.hits:
            age = self.t - tt
            if age < 0.25:
                (px, py), = amb.project_pts(canvas, [[hx, hy]])
                rr = max(2, int((3 + 30 * age) * k))
                cv2.circle(canvas.img, (int(px), int(py)), rr, tuple(int(min(255, v + 80)) for v in cc * (1 - age / 0.25)),
                           th, cv2.LINE_AA)
        st = self.st
        if len(st):
            a = math.radians(float(self.p["angulo"]))
            v = float(self.p["velocidad"]) * float(self.p["estela"])
            tail = st[:, :2] - np.stack([math.sin(a) * v * st[:, 2], self.down * math.cos(a) * v * st[:, 2]], 1)
            P, T = amb.project_pts(canvas, st[:, :2]), amb.project_pts(canvas, tail)
            tw = 0.55 + 0.45 * np.sin(self.t * 7.0 + st[:, 4])
            # por baldes de brillo: dos polylines por balde (estelas finas, cabezas gruesas) en vez de 2 llamadas por estrella
            bucket = np.minimum((tw * 4).astype(np.int32), 3)
            big = st[:, 3] > 1.1
            seg = np.stack([T, P], 1).astype(np.int32)                       # (n, 2, 2)
            head = np.stack([P, P + np.int32([0, 1])], 1).astype(np.int32)
            for b in range(4):
                c = np.clip(col * ((b + 0.5) / 4 * br), 0, 255)
                sel = bucket == b
                if not sel.any():
                    continue
                cv2.polylines(canvas.img, list(seg[sel]), False, tuple(int(v * 0.45) for v in c), 1, cv2.LINE_AA)
                for thick, m in ((max(2, int(2 * k)), sel & ~big), (max(3, int(3 * k)), sel & big)):
                    if m.any():
                        cv2.polylines(canvas.img, list(head[m]), False, tuple(int(v) for v in c), thick, cv2.LINE_8)
        dt_ms = (time.perf_counter() - t0) * 1000
        self.ms = 0.9 * self.ms + 0.1 * dt_ms if self.ms else dt_ms

    def state(self) -> dict:
        return {**super().state(), "ms": round(self.ms, 2), "choques": self.total_hits,
                "objetos": [ob["o"].id for ob in self.objs]}
