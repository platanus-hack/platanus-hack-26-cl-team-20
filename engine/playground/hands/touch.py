"""Lógica táctil: manos de la Brio → toques (down / move / up) en px del proyector y, si hay métrica, en mm.

Posición: la punta del índice (landmark 8) en px de la Brio pasa por la homografía del plano de la mesa
(`calib.Calibration.brio_to_proj`), se suaviza con EMA y solo se mueve si cambió ≥ `deadband_px` (3 px): el temblor
del detector no genera eventos.

Cuándo hay toque (`mode`):
- **"pinch"**: pulgar e índice juntos (señal `pinch` del servicio, o recalculada aquí con histéresis). Gesto
  explícito y robusto; no exige tocar la mesa.
- **"apoyo"**: la mano apoyada en la mesa. Con una sola cámara en ángulo no hay profundidad, así que se usa el
  tamaño aparente de la mano contra la distancia a la cámara: se mapea la palma (puntos rígidos: muñeca, nudillos)
  al plano de la mesa con la misma homografía. Si la mano está sobre la mesa, ese tamaño es el de su palma real (en
  px de proyector) y no depende de dónde esté; si se levanta hacia la cámara, se agranda (≈ H/(H − h), H = altura
  de la cámara sobre la mesa). `ratio = tamaño / referencia apoyada`: down si ratio < `ratio_down`, up si
  > `ratio_up` (histéresis), más `frames_down/up` cuadros seguidos (antirrebote). La referencia sale de la
  calibración (`python -m playground.hands rest`) o se aprende sola (percentil bajo de lo observado). La z de
  MediaPipe es relativa a la muñeca (no da distancia a la cámara); `z_weight` la suma como señal secundaria (0 por
  defecto). Límite físico: con la cámara a ~1,5 m, 3 cm de altura cambian el tamaño ~2 %, cerca del ruido; la
  discriminación fina de "rozando vs apoyado" necesita otra vista o profundidad (ver README).
- **"auto"**: cualquiera de las dos.

Zonas excluidas (`Zones`): polígonos en px de proyector de `captures/scan2/workbench.json` o `segments.json`
(contour > quad > bbox por objeto; la mesa y la hoja no se excluyen). Un toque que empezaría dentro de una zona no
se emite (apoyar la mano en el cocodrilo no es un toque); un arrastre que ya empezó en la mesa sigue aunque cruce.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .calib import Calibration
from .model import Hand, palm_size, pinch_ratio

NOT_EXCLUDED = ("mesa", "papel")


# ---------------------------------------------------------------------- zonas

class Zones:
    """Polígonos (px de proyector) donde no empieza un toque."""

    def __init__(self, polys: dict[str, np.ndarray] | None = None, margin: float = 8.0):
        self.polys = {k: np.asarray(v, np.float32).reshape(-1, 1, 2) for k, v in (polys or {}).items()}
        self.margin = margin

    @staticmethod
    def polygon_of(obj: dict) -> np.ndarray | None:
        for key in ("contour", "quad"):
            pts = obj.get(key)
            if pts is not None and len(pts) >= 3:
                return np.asarray(pts, np.float32).reshape(-1, 2)
        bb = obj.get("bbox")
        if bb is not None and len(bb) == 4:
            x, y, w, h = (float(v) for v in bb)
            return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], np.float32)
        return None

    @classmethod
    def from_file(cls, path: str | Path, names: list[str] | None = None, skip=NOT_EXCLUDED,
                  margin: float = 8.0) -> "Zones":
        """`names`: solo esos objetos; si no, todos menos `skip`. Acepta workbench.json y segments.json."""
        data = json.loads(Path(path).read_text())
        polys = {}
        for name, obj in data.items():
            if not isinstance(obj, dict) or (names and name not in names) or (not names and name in skip):
                continue
            poly = cls.polygon_of(obj)
            if poly is not None:
                polys[name] = poly
        return cls(polys, margin)

    def contains(self, xy) -> str | None:
        x, y = float(xy[0]), float(xy[1])
        for name, poly in self.polys.items():
            if cv2.pointPolygonTest(poly, (x, y), True) >= -self.margin:
                return name
        return None

    def __len__(self) -> int:
        return len(self.polys)


# ---------------------------------------------------------------------- eventos y configuración

@dataclass
class TouchConfig:
    mode: str = "auto"                      # "pinch" | "apoyo" | "auto"
    pinch_source: str = "msg"               # "msg" (pinch del servicio) | "landmarks" (histéresis propia)
    pinch_on: float = 0.30                  # pulgar–índice / palma para cerrar el pinch…
    pinch_off: float = 0.42                 # …y para soltarlo
    ratio_down: float = 1.06                # palma en el plano / palma apoyada
    ratio_up: float = 1.12
    frames_down: int = 2
    frames_up: int = 2
    hand_ref: float | None = None           # palma apoyada en px de proyector (None: calib.hand_ref o aprendida)
    auto_ref_window: int = 300              # muestras para aprender la referencia
    auto_ref_pct: float = 10.0
    auto_ref_min: int = 30
    z_weight: float = 0.0                   # suma z_punta/palma (MediaPipe, relativa a la muñeca) al ratio
    ema_alpha: float = 0.5
    deadband_px: float = 3.0
    lost_s: float = 0.3                     # mano perdida más que esto → up


@dataclass
class TouchEvent:
    id: str                                 # id del toque (nuevo en cada down)
    state: str                              # "down" | "move" | "up"
    x: float                                # px de proyector
    y: float
    t: float
    mm: tuple[float, float] | None = None
    hand: str = ""                          # id de la mano en el servicio
    handed: str = "?"
    trigger: str = ""                       # "pinch" | "apoyo"
    ratio: float | None = None
    brio: tuple[float, float] | None = None
    reason: str = ""

    def to_args(self, projector: str = "proj0", source: str = "brio") -> dict:
        """Argumentos del comando "touch" del runtime (ver bridge.py)."""
        return {"id": self.id, "state": self.state, "x": round(self.x, 1), "y": round(self.y, 1),
                "space": "proj", "projector": projector,
                "mm": None if self.mm is None else [round(self.mm[0], 1), round(self.mm[1], 1)],
                "hand": self.hand, "handed": self.handed, "trigger": self.trigger, "t": round(self.t, 4),
                "source": source, **({"reason": self.reason} if self.reason else {})}


@dataclass
class _HandState:
    smooth: np.ndarray | None = None
    emitted: np.ndarray | None = None
    touching: bool = False
    blocked: str | None = None
    pinch: bool = False
    apoyo: bool = False
    cnt_on: int = 0
    cnt_off: int = 0
    last_seen: float = 0.0
    handed: str = "?"
    trigger: str = ""
    ratio: float | None = None
    touch_id: str = ""
    tip_brio: tuple[float, float] | None = None


# ---------------------------------------------------------------------- tracker

class TouchTracker:
    def __init__(self, cal: Calibration, config: TouchConfig | None = None, zones: Zones | None = None):
        self.cal = cal
        self.cfg = config or TouchConfig()
        self.zones = zones
        self.states: dict[str, _HandState] = {}
        self._sizes: deque[float] = deque(maxlen=self.cfg.auto_ref_window)
        self._n = 0

    # ---- referencia de palma apoyada
    @property
    def hand_ref(self) -> float | None:
        if self.cfg.hand_ref:
            return self.cfg.hand_ref
        if self.cal.hand_ref:
            return self.cal.hand_ref
        if len(self._sizes) >= self.cfg.auto_ref_min:
            return float(np.percentile(self._sizes, self.cfg.auto_ref_pct))
        return None

    def plane_palm(self, h: Hand) -> float:
        return palm_size(self.cal.brio_to_proj(h.landmarks[:, :2]))

    # ---- señales
    def _signals(self, h: Hand, st: _HandState) -> tuple[bool, str]:
        cfg = self.cfg
        if cfg.pinch_source == "landmarks":
            r = pinch_ratio(h.landmarks)
            st.pinch = r < (cfg.pinch_off if st.pinch else cfg.pinch_on)
        else:
            st.pinch = bool(h.pinch)
        size = self.plane_palm(h)
        ref = self.hand_ref
        self._sizes.append(size)
        st.ratio = None
        if ref:
            ratio = size / ref
            if cfg.z_weight:
                ratio += cfg.z_weight * float(h.landmarks[8, 2]) / max(palm_size(h.landmarks), 1e-6)
            st.ratio = ratio
            st.apoyo = ratio < (cfg.ratio_up if st.apoyo else cfg.ratio_down)
        else:
            st.apoyo = False
        if cfg.mode == "pinch":
            return st.pinch, "pinch"
        if cfg.mode == "apoyo":
            return st.apoyo, "apoyo"
        return st.pinch or st.apoyo, ("pinch" if st.pinch else "apoyo")

    def _event(self, st: _HandState, key: str, state: str, pos, t: float, reason: str = "") -> TouchEvent:
        mm = self.cal.proj_to_mm([pos])
        return TouchEvent(st.touch_id, state, float(pos[0]), float(pos[1]), t,
                          None if mm is None else (float(mm[0, 0]), float(mm[0, 1])), key, st.handed, st.trigger,
                          None if st.ratio is None else round(st.ratio, 3), st.tip_brio, reason)

    # ---- paso
    def update(self, hands: list[Hand], t: float) -> list[TouchEvent]:
        cfg = self.cfg
        events: list[TouchEvent] = []
        seen = set()
        for h in hands:
            key = str(h.id)
            seen.add(key)
            st = self.states.setdefault(key, _HandState())
            st.last_seen = t
            st.handed = h.handed
            st.tip_brio = (float(h.tip_index[0]), float(h.tip_index[1]))
            tip = self.cal.brio_to_proj([h.tip_index])[0]
            st.smooth = tip if st.smooth is None else cfg.ema_alpha * tip + (1 - cfg.ema_alpha) * st.smooth
            contact, trigger = self._signals(h, st)

            if not st.touching:
                st.cnt_on = st.cnt_on + 1 if contact else 0
                if st.cnt_on >= cfg.frames_down:
                    st.touching, st.cnt_on, st.cnt_off = True, 0, 0
                    st.trigger = trigger
                    st.emitted = st.smooth.copy()
                    st.blocked = self.zones.contains(st.smooth) if self.zones else None
                    if st.blocked is None:
                        self._n += 1
                        st.touch_id = f"t{self._n}"
                        events.append(self._event(st, key, "down", st.emitted, t))
                continue

            st.cnt_off = st.cnt_off + 1 if not contact else 0
            if st.cnt_off >= cfg.frames_up:
                if st.blocked is None:
                    events.append(self._event(st, key, "up", st.emitted, t))
                st.touching, st.blocked, st.cnt_off = False, None, 0
                continue
            if st.blocked is None and np.linalg.norm(st.smooth - st.emitted) >= cfg.deadband_px:
                st.emitted = st.smooth.copy()
                events.append(self._event(st, key, "move", st.emitted, t))

        for key in list(self.states):
            st = self.states[key]
            if key in seen or t - st.last_seen <= cfg.lost_s:
                continue
            if st.touching and st.blocked is None:
                events.append(self._event(st, key, "up", st.emitted, t, reason="mano perdida"))
            del self.states[key]
        return events

    def active(self) -> dict[str, dict]:
        """Toques vivos: id del toque → posición y mano."""
        return {st.touch_id: {"x": float(st.emitted[0]), "y": float(st.emitted[1]), "hand": k, "handed": st.handed}
                for k, st in self.states.items() if st.touching and st.blocked is None and st.emitted is not None}
