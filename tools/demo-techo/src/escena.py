"""Entiende la escena del techo a partir de la calibración real (LUT proyector→cámara de pcx-calib).

uso: python -I escena.py <dir_calib> <dir_salida>
Salida:
- escena.json: viga (polilíneas en px y mm del proyector), rejilla (círculo), errores por plano, homografía cam→proy.
- cam2proj.npy: mapa inverso cámara→proyector (720×1280×2 f32, NaN = sin dato) para las manos.
- imágenes de diagnóstico.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

C, O = Path(sys.argv[1]), Path(sys.argv[2])
O.mkdir(parents=True, exist_ok=True)
lut = np.load(C / "lut_proj_main_to_cam_main.npy")  # (1080, 1920, 2) cam px
PH, PW = lut.shape[:2]
CW, CH = 1280, 720
neg = cv2.imread(str(C / "ref_negro16.png"), cv2.IMREAD_UNCHANGED).astype(np.float32) / 64
bla = cv2.imread(str(C / "ref_blanco16.png"), cv2.IMREAD_UNCHANGED).astype(np.float32) / 64
span = bla - neg

# ── 1. muestras proj→cam (paso 4) y homografía global ──
S = 4
vv, uu = np.mgrid[0:PH:S, 0:PW:S]
cam = lut[::S, ::S]
ok = np.isfinite(cam).all(-1)
P = np.stack([uu[ok], vv[ok]], -1).astype(np.float32)
Q = cam[ok].astype(np.float32)
Hpc, _ = cv2.findHomography(P, Q, cv2.LMEDS)
Hcp = np.linalg.inv(Hpc)


def err_proj(Hcp_, Psel, Qsel):
    """Error en px de proyector: cam→proj con la homografía vs. coordenada real del proyector."""
    pr = cv2.perspectiveTransform(Qsel.reshape(-1, 1, 2), Hcp_).reshape(-1, 2)
    return np.linalg.norm(pr - Psel, axis=1)


def stats(e):
    return {"p50": round(float(np.percentile(e, 50)), 2), "p95": round(float(np.percentile(e, 95)), 2),
            "max": round(float(e.max()), 2), "n": int(len(e))}


e_all = err_proj(Hcp, P, Q)

# ── 2. viga: bordes casi verticales en la imagen de la cámara (mapa blanco−negro) ──
roi = cv2.GaussianBlur(span, (0, 0), 2.0)
lit = cv2.erode((span > 1.0).astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
gx = cv2.Sobel(roi, cv2.CV_32F, 1, 0, ksize=5)
gy = cv2.Sobel(roi, cv2.CV_32F, 0, 1, ksize=5)
vert = (np.abs(gx) > 2.5 * np.abs(gy)) & lit
mag = np.abs(gx) * vert
prof = np.array([np.percentile(mag[:, x][lit[:, x]], 75) if lit[:, x].sum() > 50 else 0 for x in range(CW)])
prof_s = np.convolve(prof, np.ones(5) / 5, "same")
cand = [x for x in range(CW // 4, 3 * CW // 4) if prof_s[x] == prof_s[max(0, x - 25):x + 26].max() and prof_s[x] > 0]
cand = sorted(cand, key=lambda x: -prof_s[x])[:3]
beam_lines_cam = []
for x0 in sorted(cand):
    # ajuste de recta x = a·y + b con los máximos de |gx| por fila cerca de x0
    ys, xs = [], []
    for y in range(0, CH, 4):
        if not lit[y, max(0, x0 - 20):x0 + 20].any():
            continue
        seg = mag[y, max(0, x0 - 20):x0 + 21]
        if seg.max() > 0:
            ys.append(y)
            xs.append(max(0, x0 - 20) + int(seg.argmax()))
    if len(ys) > 20:
        a, b = np.polyfit(ys, xs, 1)
        beam_lines_cam.append({"x0": int(x0), "a": float(a), "b": float(b), "y_min": int(min(ys)), "y_max": int(max(ys)),
                               "fuerza": round(float(prof_s[x0]), 3)})

# ── 3. mapa inverso cámara→proyector (dispersión + relleno) ──
S2 = 2
v2, u2 = np.mgrid[0:PH:S2, 0:PW:S2]
c2 = lut[::S2, ::S2]
ok2 = np.isfinite(c2).all(-1)
cx, cy = np.round(c2[ok2][:, 0]).astype(int), np.round(c2[ok2][:, 1]).astype(int)
inb = (cx >= 0) & (cx < CW) & (cy >= 0) & (cy < CH)
su, sv, cnt = (np.zeros((CH, CW), np.float32) for _ in range(3))
np.add.at(su, (cy[inb], cx[inb]), u2[ok2][inb])
np.add.at(sv, (cy[inb], cx[inb]), v2[ok2][inb])
np.add.at(cnt, (cy[inb], cx[inb]), 1)
for _ in range(3):  # relleno de huecos con el promedio de vecinos
    k = np.ones((3, 3), np.float32)
    su2, sv2, c2_ = (cv2.filter2D(a, -1, k, borderType=cv2.BORDER_CONSTANT) for a in (su, sv, cnt))
    hole = cnt == 0
    su[hole], sv[hole], cnt[hole] = su2[hole], sv2[hole], c2_[hole]
inv = np.full((CH, CW, 2), np.nan, np.float32)
m = cnt > 0
inv[m, 0], inv[m, 1] = su[m] / cnt[m], sv[m] / cnt[m]
np.save(O / "cam2proj.npy", inv)


def cam_to_proj(x, y):
    xi, yi = int(round(x)), int(round(y))
    if 0 <= xi < CW and 0 <= yi < CH and np.isfinite(inv[yi, xi]).all():
        return [float(inv[yi, xi, 0]), float(inv[yi, xi, 1])]
    p = cv2.perspectiveTransform(np.array([[[x, y]]], np.float32), Hcp)[0, 0]
    return [float(p[0]), float(p[1])]


# ── 4. planos a cada lado de la viga y errores ──
planes = {}
if beam_lines_cam:
    L = beam_lines_cam[0] if len(beam_lines_cam) == 1 else min(beam_lines_cam, key=lambda l: l["b"])
    R = beam_lines_cam[-1]
    xl = L["a"] * Q[:, 1] + L["b"]
    xr = R["a"] * Q[:, 1] + R["b"]
    sides = {"izquierda": Q[:, 0] < xl - 6, "derecha": Q[:, 0] > xr + 6}
    for k, sel in sides.items():
        if sel.sum() > 50:
            Hs, _ = cv2.findHomography(Q[sel], P[sel], cv2.LMEDS)
            planes[k] = {"homografia_propia": stats(err_proj(Hs, P[sel], Q[sel])),
                         "homografia_global": stats(e_all[sel])}

# ── 5. viga en el proyector: polilíneas de sus bordes ──
beam_proj = []
for l in beam_lines_cam:
    pts = []
    for y in np.linspace(l["y_min"], l["y_max"], 24):
        pts.append(cam_to_proj(l["a"] * y + l["b"], y))
    beam_proj.append(pts)

# ── 6. rejilla redonda (Hough sobre la imagen blanca de la cámara) ──
g8 = cv2.normalize(cv2.GaussianBlur(bla, (0, 0), 1.5), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
circ = cv2.HoughCircles(g8, cv2.HOUGH_GRADIENT, dp=1.5, minDist=200, param1=60, param2=40, minRadius=40, maxRadius=160)
vent = None
if circ is not None:
    for x, y, r in circ[0]:
        if lit[int(min(y, CH - 1)), int(min(x, CW - 1))]:
            ring = [cam_to_proj(x + r * np.cos(t), y + r * np.sin(t)) for t in np.linspace(0, 2 * np.pi, 33)[:-1]]
            vent = {"cam": [float(x), float(y), float(r)], "proj": ring, "centro_proj": cam_to_proj(x, y)}
            break

# ── 7. contorno del área calibrada (borde de la proyección vista) ──
okmask = np.isfinite(lut[::8, ::8]).all(-1).astype(np.uint8)
cs, _ = cv2.findContours(okmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
area = (max(cs, key=cv2.contourArea).reshape(-1, 2) * 8).tolist() if cs else []

out = {
    "homografia_cam_proj": Hcp.flatten().tolist(),
    "error_homografia_global_px_proy": stats(e_all),
    "planos": planes,
    "viga_cam": beam_lines_cam,
    "viga_proj_px": beam_proj,
    "rejilla": vent,
    "area_calibrada_proj_px": area,
}
(O / "escena.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
# diagnóstico
vis = cv2.cvtColor(cv2.normalize(span, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8), cv2.COLOR_GRAY2BGR)
for l in beam_lines_cam:
    cv2.line(vis, (int(l["a"] * l["y_min"] + l["b"]), l["y_min"]), (int(l["a"] * l["y_max"] + l["b"]), l["y_max"]), (0, 0, 255), 2)
if vent:
    x, y, r = vent["cam"]
    cv2.circle(vis, (int(x), int(y)), int(r), (0, 255, 0), 2)
cv2.imwrite(str(O / "escena_cam.jpg"), vis)
res = np.full((PH // S, PW // S), np.nan, np.float32)
res[ok] = e_all
rv = cv2.applyColorMap(np.nan_to_num(np.clip(res / 15 * 255, 0, 255)).astype(np.uint8), cv2.COLORMAP_TURBO)
rv[~ok] = 0
cv2.imwrite(str(O / "residuo_homografia_proj.png"), cv2.resize(rv, (PW // 2, PH // 2), interpolation=cv2.INTER_NEAREST))
np.save(O / "residuo_proj_s4.npy", res)
print(json.dumps({k: out[k] for k in ["error_homografia_global_px_proy", "planos", "viga_cam"]}, ensure_ascii=False))
print("rejilla:", vent and vent["cam"], "| bordes de viga en proyector:", [[(round(p[0]), round(p[1])) for p in b[::8]] for b in beam_proj])
