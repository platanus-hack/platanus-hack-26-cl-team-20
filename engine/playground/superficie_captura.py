"""Captura con la Osmo del rectángulo proyectado (superficie calibrada a mano) y sus coordenadas.

    python -m playground.superficie_captura captures/dale-XXXX

Proyecta negro (2 s), toma un cuadro; proyecta el rectángulo blanco (las 4 esquinas de superficie.json), toma otro;
la diferencia aísla la luz del proyector (sirve sobre el pad oscuro y la mesa clara por igual). Del blob más grande
sale el cuadrilátero (TL, TR, BR, BL en px de cámara 4K) y la homografía cámara→lienzo (las 4 esquinas de cámara ↔
las 4 del lienzo). Guarda superficie_captura.json, superficie_osmo.png (foto con la luz), superficie_osmo_negro.png,
superficie_feed.json (calibración para playground.superficie_feed) y un debug. Deja el proyector como estaba (overlay
del rectángulo blanco).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from . import pad_checker as pc

W, H = 1920, 1080


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("scan"); p.add_argument("--camera", default="osmo"); p.add_argument("--runtime", default="http://127.0.0.1:8790")
    p.add_argument("--settle", type=float, default=2.2, help="latencia proyector AirPlay + cámara")
    a = p.parse_args(argv)
    scan = Path(a.scan)
    sup = json.loads((scan / "superficie.json").read_text())
    corners = np.array(sup["rect_proj_tl_tr_br_bl"], np.float64)
    lw, lh = (int(v) for v in sup["lienzo_px"])
    white = np.zeros((H, W, 3), np.uint8); cv2.fillPoly(white, [corners.astype(np.int32).reshape(-1, 1, 2)], (255, 255, 255))
    black = np.zeros((H, W, 3), np.uint8)
    pw, pb = scan / "superficie_blanco.png", scan / "superficie_negro.png"
    cv2.imwrite(str(pw), white); cv2.imwrite(str(pb), black)
    if not pc.runtime_alive(a.runtime):
        raise SystemExit("el runtime no está en 8790: no puedo proyectar")
    from slscan.capture import Camera
    cam = Camera(a.camera, size="3840x2160", rotate=0, downscale=1)
    try:
        pc.runtime_cmd(a.runtime, "overlay", path=str(pb.resolve()), seconds=30); time.sleep(a.settle); cam.flush(20); f0 = cam.read()
        pc.runtime_cmd(a.runtime, "overlay", path=str(pw.resolve()), seconds=3600); time.sleep(a.settle); cam.flush(20); f1 = cam.read()
    finally:
        cam.close()
    cv2.imwrite(str(scan / "superficie_osmo_negro.png"), f0); cv2.imwrite(str(scan / "superficie_osmo.png"), f1)
    g0 = cv2.cvtColor(f0, cv2.COLOR_BGR2GRAY).astype(np.float32); g1 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY).astype(np.float32)
    d = g1 - g0
    thr = max(18.0, 0.3 * float(np.percentile(d, 99.5)))
    m = (d > thr).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)); m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((41, 41), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    if n < 2:
        raise SystemExit("la cámara no ve la luz del rectángulo (¿Osmo apuntando a la mesa?)")
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    comp = (lab == i).astype(np.uint8)
    comp = cv2.morphologyEx(comp, cv2.MORPH_CLOSE, np.ones((61, 61), np.uint8))     # cables y sombras finas
    ys, xs = np.nonzero(comp)
    hull = cv2.convexHull(np.stack([xs, ys], 1).astype(np.float32)); peri = cv2.arcLength(hull, True)
    quad = None
    for eps in (0.01, 0.02, 0.03, 0.05, 0.08):
        ap = cv2.approxPolyDP(hull, eps * peri, True)
        if len(ap) == 4:
            quad = ap.reshape(4, 2).astype(np.float64); break
    if quad is None:
        quad = pc.order_quad(hull.reshape(-1, 2))
    quad = pc.order_quad(quad)
    # afinar cada esquina: intersección de las rectas de los lados (bordes del blob) ajustadas por mínimos cuadrados
    cnt = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]
    pts = np.vstack([c.reshape(-1, 2) for c in cnt]).astype(np.float64)
    lines = []
    for k in range(4):
        p0, p1 = quad[k], quad[(k + 1) % 4]
        dvec = p1 - p0; L = np.linalg.norm(dvec); dvec /= L; nv = np.array([-dvec[1], dvec[0]])
        t = (pts - p0) @ dvec; dist = np.abs((pts - p0) @ nv)
        sel = (t > 0.08 * L) & (t < 0.92 * L) & (dist < 12)
        if sel.sum() >= 30:
            c = pts[sel].mean(0); _, _, vt = np.linalg.svd(pts[sel] - c, full_matrices=False); lines.append((c, vt[0]))
        else:
            lines.append((p0, dvec))
    refined = np.array([pc._intersect(lines[(k - 1) % 4], lines[k]) for k in range(4)])
    if np.all(np.isfinite(refined)) and np.linalg.norm(refined - quad, axis=1).max() < 60:
        quad = pc.order_quad(refined)
    # cámara → lienzo
    src = quad.astype(np.float32); dst = np.array([[0, 0], [lw, 0], [lw, lh], [0, lh]], np.float32)
    Hcl = cv2.getPerspectiveTransform(src, dst).astype(np.float64)
    cal = {"H_cam_to_lienzo": Hcl.tolist(), "camera": a.camera, "camera_size": [f1.shape[1], f1.shape[0]], "lienzo_px": [lw, lh],
           "source": "4 esquinas del rectángulo blanco (diferencia negro/blanco)", "calibrado": time.strftime("%Y-%m-%d %H:%M:%S")}
    (scan / "superficie_feed.json").write_text(json.dumps(cal, indent=1))
    out = {"proj_tl_tr_br_bl": corners.round(1).tolist(), "osmo_tl_tr_br_bl": quad.round(1).tolist(), "camera_size": [f1.shape[1], f1.shape[0]],
           "lienzo_px": [lw, lh], "umbral_diff": round(thr, 1), "area_px": int(st[i, cv2.CC_STAT_AREA]), "ajuste_manual_px": sup.get("ajuste_manual_px"),
           "foto": str(scan / "superficie_osmo.png"), "t": cal["calibrado"]}
    (scan / "superficie_captura.json").write_text(json.dumps(out, indent=1))
    dbg = f1.copy(); cv2.polylines(dbg, [quad.astype(np.int32)], True, (0, 255, 0), 4)
    for k, (x, y) in enumerate(quad):
        cv2.circle(dbg, (int(x), int(y)), 16, (0, 255, 255), 3); cv2.putText(dbg, str(k + 1), (int(x) + 20, int(y) - 10), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 255), 3)
    cv2.imwrite(str(scan / "superficie_captura_debug.jpg"), cv2.resize(dbg, (1920, 1080)))
    rect = cv2.warpPerspective(f1, Hcl, (lw, lh)); cv2.imwrite(str(scan / "superficie_rectificada.jpg"), rect)
    print(json.dumps(out), flush=True)
    return 0


def quad_from_pair(f0: np.ndarray, f1: np.ndarray) -> tuple[np.ndarray, float, int]:
    """Cuadrilátero (TL TR BR BL, px de cámara) de la luz proyectada: f1 (rectángulo blanco) − f0 (negro).
    Mismo método que main(), para recalibrar desde el servidor del feed sin cerrar la cámara."""
    g0 = cv2.cvtColor(f0, cv2.COLOR_BGR2GRAY).astype(np.float32); g1 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY).astype(np.float32)
    d = g1 - g0
    thr = max(18.0, 0.3 * float(np.percentile(d, 99.5)))
    m = (d > thr).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)); m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((41, 41), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    if n < 2:
        raise RuntimeError("la cámara no ve la luz del rectángulo")
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    comp = cv2.morphologyEx((lab == i).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((61, 61), np.uint8))
    ys, xs = np.nonzero(comp)
    hull = cv2.convexHull(np.stack([xs, ys], 1).astype(np.float32)); peri = cv2.arcLength(hull, True)
    quad = None
    for eps in (0.01, 0.02, 0.03, 0.05, 0.08):
        ap = cv2.approxPolyDP(hull, eps * peri, True)
        if len(ap) == 4:
            quad = ap.reshape(4, 2).astype(np.float64); break
    quad = pc.order_quad(quad if quad is not None else hull.reshape(-1, 2))
    cnt = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]
    pts = np.vstack([c.reshape(-1, 2) for c in cnt]).astype(np.float64)
    lines = []
    for k in range(4):
        p0, p1 = quad[k], quad[(k + 1) % 4]
        dvec = p1 - p0; L = np.linalg.norm(dvec); dvec /= max(L, 1e-6); nv = np.array([-dvec[1], dvec[0]])
        t = (pts - p0) @ dvec; dist = np.abs((pts - p0) @ nv)
        sel = (t > 0.08 * L) & (t < 0.92 * L) & (dist < 12)
        if sel.sum() >= 30:
            c = pts[sel].mean(0); _, _, vt = np.linalg.svd(pts[sel] - c, full_matrices=False); lines.append((c, vt[0]))
        else:
            lines.append((p0, dvec))
    refined = np.array([pc._intersect(lines[(k - 1) % 4], lines[k]) for k in range(4)])
    if np.all(np.isfinite(refined)) and np.linalg.norm(refined - quad, axis=1).max() < 60:
        quad = pc.order_quad(refined)
    return quad, float(thr), int(st[i, cv2.CC_STAT_AREA])


if __name__ == "__main__":
    sys.exit(main())
