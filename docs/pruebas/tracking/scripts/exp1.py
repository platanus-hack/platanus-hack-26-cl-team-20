"""Exp 1: el pipeline tal cual (pintura.py) sobre las 4 fotos de data/mesa/2026-11-21. Tiempos, estabilidad y overlays."""
import sys, json, time, cv2, numpy as np
sys.path.insert(0, "/tmp/trk-work")
from pipe import *
OUT = "/Users/manuelblanco/Projects/platanus_hack_26/informes/tracking"; D = "/tmp/trk-consola/data/mesa/2026-11-21"
t = time.perf_counter(); dino = Dino(); print("carga DINO %.1fs" % (time.perf_counter() - t))
t = time.perf_counter(); sam = MobileSAM("/tmp/trk-models/sam"); print("carga SAM %.2fs" % (time.perf_counter() - t))
res = {}
for rep in range(2):          # 2 pasadas: la primera calienta
    for n in range(1, 5):
        img = cv2.imread(f"{D}/mesa-limpia-{n}.jpg")
        t0 = time.perf_counter(); Dd = dino.detect(img, list(P_PINTURA), box_th=0.24); td = time.perf_counter() - t0
        dets = filtro_pintura(Dd, img.shape, MESA_PINTURA)
        t0 = time.perf_counter(); sam.set_image(img); te = time.perf_counter() - t0
        objs = []; tdec = []
        for det in dets[:16]:
            b = det["box"]; m, info = sam.predict(np.array(b, float), positive=[((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)]); tdec.append(info["seconds"])
            poly = contorno(m)
            if poly is None: continue
            objs.append({"name": P_PINTURA[det["prompt"]], "label": det["label"], "box": b, "score": det["score"], "poly": poly.tolist(), "sam_iou": info["score"], "area_mask": int(m.sum()),
                         "area_box": (b[2] - b[0]) * (b[3] - b[1])})
        if rep == 1:
            res[n] = dict(dino_s=td, dino_detalle=dino.last, enc_s=te, dec_s=tdec, crudas=len(Dd), filtradas=len(dets), objs=objs, raw=Dd)
            print(f"foto {n}: DINO {td:.2f}s (modelo {dino.last['model']:.2f}, entrada {dino.last['input_hw']}) | SAM encoder {te:.2f}s | decoder {1000*np.mean(tdec):.0f} ms x {len(tdec)} | cajas crudas {len(Dd)} -> filtradas {len(dets)} -> objetos {len(objs)}")
            print("   ", ", ".join(f"{o['name']}({o['score']:.2f})" for o in objs))
            cv2.imwrite(f"{OUT}/exp1-tal-cual-foto{n}.jpg", dibuja(img[200:720, 200:1000], [{**o, "poly": (np.array(o["poly"]) - [200, 200]).tolist(), "box": [o["box"][0] - 200, o["box"][1] - 200, o["box"][2] - 200, o["box"][3] - 200]} for o in objs], f"pipeline tal cual, foto {n} (recorte ampliado x2)", 2.0))
json.dump(res, open("/tmp/trk-work/exp1.json", "w"))
