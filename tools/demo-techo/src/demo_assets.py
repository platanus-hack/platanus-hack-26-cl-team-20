"""Prepara la demo a partir de la escena: geometría en mm del lienzo (1 mm = 2,5 px del T749) y recursos PNG.

uso: python -I demo_assets.py <dir_calib> <dir_escena> <dir_assets>
Salida: demo.json (viga, planos, rejilla, textos), barra.png, revela_XX.png (barrido), cam2proj.bin (f32 720×1280×2).
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

C, E, A = (Path(p) for p in sys.argv[1:4])
A.mkdir(parents=True, exist_ok=True)
esc = json.loads((E / "escena.json").read_text())
lut = np.load(C / "lut_proj_main_to_cam_main.npy")
PH, PW = lut.shape[:2]
PX = 2.5  # px por mm del lienzo
planes = esc["planos_segmentados"]

# ── etiquetas por plano (paso 4) con las homografías de cada plano ──
S = 4
vv, uu = np.mgrid[0:PH:S, 0:PW:S]
cam = lut[::S, ::S]
ok = np.isfinite(cam).all(-1)
err = np.full((len(planes),) + ok.shape, np.inf, np.float32)
for i, p in enumerate(planes):
    H = np.array(p["homografia_cam_proj"]).reshape(3, 3)
    pr = cv2.perspectiveTransform(cam[ok].reshape(-1, 1, 2).astype(np.float32), H).reshape(-1, 2)
    e = np.linalg.norm(pr - np.stack([uu[ok], vv[ok]], -1), axis=1)
    err[i][ok] = e
lab = np.where(err.min(0) < 5.0, err.argmin(0), -1)
lab[~ok] = -1

# ── viga: hueco entre el plano 0 (techo) y el 1 (cara derecha), fila por fila ──
L, R = [], []
for r in range(lab.shape[0]):
    row = lab[r]
    u0 = np.flatnonzero(row == 0)
    u1 = np.flatnonzero(row == 1)
    u0 = u0[u0 * S > PW * 0.5]
    u1 = u1[u1 * S > PW * 0.5]
    if len(u0) and len(u1):
        xl, xr = u0.max() * S, u1.min() * S
        if 0 < xr - xl < 200:
            L.append((r * S, xl))
            R.append((r * S, xr))
L, R = np.array(L, float), np.array(R, float)
al, bl = np.polyfit(L[:, 0], L[:, 1], 1)
ar, br = np.polyfit(R[:, 0], R[:, 1], 1)
v0, v1 = 0.0, PH - 1.0
beam_px = [[al * v0 + bl, v0], [ar * v0 + br, v0], [ar * v1 + br, v1], [al * v1 + bl, v1]]
ancho_px = float(np.median(R[:, 1] - L[:, 1]))
mm = lambda pts: [[round(x / PX, 1), round(y / PX, 1)] for x, y in pts]

# ── errores para las etiquetas ──
g = esc["error_homografia_global_px_proy"]["p50"]
p0, p1 = planes[0]["error_px_proy"]["p50"], planes[1]["error_px_proy"]["p50"]
fmt = lambda x: f"{x:.1f}".replace(".", ",")
demo = {
    "viga_mm": mm(beam_px),
    "viga_izq_mm": mm([[al * v0 + bl, v0], [al * v1 + bl, v1]]),
    "viga_der_mm": mm([[ar * v0 + br, v0], [ar * v1 + br, v1]]),
    "viga_ancho_px": round(ancho_px, 1),
    "planos": [{"id": p["id"], "contorno_mm": mm(p["contorno_px"]), "centro_mm": mm([p["centro_px"]])[0],
                "area_pct": p["area_pct"], "err_p50": p["error_px_proy"]["p50"]} for p in planes],
    "texto_viga": f"viga · detectada · error {fmt(max(p0, p1))} px",
    "texto_viga2": f"una sola homografía: {fmt(g)} px · por plano: {fmt(p0)} px",
    "homografia_cam_proj": esc["homografia_cam_proj"],
}
# rejilla = plano más «redondo» de los chicos (el disco)
for p in planes[2:]:
    c = np.array(p["contorno_px"], np.float32)
    a, per = cv2.contourArea(c), cv2.arcLength(c, True)
    if per > 0 and 4 * np.pi * a / per ** 2 > 0.75:
        (x, y), r = cv2.minEnclosingCircle(c)
        demo["rejilla"] = {"centro_mm": [round(x / PX, 1), round(y / PX, 1)], "radio_mm": round(r / PX, 1), "plano": p["id"]}
        break
(A / "demo.json").write_text(json.dumps(demo, ensure_ascii=False, indent=1))

# ── barrido: barra luminosa (RGBA) y 20 etapas de «revelado» de los planos ──
bar = np.zeros((64, 48, 4), np.uint8)
xs = np.linspace(-1, 1, 48)
prof = np.exp(-(xs / 0.35) ** 2)
bar[..., 0] = 255
bar[..., 1] = 255
bar[..., 2] = 255
bar[..., 3] = (255 * prof)[None, :]
cv2.imwrite(str(A / "barra.png"), bar)
pal = np.array([[255, 160, 40], [40, 210, 255], [90, 255, 120], [255, 60, 220], [60, 120, 255]], np.uint8)  # BGR
full = np.zeros((PH, PW, 4), np.uint8)
labf = cv2.resize(lab.astype(np.int16), (PW, PH), interpolation=cv2.INTER_NEAREST)
for i in range(len(planes)):
    m = labf == i
    full[m, :3] = pal[i % len(pal)] // 3
    full[m, 3] = 150
    # rayas de escaneo dentro de cada plano (cada 24 px)
    stripes = m & ((np.arange(PH)[:, None] // 12) % 2 == 0)
    full[stripes, :3] = pal[i % len(pal)] // 2
for p in planes:
    cv2.polylines(full, [np.array(p["contorno_px"], np.int32)], True, (255, 255, 255, 255), 4)
cv2.polylines(full, [np.array(beam_px, np.int32)], True, (0, 200, 255, 255), 6)
N = 20
for k in range(N + 1):
    x = int(PW * k / N)
    st = full.copy()
    st[:, x:] = 0
    cv2.imwrite(str(A / f"revela_{k:02d}.png"), st, [cv2.IMWRITE_PNG_COMPRESSION, 6])
# ── mapa cámara→proyector para las manos ──
inv = np.load(E / "cam2proj.npy").astype("<f4")
inv.tofile(A / "cam2proj.bin")
print(json.dumps({k: demo[k] for k in ["viga_mm", "viga_ancho_px", "texto_viga", "texto_viga2"]}, ensure_ascii=False))
print("rejilla:", demo.get("rejilla"))
