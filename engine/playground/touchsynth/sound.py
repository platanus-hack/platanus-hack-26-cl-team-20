"""Por dónde suena la mesa táctil: por la mesa (PC Windows), por el MacBook o por Ableton.

- **Mesa (PC)**: `UdpMidi` manda cada mensaje MIDI por UDP al receptor `windows/midi_play.py` del PC, que lo toca
  con el "Microsoft GS Wavetable Synth"; el audio sale por el dispositivo predeterminado de Windows (la QX1222USB
  si Max la deja como predeterminada). Datagrama JSON, un mensaje o varios:

      {"v": 1, "m": [[status, data1, data2], ...]}        status = 0x90 note on | 0x80 note off | 0xB0 CC (+ canal 0..15)
      {"v": 1, "ping": "<nonce>"}  →  {"v": 1, "pong": "<nonce>", "out": "<puerto>", "n": <mensajes tocados>}

  Puerto UDP 8797 (LAN y tailnet). Sin respuesta al ping, el PC no está escuchando (o el firewall de Windows no
  deja pasar UDP 8797: hoy solo están abiertos TCP 8790 y 8794).
- **Mesa (PC) por ssh**: `SshMidi` lanza el mismo receptor por ssh con `--stdin` y le escribe las líneas JSON por
  la sesión: no necesita abrir puertos y no deja nada corriendo en el PC (muere con la sesión, o sea con el runtime).
- **MacBook**: `MacSynth`, un sintetizador mínimo con sounddevice (pad suave en el canal 1, marimba en el 2,
  campana en el 3) por la salida de audio del Mac. Respaldo cuando el PC no responde.
- **Ableton**: el puerto virtual "sl-scanner touch" (o el bus IAC) sigue abierto en paralelo (no suena si Ableton
  no lo escucha).
"""

from __future__ import annotations

import json
import math
import os
import socket
import threading
import time
import uuid

import numpy as np

PC_DEFAULT = "192.168.1.233:8797"
STATUS = {"note_on": 0x90, "note_off": 0x80, "control_change": 0xB0}
KINDS = {0x90: "note_on", 0x80: "note_off", 0xB0: "control_change"}


def pc_address(spec: str | None = None) -> tuple[str, int]:
    spec = spec or os.environ.get("TOUCHSYNTH_PC") or PC_DEFAULT
    host, _, port = spec.partition(":")
    return host, int(port or 8797)


def encode(kind: str, ch0: int, a: int, b: int) -> list[int]:
    return [STATUS[kind] | (int(ch0) & 0x0F), int(a) & 0x7F, int(b) & 0x7F]


class UdpMidi:
    """Mensajes MIDI por UDP al receptor del PC (sin conexión: se manda y listo)."""

    def __init__(self, addr: tuple[str, int] | str | None = None, timeout: float = 0.35):
        self.addr = pc_address(addr) if isinstance(addr, (str, type(None))) else addr
        self.name = f"mesa (PC {self.addr[0]}:{self.addr[1]})"
        self.timeout = timeout
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)
        self.sent = 0
        self.error: str | None = None
        self.last_pong: dict | None = None
        self._lock = threading.Lock()

    def send(self, kind: str, ch0: int, a: int, b: int = 0) -> None:
        data = json.dumps({"v": 1, "m": [encode(kind, ch0, a, b)]}).encode()
        try:
            self.sock.sendto(data, self.addr)
            self.sent += 1
        except OSError as e:
            self.error = f"{type(e).__name__}: {e}"

    def ping(self, timeout: float | None = None) -> dict | None:
        """{"pong", "out", "n", "rtt_ms"} si el receptor del PC contesta; None si no."""
        nonce = uuid.uuid4().hex[:8]
        t0 = time.time()
        with self._lock:
            try:
                self.sock.settimeout(timeout or self.timeout)
                self.sock.sendto(json.dumps({"v": 1, "ping": nonce}).encode(), self.addr)
                while time.time() - t0 < (timeout or self.timeout):
                    data, _ = self.sock.recvfrom(4096)
                    msg = json.loads(data)
                    if msg.get("pong") == nonce:
                        self.last_pong = {**msg, "rtt_ms": round(1000 * (time.time() - t0), 1), "t": time.time()}
                        return self.last_pong
            except (OSError, ValueError):
                return None
        return None

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def status(self) -> dict:
        return {"sink": self.name, "sent": self.sent, "pong": self.last_pong, "error": self.error}


