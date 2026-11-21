"""Escena 3D para el visor web: heightfield del escaneo + objetos segmentados, en píxeles del proyector.

Fuente, en orden: `scan` explícito → env PLAYGROUND_SCAN → <repo>/captures/scan2. Si no hay escaneo (faltan
scene_pov.png o height_proj.npy) se devuelve la escena demo del runtime (playground/scene.py) como planos.

Marco: x, y en px del proyector (origen arriba a la izquierda, y hacia abajo); z = altura relativa × z_scale,
positiva hacia el proyector. La altura del escaneo es paralaje (px de cámara) respecto al plano dominante: no es
métrica. El apex del proyector sale de captures/rig.json si existe; si no, centrado con un throw ratio supuesto.

`mount_web(app, rt)` agrega:
  GET /api/scene3d?nx=192&parts=all|objects   JSON de la escena (heightfield + objetos + proyector)
  GET /api/scene3d/texture.jpg                textura (scene_pov.png, o la demo rasterizada)
  /web/…                                      el visor (index.html, app.js, style.css)
"""

import json
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np

WEB_DIR = Path(__file__).parent
PROJECT_DIR = WEB_DIR.parent.parent
NX_DEFAULT = 192              # 192x108 vértices para 1920x1080 (un vértice cada 10 px)
THROW_RATIO = 1.2             # distancia / ancho de la imagen, supuesto mientras no haya rig.json
RELIEF_FRAC = 0.06            # el relieve robusto (p99.5 − mediana) ocupa ~6 % del ancho de la escena
TEXTURE_URL = "/api/scene3d/texture.jpg"

# nombres de la escena demo → clase del visor (el resto usa su `kind`)
DEMO_CLASS = {"croc": "cocodrilo", "keys": "teclado", "frame": "cuadro", "screen": "pantalla", "table": "mesa"}

_lock = threading.Lock()
_cache: dict = {}             # una sola entrada: el último escaneo cargado


def scan_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("PLAYGROUND_SCAN")
    return Path(env) if env else PROJECT_DIR / "captures" / "scan2"


def has_scan(d: Path) -> bool:
    return (d / "scene_pov.png").exists() and (d / "height_proj.npy").exists()


# ---------------------------------------------------------------------- geometría

def _rect(b) -> list[list[float]]:
    x, y, w, h = b
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _circle(cx, cy, r, n: int = 32) -> list[list[float]]:
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.column_stack([cx + r * np.cos(a), cy + r * np.sin(a)]).tolist()


def _crop_mask(poly: np.ndarray, shape) -> tuple[np.ndarray, int, int]:
    """Máscara del polígono recortada a su bbox (más barato que una máscara del tamaño de la escena)."""
    H, W = shape
    x0, y0 = np.clip(np.floor(poly.min(axis=0)).astype(int), 0, [W - 1, H - 1])
    x1, y1 = np.clip(np.ceil(poly.max(axis=0)).astype(int) + 1, 1, [W, H])
    mask = np.zeros((max(1, y1 - y0), max(1, x1 - x0)), np.uint8)
    cv2.fillPoly(mask, [np.round(poly - [x0, y0]).astype(np.int32)], 1)
    return mask, int(x0), int(y0)


def _z_stats(h: np.ndarray, poly: np.ndarray) -> tuple[float, float, float]:
    """(media, p10, p90) de la altura dentro del polígono; si cae fuera, la altura del centroide."""
    mask, x0, y0 = _crop_mask(poly, h.shape)
    vals = h[y0:y0 + mask.shape[0], x0:x0 + mask.shape[1]][mask > 0]
    if vals.size == 0:
        cx, cy = np.clip(poly.mean(axis=0).astype(int), 0, [h.shape[1] - 1, h.shape[0] - 1])
        v = float(h[cy, cx])
        return v, v, v
    return float(vals.mean()), float(np.percentile(vals, 10)), float(np.percentile(vals, 90))


