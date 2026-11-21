"""Referencia de paridad: pipeline del opencv_zoo (mp_palmdet + mp_handpose) sobre imágenes de prueba.
uso: python -I reference.py <ref_dir> <models_dir> <out.json> img1 img2 ..."""
import importlib.util
import json
import sys

import cv2 as cv
import numpy as np

ref_dir, models, out = sys.argv[1], sys.argv[2], sys.argv[3]


def load(name):
    spec = importlib.util.spec_from_file_location(name, f"{ref_dir}/{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


palm = load("mp_palmdet").MPPalmDet(f"{models}/palm_detection_mediapipe_2023feb.onnx", nmsThreshold=0.3, scoreThreshold=0.6)
hp = load("mp_handpose").MPHandPose(f"{models}/handpose_estimation_mediapipe_2023feb.onnx", confThreshold=0.8)
res = {}
for p in sys.argv[4:]:
    img = cv.imread(p)  # BGR, como el demo del zoo
    palms = palm.infer(img)
    hands = []
    for pm in palms:
        h = hp.infer(img, pm)
        if h is None:
            continue
        hands.append({"palm_box": pm[0:4].tolist(), "palm_kp": pm[4:18].reshape(7, 2).tolist(), "palm_score": float(pm[18]),
                      "bbox": h[0:4].tolist(), "landmarks": h[4:67].reshape(21, 3).tolist(),
                      "handedness": float(h[130]), "conf": float(h[131])})
    name = p.split("/")[-1]
    res[name] = {"w": img.shape[1], "h": img.shape[0], "n_palms": len(palms), "hands": hands}
    print(name, img.shape[1], img.shape[0], "palms", len(palms), "hands", len(hands),
          [round(x["conf"], 3) for x in hands], [round(x["handedness"], 3) for x in hands])
with open(out, "w") as f:
    json.dump(res, f, indent=1)
