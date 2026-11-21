"""Ayudas comunes de los efectos de ambiente (pared_viva, plantas, libros, sombras_largas, lluvia_estrellas).

Nada está amarrado a un objeto por nombre: los efectos piden objetos de la escena viva con `select()` (lista de ids,
nombres, alias o kinds; `-id` saca uno; '' = una regla por forma) y trabajan con su polígono en unidades de la escena
(px del proyector en la escena del escaneo, mm en la demo). Lo pesado (máscaras, relieve, distancias) se calcula una
vez a baja resolución (`escala` unidades por píxel) y se cachea por la forma de la escena; por cuadro solo se anima
a baja resolución y se sube al lienzo con un único `resize` + suma.
"""

from __future__ import annotations

import hashlib
import math
import re

import cv2
import numpy as np

from . import scan_geom as sg
from .audio.features import audio_level

SKIP_KINDS = ("table",)
BIG_FRAC = 0.45                     # un "objeto" que tapa casi media escena es un plano (pared, mesa), no un objeto

PALETTES = {                        # RGB, de oscuro a claro
    "oceano": [[4, 12, 40], [0, 90, 150], [40, 200, 230], [210, 250, 255]],
    "aurora": [[10, 0, 40], [40, 200, 140], [120, 80, 255], [230, 255, 240]],
    "fuego": [[30, 0, 0], [200, 30, 0], [255, 150, 0], [255, 240, 180]],
    "neon": [[30, 0, 60], [255, 0, 150], [0, 220, 255], [255, 255, 120]],
    "bosque": [[0, 20, 10], [20, 120, 50], [150, 220, 80], [240, 255, 200]],
    "atardecer": [[30, 0, 50], [200, 40, 90], [255, 140, 60], [255, 230, 170]],
    "oro": [[20, 10, 0], [140, 80, 10], [255, 190, 60], [255, 245, 210]],
}


# ---------------------------------------------------------------------- objetos

def poly_of(o) -> np.ndarray | None:
    """Polígono (n, 2) float32 del objeto en unidades de la escena: contorno fino, contorno, o el de P5."""
    ex = getattr(o, "extra", None) or {}
    for p in (ex.get("outline"), getattr(o, "contour", None), ex.get("projector_contour_px")):
        if p is not None and len(p) >= 3:
            return np.asarray(p, np.float32).reshape(-1, 2)
    return None


def scene_wh(scene) -> tuple[int, int]:
    w, h = scene.table_mm
    return max(8, int(round(w))), max(8, int(round(h)))


def tokens(spec) -> list[str]:
    if isinstance(spec, (list, tuple)):
        return [str(s).strip().lower() for s in spec if str(s).strip()]
    return [t.strip().lower() for t in re.split(r"[,;|]", str(spec or "")) if t.strip()]


def _matches(o, tok: str) -> bool:
    al = [str(a).lower() for a in (o.extra or {}).get("aliases", [])]
    return tok == o.id.lower() or tok == str(o.kind).lower() or tok in str(o.name).lower() or tok in al


def solidity(poly: np.ndarray) -> float:
    a = abs(cv2.contourArea(poly))
    h = abs(cv2.contourArea(cv2.convexHull(poly)))
    return a / h if h > 1e-6 else 1.0


def rectness(poly: np.ndarray) -> float:
    (_, _), (w, h), _ = cv2.minAreaRect(poly)
    return abs(cv2.contourArea(poly)) / max(w * h, 1e-6)


def select(scene, spec="", default=None, target=None) -> list[tuple[object, np.ndarray]]:
    """[(objeto, polígono)] de la escena viva, sin la mesa ni planos enormes. `target` (spawn sobre un objeto) gana.
    spec: 'planta, libro, e3' (ids, nombres, alias o kinds); '-ohm' saca uno; '' = default(o, poly) o todos."""
    W, H = scene_wh(scene)
    toks = tokens(spec)
    inc = [t for t in toks if not t.startswith("-")]
    exc = [t[1:] for t in toks if t.startswith("-")]
    out = []
    for o in scene.objects.values():
        if target is not None and o.id != target.id:
            continue
        poly = poly_of(o)
        if poly is None:
            continue
        area = abs(cv2.contourArea(poly))
        if target is None and (o.kind in SKIP_KINDS or area < 20 or area > BIG_FRAC * W * H):
            continue
        if any(_matches(o, t) for t in exc):
            continue
        if target is not None or (inc and any(_matches(o, t) for t in inc)) or (not inc and (default is None or default(o, poly))):
            out.append((o, poly))
    return out


