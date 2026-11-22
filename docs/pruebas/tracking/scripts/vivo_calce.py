import json, sys, glob, cv2, numpy as np
sys.path.insert(0, "/tmp/trk-work")
from geo import Calib
OUT = "/Users/manuelblanco/Projects/platanus_hack_26/informes/tracking"
ms = [json.loads(l) for l in open("/tmp/trk-work/live/mensajes.jsonl")]
w = [m for m in ms if m.get("kind") == "world"][-1]
h = w["homography"]; C = Calib(h["matrix"], h["image_px"], h["lens"])
frames = sorted(glob.glob("/tmp/trk-work/live/cuadro-*.jpg")); img = cv2.imread(frames[0]); s = img.shape[1] / 1280
print("cuadro", img.shape, "escala", s)
# UI esperada (render.js UI): pestañas [10+i*180, 6, 170, 40], voz centro (935,65) radio 51
ov = img.copy()
def poly_mm(pts, col, th=1):
    p = C.mm_to_px(np.array(pts, float), s).astype(np.int32); cv2.polylines(ov, [p], True, col, th, cv2.LINE_AA)
def rect(x, y, ww, hh, n=12):
    e = []
    for a, b in [((x, y), (x + ww, y)), ((x + ww, y), (x + ww, y + hh)), ((x + ww, y + hh), (x, y + hh)), ((x, y + hh), (x, y))]:
        e += [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n)]
    return e
poly_mm(rect(0, 0, 1000, 563, 30), (0, 255, 255), 1)
for i in range(2): poly_mm(rect(10 + i * 180, 6, 170, 40), (0, 255, 0), 1)
poly_mm([(935 + 51 * np.cos(t), 65 + 51 * np.sin(t)) for t in np.linspace(0, 2 * np.pi, 60, endpoint=False)], (0, 255, 0), 1)
for x in range(100, 1000, 100): poly_mm([(x, y) for y in range(0, 564, 20)] + [(x, y) for y in range(560, -1, -20)], (80, 80, 255), 1)
cv2.imwrite(OUT + "/vivo-01-ui-esperada-sobre-camara.jpg", ov)
# medir: pestaña activa naranja (#FF5B24) -> centroide observado vs esperado (95,26) mm
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
errs = []
for f in frames[:6]:
    im = cv2.imread(f); hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (0, 70, 120), (16, 255, 255)); m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(m)
    exp = C.mm_to_px(np.array([95.0, 26.0]), s)
    best = None
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] < 400: continue
        d = np.hypot(*(cen[i] - exp))
        if best is None or d < best[0]: best = (d, cen[i], st[i])
    if best is None: continue
    obs_mm = C.px_to_mm(best[1], s); errs.append((best[0], obs_mm, best[2][cv2.CC_STAT_AREA]))
for e in errs: print("pestaña Principal: error %.1f px (a %d px de ancho) | observada en mm (%.1f, %.1f) vs esperada (95, 26) | área %d" % (e[0], img.shape[1], e[1][0], e[1][1], e[2]))
rect_img = C.rectify(img, ppmm=1.0); cv2.imwrite(OUT + "/vivo-02-rectificado-mm.jpg", rect_img)
print("px por mm en la mesa (centro):", np.linalg.norm(C.mm_to_px(np.array([550., 280.]), 1) - C.mm_to_px(np.array([450., 280.]), 1)) / 100, "a 1280")
