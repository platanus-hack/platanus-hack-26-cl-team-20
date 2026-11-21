"""Ayudas comunes de los efectos de cuadros (cuadro_portal, cuadro_vivo, marco_respira, parallax).

Todo sale de la escena viva, sin nombres: el polígono del objeto (`extra.outline` fino si lo hay, si no `contour`,
o `extra.projector_contour_px` de P5), su recorte de la foto del escaneo (`scene_pov.png`, 1 px = 1 px del
proyector) y mapas por píxel que los efectos reutilizan cada cuadro:

- `mask` / `alpha`: el objeto (sin lo que tenga delante: objetos más chicos que lo tapan, p. ej. una planta o el
  cocodrilo) y su borde suave hacia adentro.
- `rn` / `theta`: radio normalizado al polígono (0 = centro, 1 = borde, en cualquier dirección) y ángulo. Sirven para
  un iris o un túnel con la forma del cuadro, y para luces que recorren el contorno.

`Patch.key()` cambia si cambia el objeto (otro contorno, nudge, otro escaneo): los efectos re-preparan solo entonces.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import scan_geom as sg

DEFAULT_KINDS = (("frame",), ("plane", "screen"), ("prop",))     # sin objeto: el más grande de la primera clase que haya
NO_EXCLUDE = ("table",)


def poly_of(obj) -> list[list[float]] | None:
    """Polígono del objeto en unidades de la escena: contorno fino de la foto, contorno, o el del proyector de P5."""
    if obj is None:
        return None
    ex = getattr(obj, "extra", None) or {}
    for cand in (ex.get("outline"), obj.contour, ex.get("projector_contour_px")):
        if cand is not None and len(cand) >= 3:
            return [[float(p[0]), float(p[1])] for p in cand]
    return None


def area(poly) -> float:
    return abs(float(cv2.contourArea(np.asarray(poly, np.float32).reshape(-1, 1, 2)))) if poly is not None else 0.0


def pick(scene, obj=None):
    """El objeto del efecto: el pedido (vivo, por id) o, sin objeto, el cuadro más grande de la escena."""
    if obj is not None:
        return scene.objects.get(obj.id, obj)
    for kinds in DEFAULT_KINDS:
        cands = [o for o in scene.objects.values() if o.kind in kinds and poly_of(o) is not None]
        if cands:
            return max(cands, key=lambda o: area(poly_of(o)))
    return None


def obj_key(scene, obj, *more) -> tuple:
    poly = poly_of(obj)
    first = tuple(round(v, 1) for v in poly[0]) if poly else ()
    return (id(sg.scene_scan(scene)), obj.id, tuple(sg.offset_of(obj)), len(poly or ()), first,
            round(area(poly), 0), *more)


def _resample(poly: np.ndarray, step: float = 1.0) -> np.ndarray:
    pts = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        k = max(1, int(math.ceil(np.hypot(*(b - a)) / step)))
        t = np.arange(k, dtype=np.float64)[:, None] / k
        pts.append(a + (b - a) * t)
    return np.vstack(pts)


def _synthetic_texture(obj, w: int, h: int, seed: int) -> np.ndarray:
    """Sin foto (escena demo o sintética): el albedo del objeto con un degradé y manchas suaves, para que se vea algo."""
    alb = np.array((obj.albedo or [0.6, 0.6, 0.6])[:3][::-1], np.float32)
    rng = np.random.default_rng(seed)
    small = rng.random((max(2, h // 24), max(2, w // 24), 3)).astype(np.float32)
    blobs = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    yy = np.linspace(0.65, 1.0, h, dtype=np.float32)[:, None, None]
    tex = np.clip(alb * yy * (0.7 + 0.6 * blobs), 0, 1)
    return (tex * 255).astype(np.uint8)


@dataclass
class Patch:
    obj_id: str
    roi: tuple[int, int, int, int]          # x0, y0, w, h en unidades de la escena (px del proyector en el escaneo)
    poly: np.ndarray                        # polígono local (relativo a x0, y0)
    mask: np.ndarray                        # bool (h, w): el objeto sin lo que tiene delante
    alpha: np.ndarray                       # float32 0..1, borde suave hacia adentro
    alpha8: np.ndarray                      # uint8 (h, w, 3) del mismo alfa (para cv2.multiply)
    tex8: np.ndarray                        # BGR uint8: la foto del objeto (o una textura sintética)
    center: tuple[float, float]
    rn: np.ndarray                          # radio normalizado al polígono (1 = borde)
    theta: np.ndarray                       # ángulo alrededor del centro (-pi..pi)
    from_photo: bool
    extra: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.roi[3], self.roi[2]


def prepare(scene, obj, *, feather: float = 6.0, pad: float = 0.0, exclude: bool = True) -> Patch | None:
    """Recorte del objeto con su foto, máscara y mapas polares. `pad` = margen extra (px) para halos por fuera."""
    poly = poly_of(obj)
    if poly is None:
        return None
    P = np.asarray(poly, float)
    sd = sg.scene_scan(scene)
    dx, dy = sg.offset_of(obj)
    margin = math.ceil(0.03 * float(max(np.ptp(P, axis=0)))) + 4 + int(pad)
    size = (int(scene.table_mm[0]), int(scene.table_mm[1])) if getattr(scene, "projector_px", False) else (10 ** 6, 10 ** 6)
    roi = sg.roi_of(P, margin, size)
    if roi is None:
        return None
    x0, y0, w, h = roi
    local = P - [x0, y0]
    full = sg.poly_mask((h, w), local)
    if full.sum() < 30:
        return None
    mask = full.copy()
    own = area(P)
    if exclude:                                  # lo que está delante (más chico y encima) no es el cuadro
        for o in scene.objects.values():
            if o.id == obj.id or o.kind in NO_EXCLUDE or str((o.extra or {}).get("placement", "")).startswith("provisional"):
                continue
            q = poly_of(o)
            if q is None or area(q) >= 0.7 * own:
                continue
            Q = np.asarray(q, float) - [x0, y0]
            if Q[:, 0].max() < 0 or Q[:, 1].max() < 0 or Q[:, 0].min() > w or Q[:, 1].min() > h:
                continue
            mask &= ~sg.poly_mask((h, w), Q, dilate=5)
        if mask.sum() < 0.25 * full.sum():      # casi todo tapado: mejor el contorno entero
            mask = full
    tex8 = sg.texture(sd, x0, y0, w, h, dx, dy) if sd is not None else None
    from_photo = tex8 is not None
    if tex8 is None:
        tex8 = _synthetic_texture(obj, w, h, seed=sum(map(ord, obj.id)))
    tex8 = np.ascontiguousarray(tex8)
    alpha = sg.feather(mask, feather)
    a8 = (alpha * 255).astype(np.uint8)
    m = cv2.moments(full.astype(np.uint8), True)
    cx, cy = (m["m10"] / m["m00"], m["m01"] / m["m00"]) if m["m00"] > 0 else (w / 2, h / 2)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ddx, ddy = xx - np.float32(cx), yy - np.float32(cy)
    r = np.sqrt(ddx * ddx + ddy * ddy)
    theta = np.arctan2(ddy, ddx)
    # R(θ): distancia del centro al borde del polígono en cada dirección (720 sectores)
    nb = 720
    b = _resample(local, 1.0)
    bt = np.arctan2(b[:, 1] - cy, b[:, 0] - cx)
    br = np.hypot(b[:, 0] - cx, b[:, 1] - cy)
    R = np.zeros(nb)
    np.maximum.at(R, ((bt + np.pi) / (2 * np.pi) * nb).astype(int) % nb, br)
    good = R > 0
    if good.sum() >= 2:
        ang = np.arange(nb)
        R = np.interp(ang, ang[good], R[good], period=nb)
    else:
        R[:] = max(1.0, float(r[full].max()) if full.any() else 1.0)
    idx = ((theta + np.pi) / (2 * np.pi) * nb).astype(np.int32) % nb
    rn = (r / np.maximum(R[idx], 1.0)).astype(np.float32)
    return Patch(obj.id, roi, local, mask, alpha, cv2.merge([a8, a8, a8]), tex8, (float(cx), float(cy)), rn,
                 theta.astype(np.float32), from_photo, {"R": R.astype(np.float32), "full": full})


def rgb01(c) -> np.ndarray:
    """[r, g, b] 0-255 de los params → BGR float32 0..1."""
    r, g, b = (float(max(0, min(255, v))) for v in list(c)[:3])
    return np.array([b, g, r], np.float32) / 255.0


def tint(gray8: np.ndarray, color_bgr01: np.ndarray, k: float = 1.0) -> np.ndarray:
    """Imagen gris uint8 → BGR uint8 teñida (color × k), con OpenCV (rápido)."""
    return cv2.merge([cv2.convertScaleAbs(gray8, alpha=float(c) * k) for c in color_bgr01])


def beat_env(scene, t: float, speed: float = 1.0) -> float:
    """0..1: envolvente del beat del runtime si suena (1 en el golpe y cae), si no una respiración lenta."""
    b = getattr(scene, "beat", None) or {}
    if b.get("playing"):
        return float(math.exp(-4.0 * float(b.get("phase", 0.0))))
    return 0.5 + 0.5 * math.sin(2 * math.pi * 0.25 * speed * t)


def touches_inside(scene, patch: Patch, since_seq: int) -> tuple[list[dict], int]:
    """Toques nuevos (down) que caen dentro del objeto desde `since_seq`. Devuelve (toques, último seq)."""
    try:
        from .touchsynth.registry import touch_state
    except Exception:  # noqa: BLE001
        return [], since_seq
    ts = touch_state(scene)
    out = []
    x0, y0, w, h = patch.roi
    for ev in ts.log[-200:]:
        if ev.get("seq", 0) <= since_seq or ev.get("state") != "down":
            continue
        lx, ly = float(ev.get("x", -1)) - x0, float(ev.get("y", -1)) - y0
        if 0 <= lx < w and 0 <= ly < h and patch.extra["full"][int(ly), int(lx)]:
            out.append(ev)
    return out, ts.seq


def pointer(scene) -> tuple[float, float] | None:
    """Posición (px del proyector) de la primera mano sobre la mesa, si no del último toque vivo; None si no hay."""
    try:
        from .touchsynth.registry import touch_state
    except Exception:  # noqa: BLE001
        return None
    ts = touch_state(scene)
    for src in (ts.hands, ts.touches):
        for v in src.values():
            try:
                return float(v["x"]), float(v["y"])
            except (KeyError, TypeError, ValueError):
                continue
    return None


def blit(canvas, patch: Patch, layer8: np.ndarray, mode: str = "add") -> None:
    x0, y0, _, _ = patch.roi
    sg.blit(canvas, x0, y0, layer8, None, mode=mode)


def polar_grid(patch: Patch, scale: float) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """(rn, theta, (ws, hs)) recalculados a otra resolución (para efectos que calculan a baja resolución y escalan)."""
    x0, y0, w, h = patch.roi
    ws, hs = max(2, int(round(w * scale))), max(2, int(round(h * scale)))
    xs = (np.arange(ws, dtype=np.float32) + 0.5) * (w / ws) - 0.5
    ys = (np.arange(hs, dtype=np.float32) + 0.5) * (h / hs) - 0.5
    ddx, ddy = xs[None, :] - np.float32(patch.center[0]), ys[:, None] - np.float32(patch.center[1])
    r = np.sqrt(ddx * ddx + ddy * ddy)
    th = np.arctan2(np.broadcast_to(ddy, r.shape), np.broadcast_to(ddx, r.shape)).astype(np.float32)
    R = patch.extra["R"]
    nb = len(R)
    idx = ((th + np.pi) / (2 * np.pi) * nb).astype(np.int32) % nb
    return (r / np.maximum(R[idx], 1.0)).astype(np.float32), th, (ws, hs)


def small(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)
