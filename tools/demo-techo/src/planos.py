"""Segmenta la superficie proyectada en planos (RANSAC secuencial de homografías sobre la LUT proyector→cámara).

uso: python -I planos.py <dir_calib> <dir_escena>
Agrega a escena.json: planos[] (contorno en px del proyector, área, error con su homografía), y guarda planos.png.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

C, O = Path(sys.argv[1]), Path(sys.argv[2])
lut = np.load(C / "lut_proj_main_to_cam_main.npy")
PH, PW = lut.shape[:2]
S = 6
vv, uu = np.mgrid[0:PH:S, 0:PW:S]
cam = lut[::S, ::S]
ok = np.isfinite(cam).all(-1)
gh, gw = ok.shape
P = np.stack([uu, vv], -1).astype(np.float32)


def err(H, Pq, Qc):
    pr = cv2.perspectiveTransform(Qc.reshape(-1, 1, 2), H).reshape(-1, 2)
    return np.linalg.norm(pr - Pq, axis=1)


free = ok.copy()
labels = np.full(ok.shape, -1, np.int32)
planes = []
rng = np.random.default_rng(1)
for k in range(5):
    idx = np.flatnonzero(free)
    if len(idx) < 400:
        break
    sub = rng.choice(idx, min(len(idx), 6000), replace=False)
    Pq, Qc = P.reshape(-1, 2)[sub], cam.reshape(-1, 2)[sub].astype(np.float32)
    H, inl = cv2.findHomography(Qc, Pq, cv2.RANSAC, 4.0, maxIters=4000)
    if H is None or inl.sum() < 300:
        break
    e = np.full(ok.shape, np.inf, np.float32)
    e[ok] = err(H, P[ok], cam[ok].astype(np.float32))
    m = (e < 5.0) & free
    # región conexa más grande (un plano físico = una región)
    m8 = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m8)
    if n < 2:
        break
    big = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    reg = lab == big
    if reg.sum() < 400:
        free &= ~m
        continue
    # reajuste con todos los puntos de la región
    H2, _ = cv2.findHomography(cam[reg].astype(np.float32), P[reg], cv2.LMEDS)
    e2 = err(H2, P[reg], cam[reg].astype(np.float32))
    regc = cv2.morphologyEx(reg.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cs, _ = cv2.findContours(regc, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    c = max(cs, key=cv2.contourArea)
    c = cv2.approxPolyDP(c, 2.0, True).reshape(-1, 2) * S
    labels[reg] = len(planes)
    planes.append({"id": len(planes), "area_pct": round(100 * reg.sum() / ok.sum(), 1),
                   "error_px_proy": {"p50": round(float(np.median(e2)), 2), "p95": round(float(np.percentile(e2, 95)), 2)},
                   "contorno_px": c.tolist(), "centro_px": [float(uu[reg].mean()), float(vv[reg].mean())],
                   "homografia_cam_proj": (H2 / H2[2, 2]).flatten().tolist()})
    free &= ~reg
    print(f"plano {len(planes) - 1}: {planes[-1]['area_pct']} % · error p50 {planes[-1]['error_px_proy']['p50']} p95 "
          f"{planes[-1]['error_px_proy']['p95']} px · {len(c)} vértices")

esc = json.loads((O / "escena.json").read_text())
esc["planos_segmentados"] = planes
esc["cobertura_planos_pct"] = round(100 * (labels >= 0).sum() / ok.sum(), 1)
(O / "escena.json").write_text(json.dumps(esc, ensure_ascii=False, indent=1))
pal = np.array([[255, 120, 0], [0, 200, 255], [80, 255, 80], [255, 0, 200], [0, 80, 255]], np.uint8)
vis = np.zeros((gh, gw, 3), np.uint8)
for i in range(len(planes)):
    vis[labels == i] = pal[i]
vis[~ok] = 0
vis = cv2.resize(vis, (PW // 2, PH // 2), interpolation=cv2.INTER_NEAREST)
for p in planes:
    cv2.polylines(vis, [np.array(p["contorno_px"], np.int32) // 2], True, (255, 255, 255), 2)
cv2.imwrite(str(O / "planos.png"), vis)
print("cobertura de planos:", esc["cobertura_planos_pct"], "%")
