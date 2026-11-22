"""Escalas y salida MIDI de la mesa táctil.

- `scale_notes(root, scale, n)`: n notas MIDI ascendentes de una escala (pentatónica mayor por defecto).
- `MidiOut`: una salida por proceso hacia varios destinos (`sound.py`). `port="auto"`: suena por la mesa (el
  receptor `windows/midi_play.py` del PC, por UDP) si contesta, si no por el MacBook (sintetizador simple), y en
  paralelo el puerto virtual "sl-scanner touch" (o el bus IAC) para Ableton. `TOUCHSYNTH_MIDI` elige: auto | pc |
  mac | virtual | iac | null | <nombre de puerto> | combinaciones con "+". `TOUCHSYNTH_PC` = host:puerto del PC.
- `Notes`: notas sostenidas con conteo por (canal, nota), largo mínimo (un tap no queda en 0 ms) y notas con
  duración fija (marimba). `tick(now)` despacha los note off pendientes.

Canales (1..16 hacia afuera, 0..15 en mido): cocodrilo 1, marimba 2, dibujo 3. En Ableton: una pista MIDI por
instrumento con "MIDI From: sl-scanner touch" (o IAC) y el canal correspondiente.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

SCALES: dict[str, list[int]] = {
    "pentatonica_mayor": [0, 2, 4, 7, 9],
    "pentatonica_menor": [0, 3, 5, 7, 10],
    "mayor": [0, 2, 4, 5, 7, 9, 11],
    "menor": [0, 2, 3, 5, 7, 8, 10],
    "dorica": [0, 2, 3, 5, 7, 9, 10],
    "blues": [0, 3, 5, 6, 7, 10],
    "hirajoshi": [0, 2, 3, 7, 8],
    "in_sen": [0, 1, 5, 7, 10],
    "cromatica": list(range(12)),
}
ALIASES = {"pentatonica": "pentatonica_mayor", "pentatonic": "pentatonica_mayor", "major_pentatonic": "pentatonica_mayor",
           "minor_pentatonic": "pentatonica_menor", "major": "mayor", "minor": "menor", "dorian": "dorica",
           "chromatic": "cromatica"}
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
DEFAULT_PORT = "sl-scanner touch"


def scale_steps(scale: str | list[int]) -> list[int]:
    if isinstance(scale, (list, tuple)):
        steps = sorted({int(v) % 12 for v in scale})
        if not steps:
            raise ValueError("escala vacía")
        return steps
    key = ALIASES.get(str(scale).lower(), str(scale).lower())
    if key not in SCALES:
        raise ValueError(f"escala desconocida: {scale}. Hay: {sorted(SCALES)}")
    return SCALES[key]


def scale_notes(root: int = 48, scale: str | list[int] = "pentatonica_mayor", n: int = 10) -> list[int]:
    """n notas ascendentes desde `root` (nota MIDI, 48 = C3) siguiendo la escala; recorta a 0..127."""
    steps = scale_steps(scale)
    out = []
    i = 0
    while len(out) < n:
        octave, k = divmod(i, len(steps))
        out.append(int(root) + 12 * octave + steps[k])
        i += 1
    return [max(0, min(127, v)) for v in out]


def note_name(n: int) -> str:
    return f"{NAMES[n % 12]}{n // 12 - 1}"


def velocity_of(v: float | None, default: int = 100) -> int:
    """0..1 → 1..127 (None = default)."""
    if v is None:
        return int(default)
    return max(1, min(127, int(round(float(v) * 127))))


# ---------------------------------------------------------------------- salida MIDI

class MidiOut:
    """Salida de notas de la mesa táctil hacia uno o más destinos (`sound.py`). Se abre con la primera nota (o con
    `prepare()`, que llaman los instrumentos al crearse para que la búsqueda del PC empiece antes).

    `port` (o `TOUCHSYNTH_MIDI`):
    - "auto" (por defecto): el puerto virtual "sl-scanner touch" (o IAC) para Ableton + el sintetizador del MacBook
      mientras se busca la mesa en un hilo: primero el receptor del PC por UDP (`pc`, si el firewall lo deja) y si
      no, lanzándolo por ssh (`pc-ssh`); cuando la mesa contesta reemplaza al MacBook. Si no aparece, sigue el Mac.
    - "pc" (UDP), "pc-ssh", "mac", "virtual", "iac", "null", un nombre de puerto o combinaciones con "+".
    """

    def __init__(self, port: str | None = None, pc: str | None = None, reprobe_s: float = 60.0,
                 ssh_cmd: list[str] | None = None):
        self.want = port or os.environ.get("TOUCHSYNTH_MIDI", "auto")
        self.pc = pc
        self.ssh_cmd = ssh_cmd
        self.reprobe_s = reprobe_s
        self.sinks: list = []
        self.name: str | None = None
        self.mode = "cerrado"
        self.error: str | None = None
        self.search = None                            # estado de la búsqueda de la mesa (auto)
        self.sent = 0
        self.log: deque[tuple] = deque(maxlen=64)
        self._lock = threading.Lock()
        self._tried = False
        self._probe: threading.Thread | None = None
        self._closed = False

    @property
    def port(self):                                   # compatibilidad: ¿hay algún destino?
        return self.sinks[0] if self.sinks else None

    def prepare(self) -> None:
        with self._lock:
            if not self._tried:
                self._open()

    def _midi_port(self, which: str):
        import mido
        from .sound import MidoPort
        outs = mido.get_output_names()
        iac = next((o for o in outs if "iac" in o.lower()), None)
        if which in ("iac", "auto") and iac:
            return MidoPort(mido.open_output(iac), iac)
        if which in ("virtual", "auto", "iac"):
            return MidoPort(mido.open_output(DEFAULT_PORT, virtual=True), DEFAULT_PORT)
        match = next((o for o in outs if which.lower() in o.lower()), None)
        if match is None:
            raise RuntimeError(f"no hay un puerto '{which}' (hay: {outs})")
        return MidoPort(mido.open_output(match), match)

    def _open(self) -> None:
        from .sound import MacSynth, SshMidi, UdpMidi
        self._tried = True
        if self.want == "null":
            self.mode, self.name = "nulo", None
            return
        parts = ["virtual-auto", "mac", "buscar-pc"] if self.want == "auto" else self.want.split("+")
        errors = []
        for part in parts:
            try:
                if part == "virtual-auto":
                    self.sinks.append(self._midi_port("auto"))
                elif part == "buscar-pc":
                    self._start_search()
                elif part == "pc":
                    udp = UdpMidi(self.pc)
                    udp.ping()
                    self.sinks.append(udp)
                elif part == "pc-ssh":
                    ssh = SshMidi(self.ssh_cmd)
                    if not ssh.start():
                        raise RuntimeError(ssh.error)
                    self.sinks.append(ssh)
                elif part in ("mac", "synth"):
                    self.sinks.append(MacSynth())
                else:
                    self.sinks.append(self._midi_port(part))
            except Exception as e:  # noqa: BLE001  un destino que falla no apaga a los otros
                errors.append(f"{part}: {type(e).__name__}: {e}")
        self.error = "; ".join(errors) or None
        self._update_mode()

    def _update_mode(self) -> None:
        from .sound import MacSynth, SshMidi, UdpMidi
        kinds = ["pc" if isinstance(k, UdpMidi) else "pc-ssh" if isinstance(k, SshMidi) else
                 "mac" if isinstance(k, MacSynth) else "puerto" for k in self.sinks]
        self.mode = "+".join(kinds) or "nulo"
        self.name = " + ".join(k.name for k in self.sinks) or None

    def _use_pc(self, sink) -> None:
        """La mesa contestó: reemplaza al MacBook."""
        from .sound import MacSynth
        with self._lock:
            if self._closed:
                sink.close()
                return
            for k in [k for k in self.sinks if isinstance(k, MacSynth)]:
                k.close()
                self.sinks.remove(k)
            self.sinks.append(sink)
            self._update_mode()

    def _start_search(self) -> None:
        def loop():
            from .sound import SshMidi, UdpMidi
            while not self._closed:
                self.search = "probando la mesa por UDP"
                udp = UdpMidi(self.pc)
                if udp.ping() is not None:
                    self.search = "mesa por UDP"
                    return self._use_pc(udp)
                udp.close()
                self.search = "lanzando el receptor del PC por ssh"
                ssh = SshMidi(self.ssh_cmd)
                if ssh.start():
                    self.search = "mesa por ssh"
                    return self._use_pc(ssh)
                self.search = f"sin mesa ({ssh.error}); sigue el MacBook, reintento en {self.reprobe_s:.0f} s"
                for _ in range(int(self.reprobe_s * 10)):
                    if self._closed:
                        return
                    time.sleep(0.1)
        self._probe = threading.Thread(target=loop, name="midi-busca-mesa", daemon=True)
        self._probe.start()

    def send(self, kind: str, channel: int, a: int, b: int = 0) -> None:
        """kind: note_on | note_off | control_change. channel 1..16."""
        with self._lock:
            if not self._tried:
                self._open()
            ch = max(0, min(15, int(channel) - 1))
            self.log.append((round(time.time(), 3), kind, ch + 1, int(a), int(b)))
            self.sent += 1
            for sink in self.sinks:
                sink.send(kind, ch, int(a), int(b))

    def note_on(self, note: int, velocity: int, channel: int = 1) -> None:
        self.send("note_on", channel, note, velocity)

    def note_off(self, note: int, channel: int = 1) -> None:
        self.send("note_off", channel, note, 0)

    def cc(self, control: int, value: int, channel: int = 1) -> None:
        self.send("control_change", channel, control, max(0, min(127, int(value))))

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for k in self.sinks:
                k.close()
            self.sinks = []
            self.mode = "cerrado"

    def status(self) -> dict:
        return {"port": self.name, "mode": self.mode if self._tried else "sin abrir (abre con la primera nota)",
                "destinos": [k.status() for k in self.sinks], "busqueda": self.search, "sent": self.sent,
                "error": self.error, "last": list(self.log)[-5:]}


class FakeMidi(MidiOut):
    """Para tests: guarda los mensajes, no abre puertos."""

    def __init__(self):
        super().__init__("null")
        self.messages: list[tuple] = []

    def send(self, kind, channel, a, b=0):
        self.messages.append((kind, int(channel), int(a), int(b)))
        super().send(kind, channel, a, b)


_MIDI: MidiOut | None = None
_MIDI_LOCK = threading.Lock()


def get_midi() -> MidiOut:
    global _MIDI
    with _MIDI_LOCK:
        if _MIDI is None:
            _MIDI = MidiOut()
        return _MIDI


def set_midi(m: MidiOut | None) -> None:
    """Reemplaza la salida del proceso (tests, o para elegir otro puerto en vivo)."""
    global _MIDI
    with _MIDI_LOCK:
        _MIDI = m


# ---------------------------------------------------------------------- notas sostenidas y programadas

class Notes:
    """Notas de un instrumento: on/off con conteo por (canal, nota), largo mínimo y note off programados."""

    def __init__(self, midi: MidiOut | None = None, min_len: float = 0.12):
        self._midi = midi
        self.min_len = min_len
        self.count: dict[tuple[int, int], int] = {}
        self.started: dict[tuple[int, int], float] = {}
        self.pending: list[tuple[float, int, int]] = []          # (t_off, canal, nota)

    @property
    def midi(self) -> MidiOut:
        return self._midi or get_midi()

    def on(self, note: int, velocity: int, channel: int, now: float, dur: float | None = None) -> None:
        key = (channel, note)
        if self.count.get(key, 0) > 0:                            # re-ataque de una nota que suena: corta y vuelve
            self.midi.note_off(note, channel)
        self.midi.note_on(note, velocity, channel)
        self.count[key] = self.count.get(key, 0) + 1
        self.started[key] = now
        if dur is not None:
            self.pending.append((now + max(dur, self.min_len), channel, note))

    def off(self, note: int, channel: int, now: float) -> None:
        key = (channel, note)
        if self.count.get(key, 0) <= 0:
            return
        t_off = max(now, self.started.get(key, now) + self.min_len)
        self.pending.append((t_off, channel, note))

    def tick(self, now: float) -> None:
        due = [p for p in self.pending if p[0] <= now]
        if not due:
            return
        self.pending = [p for p in self.pending if p[0] > now]
        for _, ch, note in sorted(due):
            key = (ch, note)
            n = self.count.get(key, 0)
            if n <= 0:
                continue
            if n == 1:
                self.midi.note_off(note, ch)
                self.count.pop(key, None)
            else:
                self.count[key] = n - 1

    def panic(self) -> None:
        for (ch, note), n in list(self.count.items()):
            if n > 0:
                self.midi.note_off(note, ch)
        self.count.clear()
        self.pending.clear()

    @property
    def sounding(self) -> list[tuple[int, int]]:
        return sorted(k for k, n in self.count.items() if n > 0)