PC_SSH_DEFAULT = "Roberto Mardones@192.168.1.233"
PC_ROOT = "C:/sl-scanner"


def ssh_command(target: str | None = None, root: str = PC_ROOT, extra: str = "") -> list[str]:
    """ssh al PC que deja corriendo el receptor con --stdin (PowerShell, venv del PC). Muere con la sesión."""
    target = target or os.environ.get("TOUCHSYNTH_PC_SSH") or PC_SSH_DEFAULT
    ps = (f"$env:Path = '{root}/.venv/Scripts;' + $env:Path; Set-Location '{root}'; "
          f"python -u windows/midi_play.py --stdin {extra}".strip())
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o", "ServerAliveInterval=15", target, ps]


class SshMidi:
    """El receptor del PC lanzado por ssh y alimentado por su stdin (una línea JSON por mensaje): no necesita abrir
    puertos en el firewall de Windows y no deja nada corriendo en el PC al cerrar. `cmd` reemplaza el ssh (tests)."""

    def __init__(self, cmd: list[str] | None = None, name: str | None = None):
        self.cmd = cmd or ssh_command()
        self.name = name or f"mesa (PC por ssh, {self.cmd[-2] if len(self.cmd) > 1 else ''})"
        self.proc = None
        self.ready: dict | None = None
        self.last_pong: dict | None = None
        self.error: str | None = None
        self.sent = 0
        self.played_lines: list[str] = []
        self._pongs: dict[str, dict] = {}
        self._cond = threading.Condition()
        self._lock = threading.Lock()

    def start(self, timeout: float = 12.0) -> bool:
        import subprocess
        try:
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1, encoding="utf-8",
                                         errors="replace")
        except OSError as e:
            self.error = f"{type(e).__name__}: {e}"
            return False
        threading.Thread(target=self._reader, name="midi-ssh-reader", daemon=True).start()
        with self._cond:
            self._cond.wait_for(lambda: self.ready is not None or getattr(self, "_eof", False), timeout)
        if self.ready is None:
            self.error = self.error or "el receptor del PC no contestó (¿ssh? ¿mido en el venv del PC?)"
            self.close()
            return False
        return True

    def _reader(self) -> None:
        for line in self.proc.stdout:
            line = line.strip()
            try:
                msg = json.loads(line)
            except ValueError:
                if line:
                    self.error = line[-300:]
                continue
            with self._cond:
                if "listo" in msg:
                    self.ready = msg
                elif "pong" in msg:
                    self._pongs[msg["pong"]] = msg
                elif "tocado" in msg:
                    self.played_lines.append(msg["tocado"])
                self._cond.notify_all()
        with self._cond:                                   # la sesión terminó (ssh falló o el receptor salió)
            self._eof = True
            self._cond.notify_all()

    def _write(self, obj: dict) -> bool:
        if self.proc is None or self.proc.poll() is not None:
            self.error = self.error or "el receptor del PC se cerró"
            return False
        try:
            with self._lock:
                self.proc.stdin.write(json.dumps(obj) + "\n")
                self.proc.stdin.flush()
            return True
        except (OSError, ValueError) as e:
            self.error = f"{type(e).__name__}: {e}"
            return False

    def send(self, kind: str, ch0: int, a: int, b: int = 0) -> None:
        if self._write({"v": 1, "m": [encode(kind, ch0, a, b)]}):
            self.sent += 1

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None and self.ready is not None

    def ping(self, timeout: float = 2.0) -> dict | None:
        nonce = uuid.uuid4().hex[:8]
        t0 = time.time()
        if not self._write({"v": 1, "ping": nonce}):
            return None
        with self._cond:
            self._cond.wait_for(lambda: nonce in self._pongs, timeout)
            msg = self._pongs.pop(nonce, None)
        if msg is not None:
            self.last_pong = {**msg, "rtt_ms": round(1000 * (time.time() - t0), 1), "t": time.time()}
        return self.last_pong if msg is not None else None

    def close(self) -> None:
        if self.proc is None:
            return
        self._write({"v": 1, "quit": True})
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            self.proc.kill()

    def status(self) -> dict:
        return {"sink": self.name, "sent": self.sent, "vivo": self.alive, "listo": self.ready,
                "pong": self.last_pong, "error": self.error}


# ---------------------------------------------------------------------- sintetizador del Mac

