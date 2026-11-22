"""Reiluminación de personas con la normal de DAViD: focos que salen de la punta de los dedos.

Todo en px de la imagen de la cámara, en un marco 3D x a la derecha, y hacia abajo, z hacia la cámara. La superficie
de la persona es P = (x, y, z(x, y)) con z sacada de la profundidad relativa (o un plano si no hay); cada foco está en
la punta de un índice, un poco delante, y apunta hacia donde apunta el dedo (cono suave). Sombreado Lambert envolvente
+ Blinn en luz lineal; sin sombras proyectadas (v1).

    lights = finger_lights(hands, z, ...)                # dedos -> focos
    shade = light_map(normal_img, z, lights, ...)        # (h, w, 3) luz que llega a cada punto (lineal, BGR)
    out = compose_screen(frame, alpha, shade, lights)    # salida en pantalla: la cámara reiluminada (como el reel)
    proj = compose_projector(alpha, shade)               # salida proyectada: solo la luz, donde pega en la persona

Costo (M4, 960x540, 2 focos): ~25 ms con `scale=0.5` (la luz se calcula a media resolución y se sube; la textura
de la cámara queda a resolución completa).
"""

from __future__ import annotations

import math
from functools import lru_cache

import cv2
import numpy as np

INDEX_PIP, INDEX_DIP, INDEX_TIP = 6, 7, 8


def to_image_frame(normal: np.ndarray) -> np.ndarray:
    """Normal de DAViD (x derecha, y arriba, z a la cámara) -> marco de la imagen (x derecha, y abajo, z a la cámara)."""
    n = normal.astype(np.float32, copy=True)
    n[..., 1] *= -1
    return n


def depth_to_z(depth: np.ndarray | None, alpha: np.ndarray, shape, relief_px: float) -> np.ndarray:
    """Profundidad relativa (mayor = más cerca) -> z en px (0 = mediana de la persona, + hacia la cámara).
    Sin profundidad: plano z=0."""
    h, w = shape[:2]
    if depth is None:
        return np.zeros((h, w), np.float32)
    m = alpha > 0.5
    if m.sum() < 50:
        return np.zeros((h, w), np.float32)
    d = depth[m]
    lo, med, hi = np.percentile(d, [5, 50, 95])
    z = np.clip((depth - med) / max(hi - lo, 1e-6), -1.5, 1.5) * relief_px
    z = np.where(alpha > 0.05, z, -relief_px).astype(np.float32)      # fondo: detrás de la persona
    return cv2.GaussianBlur(z, (0, 0), max(1.0, w / 200))             # quita el escalonado fp16


def finger_lights(hands, z_map: np.ndarray, *, color=(1.0, 0.85, 0.6), intensity: float = 1.0,
                  lift_px: float = 40.0, cone_deg: float = 140.0, radius_px: float = 260.0, which=(INDEX_TIP,)):
    """Manos (landmarks MediaPipe en px; z en px, negativa = más cerca) -> focos.

    Cada foco: pos (x, y, z), dir unitaria (la del dedo: falange media -> punta), color BGR lineal 0..1."""
    h, w = z_map.shape
    out = []
    for hd in hands:
        lm = np.asarray(hd["landmarks"], np.float32)
        for tip in which:
            p = lm[tip].copy(); q = lm[tip - 2]                   # punta y PIP del mismo dedo
            x, y = float(np.clip(p[0], 0, w - 1)), float(np.clip(p[1], 0, h - 1))
            zs = float(z_map[int(y), int(x)])
            d = p - q
            d[2] = -(p[2] - q[2])                                 # a nuestro marco: + hacia la cámara
            n = float(np.linalg.norm(d))
            dirv = (d / n) if n > 1e-3 else np.array([0, 0, -1], np.float32)
            pos = np.array([x, y, zs + lift_px], np.float32)
            out.append({"pos": pos, "dir": dirv.astype(np.float32), "color": np.asarray(color, np.float32),
                        "intensity": float(intensity), "cone_deg": float(cone_deg), "radius_px": float(radius_px),
                        "hand": hd.get("handed", "?")})
    return out


