"""Escala métrica con una hoja carta: del mapa relativo proyector<->cámara a 3D en mm y a una malla.

Idea: una hoja carta (215,9 x 279,4 mm) apoyada en una superficie plana dentro de la proyección es una regla
que la cámara ve sola. Con sus 4 esquinas en la cámara y el mapa denso del gray code (cámara -> proyector)
sabemos dónde caen esas esquinas en el proyector, y eso da la homografía métrica plano(mm) -> cámara y
plano(mm) -> proyector de ese plano (`paper_scale`).

Con la hoja pegada a una cartulina que se inclina en 3+ poses se calibra el par cámara-proyector
(`calibrate_from_paper`): por pose, 4 puntos métricos de la hoja + las esquinas del ChArUco PROYECTADO
(su píxel de proyector se conoce exacto; su posición en mm sale de la homografía de la hoja), 24+ puntos
por pose. Resultado: `slscan.rig.Rig` con el mundo en la cámara.

Con un rig, `relative_to_metric` triangula las correspondencias del escaneo (nube en mm por píxel de
proyector) y `cloud_to_mesh` la convierte en malla (Open3D si está instalado; si no, triangulación por
grilla en el espacio del proyector, con normales y color). `write_mesh_ply` guarda PLY con caras.

Convención de píxeles de proyector (la misma de `calib.py` y de los tests): el código gray k cubre
[k, k+1) y su centro es k + 0,5; con `skip_bits` > 0 se usa el centro del bloque (`proj_coords`, que además
corrige los bits bajos que deja `graycode.decode`). Las esquinas del ChArUco proyectado
(`cli.projected_board`) usan la misma convención.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .graycode import Decoded
from .rig import Device, Rig

PAPER_MM = (215.9, 279.4)        # carta: ancho x alto (lado corto, lado largo)


# ============================================================================= hoja: detección

def order_quad(quad) -> np.ndarray:
    """4 puntos en orden horario en la imagen (y hacia abajo), empezando por el de arriba a la izquierda."""
    q = np.asarray(quad, np.float64).reshape(4, 2)
    c = q.mean(axis=0)
    q = q[np.argsort(np.arctan2(q[:, 1] - c[1], q[:, 0] - c[0]))]
    return np.roll(q, -int(np.argmin(q.sum(axis=1))), axis=0)


def _sides(q: np.ndarray) -> np.ndarray:
    return np.linalg.norm(np.roll(q, -1, axis=0) - q, axis=1)


def _line_intersect(l1, l2) -> np.ndarray | None:
    """Intersección de dos rectas dadas como (punto, dirección)."""
    (p, d), (q, e) = l1, l2
    A = np.array([d, -e]).T
    if abs(np.linalg.det(A)) < 1e-9:
        return None
    s = np.linalg.solve(A, q - p)
    return p + s[0] * d


def refine_quad_edges(gray: np.ndarray, quad: np.ndarray, n_samples: int = 32, search: float = 5.0) -> np.ndarray:
    """Esquinas subpíxel: cada lado se ajusta a los cruces de nivel medio (papel/fondo) a lo largo de su normal.

    Por lado se toman `n_samples` perfiles perpendiculares de ±`search` px en el 15–85 % central (lejos de las
    esquinas), se busca el cruce del nivel medio entre el lado de adentro y el de afuera, se ajusta una recta
    robusta y las esquinas son las intersecciones de rectas vecinas. Si algo sale raro devuelve el quad tal cual.
    """
    q = order_quad(quad)
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 0.7)
    c = q.mean(axis=0)
    offs = np.arange(-search, search + 1e-6, 0.25, dtype=np.float32)
    lines = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        d = b - a
        L = float(np.linalg.norm(d))
        if L < 8:
            return q
        u = d / L
        n = np.array([-u[1], u[0]])
        if np.dot(n, (a + b) / 2 - c) < 0:
            n = -n                                   # normal hacia afuera de la hoja
        ts = np.linspace(0.15, 0.85, n_samples)
        base = a[None, :] + ts[:, None] * d[None, :]
        pos = base[:, None, :] + offs[None, :, None] * n[None, None, :]
        mx, my = pos[..., 0].astype(np.float32), pos[..., 1].astype(np.float32)
        prof = cv2.remap(g, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        inner = np.median(prof[:, offs < -1.5], axis=1)
        outer = np.median(prof[:, offs > 1.5], axis=1)
        pts = []
        for k in range(n_samples):
            if abs(inner[k] - outer[k]) < 8:
                continue
            s = np.sign(inner[k] - outer[k])
            v = (prof[k] - (inner[k] + outer[k]) / 2) * s   # > 0 adentro, < 0 afuera
            cross = np.nonzero((v[:-1] > 0) & (v[1:] <= 0))[0]
            if len(cross) == 0:
                continue
            j = cross[np.argmin(np.abs(offs[cross]))]       # el cruce más cercano al borde aproximado
            t = v[j] / (v[j] - v[j + 1])
            o = offs[j] + t * (offs[j + 1] - offs[j])
            pts.append(base[k] + o * n)
        if len(pts) < max(5, n_samples // 3):
            return q
        vx, vy, x0, y0 = cv2.fitLine(np.array(pts, np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0], np.float64), np.array([vx, vy], np.float64)))
    out = []
    for i in range(4):
        p = _line_intersect(lines[i - 1], lines[i])        # esquina i = lado (i-1) ∩ lado i
        if p is None or np.linalg.norm(p - q[i]) > 3 * search:
            return q
        out.append(p)
    return np.array(out)


def detect_paper(image_bgr: np.ndarray, aspect: float = PAPER_MM[1] / PAPER_MM[0], roi=None,
                 min_area: float = 300.0, max_area_frac: float = 0.35, refine: bool = True,
                 return_info: bool = False):
    """Hoja blanca rectangular -> quad (4,2) en px, horario desde arriba-izquierda; None si no hay.

    Umbrales en varios niveles (Otsu y percentiles) sobre la luma, con saturación baja (blanco); cada
    contorno se aproxima con approxPolyDP y se queda si da 4 vértices convexos. Se elige el mejor por:
    llenado del cuadrilátero, proporción parecida a la de la hoja (con margen por la perspectiva), ángulos
    cercanos a 90°, contraste con el borde de afuera y uniformidad adentro. `roi` = (x, y, w, h) acota la
    búsqueda. Con `refine` las esquinas quedan subpíxel (`refine_quad_edges`).
    """
    img = np.asarray(image_bgr)
    if img.dtype != np.uint8:
        img = np.clip(img, 0, 255).astype(np.uint8)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sat = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 1]
    Hh, Ww = gray.shape
    x0, y0, w, h = (0, 0, Ww, Hh) if roi is None else (int(v) for v in roi)
    g, s = gray[y0:y0 + h, x0:x0 + w], sat[y0:y0 + h, x0:x0 + w]
    lit = g[g > 12]
    if lit.size < 100:
        return (None, {"candidates": 0}) if return_info else None
    otsu, _ = cv2.threshold(lit.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    levels = sorted({int(otsu)} | {int(np.percentile(lit, p)) for p in (50, 65, 80, 90, 95)})
    sat_max = max(70, int(np.percentile(s[g > otsu], 60)) + 30) if (g > otsu).any() else 90
    k3 = np.ones((3, 3), np.uint8)
    cands = []
    for t in levels:
        m = ((g > t) & (s < sat_max)).astype(np.uint8) * 255
        m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, k3), cv2.MORPH_CLOSE, k3)
        cnts, _ = cv2.findContours(m, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        for cnt in cnts:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area_frac * Ww * Hh:
                continue
            ap = cv2.approxPolyDP(cnt, 0.02 * cv2.arcLength(cnt, True), True)
            if len(ap) != 4 or not cv2.isContourConvex(ap):
                continue
            quad = order_quad(ap.reshape(4, 2) + np.array([x0, y0]))
            qa = cv2.contourArea(quad.astype(np.float32))
            fill = area / max(qa, 1.0)
            if not 0.85 < fill < 1.1:
                continue
            sd = _sides(quad)
            r = (sd[0] + sd[2]) / max(sd[1] + sd[3], 1e-6)
            ar = max(r, 1 / r)
            ar_score = float(np.exp(-abs(np.log(ar / aspect)) / 0.25))
            if ar_score < 0.15:                      # proporción > ~2: banda, no hoja (ni con perspectiva fuerte)
                continue
            e = np.roll(quad, -1, axis=0) - quad
            cosang = [abs(np.dot(-e[i - 1], e[i])) / (np.linalg.norm(e[i - 1]) * np.linalg.norm(e[i]) + 1e-9) for i in range(4)]
            rect_score = float(max(0.0, 1 - np.mean(cosang) * 2))
            # contraste: adentro (erosionado) contra un anillo afuera
            mask = np.zeros((Hh, Ww), np.uint8)
            cv2.fillPoly(mask, [quad.astype(np.int32).reshape(-1, 1, 2)], 255)
            kk = max(3, int(np.sqrt(qa) * 0.08)) | 1
            inner = cv2.erode(mask, np.ones((kk, kk), np.uint8))
            ring = cv2.dilate(mask, np.ones((kk, kk), np.uint8)) - mask
            if inner.sum() == 0 or ring.sum() == 0:
                continue
            gin, gout = gray[inner > 0].astype(np.float32), gray[ring > 0].astype(np.float32)
            contrast = float(np.clip((np.median(gin) - np.median(gout)) / max(np.median(gin), 1), 0, 1))
            uniform = float(np.exp(-np.std(gin) / 40.0))
            score = fill * ar_score * rect_score * (0.2 + contrast) * uniform
            cands.append((score, area, quad))
    if not cands:
        return (None, {"candidates": 0}) if return_info else None
    cands.sort(key=lambda c: (-c[0], -c[1]))
    best = cands[0][2]
    if refine:
        best = refine_quad_edges(gray, best)
    info = {"candidates": len(cands), "score": float(cands[0][0]), "area_px": float(cands[0][1]),
            "levels": levels}
    return (best, info) if return_info else best


def paper_corners_mm(size_mm=PAPER_MM) -> np.ndarray:
    """Esquinas de la hoja en su plano (mm): (0,0), (ancho,0), (ancho,alto), (0,alto); el 0-1 es el lado corto."""
    w, h = size_mm
    return np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float64)


def orient_paper_quad(quad_cam: np.ndarray, quad_proj: np.ndarray | None = None, K_cam: np.ndarray | None = None,
                      size_mm=PAPER_MM) -> tuple[np.ndarray, np.ndarray | None]:
    """Rota el orden cíclico del quad para que el lado 0-1 sea el corto de la hoja.

    Con K de la cámara (aunque sea aproximada) se elige la hipótesis cuya homografía plano->cámara da columnas
    K^-1 H más ortogonales y de igual norma (restricción de Zhang); sin K, por las longitudes en la imagen
    (cámara y proyector suman evidencia).
    """
    # el quad del proyector viene punto a punto de la homografía: comparte el orden del de la cámara
    qc = order_quad(quad_cam)
    qp = None if quad_proj is None else np.asarray(quad_proj, np.float64).reshape(4, 2)
    if K_cam is not None:
        costs = [zhang_cost(np.roll(qc, -r, axis=0), K_cam, size_mm) for r in (0, 1)]
        r = int(np.argmin(costs))
    else:
        ev = 0.0
        for q in (qc, qp):
            if q is None:
                continue
            sd = _sides(q)
            ev += np.log((sd[0] + sd[2]) / max(sd[1] + sd[3], 1e-9))
        r = 0 if ev <= 0 else 1          # ev < 0: 0-1 y 2-3 son los cortos
    qc = np.roll(qc, -r, axis=0)
    if qp is not None:
        qp = np.roll(qp, -r, axis=0)
    return qc, qp


def zhang_cost(quad_cam: np.ndarray, K_cam: np.ndarray, size_mm=PAPER_MM) -> float:
    """Qué tan mal calza el quad (esquina i <-> paper_corners_mm[i]) con una cámara K: las columnas r1, r2 de
    K^-1 H deben tener igual norma y ser ortogonales. ~0 si el orden es el correcto; con la hoja girada 90°
    la razón de normas se va a ~1,29² (lado largo / corto)."""
    mm = paper_corners_mm(size_mm).astype(np.float32)
    H = cv2.getPerspectiveTransform(mm, np.asarray(quad_cam, np.float32))
    M = np.linalg.inv(K_cam) @ H
    m1, m2 = M[:, 0], M[:, 1]
    n1, n2 = np.linalg.norm(m1), np.linalg.norm(m2)
    return float(abs(np.log(n1 / n2)) + abs(np.dot(m1, m2)) / (n1 * n2))


# ============================================================================= plano de la hoja

def block_px(dec: Decoded | None) -> int:
    """Lado del bloque de código en px de proyector (2^skip_bits)."""
    return 1 if dec is None else 2 ** int(dec.stats.get("skip_bits", 0) or 0)


def proj_coords(dec: Decoded, px, py) -> tuple[np.ndarray, np.ndarray]:
    """Código gray decodificado -> px de proyector del centro del código (convención de bordes).

    Sin skip_bits: k + 0,5. Con skip_bits, `graycode.decode` deja los bits bajos en 2 o 3 según el bit `skip_bits`
    (al pasar de gray a binario los bits ignorados se rellenan con ese bit, y después se les suma el medio
    bloque): la mitad de los bloques queda 1 px corrida (visto en captures/scan2). Acá se redondea al bloque y
    se toma su centro exacto.
    """
    px = np.asarray(px, np.float64); py = np.asarray(py, np.float64)
    b = block_px(dec)
    if b == 1:
        return px + 0.5, py + 0.5
    return np.floor(px / b) * b + b / 2, np.floor(py / b) * b + b / 2


def _apply_h(H: np.ndarray, pts) -> np.ndarray:
    p = np.asarray(pts, np.float64).reshape(-1, 2)
    q = np.column_stack([p, np.ones(len(p))]) @ H.T
    return q[:, :2] / q[:, 2:3]


def _local_scale(H: np.ndarray, p, eps: float = 0.5) -> float:
    """Factor de escala local (raíz del determinante del jacobiano) de la homografía H en el punto p."""
    p = np.asarray(p, np.float64)
    q = _apply_h(H, [p + [eps, 0], p - [eps, 0], p + [0, eps], p - [0, eps]])
    J = np.column_stack([(q[0] - q[1]) / (2 * eps), (q[2] - q[3]) / (2 * eps)])
    return float(np.sqrt(abs(np.linalg.det(J))))


def fit_cam_to_proj(dec: Decoded, polygon: np.ndarray, shrink: float = 0.85, ransac_px: float | None = None,
                    max_pts: int = 30000) -> tuple[np.ndarray | None, dict]:
    """Homografía cámara -> proyector del plano con los píxeles decodificados DENTRO del polígono (encogido).

    A diferencia de `calib.plane_homography` (que agranda el polígono para leer alrededor de un tablero que no
    se decodifica), acá se usa el interior de la hoja: es blanca, decodifica bien y es plana con seguridad.
    RANSAC con umbral acorde al bloque (skip_bits) y refinamiento por mínimos cuadrados con los inliers.
    """
    h, w = dec.px.shape
    poly = np.asarray(polygon, np.float64).reshape(-1, 2)
    c = poly.mean(axis=0)
    small = c + (poly - c) * shrink
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [np.round(small).astype(np.int32).reshape(-1, 1, 2)], 255)
    ys, xs = np.nonzero((mask > 0) & dec.valid)
    info = {"n": int(len(xs))}
    if len(xs) < 30:
        return None, info
    if len(xs) > max_pts:
        idx = np.random.default_rng(0).choice(len(xs), max_pts, replace=False)
        xs, ys = xs[idx], ys[idx]
    src = np.column_stack([xs, ys]).astype(np.float64)
    dst = np.column_stack(proj_coords(dec, dec.px[ys, xs], dec.py[ys, xs]))
    thr = ransac_px if ransac_px is not None else 1.0 + 0.75 * block_px(dec)
    H, m = cv2.findHomography(src, dst, cv2.RANSAC, thr)
    if H is None or m.sum() < 30:
        return None, info
    inl = m.ravel() > 0
    H2, _ = cv2.findHomography(src[inl], dst[inl], 0)
    if H2 is not None:
        H = H2
    res = np.linalg.norm(_apply_h(H, src[inl]) - dst[inl], axis=1)
    info.update({"inliers": int(inl.sum()), "rms_proj_px": float(np.sqrt(np.mean(res ** 2))),
                 "block_px": block_px(dec)})
    return H / H[2, 2], info


@dataclass
class PaperPlane:
    """La hoja en un plano: esquinas en cámara y proyector y homografías métricas (mm) de ese plano."""
    quad_cam: np.ndarray                 # (4,2) px cámara, esquina i <-> corners_mm[i]
    quad_proj: np.ndarray | None         # (4,2) px proyector
    corners_mm: np.ndarray               # (4,2) mm en el plano de la hoja
    H_plane_cam: np.ndarray              # mm -> px cámara
    H_plane_proj: np.ndarray | None      # mm -> px proyector
    H_cam_proj: np.ndarray | None        # px cámara -> px proyector (en ese plano)
    mm_per_px_cam: float
    mm_per_px_proj: float | None
    fit: dict = field(default_factory=dict)

    def cam_to_mm(self, pts) -> np.ndarray:
        return _apply_h(np.linalg.inv(self.H_plane_cam), pts)

    def cam_to_proj(self, pts) -> np.ndarray:
        return _apply_h(self.H_cam_proj, pts)

    def proj_per_cam_px(self) -> float:
        """Cuántos px de proyector caben en un px de cámara en el centro de la hoja."""
        if self.H_cam_proj is None:
            return float("nan")
        return _local_scale(self.H_cam_proj, self.quad_cam.mean(axis=0))

    def to_json(self) -> dict:
        def L(a):
            return None if a is None else np.asarray(a, float).tolist()
        sd_c = _sides(self.quad_cam)
        d = {"quad_cam": L(self.quad_cam), "quad_proj": L(self.quad_proj), "corners_mm": L(self.corners_mm),
             "H_plane_cam": L(self.H_plane_cam), "H_plane_proj": L(self.H_plane_proj), "H_cam_proj": L(self.H_cam_proj),
             "mm_per_px_cam": self.mm_per_px_cam, "mm_per_px_proj": self.mm_per_px_proj,
             "sides_cam_px": L(sd_c), "fit": self.fit}
        if self.quad_proj is not None:
            d["sides_proj_px"] = L(_sides(self.quad_proj))
        return d


def paper_scale(quad_cam: np.ndarray, dec: Decoded | None = None, size_mm=PAPER_MM,
                cam_proj: tuple[np.ndarray, np.ndarray] | None = None, K_cam: np.ndarray | None = None,
                shrink: float = 0.85) -> PaperPlane:
    """Esquinas de la hoja en la cámara -> homografías métricas plano->cámara y plano->proyector (mm).

    Las esquinas se llevan al proyector con la homografía cámara->proyector del plano de la hoja, ajustada
    con el mapa denso (`fit_cam_to_proj`, interior de la hoja). Si no hay mapa, sirve una lista de
    correspondencias del mismo plano (`cam_proj` = (px cámara, px proyector), p.ej. el ChArUco proyectado).
    """
    quad_cam = order_quad(quad_cam)
    H_cp, fit = None, {}
    if dec is not None:
        H_cp, fit = fit_cam_to_proj(dec, quad_cam, shrink)
        fit["source"] = "gray code"
    if H_cp is None and cam_proj is not None and len(cam_proj[0]) >= 4:
        c, p = (np.asarray(a, np.float64).reshape(-1, 2) for a in cam_proj)
        H_cp, m = cv2.findHomography(c, p, cv2.RANSAC, 4.0)
        if H_cp is not None:
            inl = m.ravel() > 0
            res = np.linalg.norm(_apply_h(H_cp, c[inl]) - p[inl], axis=1)
            fit = {"source": "charuco", "n": int(len(c)), "inliers": int(inl.sum()),
                   "rms_proj_px": float(np.sqrt(np.mean(res ** 2)))}
    quad_proj = None if H_cp is None else _apply_h(H_cp, quad_cam)
    qc, qp = orient_paper_quad(quad_cam, quad_proj, K_cam, size_mm)
    return _make_plane(qc, qp, H_cp, size_mm, fit)


def _make_plane(qc: np.ndarray, qp: np.ndarray | None, H_cp: np.ndarray | None, size_mm, fit: dict) -> PaperPlane:
    """PaperPlane con las esquinas ya ordenadas (la i-ésima <-> paper_corners_mm[i])."""
    mm = paper_corners_mm(size_mm)
    H_pc = cv2.getPerspectiveTransform(mm.astype(np.float32), np.asarray(qc, np.float32)).astype(np.float64)
    H_pp = None if H_cp is None else H_cp @ H_pc
    if H_pp is not None:
        H_pp = H_pp / H_pp[2, 2]
    ctr = mm.mean(axis=0)
    mm_cam = 1.0 / _local_scale(H_pc, ctr)
    mm_proj = None if H_pp is None else 1.0 / _local_scale(H_pp, ctr)
    return PaperPlane(np.asarray(qc, np.float64), qp, mm, H_pc, H_pp, H_cp, mm_cam, mm_proj, dict(fit))


def turn_pose(pose: "PaperPose") -> "PaperPose":
    """La misma pose con la hoja girada 90° (el lado largo pasa a ser el 0-1): hoja y mm del ChArUco rehechos."""
    pl = pose.plane
    size_mm = (pl.corners_mm[1, 0], pl.corners_mm[2, 1])
    qp = None if pl.quad_proj is None else np.roll(pl.quad_proj, -1, axis=0)
    new = _make_plane(np.roll(pl.quad_cam, -1, axis=0), qp, pl.H_cam_proj, size_mm, pl.fit)
    ch = None
    if pose.charuco is not None:
        ch = dict(pose.charuco)
        xy = new.cam_to_mm(ch["cam"])
        ch["obj"] = np.column_stack([xy, np.zeros(len(xy))])
    return PaperPose(new, ch, pose.name)


# ============================================================================= ChArUco proyectado

def projected_charuco(board, proj_size: tuple[int, int], margin_px: int = 40) -> tuple[np.ndarray, np.ndarray]:
    """Imagen del ChArUco a tamaño del proyector y px de proyector de cada esquina (por id). Igual que
    `slscan calib-projected` (cli.projected_board), así las capturas de ambos comandos son intercambiables."""
    from .cli import projected_board
    canvas, corners_px, _ = projected_board(board, proj_size, margin_px)
    return canvas, corners_px


def charuco_on_plane(img: np.ndarray, board, corners_px: np.ndarray, plane: PaperPlane | None = None,
                     tol_px: float | None = None, min_corners: int = 4) -> dict | None:
    """Esquinas del ChArUco proyectado vistas por la cámara -> (mm en el plano de la hoja, px cámara, px proyector).

    El px de proyector de cada esquina se conoce (lo dibujamos nosotros); el de cámara se detecta; los mm salen
    de la homografía de la hoja. Con `plane` se botan las esquinas que no calzan con la homografía del plano:
    las de otra superficie y las mal detectadas (p.ej. una esquina justo sobre el borde de la hoja, donde el
    cambio de albedo tira el subpíxel). Tolerancia por defecto: 3 x la mediana del error (robusta), entre medio
    px de cámara y el máximo de pertenencia al plano (6 px de proyector o 3 px de cámara).
    """
    d = board.detect(img, min_corners)
    if d is None:
        return None
    ids = d["ids"]
    cam = d["img"].astype(np.float64)
    proj = np.asarray(corners_px, np.float64)[ids]
    out = {"ids": ids, "cam": cam, "proj": proj, "detected": int(len(ids))}
    if plane is not None and plane.H_cam_proj is not None:
        err = np.linalg.norm(plane.cam_to_proj(cam) - proj, axis=1)
        s = plane.proj_per_cam_px()
        tol = tol_px if tol_px is not None else float(np.clip(3.0 * np.median(err), 0.5 * s, max(6.0, 3.0 * s)))
        keep = err < tol
        out.update({"ids": ids[keep], "cam": cam[keep], "proj": proj[keep], "plane_err_proj_px": err[keep],
                    "rejected": int((~keep).sum()), "tol_px": float(tol)})
    if plane is not None:
        xy = plane.cam_to_mm(out["cam"])
        out["obj"] = np.column_stack([xy, np.zeros(len(xy))])
    return out


# ============================================================================= calibración

@dataclass
class PaperPose:
    """Una pose del plano (hoja + cartulina): la hoja y, opcional, el ChArUco proyectado sobre ese plano."""
    plane: PaperPlane
    charuco: dict | None = None
    name: str = ""

    def points(self, use_charuco: bool = True) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(obj (n,3) mm z=0, cam (n,2) px, proj (n,2) px): 4 esquinas de la hoja + esquinas del ChArUco."""
        p = self.plane
        if p.quad_proj is None:
            raise ValueError(f"pose {self.name}: la hoja no tiene esquinas en el proyector (sin mapa ni ChArUco)")
        obj = [np.column_stack([p.corners_mm, np.zeros(4)])]
        cam, pro = [p.quad_cam], [p.quad_proj]
        ch = self.charuco
        if use_charuco and ch is not None and len(ch.get("cam", [])) > 0:
            o = ch.get("obj")
            if o is None:
                xy = p.cam_to_mm(ch["cam"]); o = np.column_stack([xy, np.zeros(len(xy))])
            obj.append(o); cam.append(ch["cam"]); pro.append(ch["proj"])
        return np.concatenate(obj), np.concatenate(cam), np.concatenate(pro)


