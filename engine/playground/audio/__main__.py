"""CLI: python -m playground.audio devices | run | latency"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="playground.audio")
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("devices", help="entradas de audio en orden de preferencia y cuál se elegiría")
    s.add_argument("--json", action="store_true"); s.add_argument("--no-probe", action="store_true")
    s = sp.add_parser("run", help="captura, analiza a ~50 Hz y publica al runtime")
    s.add_argument("--device", default=None, help="índice o parte del nombre (por defecto: QX1222 > loopback > micrófono)")
    s.add_argument("--url", default=None, help="runtime (por defecto PLAYGROUND_URL o http://127.0.0.1:8790)")
    s.add_argument("--rate", type=float, default=50.0)
    s.add_argument("--channel", type=int, default=-1,
                   help="canal (0 = primero) que hace de mezcla; -1 = promedio de todos. Igual se analiza cada canal")
    s.add_argument("--samplerate", type=int, default=None); s.add_argument("--seconds", type=float, default=None)
    s.add_argument("--no-post", action="store_true", help="solo medir (no publica)")
    s.add_argument("--no-tempo", action="store_true")
    s.add_argument("--meter", action="store_true", help="medidor en una línea (por defecto si es terminal)")
    s.add_argument("--log", action="store_true", help="una línea cada --log-every s en vez del medidor")
    s.add_argument("--log-every", type=float, default=5.0)
    s = sp.add_parser("latency", help="mide latencia con golpes graves por el parlante (lazo acústico)")
    s.add_argument("--device", default=None); s.add_argument("--clicks", type=int, default=6)
    s.add_argument("--amp", type=float, default=0.12)
    a = p.parse_args(argv)

    from . import capture
    if a.cmd == "devices":
        devs = capture.input_devices()
        try:
            chosen = capture.choose_device(None, probe_loopback=not a.no_probe)
        except RuntimeError as e:
            chosen = {"index": None, "why": str(e)}
        if a.json:
            print(json.dumps({"devices": devs, "chosen": chosen,
                              "multichannel": [d for d in devs if d.get("multichannel")]}, ensure_ascii=False, indent=1))
            return 0
        for d in devs:
            mark = "→" if d["index"] == chosen.get("index") else " "
            multi = "  MULTICANAL: cada entrada se analiza aparte" if d.get("multichannel") else ""
            print(f"{mark} #{d['index']:<2} {d['name']:<36} {d['inputs']:>2} in  {d['samplerate']} Hz  "
                  f"{d['latency_ms']:5.1f} ms  [{d['kind']}]{multi}")
        print(f"elegido: #{chosen.get('index')} ({chosen.get('why')})")
        multi = [d for d in devs if d.get("multichannel")]
        print("con más de 2 entradas: " + (", ".join(f"#{d['index']} {d['name']} ({d['inputs']})" for d in multi)
                                         if multi else "ninguno (la QX1222USB entrega solo L/R; ver playground/audio/README.md)"))
        return 0
    if a.cmd == "latency":
        print(json.dumps(capture.measure_latency(a.device, a.clicks, amp=a.amp), ensure_ascii=False, indent=1))
        return 0
    url = a.url or capture.DEFAULT_URL
    meter = True if a.meter else (False if a.log else None)
    try:
        out = capture.run(a.device, url, a.rate, a.channel, post=not a.no_post, meter=meter, seconds=a.seconds,
                          samplerate=a.samplerate, tempo=not a.no_tempo, log_every=a.log_every)
    except KeyboardInterrupt:
        return 0
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