@lru_cache(maxsize=8)
def _grid(h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    return xs, ys


def _light_map_full(n: np.ndarray, z: np.ndarray, lights, spec: float, shininess: float, wrap: float,
                    px_scale: float) -> np.ndarray:
    h, w = z.shape
    xs, ys = _grid(h, w)
    acc = np.zeros((h, w, 3), np.float32)
    nx, ny, nz = n[..., 0], n[..., 1], n[..., 2]
    for L in lights:
        lx, ly, lz = (float(v) * px_scale for v in L["pos"])
        vx = lx - xs; vy = ly - ys; vz = lz - z                   # punto -> luz
        dist2 = vx * vx + vy * vy + vz * vz + 1.0
        inv = 1.0 / np.sqrt(dist2)
        vx *= inv; vy *= inv; vz *= inv
        ndl = nx * vx + ny * vy + nz * vz
        diff = np.clip((ndl + wrap) * (1.0 / (1.0 + wrap)), 0, 1)   # envolvente: no corta seco (piel, tela)
        diff *= np.sqrt(diff)
        d = L["dir"]
        k = diff
        if L["cone_deg"] < 359:                                   # 360 = omni; 240 = todo menos detrás del dedo
            cos_a = -(vx * d[0] + vy * d[1] + vz * d[2])           # eje del foco vs. luz -> punto
            half = math.radians(max(L["cone_deg"], 5.0) / 2)
            e0, e1 = math.cos(half), math.cos(min(half * 0.4, math.radians(60)))
            t = np.clip((cos_a - e0) * (1.0 / max(e1 - e0, 1e-6)), 0, 1)
            k = k * (t * t * (3 - 2 * t))
        r = L["radius_px"] * px_scale
        att = 1.0 / (1.0 + dist2 * (1.0 / (r * r)))
        if spec > 0:
            hz = vz + 1.0                                         # Blinn con el observador en +z
            hn = 1.0 / np.sqrt(vx * vx + vy * vy + hz * hz + 1e-6)
            ndh = np.clip((nx * vx + ny * vy + nz * hz) * hn, 0, 1)
            sp = spec * np.power(ndh, shininess) * (ndl > 0)
            k = k + sp * (k > 0)
        k = k * att * L["intensity"]
        acc += k[..., None] * L["color"][None, None, :]
    return acc


def light_map(normal_img: np.ndarray, z: np.ndarray, lights, *, spec: float = 0.35, shininess: float = 24.0,
              wrap: float = 0.35, scale: float = 0.5) -> np.ndarray:
    """Luz que llega a cada píxel (h, w, 3) lineal: sum color·I·(difusa + especular)·cono·caída.
    `scale` < 1 calcula a menor resolución y sube (la luz es suave; la textura sigue a resolución completa)."""
    h, w = z.shape
    if not lights:
        return np.zeros((h, w, 3), np.float32)
    if scale >= 0.999:
        return _light_map_full(normal_img, z, lights, spec, shininess, wrap, 1.0)
    sw, sh = max(8, int(w * scale)), max(8, int(h * scale))
    n_s = cv2.resize(normal_img, (sw, sh), interpolation=cv2.INTER_AREA)
    n_s /= np.linalg.norm(n_s, axis=-1, keepdims=True) + 1e-6
    z_s = cv2.resize(z, (sw, sh), interpolation=cv2.INTER_AREA) * (sw / w)
    acc = _light_map_full(n_s, z_s, lights, spec, shininess, wrap, sw / w)
    return cv2.resize(acc, (w, h), interpolation=cv2.INTER_LINEAR)


_LIN_LUT = ((np.arange(256, dtype=np.float32) / 255.0) ** 2).astype(np.float32)
_SRGB_N = 4096
_SRGB_LUT = (np.sqrt(np.linspace(0, 1, _SRGB_N, dtype=np.float32)) * 255 + 0.5).astype(np.uint8)


def _lin(img_u8: np.ndarray) -> np.ndarray:
    return _LIN_LUT[img_u8]                                       # gamma 2 (rápido y suficiente)


def _srgb(x: np.ndarray) -> np.ndarray:
    idx = np.clip(x * (_SRGB_N - 1), 0, _SRGB_N - 1).astype(np.int32)
    return _SRGB_LUT[idx]


def _tonemap(x: np.ndarray) -> np.ndarray:
    return x / (1 + x * 0.25)                                     # hombro suave: el destello no quema todo


def glow_sprites(shape, lights, size_px: float = 14.0) -> np.ndarray:
    """Destello en la punta de cada dedo: núcleo chico + halo suave (aditivo, lineal). Se pinta a 1/4 y se sube."""
    h, w = shape[:2]
    q = 4
    core = np.zeros((h // q + 1, w // q + 1, 3), np.float32)
    s = size_px / q
    for L in lights:
        c = tuple(float(v) * L["intensity"] for v in L["color"])
        cv2.circle(core, (int(L["pos"][0] / q), int(L["pos"][1] / q)), max(1, int(round(s * 0.3))), c, -1, cv2.LINE_AA)
    g = cv2.GaussianBlur(core, (0, 0), max(0.6, s * 0.35)) * 3.0 + cv2.GaussianBlur(core, (0, 0), s * 1.4) * 1.2
    return cv2.resize(g, (w, h), interpolation=cv2.INTER_LINEAR)


def compose_screen(frame_bgr: np.ndarray, alpha: np.ndarray, shade: np.ndarray, lights, *, ambient: float = 0.05,
                   background: float = 0.025, exposure: float = 2.5, glow: float = 1.0) -> np.ndarray:
    """La cámara reiluminada: la persona con ambiente + focos, el fondo casi negro, destellos en los dedos."""
    lin = _lin(frame_bgr)
    a = alpha[..., None]
    out = lin * ((ambient + shade * exposure) * a + background * (1 - a))
    if glow > 0 and lights:
        out += glow_sprites(frame_bgr.shape, lights, size_px=max(8.0, frame_bgr.shape[1] / 70)) * glow
    return _srgb(_tonemap(out))


def compose_projector(alpha: np.ndarray, shade: np.ndarray, *, exposure: float = 2.5, tint=None) -> np.ndarray:
    """Lo que pintaría el proyector sobre la persona: solo la luz de los focos donde la normal dice que pega."""
    out = shade * exposure * alpha[..., None]
    if tint is not None:
        out *= np.asarray(tint, np.float32)[None, None, :]
    return _srgb(_tonemap(out))


def beat_gain(beat: dict | None, amount: float) -> float:
    """1 + amount·pulso que cae en cada negra (fase 0..1 del beat del playground)."""
    if not beat or amount <= 0:
        return 1.0
    ph = float(beat.get("phase", 0.0)) % 1.0
    return 1.0 + amount * math.exp(-ph * 6.0)