def _flags(fix_pp: bool, k1: bool) -> int:
    """Modelo: píxel cuadrado, sin tangencial, k2 = k3 = 0; k1 y centro óptico según los datos."""
    f = (cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_ASPECT_RATIO | cv2.CALIB_ZERO_TANGENT_DIST
         | cv2.CALIB_FIX_K2 | cv2.CALIB_FIX_K3)
    if fix_pp:
        f |= cv2.CALIB_FIX_PRINCIPAL_POINT
    if not k1:
        f |= cv2.CALIB_FIX_K1
    return f


_CRIT = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 300, 1e-10)


def _f32(lst, dim):
    return [np.ascontiguousarray(a, np.float32).reshape(-1, 1, dim) for a in lst]


def _initial_K(obj_l, img_l, size, pp=None) -> np.ndarray:
    """f por initCameraMatrix2D (supone centro en el medio); el centro se pone en `pp` si se da."""
    try:
        K = cv2.initCameraMatrix2D(_f32(obj_l, 3), _f32(img_l, 2), size, 1.0)
    except cv2.error:
        K = np.array([[max(size), 0, size[0] / 2], [0, max(size), size[1] / 2], [0, 0, 1]], np.float64)
    K = np.asarray(K, np.float64)
    f = float(np.sqrt(K[0, 0] * K[1, 1]))
    if not np.isfinite(f) or f <= 0:
        f = float(max(size))
    cx, cy = (size[0] / 2, size[1] / 2) if pp is None else pp
    return np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]], np.float64)


