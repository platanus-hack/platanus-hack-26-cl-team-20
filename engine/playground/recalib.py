"""Recalibración entera en un comando, para cuando Max mueve el proyector o la cámara.

    python -m playground.recalib --scan captures/scan3            # todo
    python -m playground.recalib --scan captures/scan3 --skip-scan  # solo mapa, verificación y segmentación

Pasos: 1) pantallas (si el proyector volvió en espejo lo pone extendido con displayplacer), 2) Osmo presente,
3) patrón de prueba + foto blanca, 4) gray code (bloques de 4 px), 5) mapa proyector→cámara denso y POV,
6) verificación con ChArUco proyectado y corrección global, 7) altura por paralaje, 8) segmentación,
9) banco de trabajo proyectado (resumen). Deja todo en <scan>/ y un reporte en <scan>/recalib.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

W, H = 1920, 1080
PY = sys.executable


def ensure_extended() -> dict:
    """El T749 tiene que ser un display propio a 1920x1080 (no espejo). Se usa displayplacer con los ids conocidos."""
    out = subprocess.run(["system_profiler", "SPDisplaysDataType"], capture_output=True, text=True).stdout
    # solo el bloque del T749 (hasta el siguiente display); el espejo Mac+Sidecar no se toca
    import re
    blk = out.split("T749-fHD720")[1] if "T749" in out else ""
    blk = re.split(r"\n {8}\S[^\n]*:\n", blk)[0]
    mirrored = "Mirror: On" in blk
    if mirrored:
        cmd = ['displayplacer', 'id:37D8832A-2D66-02CA-B9F7-8F30A301B230 res:1470x956 hz:60 color_depth:8 scaling:on origin:(0,0) degree:0',
               'id:5A3AB89D-384B-4C72-B15A-B633C4481E14 res:1920x1080 hz:60 color_depth:8 scaling:off origin:(1470,0) degree:0']
        subprocess.run(cmd, capture_output=True, text=True); time.sleep(3)
    from slscan.capture import list_displays, is_reserved
    disp = list_displays()
    proj = [d for d in disp if d["w"] == W and d["h"] == H and not is_reserved(d)]
    return {"mirrored_before": mirrored, "displays": disp, "projector_index": proj[0]["index"] if proj else None}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scan", default="captures/scan3"); p.add_argument("--skip-scan", action="store_true")
    p.add_argument("--settle", type=float, default=1.2); p.add_argument("--skip-bits", type=int, default=2)
    p.add_argument("--rotate", type=int, default=270); p.add_argument("--cam-size", default="3840x2160"); p.add_argument("--downscale", type=int, default=2); p.add_argument("--no-workbench", action="store_true")
    a = p.parse_args(argv)
    scan = Path(a.scan); scan.mkdir(parents=True, exist_ok=True)
    rep = {"started": time.strftime("%Y-%m-%d %H:%M:%S")}
    # 1-2. pantallas y cámara
    rep["displays"] = ensure_extended()
    if rep["displays"]["projector_index"] is None:
        print("no hay proyector 1920x1080 extendido:", rep["displays"]); return 1
    from slscan.capture import resolve_camera
    try:
        rep["osmo_index"] = resolve_camera("osmo")
    except RuntimeError as e:
        print(e); return 1
    print("proyector índice", rep["displays"]["projector_index"], "| Osmo índice", rep["osmo_index"])
    print("capturas por paso: <scan>/osmo/*.png (gray code), debug_charuco_*.png, wb_*.png (banco de trabajo)")
    # 3-4. escaneo
    if not a.skip_scan:
        r = subprocess.run([PY, "-m", "slscan", "scan", "--camera", "osmo", "--cam-size", a.cam_size, "--downscale", str(a.downscale), "--rotate", str(a.rotate), "--name", "osmo",
                            "--screen", "1920x1080", "--size", "1920x1080", "--settle", str(a.settle), "--skip-bits", str(a.skip_bits),
                            "--out", str(scan), "--name-out", scan.name], capture_output=True, text=True)
        line = [l for l in r.stdout.splitlines() if "decodificados" in l]; rep["scan"] = line[-1] if line else r.stderr[-300:]
        print("escaneo:", rep["scan"])
    # 5. mapa denso y POV
    from slscan.graycode import Decoded
    from slscan import recon
    dec = Decoded.load(scan / "osmo" / "map.npz"); color = cv2.imread(str(scan / "osmo" / "color.png"))
    mapx, mapy, valid = recon.dense_proj_to_cam(dec, W, H)       # con relleno de agujeros chicos
    rep["correspondencias"] = int(len(recon.coalesce(dec).pro)); rep["pov_valido"] = round(float(valid.mean()) * 100, 1)
    # 6. verificación con ChArUco proyectado + corrección global
    from slscan.calib import CharucoBoard
    from slscan.capture import PatternWindow, Camera
    # marcadores chicos repartidos sobre fondo negro (nada de tablero gigante en la escena): 2 s, una sola vez
    dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50); size = 64; n_x, n_y = 8, 5
    canvas = np.zeros((H, W), np.uint8); mk = {}; k = 0
    for j in range(n_y):
        for i in range(n_x):
            x0 = int(90 + i * (W - 180 - size) / (n_x - 1)); y0 = int(70 + j * (H - 140 - size) / (n_y - 1))
            canvas[y0 - 8:y0 + size + 8, x0 - 8:x0 + size + 8] = 255                     # zona blanca mínima
            canvas[y0:y0 + size, x0:x0 + size] = cv2.aruco.generateImageMarker(dic, k, size)
            mk[k] = np.array([[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size]], np.float64); k += 1
    det = cv2.aruco.ArucoDetector(dic)
    win = PatternWindow((W, H), screen="1920x1080"); cam = Camera("osmo", size=a.cam_size, rotate=a.rotate, downscale=a.downscale)
    try:
        for it in range(2):
            win.show(canvas); time.sleep(2.0 if it == 0 else 1.2); cam.flush(20); f = cam.read()
            gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            corners, ids, _ = det.detectMarkers(gray)
            ids = [] if ids is None else [int(i) for i in ids.ravel()]
            info = {"marcadores": len(ids)}
            dbg = f.copy()
            if ids:
                cv2.aruco.drawDetectedMarkers(dbg, corners)
            cv2.imwrite(str(scan / f"debug_aruco_{it}.png"), dbg)      # captura por paso, para depurar
            if len(ids) >= 2:
                pp = np.concatenate([mk[i] for i in ids if i in mk]); pi = np.concatenate([c.reshape(4, 2) for c, i in zip(corners, ids) if i in mk])
                xi = np.clip(pp[:, 0].astype(int), 0, W - 1); yi = np.clip(pp[:, 1].astype(int), 0, H - 1); ok = valid[yi, xi]
                pr = np.column_stack([mapx[yi, xi], mapy[yi, xi]])
                e = (pi - pr)[ok]; nrm = np.linalg.norm(e, axis=1); good = nrm < 12
                info["esquinas"] = int(ok.sum()); info["mediana_px"] = round(float(np.median(nrm)), 2) if len(nrm) else None
                info["bajo_3px"] = int((nrm < 3).sum())
                if it == 0 and good.sum() >= 4:
                    shift = np.median(e[good], axis=0); mapx[valid] += shift[0]; mapy[valid] += shift[1]; info["correccion"] = shift.round(2).tolist()
            rep[f"verificacion_{it}"] = info; print("verificación", it, info)
    finally:
        win.close(); cam.close()
    np.savez_compressed(scan / "proj_to_cam.npz", mapx=mapx, mapy=mapy, valid=valid)
    pov = cv2.remap(color, mapx, mapy, cv2.INTER_LINEAR); pov[~valid] = 0; cv2.imwrite(str(scan / "scene_pov.png"), pov)
    # 7-8. altura y segmentación (segment_pov calcula height_proj.npy)
    r = subprocess.run([PY, "-m", "playground.segment_pov", str(scan)], capture_output=True, text=True)
    rep["segmentacion"] = r.stdout.strip()[-600:]; print("segmentación:", rep["segmentacion"][:300])
    # 9. banco de trabajo proyectado
    if not a.no_workbench:
        env = {"SLSCAN_SCAN": str(scan)}
        r = subprocess.run([PY, "-m", "playground.workbench", "--pace", "0.7"], capture_output=True, text=True, env={**__import__('os').environ, **env})
        rep["workbench"] = r.stdout.strip()[-400:]
    rep["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    json.dump(rep, open(scan / "recalib.json", "w"), indent=1, default=str)
    print("recalibración lista:", scan / "recalib.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
