"""Puente MIDI PC → Mac: lee el Korg EK-50 (USB en el PC) y manda cada mensaje al Mac por UDP.

Corre en el PC, en C:\\sl-scanner con su venv (mido + python-rtmidi, API WinMM):

    .venv\\Scripts\\python.exe windows\\midi_bridge.py                        # Korg → quien mande hello (UDP 8796)
    .venv\\Scripts\\python.exe windows\\midi_bridge.py --to 192.168.1.38      # además empuja a una IP fija (el Mac)
    .venv\\Scripts\\python.exe windows\\midi_bridge.py --fake --to 192.168.1.38   # notas sintéticas, sin teclado
    .venv\\Scripts\\python.exe windows\\midi_bridge.py --list                 # puertos MIDI
    .venv\\Scripts\\python.exe windows\\midi_bridge.py --diag --seconds 20     # diagnóstico paso a paso del EK-50

Por qué UDP: un datagrama por mensaje es lo más simple y lo de menor latencia en la LAN (sin conexión, sin
reintentos: una nota que llegara tarde no sirve igual). Protocolo (JSON en UTF-8):

    PC → Mac   {"k": "m", "s": seq, "t": hora_pc, "b": [status, d1, d2]}          un mensaje MIDI
               {"k": "hb", "s": seq, "port": "...", "open": true, "fake": false, "host": "...", "n": enviados}   cada 1 s
               {"k": "pong", "t": hora_mac_del_hello, "port": "..."}               respuesta a cada hello (mide la ida y vuelta)
    Mac → PC   {"k": "hello", "t": hora_mac, "id": "..."}                          cada 1 s: suscribe (o renueva) a quien lo manda

Destinos: los que mandan hello (vigentes 10 s) y los fijos de --to (no necesitan abrir el puerto UDP del PC en el
firewall de Windows). Solo IPs de la LAN o la tailnet. Los mensajes de reloj y "active sensing" (0xF8, 0xFE) no se
mandan salvo con --all. Si el teclado se desenchufa, reintenta abrirlo cada 2 s. Sin dependencias fuera de mido.
Del lado del Mac lo recibe playground/devices/keyboard.py (BridgeMidiSource, puerto UDP 8796).
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import platform
import socket
import subprocess
import sys
import threading
import time

PORT = 8796
MATCH = ("KORG", "EK-50", "EK50", "GENERIC USB MIDI")
REALTIME = (0xF8, 0xFE)          # reloj y active sensing: ruido para el juego
SUB_TTL = 10.0
PRIVADAS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                              "100.64.0.0/10", "169.254.0.0/16", "::1/128", "fd00::/8", "fe80::/10")]


def privada(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    m = getattr(a, "ipv4_mapped", None)
    if m is not None:
        a = m
    return any(a in n for n in PRIVADAS)


def pick_port(names: list[str], prefer: str = "") -> str:
    """El puerto del Korg: --port exacto o contenido; si no, el primero que diga KORG / EK-50 / Generic USB MIDI."""
    if prefer:
        return next((n for n in names if n == prefer), "") or next((n for n in names if prefer.lower() in n.lower()), "")
    for m in MATCH:
        for n in names:
            if m in n.upper():
                return n
    return ""


def parse_target(spec: str, port: int = PORT) -> tuple[str, int]:
    host, _, p = spec.rpartition(":") if ":" in spec else (spec, "", "")
    return (host or spec), int(p) if p else port


def fake_notes(bpm: float = 100.0, lo: int = 60):
    """Generador infinito de (espera_s, bytes): escala de Do arriba y abajo, nota on y off (para probar sin teclado)."""
    escala = [0, 2, 4, 5, 7, 9, 11, 12, 11, 9, 7, 5, 4, 2]
    paso = 60.0 / bpm
    i = 0
    while True:
        n = lo + escala[i % len(escala)]
        yield 0.0, [0x90, n, 96]
        yield paso * 0.8, [0x80, n, 0]
        yield paso * 0.2, None
        i += 1


class Bridge:
    """Suscriptores + envío. `send_midi` se puede llamar desde el hilo del callback de rtmidi."""

    def __init__(self, listen_port: int = PORT, targets: list[tuple[str, int]] | None = None, send_all: bool = False,
                 log=print):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", listen_port))
        self.sock.settimeout(0.5)
        self.static = list(targets or [])
        self.subs: dict[tuple[str, int], float] = {}
        self.lock = threading.Lock()
        self.seq = 0
        self.sent = 0
        self.send_all = send_all
        self.port_name = ""
        self.open = False
        self.fake = False
        self.error = ""
        self.log = log
        self.running = True
        self.n_in = 0

    @property
    def listen_port(self) -> int:
        return self.sock.getsockname()[1]

    def targets(self) -> list[tuple[str, int]]:
        now = time.time()
        with self.lock:
            for a in [a for a, t in self.subs.items() if now - t > SUB_TTL]:
                del self.subs[a]
                self.log(f"suscriptor vencido: {a[0]}:{a[1]}")
            return list(dict.fromkeys(self.static + list(self.subs)))

    def _send(self, obj: dict) -> None:
        data = json.dumps(obj, separators=(",", ":")).encode()
        for a in self.targets():
            try:
                self.sock.sendto(data, a)
            except OSError as e:
                self.error = f"envío a {a}: {e}"

    def send_midi(self, b: list[int]) -> bool:
        if not b or (b[0] in REALTIME and not self.send_all):
            return False
        with self.lock:
            self.seq += 1; s = self.seq
        self.n_in += 1
        self._send({"k": "m", "s": s, "t": time.time(), "b": [int(x) for x in b]})
        self.sent += 1
        return True

    def heartbeat(self) -> None:
        self._send({"k": "hb", "s": self.seq, "port": self.port_name, "open": self.open, "fake": self.fake,
                    "host": socket.gethostname(), "n": self.sent, "error": self.error})

    def handle(self, data: bytes, addr) -> None:
        if not privada(addr[0]):
            return
        try:
            m = json.loads(data.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if m.get("k") == "hello":
            with self.lock:
                nuevo = addr not in self.subs
                self.subs[addr] = time.time()
            if nuevo:
                self.log(f"suscriptor: {addr[0]}:{addr[1]} ({m.get('id', '')})")
            try:
                self.sock.sendto(json.dumps({"k": "pong", "t": m.get("t"), "port": self.port_name}).encode(), addr)
            except OSError:
                pass

    def serve_forever(self) -> None:
        """Hilo: hellos entrantes y latido cada 1 s."""
        last_hb = 0.0
        while self.running:
            if time.time() - last_hb >= 1.0:
                self.heartbeat(); last_hb = time.time()
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self.handle(data, addr)

    def close(self) -> None:
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------ diagnóstico

def _ps(cmd: str, timeout: int = 15) -> str:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return f"(no se pudo: {e})"


def diag(seconds: float, port_pref: str = "") -> int:
    """Paso a paso: sistema, dispositivo USB, servicio MIDI de Windows, puertos, abrir, escuchar TODO, mandar notas."""
    import mido
    print("1. sistema:", platform.platform(), "· Python", platform.python_version(), "· mido", getattr(mido, "__version__", "?"))
    try:
        import rtmidi
        print("   python-rtmidi", getattr(rtmidi, "__version__", "?"), "· APIs:", [rtmidi.get_api_name(a) for a in rtmidi.get_compiled_api()])
    except Exception as e:  # noqa: BLE001
        print("   rtmidi no carga:", e)
    if sys.platform == "win32":
        print("2. dispositivos USB de Korg (VID 0944) y de audio/MIDI presentes:")
        print("  ", _ps("Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_0944' -or $_.Class -in 'MEDIA','SoftwareDevice' -and $_.FriendlyName -match 'MIDI|KORG|EK' } | "
                        "Select-Object Status,Class,FriendlyName,InstanceId | Format-Table -AutoSize | Out-String -Width 220").replace("\n", "\n   "))
        print("3. Windows MIDI Services (servicio midisrv; si existe, los puertos WinMM pasan por él):")
        print("  ", _ps("Get-Service midisrv -ErrorAction SilentlyContinue | Select-Object Status,StartType,DisplayName | Format-List | Out-String").replace("\n", "\n   ") or "   (no está: WinMM clásico)")
    ins, outs = mido.get_input_names(), mido.get_output_names()
    print("4. entradas:", ins)
    print("   salidas:", outs)
    name = pick_port(ins, port_pref)
    if not name:
        print("   ✗ no hay entrada del Korg. Revisar cable/puerto USB (directo, sin hub) y el paso 2.")
        return 1
    print(f"5. abriendo '{name}' (WinMM es exclusivo: si otro programa lo tiene, falla acá)")
    try:
        port = mido.open_input(name)
    except Exception as e:  # noqa: BLE001
        print("   ✗ no abre:", e, "→ cerrar Ableton/MIDI-OX/otro python que lo tenga (tareas programadas incluidas)")
        return 1
    out_name = pick_port(outs, port_pref) or next((n for n in outs if "USB MIDI" in n.upper()), "")
    if out_name:
        print(f"6. mando Do-Mi-Sol al teclado por '{out_name}' (si suenan, el USB funciona en esa dirección)")
        try:
            with mido.open_output(out_name) as o:
                for n in (60, 64, 67):
                    o.send(mido.Message("note_on", note=n, velocity=100)); time.sleep(0.3); o.send(mido.Message("note_off", note=n))
        except Exception as e:  # noqa: BLE001
            print("   salida no abre:", e)
    print(f"7. escuchando TODO por {seconds:.0f} s (incluye reloj y active sensing): toca teclas, mueve la rueda de pitch y las perillas")
    cuenta: dict[str, int] = {}
    t0 = time.time(); primeros = 0
    while time.time() - t0 < seconds:
        for msg in port.iter_pending():
            cuenta[msg.type] = cuenta.get(msg.type, 0) + 1
            if primeros < 15 and msg.type not in ("clock", "active_sensing"):
                print("   ", msg); primeros += 1
        time.sleep(0.005)
    port.close()
    print("   recibidos por tipo:", cuenta or "nada")
    if not cuenta:
        print("   ✗ el puerto abre pero no llega nada: el teclado no está mandando MIDI por USB. Ver README del Piano Hero"
              " › Diagnóstico del EK-50 (ajustes MIDI del teclado, otro puerto USB, servicio MIDI de Windows).")
        return 2
    if set(cuenta) <= {"clock", "active_sensing"}:
        print("   ~ llega reloj/active sensing pero no notas: el enlace funciona; el teclado no transmite las teclas"
              " (canal de transmisión / Local / preset MIDI del EK-50).")
        return 3
    print("   ✓ llegan notas: el puente debería funcionar.")
    return 0


# ------------------------------------------------------------------------------------------------ main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--listen", type=int, default=PORT, help="puerto UDP para hellos (y el de destino por defecto)")
    ap.add_argument("--to", action="append", default=[], help="destino fijo host[:puerto], repetible (p.ej. el Mac)")
    ap.add_argument("--port", default="", help="nombre (o parte) del puerto MIDI de entrada")
    ap.add_argument("--fake", action="store_true", help="notas sintéticas (escala de Do) en vez del teclado")
    ap.add_argument("--bpm", type=float, default=100.0, help="tempo de --fake")
    ap.add_argument("--all", action="store_true", help="mandar también reloj y active sensing")
    ap.add_argument("--list", action="store_true", help="puertos MIDI y salir")
    ap.add_argument("--diag", action="store_true", help="diagnóstico paso a paso del teclado y salir")
    ap.add_argument("--seconds", type=float, default=0.0, help="salir después de N s (0 = hasta Ctrl+C)")
    ap.add_argument("--quiet", action="store_true", help="no imprimir cada nota")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):               # por ssh la consola de Windows es cp1252: sin esto, "·" o "✓" revientan
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if a.list or a.diag:
        if a.diag:
            return diag(a.seconds or 15.0, a.port)
        import mido
        print("entradas:", mido.get_input_names()); print("salidas:", mido.get_output_names())
        return 0

    log = (lambda *x: None) if a.quiet else (lambda *x: print(time.strftime("%H:%M:%S"), *x, flush=True))
    br = Bridge(a.listen, [parse_target(t, PORT) for t in a.to], a.all, log=lambda *x: print(time.strftime("%H:%M:%S"), *x, flush=True))
    threading.Thread(target=br.serve_forever, daemon=True, name="udp").start()
    print(f"puente MIDI en UDP {br.listen_port} · destinos fijos {br.static or '-'} · {socket.gethostname()}", flush=True)
    t0 = time.time()

    def vivo() -> bool:
        return not a.seconds or time.time() - t0 < a.seconds

    try:
        if a.fake:
            br.fake = True; br.open = True; br.port_name = "fake"
            for espera, b in fake_notes(a.bpm):
                if not vivo():
                    break
                time.sleep(espera)
                if b:
                    br.send_midi(b); log("fake", b, "→", len(br.targets()), "destinos")
            return 0
        import mido
        port = None
        while vivo():
            if port is None:
                names = mido.get_input_names()
                name = pick_port(names, a.port)
                if not name:
                    br.open = False; br.port_name = ""; br.error = f"sin puerto del Korg entre {names}"
                    time.sleep(2.0); continue
                try:
                    def cb(msg, _br=br):
                        if _br.send_midi(msg.bytes()) and msg.type in ("note_on", "note_off"):
                            log(msg, "→", len(_br.targets()), "destinos")
                    port = mido.open_input(name, callback=cb)
                    br.open = True; br.port_name = name; br.error = ""
                    print(f"abierto '{name}'", flush=True)
                except Exception as e:  # noqa: BLE001
                    br.open = False; br.error = f"no abre '{name}': {e}"
                    print(br.error, flush=True); time.sleep(2.0); continue
            time.sleep(0.5)
            if name not in mido.get_input_names():         # desenchufado: cerrar y volver a buscar
                print(f"'{name}' desapareció; reintento", flush=True)
                try:
                    port.close()
                except Exception:  # noqa: BLE001
                    pass
                port = None; br.open = False
        if port is not None:
            port.close()
    except KeyboardInterrupt:
        pass
    finally:
        br.close()
        print(f"enviados {br.sent} mensajes", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
