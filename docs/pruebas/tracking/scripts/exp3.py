"""Exp 3: el mismo pipeline sobre un cuadro EN VIVO (17:18, encuadre actual), con el polígono de la mesa sacado de la calibración del nodo."""
import sys, json, time, glob, cv2, numpy as np
sys.path.insert(0, "/tmp/trk-work")
from pipe import *
from geo import Calib
OUT = "/Users/manuelblanco/Projects/platanus_hack_26/informes/tracking"
ms = [json.loads(l) for l in open("/tmp/trk-work/live/mensajes.jsonl")]
w = [m for m in ms if m.get("kind") == "world"][-1]; h = w["homography"]; C = Calib(h["matrix"], h["image_px"], h["lens"])
dino = Dino(); sam = MobileSAM("/tmp/trk-models/sam")
frames = sorted(glob.glob("/tmp/trk-work/live/cuadro-*.jpg"))
borde = [(x, 0) for x in range(0, 1001, 50)] + [(1000, y) for y in range(0, 564, 50)] + [(x, 563) for x in range(1000, -1, -50)] + [(0, y) for y in range(563, -1, -50)]
mesa = C.mm_to_px(np.array(borde, float)).astype(np.int32)
P2 = dict(P_PINTURA); P2.update({"a soda can": "lata", "a card": "lamina"})
dino.detect(cv2.imread(frames[0]), ["a spoon"])
tabla = {}
for k, f in enumerate(frames[:6]):
    im = cv2.resize(cv2.imread(f), (1280, 720), interpolation=cv2.INTER_CUBIC)
    for nombre, prompts, th in (("pintura", list(P_PINTURA), 0.24), ("sam_seguir", [p for p in P_PINTURA if p != "a white foam sphere"], 0.30)):
        t0 = time.perf_counter(); Dd = dino.detect(im, prompts, box_th=th); td = time.perf_counter() - t0
        dets = filtro_pintura(Dd, im.shape, mesa) if nombre == "pintura" else [d for d in filtro_pintura(Dd, im.shape, mesa, amin=500) ]
        t0 = time.perf_counter(); sam.set_image(im); te = time.perf_counter() - t0; objs = []
        for det in dets[:16]:
            b = det["box"]; m, info = sam.predict(np.array(b, float), positive=[((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)]); poly = contorno(m)
            if poly is None: continue
            objs.append({"name": P_PINTURA[det["prompt"]], "box": b, "score": det["score"], "poly": poly.tolist(), "sam_iou": info["score"]})
        tabla.setdefault(nombre, []).append(sorted(f"{o['name']}@{int((o['box'][0]+o['box'][2])/2)//40},{int((o['box'][1]+o['box'][3])/2)//40}" for o in objs))
        print(f"cuadro {k} [{nombre} th {th}] DINO {td:.2f}s enc {te:.2f}s | crudas {len(Dd)} -> {len(objs)} objetos: " + ", ".join(f"{o['name']}({o['score']:.2f})" for o in objs))
        if k == 0:
            o = dibuja(im, objs, f"cuadro en vivo 17:18, pipeline {nombre} (umbral {th})"); cv2.polylines(o, [mesa], True, (0, 255, 255), 1); cv2.imwrite(f"{OUT}/exp3-vivo-{nombre}.jpg", o)
for nombre, filas in tabla.items():
    todos = sorted(set(x for f in filas for x in f)); print(f"== estabilidad [{nombre}] en {len(filas)} cuadros de una escena quieta:")
    for x in todos: print(f"   {x:22s} presente en {sum(x in f for f in filas)}/{len(filas)}")