def _split_at_y(poly: np.ndarray, y_split: float, shape) -> list[np.ndarray]:
    """Corta un polígono con la recta y = y_split: [parte de arriba, parte de abajo] (las que no queden vacías)."""
    mask, x0, y0 = _crop_mask(poly, shape)
    cut = int(np.clip(round(y_split) - y0, 0, mask.shape[0]))
    top, bottom = mask.copy(), mask.copy()
    top[cut:] = 0
    bottom[:cut] = 0
    out = []
    for m in (top, bottom):
        cnts = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
        if not cnts:
            out.append(None)
            continue
        c = cv2.approxPolyDP(max(cnts, key=cv2.contourArea), 2.0, True).reshape(-1, 2)
        out.append(c.astype(float) + [x0, y0] if len(c) >= 3 else None)
    return out


def _obj(oid: str, name: str, cls: str, poly, h: np.ndarray | None, z_scale: float, *, group: str | None = None,
         role: str = "solid", source: str = "", extra: dict | None = None, z: tuple | None = None) -> dict:
    """Objeto del visor: polígono en px del proyector + z (media, p10, p90) ya escalada."""
    p = np.asarray(poly, float).reshape(-1, 2)
    zm, zlo, zhi = z if z is not None else _z_stats(h, p)
    zlo, zhi = min(zlo, zm), max(zhi, zm)          # la media puede caer fuera de p10..p90 (casi todo igual)
    x0, y0 = p.min(axis=0)
    x1, y1 = p.max(axis=0)
    r1 = lambda v: round(float(v), 1)   # noqa: E731
    return {
        "id": oid, "name": name, "class": cls, "group": group, "role": role,
        "polygon": np.round(p, 1).tolist(),
        "z": r1(zm * z_scale), "z_min": r1(zlo * z_scale), "z_max": r1(zhi * z_scale),
        "bbox": [r1(x0), r1(y0), r1(x1 - x0), r1(y1 - y0)], "center": [r1((x0 + x1) / 2), r1((y0 + y1) / 2)],
        "source": source, "extra": extra or {},
    }


# ---------------------------------------------------------------------- escaneo

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _load_scan(d: Path) -> dict:
    """Carga y limpia el escaneo: altura recortada a percentiles robustos, mediana 5x5 y escala sugerida."""
    pov = cv2.imread(str(d / "scene_pov.png"))
    if pov is None:
        raise FileNotFoundError(d / "scene_pov.png")
    H, W = pov.shape[:2]
    raw = np.nan_to_num(np.load(d / "height_proj.npy").astype(np.float32))
    if raw.shape != (H, W):
        raw = cv2.resize(raw, (W, H), interpolation=cv2.INTER_LINEAR)
    valid = pov.sum(axis=2) > 0
    if (d / "proj_to_cam.npz").exists():
        v = np.load(d / "proj_to_cam.npz")["valid"].astype(np.uint8)
        valid = cv2.resize(v, (W, H), interpolation=cv2.INTER_NEAREST) > 0 if v.shape != (H, W) else v > 0
    hv = raw[valid] if valid.any() else raw.ravel()
    med = float(np.median(hv))
    hi = float(np.percentile(hv, 99.5))
    if hi - med < 1e-6:                                   # escena plana
        hi = med + 1.0
    lo = max(float(np.percentile(hv, 0.5)), med - 1.5 * (hi - med))   # el decode deja pozos falsos (hoja, brillos)
    h = cv2.medianBlur(np.clip(raw, lo, hi) - med, 5)     # quita puntos sueltos del gray code
    h[~valid] = 0.0                                       # sin correspondencia: al nivel del plano
    return {
        "pov": pov, "h": h, "valid": valid, "size": [W, H],
        "z_scale": RELIEF_FRAC * W / (hi - med), "clip": [round(lo - med, 3), round(hi - med, 3)],
        "seg": _read_json(d / "segments.json"), "wb": _read_json(d / "workbench.json"),
        "grids": {}, "objects": None, "jpeg": None,
    }


