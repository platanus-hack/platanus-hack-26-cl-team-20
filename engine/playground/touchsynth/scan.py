"""Datos del escaneo en el espacio del proyector (px de 1920x1080): objetos, cocodrilo, altura y zona libre.

Lee de una carpeta de escaneo (`captures/scan5` por defecto, o `$SLSCAN_SCAN`):
- `workbench.json` (contorno exacto del cocodrilo, teclado, torre, Buda) y `segments.json` (bbox de todo, jogs de
  la DDJ, hoja carta): polígonos de objetos.
- `height_proj.npy`: altura relativa (paralaje en px de la Osmo respecto al plano dominante; positivo = sobresale).
- `proj_to_cam.npz` (`valid`): qué píxeles del proyector ve la Osmo.

`free_mask` = mesa menos objetos: píxeles planos (|altura| chica), vistos por la cámara, fuera de todos los
polígonos (dilatados un margen) y lejos del borde del lienzo. `largest_rect` busca el rectángulo libre más grande
(ahí va la marimba). Si falta la carpeta, `ScanData.fallback()` da una escena sintética para que todo funcione
igual (y el estado lo dice).
"""

from __future__ import annotations

import json
import time
import os
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

W, H = 1920, 1080
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
NOT_OBJECTS = ("mesa", "papel")          # la hoja carta está sobre la mesa: es superficie libre
UNIONS = ("controladores",)              # bbox que junta varios objetos (y la mesa entre ellos): no tapa la zona libre


CURRENT_SCAN = "scan5"                    # escaneo vigente (16-oct, luz apagada, 1,05 px); scan2/scan4 son viejos


def default_scan_dir() -> Path:
    env = os.environ.get("SLSCAN_SCAN")
    if env:
        return Path(env)
    for p in (Path("captures") / CURRENT_SCAN, PROJECT_DIR / "captures" / CURRENT_SCAN):
        if p.exists():
            return p
    return Path("captures") / CURRENT_SCAN


def _rect_poly(b) -> np.ndarray:
    x, y, w, h = (float(v) for v in b[:4])
    return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], np.float32)


def _circle_poly(x, y, r, n=32) -> np.ndarray:
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.stack([x + r * np.cos(t), y + r * np.sin(t)], axis=1).astype(np.float32)


