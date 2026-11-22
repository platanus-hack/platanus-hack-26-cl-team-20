"""DAViD (Microsoft, ICCV 2025): normal, profundidad relativa y recorte suave de personas desde una cámara común.

Modelos ONNX (MIT) en $HF_HOME/david (por defecto /Volumes/Externo/hf-cache/david), bajados de
facesyntheticspubwedata.z6.web.core.windows.net/iccv-2025/models/:

    normal-model-vitb16_384.onnx              428 MB   172 GMAC   solo normal
    foreground-segmentation-model-vitb16_384  428 MB   172 GMAC   solo recorte
    depth-model-vitb16_384.onnx               428 MB   172 GMAC   solo profundidad
    multi-task-model-vitl16_384.onnx          1.3 GB   390 GMAC   las tres juntas (ViT-L)

Entrada BGR float [0, 1], 512x512 (cuadrado con borde replicado, como el runtime de Microsoft). Salidas en el tamaño
de la imagen de entrada:
    normal  (h, w, 3) unitaria, marco de la cámara: x a la derecha, y hacia arriba, z hacia la cámara
    alpha   (h, w) 0..1
    depth   (h, w) relativa (mayor = más cerca), sin escala ni offset métricos

Dos caminos:
- **CoreML** (Mac, por defecto): `export_coreml(task, size)` convierte una vez ONNX -> torch (onnx2torch) -> traza a
  tamaño fijo -> mlprogram fp16 (`<task>-<size>.mlpackage` junto a los ONNX); al correr solo hace falta coremltools.
  Medido en el M4 (17-nov): normal 384 px ≈ 40 ms en el Neural Engine (≈ 25 fps), 512 px ≈ 63 ms; error contra el
  ONNX 0,1°. A 256 px la traza fp16 se desvía (9°): no usar.
- **onnxruntime** (`backend="onnx"`, providers cpu | cuda): en el Mac solo CPU (~1 s por cuadro; el EP de CoreML de
  onnxruntime toma 244 de 850 nodos y queda más lento). En el PC con la RTX 4070: CUDA/TensorRT.

`bench()` mide ms por cuadro.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import cv2
import numpy as np

MODEL_DIR = Path(os.environ.get("DAVID_MODELS") or Path(os.environ.get("HF_HOME", "/Volumes/Externo/hf-cache")) / "david")
URL = "https://facesyntheticspubwedata.z6.web.core.windows.net/iccv-2025/models/"
FILES = {"normal": "normal-model-vitb16_384.onnx",
         "alpha": "foreground-segmentation-model-vitb16_384.onnx",
         "depth": "depth-model-vitb16_384.onnx",
         "multi": "multi-task-model-vitl16_384.onnx"}
ROI = 512


def model_path(task: str) -> Path:
    return MODEL_DIR / FILES[task]


def _providers(kind: str) -> list:
    kind = (kind or "cpu").lower()
    if kind == "cuda":
        return ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def prepare(img_bgr: np.ndarray, roi: int = ROI) -> tuple[np.ndarray, tuple]:
    """Imagen BGR uint8 -> tensor (1, 3, roi, roi) float32 y el relleno para volver al tamaño original."""
    h, w = img_bgr.shape[:2]
    if h < w:
        t = (w - h) // 2; pad = (t, w - h - t, 0, 0)
    else:
        l = (h - w) // 2; pad = (0, 0, l, h - w - l)
    sq = cv2.copyMakeBorder(img_bgr, *pad, borderType=cv2.BORDER_REPLICATE)
    x = cv2.resize(sq, (roi, roi), interpolation=cv2.INTER_AREA).astype(np.float32) * (1.0 / 255.0)
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None]), (h, w, sq.shape[0], pad)


def unpad(m: np.ndarray, meta: tuple, out_size: tuple[int, int] | None = None) -> np.ndarray:
    """Salida (roi, roi[, c]) -> tamaño original (h, w[, c]) o `out_size` (w, h). Recorta en el espacio del modelo
    antes de escalar (más barato que escalar el cuadrado entero)."""
    h, w, side, (t, b, l, r) = meta
    k = m.shape[0] / side
    crop = m[int(round(t * k)):m.shape[0] - int(round(b * k)), int(round(l * k)):m.shape[1] - int(round(r * k))]
    return cv2.resize(crop, out_size or (w, h), interpolation=cv2.INTER_LINEAR)


def coreml_path(task: str, size: int) -> Path:
    return MODEL_DIR / f"{task}-{size}.mlpackage"


def export_coreml(task: str, size: int = 384) -> Path:
    """ONNX -> mlprogram fp16 de tamaño fijo (una vez; ~1 min). Necesita torch, onnx2torch y coremltools (venv
    /Volumes/Externo/venvs/david); el runtime después solo necesita coremltools."""
    out = coreml_path(task, size)
    if out.exists():
        return out
    import coremltools as ct
    import onnx
    import torch
    from onnx2torch import convert
    src = model_path(task)
    if not src.exists():
        raise FileNotFoundError(f"falta {src} (bajar de {URL}{FILES[task]})")
    net = convert(onnx.load(str(src))).eval()
    x = torch.rand(1, 3, size, size)
    with torch.no_grad():
        tr = torch.jit.trace(net, x, check_trace=False)
    n_out = len(onnx.load(str(src), load_external_data=False).graph.output)
    outs = [ct.TensorType(name=f"out{i}") for i in range(n_out)]
    ml = ct.convert(tr, inputs=[ct.TensorType(name="input", shape=x.shape)], outputs=outs, convert_to="mlprogram",
                    compute_precision=ct.precision.FLOAT16, compute_units=ct.ComputeUnit.ALL,
                    minimum_deployment_target=ct.target.macOS15)
    ml.save(str(out))
    return out


class _CoreMLSession:
    def __init__(self, path: Path, units: str = "ALL"):
        import coremltools as ct
        self.m = ct.models.MLModel(str(path), compute_units=getattr(ct.ComputeUnit, units))
        self.outs = [o.name for o in self.m.get_spec().description.output]

    def run(self, x: np.ndarray) -> list[np.ndarray]:
        r = self.m.predict({"input": x})
        return [np.asarray(r[k]) for k in self.outs]


class _OrtSession:
    def __init__(self, path: Path, providers: str, threads: int = 0):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if threads:
            so.intra_op_num_threads = threads
        self.s = ort.InferenceSession(str(path), sess_options=so, providers=_providers(providers))
        self.name = self.s.get_inputs()[0].name

    def run(self, x: np.ndarray) -> list[np.ndarray]:
        return self.s.run(None, {self.name: x})


class David:
    """Una o varias sesiones (normal sola, normal+recorte, …, o el multitarea). `infer(bgr) -> dict`.

    backend "coreml": mlpackages de `size` px (se exportan si faltan y hay torch); "onnx": providers cpu | cuda,
    `size` libre (el grafo acepta lado dinámico múltiplo de 16)."""

    def __init__(self, tasks: tuple[str, ...] = ("normal", "alpha"), backend: str = "coreml", size: int = 384,
                 providers: str = "cpu", units: str = "ALL", threads: int = 0):
        self.tasks = tuple(tasks)
        self.backend, self.size = backend, int(size)
        self.sessions: dict[str, object] = {}
        names = ["multi"] if "multi" in self.tasks else [t for t in self.tasks if t in ("normal", "alpha", "depth")]
        for t in names:
            if backend == "coreml":
                p = coreml_path(t, self.size)
                self.sessions[t] = _CoreMLSession(p if p.exists() else export_coreml(t, self.size), units)
            else:
                p = model_path(t)
                if not p.exists():
                    raise FileNotFoundError(f"falta {p} (bajar de {URL}{FILES[t]})")
                self.sessions[t] = _OrtSession(p, providers, threads)
        self.ms: dict[str, float] = {}

    def raw(self, x: np.ndarray, only: tuple[str, ...] | None = None) -> dict[str, np.ndarray]:
        """Tensor preparado -> salidas crudas (roi x roi): normal (roi, roi, 3), alpha, depth. `only` corre solo
        esas sesiones (p. ej. la normal en cada cuadro y el recorte cada 3)."""
        out: dict[str, np.ndarray] = {}
        for t, s in self.sessions.items():
            if only is not None and t not in only:
                continue
            t0 = time.perf_counter()
            r = s.run(x)
            self.ms[t] = (time.perf_counter() - t0) * 1000
            if t == "multi":                       # orden del runtime de Microsoft: depth, normal, foreground
                out["depth"], out["normal"], out["alpha"] = r[0][0].squeeze(), r[1][0].transpose(1, 2, 0), r[2][0].squeeze()
            elif t == "normal":
                out["normal"] = r[0][0].transpose(1, 2, 0)
            else:
                out[t] = r[0][0].squeeze()
        return out

    def infer(self, img_bgr: np.ndarray, out_size: tuple[int, int] | None = None,
              only: tuple[str, ...] | None = None) -> dict[str, np.ndarray]:
        """Mapas en el tamaño de la imagen, o en `out_size` (w, h) si se pide uno de trabajo más chico."""
        x, meta = prepare(img_bgr, self.size)
        o = self.raw(x, only)
        res: dict[str, np.ndarray] = {}
        if "normal" in o:
            n = o["normal"].astype(np.float32)
            n /= np.linalg.norm(n, axis=-1, keepdims=True) + 1e-6
            res["normal"] = unpad(n, meta, out_size)
        if "alpha" in o:
            res["alpha"] = np.clip(unpad(o["alpha"].astype(np.float32), meta, out_size), 0, 1)
        if "depth" in o:
            # salida cruda: mayor = más cerca (17-nov: rodillas de una persona sentada > pecho > cabeza > muro). Rango
            # chico sobre un offset grande (~0,421..0,427): en fp16 quedan ~25 niveles, por eso se suaviza al usarla
            res["depth"] = unpad(o["depth"].astype(np.float32), meta, out_size)
        return res


def normal_to_rgb(n: np.ndarray, alpha: np.ndarray | None = None) -> np.ndarray:
    """Normal -> imagen BGR uint8 con la convención habitual (x rojo, y verde, z azul, 0.5 + 0.5 n)."""
    rgb = ((n * 0.5 + 0.5) * 255).clip(0, 255).astype(np.uint8)
    bgr = rgb[..., ::-1].copy()
    if alpha is not None:
        bgr = (bgr.astype(np.float32) * alpha[..., None]).astype(np.uint8)
    return bgr


def bench(tasks=("normal",), backend="coreml", size: int = 384, n: int = 20, img: np.ndarray | None = None,
          **kw) -> dict:
    """ms por cuadro (mediana) de `infer` sobre una imagen 960x540 (la de trabajo en vivo); la carga y el primer
    cuadro van aparte. Medir con la máquina quieta: dos benchmarks a la vez se pisan en el Neural Engine."""
    if img is None:
        img = (np.random.default_rng(0).random((540, 960, 3)) * 255).astype(np.uint8)
    t0 = time.perf_counter()
    d = David(tasks, backend, size, **kw)
    load = time.perf_counter() - t0
    t0 = time.perf_counter(); d.infer(img); first = time.perf_counter() - t0
    ts = []
    for _ in range(n):
        t0 = time.perf_counter(); d.infer(img); ts.append((time.perf_counter() - t0) * 1000)
    med = float(np.median(ts))
    return {"tasks": list(tasks), "backend": backend, "size": size, "load_s": round(load, 1),
            "first_s": round(first, 1), "ms_median": round(med, 1), "fps": round(1000 / med, 1),
            "ms_model": {k: round(v, 1) for k, v in d.ms.items()}}
