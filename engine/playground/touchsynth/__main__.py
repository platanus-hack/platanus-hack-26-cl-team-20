"""CLI de la mesa táctil musical.

    python -m playground.touchsynth live    [--modes croc,draw,vivo,linterna] [--ver]      (runtime en :8790)
    python -m playground.touchsynth preview [--scan captures/scan5] [--out reports/touchsynth_preview.png]
    python -m playground.touchsynth zones   [--scan captures/scan5]

`live`: cámaras + fusión → runtime (que proyecta y suena; ver live.py). `preview`: sin cámaras ni proyector, toques
sintéticos sobre el cocodrilo, la marimba, un trazo y una mano linterna → PNG del lienzo. `zones`: zonas del
cocodrilo (notas, cabeza, bbox) y rectángulo de la marimba, en JSON.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

from .scan import default_scan_dir


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m playground.touchsynth")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("live", help="cámaras → fusión → runtime (el runtime proyecta; aquí no se abren ventanas)")
    s.add_argument("--scan", default=str(default_scan_dir()))
    s.add_argument("--runtime", default="http://127.0.0.1:8790", help="API del runtime (python -m playground serve)")
    s.add_argument("--modes", default="croc,draw", help="croc, draw, marimba, linterna, vivo (coma); se spawnean ahí")
    s.add_argument("--keep", action="store_true", help="no matar los efectos al salir")
    s.add_argument("--no-osmo", dest="osmo", action="store_false")
    s.add_argument("--camera", default="osmo"); s.add_argument("--rotate", type=int, default=270)
    s.add_argument("--osmo-scale", type=int, default=3, help="reducción de la vista del proyector para detectar")
    s.add_argument("--model", default="hibrido", choices=["hibrido", "proyeccion", "fondo"])
    s.add_argument("--settle", type=float, default=0.8, help="espera tras cada tablero al medir la proyección")
    s.add_argument("--cam-retries", type=int, default=6)
    s.add_argument("--osmo-contact", default="auto", choices=["auto", "sombra", "presencia"])
    s.add_argument("--brio-ws", default="ws://192.168.1.233:8793", help="servicio de manos del PC; 'none' = sin Brio")
    s.add_argument("--brio-calib", default="captures/brio_calib.json")
    s.add_argument("--brio-mode", default="auto", choices=["auto", "pinch", "apoyo"])
    s.add_argument("--luz-base", type=int, default=18, help="gris mínimo en todo el lienzo (efecto touch_marks), 0 = no")
    s.add_argument("--ver", action="store_true", help="anillo en cada punta detectada (verde = toca)")
    s.add_argument("--seconds", type=float, default=0)
    s.add_argument("--status-every", type=float, default=3.0)
    s.add_argument("-v", "--verbose", action="store_true", help="imprime cada toque")
    s.add_argument("--debug-dir", default="", help="guarda la vista de la Osmo con máscaras (PRIVADO: reports/…, borrar)")
    s.add_argument("--debug-every", type=float, default=2.0)

    s = sp.add_parser("preview", help="toques sintéticos → PNG del lienzo (sin cámaras ni proyector)")
    s.add_argument("--scan", default=str(default_scan_dir()))
    s.add_argument("--modes", default="croc,draw,marimba,linterna")
    s.add_argument("--out", default="reports/touchsynth_preview.png")

    s = sp.add_parser("zones", help="zonas del cocodrilo y marimba en JSON")
    s.add_argument("--scan", default=str(default_scan_dir()))
    s.add_argument("--zones", type=int, default=10)
    a = p.parse_args(argv)

    if a.cmd == "live":
        from .live import Live
        return Live(a).run()

    if a.cmd == "zones":
        from .croc import CrocSynth
        from .marimba import Marimba
        from .music import FakeMidi, set_midi
        set_midi(FakeMidi())
        c = CrocSynth(params={"scan": a.scan, "zones": a.zones})
        m = Marimba(scan=c.scan, params={"scan": a.scan})
        out = {"cocodrilo": c.status(), "centros": [[round(x), round(y)] for x, y in c.zones.centers],
               "marimba": m.status()}
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    if a.cmd == "preview":
        import cv2
        from ..runtime import Runtime
        from .live import MODES
        from .music import FakeMidi, get_midi, set_midi
        set_midi(FakeMidi())
        rt = Runtime(demo=False, canvas_size=(1920, 1080))
        for m in [m.strip() for m in a.modes.split(",") if m.strip()]:
            rt.spawn(MODES.get(m, m), None, {"scan": a.scan})
        rt.tick(0.03)
        reg = rt.command("touches")["touchsynth"]["instrumentos"]
        now = time.time()
        # cocodrilo: zonas 0, 4 y 8 (la 8 queda sostenida)
        for z in (0, 4):
            rt.command("touch", {"state": "tap", "target": f"croc:{z}", "id": f"c{z}"})
        rt.command("touch", {"state": "down", "target": "croc:8", "id": "c8"})
        # marimba: dos golpes
        for b in (1, 5):
            rt.command("touch", {"state": "tap", "target": f"marimba:{b}", "id": f"m{b}", "velocity": 0.9})
        # un trazo en espiral sobre la mesa (abajo a la izquierda del cocodrilo)
        rt.command("touch", {"state": "down", "x": 300, "y": 980, "id": "d1", "t": now})
        for k in range(1, 60):
            ang = k * 0.25
            rt.command("touch", {"state": "move", "x": 300 + 8 * k * math.cos(ang) * 0.6, "y": 900 + 5 * k * math.sin(ang) * 0.6,
                                 "id": "d1", "t": now + k * 0.02})
        rt.command("touch", {"state": "up", "x": 300, "y": 900, "id": "d1"})
        # mano linterna sobre el Buda
        rt.command("hands", {"hands": [{"id": "1", "x": 1050, "y": 420, "height": 0.35, "dir": [-0.7, -0.7]}]})
        for _ in range(4):
            rt.tick(0.05)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(a.out, rt.frame)
        st = rt.command("touches")
        print(json.dumps({"png": a.out, "instrumentos": {k: v.get("kind") for k, v in reg.items()},
                          "midi": get_midi().messages[:12], "estado": st["touchsynth"]["instrumentos"]},
                         ensure_ascii=False, default=str)[:3000])
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