def _calib_device(obj_l, img_l, size, fix_pp: bool, k1: bool, pp=None) -> dict:
    K0 = _initial_K(obj_l, img_l, size, pp)
    rms, K, dist, rv, tv = cv2.calibrateCamera(_f32(obj_l, 3), _f32(img_l, 2), size, K0, np.zeros(5),
                                               flags=_flags(fix_pp, k1), criteria=_CRIT)
    return {"K": K, "dist": dist.ravel(), "rms": float(rms), "rvecs": rv, "tvecs": tv}


def _stereo(obj_l, cam_l, pro_l, cam_size, Kc, dc, Kp, dp):
    out = cv2.stereoCalibrate(_f32(obj_l, 3), _f32(cam_l, 2), _f32(pro_l, 2), Kc, dc, Kp, dp, cam_size,
                              flags=cv2.CALIB_FIX_INTRINSIC, criteria=_CRIT)
    rms, _, _, _, _, R, T = out[:7]
    return float(rms), R, T.ravel()


def _per_pose_rms(obj_l, cam_l, pro_l, cam_res, R, T, Kp, dp) -> list[dict]:
    out = []
    for i, (o, c, p) in enumerate(zip(obj_l, cam_l, pro_l)):
        rv, tv = cam_res["rvecs"][i], cam_res["tvecs"][i]
        pc, _ = cv2.projectPoints(o.reshape(-1, 1, 3), rv, tv, cam_res["K"], cam_res["dist"])
        Rb, _ = cv2.Rodrigues(rv)
        Rp = R @ Rb; tp = R @ np.asarray(tv).ravel() + T
        pp_, _ = cv2.projectPoints(o.reshape(-1, 1, 3), cv2.Rodrigues(Rp)[0], tp, Kp, dp)
        ec = np.linalg.norm(pc.reshape(-1, 2) - c, axis=1)
        ep = np.linalg.norm(pp_.reshape(-1, 2) - p, axis=1)
        n_rad = np.degrees(np.arccos(np.clip(abs(Rb[2, 2]), 0, 1)))
        out.append({"n": int(len(o)), "rms_cam_px": float(np.sqrt(np.mean(ec ** 2))),
                    "rms_proj_px": float(np.sqrt(np.mean(ep ** 2))), "tilt_deg": float(n_rad),
                    "dist_mm": float(np.linalg.norm(tv))})
    return out