def _scan(d: Path) -> dict:
    """Escaneo cacheado; se recarga si cambia algún archivo (mtime)."""
    names = ("scene_pov.png", "height_proj.npy", "proj_to_cam.npz", "segments.json", "workbench.json")
    key = (str(d.resolve()), tuple((d / n).stat().st_mtime if (d / n).exists() else 0 for n in names))
    with _lock:
        if _cache.get("key") != key:
            t0 = time.perf_counter()
            _cache.clear()
            _cache.update(key=key, data=_load_scan(d), load_ms=round((time.perf_counter() - t0) * 1000))
        return _cache["data"]


def _grid(h: np.ndarray, nx: int) -> dict:
    """Heightfield submuestreado (INTER_AREA): cada vértice es el promedio de su celda, centrado en ella."""
    H, W = h.shape
    ny = max(2, int(round(nx * H / W)))
    small = cv2.resize(h, (nx, ny), interpolation=cv2.INTER_AREA)
    xs = (np.arange(nx) + 0.5) * W / nx - 0.5
    ys = (np.arange(ny) + 0.5) * H / ny - 0.5
    return {"nx": nx, "ny": ny, "xs": np.round(xs, 2).tolist(), "ys": np.round(ys, 2).tolist(), "small": small}


def _scan_objects(s: dict) -> list[dict]:
    """Objetos de workbench.json (más nuevo y fino) con lo que falte de segments.json."""
    seg, wb, h, k = s["seg"], s["wb"], s["h"], s["z_scale"]
    out: list[dict] = []

    def pick(key):
        if key in wb:
            return wb[key], "workbench.json"
        if key in seg:
            return seg[key], "segments.json"
        return None, ""

    b, src = pick("buda")
    if b:
        out.append(_obj("buda", "Buda (cuadro)", "buda", b.get("quad") or _rect(b["bbox"]), h, k, source=src))

    t, src = pick("torre")
    if t:
        poly = np.asarray(t.get("contour") or _rect(t["bbox"]), float)
        if t.get("split_y"):
            out.append(_obj("torre", "torre", "torre", poly, h, k, role="outline", source=src,
                            extra={"split_y": t["split_y"]}))
            top, front = _split_at_y(poly, t["split_y"], h.shape)
            for oid, name, part in (("torre_superior", "torre · cara superior", top),
                                    ("torre_frontal", "torre · cara frontal", front)):
                if part is not None:
                    out.append(_obj(oid, name, "torre", part, h, k, group="torre", source=src))
        else:
            out.append(_obj("torre", "torre", "torre", poly, h, k, source=src))

    p, src = pick("papel")
    if p:
        out.append(_obj("papel", "hoja carta", "papel", p.get("quad") or _rect(p["bbox"]), h, k, source=src,
                        extra={"mm": p.get("mm")} if p.get("mm") else None))

    c, src = pick("cocodrilo")
    if c:
        out.append(_obj("cocodrilo", "cocodrilo", "cocodrilo", c.get("contour") or _rect(c["bbox"]), h, k, source=src))

    kb, src = pick("teclado")
    if kb:
        box = cv2.boxPoints(((kb["rect"][0][0], kb["rect"][0][1]), tuple(kb["rect"][1]), kb["rect"][2])) \
            if kb.get("rect") else _rect(kb["bbox"])
        out.append(_obj("teclado", "teclado", "teclado", box, h, k, role="outline", source=src,
                        extra={"pitch_px": kb.get("pitch_px")}))
        for j, w in enumerate(kb.get("teclas_blancas", [])):
            r = [w["x"], w["y"], w["w"], w["h"]] if isinstance(w, dict) else w
            ex = {"visible": w["visible"]} if isinstance(w, dict) and "visible" in w else {}
            out.append(_obj(f"tecla_b{j + 1:02d}", f"tecla blanca {j + 1}", "tecla_blanca", _rect(r), h, k,
                            group="teclado", source=src, extra=ex))
        black_h = int(kb["bbox"][3] * 0.6) if src == "workbench.json" else None   # como en el resumen del banco
        for j, (x, y, w, bh) in enumerate(kb.get("teclas_negras", [])):
            out.append(_obj(f"tecla_n{j + 1:02d}", f"tecla negra {j + 1}", "tecla_negra",
                            _rect([x, y, w, black_h or bh]), h, k, group="teclado", source=src))

    dj, src = pick("ddj")
    if dj:
        out.append(_obj("ddj", "DDJ", "ddj", _rect(dj["bbox"]), h, k, role="outline", source=src))
        for j, (cx, cy, r) in enumerate(dj.get("jogs", [])):
            out.append(_obj(f"ddj_jog{j + 1}", f"DDJ jog {j + 1}", "ddj", _circle(cx, cy, r), h, k,
                            group="ddj", source=src))

    ci, src = pick("circuit")
    if ci:
        out.append(_obj("circuit", "Circuit Tracks", "circuit", _rect(ci["bbox"]), h, k, source=src))
    return out


