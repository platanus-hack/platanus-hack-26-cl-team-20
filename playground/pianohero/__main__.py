"""Piano Hero proyectado: runtime propio + efecto pianohero + teclado + ventana en el proyector + API HTTP.

    python -m playground.pianohero --level 1 --song oda                 # espera una tecla para empezar
    python -m playground.pianohero --level 3 --song ~/musica/x.mid --practice
    python -m playground.pianohero --level 2 --song elisa --demo --no-window --port 0 --seconds 20 --snapshot reports/ph.png
    python -m playground.pianohero --list                               # canciones y niveles

Teclado: --midi auto (Korg en el Mac por rtmidi si está; si no, el puente UDP del PC, ver windows/midi_bridge.py) |
direct | bridge | fake (sin teclado; con --demo el juego se toca solo). La ventana va al display del proyector
(1920x1080), nunca al interno ni al Sidecar. Con la API en :8790 (si está libre) la secretaria lo maneja con
pg_pianohero y pg_state (--port 0 = sin API, p.ej. si ya corre otro runtime). Si ya hay un runtime proyectando,
mejor correr el juego como efecto dentro de él (pg_pianohero start, o pg_spawn pianohero con {"demo": true}): así no
pelean dos ventanas por el proyector. Teclas en la ventana: espacio pausa/sigue, r reinicia, 1/2/3 nivel, p práctica,
s siguiente canción, +/- offset 10 ms, q o esc sale.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time

SONG_CYCLE = ["oda", "estrellita", "elisa", "escala"]


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _serve_api(rt, host: str, port: int):
    import uvicorn
    from ..runtime import build_app
    server = uvicorn.Server(uvicorn.Config(build_app(rt), host=host, port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True, name="pianohero-api")
    th.start()
    return server


def _inst(rt):
    return next((i for i in rt.instances.values() if i.name == "pianohero"), None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m playground.pianohero", description=__doc__.splitlines()[0])
    ap.add_argument("--level", type=int, default=1, choices=(1, 2, 3))
    ap.add_argument("--song", default="oda", help="oda | estrellita | elisa | escala | ruta a un .mid")
    ap.add_argument("--practice", action="store_true", help="espera a que toques cada nota")
    ap.add_argument("--keys", default="", help="keys.json (por defecto captures/scan5 → scan2; ajusta el modelo si la confianza es baja)")
    ap.add_argument("--midi", default="auto", choices=("auto", "direct", "bridge", "fake"))
    ap.add_argument("--bridge", default="", help="host:puerto del puente MIDI (por defecto 192.168.1.233:8796 o $MIDI_BRIDGE)")
    ap.add_argument("--screen", default="1920x1080", help="display del proyector (WxH o índice)")
    ap.add_argument("--no-window", action="store_true", help="sin ventana (pruebas, snapshot)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790, help="API HTTP del runtime (0 = sin API)")
    ap.add_argument("--offset-ms", type=float, default=0.0, help="latencia proyector+puente a compensar")
    ap.add_argument("--speed", type=float, default=1.0, help="multiplica el tempo del nivel")
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--start", action="store_true", help="empezar ya (sin esperar una tecla)")
    ap.add_argument("--demo", action="store_true", help="el juego se toca solo (sin teclado)")
    ap.add_argument("--seconds", type=float, default=0.0, help="salir después de N s")
    ap.add_argument("--snapshot", default="", help="PNG del último cuadro al salir")
    ap.add_argument("--list", action="store_true", help="canciones y niveles")
    a = ap.parse_args(argv)

    from .songs import LEVELS, list_songs
    if a.list:
        print(json.dumps({"songs": list_songs(), "levels": {n: L.name for n, L in LEVELS.items()}}, indent=1, ensure_ascii=False))
        return 0

    from ..runtime import Runtime
    rt = Runtime(canvas_size=(1920, 1080), fps=a.fps, demo=False)
    if a.midi == "fake":
        rt.connect_keyboard("fake")
    else:
        print("teclado:", json.dumps(rt.connect_keyboard(a.midi, a.bridge or None)["source"], ensure_ascii=False))
    rt.pianohero({"action": "start" if (a.start or a.demo) else "state_new", "level": a.level, "song": a.song,
                  "practice": a.practice, "keys": a.keys, "offset_ms": a.offset_ms, "speed": a.speed, "demo": a.demo,
                  "prefer": "none"})
    rt.tick(0.0)                                  # primer cuadro: el efecto elige las teclas (escena o keys.json)
    out = rt.pianohero({"action": "state", "prefer": "none"})
    lay = out.get("layout", {})
    print(f"{out.get('title')} · nivel {out.get('level_name')}{' · práctica' if a.practice else ''} · "
          f"{out.get('notes_total')} notas · {out.get('duration')} s")
    print(f"teclas: {lay.get('source')} ({lay.get('method')}, confianza {lay.get('confidence')}) · carril {lay.get('lane_px')} px · HUD en {lay.get('hud_source')}")
    for w in lay.get("warnings", []):
        print("aviso:", w)

    server = None
    if a.port:
        if _port_free(a.host, a.port):
            server = _serve_api(rt, a.host, a.port)
            print(f"API en http://{a.host}:{a.port} (pg_pianohero / pg_state)")
        else:
            print(f"aviso: el puerto {a.port} está ocupado (¿otro runtime?): sigo sin API. pg_pianohero hablaría con ese otro.")

    win = None
    if not a.no_window:
        from slscan.capture import PatternWindow
        win = PatternWindow((1920, 1080), screen=a.screen, name="pianohero")
        rt.projector = {"display": win.display.get("index"), "size": [win.display["w"], win.display["h"]], "by": "pianohero"}
        print(f"proyectando en el display {win.display.get('index')} ({win.display['w']}x{win.display['h']})")
    if not (a.start or a.demo):
        print("toca una tecla del Korg (o espacio en la ventana) para empezar")

    t0 = time.time(); last = time.perf_counter(); t_print = 0.0; running = True
    try:
        while running:
            now_pc = time.perf_counter(); dt = now_pc - last; last = now_pc
            rt.tick(dt)
            if win is not None:
                win.show(rt.frame)
                running = _keys(win, rt)
            if time.time() - t_print > 2.0:
                t_print = time.time()
                inst = _inst(rt)
                if inst is not None:
                    s = inst.game.status()
                    kb = next((x.describe() for x in rt.devices.sources if getattr(x, "device_id", None) == "ek50"), {})
                    print(f"[{s['state']}] t {s['t']:6.1f}/{s['duration']} · {s['score']} pts · combo {s['combo']} · "
                          f"precisión {round(100 * s['accuracy'])}% · teclado {kb.get('source')} "
                          f"{'conectado' if kb.get('connected') else ''} {kb.get('port') or ''}", flush=True)
            if a.seconds and time.time() - t0 >= a.seconds:
                break
            time.sleep(max(0.0, 1.0 / a.fps - (time.perf_counter() - now_pc)))
    except KeyboardInterrupt:
        pass
    finally:
        if a.snapshot:
            rt.snapshot(a.snapshot); print("cuadro:", a.snapshot)
        if win is not None:
            win.close()
        rt.devices.stop()
        if server is not None:
            server.should_exit = True
        inst = _inst(rt)
        if inst is not None:
            print(json.dumps(inst.game.summary(), ensure_ascii=False))
    return 0


def _keys(win, rt) -> bool:
    """Teclado del Mac sobre la ventana del proyector. Devuelve False para salir."""
    pg = win.pg
    inst = _inst(rt)
    for ev in pg.event.get():
        if ev.type == pg.QUIT:
            return False
        if ev.type != pg.KEYDOWN or inst is None:
            continue
        k = ev.unicode.lower() if ev.unicode else ""
        if ev.key == pg.K_ESCAPE or k == "q":
            return False
        if ev.key == pg.K_SPACE:
            rt.pianohero({"action": "toggle", "prefer": "none"})
        elif k == "r":
            rt.pianohero({"action": "restart", "prefer": "none"})
        elif k in ("1", "2", "3"):
            rt.pianohero({"action": "level", "level": int(k), "prefer": "none"})
        elif k == "p":
            rt.pianohero({"action": "practice", "practice": not inst.game.practice, "prefer": "none"})
        elif k == "s":
            cur = str(inst.p.get("song"))
            nxt = SONG_CYCLE[(SONG_CYCLE.index(cur) + 1) % len(SONG_CYCLE)] if cur in SONG_CYCLE else SONG_CYCLE[0]
            rt.pianohero({"action": "song", "song": nxt, "prefer": "none"})
        elif k in ("+", "="):
            rt.pianohero({"action": "offset", "offset_ms": inst.game.offset * 1000 + 10, "prefer": "none"})
        elif k == "-":
            rt.pianohero({"action": "offset", "offset_ms": inst.game.offset * 1000 - 10, "prefer": "none"})
    return True


if __name__ == "__main__":
    sys.exit(main())
