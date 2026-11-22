"""La hoja carta como lienzo: borde, grilla fina y un texto o imagen, rectificados a las 4 esquinas reales de la hoja.

La hoja (215,9 × 279,4 mm) está pegada sobre la pantalla; sus esquinas salen de la foto del escaneo
(`scan_geom.papel_quad`, el blanco dentro de su caja) y no del rectángulo de `segments.json`, que se comía la
pantalla entera. Se dibuja en un lienzo derecho de 2 px/mm y se lleva a la hoja con una homografía, así el texto se
lee derecho. Modos: `texto` (titulo + texto, '|' o salto de línea separa renglones), `estado` (hora, bpm, beat,
audio y lo que otros efectos dejen en `scene.hud`, p. ej. el puntaje del piano hero) e `imagen` (un PNG local).
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import cv2
import numpy as np

from .base import Effect, Canvas, bgr
from .. import scan_geom as sg

PX_MM = 2.0                       # resolución del lienzo derecho de la hoja


class PapelCanvas(Effect):
    name = "papel_canvas"
    label = "Hoja carta como lienzo (HUD)"
    aliases = ("hoja", "la hoja", "papel", "lienzo", "hud", "hoja carta", "pizarra", "escribe en la hoja", "puntaje")
    description = ("La hoja carta pegada sobre la pantalla como lienzo/HUD: borde, grilla fina y texto o imagen derechos "
                   "sobre sus 4 esquinas reales. modo: 'texto' (titulo, texto; '|' separa renglones), 'estado' (hora, bpm, "
                   "audio y lo que haya en scene.hud, como el puntaje del piano hero) o 'imagen' (ruta a un PNG). Params: "
                   "color, color_grilla, grilla_mm, brillo, borde.")
    needs = ("planes", "contours", "beat")
    targets = ("prop", "frame", "screen")
    default_target = "papel"
    params = {"modo": "estado", "titulo": "PLAYGROUND", "texto": "", "imagen": "", "color": [255, 255, 255],
              "color_grilla": [0, 200, 255], "grilla_mm": 10.0, "brillo": 0.9, "borde": True, "fondo": 0.06}

    def __init__(self, target, params=None, instance_id=""):
        super().__init__(target, params, instance_id)
        self._key = None
        self.prep = None
        self._page_key = None
        self.page = None
        self.lines: list[str] = []
        self._img_cache: tuple[str, np.ndarray | None] = ("", None)

    def _prepare(self, scene, obj):
        sd = sg.scene_scan(scene)
        dx, dy = sg.offset_of(obj)
        if obj.extra.get("quad"):                       # escena viva: las 4 esquinas de la hoja, ya con los ajustes
            quad = sg.order_quad(obj.extra["quad"][:4])
        else:                                           # escena de antes (sin la hoja fina): foto + ajuste del objeto
            q = sg.papel_quad(sd) if (sd is not None and obj.id == "papel") else None
            quad = sg.shifted(q, dx, dy) if q else sg.order_quad(obj.contour[:4])
        if not scene.projector_px:              # demo en mm (y hacia arriba): TL es el de y grande
            tl, tr, br, bl = quad
            quad = [bl, br, tr, tl]
        mm = obj.extra.get("mm") or [215.9, 279.4]
        pw, ph = int(round(mm[0] * PX_MM)), int(round(mm[1] * PX_MM))
        bounds = (int(scene.table_mm[0]), int(scene.table_mm[1])) if scene.projector_px else (10 ** 6, 10 ** 6)
        roi = sg.roi_of(quad, 2, bounds)
        if roi is None:
            return None
        x0, y0, w, h = roi
        dst = (np.asarray(quad, np.float32) - [x0, y0]).astype(np.float32)
        Hm = cv2.getPerspectiveTransform(np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]]), dst)
        alpha = sg.feather(sg.poly_mask((h, w), dst), 1.5)
        return {"roi": roi, "H": Hm, "page": (pw, ph), "alpha": alpha, "quad": quad, "mm": mm}

    # ------------------------------------------------------------------ contenido
    def _content(self, scene) -> list[str]:
        modo = str(self.p["modo"]).lower()
        if modo == "texto":
            raw = str(self.p.get("texto") or "")
            return [s.strip() for s in raw.replace("|", "\n").splitlines() if s.strip()]
        lines = [time.strftime("%H:%M:%S")]
        b = scene.beat
        lines.append(f"{b.get('bpm', 0):.0f} bpm" + ("  >" if b.get("playing") else "  ||"))
        a = getattr(scene, "audio", None) or {}
        if a.get("live"):
            lines.append("audio en vivo")
        for k, v in (getattr(scene, "hud", None) or {}).items():
            lines.append(f"{k}: {v}")
        extra = str(self.p.get("texto") or "")
        lines += [s.strip() for s in extra.replace("|", "\n").splitlines() if s.strip()]
        return lines

    def _image(self) -> np.ndarray | None:
        path = str(self.p.get("imagen") or "")
        if not path:
            return None
        if self._img_cache[0] != path:
            img = cv2.imread(path, cv2.IMREAD_COLOR) if Path(path).exists() else None
            self._img_cache = (path, img)
        return self._img_cache[1]

    def _render_page(self, pw: int, ph: int, lines: list[str], beat: float, audio: dict) -> np.ndarray:
        pg = np.zeros((ph, pw, 3), np.uint8)
        col, grid = bgr(self.p["color"]), bgr(self.p["color_grilla"])
        fondo = float(self.p.get("fondo", 0.0))
        if fondo > 0:
            pg[:] = tuple(int(c * fondo) for c in grid)
        step = float(self.p["grilla_mm"]) * PX_MM
        if step >= 4:
            gcol = tuple(int(c * 0.35) for c in grid)
            for x in np.arange(step, pw, step):
                cv2.line(pg, (int(x), 0), (int(x), ph), gcol, 1)
            for y in np.arange(step, ph, step):
                cv2.line(pg, (0, int(y)), (pw, int(y)), gcol, 1)
        if self.p["borde"]:
            ins = int(4 * PX_MM)
            k = 0.6 + 0.4 * beat
            cv2.rectangle(pg, (ins, ins), (pw - ins, ph - ins), tuple(int(c * k) for c in col), 3, cv2.LINE_AA)
        img = self._image() if str(self.p["modo"]).lower() == "imagen" else None
        y = int(26 * PX_MM)
        title = str(self.p.get("titulo") or "")
        if title:
            fs = min(1.6, 0.85 * pw / max(1, cv2.getTextSize(title, cv2.FONT_HERSHEY_DUPLEX, 1.0, 2)[0][0]))
            (tw, th), _ = cv2.getTextSize(title, cv2.FONT_HERSHEY_DUPLEX, fs, 2)
            cv2.putText(pg, title, ((pw - tw) // 2, y), cv2.FONT_HERSHEY_DUPLEX, fs, col, 2, cv2.LINE_AA)
            y += int(th + 10 * PX_MM)
        if img is not None:
            mw, mh = pw - int(24 * PX_MM), ph - y - int(14 * PX_MM)
            if mw > 10 and mh > 10:
                s = min(mw / img.shape[1], mh / img.shape[0])
                im = cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))), interpolation=cv2.INTER_AREA)
                ox, oy = (pw - im.shape[1]) // 2, y
                pg[oy:oy + im.shape[0], ox:ox + im.shape[1]] = im
        else:
            for ln in lines[:9]:
                fs = min(1.15, 0.9 * (pw - 24 * PX_MM) / max(1, cv2.getTextSize(ln, cv2.FONT_HERSHEY_SIMPLEX, 1.0, 2)[0][0]))
                (tw, th), _ = cv2.getTextSize(ln, cv2.FONT_HERSHEY_SIMPLEX, fs, 2)
                cv2.putText(pg, ln, ((pw - tw) // 2, y + th), cv2.FONT_HERSHEY_SIMPLEX, fs, col, 2, cv2.LINE_AA)
                y += int(th + 7 * PX_MM)
                if y > ph - 30 * PX_MM:
                    break
        if str(self.p["modo"]).lower() == "estado":       # medidores de audio al pie: bajo, medios, agudos
            names = ("bass", "mid", "high")
            bw_ = (pw - int(30 * PX_MM)) // len(names)
            for i, k in enumerate(names):
                v = max(0.0, min(1.0, float(audio.get(k, 0.0) or 0.0)))
                x = int(15 * PX_MM) + i * bw_
                y0, y1 = ph - int(26 * PX_MM), ph - int(12 * PX_MM)
                cv2.rectangle(pg, (x + 4, y0), (x + bw_ - 4, y1), tuple(int(c * 0.4) for c in grid), 1)
                cv2.rectangle(pg, (x + 4, y0), (x + 4 + int((bw_ - 8) * v), y1), grid, -1)
        return pg

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
            self.prep, self._key, self._page_key = self._prepare(scene, obj), key, None
        if self.prep is None:
            return
        b = scene.beat
        beat = 0.5 + 0.5 * math.cos(2 * math.pi * b.get("phase", 0.0)) if b.get("playing") else 0.5 + 0.5 * math.sin(self.t * 2)
        audio = getattr(scene, "audio", None) or {}
        lines = self._content(scene)
        pkey = (tuple(lines), round(beat, 1), tuple(round(float(audio.get(k, 0.0) or 0.0), 2) for k in ("bass", "mid", "high")),
                tuple(map(str, self.p.values())))
        if pkey != self._page_key:                      # se redibuja solo si cambió algo (hora, beat, audio, params)
            pw, ph = self.prep["page"]
            page = self._render_page(pw, ph, lines, beat, audio)
            x0, y0, w, h = self.prep["roi"]
            self.page = cv2.warpPerspective(page, self.prep["H"], (w, h), flags=cv2.INTER_AREA)
            self._page_key, self.lines = pkey, lines

    def draw(self, canvas: Canvas):
        if self.prep is None or self.page is None:
            return
        x0, y0, w, h = self.prep["roi"]
        layer = self.page.astype(np.float32) * (float(self.p["brillo"]) / 255.0)
        sg.blit(canvas, x0, y0, layer, self.prep["alpha"], mode="add")
