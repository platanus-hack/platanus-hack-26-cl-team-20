"""Korg EK-50 (61 teclas, C2..C7) como dispositivo del playground y sus fuentes MIDI.

El teclado está enchufado por USB al PC Windows; el Mac no lo ve. ``windows/midi_bridge.py`` lee su puerto y manda
cada mensaje al Mac por **UDP** (un datagrama JSON por mensaje: lo más simple y de menor latencia en la LAN; sin
reintentos, que para notas en vivo llegarían tarde igual). Este módulo es el lado del Mac:

- ``BridgeMidiSource``: escucha UDP (puerto 8796) y cada segundo le manda un ``hello`` al puente (así el puente sabe
  a quién mandar sin configurar la IP del Mac, y el ``pong`` mide la ida y vuelta). Mensajes del puente:
  ``{"k": "m", "s": seq, "t": hora_pc, "b": [status, data1, data2]}`` (MIDI), ``{"k": "hb", ...}`` (latido con el
  puerto abierto) y ``{"k": "pong", "t": hora_mac}``. Solo acepta datagramas de la LAN o la tailnet.
- ``KeyboardSource``: la fuente "auto" del teclado: si el Korg está enchufado al Mac (rtmidi local) abre ese puerto
  (``DirectMidiSource``); si no, escucha el puente. Misma interfaz que las otras fuentes (``registry``, ``start``,
  ``stop``, ``describe``) y entrega ``note{N}`` down/up con valor = velocidad/127.

Cada evento entra al registro con su hora de llegada (``t``) y el runtime lo pasa a los efectos en ``scene.inputs``.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
import time

from .midi import Control, Device, DirectMidiSource

EK50_PORTS = ["KORG", "EK-50", "EK50", "Generic USB MIDI"]
BRIDGE_PORT = 8796
DEFAULT_BRIDGE = os.environ.get("MIDI_BRIDGE", f"192.168.1.233:{BRIDGE_PORT}")     # el PC Windows (NEW-WHITE-4K)
PRIVADAS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                              "100.64.0.0/10", "169.254.0.0/16", "::1/128", "fd00::/8", "fe80::/10")]
NOMBRES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def privada(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    m = getattr(a, "ipv4_mapped", None)
    if m is not None:
        a = m
    return any(a in n for n in PRIVADAS)


def ek50_device(lo: int = 36, n: int = 61) -> Device:
    """El teclado como dispositivo: un control `noteN` por tecla (tipo key, posición = índice de tecla)."""
    controls = {}
    for i, m in enumerate(range(lo, lo + n)):
        cid = f"note{m}"
        controls[cid] = Control(cid, "key", [float(i), 0.0], [1.0, 1.0], midi={"note": m, "name": f"{NOMBRES[m % 12]}{m // 12 - 1}"})
    return Device("ek50", "Korg EK-50", "midi", port_match=list(EK50_PORTS), has_midi=True, controls=controls,
                  note="USB al PC Windows → windows/midi_bridge.py → UDP 8796 → Mac; o enchufado directo al Mac")


def parse_hostport(spec: str | None, default_port: int = BRIDGE_PORT) -> tuple[str, int] | None:
    if not spec:
        return None
    host, _, port = str(spec).rpartition(":") if ":" in str(spec) else (str(spec), "", "")
    if not host:
        host, port = str(spec), ""
    return host, int(port) if port else default_port


def midi_to_event(b: list[int]) -> tuple[str, float, str] | None:
    """Bytes MIDI -> (control, valor 0..1, estado) para el registro; None si no es nota."""
    if len(b) < 3:
        return None
    st = b[0] & 0xF0
    if st == 0x90 and b[2] > 0:
        return f"note{b[1]}", b[2] / 127.0, "down"
    if st == 0x80 or (st == 0x90 and b[2] == 0):
        return f"note{b[1]}", 0.0, "up"
    if st == 0xB0:
        return f"cc{b[1]}", b[2] / 127.0, "move"
    return None


class BridgeMidiSource:
    """Recibe el MIDI del puente del PC por UDP (ver el docstring del módulo)."""
    name = "bridge"

    def __init__(self, device_id: str = "ek50", bridge: str | None = DEFAULT_BRIDGE, listen_port: int = BRIDGE_PORT,
                 hello_s: float = 1.0):
        self.device_id = device_id
        self.bridge = parse_hostport(bridge)
        self.listen_port = listen_port
        self.hello_s = hello_s
        self.registry = None
        self.sock: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.running = False
        self.last_error = ""
        self.port_name = ""          # el puerto MIDI que tiene abierto el puente
        self.peer = None
        self.last_rx = 0.0
        self.n_msgs = 0
        self.n_notes = 0
        self.lost = 0
        self.dropped = 0             # notas fuera de las teclas del dispositivo
        self.rtt_ms: float | None = None
        self._seq = None
        self.bridge_info: dict = {}

    @property
    def bound_port(self) -> int:
        return self.sock.getsockname()[1] if self.sock else 0

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("0.0.0.0", self.listen_port))
        except OSError as e:                       # puerto ocupado: uno libre (el puente contesta al que mande hello)
            self.last_error = f"UDP {self.listen_port} ocupado ({e}); uso uno libre"
            self.sock.bind(("0.0.0.0", 0))
        self.sock.settimeout(0.2)
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="midi-bridge-rx")
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        if self.sock:
            self.sock.close(); self.sock = None
        self._status("sin conectar")

    @property
    def connected(self) -> bool:
        return self.last_rx > 0 and time.time() - self.last_rx < 3.0

    def _status(self, st: str) -> None:
        dev = self.registry.devices.get(self.device_id) if self.registry else None
        if dev is not None:
            dev.status = st

    def _hello(self):
        if self.bridge and self.sock:
            try:
                self.sock.sendto(json.dumps({"k": "hello", "t": time.time(), "id": socket.gethostname()}).encode(), self.bridge)
            except OSError as e:
                self.last_error = f"hello a {self.bridge}: {e}"

    def _loop(self):
        ultimo_hello = 0.0
        while self.running:
            now = time.time()
            if now - ultimo_hello >= self.hello_s:
                self._hello(); ultimo_hello = now
                if self.last_rx and not self.connected:
                    self._status("sin conectar")
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            self.handle_datagram(data, addr, time.time())

    def handle_datagram(self, data: bytes, addr, t_rx: float) -> dict | None:
        if not privada(addr[0]):
            return None
        try:
            m = json.loads(data.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        self.last_rx = t_rx; self.peer = f"{addr[0]}:{addr[1]}"
        k = m.get("k")
        if k == "pong" and "t" in m:
            self.rtt_ms = round((t_rx - float(m["t"])) * 1000, 2)
            self.port_name = m.get("port", self.port_name)
            self._status("puente")
            return None
        if k == "hb":
            self.port_name = m.get("port", "") or ""
            self.bridge_info = {x: m.get(x) for x in ("host", "open", "fake", "n", "error") if x in m}
            self._status("puente")
            return None
        if k != "m":
            return None
        s = m.get("s")
        if isinstance(s, int):
            if self._seq is not None:
                if s <= self._seq and self._seq - s < 1000:
                    return None                 # duplicado o desordenado
                self.lost += max(0, s - self._seq - 1)
            self._seq = s
        self.n_msgs += 1
        ev = midi_to_event(list(m.get("b") or []))
        if ev is None or self.registry is None:
            return None
        self._status("puente")
        out = self.registry.handle(self.device_id, *ev, t=t_rx)
        if out is None:
            self.dropped += 1
        elif ev[0].startswith("note"):
            self.n_notes += 1
        return out

    def describe(self) -> dict:
        return {"source": self.name, "device": self.device_id,
                "bridge": f"{self.bridge[0]}:{self.bridge[1]}" if self.bridge else None,
                "listen": self.bound_port, "connected": self.connected, "port": self.port_name, "peer": self.peer,
                "rtt_ms": self.rtt_ms, "msgs": self.n_msgs, "notes": self.n_notes, "lost": self.lost,
                "dropped": self.dropped, "bridge_info": self.bridge_info, "error": self.last_error}


class KeyboardSource:
    """Fuente "auto" del EK-50: MIDI local si el Korg está en el Mac, si no el puente UDP del PC."""
    name = "keyboard"

    def __init__(self, device_id: str = "ek50", prefer: str = "auto", bridge: str | None = DEFAULT_BRIDGE,
                 listen_port: int = BRIDGE_PORT, port_match: list[str] | None = None):
        self.device_id = device_id
        self.prefer = prefer
        self.bridge = bridge
        self.listen_port = listen_port
        self.port_match = port_match or list(EK50_PORTS)
        self.registry = None
        self.inner = None
        self.mode = ""

    def _local_port(self) -> str:
        try:
            import mido
            names = mido.get_input_names()
        except Exception:  # noqa: BLE001
            return ""
        return next((n for n in names if any(m.lower() in n.lower() for m in self.port_match)), "")

    def start(self):
        if self.prefer in ("auto", "direct") and self._local_port():
            self.inner = _TimedDirect(self.device_id, self.port_match); self.mode = "directo"
        elif self.prefer in ("auto", "bridge"):
            self.inner = BridgeMidiSource(self.device_id, self.bridge, self.listen_port); self.mode = "puente"
        else:                                        # direct pedido pero no hay puerto local: queda registrado el error
            self.inner = _TimedDirect(self.device_id, self.port_match); self.mode = "directo"
        self.inner.registry = self.registry
        self.inner.start()

    def stop(self):
        if self.inner:
            self.inner.stop()

    def describe(self) -> dict:
        d = self.inner.describe() if self.inner else {}
        return {**d, "source": f"{self.name}:{self.mode or '-'}", "device": self.device_id, "prefer": self.prefer}


class _TimedDirect(DirectMidiSource):
    """DirectMidiSource que marca la hora de llegada de cada nota (para juzgar a tiempo)."""

    def _on_msg(self, msg):
        t = time.time()
        if msg.type in ("note_on", "note_off"):
            down = msg.type == "note_on" and msg.velocity > 0
            self.registry.handle(self.device_id, f"note{msg.note}", (msg.velocity / 127.0) if down else 0.0,
                                 "down" if down else "up", t=t)
        elif msg.type == "control_change":
            self.registry.handle(self.device_id, f"cc{msg.control}", msg.value / 127.0, "move", t=t)
