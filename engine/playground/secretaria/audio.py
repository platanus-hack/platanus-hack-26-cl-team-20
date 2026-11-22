"""Audio de la conversación con `sounddevice` (micrófono y parlantes del MacBook), sin PyAudio.

Implementa el `AudioInterface` del SDK de ElevenLabs: entra PCM 16 bit mono 16 kHz en bloques de 250 ms, sale lo
mismo. Sin cancelación de eco (eso lo da WebRTC, no PortAudio): por defecto es **semidúplex**: mientras ella habla
(y 0,4 s después) al micrófono se le manda silencio, para que no se escuche a sí misma por los parlantes y se
interrumpa sola. Con audífonos se puede usar dúplex (`--duplex`) y entonces Max la puede interrumpir.

También detecta si Max está hablando (nivel del micrófono sobre un piso de ruido adaptativo: música o ventilador
constantes no cuentan) para que los avances esperen a que termine, y avisa si el micrófono entrega ceros exactos
(macOS sin permiso de micrófono para la terminal).
"""

from __future__ import annotations

import math
import queue
import threading
import time
from typing import Callable

try:
    from elevenlabs.conversational_ai.conversation import AudioInterface
except Exception:  # noqa: BLE001  (sin el SDK: igual se puede probar la clase)
    class AudioInterface:  # type: ignore[no-redef]
        pass

RATE = 16000
IN_FRAMES = 4000          # 250 ms
OUT_FRAMES = 1600         # 100 ms por escritura: la interrupción corta en ≤100 ms


def dbfs(pcm: bytes) -> float:
    """Nivel RMS en dBFS de PCM int16 (−120 si está en cero)."""
    n = len(pcm) // 2
    if n == 0:
        return -120.0
    import array
    a = array.array("h", pcm[: n * 2])
    s = sum(x * x for x in a) / n
    return -120.0 if s <= 0 else 10 * math.log10(s / (32768.0 ** 2))


def find_device(sd, match: str, kind: str):
    """Índice del primer dispositivo cuyo nombre contiene `match` y tiene canales de `kind` (input|output)."""
    if not match:
        return None
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    for i, d in enumerate(sd.query_devices()):
        if match.lower() in str(d.get("name", "")).lower() and d.get(key, 0) > 0:
            return i
    return None


class SoundDeviceAudio(AudioInterface):
    def __init__(self, sd=None, input_match: str = "MacBook", output_match: str = "MacBook", half_duplex: bool = True,
                 tail_s: float = 0.4, vad_dbfs: float = -55.0, vad_margin_db: float = 9.0,
                 on_warning: Callable[[str], None] | None = None):
        if sd is None:
            import sounddevice as sd
        self.sd = sd
        self.input_match, self.output_match = input_match, output_match
        self.half_duplex, self.tail_s, self.vad_dbfs, self.vad_margin_db = half_duplex, tail_s, vad_dbfs, vad_margin_db
        self.floor = -60.0           # piso de ruido estimado (dBFS)
        self.on_warning = on_warning or (lambda m: None)
        self.in_stream = self.out_stream = None
        self.input_callback: Callable[[bytes], None] | None = None
        self.out_q: queue.Queue[bytes] = queue.Queue()
        self._stop = threading.Event()
        self._interrupted = threading.Event()
        self._thread: threading.Thread | None = None
        self.play_end = 0.0          # hasta cuándo suena lo ya escrito
        self.last_voice = 0.0        # última vez que el micrófono pasó el umbral (fuera del semidúplex)
        self.level = -120.0
        self.zero_run = 0.0          # segundos seguidos de ceros exactos
        self.played_s = 0.0          # total reproducido en esta sesión
        self.devices = {}
        self._warned = False

    # ---------------------------------------------------------------- AudioInterface
    def start(self, input_callback):
        self.input_callback = input_callback
        self._stop.clear()
        self._interrupted.clear()
        self.out_q = queue.Queue()
        self.played_s = 0.0
        self.zero_run = 0.0
        self._warned = False
        din = find_device(self.sd, self.input_match, "input")
        dout = find_device(self.sd, self.output_match, "output")
        self.devices = {"entrada": self._name(din, "input"), "salida": self._name(dout, "output")}
        self.in_stream = self.sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=IN_FRAMES,
                                                device=din, callback=self._on_input)
        self.out_stream = self.sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16", device=dout)
        self.out_stream.start()
        self.in_stream.start()
        self._thread = threading.Thread(target=self._player, name="voz-salida", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._interrupted.set()
        if self._thread:
            self._thread.join(timeout=2)
        for s in (self.in_stream, self.out_stream):
            if s is None:
                continue
            try:
                s.stop()
                s.close()
            except Exception:  # noqa: BLE001
                pass
        self.in_stream = self.out_stream = None
        self.input_callback = None

    def output(self, audio: bytes):
        self._interrupted.clear()
        self.out_q.put(audio)

    def interrupt(self):
        self._interrupted.set()
        self._drain()
        self.play_end = time.monotonic()

    # ---------------------------------------------------------------- estado
    def playing(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return not self.out_q.empty() or now < self.play_end + self.tail_s

    def user_speaking(self, within_s: float = 1.2, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return now - self.last_voice < within_s

    # ---------------------------------------------------------------- interno
    def _name(self, idx, kind):
        try:
            return self.sd.query_devices(idx if idx is not None else None, kind)["name"]
        except Exception:  # noqa: BLE001
            return "por defecto"

    def _drain(self):
        try:
            while True:
                self.out_q.get_nowait()
        except queue.Empty:
            pass

    def _on_input(self, indata, frames, time_info, status):
        cb = self.input_callback
        if cb is None or self._stop.is_set():
            return
        data = bytes(indata)
        now = time.monotonic()
        if self.half_duplex and self.playing(now):
            data = b"\x00" * len(data)               # semidúplex: ella no se oye a sí misma
        else:
            if not any(data):
                self.zero_run += frames / RATE
                if self.zero_run >= 6 and not self._warned:
                    self._warned = True
                    self.on_warning("el micrófono entrega silencio exacto: ¿falta el permiso de micrófono de macOS "
                                    "para la terminal (Ajustes › Privacidad › Micrófono)?")
            else:
                self.zero_run = 0.0
            self.level = dbfs(data)
            # piso de ruido adaptativo: baja rápido, sube lento (música o ventilador constantes no cuentan como voz)
            if self.level < self.floor:
                self.floor += (self.level - self.floor) * 0.3
            else:
                self.floor += (self.level - self.floor) * 0.01
            if self.level > max(self.vad_dbfs, self.floor + self.vad_margin_db):
                self.last_voice = now
        try:
            cb(data)
        except Exception:  # noqa: BLE001  (la sesión se está cerrando)
            pass

    def _player(self):
        while not self._stop.is_set():
            try:
                chunk = self.out_q.get(timeout=0.1)
            except queue.Empty:
                continue
            step = OUT_FRAMES * 2
            for i in range(0, len(chunk), step):
                if self._interrupted.is_set() or self._stop.is_set():
                    break
                part = chunk[i:i + step]
                now = time.monotonic()
                self.play_end = max(self.play_end, now) + len(part) / 2 / RATE
                try:
                    self.out_stream.write(part)
                except Exception:  # noqa: BLE001
                    return
                self.played_s += len(part) / 2 / RATE
