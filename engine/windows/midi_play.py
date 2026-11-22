"""Receptor de notas de la mesa táctil en el PC: las toca con el sintetizador de Windows, así el sonido sale por la
mesa (el dispositivo de audio predeterminado de Windows, la QX1222USB).

    C:\\sl-scanner\\.venv\\Scripts\\python.exe windows\\midi_play.py                  # escucha UDP :8797
    ... windows\\midi_play.py --stdin                                                 # mensajes por stdin (vía ssh)
    ... windows\\midi_play.py --list                                                  # puertos MIDI de salida
    ... windows\\midi_play.py --out "GS Wavetable" --programs 1:89,2:12,3:98 --test   # 3 notas de prueba al arrancar

Desde el Mac, `playground.touchsynth` manda (sound.py):
    {"v": 1, "m": [[status, data1, data2], ...]}   note on 0x9n · note off 0x8n · CC 0xBn (n = canal 0..15)
    {"v": 1, "ping": "<nonce>"}  →  responde {"v": 1, "pong": "<nonce>", "out": "<puerto>", "n": <mensajes>}

Con `--stdin` lee una línea JSON por mensaje (mismo formato) y contesta los ping por stdout: el Mac lo lanza por ssh
y le escribe directo, sin abrir puertos en el firewall de Windows; termina solo cuando se cierra la sesión.
Por UDP solo acepta loopback, la LAN privada y la tailnet (100.64.0.0/10). Programas General MIDI por canal
(`--programs`, 0..127): cocodrilo (1) 89 "Pad 2 warm", marimba (2) 12 "Marimba", dibujo (3) 98 "FX 3 crystal".
Al cerrar (Ctrl+C) apaga todas las notas. Autocontenido: solo mido (+ python-rtmidi) del venv del PC.

Sonido por la mesa: Configuración > Sistema > Sonido > Salida > elegir la QX1222USB (o clic derecho en el
parlante de la barra de tareas > Configuración de sonido). El GS Wavetable Synth siempre suena por el dispositivo
predeterminado. Firewall (una vez, PowerShell de administrador):
    New-NetFirewallRule -Name sl-scanner-midi -DisplayName "midi mesa 8797 UDP solo LAN y tailnet" -Direction Inbound `
      -Protocol UDP -LocalPort 8797 -Action Allow -Profile Any -RemoteAddress @("LocalSubnet","100.64.0.0/10")
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import sys
import time

DEFAULT_PORT = 8797
DEFAULT_OUT = "GS Wavetable"
DEFAULT_PROGRAMS = "1:89,2:12,3:98"
TAILNET = ipaddress.ip_network("100.64.0.0/10")


def is_lan(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_loopback or a.is_private or a in TAILNET


def parse_programs(spec: str) -> dict[int, int]:
    """'1:89,2:12' → {canal 0..15: programa}."""
    out = {}
    for part in (spec or "").split(","):
        if ":" in part:
            ch, prog = part.split(":", 1)
            out[max(0, min(15, int(ch) - 1))] = max(0, min(127, int(prog)))
    return out


def to_message(raw):
    """[status, d1, d2] → mido.Message (o None si no es note on/off ni CC)."""
    import mido
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return None
    st, d1, d2 = (int(v) for v in raw[:3])
    kind, ch = st & 0xF0, st & 0x0F
    d1, d2 = d1 & 0x7F, d2 & 0x7F
    if kind == 0x90:
        return mido.Message("note_on", channel=ch, note=d1, velocity=d2)
    if kind == 0x80:
        return mido.Message("note_off", channel=ch, note=d1, velocity=0)
    if kind == 0xB0:
        return mido.Message("control_change", channel=ch, control=d1, value=d2)
    return None


class Player:
    def __init__(self, out, out_name: str, programs: dict[int, int] | None = None):
        self.out = out
        self.out_name = out_name
        self.played = 0
        self.rejected = 0
        self.last_from: str | None = None
        import mido
        for ch, prog in (programs or {}).items():
            out.send(mido.Message("program_change", channel=ch, program=prog))

    def handle(self, data: bytes, addr: tuple[str, int]) -> bytes | None:
        """Un datagrama: toca sus mensajes; si es un ping, devuelve la respuesta (bytes) para mandar de vuelta."""
        if not is_lan(addr[0]):
            self.rejected += 1
            return None
        try:
            msg = json.loads(data)
        except ValueError:
            self.rejected += 1
            return None
        self.last_from = addr[0]
        if "ping" in msg:
            return json.dumps({"v": 1, "pong": msg["ping"], "out": self.out_name, "n": self.played,
                               "host": socket.gethostname()}).encode()
        for raw in msg.get("m") or []:
            m = to_message(raw)
            if m is not None:
                self.out.send(m)
                self.played += 1
        return None

    def test_notes(self) -> None:
        import mido
        for n in (60, 64, 67):
            self.out.send(mido.Message("note_on", channel=1, note=n, velocity=100))
            time.sleep(0.35)
            self.out.send(mido.Message("note_off", channel=1, note=n))

    def panic(self) -> None:
        import mido
        for ch in range(16):
            self.out.send(mido.Message("control_change", channel=ch, control=123, value=0))   # all notes off


class PrintPort:
    """Salida de prueba (--out fake): imprime cada mensaje como JSON en stdout."""

    def send(self, m):
        print(json.dumps({"tocado": str(m)}), flush=True)

    def close(self):
        pass


def run_stdin(player: "Player", name: str, a) -> int:
    """Una línea JSON por mensaje; los ping se contestan por stdout. EOF (sesión ssh cerrada) = fin."""
    print(json.dumps({"listo": name, "programas": a.programs}), flush=True)
    if a.test:
        player.test_notes()
    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("quit"):
                    break
            except ValueError:
                continue
            reply = player.handle(line.encode(), ("127.0.0.1", 0))
            if reply:
                print(reply.decode(), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        player.panic()
        player.out.close()
        print(json.dumps({"fin": player.played}), flush=True)
    return 0


def pick_output(want: str):
    if want == "fake":
        return PrintPort(), "fake"
    import mido
    outs = mido.get_output_names()
    name = next((o for o in outs if want.lower() in o.lower()), None)
    if name is None:
        raise SystemExit(f"no hay una salida MIDI con '{want}'. Hay: {outs}")
    return mido.open_output(name), name


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="notas de la mesa táctil (UDP) → sintetizador de Windows")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--out", default=DEFAULT_OUT, help="parte del nombre del puerto MIDI de salida")
    p.add_argument("--programs", default=DEFAULT_PROGRAMS, help="canal:programa GM, separados por coma")
    p.add_argument("--list", action="store_true", help="lista los puertos de salida y termina")
    p.add_argument("--test", action="store_true", help="toca 3 notas al arrancar")
    p.add_argument("--seconds", type=float, default=0, help="termina sola después de N s (pruebas)")
    p.add_argument("--stdin", action="store_true", help="mensajes por stdin, una línea JSON cada uno (vía ssh)")
    a = p.parse_args(argv)
    if a.list:
        import mido
        print("salidas:", mido.get_output_names())
        return 0
    out, name = pick_output(a.out)
    player = Player(out, name, parse_programs(a.programs))
    if a.stdin:
        return run_stdin(player, name, a)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((a.host, a.port))
    sock.settimeout(1.0)
    print(f"midi_play: UDP {a.host}:{a.port} -> {name} (programas {a.programs})", flush=True)
    if a.test:
        player.test_notes()
        print("midi_play: 3 notas de prueba tocadas", flush=True)
    t_end = time.time() + a.seconds if a.seconds else None
    t_stat, last = time.time(), 0
    try:
        while t_end is None or time.time() < t_end:
            try:
                data, addr = sock.recvfrom(65536)
            except socket.timeout:
                data = None
            if data:
                reply = player.handle(data, addr)
                if reply:
                    sock.sendto(reply, addr)
            if time.time() - t_stat >= 5:
                if player.played != last:
                    print(f"midi_play: {player.played} mensajes tocados (desde {player.last_from})", flush=True)
                t_stat, last = time.time(), player.played
    except KeyboardInterrupt:
        pass
    finally:
        player.panic()
        out.close()
        sock.close()
        print(f"midi_play: fin ({player.played} mensajes, {player.rejected} rechazados)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
