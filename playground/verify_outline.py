"""Verificación estándar de la calibración: fondo BLANCO con los contornos de los objetos en NEGRO (se ven los objetos
en la foto) más marcadores ArUco repartidos por todo el proyector. Se fotografía con la Osmo y se mide el residuo:

- por marcador: dónde cae la luz (detección en la cámara) vs dónde dice el mapa proyector→cámara del escaneo;
- modelo del residuo global: traslación, similitud (escala = FOV/zoom), afín y homografía (pose/baseline), con el
  residuo que queda tras cada uno → diagnóstico: si una traslación lo explica es el proyector corrido; si hace falta
  escala es FOV; si hace falta homografía es pose/baseline;
- por objeto: residuo medio de los marcadores cercanos, en px de proyector y en mm (escala local del mapa; 1 px ≈ 1 mm
  a la distancia de la mesa en scan5, se refina con la hoja carta si está).

    python -m playground.verify_outline --scan captures/scan5 [--sin-offset] [--guardar]

Proyecta por el runtime (overlay) si está corriendo; si no, con ventana propia. Capturas privadas en reports/_crit/.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

W, H = 1920, 1080
URL = "http://127.0.0.1:8790"


def runtime_alive() -> bool:
    try:
        with urllib.request.urlopen(URL + "/api/state", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def cmd(c: str, **args) -> dict:
    body = json.dumps({"cmd": c, "args": args}).encode()
    with urllib.request.urlopen(urllib.request.Request(URL + "/api/command", body, {"Content-Type": "application/json"}), timeout=6) as r:
        return json.loads(r.read())


def objects_from_scan(scan: Path) -> dict[str, np.ndarray]:
    seg = json.loads((scan / "segments.json").read_text()); wb = json.loads((scan / "workbench.json").read_text()) if (scan / "workbench.json").exists() else {}
    out = {}
    for name in ("buda", "cocodrilo", "teclado", "ddj", "ohm", "torre", "papel", "circuit"):
        o = wb.get(name) or seg.get(name)
        if not o:
            continue
        if o.get("contour"):
            out[name] = np.array(o["contour"], np.float32)
        elif o.get("quad"):
            out[name] = np.array(o["quad"], np.float32)
        elif o.get("bbox"):
            x, y, w, h = o["bbox"]; out[name] = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], np.float32)
    return out


def marker_canvas(objs: dict[str, np.ndarray], n_x: int = 8, n_y: int = 5, size: int = 72) -> tuple[np.ndarray, dict[int, np.ndarray]]:
    """Fondo blanco, contornos negros (grosor 3) y marcadores ArUco 4x4 en una grilla; devuelve las esquinas en px."""
    canvas = np.full((H, W, 3), 255, np.uint8)
    for name, poly in objs.items():
        cv2.polylines(canvas, [np.round(poly).astype(np.int32).reshape(-1, 1, 2)], True, (0, 0, 0), 3)
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50); corners = {}
    k = 0
    for j in range(n_y):
        for i in range(n_x):
            x0 = int(80 + i * (W - 160 - size) / (n_x - 1)); y0 = int(60 + j * (H - 120 - size) / (n_y - 1))
            m = cv2.aruco.generateImageMarker(d, k, size); canvas[y0:y0 + size, x0:x0 + size] = m[..., None]
            cv2.rectangle(canvas, (x0 - 6, y0 - 6), (x0 + size + 5, y0 + size + 5), (255, 255, 255), 6)   # quiet zone
            corners[k] = np.array([[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size]], np.float32); k += 1
    return canvas, corners


def fit_models(src: np.ndarray, dst: np.ndarray) -> dict:
    """Residuo (px de proyector) tras ajustar traslación, similitud, afín y homografía de src→dst."""
    out = {}
    t = np.median(dst - src, axis=0); out["traslacion"] = {"dx": round(float(t[0]), 2), "dy": round(float(t[1]), 2),
                                                          "residuo_px": round(float(np.median(np.linalg.norm(dst - (src + t), axis=1))), 2)}
    if len(src) >= 3:
        S, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.LMEDS)
        if S is not None:
            sc = float(np.hypot(S[0, 0], S[1, 0])); pred = (S[:, :2] @ src.T).T + S[:, 2]
            out["similitud"] = {"escala": round(sc, 4), "rot_deg": round(float(np.degrees(np.arctan2(S[1, 0], S[0, 0]))), 3),
                                "residuo_px": round(float(np.median(np.linalg.norm(dst - pred, axis=1))), 2)}
        A, _ = cv2.estimateAffine2D(src, dst, method=cv2.LMEDS)
        if A is not None:
            pred = (A[:, :2] @ src.T).T + A[:, 2]
            out["afin"] = {"residuo_px": round(float(np.median(np.linalg.norm(dst - pred, axis=1))), 2)}
    if len(src) >= 4:
        Hm, _ = cv2.findHomography(src, dst, cv2.LMEDS)
        if Hm is not None:
            p = cv2.perspectiveTransform(src.reshape(-1, 1, 2), Hm).reshape(-1, 2)
            out["homografia"] = {"residuo_px": round(float(np.median(np.linalg.norm(dst - p, axis=1))), 2)}
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scan", default="captures/scan5"); p.add_argument("--sin-offset", action="store_true", help="medir sin el corrimiento global de salida")
    p.add_argument("--guardar", action="store_true"); p.add_argument("--rotate", type=int, default=270); p.add_argument("--segundos", type=float, default=20.0)
    a = p.parse_args(argv)
    scan = Path(a.scan); out = Path("reports/_crit"); out.mkdir(parents=True, exist_ok=True)
    z = np.load(scan / "proj_to_cam.npz"); mapx, mapy, valid = z["mapx"], z["mapy"], z["valid"]
    objs = objects_from_scan(scan); canvas, mk = marker_canvas(objs)
    cv2.imwrite(str(out / "verify_canvas.png"), canvas)
    from slscan.capture import Camera, PatternWindow
    alive = runtime_alive(); win = None; off_prev = None
    if alive:
        off_prev = cmd("output_offset").get("result", {})
        if a.sin_offset:
            cmd("output_offset", dx=0, dy=0)
        cmd("overlay", path=str((out / "verify_canvas.png").resolve()), seconds=a.segundos + 10)
    else:
        win = PatternWindow((W, H), screen="1920x1080"); win.show(canvas)
    cam = Camera("osmo", size="3840x2160", rotate=a.rotate, downscale=2)
    rep = {"scan": str(scan), "offset_salida": None if a.sin_offset else off_prev, "objetos": {}}
    try:
        time.sleep(2.5); cam.flush(15); f = cam.read(); cv2.imwrite(str(out / "verify_foto.png"), f)
        det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50))
        corners, ids, _ = det.detectMarkers(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
        ids = [] if ids is None else [int(i) for i in ids.ravel()]
        rep["marcadores_detectados"] = len(ids)
        src, dst, per = [], [], {}
        hh, ww = f.shape[:2]
        for c, i in zip(corners, ids):
            if i not in mk:
                continue
            pp = mk[i]; pi = c.reshape(4, 2)
            xi = np.clip(pp[:, 0].astype(int), 0, W - 1); yi = np.clip(pp[:, 1].astype(int), 0, H - 1); ok = valid[yi, xi]
            if ok.sum() < 4:
                continue
            exp = np.column_stack([mapx[yi, xi], mapy[yi, xi]])            # dónde debería verse en la cámara
            # escala local cámara/proyector para pasar el residuo a px de proyector
            sc = np.linalg.norm(exp[1] - exp[0]) / np.linalg.norm(pp[1] - pp[0])
            e_cam = pi - exp; e_proj = e_cam / max(sc, 1e-6)
            ctr = pp.mean(0); src.append(ctr); dst.append(ctr + e_proj.mean(0)); per[i] = {"centro_proj": ctr.round(1).tolist(), "residuo_proj_px": e_proj.mean(0).round(2).tolist(), "escala_cam_por_proj": round(float(sc), 3)}
        rep["por_marcador"] = per
        if len(src) >= 3:
            src = np.array(src, np.float32); dst = np.array(dst, np.float32)
            rep["modelos"] = fit_models(src, dst)
            # mm por px de proyector: SIN rig no hay escala real; se usa la hoja carta (215,9 mm de ancho) como referencia
            # y se declara aproximado (vale a la profundidad de la hoja; en otros objetos la escala cambia con la distancia)
            mm_por_px = None
            if "papel" in objs:
                pw = float(np.ptp(objs["papel"][:, 0])); mm_por_px = 215.9 / pw if pw > 50 else None
            rep["mm_por_px_hoja"] = None if mm_por_px is None else round(mm_por_px, 3)
            for name, poly in objs.items():                          # residuo por objeto: marcadores cercanos al objeto
                c = poly.mean(0); d = np.linalg.norm(src - c, axis=1); near = d < 320
                if near.sum() >= 2:
                    r = (dst - src)[near].mean(0); ent = {"residuo_proj_px": r.round(2).tolist(), "n": int(near.sum())}
                    if mm_por_px is not None:
                        ent["residuo_mm_aprox_hoja"] = (r * mm_por_px).round(1).tolist(); ent["nota"] = "escala de la hoja; sin rig no es mm real en otras profundidades"
                    rep["objetos"][name] = ent
        print(json.dumps({k: v for k, v in rep.items() if k != "por_marcador"}, indent=1, ensure_ascii=False))
        time.sleep(max(0.0, a.segundos - 3))                          # que Max lo vea
    finally:
        cam.close()
        if win:
            win.close()
        elif alive:
            cmd("overlay", off=True)
            if a.sin_offset and off_prev:
                cmd("output_offset", dx=off_prev.get("dx", 0), dy=off_prev.get("dy", 0))
    (scan / "verify_outline.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    if not a.guardar:
        shutil.rmtree(out, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