def _triangulate(rig: Rig, cam_name: str, cam_px, proj_px) -> np.ndarray:
    from .recon import Matches, triangulate_pair
    c = np.asarray(cam_px, np.float32).reshape(-1, 2)
    m = Matches(c, np.asarray(proj_px, np.float32).reshape(-1, 2), np.zeros((len(c), 3), np.float32))
    return triangulate_pair(m, rig[cam_name], rig.projector(), mode="world").astype(np.float64)


def paper_check(rig: Rig, poses: list[PaperPose], cam_name: str) -> list[dict]:
    """La hoja medida con el rig: lados triangulados de cada pose contra 215,9 x 279,4 mm, y planitud del
    ChArUco triangulado (RMS a su plano, mm)."""
    out = []
    for p in poses:
        pl = p.plane
        X = _triangulate(rig, cam_name, pl.quad_cam, pl.quad_proj)
        sides = np.linalg.norm(np.roll(X, -1, axis=0) - X, axis=1)
        truth = _sides(pl.corners_mm)
        r = {"pose": p.name, "sides_mm": [round(float(s), 2) for s in sides],
             "err_mm": [round(float(s - t), 2) for s, t in zip(sides, truth)],
             "dist_mm": round(float(np.linalg.norm(X.mean(axis=0))), 1)}
        ch = p.charuco
        if ch is not None and len(ch.get("cam", [])) >= 4:
            Y = _triangulate(rig, cam_name, ch["cam"], ch["proj"])
            Yc = Y - Y.mean(axis=0)
            _, s, vt = np.linalg.svd(Yc, full_matrices=False)
            r["charuco_planarity_mm"] = round(float(np.sqrt(np.mean((Yc @ vt[2]) ** 2))), 3)
        out.append(r)
    return out


def _rig_from(cam_name, cam_size, proj_size, cam_res, pro_res, R, T) -> Rig:
    cam = Device(cam_name, "camera", tuple(cam_size), cam_res["K"], cam_res["dist"], rms=cam_res["rms"])
    pro = Device("proj", "projector", tuple(proj_size), pro_res["K"], pro_res["dist"], np.asarray(R, np.float64),
                 np.asarray(T, np.float64).ravel(), rms=pro_res["rms"])
    return Rig({cam_name: cam, "proj": pro}, world=cam_name)


def _solve(obj_l, cam_l, pro_l, cam_size, proj_size, fix_pp_cam, fix_pp_proj, cam_k1, proj_k1, cam_pp, proj_pp):
    cr = _calib_device(obj_l, cam_l, cam_size, fix_pp_cam, cam_k1, cam_pp)
    pr = _calib_device(obj_l, pro_l, proj_size, fix_pp_proj, proj_k1, proj_pp)
    srms, R, T = _stereo(obj_l, cam_l, pro_l, cam_size, cr["K"], cr["dist"], pr["K"], pr["dist"])
    return cr, pr, srms, R, T


def _rig_delta(a: Rig, b: Rig, cam_name: str) -> dict:
    pa, pb = a.projector(), b.projector()
    dR = pa.R @ pb.R.T
    return {"proj_pos_mm": float(np.linalg.norm(pa.center_world() - pb.center_world())),
            "proj_rot_deg": float(np.degrees(np.arccos(np.clip((np.trace(dR) - 1) / 2, -1, 1)))),
            "cam_f_pct": float(100 * (a[cam_name].K[0, 0] / b[cam_name].K[0, 0] - 1)),
            "proj_f_pct": float(100 * (pa.K[0, 0] / pb.K[0, 0] - 1))}


