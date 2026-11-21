"""Registro de instrumentos de una escena: estado que sobrevive al hot reload, reparto de toques y comandos.

Vive colgado de la escena (`registry_of(scene)`), así cada runtime tiene el suyo. Los efectos de
`playground/effects/` piden su instrumento por id de instancia (`get(iid, factory)`): si el archivo del efecto se
recarga, la instancia nueva recibe el mismo instrumento (notas sostenidas, trazos y pulsos siguen).

Reparto: un toque pertenece al instrumento que lo reclama en su "down" (el de mayor `priority` cuyo `hit` da True:
cocodrilo 30 > marimba 20 > dibujo 10); sus move/up van solo a ese dueño. La linterna no reclama toques.
"""

from __future__ import annotations

import time
from typing import Callable

from .instrument import Instrument


class Registry:
    def __init__(self):
        self.instruments: dict[str, Instrument] = {}
        self.claims: dict[str, str | None] = {}
        self.flags: dict[str, float] = {}              # pedidos para el lazo en vivo (p.ej. "recalibrar")
        self.live_status: dict = {}                    # lo publica el lazo en vivo (fusión, cámaras)

    # ------------------------------------------------------------------ instrumentos
    def get(self, iid: str, factory: Callable[[], Instrument], last_seq: int = 0) -> Instrument:
        inst = self.instruments.get(iid)
        if inst is None:
            inst = factory()
            inst.last_seq = last_seq                   # no repite toques viejos
            self.instruments[iid] = inst
        return inst

    def drop(self, iid: str) -> None:
        inst = self.instruments.pop(iid, None)
        if inst is not None:
            inst.close()
        for k in [k for k, v in self.claims.items() if v == iid]:
            del self.claims[k]

    # ------------------------------------------------------------------ toques
    def owner(self, ev: dict) -> str | None:
        tid = str(ev.get("id"))
        if ev.get("state") == "down" or tid not in self.claims:
            best, best_p = None, None
            for iid, inst in self.instruments.items():
                if inst.claims_touch and (best_p is None or inst.priority > best_p) and inst.hit(float(ev["x"]), float(ev["y"])):
                    best, best_p = iid, inst.priority
            self.claims[tid] = best
            if len(self.claims) > 512:                 # toques viejos que nunca mandaron up
                for k in list(self.claims)[:256]:
                    del self.claims[k]
        return self.claims.get(tid)

    def pump(self, iid: str, touch_log: list[dict], now: float | None = None) -> int:
        """Entrega al instrumento `iid` los toques nuevos que le pertenecen. Devuelve cuántos procesó."""
        inst = self.instruments.get(iid)
        if inst is None:
            return 0
        now = time.time() if now is None else now
        n = 0
        for ev in touch_log:
            if ev.get("seq", 0) <= inst.last_seq:
                continue
            inst.last_seq = ev["seq"]
            if inst.claims_touch:
                if self.owner(ev) == iid:
                    inst.handle(ev, now)
                    n += 1
            elif getattr(inst, "observes_touch", False):        # mira todos los toques sin quitárselos a nadie
                inst.handle(ev, now)
                n += 1
        return n

    # ------------------------------------------------------------------ consultas y comandos
    def by_kind(self, kind: str) -> list[tuple[str, Instrument]]:
        return [(k, v) for k, v in self.instruments.items() if v.kind == kind]

    def resolve(self, target: str) -> tuple[float, float] | None:
        """'croc:3' / 'cocodrilo:3' / 'marimba:5' / 'cocodrilo' / 'marimba' → (x, y) px del proyector."""
        name, _, idx = target.partition(":")
        kind = {"croc": "cocodrilo", "cocodrilo": "cocodrilo", "marimba": "marimba", "dibujo": "dibujo",
                "objeto": "objeto", "memoria": "memoria"}.get(name.lower())
        index = int(idx) if idx.strip().lstrip("-").isdigit() else None
        if kind is None:
            # objeto genérico por id: 'buda:3' = zona 3 del instrumento sobre el Buda (objeto_synth antes que objeto_vivo)
            cands = sorted((inst for inst in self.instruments.values()
                            if str(getattr(inst, "obj_id", "") or "").lower() == name.lower()),
                           key=lambda i: -i.priority)
            for inst in cands:
                xy = inst.resolve(index)
                if xy is not None:
                    return (float(xy[0]), float(xy[1]))
            return None
        for _, inst in self.by_kind(kind):
            xy = inst.resolve(index)
            if xy is not None:
                return (float(xy[0]), float(xy[1]))
        return None

    def status(self) -> dict:
        return {"instrumentos": {k: v.status() for k, v in self.instruments.items()},
                "toques_con_dueno": sum(1 for v in self.claims.values() if v), "en_vivo": self.live_status}

    def command(self, action: str, args: dict | None = None) -> dict:
        if action == "status":
            return self.status()
        if action == "clear":
            n = 0
            for _, inst in self.by_kind("dibujo"):
                inst.clear(time.time(), "comando")
                n += 1
            return {"borrados": n}
        if action == "panic":
            for inst in self.instruments.values():
                inst.notes.panic()
            return {"panic": True, "instrumentos": len(self.instruments)}
        if action == "recalibrar":
            self.flags["recalibrar"] = time.time()
            return {"recalibrar": "pedido: el lazo en vivo vuelve a medir la proyección con la Osmo"}
        if action == "sonido":                      # por dónde suena: auto | pc | mac | virtual | null | "pc+virtual"…
            from .music import MidiOut, get_midi, set_midi
            salida = str((args or {}).get("salida") or "")
            if salida:
                for inst in self.instruments.values():
                    inst.notes.panic()
                get_midi().close()
                m = MidiOut(salida)
                m.send("control_change", 16, 123, 0)        # abre los destinos ya (all notes off en el canal 16)
                set_midi(m)
            return get_midi().status()
        if action == "en_vivo":                     # el lazo en vivo (otro proceso) publica su estado y lee pedidos
            self.live_status = (args or {}).get("status") or {}
            return {"recalibrar": self.flags.pop("recalibrar", None) is not None}
        raise ValueError(f"acción desconocida: {action} (status | clear | panic | recalibrar | sonido | en_vivo)")