@dataclass
class ScanData:
    folder: Path | None
    objects: dict[str, list[np.ndarray]] = field(default_factory=dict)   # nombre → polígonos (px de proyector)
    croc: np.ndarray | None = None                                     # contorno del cocodrilo (N, 2)
    height: np.ndarray | None = None                                   # (H, W) float32
    valid: np.ndarray | None = None                                    # (H, W) bool
    size: tuple[int, int] = (W, H)
    source: str = "escaneo"
    croc_source: str | None = None                                     # de qué archivo salió el contorno

    @classmethod
    def load(cls, folder: str | Path | None = None, need_height: bool = True) -> "ScanData":
        folder = Path(folder) if folder else default_scan_dir()
        if not folder.exists():
            return cls.fallback(f"no existe {folder}")
        seg = json.loads((folder / "segments.json").read_text()) if (folder / "segments.json").exists() else {}
        wb = json.loads((folder / "workbench.json").read_text()) if (folder / "workbench.json").exists() else {}
        objs: dict[str, list[np.ndarray]] = {}
        for src in (seg, wb):                                          # workbench (más exacto) pisa a segments
            for name, o in src.items():
                if not isinstance(o, dict) or name in NOT_OBJECTS:
                    continue
                polys = []
                if o.get("contour") is not None and len(o["contour"]) >= 3:
                    polys.append(np.asarray(o["contour"], np.float32).reshape(-1, 2))
                elif o.get("quad") is not None:
                    polys.append(np.asarray(o["quad"], np.float32).reshape(-1, 2))
                elif o.get("bbox") is not None:
                    polys.append(_rect_poly(o["bbox"]))
                for j in o.get("jogs") or []:
                    polys.append(_circle_poly(*j[:3]))
                if polys:
                    objs[name] = polys
        # contorno del cocodrilo: el de workbench.json (es el que se afina a mano y con las cámaras,
        # playground/croc_refine.py); segments.json solo si workbench no lo tiene
        croc, croc_src = None, None
        for fname, src in (("workbench.json", wb), ("segments.json", seg)):
            c = (src.get("cocodrilo") or {}).get("contour")
            if c is not None and len(c) >= 3:
                mt = (folder / fname).stat().st_mtime
                croc = np.asarray(c, np.float32).reshape(-1, 2)
                croc_src = f"{fname} ({time.strftime('%H:%M', time.localtime(mt))})"
                objs["cocodrilo"] = [croc]
                break
        sd = cls(folder, objs, croc, source=f"escaneo {folder.name}")
        sd.croc_source = croc_src
        if need_height and (folder / "height_proj.npy").exists():
            sd.height = np.load(folder / "height_proj.npy").astype(np.float32)
            sd.size = (sd.height.shape[1], sd.height.shape[0])
        if (folder / "proj_to_cam.npz").exists():
            try:
                with np.load(folder / "proj_to_cam.npz") as z:
                    sd.valid = z["valid"].astype(bool)
            except Exception:  # noqa: BLE001
                sd.valid = None
        if sd.croc is None:
            sd.croc = fallback_croc()
            sd.source += " (sin contorno de cocodrilo: uno sintético)"
        return sd

    def json_mtime(self) -> float:
        """Última modificación de workbench.json / segments.json (0 si no hay carpeta)."""
        if self.folder is None:
            return 0.0
        ts = [(self.folder / f).stat().st_mtime for f in ("workbench.json", "segments.json") if (self.folder / f).exists()]
        return max(ts) if ts else 0.0

    @classmethod
    def fallback(cls, why: str = "") -> "ScanData":
        """Escena sintética: cocodrilo de 600x150 px sobre un 'teclado', una torre y relieve suave."""
        croc = fallback_croc()
        objs = {"cocodrilo": [croc], "teclado": [_rect_poly([650, 640, 790, 100])], "torre": [_rect_poly([120, 150, 450, 700])]}
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        height = np.zeros((H, W), np.float32)
        height += 60 * np.exp(-(((xx - 345) / 200) ** 2 + ((yy - 500) / 330) ** 2) * 3)        # torre
        cmask = np.zeros((H, W), np.uint8)
        cv2.fillPoly(cmask, [croc.astype(np.int32).reshape(-1, 1, 2)], 1)
        height += cv2.GaussianBlur(cmask.astype(np.float32) * 25, (0, 0), 8)                # cocodrilo
        return cls(None, objs, croc, height, None, (W, H), f"sintético ({why})" if why else "sintético")

    # ------------------------------------------------------------------ máscaras
    def object_mask(self, margin: int = 0, skip: tuple[str, ...] = ()) -> np.ndarray:
        m = np.zeros((self.size[1], self.size[0]), np.uint8)
        for name, polys in self.objects.items():
            if name in skip:
                continue
            for p in polys:
                cv2.fillPoly(m, [np.round(p).astype(np.int32).reshape(-1, 1, 2)], 255)
        if margin > 0:
            m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin + 1, 2 * margin + 1)))
        return m > 0

    def photo_gray(self) -> np.ndarray | None:
        """La foto con luz blanca vista desde el proyector (scene_pov.png), en grises."""
        p = self.folder / "scene_pov.png" if self.folder else None
        if p is None or not p.exists():
            return None
        g = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        return None if g is None or g.shape[::-1] != tuple(self.size) else g

    def free_mask(self, margin: int = 30, grad_thr: float | None = 0.15, bright_thr: float | None = 120,
                  std_thr: float | None = 25, border: int = 40, skip: tuple[str, ...] = UNIONS) -> np.ndarray:
        """Mesa menos objetos (bool, tamaño del proyector): fuera de los polígonos (+ margen), localmente plano
        (gradiente de la altura suavizada < grad_thr; el plano dominante del escaneo no siempre es la mesa, así que
        no se usa la altura absoluta), claro con luz blanca (bright_thr: la mesa refleja el proyector) y sin textura
        (std local < std_thr). None en un umbral = no se usa ese criterio."""
        w, h = self.size
        free = np.ones((h, w), bool)
        if grad_thr is not None and self.height is not None:
            hs = cv2.GaussianBlur(self.height, (0, 0), 6)
            gy, gx = np.gradient(hs)
            free &= np.hypot(gx, gy) < grad_thr
        g = self.photo_gray() if (bright_thr is not None or std_thr is not None) else None
        if g is not None:
            gf = g.astype(np.float32)
            if bright_thr is not None:
                free &= gf > bright_thr
            if std_thr is not None:
                m = cv2.blur(gf, (31, 31))
                free &= np.sqrt(np.maximum(cv2.blur(gf * gf, (31, 31)) - m * m, 0)) < std_thr
        return free & self.hard_free(margin, border, skip)

    def hard_free(self, margin: int = 30, border: int = 40, skip: tuple[str, ...] = UNIONS) -> np.ndarray:
        """Lo que no se negocia: fuera de los objetos (+ margen), dentro de lo que ve la cámara y lejos del borde."""
        w, h = self.size
        free = ~self.object_mask(margin, skip)
        if self.valid is not None:
            free &= self.valid
        if border > 0:
            free[:border, :] = free[-border:, :] = False
            free[:, :border] = free[:, -border:] = False
        return free

    def free_rect(self, margin: int = 30, min_w: int = 240, min_h: int = 90) -> tuple[tuple[int, int, int, int] | None, str]:
        """Rectángulo libre más grande: 100 % fuera de los objetos y ≥ 80 % de mesa plana/clara/lisa (tolera
        manchitas del escaneo); relaja los criterios de mesa si no cabe. Devuelve (rect, cómo se eligió)."""
        hard = self.hard_free(margin)
        tries = [("mesa plana, clara y lisa sin objetos", {}),
                 ("mesa plana y clara sin objetos", {"std_thr": None}),
                 ("mesa plana sin objetos", {"bright_thr": None, "std_thr": None}),
                 ("fuera de los objetos", {"grad_thr": None, "bright_thr": None, "std_thr": None})]
        for why, kw in tries:
            r = largest_rect(self.free_mask(margin, **kw), min_w=min_w, min_h=min_h, frac=0.8, hard=hard)
            if r is not None:
                return r, why
        return None, "sin zona libre"


