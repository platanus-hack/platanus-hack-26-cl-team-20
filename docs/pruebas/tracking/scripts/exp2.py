"""Exp 2: mismas fotos, variando la entrada de DINO: cuadro completo vs recorte a la mesa (x2) vs vista rectificada; tamaños y prompts."""
import sys, json, time, cv2, numpy as np
sys.path.insert(0, "/tmp/trk-work")
from pipe import *
OUT = "/Users/manuelblanco/Projects/platanus_hack_26/informes/tracking"; D = "/tmp/trk-consola/data/mesa/2026-11-21"
dino = Dino(); sam = MobileSAM("/tmp/trk-models/sam")
img = cv2.imread(f"{D}/mesa-limpia-1.jpg"); dino.detect(img, ["a spoon"])  # calentar
# verdad a ojo (px del cuadro completo): centro de cada objeto sobre la superficie proyectable
GT = {"taza": (711, 403), "cuchara1": (762, 392), "cuchara2": (790, 392), "canasto": (664, 518), "pastillero": (425, 458), "caja": (417, 312), "lamina": (551, 404), "plasticina": (787, 563),
      "bola_grande": (677, 507), "bola_med1": (725, 485), "bola_med2": (717, 529), "bola_ch1": (757, 467), "bola_suelta": (688, 441), "engranaje": (613, 558)}
ESPERADO = {"taza": "a coffee mug", "cuchara1": "a spoon", "cuchara2": "a spoon", "canasto": "a wicker basket", "pastillero": "a pill organizer", "caja": "a white plastic box",
            "bola_grande": "a white ball", "bola_med1": "a white ball", "bola_med2": "a white ball", "bola_ch1": "a white ball", "bola_suelta": "a white ball"}
def evalua(dets, nombre):
    """Para cada objeto de la verdad: mejor caja que contiene su centro y no es gigante; ¿con qué prompt y score?"""
    ok = 0; mal = 0; lines = []
    for k, (x, y) in GT.items():
        c = [d for d in dets if d["box"][0] <= x <= d["box"][2] and d["box"][1] <= y <= d["box"][3] and (d["box"][2] - d["box"][0]) * (d["box"][3] - d["box"][1]) < (30000 if k != "canasto" else 60000)
             and (k == "canasto" or d["prompt"] != "a wicker basket")]
        # la caja más chica que lo contiene (evita que el canasto «gane» las bolas)
        c = sorted(c, key=lambda d: (d["box"][2] - d["box"][0]) * (d["box"][3] - d["box"][1]))
        if not c: lines.append(f"{k}: —"); continue
        d = c[0]; good = ESPERADO.get(k) in (d["prompt"], None) or (k.startswith("bola") and "white" in d["prompt"])
        ok += good and k in ESPERADO; mal += (not good) and k in ESPERADO
        lines.append(f"{k}: {d['prompt'][2:]} {d['score']:.2f}{'' if good or k not in ESPERADO else ' ✗'}")
    print(f"[{nombre}] bien {ok}/{len(ESPERADO)}, mal etiquetados {mal}, sin detectar {len(ESPERADO)-ok-mal} | " + "; ".join(lines))
    return ok, mal
prompts = list(P_PINTURA)
# A) cuadro completo, tal cual
for th in (0.24, 0.30):
    d = dino.detect(img, prompts, box_th=th); evalua(d, f"A completo 1280x720, th {th}, {dino.last['model']:.2f}s")
# B) recorte a la mesa + x2
x0, y0, x1, y1 = 228, 230, 883, 689
crop = cv2.resize(img[y0:y1, x0:x1], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
def back(dets, s=2.0):
    return [{**d, "box": [d["box"][0] / s + x0, d["box"][1] / s + y0, d["box"][2] / s + x0, d["box"][3] / s + y0]} for d in dets]
for th in (0.24, 0.30):
    d = back(dino.detect(crop, prompts, box_th=th)); evalua(d, f"B recorte mesa x2 ({crop.shape[1]}x{crop.shape[0]}), th {th}, entrada {dino.last['input_hw']}, {dino.last['model']:.2f}s")
dB = d
# C) recorte sin ampliar y con entrada chica (más rápido)
for se in (800, 608, 480):
    t = time.perf_counter(); d = back(dino.detect(img[y0:y1, x0:x1], prompts, box_th=0.24, size={"shortest_edge": se, "longest_edge": 1333}), 1.0)
    evalua(d, f"C recorte mesa, lado corto {se}, entrada {dino.last['input_hw']}, {dino.last['model']:.2f}s")
# D) un prompt por pasada sobre el recorte x2 (9 pasadas)
t = time.perf_counter(); d = []
for p in prompts: d += back(dino.detect(crop, [p], box_th=0.30))
evalua(d, f"D recorte x2, un prompt por pasada, th 0.30, total {time.perf_counter()-t:.1f}s")
# E) prompts genéricos: un solo prompt «object» para proponer cajas (el nombre lo pone otra etapa)
for p in (["an object"], ["object", "thing"], ["all objects on the table"]):
    d = back(dino.detect(crop, p, box_th=0.20)); n = sum(1 for k, (x, y) in GT.items() if any(b["box"][0] <= x <= b["box"][2] and b["box"][1] <= y <= b["box"][3] and (b["box"][2]-b["box"][0])*(b["box"][3]-b["box"][1]) < 30000 for b in d))
    print(f"[E genérico {p}] {len(d)} cajas, cubren {n}/{len(GT)} objetos, {dino.last['model']:.2f}s")
json.dump(dB, open("/tmp/trk-work/exp2-B.json", "w"))
