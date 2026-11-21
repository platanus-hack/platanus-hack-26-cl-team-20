"""Barrido radiométrico con el proyector y la Osmo: proyecta los patrones de `slscan.radiometry.plan()` (negro/base,
referencia, rampas R/G/B y gris) y guarda cada captura en <sesion>/radiometry/<nombre>.png más radiometry_capture.json
(orden, niveles, hora, exposición conocida o no). Lo consume P3/P7 (radiometry.json, materials).

    python -m playground.radiometria captures/dale-XXXX [--rotate 0] [--settle 0.8] [--levels 32,64,96,144,192,240]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from slscan.radiometry import plan, pattern_image

W, H = 1920, 1080


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("sesion"); p.add_argument("--rotate", type=int, default=0); p.add_argument("--settle", type=float, default=0.8)
    p.add_argument("--levels", default="32,64,96,144,192,240"); p.add_argument("--frames", type=int, default=3)
    p.add_argument("--downscale", type=int, default=2); p.add_argument("--cam-size", default="3840x2160")
    a = p.parse_args(argv)
    sesion = Path(a.sesion); out = sesion / "radiometry"; out.mkdir(parents=True, exist_ok=True)
    levels = tuple(int(v) for v in a.levels.split(","))
    specs = plan(levels=levels, stable_frames=a.frames)
    from slscan.capture import PatternWindow, Camera
    win = PatternWindow((W, H), screen="1920x1080"); cam = Camera("osmo", size=a.cam_size, rotate=a.rotate, downscale=a.downscale)
    rep = {"levels": list(levels), "settle_s": a.settle, "frames": a.frames, "exposure": "auto (Osmo webcam, no fijable)",
           "started": time.strftime("%Y-%m-%d %H:%M:%S"), "patterns": []}
    try:
        for i, spec in enumerate(specs):
            img = pattern_image(spec, (W, H))
            win.show(img); time.sleep(a.settle); cam.flush(6)
            frames = [cam.read() for _ in range(a.frames)]
            f = np.median(np.stack(frames), axis=0).astype(np.uint8)
            name = getattr(spec, "name", f"p{i:02d}")
            cv2.imwrite(str(out / f"{name}.png"), f)
            rep["patterns"].append({"i": i, "name": name, "file": f"radiometry/{name}.png", "mean_cam": round(float(f.mean()), 2),
                                    "spec": {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in vars(spec).items() if not k.startswith("_")}})
            print(json.dumps({"stage": "radiometria", "step": name, "progress": round((i + 1) / len(specs), 3), "msg": f"{name} media {f.mean():.1f}", "ok": True}), flush=True)
    finally:
        win.close(); cam.close()
    rep["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (sesion / "radiometry_capture.json").write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({"stage": "radiometria", "step": "done", "progress": 1.0, "msg": f"{len(specs)} patrones en {out}", "ok": True}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