def fallback_croc() -> np.ndarray:
    """Contorno sintético de cocodrilo (cola a la izquierda, cabeza a la derecha), en px del proyector."""
    xs = np.linspace(660, 1230, 40)
    t = (xs - 660) / 570                                             # 0 cola … 1 cabeza
    half = 12 + 48 * np.clip(t * 1.6, 0, 1) - 10 * np.clip((t - 0.85) / 0.15, 0, 1)
    top = np.stack([xs, 550 - half], axis=1)
    bot = np.stack([xs[::-1], 550 + half[::-1]], axis=1)
    return np.vstack([top, bot]).astype(np.float32)


def largest_rect(mask: np.ndarray, cell: int = 16, min_w: int = 240, min_h: int = 90, frac: float = 0.999,
                 max_aspect: float = 6.0, hard: np.ndarray | None = None) -> tuple[int, int, int, int] | None:
    """Rectángulo alineado a los ejes más grande dentro de `mask` (True = libre) con al menos `frac` de píxeles
    libres (tolera manchitas) y, si se da `hard`, 100 % dentro de `hard`. En celdas de `cell` px; prueba tamaños de
    mayor a menor área con imágenes integrales y se queda con el primero que cabe (el del medio entre los que caben).
    None si no cabe ni min_w x min_h."""
    h, w = mask.shape
    gh, gw = h // cell, w // cell
    if gh == 0 or gw == 0:
        return None

    def integral(m):
        g = cv2.resize(m[:gh * cell, :gw * cell].astype(np.float32), (gw, gh), interpolation=cv2.INTER_AREA)
        return np.pad(g.cumsum(0).cumsum(1), ((1, 0), (1, 0)))

    def box_sum(ii, hh, ww):
        return ii[hh:, ww:] - ii[:-hh, ww:] - ii[hh:, :-ww] + ii[:-hh, :-ww]

    ii = integral(mask)
    ih = integral(hard) if hard is not None else None
    sizes = [(hh * ww, hh, ww) for hh in range(max(1, -(-min_h // cell)), gh + 1)
             for ww in range(max(1, -(-min_w // cell)), gw + 1) if max(hh, ww) / min(hh, ww) <= max_aspect]
    for _, hh, ww in sorted(sizes, reverse=True):
        good = box_sum(ii, hh, ww) >= frac * hh * ww - 1e-6
        if ih is not None:
            good &= box_sum(ih, hh, ww) >= hh * ww - 1e-3
        ok = np.argwhere(good)
        if len(ok):
            c = ok[len(ok) // 2]
            return int(c[1] * cell), int(c[0] * cell), int(ww * cell), int(hh * cell)
    return None
