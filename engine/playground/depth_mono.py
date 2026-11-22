"""Profundidad monocular (Depth Anything V2, métrico interior) sobre una foto de la Osmo, con nube de puntos.

    /opt/homebrew/bin/python3.14 -m playground.depth_mono captures/dale-XXXX/osmo/color.png [--model small|base|large]
                                   [--hfov 84] [--out reports/depth/<nombre>] [--max-side 1288]

Corre en el Python del sistema (torch 2.10 + MPS) con Transformers desde ~/hardware/models/slscan-segmentation/
semantic-python. Pesos en HF_HOME=/Volumes/Externo/hf-cache (se bajan la primera vez: small 99 MB, base 390 MB).
Salidas: depth_m.npy (metros, tamaño de la entrada), depth.png (coloreada), cloud.ply (nube XYZ+RGB con K aproximada
por el FOV horizontal: es métrica solo hasta donde lo sea el modelo y la K; sirve para comparar con la luz
estructurada, no como medida certificada).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HOME", "/Volumes/Externo/hf-cache")
sys.path.insert(0, str(Path.home() / "hardware/models/slscan-segmentation/semantic-python"))

MODELS = {"small": "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf",
          "base": "depth-anything/Depth-Anything-V2-Metric-Indoor-Base-hf",
          "large": "depth-anything/Depth-Anything-V2-Metric-Indoor-Large-hf"}


def write_ply(path: Path, xyz, rgb) -> None:
    import numpy as np
    n = len(xyz)
    with open(path, "wb") as f:
        f.write(f"ply\nformat binary_little_endian 1.0\nelement vertex {n}\nproperty float x\nproperty float y\nproperty float z\n"
                f"property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n".encode())
        rec = np.empty(n, dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
        rec["x"], rec["y"], rec["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
        rec["r"], rec["g"], rec["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
        f.write(rec.tobytes())


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("image"); p.add_argument("--model", default="base", choices=list(MODELS))
    p.add_argument("--hfov", type=float, default=84.0); p.add_argument("--out", default=None)
    p.add_argument("--max-side", type=int, default=1288, help="lado mayor para el modelo (múltiplo de 14 ideal)")
    p.add_argument("--stride", type=int, default=2, help="submuestreo de la nube")
    a = p.parse_args(argv)
    import numpy as np
    import torch
    from PIL import Image
    from transformers import pipeline
    img = Image.open(a.image).convert("RGB")
    W0, H0 = img.size
    s = min(1.0, a.max_side / max(W0, H0))
    small = img.resize((int(W0 * s) // 14 * 14, int(H0 * s) // 14 * 14), Image.BICUBIC) if s < 1 else img
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    t0 = time.time()
    pipe = pipeline("depth-estimation", model=MODELS[a.model], device=device)
    t1 = time.time()
    out = pipe(small)
    t2 = time.time()
    depth = out["predicted_depth"]
    depth = depth.squeeze().float().cpu().numpy() if hasattr(depth, "cpu") else np.asarray(depth, np.float32)
    # al tamaño original
    dep = np.array(Image.fromarray(depth).resize((W0, H0), Image.BILINEAR), np.float32)
    name = Path(a.image).resolve().parent.parent.name if Path(a.image).name == "color.png" else Path(a.image).stem
    od = Path(a.out or f"reports/depth/{name}-{a.model}"); od.mkdir(parents=True, exist_ok=True)
    np.save(od / "depth_m.npy", dep)
    lo, hi = np.percentile(dep, 1), np.percentile(dep, 99)
    # coloreado sin OpenCV (el Python del sistema no lo tiene): gradiente azul → cian → verde → amarillo → rojo
    t = np.clip((dep - lo) / max(hi - lo, 1e-6), 0, 1)
    stops = np.array([[0, 0, 130], [0, 180, 255], [0, 220, 60], [255, 230, 0], [220, 20, 20]], np.float32)
    pos = t * (len(stops) - 1); i0 = np.clip(np.floor(pos).astype(int), 0, len(stops) - 2); fr = (pos - i0)[..., None]
    vis = (stops[i0] * (1 - fr) + stops[i0 + 1] * fr).astype(np.uint8)
    Image.fromarray(vis).save(od / "depth.png")
    # nube con K aproximada
    f = 0.5 * W0 / math.tan(math.radians(a.hfov) / 2); cx, cy = W0 / 2, H0 / 2
    st = max(1, a.stride)
    vv, uu = np.mgrid[0:H0:st, 0:W0:st]
    z = dep[::st, ::st]
    x = (uu - cx) / f * z; y = (vv - cy) / f * z
    rgb = np.asarray(img)[::st, ::st].reshape(-1, 3)
    xyz = np.stack([x.ravel(), y.ravel(), z.ravel()], 1).astype(np.float32)
    ok = np.isfinite(xyz).all(1) & (xyz[:, 2] > 0.05)
    write_ply(od / "cloud.ply", xyz[ok], rgb[ok])
    rep = {"image": str(a.image), "model": MODELS[a.model], "device": device, "input_size": [small.size[0], small.size[1]],
           "load_s": round(t1 - t0, 1), "infer_s": round(t2 - t1, 2), "depth_p1_p99_m": [round(float(lo), 3), round(float(hi), 3)],
           "median_m": round(float(np.median(dep)), 3), "hfov_assumed": a.hfov, "focal_px": round(f, 1), "points": int(ok.sum()),
           "out": str(od), "metric_status": "modelo monocular + K por FOV: no certificado"}
    (od / "report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
