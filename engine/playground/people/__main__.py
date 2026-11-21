"""CLI de personas (pedido 046). Corre en el venv /Volumes/Externo/venvs/david (onnxruntime, coremltools, mediapipe;
torch y onnx2torch solo para exportar a CoreML).

    PY=/Volumes/Externo/venvs/david/bin/python
    $PY -m playground.people bench   [--tasks normal,alpha] [--size 384] [--backend coreml|onnx]
    $PY -m playground.people export  --tasks normal,alpha,depth --size 384
    $PY -m playground.people offline captures/david_test/bharatanatyam_doble.webm --start 20 --dur 12 \\
                                     --out reports/david/bharatanatyam [--size 512] [--width 960]

`offline` procesa cada cuadro (sin apuro de tiempo real) y deja en --out: antes_despues.mp4 (cámara | reiluminada),
capas.mp4 (cámara, normal, recorte, reiluminada), proyector.mp4 (solo la luz que pintaría el proyector), fotos PNG y
stats.json con ms por etapa.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np


def _p(msg: str) -> None:
    print(msg, flush=True)


def _rgb(s: str) -> tuple[float, float, float]:
    """'255,200,140' (RGB 0-255) -> BGR lineal 0..1."""
    r, g, b = (float(v) / 255 for v in s.split(","))
    return (b * b, g * g, r * r)


def cmd_bench(a) -> int:
    from . import david
    _p(json.dumps(david.bench(tuple(a.tasks.split(",")), a.backend, a.size, n=a.n, providers=a.providers)))
    return 0


def cmd_export(a) -> int:
    from . import david
    for t in a.tasks.split(","):
        t0 = time.time()
        _p(f"{t} {a.size}: {david.export_coreml(t, a.size)} ({time.time() - t0:.0f} s)")
    return 0


class _Writer:
    """mp4 h264 por ffmpeg (cv2 en el Mac no trae h264)."""

    def __init__(self, path: Path, size: tuple[int, int], fps: float):
        self.p = subprocess.Popen(["nice", "-n", "10", "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                                   "-s", f"{size[0]}x{size[1]}", "-r", f"{fps}", "-i", "-", "-c:v", "libx264",
                                   "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium", "-movflags", "+faststart",
                                   str(path)], stdin=subprocess.PIPE)

    def write(self, img: np.ndarray) -> None:
        self.p.stdin.write(np.ascontiguousarray(img).tobytes())

    def close(self) -> None:
        self.p.stdin.close(); self.p.wait()


def _label(img: np.ndarray, text: str) -> np.ndarray:
    img = img.copy()
    s = max(0.5, img.shape[1] / 1100)
    cv2.putText(img, text, (int(14 * s), int(34 * s)), cv2.FONT_HERSHEY_SIMPLEX, s, (0, 0, 0), int(4 * s), cv2.LINE_AA)
    cv2.putText(img, text, (int(14 * s), int(34 * s)), cv2.FONT_HERSHEY_SIMPLEX, s, (255, 255, 255), max(1, int(1.5 * s)), cv2.LINE_AA)
    return img


def cmd_offline(a) -> int:
    from . import david, relight
    from .hands_mp import Hands
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.set(cv2.CAP_PROP_POS_MSEC, a.start * 1000)
    n_frames = int(a.dur * fps)
    tasks = tuple(a.tasks.split(","))
    model = david.David(tasks, a.backend, a.size, providers=a.providers)
    hands = Hands(num_hands=a.hands)
    color = _rgb(a.color)
    colors = [color, _rgb(a.color2)] if a.color2 else [color]
    writers: dict[str, _Writer] = {}
    stats = {"david_ms": [], "hands_ms": [], "relight_ms": [], "n_hands": []}
    prev = None
    for i in range(n_frames):
        ok, f = cap.read()
        if not ok:
            break
        h0, w0 = f.shape[:2]
        if w0 != a.width:
            f = cv2.resize(f, (a.width, int(h0 * a.width / w0) // 2 * 2), interpolation=cv2.INTER_AREA)
        h, w = f.shape[:2]
        t0 = time.perf_counter(); res = model.infer(f); t1 = time.perf_counter()
        hs = hands(f, int(i * 1000 / fps)); t2 = time.perf_counter()
        n = res["normal"]; al = res.get("alpha", np.ones((h, w), np.float32)); dp = res.get("depth")
        if prev is not None and a.smooth > 0:                 # EMA temporal: la normal no parpadea
            n = n * (1 - a.smooth) + prev[0] * a.smooth
            n /= np.linalg.norm(n, axis=-1, keepdims=True) + 1e-6
            al = al * (1 - a.smooth) + prev[1] * a.smooth
        prev = (n, al)
        ni = relight.to_image_frame(n)
        z = relight.depth_to_z(dp, al, (h, w), relief_px=w * a.relief)
        lights = []
        for k, hd in enumerate(hs):
            lights += relight.finger_lights([hd], z, color=colors[k % len(colors)], intensity=a.intensity,
                                            lift_px=w * 0.03, cone_deg=a.cone, radius_px=w * a.radius)
        shade = relight.light_map(ni, z, lights, spec=a.spec)
        lit = relight.compose_screen(f, al, shade, lights, ambient=a.ambient, background=a.background,
                                     exposure=a.exposure)
        proj = relight.compose_projector(al, shade, exposure=a.exposure)
        t3 = time.perf_counter()
        stats["david_ms"].append((t1 - t0) * 1000); stats["hands_ms"].append((t2 - t1) * 1000)
        stats["relight_ms"].append((t3 - t2) * 1000); stats["n_hands"].append(len(hs))
        nrgb = david.normal_to_rgb(n, al)
        argb = cv2.cvtColor((al * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        ba = np.hstack([_label(f, "camara"), _label(lit, "DAViD + focos en los dedos")])
        grid = np.vstack([np.hstack([_label(f, "camara"), _label(nrgb, "normal (DAViD)")]),
                          np.hstack([_label(argb, "recorte"), _label(lit, "reiluminada")])])
        if not writers:
            writers = {"antes_despues": _Writer(out / "antes_despues.mp4", (ba.shape[1], ba.shape[0]), fps),
                       "capas": _Writer(out / "capas.mp4", (grid.shape[1], grid.shape[0]), fps),
                       "proyector": _Writer(out / "proyector.mp4", (w, h), fps)}
        writers["antes_despues"].write(ba); writers["capas"].write(grid); writers["proyector"].write(proj)
        if i in (int(n_frames * 0.25), int(n_frames * 0.6), int(n_frames * 0.9)):
            cv2.imwrite(str(out / f"capas_{i:04d}.jpg"), grid, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if i % 25 == 0:
            _p(f"{i}/{n_frames}  david {stats['david_ms'][-1]:.0f} ms  manos {stats['hands_ms'][-1]:.0f} ms  "
               f"luz {stats['relight_ms'][-1]:.0f} ms  manos={len(hs)}")
    for wr in writers.values():
        wr.close()
    med = {k: round(float(np.median(v)), 1) for k, v in stats.items() if v}
    summary = {"video": a.video, "start": a.start, "frames": len(stats["david_ms"]), "fps_video": fps,
               "work_size": [w, h], "tasks": list(tasks), "backend": a.backend, "size": a.size,
               "ms_median": med, "frames_with_hands": int(sum(1 for v in stats["n_hands"] if v)),
               "model_ms": {k: round(v, 1) for k, v in model.ms.items()}}
    (out / "stats.json").write_text(json.dumps(summary, indent=2))
    _p(json.dumps(summary))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m playground.people")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bench"); b.add_argument("--tasks", default="normal"); b.add_argument("--size", type=int, default=384)
    b.add_argument("--backend", default="coreml"); b.add_argument("--providers", default="cpu"); b.add_argument("--n", type=int, default=20)
    e = sub.add_parser("export"); e.add_argument("--tasks", default="normal,alpha"); e.add_argument("--size", type=int, default=384)
    o = sub.add_parser("offline"); o.add_argument("video"); o.add_argument("--out", required=True)
    o.add_argument("--start", type=float, default=0); o.add_argument("--dur", type=float, default=10)
    o.add_argument("--width", type=int, default=960); o.add_argument("--size", type=int, default=512)
    o.add_argument("--tasks", default="normal,alpha,depth"); o.add_argument("--backend", default="coreml")
    o.add_argument("--providers", default="cpu"); o.add_argument("--hands", type=int, default=4)
    o.add_argument("--color", default="255,190,120", help="RGB 0-255 del foco de la primera mano")
    o.add_argument("--color2", default="120,200,255", help="RGB de la segunda mano ('' = igual a la primera)")
    o.add_argument("--intensity", type=float, default=3.5)
    o.add_argument("--cone", type=float, default=300, help="apertura del foco en grados (360 = omni)")
    o.add_argument("--radius", type=float, default=0.32, help="caída de la luz, en fracción del ancho")
    o.add_argument("--relief", type=float, default=0.12, help="relieve z de la persona, en fracción del ancho")
    o.add_argument("--spec", type=float, default=0.35); o.add_argument("--ambient", type=float, default=0.04)
    o.add_argument("--background", type=float, default=0.025); o.add_argument("--exposure", type=float, default=2.5)
    o.add_argument("--smooth", type=float, default=0.35)
    a = ap.parse_args(argv)
    return {"bench": cmd_bench, "export": cmd_export, "offline": cmd_offline}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
