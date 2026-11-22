"""Réplica offline del pipeline de tools/mesa-agente (pintura.py / sam_seguir.py) con las mismas clases y filtros."""
import time, json
from pathlib import Path
import cv2, numpy as np

# ---- copia textual de engine/playground/appearance_prepare.py:137-179 (clase MobileSAM) ----
class MobileSAM:
    def __init__(self, model_dir, backend="cpu", threads=4, decoder="sam_mask_decoder_single.onnx"):
        import onnxruntime as ort
        root = Path(model_dir)
        options = ort.SessionOptions()
        options.intra_op_num_threads = int(threads)
        options.log_severity_level = 3
        providers = ["CPUExecutionProvider"]
        if backend == "coreml":
            providers.insert(0, ("CoreMLExecutionProvider", {"ModelFormat":"MLProgram", "MLComputeUnits":"CPUAndGPU"}))
        self.encoder = ort.InferenceSession(str(root/"mobile_sam_image_encoder.onnx"), options, providers=providers)
        self.decoder = ort.InferenceSession(str(root/decoder), options, providers=providers)
        self.backend = backend
        self.timings = {}

    def set_image(self, bgr):
        self.shape = bgr.shape[:2]
        h, w = self.shape
        self.scale = 1024 / max(h, w)
        self.resized = (round(w*self.scale), round(h*self.scale))
        rgb = cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), self.resized, interpolation=cv2.INTER_LINEAR)
        t = time.perf_counter()
        self.embedding = self.encoder.run(None, {"input_image":rgb.astype(np.float32)})[0]
        self.timings["encoder_seconds"] = time.perf_counter()-t

    def predict(self, box, positive=(), negative=()):
        pts = [*positive, *negative, box[:2], box[2:]]
        labels = [1]*len(positive) + [0]*len(negative) + [2, 3]
        h, w = self.shape
        coords = np.asarray(pts, np.float32)*[self.resized[0]/w, self.resized[1]/h]
        feed = dict(image_embeddings=self.embedding, point_coords=coords[None].astype(np.float32),
                    point_labels=np.asarray(labels, np.float32)[None], mask_input=np.zeros((1,1,256,256),np.float32),
                    has_mask_input=np.zeros(1,np.float32), orig_im_size=np.asarray(self.shape,np.float32))
        t = time.perf_counter()
        masks, score, _ = self.decoder.run(None, feed)
        return masks[0,0] > 0, dict(score=float(score.ravel()[0]), seconds=time.perf_counter()-t)

    # ---- variante mía para el diagnóstico: devuelve todo lo que entrega el decoder ----
    def raw(self, pts, labels):
        h, w = self.shape
        coords = np.asarray(pts, np.float32)*[self.resized[0]/w, self.resized[1]/h]
        feed = dict(image_embeddings=self.embedding, point_coords=coords[None].astype(np.float32),
                    point_labels=np.asarray(labels, np.float32)[None], mask_input=np.zeros((1,1,256,256),np.float32),
                    has_mask_input=np.zeros(1,np.float32), orig_im_size=np.asarray(self.shape,np.float32))
        return self.decoder.run(None, feed)


# ---- réplica de engine/scripts/detector_dino.py:24-38 ----
class Dino:
    def __init__(self, ckpt="/tmp/trk-models/hf/gdino-tiny", threads=6):
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        self.torch = torch
        self.proc = AutoProcessor.from_pretrained(ckpt, local_files_only=True)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(ckpt, local_files_only=True).eval()
        torch.set_num_threads(threads)

    def detect(self, bgr, prompts, box_th=0.25, text_th=0.2, size=None):
        from PIL import Image
        img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        text = ". ".join(p.strip().rstrip(".").lower() for p in prompts) + "."
        kw = {"size": size} if size else {}
        t = time.perf_counter()
        inputs = self.proc(images=img, text=text, return_tensors="pt", **kw)
        t1 = time.perf_counter()
        with self.torch.no_grad():
            out = self.model(**inputs)
        t2 = time.perf_counter()
        res = self.proc.post_process_grounded_object_detection(out, inputs.input_ids, threshold=box_th, text_threshold=text_th,
                                                               target_sizes=[img.size[::-1]])[0]
        dets = []
        labels = res.get("text_labels", res.get("labels"))
        for box, score, lab in zip(res["boxes"].tolist(), res["scores"].tolist(), labels):
            lab = str(lab)
            best = max(prompts, key=lambda p: len(set(p.lower().split()) & set(lab.split())))
            dets.append({"prompt": best, "label": lab, "box": [round(v, 1) for v in box], "score": round(score, 3)})
        self.last = dict(pre=t1 - t, model=t2 - t1, post=time.perf_counter() - t2, input_hw=tuple(inputs["pixel_values"].shape[-2:]))
        return dets