def calibrate_from_paper(poses: list[PaperPose], cam_size: tuple[int, int], proj_size: tuple[int, int],
                         cam_name: str = "cam0", use_charuco: bool = True, fix_principal: str | bool = "auto",
                         cam_k1: str | bool = "auto", proj_k1: bool = False, cam_pp=None, proj_pp=None,
                         repeat: int = 20, seed: int = 0, log=None) -> Rig:
    """Cámara + proyector + pose relativa desde 3+ poses del plano con la hoja (y el ChArUco proyectado).

    Por pose: puntos objeto = esquinas de la hoja (mm, z=0) + esquinas del ChArUco llevadas al plano de la
    hoja. calibrateCamera para la cámara y para el proyector (como cámara inversa) y stereoCalibrate con
    intrínsecos fijos -> R, T (proyector respecto a la cámara). Modelo: píxel cuadrado, sin tangencial;
    con solo la hoja (4 puntos por pose) se fija el punto principal (centro, o `cam_pp`/`proj_pp`) y la
    distorsión es cero; con ChArUco el centro queda libre y la cámara ajusta k1.

    Qué lado de la hoja es el corto se verifica probando cada pose girada 90° (`turn_pose`) contra el rms estéreo.
    Devuelve el Rig (mundo = cámara). El reporte va en `rig.extra["metric_report"]`: rms por dispositivo y
    por pose, hoja medida con el rig (`paper_check`) y repetibilidad: dejando una pose fuera (con 4+
    poses) y re-calibrando `repeat` veces con ruido del tamaño del rms observado.
    """
    log = log or (lambda *_: None)
    if len(poses) < 3:
        raise ValueError(f"se necesitan >= 3 poses del plano, hay {len(poses)}")
    poses = list(poses)

    def gather():
        pts = [p.points(use_charuco) for p in poses]
        return [a for a, _, _ in pts], [b for _, b, _ in pts], [c for _, _, c in pts]

    obj_l, cam_l, pro_l = gather()
    paper_only = min(len(o) for o in obj_l) < 8
    fix_c = paper_only if fix_principal == "auto" else bool(fix_principal)
    k1c = (not paper_only) if cam_k1 == "auto" else bool(cam_k1)
    args = (cam_size, proj_size, fix_c, fix_c, k1c, proj_k1 and not paper_only, cam_pp, proj_pp)
    cr, pr, srms, R, T = _solve(obj_l, cam_l, pro_l, *args)
    # qué lado de la hoja es el corto se decidió por proporciones en la imagen (engaña con inclinación fuerte):
    # por ronda se prueba cada pose girada 90° y se acepta el giro que más baje el rms estéreo, si lo baja claro
    # (< 80 %). Es barato (pocas poses) y no depende de la K, que una pose mala puede dejar muy sesgada.
    reoriented = []
    for _ in range(len(poses)):
        best = None
        for i in range(len(poses)):
            trial = poses[:i] + [turn_pose(poses[i])] + poses[i + 1:]
            o2 = [t.points(use_charuco) for t in trial]
            try:
                sol = _solve([a for a, _, _ in o2], [b for _, b, _ in o2], [c for _, _, c in o2], *args)
            except cv2.error:
                continue
            if np.isfinite(sol[2]) and (best is None or sol[2] < best[1][2]):
                best = (i, sol, trial)
        if best is None or not best[1][2] < 0.8 * srms:
            break
        i, sol, trial = best
        log(f"  {poses[i].name}: la hoja estaba girada 90° (rms estéreo {srms:.2f} -> {sol[2]:.2f} px)")
        poses, (cr, pr, srms, R, T) = trial, sol
        reoriented.append(poses[i].name or str(i))
    obj_l, cam_l, pro_l = gather()
    rig = _rig_from(cam_name, cam_size, proj_size, cr, pr, R, T)
    log(f"  cámara: f {cr['K'][0, 0]:.1f} px, centro ({cr['K'][0, 2]:.1f}, {cr['K'][1, 2]:.1f}), k1 {cr['dist'][0]:+.4f}, "
        f"rms {cr['rms']:.3f} px")
    log(f"  proyector: f {pr['K'][0, 0]:.1f} px, centro ({pr['K'][0, 2]:.1f}, {pr['K'][1, 2]:.1f}), rms {pr['rms']:.3f} px")
    log(f"  estéreo: rms {srms:.3f} px, baseline {np.linalg.norm(T):.1f} mm")
    per_pose = _per_pose_rms(obj_l, cam_l, pro_l, cr, R, T, pr["K"], pr["dist"])
    for p, r in zip(poses, per_pose):
        r["pose"] = p.name
    report = {
        "method": "hoja carta + ChArUco proyectado" if (use_charuco and not paper_only) else "solo hoja carta",
        "poses": len(poses), "points_per_pose": [int(len(o)) for o in obj_l],
        "model": {"fix_principal": fix_c, "cam_k1": k1c, "proj_k1": bool(proj_k1 and not paper_only),
                  "aspect": "fx = fy", "tangential": 0},
        "rms_px": {"cam": cr["rms"], "proj": pr["rms"], "stereo": srms},
        "cam": {"f": float(cr["K"][0, 0]), "cx": float(cr["K"][0, 2]), "cy": float(cr["K"][1, 2]), "k1": float(cr["dist"][0])},
        "proj": {"f": float(pr["K"][0, 0]), "cx": float(pr["K"][0, 2]), "cy": float(pr["K"][1, 2])},
        "baseline_mm": float(np.linalg.norm(T)),
        "reoriented": reoriented,
        "per_pose": per_pose,
        "paper_check": paper_check(rig, poses, cam_name),
    }
    if paper_only:
        report["warning"] = ("solo 4 puntos por pose: centro óptico fijo (cámara al medio; proyector en "
                             f"{proj_pp or 'el medio'}), sin distorsión. Proyecta el ChArUco para soltar el modelo.")
    # repetibilidad
    rep = {}
    if len(poses) >= 4:
        loo = []
        for i in range(len(poses)):
            keep = [j for j in range(len(poses)) if j != i]
            try:
                c2, p2, _, R2, T2 = _solve([obj_l[j] for j in keep], [cam_l[j] for j in keep], [pro_l[j] for j in keep], *args)
            except cv2.error:
                continue
            d = _rig_delta(_rig_from(cam_name, cam_size, proj_size, c2, p2, R2, T2), rig, cam_name)
            d["sin_pose"] = poses[i].name or str(i)
            loo.append(d)
        if loo:
            rep["leave_one_out"] = loo
            rep["leave_one_out_max"] = {k: float(max(abs(d[k]) for d in loo))
                                        for k in ("proj_pos_mm", "proj_rot_deg", "cam_f_pct", "proj_f_pct")}
    if repeat > 0:
        rng = np.random.default_rng(seed)
        sc = max(cr["rms"], 0.05); sp = max(pr["rms"], 0.05)
        ds = []
        for _ in range(repeat):
            cn = [c + rng.normal(0, sc, c.shape) for c in cam_l]
            pn = [p + rng.normal(0, sp, p.shape) for p in pro_l]
            try:
                c2, p2, _, R2, T2 = _solve(obj_l, cn, pn, *args)
            except cv2.error:
                continue
            ds.append(_rig_delta(_rig_from(cam_name, cam_size, proj_size, c2, p2, R2, T2), rig, cam_name))
        if ds:
            rep["noise_std"] = {k: float(np.std([d[k] for d in ds]) if k.endswith("pct") else np.sqrt(np.mean([d[k] ** 2 for d in ds])))
                                for k in ("proj_pos_mm", "proj_rot_deg", "cam_f_pct", "proj_f_pct")}
            rep["noise_runs"] = len(ds)
    report["repeatability"] = rep
    rig.extra["metric_report"] = _jsonable(report)
    rig.extra["stereo_rms"] = srms
    rig.notes = f"calibrado con hoja carta ({report['method']}), {len(poses)} poses"
    return rig


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return _jsonable(o.tolist())
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def load_pose(folder: Path, board, corners_px: np.ndarray, size_mm=PAPER_MM, skip_bits: int = 2,
              threshold: int = 5, roi=None, name: str | None = None) -> PaperPose:
    """Una carpeta de pose (`pose_NN/<cam>/`) -> PaperPose.

    Usa `map.npz` (o decodifica las imágenes gray code `[AA-BB-CC_NN].png` y guarda el map.npz), la hoja se
    busca en `white.png`, `color.png` o el color del gray code (en ese orden) y el ChArUco proyectado en
    `board.png`. Sin mapa, el plano cámara->proyector sale del propio ChArUco.
    """
    from . import graycode
    folder = Path(folder)
    dec = None
    if (folder / "map.npz").exists():
        dec = Decoded.load(folder / "map.npz")
    else:
        try:
            dec = graycode.decode(graycode.load_sequence(folder), threshold=threshold, skip_bits=skip_bits)
            dec.save(folder / "map.npz")
        except (FileNotFoundError, ValueError):
            dec = None
    board_img = cv2.imread(str(folder / "board.png")) if (folder / "board.png").exists() else None
    quad = None
    for src in ("white.png", "color.png", "paper.png"):
        if (folder / src).exists():
            quad = detect_paper(cv2.imread(str(folder / src)), roi=roi)
            if quad is not None:
                break
    if quad is None and dec is not None:
        quad = detect_paper(dec.color, roi=roi)
    if quad is None:
        raise ValueError(f"{folder}: no encontré la hoja (white.png / color.png / gray code)")
    ch0 = charuco_on_plane(board_img, board, corners_px) if board_img is not None else None
    cam_proj = (ch0["cam"], ch0["proj"]) if ch0 is not None else None
    plane = paper_scale(quad, dec, size_mm, cam_proj=cam_proj)
    ch = charuco_on_plane(board_img, board, corners_px, plane) if board_img is not None else None
    return PaperPose(plane, ch, name or folder.parent.name)


