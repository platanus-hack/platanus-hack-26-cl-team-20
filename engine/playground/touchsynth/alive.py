"""Cocodrilo vivo (pedido 018): patas con rigging que aletean, respiración y escamas reactivas.

Todo sale del contorno (px del proyector) y del eje cabeza → cola de `CrocZones`:

- **Patas**: a lo largo del eje se mide, por franjas de 4 px, cuánto sobresale el cuerpo hacia cada costado; la
  línea base del cuerpo es la mediana móvil larga de ese perfil, y una pata es un tramo corto donde el perfil se
  sale de la base más de `leg_min_px` (lejos de la cabeza y la cola). Hasta 2 por costado (4 patas). Cada pata es
  la parte de la silueta más allá de la base en ese tramo; su raíz (pivote) es el punto de la base en el centro.
- **Rigging**: cada pata rota alrededor de su raíz. En reposo un vaivén lento (`idle_deg`, `idle_hz`, en diagonal
  como al caminar); al tocar el cocodrilo aletean (`flap_deg`, `flap_hz`, se apaga en `flap_s`): la pata más
  cercana al toque a pleno y las demás a la mitad. Lo proyectado es la pata girada; la pata real queda a oscuras
  donde la girada no la cubre, y así parece que se mueve (en sala oscura).
- **Respiración**: todo el cocodrilo escala ±`breath` (2 %) alrededor de su centro: con el audio (`scene.audio`,
  bajo suavizado) si llega, con el beat si suena, o lento (`breath_hz`).
- **Escamas**: celdas hexagonales de `cell_px` recortadas a la silueta (y a las patas, que se las llevan al
  girar); cada celda tiene su zona (las mismas del cocodrilo sintetizador): al tocar se enciende la zona y una onda
  sale del dedo; en reposo brillan apenas y titilan. `foto` multiplica por la foto del escaneo (textura real).
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .croc import CrocZones
from .instrument import Instrument, add_layer, palette, scale_bgr


def _rolling_median(x: np.ndarray, win: int) -> np.ndarray:
    from scipy.ndimage import median_filter
    return median_filter(np.asarray(x, np.float64), size=max(3, win | 1), mode="nearest")


class Leg:
    def __init__(self, mask: np.ndarray, root: tuple[float, float], side: int, s_c: float, area: int, zone: int):
        self.mask = mask                  # (h, w) bool en el recorte del cocodrilo
        self.root = root                  # (x, y) en el recorte
        self.side = side                  # +1 / −1: costado respecto al eje
        self.s = s_c                      # posición a lo largo del eje (0 = cabeza)
        self.area = area
        self.zone = zone

    def to_json(self, x0: int, y0: int) -> dict:
        return {"raiz": [round(self.root[0] + x0, 1), round(self.root[1] + y0, 1)], "costado": self.side,
                "area_px": self.area, "zona": self.zone}


class CrocRig:
    """Patas, celdas de escamas y zonas de un contorno de cocodrilo."""

    def __init__(self, contour, n_zones: int = 10, head: str = "auto", leg_min_px: float = 9.0, max_per_side: int = 2,
                 cell_px: int = 14, end_frac: float = 0.10, bin_px: float = 4.0):
        self.z = CrocZones(contour, n_zones, head, "largo", margin=0)
        z = self.z
        self.x0, self.y0, self.w, self.h = z.x0, z.y0, z.w, z.h
        self.mask = z.mask
        ys, xs = np.nonzero(self.mask)
        pts = np.stack([xs, ys], axis=1).astype(np.float64) - z.center
        s, t = pts @ z.u, pts @ z.v
        self.legs: list[Leg] = []
        L = z.s_hi - z.s_lo
        nb = max(8, int(L / bin_px))
        bins = np.clip(((s - z.s_lo) / L * nb).astype(int), 0, nb - 1)
        for side in (+1, -1):
            ext = np.full(nb, np.nan)
            st = side * t
            np.fmax.at(ext, bins, st)                                   # cuánto sale el cuerpo hacia ese costado
            ok = np.isfinite(ext)
            if ok.sum() < 8:
                continue
            ext = np.interp(np.arange(nb), np.nonzero(ok)[0], ext[ok])
            base = _rolling_median(ext, int(nb * 0.35))
            bump = ext - base
            cand = bump > leg_min_px
            lo_e, hi_e = int(nb * end_frac), int(nb * (1 - end_frac))
            cand[:lo_e] = False
            cand[hi_e:] = False
            runs, i = [], 0
            while i < nb:
                if cand[i]:
                    j = i
                    while j + 1 < nb and (cand[j + 1] or (j + 2 < nb and cand[j + 2])):
                        j += 1
                    runs.append((i, j))
                    i = j + 1
                else:
                    i += 1
            scored = sorted(((float(bump[a:b + 1].sum()), a, b) for a, b in runs if b - a >= 1), reverse=True)
            for area_score, a, b in scored[:max_per_side]:
                sel = (bins >= a) & (bins <= b) & (st > base[bins] - 2.0)
                if sel.sum() < 30:
                    continue
                m = np.zeros_like(self.mask)
                m[ys[sel], xs[sel]] = True
                c = (a + b) / 2
                s_c = z.s_lo + (c + 0.5) / nb * L
                t_c = side * float(base[int(round(c))])
                root = z.center + s_c * z.u + t_c * z.v
                zone = int(np.clip(np.searchsorted(z.edges, s_c, side="right") - 1, 0, z.n - 1))
                self.legs.append(Leg(m, (float(root[0]), float(root[1])), side, float((s_c - z.s_lo) / L),
                                     int(sel.sum()), zone))
        self.legs.sort(key=lambda g: (g.s, g.side))
        self.leg_union = np.zeros_like(self.mask)
        for g in self.legs:
            self.leg_union |= g.mask
        self.body = self.mask & ~self.leg_union
        self._cells(cell_px)

    def _cells(self, cell_px: int) -> None:
        """Celdas hexagonales: etiqueta por píxel (−1 afuera), zona y centro de cada celda, y los bordes."""
        h, w = self.mask.shape
        r = max(4.0, float(cell_px))
        dx, dy = r * math.sqrt(3), r * 1.5
        centers = []
        for j in range(int(h / dy) + 2):
            for i in range(int(w / dx) + 2):
                centers.append((i * dx + (dx / 2 if j % 2 else 0.0), j * dy))
        c = np.asarray(centers, np.float32)
        ys, xs = np.nonzero(self.mask)
        if len(xs) == 0 or len(c) == 0:
            self.label = np.full((h, w), -1, np.int32)
            self.cell_xy = np.zeros((0, 2), np.float32)
            self.cell_zone = np.zeros(0, int)
            self.edges = np.zeros((h, w), bool)
            return
        # vecino más cercano por grilla gruesa: celda más cercana a cada píxel de la silueta
        from scipy.spatial import cKDTree
        _, idx = cKDTree(c).query(np.stack([xs, ys], axis=1))
        used, inv = np.unique(idx, return_inverse=True)
        self.label = np.full((h, w), -1, np.int32)
        self.label[ys, xs] = inv
        self.cell_xy = c[used]
        z = self.z
        sc = (self.cell_xy - z.center) @ z.u
        self.cell_zone = np.clip(np.searchsorted(z.edges, sc, side="right") - 1, 0, z.n - 1)
        lab = self.label
        e = np.zeros((h, w), bool)
        e[:, 1:] |= lab[:, 1:] != lab[:, :-1]
        e[1:, :] |= lab[1:, :] != lab[:-1, :]
        self.edges = e & self.mask
        self.cell_phase = np.random.default_rng(7).uniform(0, 2 * math.pi, len(self.cell_xy)).astype(np.float32)

    @property
    def n_cells(self) -> int:
        return len(self.cell_xy)

    def center_local(self) -> tuple[float, float]:
        return float(self.z.center[0]), float(self.z.center[1])


class CrocAlive(Instrument):
    kind = "cocodrilo_vivo"
    priority = 0
    claims_touch = False
    observes_touch = True                  # ve los toques sin quitárselos al cocodrilo sintetizador
    DEFAULTS = {"legs": True, "idle_deg": 6.0, "idle_hz": 0.35, "flap_deg": 24.0, "flap_hz": 3.0, "flap_s": 1.4,
                "breath": 0.02, "breath_src": "auto", "breath_hz": 0.22, "scales": True, "cell_px": 13,
                "color": [90, 220, 120], "scale_color": [190, 255, 140], "palette": "selva", "base": 0.16,
                "glow": 0.9, "decay_s": 1.1, "ripple": True, "shimmer": 0.05, "foto": False, "zones": 10,
                "head": "auto", "leg_min_px": 9.0, "scan": ""}

    def __init__(self, scan=None, params: dict | None = None):
        super().__init__()
        self.scan = scan
        self.rig: CrocRig | None = None
        self.geo_key = None
        self.flaps: list[tuple[float, float]] = []      # (t0, ganancia) por pata
        self.glow = np.zeros(10, np.float32)
        self.ripples: list[tuple[float, float, float]] = []
        self.breath_now = 1.0
        self._bass = 0.0
        self._t = None
        self._photo = None
        self.configure(params or {})

    # ------------------------------------------------------------------ geometría
    def set_contour(self, contour) -> None:
        c = np.asarray(contour, np.float32).reshape(-1, 2)
        key = hash(np.round(c, 1).tobytes())
        if key != getattr(self, "_ext_key", None):
            self._ext, self._ext_key = c, key
            self.configure(self.params)

    def configure(self, params: dict) -> None:
        p = {**self.DEFAULTS, **{k: v for k, v in params.items() if k in self.DEFAULTS}}
        self.params = p
        ext = getattr(self, "_ext", None)
        if ext is None:
            self._ensure_scan(p["scan"], need_height=False)
        src = ("escena", self._ext_key) if ext is not None else id(self.scan)
        key = (src, int(p["zones"]), str(p["head"]), float(p["leg_min_px"]), int(p["cell_px"]))
        if key != self.geo_key:
            contour = ext if ext is not None else self.scan.croc
            self.rig = CrocRig(contour, key[1], key[2], key[3], cell_px=key[4])
            self.flaps = [(-1e9, 0.0)] * len(self.rig.legs)
            self.glow = np.zeros(self.rig.z.n, np.float32)
            self.geo_key = key
            self._photo = None

    def hit(self, x: float, y: float) -> bool:
        return self.rig is not None and self.rig.z.zone_at(x, y) is not None

    # ------------------------------------------------------------------ toques
    def handle(self, ev: dict, now: float) -> None:
        if self.rig is None or ev.get("state") not in ("down", "move"):
            return
        x, y = float(ev["x"]), float(ev["y"])
        zone = self.rig.z.zone_at(x, y)
        if zone is None:
            # un poco afuera también vale para las patas (el dedo al lado de una pata)
            near = [g for g in self.rig.legs if math.hypot(x - self.rig.x0 - g.root[0], y - self.rig.y0 - g.root[1]) < 60]
            if not near:
                return
            zone = near[0].zone
        if ev["state"] == "move" and getattr(self, "_last_zone", {}).get(ev.get("id")) == zone:
            return
        self.__dict__.setdefault("_last_zone", {})[ev.get("id")] = zone
        self.trigger(x, y, zone, now)

    def trigger(self, x: float, y: float, zone: int, now: float) -> None:
        """Toque en (x, y) px del proyector: enciende la zona, lanza una onda y hace aletear las patas."""
        self.glow[zone] = 1.0
        self.ripples.append((x - self.rig.x0, y - self.rig.y0, now))
        self.ripples = self.ripples[-8:]
        if self.rig.legs:
            d = [math.hypot(x - self.rig.x0 - g.root[0], y - self.rig.y0 - g.root[1]) for g in self.rig.legs]
            k = int(np.argmin(d))
            self.flaps = [(now, 1.0 if i == k else 0.5) for i in range(len(self.rig.legs))]

    # ------------------------------------------------------------------ animación
    def leg_angle(self, i: int, now: float) -> float:
        p = self.params
        g = self.rig.legs[i]
        phase = 0.0 if (i % 2 == 0) == (g.side > 0) else math.pi             # diagonales juntas
        a = float(p["idle_deg"]) * math.sin(2 * math.pi * float(p["idle_hz"]) * now + phase)
        t0, gain = self.flaps[i]
        dt = now - t0
        if 0 <= dt < 4 * float(p["flap_s"]):
            a += float(p["flap_deg"]) * gain * math.exp(-dt / float(p["flap_s"])) * math.sin(2 * math.pi * float(p["flap_hz"]) * dt)
        return a * g.side

    def update_breath(self, scene, now: float) -> float:
        p = self.params
        src = str(p["breath_src"])
        audio = getattr(scene, "audio", None) or {}
        beat = getattr(scene, "beat", None) or {}
        if src in ("auto", "audio") and audio.get("live"):
            self._bass = 0.8 * self._bass + 0.2 * float(audio.get("bass", 0.0))
            k = 2 * min(1.0, self._bass) - 1
            self.breath_src_used = "audio"
        elif src in ("auto", "beat") and beat.get("playing"):
            k = math.cos(2 * math.pi * float(beat.get("phase", 0.0)))
            self.breath_src_used = "beat"
        else:
            k = math.sin(2 * math.pi * float(p["breath_hz"]) * now)
            self.breath_src_used = "lento"
        self.breath_now = 1.0 + float(p["breath"]) * k
        return self.breath_now

    def step(self, now: float) -> None:
        super().step(now)
        dt = 0.0 if self._t is None else max(0.0, now - self._t)
        self._t = now
        self.glow *= math.exp(-dt / max(float(self.params["decay_s"]), 1e-3))
        self.ripples = [r for r in self.ripples if now - r[2] < 1.6]

    # ------------------------------------------------------------------ dibujo
    def _cell_brightness(self, now: float) -> np.ndarray:
        p, rig = self.params, self.rig
        b = np.full(rig.n_cells, float(p["base"]), np.float32)
        if rig.n_cells == 0:
            return b
        b += float(p["glow"]) * self.glow[rig.cell_zone]
        if float(p["shimmer"]) > 0:
            b += float(p["shimmer"]) * np.sin(now * 2.2 + rig.cell_phase)
        if p["ripple"]:
            for rx, ry, t0 in self.ripples:
                age = now - t0
                rad = 40 + 420 * age
                d = np.hypot(rig.cell_xy[:, 0] - rx, rig.cell_xy[:, 1] - ry)
                b += 0.9 * math.exp(-age / 0.6) * np.exp(-((d - rad) / 26.0) ** 2)
        return np.clip(b, 0, 1.2)

    def _photo_crop(self):
        if self._photo is None:
            self._photo = False
            folder = getattr(self.scan, "folder", None) if self.scan is not None else None
            if folder is not None and (folder / "scene_pov.png").exists():
                img = cv2.imread(str(folder / "scene_pov.png"))
                r = self.rig
                if img is not None:
                    crop = np.zeros((r.h, r.w, 3), np.uint8)
                    H, W = img.shape[:2]
                    xa, ya, xb, yb = max(0, r.x0), max(0, r.y0), min(W, r.x0 + r.w), min(H, r.y0 + r.h)
                    if xb > xa and yb > ya:
                        crop[ya - r.y0:yb - r.y0, xa - r.x0:xb - r.x0] = img[ya:yb, xa:xb]
                    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
                    self._photo = cv2.merge([g, g, g])
        return None if self._photo is False else self._photo

    def sprite(self, now: float) -> np.ndarray:
        """Imagen del cocodrilo (recorte, float32 BGR 0..1) sin transformar: celdas + bordes de escamas."""
        p, rig = self.params, self.rig
        h, w = rig.mask.shape
        out = np.zeros((h, w, 3), np.float32)
        if not p["scales"] or rig.n_cells == 0:
            col = np.asarray(scale_bgr(tuple(int(c) for c in p["color"][::-1]), 1.0), np.float32) / 255.0
            out[rig.mask] = col * (float(p["base"]) + float(p["glow"]) * float(self.glow.max(initial=0)))
            return out
        b = self._cell_brightness(now)
        zone_cols = np.asarray(palette(str(p["palette"]), rig.z.n), np.float32) / 255.0      # BGR por zona
        base_col = np.asarray(p["color"][::-1], np.float32) / 255.0
        cell_col = 0.6 * base_col + 0.4 * zone_cols[rig.cell_zone]
        lab = rig.label
        inside = lab >= 0
        out[inside] = (cell_col[lab[inside]] * b[lab[inside], None])
        edge_col = np.asarray(p["scale_color"][::-1], np.float32) / 255.0
        e = rig.edges
        out[e] = np.minimum(1.0, edge_col * (b[lab[e], None] + 0.25))
        photo = self._photo_crop() if p["foto"] else None
        if photo is not None:
            out *= 0.35 + 0.9 * photo
        return out

    def compose(self, now: float) -> np.ndarray:
        """Recorte final (float32 0..1): cuerpo quieto + patas giradas en su raíz, todo escalado por la respiración."""
        rig = self.rig
        spr = self.sprite(now)
        h, w = spr.shape[:2]
        out = spr * rig.body[..., None]
        if self.params["legs"]:
            for i, g in enumerate(rig.legs):
                leg = spr * g.mask[..., None]
                M = cv2.getRotationMatrix2D(g.root, self.leg_angle(i, now), 1.0)
                out = np.maximum(out, cv2.warpAffine(leg, M, (w, h), flags=cv2.INTER_LINEAR))
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
        layer = (np.clip(self.compose(now), 0, 1) * 255).astype(np.uint8)
        if abs(sx - 1) > 1e-6 or abs(sy - 1) > 1e-6:
            layer = cv2.resize(layer, (max(1, int(round(rig.w * sx))), max(1, int(round(rig.h * sy)))),
                               interpolation=cv2.INTER_AREA)
        add_layer(img, layer, int(round(rig.x0 * sx)), int(round(rig.y0 * sy)))

    def resolve(self, index: int | None):
        if self.rig is None:
            return None
        if index is None or not (0 <= index < len(self.rig.legs)):
            c = self.rig.center_local()
            return (c[0] + self.rig.x0, c[1] + self.rig.y0)
        r = self.rig.legs[index].root
        return (r[0] + self.rig.x0, r[1] + self.rig.y0)

    def status(self) -> dict:
        r = self.rig
        return {"kind": self.kind, "patas": [g.to_json(r.x0, r.y0) for g in r.legs] if r else [],
                "escamas": r.n_cells if r else 0, "zonas": r.z.n if r else 0,
                "respiracion": {"escala": round(self.breath_now, 4), "fuente": getattr(self, "breath_src_used", None)},
                "bbox": [r.x0, r.y0, r.w, r.h] if r else None}