def shape_key(objs) -> tuple:
    return tuple((o.id, len(p), round(float(p[0, 0]), 1), round(float(p[0, 1]), 1), round(float(p[:, 0].sum()), 0))
                 for o, p in objs)


def mask_small(wh, polys, scale: float, dilate_units: float = 0.0) -> np.ndarray:
    """Máscara uint8 0/1 a baja resolución (filas = y de la escena hacia abajo)."""
    W, H = wh
    w, h = max(1, int(math.ceil(W / scale))), max(1, int(math.ceil(H / scale)))
    m = np.zeros((h, w), np.uint8)
    for p in polys:
        if p is not None and len(p) >= 3:
            cv2.fillPoly(m, [np.round(np.asarray(p, np.float32) / scale).astype(np.int32).reshape(-1, 1, 2)], 1)
    d = int(round(dilate_units / scale))
    if d > 0:
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1)))
    return m


def color_of(i: int, oid: str, colores) -> tuple[float, float, float]:
    """Color BGR 0..1 de un objeto: de la lista `colores` (RGB) por orden, o uno estable por id."""
    cols = colores if isinstance(colores, (list, tuple)) and colores and isinstance(colores[0], (list, tuple)) else None
    if cols:
        r, g, b = cols[i % len(cols)][:3]
    else:
        hue = int(hashlib.md5(oid.encode()).hexdigest()[:4], 16) % 180
        hsv = np.uint8([[[hue, 200, 255]]])
        b, g, r = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    return (float(b) / 255.0, float(g) / 255.0, float(r) / 255.0)


# ---------------------------------------------------------------------- relieve

_hcache: dict = {}


def height01(scene, scale: float) -> tuple[np.ndarray, bool]:
    """Relieve 0..1 a baja resolución (filas = y hacia abajo) y si viene del escaneo (True) o es sintético (False).
    Escaneo: height_proj robusto (mediana, percentiles), suavizado. Sin escaneo: cada objeto sube a su altura de
    bbox (z máx), con bordes suaves: así sombras y ondas funcionan con cualquier escena."""
    W, H = scene_wh(scene)
    sd = sg.scene_scan(scene)
    w, h = max(1, int(math.ceil(W / scale))), max(1, int(math.ceil(H / scale)))
    if sd is not None and sd.height is not None:
        key = ("scan", id(sd), sd.memo.get("_stamp"), scale, (w, h))
        if key not in _hcache:
            z = sd.height.astype(np.float32)
            z = cv2.resize(z, (w, h), interpolation=cv2.INTER_AREA)
            z = cv2.medianBlur(z, 5)
            lo, mid, hi = np.percentile(z, [5, 50, 99.5])
            z = np.clip((z - mid) / max(hi - mid, 1e-3), 0, 1)
            z = cv2.GaussianBlur(z, (0, 0), 1.2)
            _hcache.clear()
            _hcache[key] = z.astype(np.float32)
        return _hcache[key], True
    objs = select(scene)
    key = ("synth", shape_key(objs), scale, (w, h))
    if key not in _hcache:
        z = np.zeros((h, w), np.float32)
        top = max([max(1.0, float(o.bbox[5]) - float(o.bbox[2])) for o, _ in objs] or [1.0])
        for o, p in objs:
            m = mask_small((W, H), [p], scale)
            z = np.maximum(z, m.astype(np.float32) * max(0.15, (float(o.bbox[5]) - float(o.bbox[2])) / top))
        z = cv2.GaussianBlur(z, (0, 0), 1.0)
        _hcache.clear()
        _hcache[key] = z
    return _hcache[key], False


# ---------------------------------------------------------------------- color y salida

