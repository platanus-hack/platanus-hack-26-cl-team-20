"""Linterna: la mano es una fuente de luz que ilumina el mundo 3D reconstruido.

El relieve sale de `height_proj.npy` del escaneo (altura relativa por píxel del proyector). Se baja de resolución
(`escala`), se suaviza y se escala (`relieve`: px de proyector por unidad de altura) → normales por gradiente.
Cada mano es una luz puntual en (x, y) del proyector a una altura `z` sobre la mesa (de la mano si la fusión la da,
si no `altura`). Por píxel:

    luz = intensidad · max(0, n·l) · caída radial (radio) · cono de linterna · sombra  +  ambiente

- caída radial: (1 − d²/radio²)² (se apaga del todo a `radio` px);
- cono: el eje apunta hacia abajo, inclinado `inclinacion` grados hacia donde apunta la mano (muñeca → dedo, si se
  sabe); `cono` es el semiángulo (90 = sin cono, luz desnuda);
- sombra: se marcha el rayo superficie → luz sobre el relieve (`pasos` muestras); lo que tapa el relieve queda en
  sombra suave. Con la luz baja (rasante) los objetos ganan volumen y arrastran sombra.

Sin manos: solo `ambiente` (muy tenue). Todo en float32 a 480x270 (escala 4): ~10 ms por luz con sombras.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import cv2
import numpy as np

from .instrument import Instrument
from .scan import ScanData


def normals(z: np.ndarray, spacing: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Normales unitarias de un campo de alturas z(y, x) con x a la derecha, y hacia abajo y z hacia arriba."""
    gy, gx = np.gradient(z.astype(np.float32), spacing)
    inv = 1.0 / np.sqrt(gx * gx + gy * gy + 1.0)
    return (-gx * inv).astype(np.float32), (-gy * inv).astype(np.float32), inv.astype(np.float32)