class TouchState:
    """Toques y manos de una escena (los llena el runtime con los comandos "touch" y "hands"). Vive colgado del
    objeto escena (sin campos nuevos en `Scene`): sirve con la escena demo y con la del escaneo."""

    MAX_LOG = 2000

    def __init__(self):
        self.touches: dict[str, dict] = {}       # toques vivos: id → último down/move
        self.log: list[dict] = []                # últimos eventos con "seq" (los instrumentos los consumen)
        self.seq = 0
        self.hands: dict[str, dict] = {}         # manos sobre la mesa (linterna): id → {x, y, height, dir…}

    def add(self, ev: dict) -> dict:
        self.seq += 1
        ev = {**ev, "seq": self.seq}
        self.log.append(ev)
        if len(self.log) > self.MAX_LOG:
            del self.log[: len(self.log) - self.MAX_LOG]
        if ev["state"] == "up":
            self.touches.pop(ev["id"], None)
        else:
            self.touches[ev["id"]] = ev
        return ev

    def to_json(self) -> dict:
        return {"touches": dict(self.touches), "hands": dict(self.hands), "seq": self.seq}


def touch_state(scene) -> TouchState:
    ts = scene.__dict__.get("_touch")
    if ts is None:
        ts = TouchState()
        scene.__dict__["_touch"] = ts
    return ts


def registry_of(scene) -> Registry:
    reg = scene.__dict__.get("_touchsynth")
    if reg is None:
        reg = Registry()
        scene.__dict__["_touchsynth"] = reg
    return reg