def mtof(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


class MacSynth:
    """Sintetizador mínimo (numpy + sounddevice). `render(n)` es puro (sirve en tests sin audio)."""

    def __init__(self, sr: int = 44100, start: bool = True, gain: float = 0.22):
        self.sr = sr
        self.gain = gain
        self.voices: dict[tuple[int, int], dict] = {}
        self._lock = threading.Lock()
        self.stream = None
        self.name = "MacBook (sintetizador simple)"
        self.error: str | None = None
        self.sent = 0
        if start:
            try:
                import sounddevice as sd
                self.stream = sd.OutputStream(samplerate=sr, channels=1, dtype="float32", blocksize=256,
                                              callback=self._cb)
                self.stream.start()
                self.name = f"MacBook ({sd.query_devices(kind='output')['name']})"
            except Exception as e:  # noqa: BLE001  sin dispositivo de audio: queda mudo, la mesa sigue
                self.error = f"{type(e).__name__}: {e}"

    def send(self, kind: str, ch0: int, a: int, b: int = 0) -> None:
        self.sent += 1
        with self._lock:
            key = (int(ch0), int(a))
            if kind == "note_on" and b > 0:
                self.voices[key] = {"f": mtof(a), "v": b / 127.0, "t": 0, "rel": None, "ph": 0.0, "ch": int(ch0)}
            elif kind in ("note_off", "note_on"):
                v = self.voices.get(key)
                if v is not None and v["rel"] is None:
                    v["rel"] = v["t"]

    def render(self, n: int) -> np.ndarray:
        out = np.zeros(n, np.float32)
        sr = self.sr
        with self._lock:
            dead = []
            for key, v in self.voices.items():
                i = np.arange(n, dtype=np.float64) + v["t"]
                tt = i / sr
                ph = v["ph"] + 2 * math.pi * v["f"] * np.arange(n) / sr
                ch = v["ch"]
                if ch == 1:                                        # marimba: golpe que se apaga solo
                    env = np.exp(-tt / 0.45) * np.minimum(1.0, tt / 0.003)
                    wave = np.sin(ph) + 0.25 * np.sin(4 * ph) * np.exp(-tt / 0.08)
                elif ch == 2:                                      # campana
                    env = np.exp(-tt / 1.2) * np.minimum(1.0, tt / 0.002)
                    wave = np.sin(ph) + 0.4 * np.sin(2.76 * ph)
                else:                                              # pad suave (cocodrilo): sostiene mientras se toca
                    env = np.minimum(1.0, tt / 0.03) * (0.75 + 0.25 * np.exp(-tt / 0.3))
                    wave = np.sin(ph) + 0.3 * np.sin(2 * ph) + 0.12 * np.sin(3 * ph)
                if v["rel"] is not None:
                    rel_t = np.maximum(0.0, (i - v["rel"]) / sr)
                    env = env * np.exp(-rel_t / 0.25)
                out += (v["v"] * env * wave).astype(np.float32)
                v["t"] += n
                v["ph"] = float(ph[-1] + 2 * math.pi * v["f"] / sr) % (2 * math.pi)
                life = {1: 3.0, 2: 6.0}.get(ch, 12.0)              # marimba y campana se apagan solas
                if (v["rel"] is not None and (v["t"] - v["rel"]) / sr > 1.5) or v["t"] / sr > life:
                    dead.append(key)
            for k in dead:
                self.voices.pop(k, None)
        return np.tanh(out * self.gain).astype(np.float32)

    def _cb(self, outdata, frames, time_info, status) -> None:  # noqa: ARG002
        outdata[:, 0] = self.render(frames)

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:  # noqa: BLE001
                pass
            self.stream = None

    def status(self) -> dict:
        return {"sink": self.name, "sent": self.sent, "voces": len(self.voices), "error": self.error}


class MidoPort:
    """Puerto MIDI local (virtual 'sl-scanner touch', IAC o uno por nombre) como destino."""

    def __init__(self, port, name: str):
        self.port, self.name = port, name
        self.sent = 0
        self.error: str | None = None

    def send(self, kind: str, ch0: int, a: int, b: int = 0) -> None:
        try:
            import mido
            if kind == "control_change":
                msg = mido.Message(kind, channel=ch0, control=int(a), value=int(b))
            else:
                msg = mido.Message(kind, channel=ch0, note=int(a), velocity=int(b))
            self.port.send(msg)
            self.sent += 1
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"

    def close(self) -> None:
        try:
            self.port.close()
        except Exception:  # noqa: BLE001
            pass

    def status(self) -> dict:
        return {"sink": self.name, "sent": self.sent, "error": self.error}
