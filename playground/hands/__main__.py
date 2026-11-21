"""CLI de manos y touch (corre en el Mac; el servicio corre en el PC: windows/hands_service.py).

    python -m playground.hands status      --pc 192.168.1.233
    python -m playground.hands watch       --pc 192.168.1.233 --seconds 10
    python -m playground.hands calib-gray  --pc 192.168.1.233 --screen 1 [--skip-bits 2] [--mm-segments captures/scan2/segments.json]
    python -m playground.hands calib-gray  --decode-only --out captures/brio_scan          # solo recalcula
    python -m playground.hands calib-dots  --pc 192.168.1.233 --screen 1 --grid 3x3
    python -m playground.hands calib-points puntos.json
    python -m playground.hands rest        --pc 192.168.1.233 --seconds 3                   # mano plana en la mesa
    python -m playground.hands run         --pc 192.168.1.233 --runtime http://127.0.0.1:8790 \\
                                           --exclude captures/scan2/workbench.json [--mode auto]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from . import calib as calib_mod

PC_DEFAULT = "192.168.1.233"


def _p(msg: str) -> None:
    print(msg, flush=True)


def _size(s: str) -> tuple[int, int]:
    w, h = (int(v) for v in s.lower().split("x"))
    return w, h


def _host(pc: str) -> str:
    return pc.split("://")[-1].split("/")[0].split(":")[0]


def _client(a):
    from .client import HandsClient, ws_url
    return HandsClient(ws_url(_host(a.pc), a.ws_port)).start()


def _finish(cal, a) -> int:
    """Métrica opcional, conserva hand_ref de una calibración anterior (vive en px de proyector) y guarda."""
    out = Path(a.calib)
    if out.exists():
        try:
            prev = calib_mod.Calibration.load(out)
            if prev.hand_ref and not cal.hand_ref:
                cal.hand_ref = prev.hand_ref
            if prev.H_proj_to_mm is not None and cal.H_proj_to_mm is None and not (a.mm_segments or a.mm_rig):
                cal.H_proj_to_mm, cal.mm_source = prev.H_proj_to_mm, prev.mm_source
        except Exception:  # noqa: BLE001  archivo viejo ilegible: se reemplaza
            pass
    if a.mm_segments:
        cal.H_proj_to_mm = calib_mod.mm_from_segments(a.mm_segments)
        cal.mm_source = f"papel en {a.mm_segments}"
    if a.mm_rig:
        cal.H_proj_to_mm = calib_mod.mm_from_rig(a.mm_rig, a.rig_projector, a.plane_z)
        cal.mm_source = f"rig {a.mm_rig} (z = {a.plane_z} mm)"
    cal.projector = a.projector
    path = cal.save(out)
    _p(json.dumps({"guardado": str(path), "source": cal.source, "stats": cal.stats, "mm": cal.mm_source,
                   "hand_ref": cal.hand_ref}, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m playground.hands")
    sp = p.add_subparsers(dest="cmd", required=True)

    def common(s, calib=True):
        s.add_argument("--pc", default=PC_DEFAULT, help="IP o nombre del PC (servicio de manos)")
        s.add_argument("--ws-port", type=int, default=8793); s.add_argument("--http-port", type=int, default=8794)
        if calib:
            s.add_argument("--calib", default=str(calib_mod.DEFAULT_PATH))

    def metric(s):
        s.add_argument("--projector", default="proj0")
        s.add_argument("--mm-segments", help="segments.json con la hoja carta ('papel') → mm")
        s.add_argument("--mm-rig", help="rig.json métrico → mm sobre el plano z de la mesa")
        s.add_argument("--rig-projector"); s.add_argument("--plane-z", type=float, default=0.0)

    common(sp.add_parser("status", help="GET /health del servicio"), calib=False)
    s = sp.add_parser("watch", help="imprime manos, fps y latencia"); common(s, calib=False)
    s.add_argument("--seconds", type=float, default=10.0)

    s = sp.add_parser("calib-gray", help="secuencia gray code vista por la Brio → homografía de la mesa"); common(s); metric(s)
    s.add_argument("--out", default="captures/brio_scan"); s.add_argument("--screen", default="1")
    s.add_argument("--proj-size", default="1920x1080"); s.add_argument("--settle", type=float, default=0.6)
    s.add_argument("--fresh", type=int, default=3); s.add_argument("--skip-bits", type=int, default=2)
    s.add_argument("--threshold", type=int, default=5); s.add_argument("--ransac-px", type=float, default=4.0)
    s.add_argument("--decode-only", action="store_true", help="no captura: decodifica <out>/brio")

    s = sp.add_parser("calib-dots", help="puntos proyectados de a uno → homografía (respaldo rápido)"); common(s); metric(s)
    s.add_argument("--screen", default="1"); s.add_argument("--proj-size", default="1920x1080")
    s.add_argument("--grid", default="3x3"); s.add_argument("--inset", type=int, default=160)
    s.add_argument("--settle", type=float, default=0.5)

    s = sp.add_parser("calib-points", help="JSON con ≥ 4 puntos brio↔proj (respaldo a mano)"); s.add_argument("points")
    s.add_argument("--calib", default=str(calib_mod.DEFAULT_PATH)); metric(s)

    s = sp.add_parser("rest", help="mide la palma apoyada (referencia del modo 'apoyo')"); common(s)
    s.add_argument("--seconds", type=float, default=3.0)

    s = sp.add_parser("run", help="manos → toques → runtime"); common(s)
    s.add_argument("--runtime", help="URL del runtime (http://127.0.0.1:8790); sin esto solo imprime")
    s.add_argument("--projector", default="proj0")
    s.add_argument("--mode", default="auto", choices=["auto", "pinch", "apoyo"])
    s.add_argument("--exclude", help="workbench.json o segments.json con zonas a excluir")
    s.add_argument("--exclude-names", help="solo estos objetos (coma); por defecto todos menos mesa y papel")
    s.add_argument("--margin", type=float, default=8.0)
    s.add_argument("--deadband", type=float, default=3.0); s.add_argument("--ema", type=float, default=0.5)
    s.add_argument("--ratio-down", type=float, default=1.06); s.add_argument("--ratio-up", type=float, default=1.12)
    s.add_argument("--seconds", type=float)
    a = p.parse_args(argv)

    if a.cmd == "status":
        url = f"{calib_mod.http_base(_host(a.pc), a.http_port)}/health"
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                _p(json.dumps(json.loads(r.read()), indent=1, ensure_ascii=False))
        except OSError as e:
            _p(f"el servicio de manos no responde en {url}: {e}")
            return 1
        return 0

    if a.cmd == "watch":
        c = _client(a)
        t_end = time.time() + a.seconds
        last = None
        try:
            while time.time() < t_end:
                f = c.wait(1.0, after_seq=last)
                if f is None:
                    _p(f"sin mensajes · {c.status()['error']}")
                    continue
                last = f.seq
                if f.seq % 15 == 0:
                    st = c.status()
                    hs = " ".join(f"{h.id}:{h.handed}@({h.tip_index[0]:.0f},{h.tip_index[1]:.0f}){'·pinch' if h.pinch else ''}"
                                  for h in f.hands) or "-"
                    _p(f"seq {f.seq} · {st['fps']} fps · latencia {st['latency_ms']} ms · {hs}")
        finally:
            c.close()
        return 0

    if a.cmd == "calib-gray":
        folder = Path(a.out) / "brio"
        if not a.decode_only:
            folder = calib_mod.capture_graycode(_host(a.pc), a.out, screen=a.screen, proj_size=_size(a.proj_size),
                                                settle=a.settle, fresh=a.fresh, http_port=a.http_port, log=_p)
        cal = calib_mod.from_graycode(folder, threshold=a.threshold, skip_bits=a.skip_bits, ransac_px=a.ransac_px,
                                      proj_size=_size(a.proj_size), projector=a.projector)
        return _finish(cal, a)

    if a.cmd == "calib-dots":
        cal = calib_mod.capture_dots(_host(a.pc), screen=a.screen, proj_size=_size(a.proj_size), grid=_size(a.grid),
                                     inset=a.inset, settle=a.settle, http_port=a.http_port, log=_p)
        return _finish(cal, a)

    if a.cmd == "calib-points":
        return _finish(calib_mod.from_points_file(a.points), a)

    if a.cmd == "rest":
        cal = calib_mod.Calibration.load(a.calib)
        c = _client(a)
        _p(f"apoya la mano plana sobre la mesa y no la muevas ({a.seconds:.0f} s)…")
        frames, last, t_end = [], None, time.time() + a.seconds
        try:
            while time.time() < t_end:
                f = c.wait(1.0, after_seq=last)
                if f is not None:
                    last = f.seq
                    frames.append(f)
        finally:
            c.close()
        cal.hand_ref = round(calib_mod.hand_ref_from_frames(frames, cal), 2)
        cal.save(a.calib)
        _p(json.dumps({"hand_ref_px_proyector": cal.hand_ref, "muestras": sum(len(f.hands) for f in frames),
                       "guardado": a.calib}, ensure_ascii=False))
        return 0

    if a.cmd == "run":
        from .bridge import TouchBridge, run_loop
        from .touch import TouchConfig, TouchTracker, Zones
        cal = calib_mod.Calibration.load(a.calib)
        zones = None
        if a.exclude:
            names = [n.strip() for n in a.exclude_names.split(",")] if a.exclude_names else None
            zones = Zones.from_file(a.exclude, names=names, margin=a.margin)
            _p(f"zonas excluidas: {sorted(zones.polys)}")
        cfg = TouchConfig(mode=a.mode, deadband_px=a.deadband, ema_alpha=a.ema, ratio_down=a.ratio_down,
                          ratio_up=a.ratio_up)
        tracker = TouchTracker(cal, cfg, zones)
        bridge = TouchBridge(a.runtime, projector=a.projector) if a.runtime else None
        c = _client(a)

        def show(ev, lat_ms):
            mm = f" · {ev.mm[0]:.0f},{ev.mm[1]:.0f} mm" if ev.mm else ""
            _p(f"{ev.state:>4} {ev.id} ({ev.x:.0f}, {ev.y:.0f}) px{mm} · {ev.trigger} · mano {ev.hand} · {lat_ms:.0f} ms")

        _p(f"touch: modo {a.mode}, calib {cal.source} ({a.calib}), runtime {a.runtime or '(solo imprime)'}; Ctrl+C para salir")
        try:
            stats = run_loop(c, tracker, bridge, seconds=a.seconds, on_event=show)
        except KeyboardInterrupt:
            stats = {"interrumpido": True, "client": c.status(), "bridge": bridge.status() if bridge else None}
        finally:
            c.close()
            if bridge is not None:
                bridge.close()
        _p(json.dumps(stats, ensure_ascii=False, default=str))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
