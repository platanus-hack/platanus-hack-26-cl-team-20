"""CLI: python -m playground serve|snapshot|mcp|effects"""

from __future__ import annotations

import argparse
import os
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="playground")
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("serve", help="runtime + API HTTP (y ventana opcional)")
    s.add_argument("--port", type=int, default=8790); s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--window", action="store_true"); s.add_argument("--no-demo", action="store_true")
    s.add_argument("--size", default="1920x1080"); s.add_argument("--scene", help="scene.json en vez de la demo")
    s.add_argument("--scan", default=os.environ.get("SLSCAN_SCAN", "captures/scan5"), help="escaneo para la escena real (carpeta con segments.json); '' = demo")
    s.add_argument("--projector", nargs="?", const="1920x1080", default=None, help="sacar el lienzo al proyector (WxH, por defecto 1920x1080)")
    s = sp.add_parser("snapshot", help="corre N segundos sin servidor y guarda un PNG")
    s.add_argument("--out", default="reports/playground_snapshot.png"); s.add_argument("--seconds", type=float, default=2.0)
    s.add_argument("--spawn", action="append", default=[], help="efecto[:objeto], repetible")
    sp.add_parser("mcp", help="servidor MCP por stdio")
    s = sp.add_parser("vnc", help="cliente VNC propio: info|screenshot|click|key (host y contraseña del llavero)")
    s.add_argument("vnc_args", nargs=argparse.REMAINDER)
    sp.add_parser("effects", help="lista la galería")
    a = p.parse_args(argv)

    if a.cmd == "vnc":
        from .vnc import main as vnc_main
        return vnc_main(a.vnc_args)
    if a.cmd == "mcp":
        from .mcp_server import mcp
        mcp.run()
        return 0

    from .runtime import Runtime, serve
    from .scene import Scene
    size = tuple(int(v) for v in getattr(a, "size", "1920x1080").split("x"))
    scene = Scene.load(a.scene) if getattr(a, "scene", None) else None
    scan = getattr(a, "scan", None) if hasattr(a, "scan") else os.environ.get("SLSCAN_SCAN", "captures/scan5")
    if scene is None and scan and (Path(scan) / "segments.json").exists():
        from .scene import scene_from_scan
        scene = scene_from_scan(scan)
    demo = not getattr(a, "no_demo", False) and not (scene is not None and getattr(scene, "projector_px", False))
    rt = Runtime(scene=scene, canvas_size=size, demo=demo)
    if a.cmd == "effects":
        print(json.dumps(rt.command("list_effects"), indent=2, ensure_ascii=False))
        return 0
    if a.cmd == "snapshot":
        for spec in a.spawn:
            eff, _, tgt = spec.partition(":")
            rt.spawn(eff, tgt or None)
        rt.run(seconds=a.seconds)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        print(rt.snapshot(a.out), f"({rt.stats['ticks']} ticks)")
        return 0
    print(f"playground en http://{a.host}:{a.port}/api/state  (q en la ventana para salir)")
    serve(rt, a.host, a.port, window=a.window, projector=getattr(a, "projector", None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
