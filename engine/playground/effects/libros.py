"""Libros: recorre los objetos pequeños de caras planas (libros, cajas, cuadros chicos) uno por uno.

El turno de cada objeto dura `paso_s` (o avanza con cada golpe si `al_beat`): se enciende suave, una barra de luz le
recorre el lomo a lo largo de su eje mayor (rectángulo mínimo del contorno), su título se escribe en luz a lo largo
del lomo y un índice (lista numerada en el espacio libre de la escena) apunta a él con una línea y un punto que viaja.
Los demás quedan con el contorno tenue (`resto`). Títulos: `titulos` = 'Rayuela|Ficciones|…' por orden; '' = nombre.
"""

from __future__ import annotations

import math
import time

import cv2
import numpy as np

from .base import Effect, Canvas, bgr
from .. import ambiente as amb
from .. import scan_geom as sg

BOOK_KINDS = ("libro", "libros", "book", "books", "caja", "box", "cuaderno", "revista", "frame", "cuadro", "marco")
FONT = cv2.FONT_HERSHEY_DUPLEX


def _default(o, poly) -> bool:
    k = str(o.kind).lower()
    if k in BOOK_KINDS or any(w in str(o.name).lower() for w in BOOK_KINDS[:6]):
        return True
    return amb.rectness(poly) >= 0.78


def _smooth(e0, e1, x):
    t = min(1.0, max(0.0, (x - e0) / max(1e-6, e1 - e0)))
    return t * t * (3 - 2 * t)


