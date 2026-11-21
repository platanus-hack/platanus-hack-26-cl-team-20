"""Re-segmentar el cocodrilo (u otro objeto por tono) desde una foto NUEVA de la Osmo, sin re-escanear: para cuando lo
mueven un poco. Usa el mapa proyector→cámara del escaneo (compensando la deriva de la cámara) y deja el contorno nuevo en
workbench.json / segments.json; el runtime lo recarga (reload_scene) y se verifica proyectando el relleno.

    python -m playground.croc_resegment --scan captures/scan5            # Osmo + runtime (overlay)
    python -m playground.croc_resegment --scan captures/scan5 --sin-aplicar

Pasos: 1) luz blanca por overlay y captura; 2) deriva de cámara contra color.png (zona del teclado, estática);
3) silueta por tono en la cámara dentro del ROI del contorno viejo (ampliado); 4) silueta → proyector muestreando el
mapa; 5) contorno limpio; 6) guardar, recargar runtime; 7) verificación: relleno proyectado vs silueta (IoU).
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

import cv2
import numpy as np

from playground.croc_refine import RuntimeWindow, cam_silhouette, lit_mask, iou, fill_canvas, checker_canvas

W, H = 1920, 1080


def camera_drift(ref: np.ndarray, live: np.ndarray, box: tuple[int, int, int, int]) -> tuple[int, int, float]:
    """(dx, dy, score) de la cámara entre la foto del escaneo y la de ahora, por plantilla en una zona estática."""
    x0, y0, x1, y1 = box
    tpl = cv2.cvtColor(ref[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY); m = 40
    win = cv2.cvtColor(live[max(0, y0 - m):y1 + m, max(0, x0 - m):x1 + m], cv2.COLOR_BGR2GRAY)
    r = cv2.matchTemplate(cv2.equalizeHist(win), cv2.equalizeHist(tpl), cv2.TM_CCOEFF_NORMED)
    _, sc, _, loc = cv2.minMaxLoc(r)
    return loc[0] - min(m, x0), loc[1] - min(m, y0), float(sc)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scan", default="captures/scan5"); p.add_argument("--objeto", default="cocodrilo")
    p.add_argument("--margen", type=int, default=90); p.add_argument("--sin-aplicar", action="store_true")
    p.add_argument("--guardar", action="store_true"); p.add_argument("--rotate", type=int, default=270)
    a = p.parse_args(argv)
    scan = Path(a.scan); out = Path("reports/_crit"); out.mkdir(parents=True, exist_ok=True)
    seg = json.loads((scan / "segments.json").read_text()); wb = json.loads((scan / "workbench.json").read_text())
    old = (wb.get(a.objeto) or seg.get(a.objeto)); old_cnt = np.array(old["contour"], np.float32)
    z = np.load(scan / "proj_to_cam.npz"); mapx, mapy, valid = z["mapx"], z["mapy"], z["valid"]
    ref = cv2.imread(str(scan / "osmo" / "color.png"))
    from slscan.capture import PatternWindow, Camera
    win = RuntimeWindow() if RuntimeWindow.alive() else PatternWindow((W, H), screen="1920x1080")
    cam = Camera("osmo", size="3840x2160", rotate=a.rotate, downscale=2)
    rep = {"objeto": a.objeto, "scan": str(scan)}
    try:
        # 1) luz blanca
        win.show(np.full((H, W, 3), 255, np.uint8)); time.sleep(2.0); cam.flush(12); white = cam.read()
        win.show(np.zeros((H, W, 3), np.uint8)); time.sleep(1.3); cam.flush(10); dark = cam.read()
        if white.shape != ref.shape:          # cuadro raro de la Osmo (cambia de modo a veces): al tamaño de la referencia
            white = cv2.resize(white, (ref.shape[1], ref.shape[0])); dark = cv2.resize(dark, (ref.shape[1], ref.shape[0]))
        cv2.imwrite(str(out / "white_now.png"), white)
        if white.mean() < 45 or white.mean() - dark.mean() < 15:
            print(f"la proyección blanca no se ve en la cámara (media {white.mean():.0f} vs negro {dark.mean():.0f}): no se toca nada"); return 1
        # 2) deriva de cámara en la zona del teclado (estática)
        tb = seg["teclado"]["bbox"]; tx = np.clip(np.array([tb[0], tb[0] + tb[2]]), 0, W - 1); ty = np.clip(np.array([tb[1], tb[1] + tb[3]]), 0, H - 1)
        cxs = [mapx[y, x] for y in ty for x in tx if valid[y, x]]; cys = [mapy[y, x] for y in ty for x in tx if valid[y, x]]
        box = (int(min(cxs)), int(min(cys)), int(max(cxs)), int(max(cys)))
        ddx, ddy, sc = camera_drift(ref, white, box); rep["deriva_cam"] = [ddx, ddy, round(sc, 3)]
        print(f"deriva de cámara: dx {ddx} dy {ddy} (score {sc:.2f})")
        # 3) silueta en cámara, ROI = contorno viejo mapeado ± margen (+ deriva)
        ci = np.round(old_cnt).astype(int); xs = np.clip(ci[:, 0], 0, W - 1); ys = np.clip(ci[:, 1], 0, H - 1); ok = valid[ys, xs]
        cx, cy = mapx[ys[ok], xs[ok]] + ddx, mapy[ys[ok], xs[ok]] + ddy
        hh, ww = white.shape[:2]; m = a.margen
        roi = (int(max(0, cx.min() - m)), int(max(0, cy.min() - m)), int(min(ww, cx.max() + m)), int(min(hh, cy.max() + m)))
        sil = cam_silhouette(white, roi); rep["roi_cam"] = roi
        # fuera los otros objetos (el verde de las hojas del Buda se colaba): se tapan en la cámara sus polígonos
        for name in ("buda", "papel", "torre"):
            o = wb.get(name) or seg.get(name)
            if not o:
                continue
            poly = o.get("quad") or o.get("contour") or [[o["bbox"][0], o["bbox"][1]], [o["bbox"][0] + o["bbox"][2], o["bbox"][1]],
                                                         [o["bbox"][0] + o["bbox"][2], o["bbox"][1] + o["bbox"][3]], [o["bbox"][0], o["bbox"][1] + o["bbox"][3]]]
            pi = np.round(np.array(poly)).astype(int); px_ = np.clip(pi[:, 0], 0, W - 1); py_ = np.clip(pi[:, 1], 0, H - 1); okp = valid[py_, px_]
            if okp.sum() >= 3:
                cpoly = np.column_stack([mapx[py_[okp], px_[okp]] + ddx, mapy[py_[okp], px_[okp]] + ddy]).astype(np.int32)
                excl = np.zeros(sil.shape, np.uint8); cv2.fillPoly(excl, [cpoly.reshape(-1, 1, 2)], 1); sil &= excl == 0
        # y de lo que queda, la componente que más se superpone con el cocodrilo viejo (no la más grande)
        oldmask = np.zeros(sil.shape, np.uint8); cv2.fillPoly(oldmask, [np.column_stack([cx, cy]).astype(np.int32).reshape(-1, 1, 2)], 1)
        n0, lab0, st0, _ = cv2.connectedComponentsWithStats(sil.astype(np.uint8))
        if n0 > 1:
            best = max(range(1, n0), key=lambda i: int(((lab0 == i) & (oldmask > 0)).sum()) + 0.001 * st0[i, 4]); sil = lab0 == best
        rep["silueta_px"] = int(sil.sum())
        cv2.imwrite(str(out / "silueta_cam.png"), (sil * 255).astype(np.uint8))
        # 4) silueta → proyector: cada px del proyector mira a dónde cae en la cámara (con deriva)
        sx = np.clip(np.round(mapx + ddx).astype(int), 0, ww - 1); sy = np.clip(np.round(mapy + ddy).astype(int), 0, hh - 1)
        pm = sil[sy, sx] & valid
        pm = cv2.morphologyEx(pm.astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
        pm = cv2.morphologyEx(pm, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
        n, lab, st, _ = cv2.connectedComponentsWithStats(pm)
        if n <= 1:
            print("no se encontró el objeto en la cámara"); return 1
        i = 1 + int(np.argmax(st[1:, 4])); mask = (lab == i).astype(np.uint8)
        cnt = max(cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
        cnt = cv2.approxPolyDP(cnt, 2.0, True).reshape(-1, 2).astype(float)
        x, y, w, h = cv2.boundingRect(cnt.astype(np.int32)); rep["bbox_nuevo"] = [x, y, w, h]; rep["bbox_viejo"] = old.get("bbox")
        print("contorno nuevo:", len(cnt), "puntos, bbox", [x, y, w, h], "| viejo", old.get("bbox"))
        # 7) verificación: relleno nuevo proyectado vs silueta
        win.show(fill_canvas(cnt.astype(np.float32))); time.sleep(1.5); cam.flush(8); f = cam.read()
        new_iou = iou(lit_mask(f, dark, roi), sil)
        win.show(fill_canvas(old_cnt)); time.sleep(1.5); cam.flush(8); f2 = cam.read()
        old_iou = iou(lit_mask(f2, dark, roi), sil)
        rep["iou_nuevo"], rep["iou_viejo"] = round(new_iou, 3), round(old_iou, 3)
        print(f"IoU relleno vs silueta: viejo {old_iou:.3f} → nuevo {new_iou:.3f}")
        cv2.imwrite(str(out / "fill_nuevo.png"), f)
        if not a.sin_aplicar and new_iou > max(old_iou, 0.45) and rep["silueta_px"] > 3000:   # solo si mejora de verdad
            entry = {"bbox": [int(x), int(y), int(w), int(h)], "contour": [[float(px), float(py)] for px, py in cnt],
                     "fuente": f"croc_resegment {time.strftime('%d-%b %H:%M')} desde foto nueva de la Osmo (IoU {new_iou:.2f})"}
            wb[a.objeto] = entry; (scan / "workbench.json").write_text(json.dumps(wb, indent=1))
            seg[a.objeto] = {**seg.get(a.objeto, {}), "bbox": entry["bbox"], "contour": entry["contour"]}
            (scan / "segments.json").write_text(json.dumps(seg, indent=1))
            if isinstance(win, RuntimeWindow):
                import urllib.request
                body = json.dumps({"cmd": "reload_scene", "args": {}}).encode()
                urllib.request.urlopen(urllib.request.Request(win.url + "/api/command", body, {"Content-Type": "application/json"}), timeout=5).read()
            print("aplicado: workbench.json, segments.json y runtime recargado")
            win.show(checker_canvas(cnt.astype(np.float32)), 8) if isinstance(win, RuntimeWindow) else win.show(checker_canvas(cnt.astype(np.float32)))
            time.sleep(6)
    finally:
        win.close(); cam.close()
    (scan / f"resegment_{a.objeto}.json").write_text(json.dumps(rep, indent=1))
    if not a.guardar:
        shutil.rmtree(out, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
