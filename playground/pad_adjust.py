"""Ajuste con teclas de las 4 esquinas del mousepad en píxeles del proyector (pedido de Max, 2-nov).

    python -m playground.pad_adjust captures/dale-XXXX [--corner 3]

Proyecta el contorno del cuadrilátero (verde) por el runtime (overlay) o en una ventana propia; en el monitor de Max
abre una ventanita con la vista de la Osmo y el cuadrilátero. Teclas (con la ventanita enfocada):
  1 2 3 4  elegir esquina (TL, TR, BR, BL)        flechas / h j k l  mover 2 px      H J K L  mover 10 px
  s guardar       r volver al inicial       q guardar y salir
Guarda en <scan>/pad_quad.json: quad_proj_adjusted, ajuste_px por esquina y rehace pad_checker.png con el damero.
El cuadrilátero inicial sale de las rectas del borde rojo (dos horizontales más separadas, derecha, e izquierda por
los extremos) y de la homografía cámara→proyector del plano del pad.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import pad_checker as pc

W, H = 1920, 1080
NAMES = ["TL", "TR", "BR", "BL"]


def initial_quad(color: np.ndarray, roi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Como pad_checker.red_border_quad, pero tomando las DOS horizontales más separadas (arriba y abajo)."""
    hsv = cv2.cvtColor(color, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    b, g, r = (color[..., i].astype(np.int32) for i in range(3))
    red = ((h <= 10) | (h >= 165)) & (s >= 45) & (v >= 35) & (r > g + 25) & (r > b + 25)
    m = (red.astype(np.uint8) * 255) & roi
    fat = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)))
    fat = cv2.dilate(fat, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))
    thin = cv2.morphologyEx(m & ~fat, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(thin)
    keep = np.zeros_like(thin)
    for i in range(1, n):
        if max(st[i, cv2.CC_STAT_WIDTH], st[i, cv2.CC_STAT_HEIGHT]) >= 60:
            keep[lab == i] = 255
    ys, xs = np.nonzero(keep)
    pts = np.stack([xs, ys], 1).astype(np.float64)
    rng = np.random.default_rng(0)
    if len(pts) > 40000:
        pts = pts[rng.choice(len(pts), 40000, replace=False)]
    raw = pc._ransac_lines(pts, rng=rng)
    ref = np.array([color.shape[1] / 2, color.shape[0] / 2])
    lines = []
    for c, d, nin in raw:
        ang = math.degrees(math.atan2(d[1], d[0])) % 180
        if any(min(abs(ang - a2), 180 - abs(ang - a2)) < 8 and abs(pc._line_offset(c, d, ref) - pc._line_offset(c2, d2, ref)) < 45
               for c2, d2, _, a2 in lines):
            continue
        lines.append((c, d, nin, ang))
    strongest = lines[0][3]
    is_h = lambda a: min(abs(a - strongest), 180 - abs(a - strongest)) < 35
    # los bordes reales son las rectas con más inliers (las otras son brazos, cables o el otro lado del borde)
    hor = sorted([l for l in lines if is_h(l[3])], key=lambda l: -l[2])[:2]
    ver = sorted([l for l in lines if not is_h(l[3])], key=lambda l: -l[2])[:2]
    ver = sorted(ver, key=lambda l: l[0][0])
    if len(hor) < 2:
        raise RuntimeError(f"solo {len(hor)} borde(s) horizontal(es): {[(round(l[3], 1), l[2]) for l in lines]}")
    top, bot = sorted(hor, key=lambda l: l[0][1])

    def ends(line, side):
        c, d = line[0], line[1]
        t = (pts - c) @ d; nv = np.array([-d[1], d[0]])
        tt = t[np.abs((pts - c) @ nv) < 6]
        return c + d * (tt.min() if side == "left" else tt.max())

    mid_x = (top[0][0] + bot[0][0]) / 2
    rights = [l for l in ver if l[0][0] > mid_x]; lefts = [l for l in ver if l[0][0] <= mid_x]
    if rights:
        right = rights[-1]
    else:
        a, bq = ends(top, "right"), ends(bot, "right"); right = (a, (bq - a) / np.linalg.norm(bq - a), 0, 0.0)
    if lefts:
        left = lefts[0]
    else:
        a, bq = ends(top, "left"), ends(bot, "left"); left = (a, (bq - a) / np.linalg.norm(bq - a), 0, 0.0)
    quad = np.array([pc._intersect(top, left), pc._intersect(top, right), pc._intersect(bot, right), pc._intersect(bot, left)])
    return pc.order_quad(quad), keep


def proj_image(quad: np.ndarray, sel: int, note: str = "") -> np.ndarray:
    img = np.zeros((H, W, 3), np.uint8)
    cv2.polylines(img, [quad.astype(np.int32).reshape(-1, 1, 2)], True, (0, 255, 0), 3, cv2.LINE_AA)
    for i, (x, y) in enumerate(quad):
        col = (0, 255, 255) if i == sel else (0, 200, 0)
        cv2.circle(img, (int(x), int(y)), 14 if i == sel else 9, col, 2, cv2.LINE_AA)
        cv2.putText(img, str(i + 1), (int(x) + 16, int(y) - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.9, col, 2, cv2.LINE_AA)
    return img


class OsmoView:
    """Último cuadro de la Osmo (hilo), reducido para la ventanita."""

    def __init__(self, cam, scale: float):
        self.cam, self.scale = cam, scale
        self.frame = None
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop.is_set():
            try:
                f = self.cam.read()
                self.frame = cv2.resize(f, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
            except Exception:  # noqa: BLE001
                time.sleep(0.3)

    def close(self):
        self._stop.set(); time.sleep(0.2)
        try:
            self.cam.close()
        except Exception:  # noqa: BLE001
            pass


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("scan"); p.add_argument("--camera", default="osmo"); p.add_argument("--corner", type=int, default=3)
    p.add_argument("--runtime", default="http://127.0.0.1:8790"); p.add_argument("--no-osmo", action="store_true")
    p.add_argument("--from-json", action="store_true", help="partir del quad_proj (ajustado si hay) de pad_quad.json")
    a = p.parse_args(argv)
    scan = Path(a.scan)
    from slscan.graycode import Decoded
    dec = Decoded.load(scan / a.camera / "map.npz")
    color = cv2.imread(str(scan / a.camera / "color.png"))
    cam_size = (color.shape[1], color.shape[0])
    jpath = scan / "pad_quad.json"
    data = json.loads(jpath.read_text()) if jpath.exists() else {}
    if a.from_json and data.get("quad_proj_tl_tr_br_bl"):
        quad_proj0 = np.array(data.get("quad_proj_adjusted") or data["quad_proj_tl_tr_br_bl"], np.float64)
        Hcp = np.array(data["H_cam_to_proj"], np.float64); quad_cam = np.array(data["quad_cam_tl_tr_br_bl"], np.float64)
        stats = data.get("H_cam_to_proj_stats", {})
    else:
        valid = dec.valid.astype(np.uint8) * 255
        # el pad sigue fuera de la luz (lado izquierdo): ROI amplia alrededor de la zona proyectada
        roi = cv2.dilate(valid, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (401, 401)))
        quad_cam, keep = initial_quad(color, roi)
        pad_mask = np.zeros(color.shape[:2], np.uint8); cv2.fillPoly(pad_mask, [quad_cam.astype(np.int32)], 255)
        Hcp, stats = pc.cam_to_proj_homography(dec, pad_mask)
        quad_proj0 = cv2.perspectiveTransform(quad_cam.reshape(-1, 1, 2), Hcp).reshape(4, 2)
        dbg = color.copy(); dbg[keep > 0] = (255, 0, 255)
        cv2.polylines(dbg, [quad_cam.astype(np.int32)], True, (0, 255, 0), 4)
        cv2.imwrite(str(scan / "pad_debug.png"), cv2.resize(dbg, (cam_size[0] // 2, cam_size[1] // 2), interpolation=cv2.INTER_AREA))
    print(json.dumps({"quad_cam": quad_cam.round(1).tolist(), "quad_proj_inicial": quad_proj0.round(1).tolist(), "H_stats": stats}), flush=True)
    quad = quad_proj0.copy(); sel = int(np.clip(a.corner - 1, 0, 3))
    Hpc = np.linalg.inv(Hcp)
    # salida al proyector
    use_rt = pc.runtime_alive(a.runtime)
    win = None
    if not use_rt:
        from slscan.capture import PatternWindow
        win = PatternWindow((W, H), screen="1920x1080", name="pad_adjust")
    tmp = scan / "pad_adjust.png"

    def show():
        img = proj_image(quad, sel)
        if use_rt:
            cv2.imwrite(str(tmp), img)
            try:
                pc.runtime_cmd(a.runtime, "overlay", path=str(tmp.resolve()), seconds=3600)
            except Exception as e:  # noqa: BLE001
                print("overlay falló:", e, flush=True)
        else:
            win.show(img)

    def save():
        adj = (quad - quad_proj0).round(2).tolist()
        data.update({"quad_cam_tl_tr_br_bl": quad_cam.round(1).tolist(), "quad_proj_tl_tr_br_bl": quad_proj0.round(1).tolist(),
                     "quad_proj_adjusted": quad.round(1).tolist(), "ajuste_px": {NAMES[i]: adj[i] for i in range(4)},
                     "quad_cam_adjusted": cv2.perspectiveTransform(quad.reshape(-1, 1, 2), Hpc).reshape(4, 2).round(1).tolist(),
                     "H_cam_to_proj": Hcp.tolist(), "H_cam_to_proj_stats": stats, "camera_size": list(cam_size),
                     "ajustado": time.strftime("%Y-%m-%d %H:%M:%S")})
        aspect = data.get("aspect_w_over_h") or pc.aspect_from_view(np.array(data["quad_cam_adjusted"]), cam_size, 84.0)
        aspect = float(np.clip(aspect, 0.5, 5.0)); data["aspect_w_over_h"] = round(aspect, 4)
        chk, (cw, ch) = pc.checker_image(aspect, int(data.get("squares_v", 6)), float(data.get("margin", 0.06)))
        src = np.array([[0, 0], [cw, 0], [cw, ch], [0, ch]], np.float32)
        Hpp = cv2.getPerspectiveTransform(src, quad.astype(np.float32))
        cv2.imwrite(str(scan / "pad_checker.png"), cv2.warpPerspective(chk, Hpp, (W, H), flags=cv2.INTER_AREA))
        data["H_pad_to_proj"] = Hpp.tolist(); data["checker"] = str(scan / "pad_checker.png")
        jpath.write_text(json.dumps(data, indent=1))
        print(json.dumps({"guardado": str(jpath), "ajuste_px": data["ajuste_px"], "aspect": data["aspect_w_over_h"]}), flush=True)

    # ventanita en el monitor de Max con la Osmo
    view = None
    if not a.no_osmo:
        try:
            from slscan.capture import Camera
            view = OsmoView(Camera(a.camera, size=f"{cam_size[0]}x{cam_size[1]}", rotate=0, downscale=1), 0.25)
        except Exception as e:  # noqa: BLE001
            print("sin Osmo para la vista previa:", e, flush=True)
    name = "pad_adjust: 1-4 esquina · flechas/hjkl 2px · HJKL 10px · s guardar · q salir"
    cv2.namedWindow(name, cv2.WINDOW_AUTOSIZE)
    cv2.moveWindow(name, 80, 80)
    show()
    steps = {"h": (-2, 0), "l": (2, 0), "k": (0, -2), "j": (0, 2), "H": (-10, 0), "L": (10, 0), "K": (0, -10), "J": (0, 10)}
    arrows = {63234: (-2, 0), 63235: (2, 0), 63232: (0, -2), 63233: (0, 2), 2: (-2, 0), 3: (2, 0), 0: (0, -2), 1: (0, 2)}
    try:
        while True:
            canvas = np.zeros((560, 960, 3), np.uint8)
            if view is not None and view.frame is not None:
                f = view.frame.copy()
                qc = cv2.perspectiveTransform(quad.reshape(-1, 1, 2), Hpc).reshape(4, 2) * 0.25
                cv2.polylines(f, [qc.astype(np.int32)], True, (0, 255, 0), 2)
                for i, (x, y) in enumerate(qc):
                    cv2.circle(f, (int(x), int(y)), 7 if i == sel else 4, (0, 255, 255) if i == sel else (0, 200, 0), 2)
                canvas[:f.shape[0], :f.shape[1]] = f[:560, :960]
            d = quad[sel] - quad_proj0[sel]
            cv2.putText(canvas, f"esquina {sel + 1} {NAMES[sel]}  ajuste {d[0]:+.0f},{d[1]:+.0f} px   (s guarda, q sale)", (12, 548),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.imshow(name, canvas)
            k = cv2.waitKeyEx(50)
            if k == -1:
                continue
            ch = chr(k) if 0 <= k < 256 else ""
            if ch and ch in "1234":
                sel = int(ch) - 1; show()
            elif (ch and ch in steps) or k in arrows:
                dx, dy = steps.get(ch) or arrows[k]
                quad[sel] += (dx, dy); show()
            elif ch == "r":
                quad = quad_proj0.copy(); show()
            elif ch == "s":
                save()
            elif ch == "q" or k == 27:
                save(); break
    finally:
        cv2.destroyAllWindows()
        if view is not None:
            view.close()
        if use_rt:
            try:
                pc.runtime_cmd(a.runtime, "overlay", path=str((scan / "pad_checker.png").resolve()), seconds=3600)
            except Exception:  # noqa: BLE001
                pass
        if win is not None:
            win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
