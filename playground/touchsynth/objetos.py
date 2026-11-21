"""Objetos genéricos de la escena como superficies interactivas (nada hardcodeado por nombre).

Utilidades que comparten objeto_synth, objeto_vivo, toque_onda, mano_luz, juego_memoria y mirada:

- `object_contour_px(scene, o)`: contorno del objeto en px del proyector (1920x1080): el `contour` de la escena del
  escaneo (`projector_px`) o `extra["projector_contour_px"]` (objects.json P5). None si no hay (escena demo en mm).
- `scene_objects_px(scene)`: todos los objetos con contorno en px, menos la mesa, lo que cubre casi todo el lienzo y
  lo que tiene colocación provisional (el OHM que agrega el runtime en mm). Cada uno con su color estable por id.
- `pick_object(scene, target, query)`: el objeto de un efecto: el target del spawn, si no el param `objeto` (id,
  kind, nombre o alias), si no el más grande.
- `ObjetoSynth` y `ObjetoVivo`: el cocodrilo sintetizador y el cocodrilo vivo para cualquier contorno (zonas a lo
  largo del eje mayor, escamas, respiración, "patas" donde el contorno tiene salientes).
"""

from __future__ import annotations

import colorsys
import hashlib
import math
from dataclasses import dataclass

import cv2
import numpy as np

from .alive import CrocAlive
from .croc import CrocSynth
from .instrument import add_layer, palette as palette_of

W, H = 1920, 1080
MAX_AREA_FRAC = 0.45            # un "objeto" que tapa casi todo el lienzo es la mesa o el fondo, no un objeto


def object_color(oid: str, s: float = 0.75, v: float = 1.0) -> list[int]:
    """Color estable (RGB 0-255) por id de objeto: el mismo en todos los efectos y en todas las corridas."""
    h = int(hashlib.md5(str(oid).encode()).hexdigest()[:6], 16) / 0xFFFFFF
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return [int(r * 255), int(g * 255), int(b * 255)]


def rgb_to_bgr(rgb) -> tuple[int, int, int]:
    r, g, b = (int(max(0, min(255, v))) for v in list(rgb)[:3])
    return (b, g, r)


def object_contour_px(scene, o) -> np.ndarray | None:
    if o is None:
        return None
    c = None
    if getattr(scene, "projector_px", False) and o.contour and len(o.contour) >= 3:
        c = o.contour
    elif (o.extra or {}).get("projector_contour_px"):
        c = o.extra["projector_contour_px"]
    if c is None or len(c) < 3:
        return None
    a = np.asarray(c, np.float32).reshape(-1, 2)
    return a if np.isfinite(a).all() else None


@dataclass
class ObjPx:
    id: str
    kind: str
    name: str
    contour: np.ndarray            # (N, 2) float32, px del proyector
    color: list[int]               # RGB
    area: float
    center: tuple[float, float]
    quad: np.ndarray | None = None  # 4 esquinas (si es un plano: cuadro, hoja)

    def bbox(self) -> tuple[int, int, int, int]:
        return cv2.boundingRect(self.contour)

    def mask_at(self, sx: float, sy: float):
        """(x, y, máscara uint8) del relleno en el lienzo (sx, sy), cacheada: rellenar aditivo cuesta una pasada."""
        cache = self.__dict__.setdefault("_masks", {})
        key = (round(sx, 4), round(sy, 4))
        m = cache.get(key)
        if m is None:
            p = np.round(self.contour.astype(np.float64) * (sx, sy)).astype(np.int32).reshape(-1, 1, 2)
            x, y, w, h = cv2.boundingRect(p)
            mk = np.zeros((max(1, h), max(1, w)), np.uint8)
            cv2.fillPoly(mk, [p - (x, y)], 255)
            m = cache[key] = (x, y, mk)
        return m

    def inside(self, x: float, y: float, margin: float = 0.0) -> bool:
        d = cv2.pointPolygonTest(self.contour.reshape(-1, 1, 2), (float(x), float(y)), True)
        return d >= -margin

    def distance(self, x: float, y: float) -> float:
        """Distancia al contorno (0 adentro)."""
        d = cv2.pointPolygonTest(self.contour.reshape(-1, 1, 2), (float(x), float(y)), True)
        return max(0.0, -float(d))


def _usable(o) -> bool:
    if o.kind in ("table", "person", "light"):
        return False
    pl = str((o.extra or {}).get("placement", ""))
    return not pl.startswith("provisional")