# ============================================================================= malla

@dataclass
class Mesh:
    vertices: np.ndarray            # (n,3) float32
    faces: np.ndarray               # (m,3) int32
    colors: np.ndarray | None = None    # (n,3) uint8 BGR
    normals: np.ndarray | None = None   # (n,3) float32
    method: str = ""


def vertex_normals(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    """Normales por vértice: suma de normales de cara ponderadas por área."""
    n = np.zeros_like(v, dtype=np.float64)
    if len(f):
        fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
        for k in range(3):
            np.add.at(n, f[:, k], fn)
    norm = np.linalg.norm(n, axis=1, keepdims=True)
    return (n / np.maximum(norm, 1e-12)).astype(np.float32)


def _orient_faces(v: np.ndarray, f: np.ndarray, viewpoint) -> np.ndarray:
    """Da vuelta las caras cuya normal no mira hacia `viewpoint` (la cámara): normales hacia afuera de la superficie vista."""
    if len(f) == 0:
        return f
    fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    cen = v[f].mean(axis=1)
    flip = np.einsum("ij,ij->i", fn, np.asarray(viewpoint, np.float64)[None, :] - cen) < 0
    f = f.copy()
    f[flip] = f[flip][:, [0, 2, 1]]
    return f


def _compact(v, f, colors):
    used = np.zeros(len(v), bool)
    used[f.ravel()] = True
    remap = -np.ones(len(v), np.int64)
    remap[used] = np.arange(used.sum())
    return v[used], remap[f].astype(np.int32), (None if colors is None else colors[used])


def grid_mesh(xyz_grid: np.ndarray, color_grid: np.ndarray | None = None, max_edge: float | None = None,
              max_edge_factor: float = 4.0, viewpoint=(0.0, 0.0, 0.0)) -> Mesh:
    """Triangulación de una grilla (h, w, 3) con NaN donde no hay punto (imagen de rango).

    Cada celda 2x2 da dos triángulos (por la diagonal más corta) si sus 4 vértices existen, o uno si existen 3.
    Se descartan triángulos con algún lado > `max_edge` (por defecto `max_edge_factor` x la mediana de los lados
    de la grilla): así no se tienden caras entre un objeto y el fondo. Normales hacia `viewpoint`.
    """
    G = np.asarray(xyz_grid, np.float64)
    h, w = G.shape[:2]
    valid = np.isfinite(G).all(axis=2)
    idx = -np.ones((h, w), np.int64)
    idx[valid] = np.arange(valid.sum())
    V = G[valid]
    C = None if color_grid is None else np.clip(np.asarray(color_grid)[valid], 0, 255).astype(np.uint8)
    a, b = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()        # a b
    c, d = idx[1:, :-1].ravel(), idx[1:, 1:].ravel()          # c d
    tris = []
    full = (a >= 0) & (b >= 0) & (c >= 0) & (d >= 0)
    if full.any():
        A, B, Cc, D = a[full], b[full], c[full], d[full]
        diag_ad = np.linalg.norm(V[A] - V[D], axis=1) <= np.linalg.norm(V[B] - V[Cc], axis=1)
        t1 = np.where(diag_ad[:, None], np.column_stack([A, B, D]), np.column_stack([A, B, Cc]))
        t2 = np.where(diag_ad[:, None], np.column_stack([A, D, Cc]), np.column_stack([B, D, Cc]))
        tris += [t1, t2]
    three = ~full & ((a >= 0).astype(int) + (b >= 0) + (c >= 0) + (d >= 0) == 3)
    if three.any():
        q = np.column_stack([a[three], b[three], d[three], c[three]])          # ciclo a b d c
        tris.append(q[q >= 0].reshape(-1, 3))                                 # cada fila tiene justo 3, en orden
    if not tris:
        return Mesh(np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int32), method="grid")
    F = np.concatenate(tris)
    e = np.stack([np.linalg.norm(V[F[:, i]] - V[F[:, (i + 1) % 3]], axis=1) for i in range(3)], axis=1)
    if max_edge is None:
        horiz = np.linalg.norm(V[a[(a >= 0) & (b >= 0)]] - V[b[(a >= 0) & (b >= 0)]], axis=1)
        max_edge = max_edge_factor * float(np.median(horiz)) if len(horiz) else np.inf
    F = F[(e < max_edge).all(axis=1)]
    F = _orient_faces(V, F, viewpoint)
    V, F, C = _compact(V, F, C)
    return Mesh(V.astype(np.float32), F, C, vertex_normals(V, F), "grid")


def _auto_step(coords: np.ndarray) -> int:
    u = np.unique(np.round(coords).astype(np.int64))
    if len(u) < 2:
        return 1
    dif = np.diff(u)
    vals, cnt = np.unique(dif[dif > 0], return_counts=True)
    return int(vals[np.argmax(cnt)]) if len(vals) else 1


def points_to_grid(xyz: np.ndarray, color: np.ndarray | None, pro_xy: np.ndarray, step: int | None = None):
    """Puntos con su px de proyector -> grilla (h, w, 3) (NaN donde no hay) + grilla de color. Paso automático
    (p.ej. 4 con skip_bits=2)."""
    pro = np.asarray(pro_xy, np.float64)
    step = step or max(_auto_step(pro[:, 0]), 1)
    o = pro.min(axis=0)
    ij = np.round((pro - o) / step).astype(np.int64)
    w, h = ij[:, 0].max() + 1, ij[:, 1].max() + 1
    G = np.full((h, w, 3), np.nan)
    G[ij[:, 1], ij[:, 0]] = xyz
    Cg = None
    if color is not None:
        Cg = np.zeros((h, w, 3), np.float32)
        Cg[ij[:, 1], ij[:, 0]] = color
    return G, Cg, step


