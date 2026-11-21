"""Trackers de posición con dos cámaras: la Osmo (arriba, local) y la Brio del PC (pared, por HTTP).

Para cada objeto del escaneo (Buda, cocodrilo, teclado, platos de la DDJ, Circuit, torre, hoja) se sabe dónde
debería verse en cada cámara (mapa proyector→cámara del gray code de esa cámara). En cada tick se captura un
cuadro por cámara, se busca el objeto cerca de donde debería estar (plantilla de la foto blanca del escaneo) y se
reporta cuánto se corrió, en píxeles de cámara y de proyector. Deja una imagen lado a lado por tick (privada,
en reports/dual/) y reports/dual/tracks.json. Con --project proyecta los contornos mientras captura: así se ve en
las dos cámaras si lo proyectado cae sobre lo real (test de detalle).

    python -m playground.dualview --scan captures/scan4 --brio http://192.168.1.233:8794 --ticks 3 --project
    python -m playground.dualview --scan captures/scan4 --no-brio --ticks 1            # solo Osmo

Mapa de la Brio: python -m slscan scan --camera http://192.168.1.233:8794 --rotate 0 --cam-size 1920x1080 \\
    --name brio --screen 1920x1080 --size 1920x1080 --settle 1.0 --skip-bits 2 --out captures/scan4_brio --name-out scan4_brio
Si no existe captures/<scan>_brio/brio/map.npz, la Brio se muestra sin trackers (solo el cuadro).
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

W, H = 1920, 1080


# ------------------------------------------------------------------------------------ objetos del escaneo
def load_objects(scan: Path) -> dict[str, dict]:
    """{nombre: {"poly": [[x,y],...] en proyector, "kind": ...}} desde segments.json + workbench.json."""
    seg = json.loads((scan / "segments.json").read_text()) if (scan / "segments.json").exists() else {}
    wb = json.loads((scan / "workbench.json").read_text()) if (scan / "workbench.json").exists() else {}
    objs: dict[str, dict] = {}

    def rect(b):
        x, y, w, h = [int(v) for v in b]; return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]

    if "buda" in seg:
        objs["buda"] = {"poly": seg["buda"].get("quad") or rect(seg["buda"]["bbox"]), "kind": "quad"}
    croc = wb.get("cocodrilo") or seg.get("cocodrilo")
    if croc:
        objs["cocodrilo"] = {"poly": croc.get("contour") or rect(croc["bbox"]), "kind": "contour"}
    tec = wb.get("teclado") or seg.get("teclado")
    if tec:
        if tec.get("rect"):
            (cx, cy), (rw, rh), ang = tec["rect"]
            objs["teclado"] = {"poly": cv2.boxPoints(((cx, cy), (rw, rh), ang)).tolist(), "kind": "rect"}
        else:
            objs["teclado"] = {"poly": rect(tec["bbox"]), "kind": "rect"}
    for i, j in enumerate((seg.get("ddj") or {}).get("jogs") or []):
        x, y, r = j[:3]
        objs[f"plato_{i + 1}"] = {"poly": [[x + r * math.cos(t), y + r * math.sin(t)] for t in np.linspace(0, 2 * math.pi, 24, endpoint=False)],
                                  "kind": "circle", "circle": [x, y, r]}
    for name in ("circuit", "ohm", "torre", "papel"):
        if name in seg and seg[name].get("bbox"):
            objs[name] = {"poly": rect(seg[name]["bbox"]), "kind": "rect"}
    return objs


# ------------------------------------------------------------------------------------ cámaras
class CamView:
    """Una cámara con su mapa proyector→cámara (si lo tiene) y su foto de referencia."""

    def __init__(self, name: str, cam, map_dir: Path | None, cam_name: str):
        self.name = name; self.cam = cam; self.mapx = self.mapy = self.valid = None; self.ref = None
        if map_dir is not None:
            p2c = map_dir / "proj_to_cam.npz"; mp = map_dir / cam_name / "map.npz"
            if not p2c.exists() and mp.exists():
                from slscan.graycode import Decoded
                from slscan.recon import dense_proj_to_cam
                mx, my, v = dense_proj_to_cam(Decoded.load(mp), W, H)
                np.savez_compressed(p2c, mapx=mx, mapy=my, valid=v)
            if p2c.exists():
                z = np.load(p2c); self.mapx, self.mapy, self.valid = z["mapx"], z["mapy"], z["valid"]
                ref = map_dir / cam_name / "color.png"
                self.ref = cv2.imread(str(ref)) if ref.exists() else None

    @property
    def has_map(self) -> bool:
        return self.mapx is not None and self.ref is not None

    def to_cam(self, pts) -> np.ndarray | None:
        """Puntos del proyector → cámara (solo los que caen en el mapa válido)."""
        if self.mapx is None:
            return None
        out = []
        for x, y in pts:
            xi, yi = int(np.clip(x, 0, W - 1)), int(np.clip(y, 0, H - 1))
            if self.valid[yi, xi]:
                out.append([self.mapx[yi, xi], self.mapy[yi, xi]])
        return np.array(out, np.float32) if len(out) >= 3 else None

    def proj_scale(self, x: float, y: float) -> float:
        """px de cámara por px de proyector alrededor de (x, y): para pasar un corrimiento de cámara a proyector."""
        xi, yi = int(np.clip(x, 8, W - 9)), int(np.clip(y, 8, H - 9))
        if not (self.valid[yi, xi - 8] and self.valid[yi, xi + 8] and self.valid[yi - 8, xi] and self.valid[yi + 8, xi]):
            return float("nan")
        dx = math.hypot(self.mapx[yi, xi + 8] - self.mapx[yi, xi - 8], self.mapy[yi, xi + 8] - self.mapy[yi, xi - 8]) / 16
        dy = math.hypot(self.mapx[yi + 8, xi] - self.mapx[yi - 8, xi], self.mapy[yi + 8, xi] - self.mapy[yi - 8, xi]) / 16
        return (dx + dy) / 2

    def track(self, frame: np.ndarray, name: str, obj: dict) -> dict:
        """Busca la plantilla (foto blanca del escaneo en el bbox del objeto) cerca de su posición esperada."""
        res = {"objeto": name, "camara": self.name, "visto": False}
        pts = self.to_cam(obj["poly"])
        if pts is None:
            res["motivo"] = "fuera del mapa"; return res
        x0, y0 = np.floor(pts.min(0)).astype(int); x1, y1 = np.ceil(pts.max(0)).astype(int)
        hh, ww = self.ref.shape[:2]
        x0, y0 = max(0, x0), max(0, y0); x1, y1 = min(ww, x1), min(hh, y1)
        bw, bh = x1 - x0, y1 - y0
        res["esperado_cam"] = [int(x0), int(y0), int(bw), int(bh)]
        if bw < 12 or bh < 12:
            res["motivo"] = "bbox muy chico"; return res
        tpl = cv2.cvtColor(self.ref[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        mx, my = int(bw * 0.4) + 10, int(bh * 0.4) + 10
        sx0, sy0 = max(0, x0 - mx), max(0, y0 - my); sx1, sy1 = min(ww, x1 + mx), min(hh, y1 + my)
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if frame.shape[:2] != self.ref.shape[:2]:
            g = cv2.resize(g, (ww, hh))
        win = g[sy0:sy1, sx0:sx1]
        if win.shape[0] <= tpl.shape[0] or win.shape[1] <= tpl.shape[1]:
            res["motivo"] = "ventana de búsqueda chica"; return res
        # plantilla y ventana ecualizadas: la foto de referencia es con luz blanca y la de ahora puede ser otra luz
        r = cv2.matchTemplate(cv2.equalizeHist(win), cv2.equalizeHist(tpl), cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(r)
        fx, fy = sx0 + loc[0], sy0 + loc[1]
        dx, dy = fx - x0, fy - y0
        cx, cy = np.mean([p[0] for p in obj["poly"]]), np.mean([p[1] for p in obj["poly"]])
        s = self.proj_scale(cx, cy)
        res.update({"visto": bool(score > 0.45), "score": round(float(score), 3), "hallado_cam": [int(fx), int(fy), int(bw), int(bh)],
                    "delta_cam_px": [int(dx), int(dy)], "delta_proj_px": None if not (s > 0) else [round(float(dx) / s, 1), round(float(dy) / s, 1)]})
        return res


COLORS = {"buda": (0, 165, 255), "cocodrilo": (0, 255, 255), "teclado": (0, 255, 0), "plato_1": (255, 0, 255), "plato_2": (255, 0, 255),
          "circuit": (255, 120, 0), "ohm": (200, 120, 255), "torre": (255, 255, 0), "papel": (255, 255, 255)}


def draw(view: CamView, frame: np.ndarray, objs: dict, tracks: list[dict]) -> np.ndarray:
    out = frame.copy()
    for name, obj in objs.items():
        pts = view.to_cam(obj["poly"])
        if pts is not None:
            cv2.polylines(out, [pts.astype(np.int32).reshape(-1, 1, 2)], True, COLORS.get(name, (200, 200, 200)), 2)
            cv2.putText(out, name, tuple(pts.min(0).astype(int) + [0, -6]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLORS.get(name, (200, 200, 200)), 2)
    for t in tracks:
        if t.get("hallado_cam"):
            x, y, w, h = t["hallado_cam"]; col = (0, 255, 0) if t["visto"] else (0, 0, 255)
            cv2.rectangle(out, (x, y), (x + w, y + h), col, 2)
            dp = t.get("delta_proj_px"); txt = f"{t['score']:.2f} d=({t['delta_cam_px'][0]},{t['delta_cam_px'][1]})" + (f" proj=({dp[0]},{dp[1]})" if dp else "")
            cv2.putText(out, txt, (x, y + h + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 2)
    cv2.putText(out, view.name, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    return out


def side_by_side(frames: list[np.ndarray], height: int = 1080) -> np.ndarray:
    rs = [cv2.resize(f, (int(f.shape[1] * height / f.shape[0]), height)) for f in frames]
    return np.hstack(rs)


def outline_canvas(objs: dict) -> np.ndarray:
    """Contornos de los objetos para proyectar (test de detalle)."""
    c = np.zeros((H, W, 3), np.uint8)
    for name, obj in objs.items():
        pts = np.array(obj["poly"], np.int32).reshape(-1, 1, 2)
        cv2.polylines(c, [pts], True, COLORS.get(name, (200, 200, 200)), 3)
        if obj["kind"] == "circle":
            x, y, r = obj["circle"]; cv2.circle(c, (int(x), int(y)), 4, COLORS[name], -1)
        cv2.putText(c, name, tuple(pts.reshape(-1, 2).min(0) + [4, 22]), cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLORS.get(name, (200, 200, 200)), 2)
    return c


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scan", default="captures/scan5"); p.add_argument("--brio", default="http://192.168.1.233:8794")
    p.add_argument("--no-brio", action="store_true"); p.add_argument("--brio-scan", default=None)
    p.add_argument("--ticks", type=int, default=3); p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--project", action="store_true", help="proyectar los contornos mientras se captura")
    p.add_argument("--out", default="reports/dual"); p.add_argument("--rotate", type=int, default=270)
    a = p.parse_args(argv)
    scan = Path(a.scan); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    objs = load_objects(scan)
    print("objetos:", ", ".join(objs) or "ninguno")
    from slscan.capture import Camera, PatternWindow, RemoteCamera
    views: list[CamView] = [CamView("osmo", Camera("osmo", size="3840x2160", rotate=a.rotate, downscale=2), scan, "osmo")]
    if not a.no_brio:
        try:
            brio_scan = Path(a.brio_scan) if a.brio_scan else Path(str(scan) + "_brio")
            views.append(CamView("brio", RemoteCamera(a.brio, name="brio", lossless=False), brio_scan if brio_scan.exists() else None, "brio"))
            print("brio:", "con mapa" if views[-1].has_map else "sin mapa (solo cuadro)", views[-1].cam.health())
        except Exception as e:  # noqa: BLE001
            print("brio no disponible:", e)
    win = None
    if a.project:
        win = PatternWindow((W, H), screen="1920x1080"); win.show(outline_canvas(objs)); time.sleep(1.5)
    all_tracks = []
    try:
        for k in range(a.ticks):
            frames, annotated, tracks = [], [], []
            for v in views:
                v.cam.flush(8); f = v.cam.read(); frames.append(f)
                tr = [v.track(f, n, o) for n, o in objs.items()] if v.has_map else []
                tracks += tr; annotated.append(draw(v, f, objs, tr) if v.has_map else f)
            img = side_by_side(annotated); cv2.imwrite(str(out / f"tick_{k:02d}.png"), img); cv2.imwrite(str(out / "latest.png"), img)
            # deriva de cámara: si TODOS los objetos de una cámara se corren igual, la que se movió es la cámara (pasa
            # cuando Max la acomoda); se descuenta para dejar el movimiento real de cada objeto
            drift = {}
            for v in views:
                ds = [t["delta_cam_px"] for t in tracks if t["camara"] == v.name and t.get("visto")]
                if len(ds) >= 3:
                    med = np.median(np.array(ds), axis=0); spread = np.abs(np.array(ds) - med).max()
                    if spread <= 4 and np.abs(med).max() >= 2:
                        drift[v.name] = [int(med[0]), int(med[1])]
            for t in tracks:
                d = drift.get(t["camara"])
                if d and t.get("delta_cam_px"):
                    t["deriva_camara_px"] = d; t["delta_objeto_px"] = [t["delta_cam_px"][0] - d[0], t["delta_cam_px"][1] - d[1]]
            if drift:
                print(f"tick {k} deriva de cámara (se descuenta): {drift}")
            all_tracks.append({"tick": k, "t": time.strftime("%H:%M:%S"), "deriva": drift, "tracks": tracks})
            for t in tracks:
                if t.get("hallado_cam"):
                    print(f"tick {k} {t['camara']:5s} {t['objeto']:10s} score {t['score']:.2f} corrimiento cam {t['delta_cam_px']} proj {t['delta_proj_px']}")
                else:
                    print(f"tick {k} {t['camara']:5s} {t['objeto']:10s} {t.get('motivo')}")
            if k < a.ticks - 1:
                time.sleep(a.interval)
    finally:
        for v in views:
            v.cam.close()
        if win:
            win.close()
    (out / "tracks.json").write_text(json.dumps(all_tracks, indent=1))
    print("listo:", out / "latest.png", out / "tracks.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