class Libros(Effect):
    name = "libros"
    label = "Libros: lomos e índice"
    aliases = ("libros", "lomos", "índice", "indice", "ilumina los libros", "recorre los libros", "títulos de luz",
               "titulos de luz", "biblioteca", "uno por uno")
    description = ("Recorre los objetos pequeños de caras planas uno por uno: lomo iluminado con una barra que lo "
                   "recorre, título de luz escrito a lo largo del lomo e índice numerado que apunta a cada uno. "
                   "objetos ('' = libros/cajas o contornos casi rectangulares), titulos ('A|B|C', '' = nombres), "
                   "paso_s, al_beat, color, color_indice, resto (brillo de los demás), texto, indice, "
                   "indice_xy ([x, y] o '' = espacio libre), orden x|y|lista.")
    needs = ("contours", "beat")
    targets = ("*",)
    params = {"objetos": "", "titulos": "", "paso_s": 2.8, "al_beat": False, "color": [255, 214, 150],
              "color_indice": [150, 220, 255], "resto": 0.18, "texto": True, "indice": True, "indice_xy": "",
              "orden": "x", "capa": 7}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self.items: list[dict] = []
        self._key = None
        self.pulse = amb.Pulse()
        self.cur = 0
        self.t_turn = 0.0
        self.anchor = (0.0, 0.0)
        self.ms = 0.0

    def _titles(self, objs) -> list[str]:
        tt = [s.strip() for s in str(self.p["titulos"] or "").split("|")] if self.p["titulos"] else []
        return [(tt[i] if i < len(tt) and tt[i] else str(o.name)) for i, (o, _) in enumerate(objs)]

    def _prepare(self, scene, objs):
        W, H = amb.scene_wh(scene)
        order = str(self.p["orden"]).lower()
        if order in ("x", "y"):
            j = 0 if order == "x" else 1
            objs = sorted(objs, key=lambda op: float(op[1][:, j].mean()))
        items = []
        for (o, poly), title in zip(objs, self._titles(objs)):
            roi = sg.roi_of(poly, 6, (W, H))
            if roi is None:
                continue
            x0, y0, w, h = roi
            s = max(1, int(math.ceil(math.sqrt(w * h / 20000.0))))
            sw, sh = max(2, w // s), max(2, h // s)
            local = (poly - np.float32([x0, y0])) * np.float32([sw / w, sh / h])
            m = np.zeros((sh, sw), np.uint8)
            cv2.fillPoly(m, [np.round(local).astype(np.int32).reshape(-1, 1, 2)], 1)
            (cx, cy), (rw, rh), ang = cv2.minAreaRect(poly)
            if rw < rh:
                rw, rh, ang = rh, rw, ang + 90.0
            ang = ((ang + 90.0) % 180.0) - 90.0                      # texto legible: (−90, 90]
            ux, uy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            X = (np.arange(sw, dtype=np.float32)[None, :] + 0.5) * (w / sw) + x0
            Y = (np.arange(sh, dtype=np.float32)[:, None] + 0.5) * (h / sh) + y0
            a = (((X - cx) * ux + (Y - cy) * uy) / max(rw, 1.0) + 0.5).astype(np.float32)
            items.append({"o": o, "poly": poly, "roi": roi, "alpha": sg.feather(m > 0, max(1.0, 2.5 / s)).astype(np.float32),
                          "a": a, "c": (float(cx), float(cy)), "len": float(rw), "wid": float(rh), "ang": ang,
                          "title": title, "patch": self._text_patch(title, rw, rh)})
        occ = amb.mask_small((W, H), [p for _, p in objs] if objs else [], 8.0, 40)
        occ[:, :1] = occ[:, -1:] = occ[:1, :] = occ[-1:, :] = 1          # lejos de los bordes también
        free = cv2.distanceTransform((1 - occ).astype(np.uint8), cv2.DIST_L2, 3)
        yy, xx = np.unravel_index(int(np.argmax(free)), free.shape)
        lw, lh = 0.17 * W, 0.13 * H                                       # que la lista quepa entera
        self.anchor = (float(np.clip(xx * 8 + 4, 0.02 * W, W - lw)), float(np.clip(yy * 8 + 4, lh, H - lh)))
        xy = self.p.get("indice_xy")
        if isinstance(xy, (list, tuple)) and len(xy) >= 2:
            self.anchor = (float(xy[0]), float(xy[1]))
        return items

    @staticmethod
    def _text_patch(text: str, length: float, width: float) -> np.ndarray | None:
        if not text:
            return None
        th = max(10.0, min(width * 0.55, 60.0))
        scale = th / 22.0
        (tw, tth), base = cv2.getTextSize(text, FONT, scale, max(1, int(scale * 1.6)))
        if tw > length * 0.9:
            scale *= length * 0.9 / tw
            (tw, tth), base = cv2.getTextSize(text, FONT, scale, max(1, int(scale * 1.6)))
        img = np.zeros((tth + base + 6, tw + 6), np.uint8)
        cv2.putText(img, text, (3, tth + 3), FONT, scale, 255, max(1, int(scale * 1.6)), cv2.LINE_AA)
        return img

    def update(self, dt, scene):
        super().update(dt, scene)
        objs = amb.select(scene, self.p["objetos"], _default, target=self.target)
        if not objs and not self.p["objetos"] and self.target is None:
            objs = amb.select(scene)
        key = (amb.shape_key(objs), str(self.p["objetos"]), str(self.p["titulos"]), str(self.p["orden"]),
               str(self.p["indice_xy"]), amb.scene_wh(scene))
        if key != self._key:
            self.items, self._key = self._prepare(scene, objs), key
            self.cur, self.t_turn = 0, self.t
        self.pulse.update(dt, scene)
        paso = max(0.4, float(self.p["paso_s"]))
        if self.items and ((self.p["al_beat"] and self.pulse.hit()) or (not self.p["al_beat"] and self.t - self.t_turn >= paso)):
            self.cur = (self.cur + 1) % len(self.items)
            self.t_turn = self.t

    def _u(self) -> float:
        paso = max(0.4, float(self.p["paso_s"]))
        return min(1.0, (self.t - self.t_turn) / paso)

    def draw(self, canvas: Canvas):
        t0 = time.perf_counter()
        if not self.items:
            return
        col = np.float32(bgr(self.p["color"])) / 255.0
        ci = bgr(self.p["color_indice"])
        u = self._u()
        resto = float(self.p["resto"])
        thick = max(1, int(round(2 * canvas.px_per_mm)))
        for i, it in enumerate(self.items):                    # contornos tenues de los demás
            if i != self.cur and resto > 0:
                pts = amb.project_pts(canvas, it["poly"]).reshape(-1, 1, 2)
                cv2.polylines(canvas.img, [pts], True, tuple(int(c * 255 * resto) for c in col), thick, cv2.LINE_AA)
        it = self.items[self.cur]
        fill = _smooth(0.0, 0.18, u) * (1 - _smooth(0.86, 1.0, u))
        pos = u * 1.3 - 0.15
        band = np.exp(-((it["a"] - pos) * (1 / 0.07)) ** 2)
        v = (0.45 * fill + 0.9 * band * fill) * it["alpha"]
        x0, y0, w, h = it["roi"]
        amb.to_canvas(canvas, np.clip(v[..., None] * col * 255, 0, 255).astype(np.uint8), x0, y0, w, h)
        pts = amb.project_pts(canvas, it["poly"]).reshape(-1, 1, 2)
        cv2.polylines(canvas.img, [pts], True, tuple(int(c * 255 * (0.4 + 0.6 * fill)) for c in col), thick + 1, cv2.LINE_AA)
        if self.p["texto"] and it["patch"] is not None:
            self._draw_title(canvas, it, u, fill)
        if self.p["indice"]:
            self._draw_index(canvas, it, u, ci)
        dt_ms = (time.perf_counter() - t0) * 1000
        self.ms = 0.9 * self.ms + 0.1 * dt_ms if self.ms else dt_ms

    def _draw_title(self, canvas, it, u, fill):
        patch = it["patch"]
        reveal = int(patch.shape[1] * min(1.0, max(0.0, (u - 0.12) / 0.45)))
        if reveal <= 0:
            return
        p = patch.copy()
        p[:, reveal:] = 0
        k = canvas.px_per_mm
        cx, cy = canvas.project(*it["c"])
        ang = -it["ang"] if canvas.flip_y else it["ang"]
        size = int(math.hypot(*patch.shape) * k) + 4
        M = cv2.getRotationMatrix2D((patch.shape[1] / 2, patch.shape[0] / 2), -ang, k)
        M[0, 2] += size / 2 - patch.shape[1] / 2
        M[1, 2] += size / 2 - patch.shape[0] / 2
        rot = cv2.warpAffine(p, M, (size, size), flags=cv2.INTER_LINEAR)
        x0, y0 = cx - size // 2, cy - size // 2
        ih, iw = canvas.img.shape[:2]
        a0, b0, a1, b1 = max(0, x0), max(0, y0), min(iw, x0 + size), min(ih, y0 + size)
        if a1 <= a0 or b1 <= b0:
            return
        sub = cv2.convertScaleAbs(rot[b0 - y0:b1 - y0, a0 - x0:a1 - x0], alpha=float(fill))
        dst = canvas.img[b0:b1, a0:a1]
        cv2.max(dst, cv2.merge([sub, sub, sub]), dst=dst)

    def _draw_index(self, canvas, it, u, ci):
        k = canvas.px_per_mm
        ax, ay = canvas.project(*self.anchor)
        fs = max(0.35, 0.55 * k)
        lh = int(26 * k)
        n = len(self.items)
        first = max(0, min(self.cur - 4, n - 9))
        rows = list(range(first, min(n, first + 9)))
        y_top = ay - lh * len(rows) // 2
        row_y = {}
        for r, i in enumerate(rows):
            y = y_top + r * lh
            row_y[i] = y
            on = i == self.cur
            c = ci if on else tuple(int(v * 0.35) for v in ci)
            txt = f"{i + 1:>2}  {self.items[i]['title'][:22]}"
            cv2.putText(canvas.img, txt, (ax + int(14 * k), y + int(6 * k)), cv2.FONT_HERSHEY_SIMPLEX, fs * (1.15 if on else 1.0),
                        c, max(1, int(round(1.2 * k))), cv2.LINE_AA)
            if on:
                tri = np.int32([[ax, y - int(6 * k)], [ax, y + int(6 * k)], [ax + int(9 * k), y]])
                cv2.fillPoly(canvas.img, [tri], ci, cv2.LINE_AA)
        sx, sy = ax - int(8 * k), row_y.get(self.cur, ay)
        tx, ty = canvas.project(*it["c"])
        cv2.line(canvas.img, (sx, sy), (tx, ty), tuple(int(v * 0.3) for v in ci), max(1, int(round(k))), cv2.LINE_AA)
        f = _smooth(0.0, 0.5, u)
        px, py = int(sx + (tx - sx) * f), int(sy + (ty - sy) * f)
        cv2.circle(canvas.img, (px, py), max(2, int(5 * k)), ci, -1, cv2.LINE_AA)
        rr = int((10 + 6 * math.sin(self.t * 6)) * k)
        cv2.circle(canvas.img, (tx, ty), max(2, rr), ci, max(1, int(round(1.5 * k))), cv2.LINE_AA)

    def state(self) -> dict:
        cur = self.items[self.cur]["o"].id if self.items else None
        return {**super().state(), "ms": round(self.ms, 2), "actual": cur, "orden": [it["o"].id for it in self.items],
                "indice_xy": [round(v, 1) for v in self.anchor]}
