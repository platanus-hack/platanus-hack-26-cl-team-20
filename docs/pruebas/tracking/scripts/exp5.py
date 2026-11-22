"""Exp 5: ¿hace falta SAM en cada ciclo? Segmentación clásica por «distinto del color de la mesa» en la vista rectificada (mm),
comparada con las máscaras de SAM del mismo cuadro. Mide calidad y tiempo. Cuadro en vivo de las 17:18."""
import sys, json, time, glob, cv2, numpy as np
sys.path.insert(0, "/tmp/trk-work")
from pipe import *
from geo import Calib
OUT = "/Users/manuelblanco/Projects/platanus_hack_26/informes/tracking"
ms = [json.loads(l) for l in open("/tmp/trk-work/live/mensajes.jsonl")]
w = [m for m in ms if m.get("kind") == "world"][-1]; h = w["homography"]; C = Calib(h["matrix"], h["image_px"], h["lens"])
frames = sorted(glob.glob("/tmp/trk-work/live/cuadro-*.jpg")); im = cv2.imread(frames[0]); s = im.shape[1] / 1280
PP = 1.0; W, Hh = 1000, 563
# tabla de remapeo fija (se calcula una vez por calibración)
xs, ys = np.meshgrid((np.arange(int(W * PP)) + 0.5) / PP, (np.arange(int(Hh * PP)) + 0.5) / PP)
lut = C.mm_to_px(np.stack([xs, ys], -1), s).astype(np.float32); mx, my = cv2.convertMaps(lut[..., 0], lut[..., 1], cv2.CV_16SC2)

def fondo_poly(lab, mask, orden=2):
    """Ajuste polinomial por canal del color de la mesa usando solo los píxeles de `mask`."""
    yy, xx = np.mgrid[0:lab.shape[0], 0:lab.shape[1]]; x = xx[::4, ::4].ravel() / lab.shape[1]; y = yy[::4, ::4].ravel() / lab.shape[0]
    A = np.stack([x ** i * y ** j for i in range(orden + 1) for j in range(orden + 1 - i)], 1); m = mask[::4, ::4].ravel()
    X = xx.ravel() / lab.shape[1]; Y = yy.ravel() / lab.shape[0]; Af = np.stack([X ** i * Y ** j for i in range(orden + 1) for j in range(orden + 1 - i)], 1)
    out = np.empty_like(lab, np.float32)
    for c in range(3):
        coef, *_ = np.linalg.lstsq(A[m], lab[::4, ::4, c].ravel()[m], rcond=None); out[..., c] = (Af @ coef).reshape(lab.shape[:2])
    return out

def segmenta(rect, T=14.0, iters=3, amin_mm2=150):
    lab = cv2.cvtColor(cv2.GaussianBlur(rect, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.float32); mask = np.ones(lab.shape[:2], bool)
    for _ in range(iters):
        bg = fondo_poly(lab, mask); d = lab - bg; de = np.sqrt((0.5 * d[..., 0]) ** 2 + d[..., 1] ** 2 + d[..., 2] ** 2)   # L pesa la mitad: tolera sombras suaves
        mask = de < T
    fg = (de >= T).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    cs, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [c for c in cs if cv2.contourArea(c) / PP ** 2 >= amin_mm2], bg

def rapido(rect, bg, T=14.0, amin_mm2=150):
    """El paso por cuadro una vez que el fondo ya está modelado (lo que correría en el nodo)."""
    lab = cv2.cvtColor(cv2.GaussianBlur(rect, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.float32); d = lab - bg
    fg = (((0.5 * d[..., 0]) ** 2 + d[..., 1] ** 2 + d[..., 2] ** 2) >= T * T).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    cs, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [c for c in cs if cv2.contourArea(c) / PP ** 2 >= amin_mm2]

t = time.perf_counter(); rect = cv2.remap(im, mx, my, cv2.INTER_LINEAR); t_rect = time.perf_counter() - t
t = time.perf_counter(); blobs, bg = segmenta(rect); t_full = time.perf_counter() - t
ts = []
for f in frames[:10]:
    r2 = cv2.remap(cv2.imread(f), mx, my, cv2.INTER_LINEAR); t = time.perf_counter(); b2 = rapido(r2, bg); ts.append(time.perf_counter() - t)
print(f"rectificar 1000x563: {t_rect*1000:.1f} ms | modelar el fondo (una vez): {t_full*1000:.0f} ms | segmentar por cuadro: {np.median(ts)*1000:.1f} ms | blobs: {len(blobs)}")
# SAM sobre el mismo cuadro (cajas de DINO) para comparar contornos, en mm
dino = Dino(); sam = MobileSAM("/tmp/trk-models/sam"); im1280 = cv2.resize(im, (1280, 720), interpolation=cv2.INTER_CUBIC)
borde = [(x, 0) for x in range(0, 1001, 50)] + [(1000, y) for y in range(0, 564, 50)] + [(x, 563) for x in range(1000, -1, -50)] + [(0, y) for y in range(563, -1, -50)]
mesa = C.mm_to_px(np.array(borde, float)).astype(np.int32)
dets = filtro_pintura(dino.detect(im1280, list(P_PINTURA) + ["a card"], box_th=0.24), im1280.shape, mesa); sam.set_image(im1280)
o = rect.copy(); filas = []
for c in blobs: cv2.drawContours(o, [c], -1, (0, 255, 0), 2)
for det in dets:
    b = det["box"]; m, _ = sam.predict(np.array(b, float), positive=[((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)]); poly = contorno(m)
    if poly is None: continue
    pm = C.px_to_mm(poly.astype(float)); sm = np.zeros(rect.shape[:2], np.uint8); cv2.fillPoly(sm, [(pm * PP).astype(np.int32)], 1)
    cv2.polylines(o, [(pm * PP).astype(np.int32)], True, (255, 0, 255), 1, cv2.LINE_AA)
    best = (0, None)
    for c in blobs:
        bm = np.zeros_like(sm); cv2.drawContours(bm, [c], -1, 1, -1); i = (bm & sm).sum() / max(1, (bm | sm).sum())
        if i > best[0]: best = (i, c)
    cen_s = pm.mean(0); filas.append((P_PINTURA.get(det["prompt"], det["prompt"]), det["score"], int(sm.sum()), best[0]))
for f in sorted(filas, key=lambda x: -x[2]): print(f"   SAM {f[0]:10s} ({f[1]:.2f}) área {f[2]:6d} mm² -> mejor blob clásico IoU {f[3]:.2f}")
cv2.putText(o, "verde: blobs por color de mesa (~ms) | magenta: SAM con cajas de DINO (~2.5 s)", (8, 555), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
cv2.imwrite(f"{OUT}/exp5-clasico-vs-sam-vivo-rectificado.jpg", o)
print("blobs (mm): " + "; ".join(f"centro ({cv2.minEnclosingCircle(c)[0][0]:.0f},{cv2.minEnclosingCircle(c)[0][1]:.0f}) área {cv2.contourArea(c):.0f}" for c in sorted(blobs, key=lambda c: -cv2.contourArea(c))))