def iou(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]); i = max(0, x1 - x0) * max(0, y1 - y0)
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i + 1e-6)


def filtro_pintura(D, shape, mesa, amin=350, amax=0.12):
    """tools/mesa-agente/pintura.py:75-82"""
    dets = []
    for det in sorted(D, key=lambda x: -x["score"]):
        b = det["box"]; a = (b[2] - b[0]) * (b[3] - b[1])
        if a < amin or a > amax * shape[0] * shape[1]: continue
        if any(iou(b, o["box"]) > 0.5 for o in dets): continue
        if mesa is not None and cv2.pointPolygonTest(mesa, ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), False) < 0: continue
        if det["prompt"] == "a pill organizer" and any(o["prompt"] == "a pill organizer" for o in dets): continue
        dets.append(det)
    return dets


def contorno(m, amin=200):
    """pintura.py:86-90: apertura 3x3, contorno externo mayor, approxPolyDP 1.5"""
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)); cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs: return None
    cc = max(cs, key=cv2.contourArea)
    if cv2.contourArea(cc) < amin: return None
    return cv2.approxPolyDP(cc, 1.5, True).reshape(-1, 2)


P_PINTURA = {"a bowl": "bowl", "a coffee mug": "taza", "a wicker basket": "canasto", "a white plastic box": "caja", "a drinking glass": "vaso", "a spoon": "cuchara",
             "a pill organizer": "pastillero", "a white ball": "planeta", "a white foam sphere": "planeta"}
MESA_PINTURA = np.int32([[817, 669], [248, 579], [310, 250], [863, 357]])
COL = {"bowl": (26, 122, 255), "taza": (255, 229, 0), "canasto": (0, 212, 255), "caja": (60, 255, 182), "vaso": (255, 156, 124), "cuchara": (255, 255, 255), "pastillero": (216, 79, 255), "planeta": (90, 90, 255), "lamina": (255, 156, 124)}


def dibuja(img, objs, titulo=None, escala=1.0):
    o = img.copy()
    if escala != 1.0: o = cv2.resize(o, None, fx=escala, fy=escala, interpolation=cv2.INTER_CUBIC)
    lay = o.copy()
    for ob in objs:
        c = COL.get(ob["name"], (0, 255, 0)); p = (np.asarray(ob["poly"], float) * escala).astype(np.int32)
        cv2.fillPoly(lay, [p], c)
    o = cv2.addWeighted(lay, 0.35, o, 0.65, 0)
    for ob in objs:
        c = COL.get(ob["name"], (0, 255, 0)); p = (np.asarray(ob["poly"], float) * escala).astype(np.int32)
        cv2.polylines(o, [p], True, c, 2, cv2.LINE_AA)
        if "box" in ob:
            b = (np.asarray(ob["box"]) * escala).astype(int); cv2.rectangle(o, (b[0], b[1]), (b[2], b[3]), c, 1)
            cv2.putText(o, f'{ob["name"]} {ob.get("score", 0):.2f}', (b[0], max(12, b[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(o, f'{ob["name"]} {ob.get("score", 0):.2f}', (b[0], max(12, b[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, c, 1, cv2.LINE_AA)
    if titulo:
        cv2.putText(o, titulo, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA); cv2.putText(o, titulo, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return o