def palette_lut(spec, fallback: str = "oceano") -> np.ndarray:
    """LUT (256, 3) BGR uint8 desde un nombre de PALETTES o una lista de colores RGB (2 o más)."""
    stops = PALETTES.get(str(spec).lower()) if isinstance(spec, str) else spec
    if not stops or len(stops) < 2:
        stops = PALETTES[fallback]
    st = np.asarray(stops, np.float32)[:, :3][:, ::-1]
    xs = np.linspace(0, 1, len(st))
    t = np.linspace(0, 1, 256)
    lut = np.stack([np.interp(t, xs, st[:, c]) for c in range(3)], 1)
    lut *= np.clip(np.arange(256, dtype=np.float32) / 20.0, 0, 1)[:, None]      # 0 = negro: sin luz fuera de la zona
    return lut.clip(0, 255).astype(np.uint8)


def to_canvas(canvas, layer: np.ndarray, x0: float, y0: float, w_units: float, h_units: float) -> None:
    """Suma (luz) una capa uint8 BGR que cubre el rectángulo de la escena (x0, y0, w, h) en unidades: un resize
    al tamaño proyectado y un cv2.add. Sirve igual para el lienzo real (1 unidad = 1 px) y para la demo en mm."""
    (cx0, cy0), (cx1, cy1) = canvas.project(x0, y0), canvas.project(x0 + w_units, y0 + h_units)
    if cy1 < cy0:
        cy0, cy1 = cy1, cy0
        layer = layer[::-1]
    dw, dh = cx1 - cx0, cy1 - cy0
    if dw <= 0 or dh <= 0 or layer.size == 0:
        return
    if (layer.shape[1], layer.shape[0]) != (dw, dh):
        layer = cv2.resize(np.ascontiguousarray(layer), (dw, dh), interpolation=cv2.INTER_LINEAR)
    ih, iw = canvas.img.shape[:2]
    ax0, ay0, ax1, ay1 = max(0, cx0), max(0, cy0), min(iw, cx0 + dw), min(ih, cy0 + dh)
    if ax1 <= ax0 or ay1 <= ay0:
        return
    dst = canvas.img[ay0:ay1, ax0:ax1]
    cv2.add(dst, layer[ay0 - cy0:ay1 - cy0, ax0 - cx0:ax1 - cx0], dst=dst)


def project_pts(canvas, pts: np.ndarray) -> np.ndarray:
    """Puntos (n, 2) de la escena → px del lienzo (int32), vectorizado (misma cuenta que Canvas.project)."""
    p = np.asarray(pts, np.float32).reshape(-1, 2)
    x = canvas.origin_px[0] + p[:, 0] * canvas.px_per_mm
    y = canvas.origin_px[1] + (-p[:, 1] if canvas.flip_y else p[:, 1]) * canvas.px_per_mm
    if canvas.flip_y:
        y = y + canvas.img.shape[0]
    return np.round(np.stack([x, y], 1)).astype(np.int32)


def up_sign(scene) -> float:
    """Hacia dónde es 'arriba' en y de la escena: −1 en px del proyector (y hacia abajo), +1 en la demo en mm."""
    return -1.0 if getattr(scene, "projector_px", False) else 1.0


# ---------------------------------------------------------------------- ritmo

class Pulse:
    """Golpes del audio vivo (onsets) o, sin audio, del beat del playground. `hit()` True en el cuadro del golpe;
    `env` decae 0..1 tras cada golpe; `level` es la banda pedida (0 sin audio)."""

    def __init__(self):
        self.last_onsets = None
        self.last_phase = 0.0
        self.env = 0.0
        self.level = 0.0
        self.live = False
        self._hit = False

    def update(self, dt: float, scene, band: str = "bass", gain: float = 1.0) -> None:
        a = getattr(scene, "audio", None) or {}
        self.live = bool(a.get("live"))
        self.level = audio_level(a, band, gain) if self.live else 0.0
        self._hit = False
        if self.live:
            on = int(a.get("onsets", 0) or 0)
            if self.last_onsets is not None and on > self.last_onsets:
                self._hit = True
            self.last_onsets = on
        else:
            b = getattr(scene, "beat", None) or {}
            ph = float(b.get("phase", 0.0))
            if b.get("playing") and ph < self.last_phase:
                self._hit = True
            self.last_phase = ph
        self.env = 1.0 if self._hit else self.env * math.exp(-dt / 0.18)

    def hit(self) -> bool:
        return self._hit
