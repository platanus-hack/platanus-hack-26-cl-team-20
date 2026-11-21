"""Registro de dispositivos y controles, fuentes de eventos MIDI y bindings.

Un `Control` es una perilla, fader, pad, botón o crossfader con valor normalizado 0..1 y posición en la
skin del dispositivo (coordenadas del SVG; en mm de la mesa cuando el fit del pedido 003 lo ubique).
Las fuentes entregan eventos `{"device", "control", "value" (0..1), "state": down|up|move}` al registro,
que actualiza el control, aplica los bindings y publica el evento al runtime.

Fuentes:
- `SmartDeckSource`: se suscribe al WebSocket de SmartDeck (ws://localhost:8090) y traduce sus mensajes
  `midi` (knob/fader/pad/button/crossfader). SmartDeck sigue dueño del puerto y de los LEDs.
- `DirectMidiSource`: abre un puerto MIDI con mido/python-rtmidi (cuando nadie más lo tiene).
- `FakeSource`: para tests y para simular desde la secretaria (`pg_midi simulate`).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Callable

CONTROL_TYPES = ("knob", "fader", "pad", "button", "crossfader", "jog", "key")


@dataclass
class Control:
    id: str                      # p.ej. "knob0", "fader3", "pad.2.5", "btn80", "xfader"
    type: str
    pos: list[float]             # [x, y] en la skin (viewBox del SVG) o en mm cuando hay fit
    size: list[float] = field(default_factory=lambda: [0.0, 0.0])
    value: float = 0.0           # 0..1
    state: str = "up"            # down | up | move
    midi: dict = field(default_factory=dict)   # {"cc": 21} o {"note": 36, "channel": 1}
    last_event_t: float = 0.0

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Device:
    id: str                      # "ohm", "circuit", "ddj", "qx1222"
    name: str
    kind: str                    # "midi" | "audio" | "midi+audio"
    port_match: list[str]        # subcadenas que identifican sus puertos MIDI
    has_midi: bool = True
    has_audio: bool = False
    skin: dict = field(default_factory=dict)      # {"viewbox": [w, h], "svg": "ruta", "license": "..."}
    controls: dict[str, Control] = field(default_factory=dict)
    status: str = "sin conectar"                  # sin conectar | smartdeck | directo | simulado
    note: str = ""

    def to_json(self, with_controls: bool = True) -> dict:
        d = {"id": self.id, "name": self.name, "kind": self.kind, "has_midi": self.has_midi, "has_audio": self.has_audio,
             "status": self.status, "note": self.note, "skin": self.skin, "n_controls": len(self.controls)}
        if with_controls:
            d["controls"] = {k: v.to_json() for k, v in self.controls.items()}
        return d


def list_midi_ports() -> dict:
    try:
        import mido
        return {"inputs": mido.get_input_names(), "outputs": mido.get_output_names()}
    except Exception as e:  # noqa: BLE001
        return {"inputs": [], "outputs": [], "error": str(e)}


# ----------------------------------------------------------------------------- registro

@dataclass
class Binding:
    control: str                 # "ohm/knob0"
    target: str                  # "effect:e3:width_mm" | "beat:bpm"
    lo: float = 0.0
    hi: float = 1.0

    def map(self, v: float) -> float:
        return self.lo + (self.hi - self.lo) * v


class DeviceRegistry:
    def __init__(self):
        self.devices: dict[str, Device] = {}
        self.bindings: list[Binding] = []
        self.sources: list = []
        self.on_event: Callable[[dict], None] | None = None     # runtime.emit
        self.on_bind: Callable[[Binding, float], None] | None = None
        self.lock = threading.RLock()
        self.events: list[dict] = []

    def add(self, dev: Device) -> Device:
        self.devices[dev.id] = dev
        return dev

    def control(self, ref: str) -> tuple[Device, Control] | None:
        dev_id, _, cid = ref.partition("/")
        dev = self.devices.get(dev_id)
        if dev is None or cid not in dev.controls:
            return None
        return dev, dev.controls[cid]

    def handle(self, device: str, control: str, value: float, state: str = "move", t: float | None = None) -> dict | None:
        """Aplica un evento de una fuente: actualiza el control, bindings y publica. `t`: hora de llegada (time.time())
        si la fuente la midió; si no, ahora."""
        with self.lock:
            dev = self.devices.get(device)
            if dev is None or control not in dev.controls:
                return None
            c = dev.controls[control]
            c.value = max(0.0, min(1.0, float(value)))
            c.state = state
            c.last_event_t = float(t) if t else time.time()
            ev = {"device": device, "control": control, "type": c.type, "value": round(c.value, 4), "state": state,
                  "t_event": c.last_event_t}
            self.events.append(ev); self.events = self.events[-200:]
            ref = f"{device}/{control}"
            for b in self.bindings:
                if b.control == ref and self.on_bind:
                    self.on_bind(b, b.map(c.value))
            if self.on_event:
                self.on_event(ev)
            return ev

    def bind(self, control: str, target: str, lo: float = 0.0, hi: float = 1.0) -> Binding:
        if self.control(control) is None:
            raise KeyError(f"control desconocido: {control}")
        self.bindings = [b for b in self.bindings if not (b.control == control and b.target == target)]
        b = Binding(control, target, lo, hi)
        self.bindings.append(b)
        return b

    def unbind(self, control: str, target: str | None = None) -> int:
        before = len(self.bindings)
        self.bindings = [b for b in self.bindings if not (b.control == control and (target is None or b.target == target))]
        return before - len(self.bindings)

    def attach(self, source) -> None:
        source.registry = self
        self.sources.append(source)
        source.start()

    def stop(self) -> None:
        for s in self.sources:
            try:
                s.stop()
            except Exception:  # noqa: BLE001
                pass

    def to_json(self, with_controls: bool = False) -> dict:
        return {"devices": {k: v.to_json(with_controls) for k, v in self.devices.items()},
                "bindings": [asdict(b) for b in self.bindings],
                "sources": [s.describe() for s in self.sources],
                "midi_ports": list_midi_ports(), "last_events": self.events[-10:]}


# ----------------------------------------------------------------------------- fuentes

class FakeSource:
    """Eventos inyectados a mano (tests, `pg_midi simulate`)."""
    name = "fake"

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.registry: DeviceRegistry | None = None

    def start(self):
        dev = self.registry.devices.get(self.device_id)
        if dev:
            dev.status = "simulado"

    def stop(self):
        pass

    def send(self, control: str, value: float, state: str = "move"):
        return self.registry.handle(self.device_id, control, value, state)

    def describe(self) -> dict:
        return {"source": self.name, "device": self.device_id}


class SmartDeckSource:
    """Lee los mensajes `midi` del WebSocket de SmartDeck (session_controller.py, puerto 8090).

    Traducción (ver ~/ohmrgb/controller_spec.json › websocket_protocol.midi_event):
      input_type knob/fader/crossfader: id = CC → control "knobN"/"faderN"/"xfader" por el mapa del OHM
      input_type pad: row, col → "pad.r.c" (value = presión) · button: id 64..80 → "btnNN"
    """
    name = "smartdeck"

    def __init__(self, device_id: str = "ohm", url: str = "ws://127.0.0.1:8090", cc_map: dict | None = None):
        self.device_id = device_id
        self.url = url
        self.cc_map = cc_map or {}
        self.registry: DeviceRegistry | None = None
        self.thread: threading.Thread | None = None
        self.running = False
        self.connected = False
        self.last_error = ""
        self.grid: list | None = None

    def available(self, timeout: float = 0.5) -> bool:
        import socket
        from urllib.parse import urlparse
        u = urlparse(self.url)
        try:
            with socket.create_connection((u.hostname, u.port or 80), timeout=timeout):
                return True
        except OSError:
            return False

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _translate(self, p: dict) -> tuple[str, float, str] | None:
        t = p.get("input_type")
        v = float(p.get("value", 0)) / 127.0
        st = p.get("state", "move")
        if t == "pad":
            return f"pad.{p.get('row')}.{p.get('col')}", v, st
        if t == "button":
            return f"btn{p.get('id')}", v, st
        if t in ("knob", "fader", "crossfader"):
            cid = self.cc_map.get(int(p.get("id", -1)))
            if cid is None:
                cid = "xfader" if t == "crossfader" else f"{t}{p.get('id')}"
            return cid, v, "move"
        return None

    def _loop(self):
        import asyncio
        try:
            import websockets
        except ImportError:
            self.last_error = "falta websockets"; return

        async def run():
            while self.running:
                try:
                    async with websockets.connect(self.url, open_timeout=3) as ws:
                        self.connected = True
                        dev = self.registry.devices.get(self.device_id)
                        if dev:
                            dev.status = "smartdeck"
                        async for raw in ws:
                            if not self.running:
                                break
                            try:
                                m = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if m.get("type") == "midi":
                                tr = self._translate(m.get("payload", m))
                                if tr:
                                    self.registry.handle(self.device_id, *tr)
                            elif m.get("type") in ("state", "frame"):
                                g = (m.get("payload") or m).get("grid")
                                if g:
                                    self.grid = g
                except Exception as e:  # noqa: BLE001
                    self.connected = False
                    self.last_error = str(e)
                    dev = self.registry.devices.get(self.device_id)
                    if dev and dev.status == "smartdeck":
                        dev.status = "sin conectar"
                    await asyncio.sleep(2.0)
        asyncio.run(run())

    def describe(self) -> dict:
        return {"source": self.name, "device": self.device_id, "url": self.url, "connected": self.connected,
                "error": self.last_error}


class DirectMidiSource:
    """Abre un puerto MIDI de entrada con mido (python-rtmidi). Solo cuando nadie más lo tiene."""
    name = "midi"

    def __init__(self, device_id: str, port_match: list[str], cc_map: dict | None = None, note_map: dict | None = None):
        self.device_id = device_id
        self.port_match = port_match
        self.cc_map = cc_map or {}
        self.note_map = note_map or {}
        self.registry: DeviceRegistry | None = None
        self.port = None
        self.port_name = ""
        self.last_error = ""

    def start(self):
        try:
            import mido
            names = mido.get_input_names()
            self.port_name = next((n for n in names if any(m.lower() in n.lower() for m in self.port_match)), "")
            if not self.port_name:
                self.last_error = f"puerto no encontrado entre {names}"
                return
            self.port = mido.open_input(self.port_name, callback=self._on_msg)
            dev = self.registry.devices.get(self.device_id)
            if dev:
                dev.status = "directo"
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)

    def stop(self):
        if self.port:
            self.port.close(); self.port = None

    def _on_msg(self, msg):
        if msg.type == "control_change":
            cid = self.cc_map.get(msg.control, f"cc{msg.control}")
            self.registry.handle(self.device_id, cid, msg.value / 127.0, "move")
        elif msg.type in ("note_on", "note_off"):
            cid = self.note_map.get(msg.note, f"note{msg.note}")
            down = msg.type == "note_on" and msg.velocity > 0
            self.registry.handle(self.device_id, cid, (msg.velocity / 127.0) if down else 0.0, "down" if down else "up")

    def describe(self) -> dict:
        return {"source": self.name, "device": self.device_id, "port": self.port_name, "open": self.port is not None,
                "error": self.last_error}