def _delaunay_mesh(xyz, color, max_edge_factor, viewpoint) -> Mesh:
    """Respaldo sin grilla ni Open3D: Delaunay 2D sobre el plano principal de la nube (sirve para relieves)."""
    from scipy.spatial import Delaunay
    X = np.asarray(xyz, np.float64)
    Xc = X - X.mean(axis=0)
    _, _, vt = np.linalg.svd(Xc[:: max(1, len(Xc) // 20000)], full_matrices=False)
    uv = Xc @ vt[:2].T
    F = Delaunay(uv).simplices.astype(np.int64)
    e = np.stack([np.linalg.norm(X[F[:, i]] - X[F[:, (i + 1) % 3]], axis=1) for i in range(3)], axis=1)
    F = F[(e < max_edge_factor * np.median(e)).all(axis=1)]
    F = _orient_faces(X, F, viewpoint)
    C = None if color is None else np.clip(color, 0, 255).astype(np.uint8)
    V, F, C = _compact(X, F, C)
    return Mesh(V.astype(np.float32), F, C, vertex_normals(V, F), "delaunay")


def _open3d_mesh(xyz, color, viewpoint, depth, trim_quantile) -> Mesh:
    import open3d as o3d
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.asarray(xyz, np.float64)))
    if color is not None:
        pcd.colors = o3d.utility.Vector3dVector(np.clip(np.asarray(color, np.float64)[:, ::-1] / 255.0, 0, 1))
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamKNN(30))
    pcd.orient_normals_towards_camera_location(np.asarray(viewpoint, np.float64))
    mesh, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=depth)
    dens = np.asarray(dens)
    mesh.remove_vertices_by_mask(dens < np.quantile(dens, trim_quantile))
    mesh = mesh.crop(pcd.get_axis_aligned_bounding_box())
    mesh.compute_vertex_normals()
    V = np.asarray(mesh.vertices, np.float32)
    F = np.asarray(mesh.triangles, np.int32)
    C = None
    if mesh.has_vertex_colors():
        C = np.clip(np.asarray(mesh.vertex_colors)[:, ::-1] * 255, 0, 255).astype(np.uint8)
    return Mesh(V, F, C, np.asarray(mesh.vertex_normals, np.float32), "open3d-poisson")


def have_open3d() -> bool:
    try:
        import open3d  # noqa: F401
        return True
    except Exception:
        return False


def cloud_to_mesh(xyz: np.ndarray, color: np.ndarray | None = None, pro_xy: np.ndarray | None = None,
                  step: int | None = None, method: str = "auto", max_edge: float | None = None,
                  max_edge_factor: float = 4.0, viewpoint=(0.0, 0.0, 0.0), poisson_depth: int = 9,
                  trim_quantile: float = 0.02) -> Mesh:
    """Nube (n,3) mm [+ color BGR] -> malla con caras, normales y color.

    method: "poisson" (Open3D, si está instalado), "grid" (triangulación en el espacio del proyector: necesita
    `pro_xy`, el px de proyector de cada punto, que el escaneo siempre tiene), "delaunay" (respaldo genérico)
    o "auto" (Open3D si está; si no, grid si hay `pro_xy`; si no, delaunay).
    """
    xyz = np.asarray(xyz, np.float64)
    ok = np.isfinite(xyz).all(axis=1)
    xyz = xyz[ok]
    color = None if color is None else np.asarray(color)[ok]
    pro_xy = None if pro_xy is None else np.asarray(pro_xy)[ok]
    if method == "auto":
        method = "poisson" if have_open3d() else ("grid" if pro_xy is not None else "delaunay")
    if method == "poisson":
        return _open3d_mesh(xyz, color, viewpoint, poisson_depth, trim_quantile)
    if method == "grid":
        if pro_xy is None:
            raise ValueError("method='grid' necesita pro_xy (px de proyector de cada punto)")
        G, Cg, _ = points_to_grid(xyz, color, pro_xy, step)
        return grid_mesh(G, Cg, max_edge, max_edge_factor, viewpoint)
    if method == "delaunay":
        return _delaunay_mesh(xyz, color, max_edge_factor, viewpoint)
    raise ValueError(method)


def write_mesh_ply(path, mesh: Mesh, comment: str = "slscan metric") -> Path:
    """PLY binario con vértices (xyz, normales, color) y caras (triángulos)."""
    v = np.asarray(mesh.vertices, np.float32)
    f = np.asarray(mesh.faces, np.int32)
    comment = comment.encode("ascii", "replace").decode("ascii").replace("\n", " ")     # el encabezado PLY es ASCII
    header = ["ply", "format binary_little_endian 1.0", f"comment {comment}", f"element vertex {len(v)}",
              "property float x", "property float y", "property float z"]
    fields = [("x", "<f4"), ("y", "<f4"), ("z", "<f4")]
    if mesh.normals is not None:
        header += ["property float nx", "property float ny", "property float nz"]
        fields += [("nx", "<f4"), ("ny", "<f4"), ("nz", "<f4")]
    if mesh.colors is not None:
        header += ["property uchar red", "property uchar green", "property uchar blue"]
        fields += [("red", "u1"), ("green", "u1"), ("blue", "u1")]
    header += [f"element face {len(f)}", "property list uchar int vertex_indices", "end_header"]
    rec = np.empty(len(v), dtype=fields)
    rec["x"], rec["y"], rec["z"] = v[:, 0], v[:, 1], v[:, 2]
    if mesh.normals is not None:
        n = np.asarray(mesh.normals, np.float32)
        rec["nx"], rec["ny"], rec["nz"] = n[:, 0], n[:, 1], n[:, 2]
    if mesh.colors is not None:
        c = np.asarray(mesh.colors, np.uint8)
        rec["red"], rec["green"], rec["blue"] = c[:, 2], c[:, 1], c[:, 0]
    frec = np.empty(len(f), dtype=[("n", "u1"), ("i", "<i4", (3,))])
    frec["n"] = 3
    frec["i"] = f
    path = Path(path)
    with open(path, "wb") as fh:
        fh.write(("\n".join(header) + "\n").encode("ascii"))
        fh.write(rec.tobytes())
        fh.write(frec.tobytes())
    return path


def read_mesh_ply(path) -> Mesh:
    """Lee lo que escribe `write_mesh_ply` (binario little endian, triángulos)."""
    with open(path, "rb") as fh:
        header = []
        while True:
            line = fh.readline().decode("ascii", "replace").strip()
            header.append(line)
            if line == "end_header":
                break
        nv = nf = 0
        props = []
        cur = None
        for l in header:
            p = l.split()
            if p[:2] == ["element", "vertex"]:
                nv, cur = int(p[2]), "v"
            elif p[:2] == ["element", "face"]:
                nf, cur = int(p[2]), "f"
            elif p and p[0] == "property" and cur == "v":
                props.append((p[2], "<f4" if p[1] == "float" else "u1"))
        rec = np.frombuffer(fh.read(nv * np.dtype(props).itemsize), dtype=props, count=nv)
        frec = np.frombuffer(fh.read(nf * 13), dtype=[("n", "u1"), ("i", "<i4", (3,))], count=nf)
    names = rec.dtype.names
    v = np.stack([rec["x"], rec["y"], rec["z"]], axis=1)
    n = np.stack([rec["nx"], rec["ny"], rec["nz"]], axis=1) if "nx" in names else None
    c = np.stack([rec["blue"], rec["green"], rec["red"]], axis=1) if "red" in names else None
    return Mesh(v, frec["i"].astype(np.int32), c, n, "ply")


