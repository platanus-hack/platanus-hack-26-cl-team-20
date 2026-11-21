"""Fusión de cámaras para la mesa táctil: la Osmo da x,y precisos en el proyector, la Brio da si el dedo está apoyado.

Entradas (cada una con su hora; se descartan si tienen más de `stale_s`):
- **Osmo** (`update_osmo`): puntas `OsmoTip` en px del proyector (de `osmo_hands.py`), con `gap` = distancia punta ↔
  punta de su sombra (~0 al tocar) cuando la sombra se ve.
- **Brio** (`update_brio`): `BrioObs` por mano desde MediaPipe (`windows/hands_service.py`) + la calibración Brio →
  proyector (`playground/hands/calib.py`): punta en px del proyector (aproximada: la homografía es del plano de la
  mesa, un dedo levantado cae corrido), contacto por pinch o por apoyo (tamaño de la palma en el plano, con
  histéresis, como `hands/touch.py`) y altura relativa 0..1. Sin calibración la Brio solo aporta contacto y se
  asocia si hay una mano en cada cámara.

Modos (en `status()["modo"]`, y `degradado` cuando falta una cámara):
- **fusion**: cada punta de la Osmo se asocia a la mano de la Brio más cercana (< `assoc_px`): posición de la Osmo,
  contacto y altura de la Brio. Una punta sin pareja usa su sombra; una mano de la Brio sin pareja (fuera de la
  vista de la Osmo) entra con su propia posición.
- **solo-osmo**: contacto por sombra (`gap` < `gap_touch`; si la sombra desaparece justo después de estar pegada
  al dedo, sigue tocando: el dedo la tapa). Si nunca hubo sombra visible (zona sin luz del proyector), un dedo
  quieto `dwell_s` (0,3 s) es un toque y sigue tocando mientras se vea (`osmo_contact`): un cuerpo que pasa no toca.
- **solo-brio**: posición por homografía y contacto de la Brio (lo mismo que `python -m playground.hands run`).
- **sin-camaras**: nada.

Salida: eventos down/move/up con el formato del comando "touch" (ids `f1, f2…`, `source` brio|osmo|fusion,
`trigger`, `velocity` 0..1 sacada de qué tan rápido bajó el dedo, `height`) y la lista de manos (`hands()`) para
la linterna (centro de la mano, dirección, altura).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .osmo_hands import OsmoTip


@dataclass
class BrioObs:
    id: str
    x: float | None                       # punta del índice en px del proyector (None sin calibración)
    y: float | None
    contact: bool | None                  # apoyado / pinch (None = la Brio no sabe)
    height: float | None = None           # 0 (en la mesa) … 1 (alto)
    hand_x: float | None = None
    hand_y: float | None = None
    handed: str = "?"
    trigger: str = ""
    ratio: float | None = None
    t: float = 0.0


class BrioContact:
    """Manos de MediaPipe (px de la Brio) → `BrioObs` en px del proyector con contacto por pinch/apoyo."""

    def __init__(self, cal=None, mode: str = "auto", ratio_down: float = 1.06, ratio_up: float = 1.12,
                 hand_ref: float | None = None, height_full: float = 0.25):
        self.cal = cal
        self.mode = mode
        self.ratio_down, self.ratio_up = ratio_down, ratio_up
        self.ref = hand_ref or (getattr(cal, "hand_ref", None) if cal is not None else None)
        self.height_full = height_full                   # h/H que se considera "alto" (1.0)
        self.apoyo: dict[str, bool] = {}
        self._sizes: deque[float] = deque(maxlen=300)

    @property
    def calibrated(self) -> bool:
        return self.cal is not None

    def hand_ref(self) -> float | None:
        if self.ref:
            return self.ref
        if len(self._sizes) >= 30:
            return float(np.percentile(self._sizes, 10))
        return None

    def observe(self, hands, t: float) -> list[BrioObs]:
        from ..hands.model import palm_size
        out = []
        for h in hands:
            key = str(h.id)
            lm = np.asarray(h.landmarks, np.float64)
            x = y = hx = hy = None
            ratio = height = None
            apoyo = False
            if self.cal is not None:
                tip = self.cal.brio_to_proj([h.tip_index])[0]
                x, y = float(tip[0]), float(tip[1])
                palm = self.cal.brio_to_proj(lm[[0, 5, 9, 13, 17], :2]).mean(axis=0)
                hx, hy = float(palm[0]), float(palm[1])
                size = palm_size(self.cal.brio_to_proj(lm[:, :2]))
                self._sizes.append(size)
                ref = self.hand_ref()
                if ref:
                    ratio = size / ref
                    prev = self.apoyo.get(key, False)
                    apoyo = ratio < (self.ratio_up if prev else self.ratio_down)
                    height = float(np.clip((1 - 1 / max(ratio, 1e-6)) / self.height_full, 0, 1))
            self.apoyo[key] = apoyo
            pinch = bool(h.pinch)
            if self.mode == "pinch":
                contact, trig = pinch, "pinch"
            elif self.mode == "apoyo":
                contact, trig = (apoyo if ratio is not None else None), "apoyo"
            else:
                contact = pinch or apoyo if (ratio is not None or pinch) else None
                trig = "pinch" if pinch else "apoyo"
            out.append(BrioObs(key, x, y, contact, height, hx, hy, h.handed, trig, ratio, t))
        return out


@dataclass
class FusionConfig:
    assoc_px: float = 120.0               # punta Osmo ↔ punta Brio (px del proyector)
    stale_s: float = 0.35
    gap_touch: float = 12.0               # px del proyector
    gap_release: float = 22.0
    gap_memory_s: float = 0.5
    osmo_contact: str = "auto"            # auto (sombra si alguna vez se vio, si no presencia) | sombra | presencia
    dwell_s: float = 0.3                  # presencia: el dedo quieto esto…
    dwell_px: float = 15.0                # …sin moverse más que esto = toque
    frames_down: int = 2
    frames_up: int = 2
    ema: float = 0.6
    deadband: float = 2.5
    max_jump: float = 160.0
    lost_s: float = 0.25
    velocity_default: float = 0.75
    projector: str = "proj0"


@dataclass
class _Track:
    id: str
    x: float
    y: float
    last_seen: float
    emitted: tuple[float, float] | None = None
    touching: bool = False
    cnt_on: int = 0
    cnt_off: int = 0
    source: str = ""
    trigger: str = ""
    hand: str = ""
    handed: str = "?"
    dir: tuple[float, float] | None = None
    hand_xy: tuple[float, float] | None = None
    height: float | None = None
    touch_id: str = ""
    anchor: tuple[float, float, float] = (0.0, 0.0, 0.0)                # (x, y, desde cuándo está quieto)
    gaps: deque = field(default_factory=lambda: deque(maxlen=20))       # (t, gap)
    heights: deque = field(default_factory=lambda: deque(maxlen=20))    # (t, altura)


class Fusion:
    def __init__(self, cfg: FusionConfig | None = None, emit: Callable[[dict], None] | None = None):
        self.cfg = cfg or FusionConfig()
        self.emit = emit
        self.osmo: list[OsmoTip] = []
        self.brio: list[BrioObs] = []
        self.t_osmo: float | None = None
        self.t_brio: float | None = None
        self.brio_calibrated = False
        self.tracks: dict[str, _Track] = {}
        self._n_track = 0
        self._n_touch = 0
        self.shadow_seen: float | None = None
        self.counts = {"down": 0, "move": 0, "up": 0}
        self.last_mode = "sin-camaras"

    # ------------------------------------------------------------------ entradas
    def update_osmo(self, tips: list[OsmoTip], t: float) -> None:
        self.osmo, self.t_osmo = list(tips), t
        if any(tp.gap is not None for tp in tips):
            self.shadow_seen = t

    def update_brio(self, obs: list[BrioObs], t: float, calibrated: bool | None = None) -> None:
        self.brio, self.t_brio = list(obs), t
        if calibrated is not None:
            self.brio_calibrated = calibrated

    def mark_alive(self, source: str, t: float) -> None:
        """La cámara mandó un cuadro sin manos: sigue viva (no es lo mismo que caída)."""
        if source == "osmo":
            self.osmo, self.t_osmo = [], t
        else:
            self.brio, self.t_brio = [], t

    def alive(self, now: float) -> tuple[bool, bool]:
        c = self.cfg
        return (self.t_osmo is not None and now - self.t_osmo <= c.stale_s,
                self.t_brio is not None and now - self.t_brio <= c.stale_s)

    def mode(self, now: float) -> str:
        o, b = self.alive(now)
        return "fusion" if (o and b) else "solo-osmo" if o else "solo-brio" if b else "sin-camaras"

    # ------------------------------------------------------------------ candidatos
    def candidates(self, now: float) -> list[dict]:
        o_ok, b_ok = self.alive(now)
        osmo = self.osmo if o_ok else []
        brio = self.brio if b_ok else []
        pairs: dict[int, int] = {}
        if osmo and brio:
            dists = []
            for i, tp in enumerate(osmo):
                for j, ob in enumerate(brio):
                    if ob.x is None:
                        continue
                    d = math.hypot(tp.x - ob.x, tp.y - ob.y)
                    if ob.hand_x is not None:                     # la palma se corre menos que la punta al levantarla
                        d = min(d, math.hypot(tp.hand_x - ob.hand_x, tp.hand_y - ob.hand_y))
                    if d <= self.cfg.assoc_px:
                        dists.append((d, i, j))
            used_j = set()
            for d, i, j in sorted(dists):
                if i not in pairs and j not in used_j:
                    pairs[i] = j
                    used_j.add(j)
            if not pairs and len(osmo) == 1 and len(brio) == 1 and brio[0].x is None:
                pairs[0] = 0                                       # Brio sin calibrar: una mano en cada cámara
        out = []
        for i, tp in enumerate(osmo):
            c = {"x": tp.x, "y": tp.y, "gap": tp.gap, "lit": tp.lit, "dir": tp.dir, "hand_xy": (tp.hand_x, tp.hand_y),
                 "brio_contact": None, "height": None, "hand": "", "handed": "?", "source": "osmo", "trigger": ""}
            if i in pairs:
                ob = brio[pairs[i]]
                c.update(brio_contact=ob.contact, height=ob.height, hand=ob.id, handed=ob.handed, source="fusion",
                         trigger=ob.trigger)
            out.append(c)
        paired_j = set(pairs.values())
        for j, ob in enumerate(brio):
            if j in paired_j or ob.x is None:
                continue
            out.append({"x": ob.x, "y": ob.y, "gap": None, "lit": 0.0, "dir": None,
                        "hand_xy": None if ob.hand_x is None else (ob.hand_x, ob.hand_y), "brio_contact": ob.contact,
                        "height": ob.height, "hand": ob.id, "handed": ob.handed, "source": "brio", "trigger": ob.trigger})
        return out

    # ------------------------------------------------------------------ contacto por pista
    def _contact(self, tr: _Track, c: dict, now: float) -> tuple[bool, str]:
        cfg = self.cfg
        if c["gap"] is not None:
            tr.gaps.append((now, c["gap"]))
        if c["height"] is not None:
            tr.heights.append((now, c["height"]))
        if c["brio_contact"] is not None:
            return bool(c["brio_contact"]), c["trigger"] or "brio"
        if c["source"] == "brio":
            return False, ""
        mode = cfg.osmo_contact
        if mode == "presencia" or (mode == "auto" and self.shadow_seen is None):
            # sin altura: un dedo quieto `dwell_s` sobre la mesa es un toque (un cuerpo que pasa no); ya tocando,
            # sigue tocando mientras se vea (así se puede dibujar)
            return tr.touching or (now - tr.anchor[2] >= cfg.dwell_s), "presencia"
        gap = c["gap"]
        if gap is not None:
            return gap < (cfg.gap_release if tr.touching else cfg.gap_touch), "sombra"
        recent = [g for t, g in tr.gaps if now - t <= cfg.gap_memory_s]
        if recent and recent[-1] < 2 * cfg.gap_touch:
            return True, "sombra"                                  # la sombra se juntó con el dedo y quedó tapada
        if tr.touching and tr.trigger == "sombra":
            return True, "sombra"                                  # sigue apoyado mientras la sombra no reaparezca
        return False, "sombra"

    def _velocity(self, tr: _Track, now: float) -> float:
        hs = [(t, h) for t, h in tr.heights if now - t <= 0.3]
        if len(hs) >= 2:
            drop = max(h for _, h in hs) - hs[-1][1]
            return float(np.clip(0.35 + 2.0 * drop / 0.3 * 0.3, 0.2, 1.0))
        gs = [(t, g) for t, g in tr.gaps if now - t <= 0.3]
        if len(gs) >= 2:
            rate = (max(g for _, g in gs) - gs[-1][1]) / max(now - gs[0][0], 0.05)
            return float(np.clip(0.35 + rate / 300.0, 0.2, 1.0))
        return self.cfg.velocity_default

    # ------------------------------------------------------------------ paso
    def step(self, now: float) -> list[dict]:
        cfg = self.cfg
        self.last_mode = self.mode(now)
        cands = self.candidates(now)
        # asociar candidatos a pistas (vecino más cercano, greedy)
        pairs = []
        for k, tr in self.tracks.items():
            for i, c in enumerate(cands):
                d = math.hypot(c["x"] - tr.x, c["y"] - tr.y)
                if d <= cfg.max_jump:
                    pairs.append((d, k, i))
        used_t, used_c = set(), set()
        assign: dict[int, str] = {}
        for d, k, i in sorted(pairs):
            if k not in used_t and i not in used_c:
                assign[i] = k
                used_t.add(k)
                used_c.add(i)
        events: list[dict] = []
        for i, c in enumerate(cands):
            k = assign.get(i)
            if k is None:
                self._n_track += 1
                k = f"h{self._n_track}"
                self.tracks[k] = _Track(k, c["x"], c["y"], now)
            tr = self.tracks[k]
            tr.x = cfg.ema * c["x"] + (1 - cfg.ema) * tr.x
            tr.y = cfg.ema * c["y"] + (1 - cfg.ema) * tr.y
            tr.last_seen = now
            tr.source = c["source"]
            tr.hand, tr.handed = c["hand"] or k, c["handed"]
            tr.dir, tr.hand_xy, tr.height = c["dir"], c["hand_xy"], c["height"]
            if k not in used_t or math.hypot(tr.x - tr.anchor[0], tr.y - tr.anchor[1]) > cfg.dwell_px:
                tr.anchor = (tr.x, tr.y, now)
            contact, trig = self._contact(tr, c, now)
            if not tr.touching:
                tr.cnt_on = tr.cnt_on + 1 if contact else 0
                if tr.cnt_on >= cfg.frames_down:
                    tr.touching, tr.cnt_on, tr.cnt_off = True, 0, 0
                    tr.trigger = trig
                    self._n_touch += 1
                    tr.touch_id = f"f{self._n_touch}"
                    tr.emitted = (tr.x, tr.y)
                    events.append(self._event(tr, "down", now, velocity=self._velocity(tr, now)))
                continue
            tr.cnt_off = tr.cnt_off + 1 if not contact else 0
            if tr.cnt_off >= cfg.frames_up:
                events.append(self._event(tr, "up", now))
                tr.touching, tr.cnt_off = False, 0
                continue
            if math.hypot(tr.x - tr.emitted[0], tr.y - tr.emitted[1]) >= cfg.deadband:
                tr.emitted = (tr.x, tr.y)
                events.append(self._event(tr, "move", now))
        for k in list(self.tracks):
            tr = self.tracks[k]
            if k in used_t or tr.last_seen == now or now - tr.last_seen <= cfg.lost_s:
                continue
            if tr.touching:
                events.append(self._event(tr, "up", now, reason="mano perdida"))
            del self.tracks[k]
        for ev in events:
            self.counts[ev["state"]] += 1
            if self.emit is not None:
                self.emit(ev)
        return events

    def _event(self, tr: _Track, state: str, now: float, velocity: float | None = None, reason: str = "") -> dict:
        x, y = tr.emitted if tr.emitted is not None else (tr.x, tr.y)
        ev = {"id": tr.touch_id, "state": state, "x": round(x, 1), "y": round(y, 1), "space": "proj",
              "projector": self.cfg.projector, "mm": None, "hand": tr.hand, "handed": tr.handed,
              "trigger": tr.trigger, "t": round(now, 4), "source": tr.source,
              "height": None if tr.height is None else round(tr.height, 3)}
        if velocity is not None:
            ev["velocity"] = round(velocity, 3)
        if reason:
            ev["reason"] = reason
        return ev

    # ------------------------------------------------------------------ salidas
    def hands(self, now: float) -> list[dict]:
        """Manos vistas ahora (toquen o no): para la linterna."""
        out = []
        for tr in self.tracks.values():
            if now - tr.last_seen > self.cfg.lost_s:
                continue
            hx, hy = tr.hand_xy if tr.hand_xy is not None else (tr.x, tr.y)
            out.append({"id": tr.id, "x": round(hx, 1), "y": round(hy, 1), "tip": [round(tr.x, 1), round(tr.y, 1)],
                        "height": tr.height if tr.height is not None else (0.0 if tr.touching else None),
                        "dir": list(tr.dir) if tr.dir else None, "source": tr.source, "touching": tr.touching,
                        "t": round(now, 4)})
        return out

    def status(self, now: float) -> dict:
        mode = self.mode(now)
        detail = {
            "fusion": "Osmo da la posición, Brio el apoyo",
            "solo-osmo": ("solo Osmo: contacto por sombra del dedo" if self.shadow_seen is not None and self.cfg.osmo_contact != "presencia"
                          else "solo Osmo sin sombra visible: dedo quieto 0,3 s = toque (sin altura)"),
            "solo-brio": "solo Brio: posición por homografía de la mesa (menos precisa), contacto por pinch/apoyo"
                         if self.brio_calibrated else "solo Brio sin calibrar: no hay posición en el proyector",
            "sin-camaras": "ninguna cámara entrega cuadros",
        }[mode]
        o_ok, b_ok = self.alive(now)
        return {"modo": mode, "degradado": mode != "fusion", "detalle": detail,
                "osmo": {"viva": o_ok, "puntas": len(self.osmo) if o_ok else 0,
                         "edad_s": None if self.t_osmo is None else round(now - self.t_osmo, 2)},
                "brio": {"viva": b_ok, "manos": len(self.brio) if b_ok else 0, "calibrada": self.brio_calibrated,
                         "edad_s": None if self.t_brio is None else round(now - self.t_brio, 2)},
                "pistas": len(self.tracks), "tocando": sum(1 for t in self.tracks.values() if t.touching),
                "eventos": dict(self.counts)}