def _projector(size, rig_path: Path) -> dict:
    """Apex del proyector en px (x, y) y distancia al plano de imagen (z = fx). Sin rig: centrado y supuesto."""
    W, H = size
    rig = _read_json(rig_path) if rig_path.exists() else {}
    for dev in rig.get("devices", {}).values():
        if dev.get("kind") == "projector" and dev.get("K"):
            K = np.array(dev["K"], float)
            sx = W / dev.get("size", [W, H])[0]           # por si el rig se calibró a otra resolución
            return {"apex": [round(K[0, 2] * sx, 1), round(K[1, 2] * sx, 1), round(K[0, 0] * sx, 1)],
                    "size": [W, H], "source": "rig.json", "note": "K del proyector calibrado"}
    return {"apex": [W / 2, H / 2, THROW_RATIO * W], "size": [W, H], "source": "supuesto",
            "throw_ratio": THROW_RATIO, "note": "sin calibrar: apex centrado y throw ratio supuesto"}


def _scene_from_scan(rt, d: Path, nx: int, parts: str) -> dict:
    s = _scan(d)
    if s["objects"] is None:
        s["objects"] = _scan_objects(s)
    out = {"source": "scan", "scan_dir": _rel(d), "size": s["size"], "objects": s["objects"],
           "frame": {"url": "/api/frame.png", "size": list(rt.canvas_size)}}
    if parts == "objects":
        return out
    if nx not in s["grids"]:
        s["grids"][nx] = _grid(s["h"], nx)
    g = s["grids"][nx]
    z = g["small"] * s["z_scale"]
    out.update(
        heightfield={"nx": g["nx"], "ny": g["ny"], "xs": g["xs"], "ys": g["ys"],
                     "z": np.round(z, 1).ravel().tolist(), "z_scale": round(s["z_scale"], 4),
                     "range": [round(float(z.min()), 1), round(float(z.max()), 1)], "clip": s["clip"],
                     "units": "altura relativa (paralaje en px de cámara) × z_scale; no métrica"},
        texture={"url": TEXTURE_URL, "size": s["size"]},
        projector=_projector(s["size"], d.parent / "rig.json"),
        files={n: (d / n).exists() for n in ("scene_pov.png", "height_proj.npy", "proj_to_cam.npz",
                                              "segments.json", "workbench.json")},
        stats={"valid_ratio": round(float(s["valid"].mean()), 4), "load_ms": _cache.get("load_ms")},
    )
    return out


# ---------------------------------------------------------------------- demo

def _demo_px(rt):
    """mm de la mesa → px del lienzo, igual que Canvas.project (origen 0, y invertida)."""
    H, k = rt.canvas_size[1], rt.px_per_mm
    return lambda x, y: [x * k, H - y * k]