def render_mesh_png(mesh: Mesh, out, yaw: float = 20, pitch: float = -25, title: str = "", size=(1600, 1200),
                    point_px: int | None = None) -> np.ndarray:
    """Captura PNG de la malla con `viewer.render_png`: vértices con su color sombreado por la normal (Lambert),
    pasados del marco de la cámara (y abajo, z adelante) a uno de pantalla (y arriba, z hacia el que mira).
    El tamaño de punto por defecto tapa el espaciado de la malla en pantalla (se ve como superficie)."""
    from .viewer import render_png, _rotation
    v = np.asarray(mesh.vertices, np.float64) * np.array([1, -1, -1])
    if point_px is None:
        point_px = 2
        if len(mesh.faces) and len(v) > 3:
            f = mesh.faces[:: max(1, len(mesh.faces) // 20000)]
            edge = float(np.median(np.linalg.norm(v[f[:, 0]] - v[f[:, 1]], axis=1)))
            p = (v - np.median(v, axis=0)) @ _rotation(yaw, pitch).T     # misma escala que usa render_png
            span = float((np.percentile(p[:, :2], 99, axis=0) - np.percentile(p[:, :2], 1, axis=0)).max())
            point_px = int(np.clip(np.ceil(edge * 0.9 * min(size) / max(span, 1e-6)) + 1, 2, 8))
    base = (np.full((len(v), 3), 200, np.float64) if mesh.colors is None else mesh.colors[:, ::-1].astype(np.float64))
    if mesh.normals is not None:
        n = np.asarray(mesh.normals, np.float64) * np.array([1, -1, -1])
        L = np.array([0.3, 0.5, 0.8]); L /= np.linalg.norm(L)
        shade = 0.35 + 0.65 * np.clip(n @ L, 0, 1)
        base = base * shade[:, None]
    return render_png(v.astype(np.float32), np.clip(base, 0, 255).astype(np.uint8), out, size=size, yaw=yaw,
                      pitch=pitch, point_px=point_px, title=title)


# ============================================================================= relativo -> métrico

def scan_correspondences(proj_to_cam=None, dec: Decoded | None = None, step: int = 1, offset: float | None = None):
    """Correspondencias del escaneo: (índice de proyector (n,2), px cámara (n,2), px proyector (n,2)).

    De `proj_to_cam` (dict o npz con mapx, mapy, valid a resolución del proyector, muestreado cada `step` px)
    o, si no hay, del mapa denso (`recon.coalesce`: un punto por código, en bloques si hubo skip_bits; `step`
    no aplica). El px de proyector = índice + `offset`; por defecto 0 para proj_to_cam (se interpola sobre los
    códigos tal cual) y, para el mapa, el centro del código (`proj_coords`).
    """
    if proj_to_cam is not None:
        z = proj_to_cam
        keys = z.files if hasattr(z, "files") else list(z)
        mx, my = np.asarray(z["mapx"], np.float64), np.asarray(z["mapy"], np.float64)
        valid = np.asarray(z["valid"], bool) if "valid" in keys else np.ones(mx.shape, bool)
        H, W = mx.shape
        ys, xs = np.mgrid[0:H:step, 0:W:step]
        ok = valid[ys, xs] & np.isfinite(mx[ys, xs]) & np.isfinite(my[ys, xs])
        idx = np.column_stack([xs[ok], ys[ok]]).astype(np.float64)
        cam = np.column_stack([mx[ys, xs][ok], my[ys, xs][ok]])
        return idx, cam, idx + (0.0 if offset is None else offset)
    if dec is not None:
        from .recon import coalesce
        m = coalesce(dec)
        idx = m.pro.astype(np.float64)
        pro = np.column_stack(proj_coords(dec, idx[:, 0], idx[:, 1])) if offset is None else idx + offset
        return idx, m.cam.astype(np.float64), pro
    raise ValueError("hace falta proj_to_cam o el mapa denso (dec)")


def fit_plane_ransac(X: np.ndarray, thr: float = 3.0, iters: int = 400, seed: int = 0):
    """Plano dominante (n, d) con n·x + d = 0 por RANSAC + ajuste por SVD de los inliers."""
    rng = np.random.default_rng(seed)
    best, best_n = None, -1
    for _ in range(iters):
        p = X[rng.choice(len(X), 3, replace=False)]
        n = np.cross(p[1] - p[0], p[2] - p[0])
        nn = np.linalg.norm(n)
        if nn < 1e-9:
            continue
        n /= nn
        cnt = int((np.abs((X - p[0]) @ n) < thr).sum())
        if cnt > best_n:
            best, best_n = (n, -n @ p[0]), cnt
    n, d = best
    inl = np.abs(X @ n + d) < thr
    c = X[inl].mean(axis=0)
    _, _, vt = np.linalg.svd(X[inl] - c, full_matrices=False)
    n = vt[2]
    return n, -n @ c, inl


def relative_to_metric(height_proj: np.ndarray | None, rig: Rig | None, proj_to_cam=None, dec: Decoded | None = None,
                       cam_name: str | None = None, step: int = 1, plane_thr_mm: float = 3.0,
                       offset: float | None = None) -> dict | None:
    """Del mapa relativo al métrico: con un rig, triangula las correspondencias proyector<->cámara del escaneo.

    Devuelve None sin rig. Con rig: dict con `xyz` (n,3) mm en el marco de la cámara, `pro` (n,2) px de proyector,
    `cam` (n,2), `xyz_grid` (h, w, 3) por píxel de proyector (cada `step` px; con el mapa denso, cada bloque de
    skip_bits), `height_mm` (h, w): altura sobre el plano dominante (positiva hacia la cámara), `plane` (n, d) y,
    si se da `height_proj`, `mm_per_unit`: mm por unidad de la altura relativa (ajuste height_mm ≈ a·height_proj;
    negativo si las convenciones de signo difieren) y su correlación. `offset`: ver `scan_correspondences`.
    """
    if rig is None:
        return None
    from .recon import Matches, triangulate_pair
    if proj_to_cam is None and dec is not None and step == 1:
        step = block_px(dec)                        # un vértice por bloque de código
    pro_idx, cam, pro = scan_correspondences(proj_to_cam, dec, step, offset)
    cdev = rig[cam_name] if cam_name else rig.cameras()[0]
    m = Matches(cam.astype(np.float32), pro.astype(np.float32), np.zeros((len(cam), 3), np.float32))
    xyz = triangulate_pair(m, cdev, rig.projector(), mode="world").astype(np.float64)
    ok = np.isfinite(xyz).all(axis=1) & (xyz[:, 2] > 0)
    xyz, pro, pro_idx, cam = xyz[ok], pro[ok], pro_idx[ok], cam[ok]
    if len(xyz) < 3:
        raise ValueError("la triangulación no dejó puntos delante de la cámara: ¿rig de otra sesión?")
    W, H = rig.projector().size
    gi = np.floor(pro_idx / step).astype(np.int64)
    gw, gh = (W + step - 1) // step, (H + step - 1) // step
    inb = (gi[:, 0] >= 0) & (gi[:, 0] < gw) & (gi[:, 1] >= 0) & (gi[:, 1] < gh)
    G = np.full((gh, gw, 3), np.nan)
    G[gi[inb, 1], gi[inb, 0]] = xyz[inb]
    n, d, inl = fit_plane_ransac(xyz, plane_thr_mm)
    if d < 0:                        # la cámara (origen) da n·0 + d = d: que quede del lado positivo
        n, d = -n, -d
    hgt = xyz @ n + d                # altura > 0 = hacia la cámara
    out = {"xyz": xyz, "pro": pro, "cam": cam, "xyz_grid": G, "plane": (n, float(d)), "plane_inliers": int(inl.sum()),
           "step": step}
    Hm = np.full((gh, gw), np.nan)
    Hm[gi[inb, 1], gi[inb, 0]] = hgt[inb]
    if height_proj is not None:
        hp = np.asarray(height_proj, np.float64)
        px_ = np.clip(np.round(pro_idx[:, 0]).astype(int), 0, hp.shape[1] - 1)
        py_ = np.clip(np.round(pro_idx[:, 1]).astype(int), 0, hp.shape[0] - 1)
        rel = hp[py_, px_]
        good = np.isfinite(rel) & np.isfinite(hgt)
        if good.sum() > 10 and np.dot(rel[good], rel[good]) > 0:
            a = float(np.dot(rel[good], hgt[good]) / np.dot(rel[good], rel[good]))     # hgt ≈ a * rel
            out["mm_per_unit"] = a
            out["relative_corr"] = float(np.corrcoef(rel[good], hgt[good])[0, 1]) if good.sum() > 2 else float("nan")
    out["height_mm"] = Hm
    return out


def relative_mesh(height_proj: np.ndarray, color_proj: np.ndarray | None = None, step: int = 4,
                  z_scale: float = 1.0, mm_per_px: float | None = None) -> Mesh:
    """Sin rig: malla RELATIVA del mapa de altura en el espacio del proyector (x, y en px de proyector, o en mm
    del plano de la hoja si se da `mm_per_px`; z = altura relativa x `z_scale`). No es métrica en z."""
    hp = np.asarray(height_proj, np.float64)[::step, ::step]
    h, w = hp.shape
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64) * step
    s = mm_per_px or 1.0
    G = np.dstack([xs * s, ys * s, -hp * z_scale])      # marco tipo cámara: z hacia adentro, la altura sale hacia el que mira
    G[~np.isfinite(hp)] = np.nan
    C = None if color_proj is None else np.asarray(color_proj)[::step, ::step]
    m = grid_mesh(G, C, viewpoint=(w * step * s / 2, h * step * s / 2, -1e6))
    m.method = "grid-relativa"
    return m
