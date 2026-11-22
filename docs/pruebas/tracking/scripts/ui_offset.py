import json, sys, glob, cv2, numpy as np
sys.path.insert(0, "/tmp/trk-work")
from geo import Calib
def medir(carpeta, sel=slice(None)):
    ms = [json.loads(l) for l in open(f"{carpeta}/mensajes.jsonl")]
    w = [m for m in ms if m.get("kind") == "world"][-1]; h = w["homography"]; C = Calib(h["matrix"], h["image_px"], h["lens"])
    frames = sorted(glob.glob(f"{carpeta}/cuadro-*.jpg"))[sel]
    acc = np.clip(np.mean([cv2.imread(f).astype(np.float32) for f in frames], 0), 0, 255).astype(np.uint8); s = acc.shape[1] / 1280; pp = 2.0
    xs, ys = np.meshgrid((np.arange(int(1100 * pp)) + 0.5) / pp - 50, (np.arange(int(700 * pp)) + 0.5) / pp - 70)
    px = C.mm_to_px(np.stack([xs, ys], -1), s).astype(np.float32); r = cv2.remap(acc, px[..., 0], px[..., 1], cv2.INTER_LINEAR)
    g = cv2.cvtColor(r, cv2.COLOR_BGR2GRAY); th = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))).astype(np.float32)
    rb = (r[..., 2].astype(np.float32) - r[..., 0]); rb = np.clip(rb - np.median(rb), 0, None)
    def P(x, y): return (int(round((x + 50) * pp)), int(round((y + 70) * pp)))
    def match(src, draw, box_mm, search=50):
        x0, y0, x1, y1 = box_mm; pad = 6
        T = np.zeros((int((y1 - y0 + 2 * pad) * pp), int((x1 - x0 + 2 * pad) * pp)), np.float32)
        draw(T, lambda x, y: (int(round((x - x0 + pad) * pp)), int(round((y - y0 + pad) * pp)))); T = cv2.GaussianBlur(T, (0, 0), 1.2)
        ox, oy = P(x0 - pad - search, y0 - pad - search); ox0, oy0 = max(0, ox), max(0, oy)
        roi = src[oy0:oy + T.shape[0] + int(2 * search * pp), ox0:ox + T.shape[1] + int(2 * search * pp)]
        res = cv2.matchTemplate(roi, T, cv2.TM_CCOEFF_NORMED); _, sc, _, loc = cv2.minMaxLoc(res)
        return ((loc[0] + ox0 - P(x0 - pad, 0)[0]) / pp, (loc[1] + oy0 - P(0, y0 - pad)[1]) / pp, sc)
    def tab(x):
        def d(T, Q):
            a = Q(x, 6); b = Q(x + 170, 46); rr = int(20 * pp)
            cv2.line(T, (a[0] + rr, a[1]), (b[0] - rr, a[1]), 1, 4); cv2.line(T, (a[0] + rr, b[1]), (b[0] - rr, b[1]), 1, 4)
            cv2.ellipse(T, (a[0] + rr, a[1] + rr), (rr, rr), 0, 90, 270, 1, 4); cv2.ellipse(T, (b[0] - rr, a[1] + rr), (rr, rr), 0, -90, 90, 1, 4)
        return d
    def fill(T, Q):
        a = Q(10, 6); b = Q(180, 46); rr = int(20 * pp); cv2.rectangle(T, (a[0] + rr, a[1]), (b[0] - rr, b[1]), 1, -1); cv2.circle(T, (a[0] + rr, a[1] + rr), rr, 1, -1); cv2.circle(T, (b[0] - rr, a[1] + rr), rr, 1, -1)
    return {"planetas (x≈275)": match(th, tab(190), (190, 6, 360, 46)), "Hablar (x≈935)": match(th, lambda T, Q: cv2.circle(T, Q(935, 65), int(51 * pp), 1, 6), (884, 14, 986, 116)),
            "Principal (x≈95)": match(rb, fill, (10, 6, 180, 46))}, len(frames)
for nombre, carpeta, sel in (("17:18", "/tmp/trk-work/live", slice(None)), ("17:31", "/tmp/trk-work/live2", slice(0, 4)), ("17:32", "/tmp/trk-work/live2", slice(-4, None))):
    r, n = medir(carpeta, sel); print(f"{nombre} ({n} cuadros): " + " | ".join(f"{k}: ({v[0]:+.1f}, {v[1]:+.1f}) mm ncc {v[2]:.2f}" for k, v in r.items()))
