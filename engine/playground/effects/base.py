"""API de efectos: plugins que leen la escena y dibujan en el lienzo de un proyector.

Un efecto es una clase que hereda de `Effect` en un archivo de `playground/effects/`. El runtime
la descubre, la recarga sola cuando el archivo cambia (hot reload: las instancias vivas se
reconstruyen con sus mismos parámetros) y la lista en la galería. Metadatos declarativos:

    class Glow(Effect):
        name = "contour_glow"                 # id único en la galería
        label = "Halo de contorno"
        needs = ("contours",)                 # qué lee: contours | geometry | textures | frames | planes | people | beat
        targets = ("prop", "instrument")      # tipos de objeto sobre los que se puede spawnear ("*" = cualquiera)
        params = {"color": [0, 255, 200], "width_mm": 20, "speed": 1.0}   # editables en vivo

        def update(self, dt, scene): ...      # lógica por cuadro
        def draw(self, canvas): ...           # dibuja sobre el lienzo (numpy BGR) con canvas.project(x_mm, y_mm)

El lienzo (`Canvas`) convierte mm de la mesa a píxeles del proyector. Hoy es ortográfico; en el PC
lo reemplaza el render calibrado (misma API).

**Colores**: hacia afuera (tools, params, JSON) siempre **RGB 0-255** `[r, g, b]`. OpenCV trabaja en BGR:
dentro de un efecto se convierte con `bgr(self.p["color"])` justo antes de dibujar, nunca antes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..scene import Scene, SceneObject

NEEDS = ("contours", "geometry", "textures", "frames", "planes", "people", "beat", "camera")


@dataclass
class Canvas:
    img: np.ndarray                      # (h, w, 3) uint8 BGR
    px_per_mm: float
    origin_px: tuple[float, float] = (0.0, 0.0)   # dónde cae (0,0) mm de la mesa
    flip_y: bool = True

    @property
    def size(self) -> tuple[int, int]:
        return (self.img.shape[1], self.img.shape[0])

    def project(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        px = self.origin_px[0] + x_mm * self.px_per_mm
        py = self.origin_px[1] + (-y_mm if self.flip_y else y_mm) * self.px_per_mm
        if self.flip_y:
            py += self.img.shape[0]
        return int(round(px)), int(round(py))

    def polygon(self, contour_mm, color, thickness: int = 2, fill: bool = False, alpha: float = 1.0) -> None:
        pts = np.array([self.project(x, y) for x, y in contour_mm], np.int32).reshape(-1, 1, 2)
        if alpha >= 1.0:
            if fill:
                cv2.fillPoly(self.img, [pts], color)
            else:
                cv2.polylines(self.img, [pts], True, color, thickness, cv2.LINE_AA)
            return
        # con alfa se mezcla solo la caja del polígono (+ grosor): mismo resultado que copiar el cuadro entero, pero
        # 61 teclas con relleno pasan de ~120 ms a pocos ms por cuadro
        h, w = self.img.shape[:2]
        pad = int(thickness) + 2
        x0, y0 = max(0, int(pts[..., 0].min()) - pad), max(0, int(pts[..., 1].min()) - pad)
        x1, y1 = min(w, int(pts[..., 0].max()) + pad + 1), min(h, int(pts[..., 1].max()) + pad + 1)
        if x1 <= x0 or y1 <= y0:
            return
        roi = self.img[y0:y1, x0:x1]
        layer = roi.copy()
        local = pts - np.array([x0, y0], np.int32)
        if fill:
            cv2.fillPoly(layer, [local], color)
        else:
            cv2.polylines(layer, [local], True, color, thickness, cv2.LINE_AA)
        roi[:] = cv2.addWeighted(layer, alpha, roi, 1 - alpha, 0)

    def mm(self, px: float) -> float:
        return px / self.px_per_mm


class Effect:
    name: str = "effect"
    label: str = "Efecto"
    needs: tuple[str, ...] = ()
    targets: tuple[str, ...] = ("*",)
    params: dict = {}
    description: str = ""
    aliases: tuple[str, ...] = ()        # cómo lo nombra Max por voz ("respira el Buda", "skin de la DDJ"): la secretaria los busca

    def __init__(self, target: SceneObject | None, params: dict | None = None, instance_id: str = ""):
        self.target = target
        self.instance_id = instance_id
        self.p = {**self.params, **(params or {})}
        self.t = 0.0
        self.alive = True

    # --- ciclo de vida ---
    def update(self, dt: float, scene: Scene) -> None:
        self.t += dt

    def draw(self, canvas: Canvas) -> None:
        pass

    def set(self, key: str, value) -> None:
        self.p[key] = value

    def kill(self) -> None:
        self.alive = False

    # --- utilidades ---
    def object(self, scene: Scene) -> SceneObject | None:
        """El objeto vivo (por id), por si fue reemplazado por el tracking."""
        if self.target is None:
            return None
        return scene.objects.get(self.target.id, self.target)

    @classmethod
    def meta(cls) -> dict:
        return {"name": cls.name, "label": cls.label, "needs": list(cls.needs), "targets": list(cls.targets),
                "params": cls.params, "description": cls.description, "aliases": list(getattr(cls, "aliases", ()) or ())}

    def state(self) -> dict:
        return {"id": self.instance_id, "effect": self.name, "target": self.target.id if self.target else None,
                "params": self.p, "t": round(self.t, 2), "alive": self.alive}


def bgr(rgb) -> tuple[int, int, int]:
    """[r, g, b] de los params (RGB 0-255) -> tupla BGR para OpenCV."""
    r, g, b = (int(max(0, min(255, v))) for v in rgb[:3])
    return (b, g, r)


def pulse(t: float, speed: float = 1.0, lo: float = 0.3, hi: float = 1.0) -> float:
    return lo + (hi - lo) * 0.5 * (1 + math.sin(2 * math.pi * speed * t))


def offset_contour(contour, mm: float):
    """Contorno expandido `mm` hacia afuera (aprox. radial desde el centroide)."""
    c = np.asarray(contour, float)
    center = c.mean(axis=0)
    d = c - center
    n = np.linalg.norm(d, axis=1, keepdims=True)
    n[n == 0] = 1
    return (c + d / n * mm).tolist()
