"""Afinar el encaje del cocodrilo (o de cualquier objeto) con capturas: 'screenshot tras screenshot moviéndolo un poquito'.

1) Proyecta un checker grueso dentro del contorno del objeto y un checker fino de otro color afuera (para ver los
   límites a ojo) y lo deja unos segundos.
2) Captura con luz blanca y segmenta el objeto en la cámara (silueta real, por tono en el caso del cocodrilo).
3) Para una grilla de corrimientos (dx, dy) proyecta el contorno relleno corrido, captura y mide IoU entre lo
   iluminado y la silueta real. Elige el mejor y lo aplica con `nudge` en el runtime (queda en ajustes.json).

    python -m playground.croc_refine --scan captures/scan5 --objeto croc --paso 4 --radio 12
    python -m playground.croc_refine --solo-checker          # solo mostrar el checker 20 s

Capturas privadas en reports/_crit/ (se borran al final salvo --guardar).
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


def contour_of(scan: Path, objeto: str) -> np.ndarray:
    seg = json.loads((scan / "segments.json").read_text()); wb = json.loads((scan / "workbench.json").read_text()) if (scan / "workbench.json").exists() else {}
    key = {"croc": "cocodrilo", "keys": "teclado"}.get(objeto, objeto)
    o = wb.get(key) or seg.get(key)
    if o is None:
        raise SystemExit(f"no hay {key} en {scan}")
    if o.get("contour"):
        return np.array(o["contour"], np.float32)
    x, y, w, h = o["bbox"]; return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], np.float32)


def checker(size: int, color_a, color_b, shape=(H, W)) -> np.ndarray:
    yy, xx = np.indices(shape); m = ((yy // size + xx // size) % 2).astype(bool)
    out = np.zeros((shape[0], shape[1], 3), np.uint8); out[m] = color_a; out[~m] = color_b
    return out


def checker_canvas(cnt: np.ndarray, dx=0, dy=0) -> np.ndarray:
    """Checker grueso (24 px, blanco/negro) dentro del contorno; fino (6 px, cian/azul oscuro) afuera."""
    mask = np.zeros((H, W), np.uint8); cv2.fillPoly(mask, [np.round(cnt + [dx, dy]).astype(np.int32)], 255)
    inside = checker(24, (255, 255, 255), (0, 0, 0)); outside = checker(6, (200, 120, 0), (40, 20, 0))
    out = outside.copy(); out[mask > 0] = inside[mask > 0]
    cv2.polylines(out, [np.round(cnt + [dx, dy]).astype(np.int32)], True, (0, 0, 255), 2)
    return out


def fill_canvas(cnt: np.ndarray, dx=0, dy=0) -> np.ndarray:
    out = np.zeros((H, W, 3), np.uint8); cv2.fillPoly(out, [np.round(cnt + [dx, dy]).astype(np.int32)], (255, 255, 255)); return out


def cam_silhouette(frame_white: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    """Silueta del cocodrilo en la cámara con luz blanca: tono verde-cian desaturado (igual que segment_pov) dentro del ROI."""
    x0, y0, x1, y1 = roi
    hsv = cv2.cvtColor(frame_white, cv2.COLOR_BGR2HSV); hval, sat, val = cv2.split(hsv)
    m = (hval >= 55) & (hval <= 125) & (sat >= 20) & (sat <= 110) & (val >= 95) & (val <= 235)
    z = np.zeros_like(m); z[y0:y1, x0:x1] = True; m &= z
    m8 = cv2.morphologyEx(m.astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m8 = cv2.morphologyEx(m8, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m8)
    if n <= 1:
        return m8 > 0
    i = 1 + int(np.argmax(st[1:, 4])); return lab == i


def lit_mask(frame: np.ndarray, dark: np.ndarray, roi) -> np.ndarray:
    """Qué quedó iluminado por el relleno blanco: diferencia contra el cuadro con proyección negra."""
    x0, y0, x1, y1 = roi
    d = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.int16) - cv2.cvtColor(dark, cv2.COLOR_BGR2GRAY).astype(np.int16)
    m = np.zeros(d.shape, bool); m[y0:y1, x0:x1] = d[y0:y1, x0:x1] > 35
    m8 = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)); return m8 > 0


def iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum(); uni = np.logical_or(a, b).sum(); return float(inter) / float(uni) if uni else 0.0


class RuntimeWindow:
    """Si el runtime está proyectando, ninguna otra ventana llega al proyector (macOS deja la suya encima): se le pide
    mostrar la imagen con el comando overlay. Misma interfaz que PatternWindow (show/close)."""

    def __init__(self, url="http://127.0.0.1:8790", tmp=Path("reports/_crit/_overlay.png")):
        self.url = url; self.tmp = tmp; tmp.parent.mkdir(parents=True, exist_ok=True); self.n = 0

    @staticmethod
    def alive(url="http://127.0.0.1:8790") -> bool:
        try:
            with urllib.request.urlopen(url + "/api/state", timeout=2) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def show(self, img: np.ndarray, seconds: float = 30.0) -> None:
        self.n += 1; path = self.tmp.with_name(f"_overlay_{self.n % 2}.png"); cv2.imwrite(str(path), img)
        body = json.dumps({"cmd": "overlay", "args": {"path": str(path.resolve()), "seconds": seconds}}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.url + "/api/command", body, {"Content-Type": "application/json"}), timeout=4) as r:
            r.read()

    def close(self) -> None:
        body = json.dumps({"cmd": "overlay", "args": {"off": True}}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(self.url + "/api/command", body, {"Content-Type": "application/json"}), timeout=4).read()
        except Exception:  # noqa: BLE001
            pass


def nudge_runtime(objeto: str, dx: float, dy: float, url="http://127.0.0.1:8790") -> dict | None:
    body = json.dumps({"cmd": "nudge", "args": {"object": objeto, "dx": dx, "dy": dy}}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url + "/api/command", body, {"Content-Type": "application/json"}), timeout=4) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scan", default="captures/scan5"); p.add_argument("--objeto", default="croc")
    p.add_argument("--paso", type=int, default=4); p.add_argument("--radio", type=int, default=12)
    p.add_argument("--checker-seg", type=float, default=12.0); p.add_argument("--solo-checker", action="store_true")
    p.add_argument("--aplicar", action="store_true", help="aplicar el mejor corrimiento en el runtime (nudge)")
    p.add_argument("--guardar", action="store_true"); p.add_argument("--rotate", type=int, default=270)
    a = p.parse_args(argv)
    scan = Path(a.scan); cnt = contour_of(scan, a.objeto); out = Path("reports/_crit"); out.mkdir(parents=True, exist_ok=True)
    from slscan.capture import PatternWindow, Camera
    z = np.load(scan / "proj_to_cam.npz"); mapx, mapy, valid = z["mapx"], z["mapy"], z["valid"]
    # ROI en cámara: bbox del contorno llevado a la cámara, con margen
    ci = np.round(cnt).astype(int); xs = np.clip(ci[:, 0], 0, W - 1); ys = np.clip(ci[:, 1], 0, H - 1); ok = valid[ys, xs]
    cx, cy = mapx[ys[ok], xs[ok]], mapy[ys[ok], xs[ok]]
    win = RuntimeWindow() if RuntimeWindow.alive() else PatternWindow((W, H), screen="1920x1080")
    print("salida:", "runtime (overlay)" if isinstance(win, RuntimeWindow) else "ventana propia")
    cam = Camera("osmo", size="3840x2160", rotate=a.rotate, downscale=2)
    rep = {"objeto": a.objeto, "scan": str(scan), "pruebas": []}
    try:
        hh, ww = cam.read().shape[:2]
        roi = (int(max(0, cx.min() - 60)), int(max(0, cy.min() - 60)), int(min(ww, cx.max() + 60)), int(min(hh, cy.max() + 60)))
        rep["roi_cam"] = roi
        # 1) checker para que Max vea los límites
        win.show(checker_canvas(cnt)); time.sleep(a.checker_seg); cam.flush(10); f = cam.read(); cv2.imwrite(str(out / "checker.png"), f)
        if a.solo_checker:
            return 0
        # 2) silueta real con luz blanca y cuadro oscuro de referencia
        win.show(np.full((H, W, 3), 255, np.uint8)); time.sleep(1.8); cam.flush(12); white = cam.read()
        win.show(np.zeros((H, W, 3), np.uint8)); time.sleep(1.5); cam.flush(12); dark = cam.read()
        sil = cam_silhouette(white, roi); rep["silueta_px"] = int(sil.sum())
        cv2.imwrite(str(out / "silueta.png"), (sil * 255).astype(np.uint8)[roi[1]:roi[3], roi[0]:roi[2]])
        # 3) búsqueda de corrimiento
        best = (-1.0, 0, 0); r, s = a.radio, a.paso
        offsets = [(dx, dy) for dy in range(-r, r + 1, s) for dx in range(-r, r + 1, s)]
        offsets.sort(key=lambda o: abs(o[0]) + abs(o[1]))
        for dx, dy in offsets:
            win.show(fill_canvas(cnt, dx, dy)); time.sleep(1.2); cam.flush(8); f = cam.read()
            lit = lit_mask(f, dark, roi); score = iou(lit, sil)
            rep["pruebas"].append({"dx": dx, "dy": dy, "iou": round(score, 4)})
            print(f"dx {dx:+d} dy {dy:+d}  IoU {score:.3f}")
            if score > best[0]:
                best = (score, dx, dy); cv2.imwrite(str(out / "mejor.png"), f)
        rep["mejor"] = {"iou": round(best[0], 4), "dx": best[1], "dy": best[2]}
        print("mejor corrimiento:", rep["mejor"])
        # 4) mostrar el mejor con checker 6 s
        win.show(checker_canvas(cnt, best[1], best[2])); time.sleep(6)
    finally:
        win.close(); cam.close()
    (scan / f"refine_{a.objeto}.json").write_text(json.dumps(rep, indent=1))
    if a.aplicar and (best[1] or best[2]):
        print("nudge runtime:", nudge_runtime(a.objeto, best[1], best[2]))
    if not a.guardar:
        shutil.rmtree(out, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