def _scene_from_demo(rt, nx: int, parts: str) -> dict:
    W, H = rt.canvas_size
    k, to_px = rt.px_per_mm, _demo_px(rt)
    objs = []
    with rt.lock:
        for o in rt.scene.objects.values():
            poly = [to_px(x, y) for x, y in o.contour]
            cls = DEMO_CLASS.get(o.id, o.kind)
            z = ((o.bbox[2] + o.bbox[5]) / 2 * k, o.bbox[2] * k, o.bbox[5] * k)
            objs.append(_obj(o.id, o.name, cls, poly, None, 1.0, z=z, source="demo",
                             role="outline" if o.kind == "table" else "solid",
                             extra={"kind": o.kind, "tracked": o.tracked}))
    out = {"source": "demo", "scan_dir": None, "size": [W, H], "objects": objs,
           "frame": {"url": "/api/frame.png", "size": [W, H]}}
    if parts == "objects":
        return out
    g = _grid(np.zeros((H, W), np.float32), nx)
    out.update(
        heightfield={"nx": g["nx"], "ny": g["ny"], "xs": g["xs"], "ys": g["ys"], "z": [0.0] * (g["nx"] * g["ny"]),
                     "z_scale": 1.0, "range": [0.0, 0.0], "clip": [0.0, 0.0],
                     "units": "demo: mesa plana; objetos como planos a su altura (mm × px_per_mm)"},
        texture={"url": TEXTURE_URL, "size": [W, H]},
        projector=_projector((W, H), Path("/nonexistent")),
        files={}, stats={"valid_ratio": 1.0, "load_ms": 0},
    )
    return out


def _demo_texture(rt) -> np.ndarray:
    """Vista de arriba de la demo con los albedos (sin los objetos que se mueven: esos los dibuja el visor)."""
    W, H = rt.canvas_size
    to_px = _demo_px(rt)
    img = np.full((H, W, 3), 18, np.uint8)
    with rt.lock:
        for o in sorted(rt.scene.objects.values(), key=lambda o: o.bbox[5]):
            if o.tracked:
                continue
            pts = np.round([to_px(x, y) for x, y in o.contour]).astype(np.int32)
            color = [int(255 * c) for c in o.albedo[::-1]]            # albedo RGB → BGR
            cv2.fillPoly(img, [pts], color)
            cv2.polylines(img, [pts], True, [min(255, c + 40) for c in color], 2, cv2.LINE_AA)
    return img


# ---------------------------------------------------------------------- API

def _rel(d: Path) -> str:
    try:
        return str(d.resolve().relative_to(PROJECT_DIR.resolve()))
    except ValueError:
        return str(d)


def build_scene3d(rt, nx: int = NX_DEFAULT, parts: str = "all", scan: str | Path | None = None) -> dict:
    """JSON de /api/scene3d. `parts="objects"` devuelve solo los objetos (para refrescarlos en vivo)."""
    nx = int(min(480, max(16, nx)))
    d = scan_dir(scan)
    if has_scan(d):
        return _scene_from_scan(rt, d, nx, parts)
    return _scene_from_demo(rt, nx, parts)


def scene_texture_jpeg(rt, scan: str | Path | None = None, quality: int = 88) -> bytes:
    d = scan_dir(scan)
    if not has_scan(d):
        return cv2.imencode(".jpg", _demo_texture(rt), [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()
    s = _scan(d)
    if s["jpeg"] is None:
        s["jpeg"] = cv2.imencode(".jpg", s["pov"], [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()
    return s["jpeg"]


def mount_web(app, rt, scan: str | Path | None = None) -> None:
    """Monta el visor en /web y la escena en /api/scene3d sobre la app FastAPI del runtime."""
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse, Response
    from fastapi.staticfiles import StaticFiles

    class WebFiles(StaticFiles):
        """Estáticos del visor: no sirve el Python de la carpeta y pide revalidar (se edita en vivo)."""

        async def get_response(self, path, scope):
            p = Path(path)
            if p.suffix in (".py", ".pyc") or "__pycache__" in p.parts:
                raise HTTPException(status_code=404)
            resp = await super().get_response(path, scope)
            resp.headers["Cache-Control"] = "no-cache"
            return resp

    @app.get("/api/scene3d")
    def scene3d(nx: int = NX_DEFAULT, parts: str = "all"):
        return JSONResponse(build_scene3d(rt, nx=nx, parts=parts, scan=scan))

    @app.get("/api/scene3d/texture.jpg")
    def scene3d_texture():
        return Response(scene_texture_jpeg(rt, scan), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})

    app.mount("/web", WebFiles(directory=WEB_DIR, html=True), name="web")
