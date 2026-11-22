"""Exp 4: chequeos propios de SAM sobre la foto 1 y el cuadro en vivo, con cajas fijas (las de DINO)."""
import sys, json, time, glob, cv2, numpy as np
sys.path.insert(0, "/tmp/trk-work")
from pipe import *
OUT = "/Users/manuelblanco/Projects/platanus_hack_26/informes/tracking"; D = "/tmp/trk-consola/data/mesa/2026-11-21"
sam = MobileSAM("/tmp/trk-models/sam"); multi = MobileSAM("/tmp/trk-models/sam", decoder="sam_mask_decoder_multi.onnx")
r = json.load(open("/tmp/trk-work/exp1.json"))
img = cv2.imread(f"{D}/mesa-limpia-1.jpg")
cajas = {f"{o['name']}{i}": o["box"] for i, o in enumerate(r["1"]["objs"])}
cajas["taza(caja a mano)"] = [679, 369, 742, 438]; cajas["pastillero real"] = [360, 388, 487, 526]; cajas["lamina(a mano)"] = [503, 371, 600, 438]
def miou(a, b): return (a & b).sum() / max(1, (a | b).sum())
# S1: ¿RGB o BGR? ¿hace falta normalizar o rellenar? Se compara el uso actual con variantes deliberadamente erradas.
def corre(s, im, variante):
    h, w = im.shape[:2]; sc = 1024 / max(h, w); rs = (round(w * sc), round(h * sc))
    x = cv2.resize(im if variante == "bgr" else cv2.cvtColor(im, cv2.COLOR_BGR2RGB), rs, interpolation=cv2.INTER_LINEAR).astype(np.float32)
    if variante == "norm": x = (x - [123.675, 116.28, 103.53]) / [58.395, 57.12, 57.375]
    if variante == "01": x = x / 255
    if variante == "pad":
        p = np.zeros((1024, 1024, 3), np.float32); p[:rs[1], :rs[0]] = x; x = p
    s.shape = (h, w); s.scale = sc; s.resized = rs; s.embedding = s.encoder.run(None, {"input_image": x.astype(np.float32)})[0]
ref = {}
for v in ("actual(rgb 0-255, sin relleno)", "bgr", "norm", "01", "pad"):
    corre(sam, img, v.split("(")[0]); ious = []; pred = []
    for k, b in cajas.items():
        m, info = sam.predict(np.array(b, float), positive=[((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)])
        if v.startswith("actual"): ref[k] = m
        ious.append(miou(m, ref[k])); pred.append(info["score"])
    print(f"S1 {v:32s} IoU predicho medio {np.mean(pred):.3f} | IoU de la máscara contra el uso actual {np.mean(ious):.3f}")
# S2: caja+centro (uso actual, token único) vs solo caja vs multimáscara con mejor IoU
sam.set_image(img); multi.set_image(img); print("S2 objeto: área caja | caja+centro (actual) | solo caja | multimáscara mejor IoU [áreas px, IoU contra actual] | llenado de la caja")
filas = []
for k, b in cajas.items():
    c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2); ab = (b[2] - b[0]) * (b[3] - b[1])
    m1, i1 = sam.predict(np.array(b, float), positive=[c]); m2, i2 = sam.predict(np.array(b, float))
    mk, sc, _ = multi.raw([c, b[:2], b[2:]], [1, 2, 3]); j = int(np.argmax(sc[0])); m3 = mk[0, j] > 0
    fuera = m1.copy(); fuera[int(b[1]):int(b[3]) + 1, int(b[0]):int(b[2]) + 1] = False
    print(f"   {k:20s} {ab:6.0f} | {m1.sum():6d} iou_pred {i1['score']:.2f} | {m2.sum():6d} ({miou(m1, m2):.2f}) | tok {j} {m3.sum():6d} ({miou(m1, m3):.2f}) scores {np.round(sc[0], 2).tolist()} | llena {m1.sum()/ab:.2f} fuera de caja {fuera.sum()/max(1,m1.sum()):.2f}")
    filas.append((k, m1, m2, m3))
# S3: estabilidad de la máscara entre las 4 fotos de la misma escena (mismas cajas)
masks = {}
for n in range(1, 5):
    sam.set_image(cv2.imread(f"{D}/mesa-limpia-{n}.jpg"))
    for k, b in cajas.items(): masks.setdefault(k, []).append(sam.predict(np.array(b, float), positive=[((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)])[0])
print("S3 IoU mínimo de la máscara entre las 4 fotos (misma escena, misma caja): " + ", ".join(f"{k} {min(miou(ms[0], m) for m in ms[1:]):.2f}" for k, ms in masks.items()))
# S5: efecto de la apertura 3x3 y de quedarse con el contorno mayor
print("S5 apertura 3x3 + contorno mayor: fracción de la máscara que sobrevive")
for k, m1, _, _ in filas:
    mo = cv2.morphologyEx(m1.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)); cs, _ = cv2.findContours(mo, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs: print(f"   {k}: sin contorno"); continue
    cc = max(cs, key=cv2.contourArea); poly = cv2.approxPolyDP(cc, 1.5, True)
    print(f"   {k:20s} máscara {m1.sum():6d} px -> contorno mayor {cv2.contourArea(cc):7.0f} px ({cv2.contourArea(cc)/max(1,m1.sum()):.2f}), {len(cs)} componentes, polígono de {len(poly)} puntos")
# overlay comparativo de las máscaras con caja a mano (taza, pastillero real, lámina): SAM segmenta bien si le dan la caja
objs = [{"name": {"taza(caja a mano)": "taza", "pastillero real": "pastillero", "lamina(a mano)": "lamina"}.get(k, "cuchara" if "cuchara" in k else "planeta" if "planeta" in k else "canasto" if "canasto" in k else "caja"), "poly": contorno(m1).tolist() if contorno(m1) is not None else [], "box": cajas[k], "score": 0} for k, m1, _, _ in filas if "pastillero2" not in k]
objs = [o for o in objs if o["poly"]]
o = dibuja(img[200:720, 200:1000], [{**ob, "poly": (np.array(ob["poly"]) - [200, 200]).tolist(), "box": [ob["box"][0] - 200, ob["box"][1] - 200, ob["box"][2] - 200, ob["box"][3] - 200]} for ob in objs], "SAM con cajas correctas (taza, pastillero y lamina con caja a mano)", 2.0)
cv2.imwrite(f"{OUT}/exp4-sam-con-cajas-correctas.jpg", o)
# tiempos por hilos
im = img
for th in (1, 4, 8):
    s = MobileSAM("/tmp/trk-models/sam", threads=th); s.set_image(im); ts = []
    for _ in range(4): s.set_image(im); ts.append(s.timings["encoder_seconds"])
    td = [s.predict(np.array(cajas["canasto0"], float))[1]["seconds"] for _ in range(8)]
    print(f"S6 hilos {th}: encoder {np.median(ts)*1000:.0f} ms, decoder {np.median(td)*1000:.0f} ms")
try:
    s = MobileSAM("/tmp/trk-models/sam", backend="coreml"); s.set_image(im); ts = []
    for _ in range(4): s.set_image(im); ts.append(s.timings["encoder_seconds"])
    print(f"S6 CoreML: encoder {np.median(ts)*1000:.0f} ms")
except Exception as e: print("S6 CoreML no disponible:", str(e)[:120])