def to_objpx(scene, o) -> ObjPx | None:
    c = object_contour_px(scene, o)
    if c is None:
        return None
    area = float(abs(cv2.contourArea(c)))
    if area < 50:
        return None
    m = cv2.moments(c)
    center = (m["m10"] / m["m00"], m["m01"] / m["m00"]) if m["m00"] else tuple(c.mean(axis=0))
    q = (o.extra or {}).get("quad")
    quad = np.asarray(q, np.float32).reshape(-1, 2) if q is not None and len(q) == 4 else None
    return ObjPx(o.id, o.kind, o.name, c, object_color(o.id), area, (float(center[0]), float(center[1])), quad)


def scene_objects_px(scene, exclude: tuple[str, ...] = ()) -> list[ObjPx]:
    out = []
    for o in getattr(scene, "objects", {}).values():
        if o.id in exclude or not _usable(o):
            continue
        op = to_objpx(scene, o)
        if op is not None and op.area < MAX_AREA_FRAC * W * H:
            out.append(op)
    return out


def objects_key(scene) -> tuple:
    """Clave barata para saber si cambió la geometría de la escena (nudge, reload_scene)."""
    return tuple((o.id, id(o.contour), len(o.contour or ())) for o in getattr(scene, "objects", {}).values())


def pick_object(scene, target=None, query: str = "", auto: bool = True):
    """(SceneObject, contorno px) del efecto: target del spawn › param `objeto` › el más grande con contorno."""
    if target is not None:
        o = scene.objects.get(target.id, target)
        c = object_contour_px(scene, o)
        if c is not None:
            return o, c
    q = str(query or "").strip()
    if q:
        o = scene.find(q) if hasattr(scene, "find") else None
        c = object_contour_px(scene, o)
        if c is not None:
            return o, c
    if auto:
        objs = scene_objects_px(scene)
        if objs:
            best = max(objs, key=lambda p: p.area)
            return scene.objects[best.id], best.contour
    return None, None


def parse_ids(v) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [s.strip() for s in v.replace(";", ",").split(",") if s.strip()]
    return [str(s) for s in v]


def order_quad(q: np.ndarray) -> np.ndarray:
    """Esquinas en orden: arriba-izq, arriba-der, abajo-der, abajo-izq."""
    q = np.asarray(q, np.float32).reshape(4, 2)
    s, d = q.sum(axis=1), np.diff(q, axis=1).ravel()
    return np.array([q[np.argmin(s)], q[np.argmin(d)], q[np.argmax(s)], q[np.argmax(d)]], np.float32)


def resample_closed(c: np.ndarray, step: float = 6.0, max_pts: int = 600) -> np.ndarray:
    """Puntos a `step` px a lo largo del contorno cerrado (para iluminar tramos del borde)."""
    c = np.asarray(c, np.float32).reshape(-1, 2)
    cc = np.vstack([c, c[:1]])
    seg = np.hypot(*np.diff(cc, axis=0).T)
    L = float(seg.sum())
    if L <= 0:
        return c
    n = int(min(max_pts, max(8, L / step)))
    cum = np.concatenate([[0], np.cumsum(seg)])
    t = np.linspace(0, L, n, endpoint=False)
    return np.stack([np.interp(t, cum, cc[:, 0]), np.interp(t, cum, cc[:, 1])], axis=1).astype(np.float32)


def runs_of(mask: np.ndarray) -> list[tuple[int, int]]:
    """Tramos [a, b) donde mask es True (contorno cerrado: un tramo que cruza el final se parte en dos)."""
    if not mask.any():
        return []
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]))


# ---------------------------------------------------------------------- instrumentos genéricos

def _contour_key(c: np.ndarray):
    return hash(np.round(np.asarray(c, np.float32), 1).tobytes())


class ObjetoSynth(CrocSynth):
    """Cualquier objeto como instrumento por zonas a lo largo de su eje mayor (cabeza = punta más ancha)."""
    kind = "objeto"

    def __init__(self, params: dict | None = None, contour=None, obj_id: str | None = None):
        self.obj_id = obj_id
        if contour is not None:                       # sin pasar por el cocodrilo del escaneo al construir
            self._ext = np.asarray(contour, np.float32).reshape(-1, 2)
            self._ext_key = _contour_key(self._ext)
        super().__init__(None, params)

    def status(self) -> dict:
        return {**super().status(), "objeto": self.obj_id}


