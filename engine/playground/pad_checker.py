"""Damero sin distorsión dentro del mousepad (borde rojo), como pedido de Max el 2-nov.

    python -m playground.pad_checker captures/dale-XXXX [--show] [--seconds 600] [--aspect 2.25 | --pad-mm 900x400]

1. Detecta el borde rojo del mousepad en la foto de la Osmo del escaneo (<scan>/osmo/color.png, 4K) y saca sus 4
   esquinas (TL, TR, BR, BL en px de cámara).
2. Lleva esas esquinas a píxeles del proyector con el gray code del escaneo (<scan>/osmo/map.npz): homografía
   cámara→proyector ajustada por RANSAC solo con las correspondencias que caen dentro del pad (su plano).
3. Proporción real del pad: con --pad-mm o --aspect se usa esa; si no, se estima con metrología de una vista
   (K aproximada de la Osmo por su FOV horizontal, --hfov) a partir del cuadrilátero en la cámara.
4. Dibuja un damero de cuadrados en el plano del pad (margen --margin) y lo pre-deforma con la homografía
   pad→proyector: proyectado, cae dentro del borde rojo y con cuadrados de verdad.
5. --show: lo proyecta por el runtime (comando overlay, si está vivo en 8790) o abriendo la ventana de patrones
   en el proyector (SLSCAN_SCREEN); luego, si la Osmo está libre, guarda una foto de verificación.

Salidas en <scan>/: pad_quad.json (esquinas, homografías, proporción), pad_checker.png (1920x1080 para el proyector),
pad_debug.png (foto con el borde y las esquinas detectadas), pad_checker_osmo.png (verificación).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

W, H = 1920, 1080


def order_quad(pts: np.ndarray) -> np.ndarray:
    """TL, TR, BR, BL por suma/diferencia de coordenadas (sirve para cuadriláteros convexos poco rotados)."""
    pts = np.asarray(pts, np.float64).reshape(-1, 2)
    s, d = pts.sum(1), pts[:, 0] - pts[:, 1]
    return np.array([pts[s.argmin()], pts[d.argmax()], pts[s.argmax()], pts[d.argmin()]], np.float64)


def _ransac_lines(pts: np.ndarray, max_lines: int = 8, min_inliers: int = 150, tol: float = 4.0, iters: int = 700,
                  rng=None) -> list[tuple[np.ndarray, np.ndarray, int]]:
    """Rectas (centro, dirección unitaria, inliers) por RANSAC secuencial sobre puntos (n,2)."""
    rng = rng or np.random.default_rng(0)
    rest = pts.copy(); out = []
    while len(rest) >= min_inliers and len(out) < max_lines:
        best_in = None
        for _ in range(iters):
            i, j = rng.choice(len(rest), 2, replace=False)
            p, q = rest[i], rest[j]
            d = q - p; nrm = float(np.linalg.norm(d))
            if nrm < 150:
                continue
            nv = np.array([-d[1], d[0]]) / nrm
            inl = np.abs((rest - p) @ nv) < tol
            if best_in is None or inl.sum() > best_in.sum():
                best_in = inl
        if best_in is None or best_in.sum() < min_inliers:
            break
        P = rest[best_in]
        c = P.mean(0); _, _, vt = np.linalg.svd(P - c, full_matrices=False)
        out.append((c, vt[0], int(best_in.sum())))
        rest = rest[~best_in]
    return out


def _line_offset(c: np.ndarray, d: np.ndarray, ref: np.ndarray) -> float:
    nv = np.array([-d[1], d[0]])
    return float((c - ref) @ nv)


def _intersect(l1, l2) -> np.ndarray:
    c1, d1 = l1[0], l1[1]; c2, d2 = l2[0], l2[1]
    A = np.array([d1, -d2]).T
    t = np.linalg.solve(A, c2 - c1)
    return c1 + d1 * t[0]


def red_border_quad(color: np.ndarray, roi: np.ndarray | None, min_area: int = 2000) -> tuple[np.ndarray, np.ndarray]:
    """4 esquinas del pad por sus 4 rectas de borde rojo. Máscara roja laxa (la luz del proyector baja la saturación)
    menos la piel (manchas gordas: apertura grande), solo trozos alargados; rectas por RANSAC; se deduplican (el borde
    tiene dos lados), se separan en dos 'horizontales' y dos 'verticales' y se cortan. Si falta una vertical (el lado
    izquierdo queda fuera de la luz), se usa el extremo de las horizontales de ese lado."""
    hsv = cv2.cvtColor(color, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    b, g, r = (color[..., i].astype(np.int32) for i in range(3))
    red = ((h <= 10) | (h >= 165)) & (s >= 45) & (v >= 35) & (r > g + 25) & (r > b + 25)
    m = red.astype(np.uint8) * 255
    if roi is not None:
        m &= roi
    fat = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)))
    fat = cv2.dilate(fat, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))
    thin = cv2.morphologyEx(m & ~fat, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(thin)
    keep = np.zeros_like(thin)
    for i in range(1, n):
        if max(st[i, cv2.CC_STAT_WIDTH], st[i, cv2.CC_STAT_HEIGHT]) >= 60:
            keep[lab == i] = 255
    ys, xs = np.nonzero(keep)
    if len(xs) < 500:
        raise RuntimeError("no veo el borde rojo del mousepad (¿a la vista y con luz?)")
    pts = np.stack([xs, ys], 1).astype(np.float64)
    rng = np.random.default_rng(0)
    if len(pts) > 40000:
        pts = pts[rng.choice(len(pts), 40000, replace=False)]
    raw = _ransac_lines(pts, rng=rng)
    ref = np.array([color.shape[1] / 2, color.shape[0] / 2])
    lines = []
    for c, d, nin in raw:                                   # deduplicar: mismo ángulo (±8°) y a menos de 45 px
        ang = math.degrees(math.atan2(d[1], d[0])) % 180
        dup = False
        for c2, d2, _, ang2 in lines:
            da = min(abs(ang - ang2), 180 - abs(ang - ang2))
            if da < 8 and abs(_line_offset(c, d, ref) - _line_offset(c2, d2, ref)) < 45:
                dup = True; break
        if not dup:
            lines.append((c, d, nin, ang))
    if not lines:
        raise RuntimeError("sin rectas en el borde rojo")
    strongest = lines[0][3]
    def is_h(ang):
        da = min(abs(ang - strongest), 180 - abs(ang - strongest)); return da < 35
    hor = [l for l in lines if is_h(l[3])]; ver = [l for l in lines if not is_h(l[3])]
    if len(hor) < 2:
        raise RuntimeError(f"solo {len(hor)} borde(s) horizontal(es) del pad; rectas: {[(round(l[3],1), l[2]) for l in lines]}")
    hor = sorted(hor, key=lambda l: _line_offset(l[0], l[1], ref))[:2]
    top, bot = sorted(hor, key=lambda l: l[0][1])                   # menor y = arriba
    ver = sorted(ver, key=lambda l: l[0][0])
    def ends(line, side):
        c, d = line[0], line[1]
        t = (pts - c) @ d; nv = np.array([-d[1], d[0]])
        on = np.abs((pts - c) @ nv) < 6
        tt = t[on]
        return c + d * (tt.min() if side == "left" else tt.max())
    if len(ver) >= 2:
        left, right = ver[0], ver[-1]
    elif len(ver) == 1:
        v0 = ver[0]
        mid_x = (top[0][0] + bot[0][0]) / 2
        if v0[0][0] > mid_x:                                     # tenemos la derecha: izquierda por los extremos
            right = v0; a, bq = ends(top, "left"), ends(bot, "left")
            left = (a, (bq - a) / np.linalg.norm(bq - a), 0, 0.0)
        else:
            left = v0; a, bq = ends(top, "right"), ends(bot, "right")
            right = (a, (bq - a) / np.linalg.norm(bq - a), 0, 0.0)
    else:
        a, bq = ends(top, "left"), ends(bot, "left"); left = (a, (bq - a) / np.linalg.norm(bq - a), 0, 0.0)
        a, bq = ends(top, "right"), ends(bot, "right"); right = (a, (bq - a) / np.linalg.norm(bq - a), 0, 0.0)
    quad = np.array([_intersect(top, left), _intersect(top, right), _intersect(bot, right), _intersect(bot, left)])
    return order_quad(quad), keep


def red_border_quad_hull(color: np.ndarray, roi: np.ndarray | None, min_area: int = 2000) -> tuple[np.ndarray, np.ndarray]:
    """Cuadrilátero exterior del borde rojo más grande (dentro de roi, si se da). Devuelve (quad 4x2, máscara)."""
    hsv = cv2.cvtColor(color, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    red = ((h <= 8) | (h >= 170)) & (s >= 90) & (v >= 70)
    mask = red.astype(np.uint8) * 255
    if roi is not None:
        mask &= roi
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask)
    if n < 2:
        raise RuntimeError("no veo rojo en la foto: ¿el borde del mousepad está a la vista y con luz?")
    # el borde es un anillo: la componente con el bounding box más grande (no la de más píxeles)
    boxes = [(st[i, cv2.CC_STAT_WIDTH] * st[i, cv2.CC_STAT_HEIGHT], i) for i in range(1, n) if st[i, cv2.CC_STAT_AREA] >= min_area]
    if not boxes:
        raise RuntimeError("rojo demasiado chico para ser el borde del mousepad")
    boxes.sort(reverse=True)
    comp = (lab == boxes[0][1]).astype(np.uint8)
    # unir también otras componentes rojas que estén cerca del bounding box (el borde se corta con las manos)
    x, y, w, hh = (int(st[boxes[0][1], j]) for j in range(4))
    for _, i in boxes[1:]:
        xi, yi, wi, hi = (int(st[i, j]) for j in range(4))
        if xi >= x - 0.15 * w and yi >= y - 0.15 * hh and xi + wi <= x + 1.15 * w and yi + hi <= y + 1.15 * hh:
            comp |= (lab == i).astype(np.uint8)
    ys, xs = np.nonzero(comp)
    pts = np.stack([xs, ys], 1).astype(np.float32)
    hull = cv2.convexHull(pts)
    peri = cv2.arcLength(hull, True)
    quad = None
    for eps in (0.01, 0.02, 0.03, 0.05, 0.08):
        ap = cv2.approxPolyDP(hull, eps * peri, True)
        if len(ap) == 4:
            quad = ap.reshape(4, 2); break
    if quad is None:                                   # respaldo: extremos de x+y y x-y del casco
        quad = order_quad(hull.reshape(-1, 2))
    return order_quad(quad), comp * 255


def cam_to_proj_homography(dec, pad_mask: np.ndarray) -> tuple[np.ndarray, dict]:
    """Homografía cámara→proyector del plano del pad, por RANSAC sobre las correspondencias dentro del pad."""
    cam, pro, _ = dec.points()
    inside = pad_mask[cam[:, 1], cam[:, 0]] > 0
    c, p = cam[inside].astype(np.float64), pro[inside].astype(np.float64)
    if len(c) < 200:
        raise RuntimeError(f"solo {len(c)} correspondencias dentro del pad: el gray code no cubrió el pad")
    if len(c) > 60000:
        sel = np.random.default_rng(0).choice(len(c), 60000, replace=False); c, p = c[sel], p[sel]
    Hm, inl = cv2.findHomography(c, p, cv2.RANSAC, 3.0)
    if Hm is None:
        raise RuntimeError("no pude ajustar la homografía cámara→proyector")
    inl = inl.ravel().astype(bool)
    err = np.linalg.norm(cv2.perspectiveTransform(c.reshape(-1, 1, 2), Hm).reshape(-1, 2) - p, axis=1)
    return Hm, {"correspondencias": int(len(c)), "inliers": int(inl.sum()), "mediana_px": float(np.median(err[inl])),
                "p95_px": float(np.percentile(err[inl], 95))}


def aspect_from_view(quad_cam: np.ndarray, size: tuple[int, int], hfov_deg: float) -> float:
    """Proporción ancho/alto de un rectángulo visto en perspectiva, con K aproximada (metrología de una vista:
    H del cuadrado unitario a la imagen; |K⁻¹h1| / |K⁻¹h2| = w/h)."""
    w_img, h_img = size
    f = 0.5 * w_img / math.tan(math.radians(hfov_deg) / 2)
    K = np.array([[f, 0, w_img / 2], [0, f, h_img / 2], [0, 0, 1]], np.float64)
    src = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float64)
    Hm = cv2.getPerspectiveTransform(src.astype(np.float32), quad_cam.astype(np.float32)).astype(np.float64)
    Ki = np.linalg.inv(K)
    r1, r2 = Ki @ Hm[:, 0], Ki @ Hm[:, 1]
    return float(np.linalg.norm(r1) / np.linalg.norm(r2))


def checker_image(aspect: float, squares_v: int, margin: float, px_h: int = 1600) -> tuple[np.ndarray, tuple[float, float]]:
    """Damero en el plano del pad (ancho = aspect·alto), con margen relativo al alto. Devuelve (imagen, (w_px, h_px))."""
    ph = px_h; pw = int(round(ph * aspect))
    img = np.zeros((ph, pw, 3), np.uint8)
    m = int(round(margin * ph))
    sq = (ph - 2 * m) / squares_v
    n_h = int((pw - 2 * m) // sq)
    x0 = int(round((pw - n_h * sq) / 2)); y0 = m
    for j in range(squares_v):
        for i in range(n_h):
            if (i + j) % 2 == 0:
                cv2.rectangle(img, (int(x0 + i * sq), int(y0 + j * sq)), (int(x0 + (i + 1) * sq) - 1, int(y0 + (j + 1) * sq) - 1),
                              (255, 255, 255), -1)
    cv2.rectangle(img, (x0, y0), (int(x0 + n_h * sq) - 1, int(y0 + squares_v * sq) - 1), (0, 200, 255), 6)
    return img, (float(pw), float(ph))


def runtime_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/schema", timeout=1.5) as r:
            return r.status == 200
    except OSError:
        return False


def runtime_cmd(url: str, cmd: str, **args) -> dict:
    body = json.dumps({"cmd": cmd, "args": args}).encode()
    req = urllib.request.Request(url + "/api/command", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("scan"); p.add_argument("--camera", default="osmo")
    p.add_argument("--aspect", type=float, default=None, help="ancho/alto real del pad (si se sabe)")
    p.add_argument("--pad-mm", default=None, help="WxH en mm del pad (p. ej. 900x400)")
    p.add_argument("--hfov", type=float, default=84.0, help="FOV horizontal aproximado de la Osmo (para estimar la proporción)")
    p.add_argument("--squares", type=int, default=6, help="cuadrados a lo alto")
    p.add_argument("--margin", type=float, default=0.06, help="margen relativo al alto del pad")
    p.add_argument("--show", action="store_true"); p.add_argument("--seconds", type=float, default=900)
    p.add_argument("--runtime", default="http://127.0.0.1:8790"); p.add_argument("--no-verify", action="store_true")
    a = p.parse_args(argv)
    scan = Path(a.scan)
    from slscan.graycode import Decoded
    dec = Decoded.load(scan / a.camera / "map.npz")
    color = cv2.imread(str(scan / a.camera / "color.png"))
    if color is None:
        raise SystemExit(f"falta {scan / a.camera / 'color.png'}")
    cam_size = (color.shape[1], color.shape[0])
    if tuple(dec.size) != cam_size:
        raise SystemExit(f"la foto es {cam_size} y el mapa {tuple(dec.size)}: no calzan")
    # 1. borde rojo, buscando cerca de la zona que el proyector alcanza
    valid = dec.valid.astype(np.uint8) * 255
    roi = cv2.dilate(valid, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (121, 121)))
    quad_cam, red = red_border_quad(color, roi)
    pad_mask = np.zeros(color.shape[:2], np.uint8)
    cv2.fillPoly(pad_mask, [quad_cam.astype(np.int32)], 255)
    # 2. cámara → proyector sobre el plano del pad
    Hcp, stats = cam_to_proj_homography(dec, pad_mask)
    quad_proj = cv2.perspectiveTransform(quad_cam.reshape(-1, 1, 2), Hcp).reshape(4, 2)
    # 3. proporción
    if a.pad_mm:
        pw, ph = (float(v) for v in a.pad_mm.lower().split("x")); aspect = pw / ph; src = f"pad-mm {a.pad_mm}"
    elif a.aspect:
        aspect = float(a.aspect); src = "aspect dado"
    else:
        aspect = aspect_from_view(quad_cam, cam_size, a.hfov); src = f"metrología de una vista (hfov {a.hfov}°)"
    # 4. damero pre-deformado: plano del pad → proyector
    chk, (cw, ch) = checker_image(aspect, a.squares, a.margin)
    src_pts = np.array([[0, 0], [cw, 0], [cw, ch], [0, ch]], np.float32)
    Hpp = cv2.getPerspectiveTransform(src_pts, quad_proj.astype(np.float32))
    proj_img = cv2.warpPerspective(chk, Hpp, (W, H), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    cv2.imwrite(str(scan / "pad_checker.png"), proj_img)
    dbg = color.copy()
    dbg[red > 0] = (0, 0, 255)
    cv2.polylines(dbg, [quad_cam.astype(np.int32)], True, (0, 255, 0), 4)
    for i, (x, y) in enumerate(quad_cam):
        cv2.circle(dbg, (int(x), int(y)), 18, (0, 255, 255), 3)
        cv2.putText(dbg, "TL TR BR BL".split()[i], (int(x) + 20, int(y) - 10), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 255, 255), 3)
    cv2.imwrite(str(scan / "pad_debug.png"), cv2.resize(dbg, (cam_size[0] // 2, cam_size[1] // 2), interpolation=cv2.INTER_AREA))
    inside = ((quad_proj[:, 0] >= 0) & (quad_proj[:, 0] < W) & (quad_proj[:, 1] >= 0) & (quad_proj[:, 1] < H)).tolist()
    out = {"camera": a.camera, "camera_size": list(cam_size), "quad_cam_tl_tr_br_bl": quad_cam.round(1).tolist(),
           "quad_proj_tl_tr_br_bl": quad_proj.round(1).tolist(), "quad_proj_inside_projector": inside,
           "H_cam_to_proj": Hcp.tolist(), "H_cam_to_proj_stats": stats, "aspect_w_over_h": round(aspect, 4), "aspect_source": src,
           "H_pad_to_proj": Hpp.tolist(), "pad_plane_px": [cw, ch], "squares_v": a.squares, "margin": a.margin,
           "checker": str(scan / "pad_checker.png")}
    (scan / "pad_quad.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if not k.startswith("H_")}, ensure_ascii=False))
    if not a.show:
        return 0
    # 5. proyectar y verificar
    if runtime_alive(a.runtime):
        r = runtime_cmd(a.runtime, "overlay", path=str((scan / "pad_checker.png").resolve()), seconds=a.seconds)
        print("overlay por el runtime:", r.get("result") or r)
        time.sleep(2.5)
        win = None
    else:
        from slscan.capture import PatternWindow
        win = PatternWindow((W, H), screen="1920x1080", name="pad_checker"); win.show(proj_img); time.sleep(2.5)
    if not a.no_verify:
        try:
            from slscan.capture import Camera
            cam = Camera(a.camera, size=f"{cam_size[0]}x{cam_size[1]}", rotate=0, downscale=1)
            cam.flush(10); f = cam.read(); cam.close()
            cv2.imwrite(str(scan / "pad_checker_osmo.png"), f)
            print("verificación:", scan / "pad_checker_osmo.png")
        except Exception as e:  # noqa: BLE001
            print("sin foto de verificación:", e)
    if win is not None:
        try:
            print(f"ventana propia en el proyector por {a.seconds:.0f} s (Ctrl+C para cerrar)")
            time.sleep(a.seconds)
        except KeyboardInterrupt:
            pass
        finally:
            win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