class HeightField:
    def __init__(self, height: np.ndarray, scale: int = 4, smooth: float = 2.0, relief: float = 1.5,
                 clip_pct: tuple[float, float] = (0.5, 99.5)):
        H, W = height.shape
        self.full = (W, H)
        self.scale = int(max(1, scale))
        h = height.astype(np.float32)
        lo, hi = np.percentile(h, clip_pct)
        h = np.clip(h, lo, hi)
        h = cv2.resize(h, (W // self.scale, H // self.scale), interpolation=cv2.INTER_AREA)
        if smooth > 0:
            h = cv2.GaussianBlur(h, (0, 0), smooth)
        self.z = (h * relief).astype(np.float32)                       # px de proyector
        self.nx, self.ny, self.nz = normals(self.z, float(self.scale))
        hh, ww = self.z.shape
        self.X = ((np.arange(ww, dtype=np.float32) + 0.5) * self.scale)[None, :].repeat(hh, 0)
        self.Y = ((np.arange(hh, dtype=np.float32) + 0.5) * self.scale)[:, None].repeat(ww, 1)

    @property
    def shape(self) -> tuple[int, int]:
        return self.z.shape

    def sample(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Altura en puntos (px de proyector), bilineal."""
        return cv2.remap(self.z, (x / self.scale - 0.5).astype(np.float32), (y / self.scale - 0.5).astype(np.float32),
                         cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


@dataclass
class Light:
    x: float
    y: float
    z: float = 220.0                         # altura sobre la mesa, px de proyector
    color: tuple[float, float, float] = (0.78, 0.92, 1.0)      # BGR 0..1
    intensity: float = 1.3
    radius: float = 420.0
    dir: tuple[float, float] | None = None   # hacia dónde apunta la mano (unitario en x, y), None = recto abajo
    cone_deg: float = 50.0
    tilt_deg: float = 35.0


def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / max(e1 - e0, 1e-6), 0, 1)
    return t * t * (3 - 2 * t)


def shade(hf: HeightField, lights: list[Light], ambient: float = 0.025,
          ambient_color: tuple[float, float, float] = (1.0, 1.0, 1.0), shadows: bool = True, steps: int = 12,
          specular: float = 0.0, bias: float = 1.5) -> np.ndarray:
    """Imagen (h, w, 3) float32 BGR, 0..~1, a la resolución del campo de alturas."""
    hh, ww = hf.shape
    out = np.empty((hh, ww, 3), np.float32)
    out[:] = np.asarray(ambient_color, np.float32) * ambient
    for L in lights:
        dx, dy, dz = L.x - hf.X, L.y - hf.Y, L.z - hf.z
        dxy2 = dx * dx + dy * dy
        dist = np.sqrt(dxy2 + dz * dz) + 1e-6
        lx, ly, lz = dx / dist, dy / dist, dz / dist
        lam = np.maximum(hf.nx * lx + hf.ny * ly + hf.nz * lz, 0)
        att = np.clip(1.0 - dxy2 / (L.radius * L.radius), 0, 1) ** 2
        k = lam * att
        if L.cone_deg < 89:
            t = math.radians(L.tilt_deg) if L.dir is not None else 0.0
            ddx, ddy = L.dir if L.dir is not None else (0.0, 0.0)
            ax, ay, az = ddx * math.sin(t), ddy * math.sin(t), -math.cos(t)
            cosang = -(lx * ax + ly * ay + lz * az)
            outer = math.cos(math.radians(L.cone_deg))
            inner = math.cos(math.radians(L.cone_deg * 0.6))
            k = k * _smoothstep(outer, inner, cosang)
        if specular > 0:
            hx, hy, hz = lx, ly, lz + 1.0                              # vista desde arriba (0, 0, 1)
            hn = np.sqrt(hx * hx + hy * hy + hz * hz) + 1e-6
            spec = np.maximum((hf.nx * hx + hf.ny * hy + hf.nz * hz) / hn, 0) ** 24
            k = k + specular * spec * att
        if shadows and steps > 0:
            live = k > 1e-3
            if live.any():
                occl = np.zeros_like(k)
                for i in range(1, steps + 1):
                    f = i / (steps + 1)
                    qx, qy = hf.X + dx * f, hf.Y + dy * f
                    ray = hf.z + dz * f
                    terr = hf.sample(qx, qy)
                    occl = np.maximum(occl, np.clip((terr - ray - bias) / 4.0, 0, 1))
                vis = cv2.GaussianBlur(1.0 - occl, (0, 0), 1.0)
                k = k * vis
        out += (L.intensity * k)[..., None] * np.asarray(L.color, np.float32)
    return out


def to_bgr8(img: np.ndarray) -> np.ndarray:
    return np.clip(img * 255.0, 0, 255).astype(np.uint8)


class Linterna(Instrument):
    """Instrumento sin toques propios: lee manos (o toques) y proyecta el relieve iluminado por ellas."""
    kind = "linterna"
    priority = 0
    claims_touch = False
    DEFAULTS = {"radio": 420.0, "intensidad": 1.3, "color": [255, 236, 200], "altura": 200.0, "relieve": 2.0,
                "suavizado": 2.0, "ambiente": 0.025, "cono": 50.0, "inclinacion": 35.0, "sombras": True,
                "pasos": 12, "especular": 0.0, "demo": False, "fuente": "auto", "escala": 4, "scan": "",
                "max_luces": 2, "suave": 0.5, "manos_stale_s": 0.6}

    def __init__(self, scan: ScanData | None = None, params: dict | None = None):
        super().__init__()
        self.scan = scan
        self.hf: HeightField | None = None
        self.hf_key = None
        self.lights: dict[str, Light] = {}
        self.source = "ninguna"
        self.last_ms = 0.0
        self.configure(params or {})

    def configure(self, params: dict) -> None:
        p = {**self.DEFAULTS, **{k: v for k, v in params.items() if k in self.DEFAULTS}}
        self.params = p
        self._ensure_scan(p["scan"])
        key = (id(self.scan), float(p["relieve"]), float(p["suavizado"]), int(p["escala"]))
        if key != self.hf_key:
            height = self.scan.height if self.scan.height is not None else np.zeros((1080, 1920), np.float32)
            self.hf = HeightField(height, key[3], key[2], key[1])
            self.hf_key = key

    def _color(self) -> tuple[float, float, float]:
        r, g, b = (float(v) / 255.0 for v in self.params["color"][:3])
        return (b, g, r)

    def update_sources(self, hands: dict, touches: dict, now: float) -> None:
        """Actualiza las luces desde las manos (comando "hands") o, si no hay, desde los toques vivos."""
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
            a = now * 0.6
            pts["demo"] = {"x": 960 + 620 * math.cos(a), "y": 560 + 300 * math.sin(a * 1.3), "height": 0.4,
                           "dir": [-math.sin(a), math.cos(a)]}
            self.source = "demo"
        items = list(pts.items())[: int(p["max_luces"])]
        alpha = float(p["suave"])
        new = {}
        for k, h in items:
            z = float(p["altura"])
            if h.get("height") is not None:
                z = float(p["altura"]) * (0.35 + 1.3 * float(np.clip(h["height"], 0, 1)))
            d = h.get("dir")
            d = None if d is None else tuple(float(v) for v in d[:2])
            prev = self.lights.get(k)
            x, y = float(h["x"]), float(h["y"])
            if prev is not None:
                x, y, z = (alpha * x + (1 - alpha) * prev.x, alpha * y + (1 - alpha) * prev.y, alpha * z + (1 - alpha) * prev.z)
            new[k] = Light(x, y, z, self._color(), float(p["intensidad"]), float(p["radio"]), d, float(p["cono"]),
                           float(p["inclinacion"]))
        self.lights = new

    def image(self) -> np.ndarray:
        p = self.params
        t0 = time.perf_counter()
        img = shade(self.hf, list(self.lights.values()), float(p["ambiente"]), self._color(), bool(p["sombras"]),
                    int(p["pasos"]), float(p["especular"]))
        self.last_ms = 1000 * (time.perf_counter() - t0)
        return img

    def render(self, img: np.ndarray, sx: float, sy: float, now: float) -> None:
        lit = to_bgr8(self.image())
        lit = cv2.resize(lit, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
        img[:] = cv2.add(img, lit)

    def status(self) -> dict:
        return {"kind": self.kind, "luces": {k: [round(l.x), round(l.y), round(l.z)] for k, l in self.lights.items()},
                "fuente": self.source, "relieve": self.scan.source if self.scan else None,
                "ms": round(self.last_ms, 1), "res": list(self.hf.shape[::-1]) if self.hf else None}