class ObjetoVivo(CrocAlive):
    """El cocodrilo vivo para cualquier contorno, con `res` (se calcula a menor resolución: objetos grandes caben en
    el presupuesto de cuadro) y energía: despierta al tocarlo (más brillo, respira más hondo, patas más sueltas) y se
    adormece en `despierto_s`."""
    kind = "objeto_vivo"
    DEFAULTS = {**CrocAlive.DEFAULTS, "color": "auto", "res": 0.5, "despierto_s": 6.0, "dormido": 0.35,
                "palette": "selva"}

    def __init__(self, params: dict | None = None, contour=None, obj_id: str | None = None, auto_color=None):
        self.obj_id = obj_id
        self.auto_color = list(auto_color or [90, 220, 120])
        self.energia = 1.0                               # despierta al aparecer y se va adormeciendo
        self._full = None
        self.f = self._res(params or {})
        if contour is not None:
            self._full = np.asarray(contour, np.float32).reshape(-1, 2)
            self._ext = self._full * self.f
            self._ext_key = (_contour_key(self._full), self.f)
        super().__init__(None, params)

    def _res(self, params: dict) -> float:
        try:
            return float(np.clip(float(params.get("res", self.DEFAULTS["res"])), 0.2, 1.0))
        except (TypeError, ValueError):
            return float(self.DEFAULTS["res"])

    def set_contour(self, contour) -> None:
        c = np.asarray(contour, np.float32).reshape(-1, 2)
        key = (_contour_key(c), self.f)
        if key != getattr(self, "_ext_key", None):
            self._full = c
            self._ext, self._ext_key = c * self.f, key
            self.configure(self.params)

    def configure(self, params: dict) -> None:
        f = self._res(params)
        if f != self.f and self._full is not None:
            self.f = f
            self._ext, self._ext_key = self._full * f, (_contour_key(self._full), f)
        p = dict(params)
        if p.get("color", "auto") == "auto" or not isinstance(p.get("color"), (list, tuple)):
            p["color"] = self.auto_color
        p["cell_px"] = max(4, int(round(float(p.get("cell_px", self.DEFAULTS["cell_px"])) * self.f)))
        p["leg_min_px"] = float(p.get("leg_min_px", self.DEFAULTS["leg_min_px"])) * self.f
        super().configure(p)
        self._idle0, self._breath0 = float(self.params["idle_deg"]), float(self.params["breath"])

    # coordenadas: afuera px del proyector, adentro px del rig (× f)
    def hit(self, x: float, y: float) -> bool:
        return self.rig is not None and self.rig.z.zone_at(x * self.f, y * self.f) is not None

    def handle(self, ev: dict, now: float) -> None:
        if "x" in ev and "y" in ev:
            ev = {**ev, "x": float(ev["x"]) * self.f, "y": float(ev["y"]) * self.f}
        super().handle(ev, now)

    def trigger(self, x: float, y: float, zone: int, now: float) -> None:
        self.energia = 1.0
        self._t_wake = now                    # la energía decae desde el toque, no desde el cuadro anterior
        super().trigger(x, y, zone, now)

    def poke(self, now: float) -> None:
        """Aletea y despierta sin tocar (set_param aletear true)."""
        if self.rig is None:
            return
        cx, cy = self.rig.center_local()
        x, y = cx + self.rig.x0, cy + self.rig.y0
        z = self.rig.z.zone_at(x, y)
        self.trigger(x, y, 0 if z is None else z, now)

    def step(self, now: float) -> None:
        ref = max(self._t if self._t is not None else now, getattr(self, "_t_wake", -1e18))
        dt = max(0.0, now - ref)
        super().step(now)
        self.energia *= math.exp(-dt / max(float(self.params["despierto_s"]), 1e-3))
        e = 0.25 + 0.75 * self.energia
        self.params["idle_deg"] = self._idle0 * e
        self.params["breath"] = self._breath0 * (0.4 + 0.6 * self.energia)

    def _cell_brightness(self, now: float) -> np.ndarray:
        p, rig, f = self.params, self.rig, self.f
        dorm = float(p["dormido"])
        b = np.full(rig.n_cells, float(p["base"]) * (dorm + (1 - dorm) * self.energia), np.float32)
        if rig.n_cells == 0:
            return b
        b += float(p["glow"]) * self.glow[rig.cell_zone]
        if float(p["shimmer"]) > 0:
            b += float(p["shimmer"]) * (0.4 + 0.6 * self.energia) * np.sin(now * 2.2 + rig.cell_phase)
        if p["ripple"]:
            for rx, ry, t0 in self.ripples:
                age = now - t0
                rad = (40 + 420 * age) * f
                d = np.hypot(rig.cell_xy[:, 0] - rx, rig.cell_xy[:, 1] - ry)
                b += 0.9 * math.exp(-age / 0.6) * np.exp(-((d - rad) / (26.0 * f)) ** 2)
        return np.clip(b, 0, 1.2)

    # dibujo en uint8 con una tabla por celda (una sola indexación por cuadro): ~4x más barato que el float del cocodrilo
    def _index(self) -> np.ndarray:
        rig = self.rig
        if getattr(self, "_idx_rig", None) is not rig:
            n = rig.n_cells
            idx = np.where(rig.label >= 0, rig.label + 1, 0)
            idx = np.where(rig.edges, rig.label + 1 + n, idx)
            self._idx, self._idx_rig = idx.astype(np.int32), rig
        return self._idx

    def sprite8(self, now: float) -> np.ndarray:
        p, rig = self.params, self.rig
        if not p["scales"] or rig.n_cells == 0 or p["foto"]:
            return (np.clip(self.sprite(now), 0, 1) * 255).astype(np.uint8)
        b = self._cell_brightness(now)
        pk = (str(p["palette"]), rig.z.n, tuple(p["color"]))
        if getattr(self, "_pal_key", None) != (pk, id(rig)):
            zone_cols = np.asarray(palette_of(str(p["palette"]), rig.z.n), np.float32) / 255.0
            base_col = np.asarray(list(p["color"])[::-1], np.float32) / 255.0
            self._cell_col = 0.6 * base_col + 0.4 * zone_cols[rig.cell_zone]
            self._pal_key = (pk, id(rig))
        edge_col = np.asarray(list(p["scale_color"])[::-1], np.float32) / 255.0
        lut = np.empty((2 * rig.n_cells + 1, 3), np.float32)
        lut[0] = 0
        lut[1:rig.n_cells + 1] = self._cell_col * b[:, None]
        lut[rig.n_cells + 1:] = np.minimum(1.0, edge_col * (b[:, None] + 0.25))
        lut8 = np.clip(lut * 255.0, 0, 255).astype(np.uint8)
        return lut8[self._index()]

    def compose8(self, now: float) -> np.ndarray:
        rig = self.rig
        spr = self.sprite8(now)
        h, w = spr.shape[:2]
        if self.params["legs"] and rig.legs:
            out = spr.copy()
            out[rig.leg_union] = 0
            for i, g in enumerate(rig.legs):
                leg = np.where(g.mask[..., None], spr, 0).astype(np.uint8)
                M = cv2.getRotationMatrix2D(g.root, self.leg_angle(i, now), 1.0)
                np.maximum(out, cv2.warpAffine(leg, M, (w, h), flags=cv2.INTER_LINEAR), out=out)
        else:
            out = spr
        s = self.breath_now
        if abs(s - 1.0) > 1e-4:
            M = cv2.getRotationMatrix2D(rig.center_local(), 0.0, s)
            out = cv2.warpAffine(out, M, (w, h), flags=cv2.INTER_LINEAR)
        return out

    def render(self, img: np.ndarray, sx: float, sy: float, now: float) -> None:
        if self.rig is None:
            return
        rig = self.rig
        layer = self.compose8(now)
        kx, ky = sx / self.f, sy / self.f
        if abs(kx - 1) > 1e-6 or abs(ky - 1) > 1e-6:
            layer = cv2.resize(layer, (max(1, int(round(rig.w * kx))), max(1, int(round(rig.h * ky)))),
                               interpolation=cv2.INTER_LINEAR)
        add_layer(img, layer, int(round(rig.x0 * kx)), int(round(rig.y0 * ky)))

    def resolve(self, index: int | None):
        xy = super().resolve(index)
        return None if xy is None else (xy[0] / self.f, xy[1] / self.f)

    def status(self) -> dict:
        st = super().status()
        f = self.f
        for g in st.get("patas", []):
            g["raiz"] = [round(v / f, 1) for v in g["raiz"]]
        if st.get("bbox"):
            st["bbox"] = [int(round(v / f)) for v in st["bbox"]]
        return {**st, "objeto": self.obj_id, "res": f, "energia": round(self.energia, 3)}
