"""Reconstrucción offline medida, en mm. No usa mapas densificados ni hardware.

OpenCV: centros de píxel enteros; X_device = R X_world + t. El código
Gray identifica una celda, no una coordenada subpíxel medida. Su centro y
su cuantización se conservan por separado. Ver docs/P3-MALLA.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntFlag
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import cv2
import numpy as np
from scipy import ndimage

from .graycode import Decoded
from .recon import _undistort, write_ply
from .rig import Device, Rig

if importlib.util.find_spec(f"{__package__}.contracts") is not None:
    from . import contracts as _contracts
else:
    # P6 vive en otra rama hasta integración. No se copia ni modifica su módulo.
    _contracts = None



REMAP_BLOCK = 16000     # cv2.remap rechaza mapas con más de 32767 filas: se muestrea por bloques (hallazgo de h6)


def sample_color(image: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Color bilineal de `image` en los puntos (x, y) de cámara, en bloques para que cv2.remap no falle con nubes grandes."""
    pts = np.asarray(pts, np.float32).reshape(-1, 2)
    if not len(pts):
        return np.zeros((0, image.shape[2] if image.ndim == 3 else 1), image.dtype)
    blocks = max(1, int(np.ceil(len(pts) / REMAP_BLOCK)))
    return np.concatenate([cv2.remap(image, xy[:, 0].reshape(-1, 1), xy[:, 1].reshape(-1, 1), cv2.INTER_LINEAR)
                           .reshape(len(xy), -1) for xy in np.array_split(pts, blocks)])

def metadata(artifact: str, evidence: str, *, units="mm", input_hashes=None, **extra) -> dict:
    if evidence not in ("synthetic", "recorded", "live"):
        raise ValueError("evidence debe ser synthetic/recorded/live")
    base = (_contracts.header(artifact, evidence, units, input_hashes) if _contracts else
            dict(schema_version="1.0", artifact=artifact, evidence=evidence,
                 units=units, input_hashes=input_hashes or {}))
    return dict(base, pixel_convention="opencv_integer_centers", **extra)


def save_npz(path, meta, **arrays):
    if _contracts is not None:
        return _contracts.write_npz(path, meta, arrays)
    np.savez_compressed(path, metadata=np.array(json.dumps(meta, allow_nan=False)), **arrays)


def save_json(path, data):
    if _contracts is not None and data["artifact"] in _contracts.SCHEMAS:
        return _contracts.atomic_json(path, data)
    Path(path).write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def sha256(path) -> str:
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@dataclass
class MeasuredMatches:
    cam: np.ndarray                   # N,2 float64, centros OpenCV
    pro: np.ndarray                   # N,2 float64, centro de celda Gray
    color: np.ndarray                 # N,3 BGR, apariencia
    cells: np.ndarray                 # N,2 int, índice de celda del proyector
    source_index: np.ndarray          # N, píxel de cámara representativo (y*w+x)
    count: np.ndarray                 # N, píxeles agrupados (no muestras independientes)
    spread_px: np.ndarray             # N, radio máximo respecto a la mediana
    confidence: np.ndarray            # N, mínimo contraste por bit / 255; NaN si desconocido
    sigma_px: np.ndarray              # N,4: cx,cy,px,py; sin covarianza del rig
    masks: dict[str, np.ndarray]      # grilla cámara; occluded es solo evidencia externa
    source_match: np.ndarray          # grilla cámara -> índice de correspondencia; -1 rechazado
    projector_size: tuple[int, int]
    camera_size: tuple[int, int]
    step: int
    bit_contrast: np.ndarray | None = None
    decoder_stats: dict | None = None


def measured_matches(dec: Decoded, projector_size: tuple[int, int], *,
                     max_spread_px: float = 6.0, camera_sigma_px: float = 0.25) -> MeasuredMatches:
    """Agrupa por código; rechaza grupos dispersos o desconectados (8 vecinos).

    No escoge silenciosamente una isla de un código ambiguo. El centro de cámara
    es la mediana, el color se toma en una muestra real cercana a esa mediana.
    El mapa source_match conserva todos los píxeles que respaldan cada muestra.
    """
    if dec.stats.get("decoder_version") != "slscan-gray/2":
        raise ValueError("caché Gray legacy: re-decodificar los crudos con projector_size y retain_contrast=True")
    if dec.stats.get("pixel_convention") != "opencv_integer_centers":
        raise ValueError("convención de píxel incompatible")
    if tuple(dec.stats.get("projector_size", ())) != tuple(projector_size):
        raise ValueError("tamaño del proyector ausente o incompatible; re-decodificar")
    if max_spread_px <= 0 or camera_sigma_px <= 0:
        raise ValueError("spread/sigma deben ser positivos")
    w, h = dec.size
    if dec.px.shape != (h, w) or dec.py.shape != (h, w) or dec.color.shape != (h, w, 3):
        raise ValueError("arrays no corresponden al tamaño de cámara")
    step = 1 << int(dec.stats.get("skip_bits", 0))
    pw, ph = projector_size
    valid = dec.valid.copy()
    ys, xs = np.nonzero(valid)
    cells = np.column_stack((dec.px[valid] // step, dec.py[valid] // step)).astype(np.int32)
    grid_w = (pw + step - 1) // step
    order = np.argsort(cells[:, 1] * grid_w + cells[:, 0], kind="stable")
    xs, ys, cells = xs[order], ys[order], cells[order]
    keys = cells[:, 1] * grid_w + cells[:, 0]
    starts = np.r_[0, np.flatnonzero(np.diff(keys)) + 1, len(keys)] if len(keys) else [0]
    cam, pro, colors, kept_cells, sources, counts, spreads, confidence, sigmas = ([] for _ in range(9))
    source_match = np.full((h, w), -1, np.int32)
    measured = np.zeros((h, w), bool)
    rejected = np.zeros((h, w), bool)
    contrast = None
    if dec.bit_contrast is not None:
        if dec.bit_contrast.shape != (len(dec.stats["bit_order"]), h, w):
            raise ValueError("bit_contrast no corresponde a bit_order/cámara")
        contrast = dec.bit_contrast.min(axis=0) / 255.0
    for a, b in zip(starts[:-1], starts[1:]):
        xx, yy = xs[a:b], ys[a:b]
        coords = np.column_stack((xx, yy))
        center = np.median(coords, axis=0)
        spread = np.linalg.norm(coords - center, axis=1).max()
        if spread > max_spread_px:
            rejected[yy, xx] = True
            continue
        local = np.zeros((np.ptp(yy) + 1, np.ptp(xx) + 1), bool)
        local[yy - yy.min(), xx - xx.min()] = True
        if ndimage.label(local, structure=np.ones((3, 3)))[1] != 1:
            rejected[yy, xx] = True
            continue
        cell = cells[a]
        origin = cell * step
        extent = np.minimum(step, np.array(projector_size) - origin)
        rep = np.argmin(np.linalg.norm(coords - center, axis=1))
        source_match[yy, xx] = len(cam)
        measured[yy, xx] = True
        cam.append(center)
        pro.append(origin + (extent - 1) / 2.0)
        colors.append(dec.color[yy[rep], xx[rep]])
        kept_cells.append(cell)
        sources.append(yy[rep] * w + xx[rep])
        counts.append(b - a)
        spreads.append(spread)
        confidence.append(float(contrast[yy, xx].min()) if contrast is not None else np.nan)
        # No dividir la cuantización por sqrt(count): no son disparidades independientes.
        sigmas.append([camera_sigma_px, camera_sigma_px, *(extent / np.sqrt(12))])
    return MeasuredMatches(
        np.asarray(cam, float).reshape(-1, 2), np.asarray(pro, float).reshape(-1, 2),
        np.asarray(colors, np.float32).reshape(-1, 3), np.asarray(kept_cells, np.int32).reshape(-1, 2),
        np.asarray(sources, np.int64), np.asarray(counts, np.int32), np.asarray(spreads, np.float32),
        np.asarray(confidence, np.float32), np.asarray(sigmas, float).reshape(-1, 4),
        dict(measured=measured, interpolated=np.zeros_like(valid), occluded=np.zeros_like(valid),
             invalid=~valid, rejected=rejected, unobserved=~valid),
        source_match, tuple(projector_size), tuple(dec.size), step, dec.bit_contrast, dec.stats)


class Reject(IntFlag):
    NONFINITE = 1
    OUT_OF_BOUNDS = 2
    CONFIDENCE = 4
    CHEIRALITY = 8
    REPROJECTION = 16
    RAY_ANGLE = 32
    RAY_GAP = 64
    RANGE = 128
    UNCERTAINTY = 256


@dataclass(frozen=True)
class Filters:
    min_confidence: float = 0.02
    max_reprojection_px: float = 1.0
    min_ray_angle_deg: float = 1.0
    max_ray_gap_mm: float = 2.0
    min_depth_mm: float = 50.0
    max_depth_mm: float = 5000.0
    max_sigma_mm: float = 2.0

    def __post_init__(self):
        values = np.array(list(asdict(self).values()))
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError("filtros deben ser finitos y no negativos")
        if self.min_depth_mm >= self.max_depth_mm or self.min_ray_angle_deg >= 90 or self.min_confidence > 1:
            raise ValueError("rango/ángulo/confianza inválido")


@dataclass
class CloudQuality:
    xyz: np.ndarray                    # todos los candidatos, incluso rechazados
    accepted: np.ndarray
    rejection: np.ndarray              # bitmask Reject, 0=aceptado
    depth_mm: np.ndarray               # N,2 cámara/proyector
    reprojection_px: np.ndarray        # N,2 norma euclidiana
    ray_angle_deg: np.ndarray
    ray_gap_mm: np.ndarray
    sigma_mm: np.ndarray               # sqrt(lambda_max(J diag(sigma_px²) Jᵀ))


def _triangulate(cxy, pxy, cam, pro):
    cu, pu = _undistort(cxy, cam), _undistort(pxy, pro)
    Pc = np.column_stack((cam.R, cam.t))
    Pp = np.column_stack((pro.R, pro.t))
    X = cv2.triangulatePoints(Pc, Pp, cu.T, pu.T)
    with np.errstate(divide="ignore", invalid="ignore"):
        xyz = (X[:3] / X[3]).T
    return xyz, cu, pu


def _project(xyz, dev):
    return cv2.projectPoints(np.ascontiguousarray(xyz, dtype=float), cv2.Rodrigues(dev.R)[0],
                             dev.t.astype(float), dev.K, dev.dist)[0].reshape(-1, 2)


def _validate_device(dev):
    if (len(dev.size) != 2 or min(dev.size) <= 0 or dev.K.shape != (3, 3)
            or not np.isfinite(dev.K).all() or not np.isfinite(dev.dist).all()
            or not np.isfinite(dev.t).all() or not np.isfinite(dev.R).all()
            or min(dev.K[0, 0], dev.K[1, 1]) <= 0
            or not np.allclose(dev.R.T @ dev.R, np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(dev.R), 1, atol=1e-6)):
        raise ValueError(f"dispositivo inválido: {dev.name}")


def triangulate_measured(m: MeasuredMatches, cam: Device, pro: Device,
                         filters: Filters = Filters(), chunk_size: int = 16384) -> CloudQuality:
    """Rechazo en ambos dispositivos y propagación numérica en mundo arbitrario.

    sigma_mm incluye solo incertidumbre de correspondencia, NO del rig ni sesgo
    sistemático. El umbral es un presupuesto de ruido; nunca un certificado ≤2 mm.
    """
    for dev in (cam, pro):
        _validate_device(dev)
    if tuple(cam.size) != m.camera_size or tuple(pro.size) != m.projector_size:
        raise ValueError("rig y correspondencias tienen tamaños incompatibles")
    if np.linalg.norm(cam.center_world() - pro.center_world()) < 1e-6:
        raise ValueError("baseline degenerado")
    if chunk_size < 1:
        raise ValueError("chunk_size debe ser positivo")
    n = len(m.cam)
    xyz = np.full((n, 3), np.nan)
    depths, reproj = np.full((n, 2), np.nan), np.full((n, 2), np.nan)
    angle, gap, sigma = (np.full(n, np.nan) for _ in range(3))
    rejection = np.zeros(n, np.uint16)
    finite = np.isfinite(m.cam).all(1) & np.isfinite(m.pro).all(1)
    finite &= np.isfinite(m.sigma_px).all(1) & (m.sigma_px > 0).all(1)
    rejection[~finite] |= int(Reject.NONFINITE)
    for pts, dev in ((m.cam, cam), (m.pro, pro)):
        rejection[((pts < 0) | (pts > np.array(dev.size) - 1)).any(1)] |= int(Reject.OUT_OF_BOUNDS)
    rejection[~np.isfinite(m.confidence) | (m.confidence < filters.min_confidence)] |= int(Reject.CONFIDENCE)
    ids = np.flatnonzero(finite & ((rejection & Reject.OUT_OF_BOUNDS) == 0))
    baseline = pro.center_world() - cam.center_world()
    for start in range(0, len(ids), chunk_size):
        ix = ids[start:start + chunk_size]
        cxy, pxy = m.cam[ix], m.pro[ix]
        X, cu, pu = _triangulate(cxy, pxy, cam, pro)
        xyz[ix] = X
        dc = np.column_stack((cu, np.ones(len(ix)))) @ cam.R
        dp = np.column_stack((pu, np.ones(len(ix)))) @ pro.R
        dc /= np.linalg.norm(dc, axis=1)[:, None]
        dp /= np.linalg.norm(dp, axis=1)[:, None]
        cross = np.cross(dc, dp)
        sin = np.linalg.norm(cross, axis=1)
        angle[ix] = np.degrees(np.arcsin(np.clip(sin, 0, 1)))
        gap[ix] = np.abs(cross @ baseline) / np.maximum(sin, 1e-15)
        depths[ix] = np.column_stack(((X @ cam.R.T + cam.t)[:, 2], (X @ pro.R.T + pro.t)[:, 2]))
        good = np.isfinite(X).all(1)
        if good.any():
            reproj[ix[good]] = np.column_stack((
                np.linalg.norm(_project(X[good], cam) - cxy[good], axis=1),
                np.linalg.norm(_project(X[good], pro) - pxy[good], axis=1)))
        # Central differences, double precision; no float32 cancellation at 0.001 px.
        jac = np.empty((len(ix), 3, 4))
        for k in range(4):
            delta = np.zeros(4); delta[k] = 1e-3
            plus = _triangulate(cxy + delta[:2], pxy + delta[2:], cam, pro)[0]
            minus = _triangulate(cxy - delta[:2], pxy - delta[2:], cam, pro)[0]
            with np.errstate(invalid="ignore"):
                jac[:, :, k] = (plus - minus) / 2e-3 * m.sigma_px[ix, k, None]
        good_j = np.isfinite(jac).all(axis=(1, 2))
        if good_j.any():
            j = jac[good_j]
            sigma[ix[good_j]] = np.sqrt(np.maximum(0, np.linalg.eigvalsh(j @ j.transpose(0, 2, 1))[:, -1]))
    rejection[~np.isfinite(xyz).all(1)] |= int(Reject.NONFINITE)
    rejection[(depths <= 0).any(1)] |= int(Reject.CHEIRALITY)
    rejection[~np.isfinite(reproj).all(1) | (reproj > filters.max_reprojection_px).any(1)] |= int(Reject.REPROJECTION)
    rejection[~np.isfinite(angle) | (angle < filters.min_ray_angle_deg)] |= int(Reject.RAY_ANGLE)
    rejection[~np.isfinite(gap) | (gap > filters.max_ray_gap_mm)] |= int(Reject.RAY_GAP)
    rejection[((depths < filters.min_depth_mm) | (depths > filters.max_depth_mm)).any(1)] |= int(Reject.RANGE)
    rejection[~np.isfinite(sigma) | (sigma > filters.max_sigma_mm)] |= int(Reject.UNCERTAINTY)
    return CloudQuality(xyz, rejection == 0, rejection, depths, reproj, angle, gap, sigma)


@dataclass(frozen=True)
class MeshLimits:
    max_edge_mm: float = 50.0
    max_depth_jump_mm: float = 20.0
    max_normal_angle_deg: float = 60.0

    def __post_init__(self):
        if not all(np.isfinite(v) and v > 0 for v in asdict(self).values()) or self.max_normal_angle_deg > 180:
            raise ValueError("límites de malla inválidos")


@dataclass
class TexturedMesh:
    vertices: np.ndarray
    faces: np.ndarray
    normals: np.ndarray
    color: np.ndarray                 # BGR
    uv: np.ndarray                    # origen superior izquierdo, (cam_xy+0.5)/(w,h)
    sample_index: np.ndarray          # índice original de match; cloud_index por accepted
    grid_sample: np.ndarray           # -1 si celda ausente o rechazada
    texture_valid: np.ndarray         # prueba de visibilidad en cámara, por vértice


def grid_mesh(m: MeasuredMatches, q: CloudQuality, cam: Device,
              limits: MeshLimits = MeshLimits()) -> TexturedMesh:
    """Solo quads completos de celdas vecinas medidas. Nunca Delaunay ni relleno.

    Corta aristas largas, saltos de profundidad en ambos dispositivos y pliegues
    incompatibles entre caras adyacentes. Winding mira hacia la cámara física.
    """
    w, h = m.projector_size
    grid = np.full(((h + m.step - 1) // m.step, (w + m.step - 1) // m.step), -1, np.int32)
    sample = np.flatnonzero(q.accepted)
    if len(np.unique(m.cells, axis=0)) != len(m.cells):
        raise ValueError("más de una muestra por celda")
    grid[m.cells[sample, 1], m.cells[sample, 0]] = np.arange(len(sample))
    vertices = q.xyz[sample]
    quads = np.stack((grid[:-1, :-1], grid[:-1, 1:], grid[1:, 1:], grid[1:, :-1]), axis=-1).reshape(-1, 4)
    quads = quads[(quads >= 0).all(1)]
    faces = np.concatenate((quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]), axis=0)
    if len(faces):
        tri = vertices[faces]
        edges = tri - np.roll(tri, 1, axis=1)
        depths = q.depth_mm[sample][faces]
        keep = (np.linalg.norm(edges, axis=2) <= limits.max_edge_mm).all(1)
        keep &= (np.ptp(depths, axis=1) <= limits.max_depth_jump_mm).all(1)
        cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        keep &= np.linalg.norm(cross, axis=1) > 1e-10
        faces, cross = faces[keep], cross[keep]
        centers = vertices[faces].mean(axis=1)
        flip = np.einsum("ij,ij->i", cross, cam.center_world() - centers) < 0
        faces[flip] = faces[flip][:, [0, 2, 1]]
        cross[flip] *= -1
        fn = cross / np.linalg.norm(cross, axis=1)[:, None]
        # Dos caras que comparten arista: no coser una discontinuidad de normal.
        edges = np.sort(np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]])), axis=1)
        face_id = np.tile(np.arange(len(faces)), 3)
        order = np.lexsort((edges[:, 1], edges[:, 0]))
        same = np.flatnonzero((np.diff(edges[order], axis=0) == 0).all(1))
        a, b = face_id[order[same]], face_id[order[same + 1]]
        bad = np.einsum("ij,ij->i", fn[a], fn[b]) < np.cos(np.deg2rad(limits.max_normal_angle_deg))
        keep = np.ones(len(faces), bool); keep[a[bad]] = False; keep[b[bad]] = False
        faces = faces[keep]
    normals = np.zeros_like(vertices)
    if len(faces):
        tri = vertices[faces]
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        for k in range(3):
            np.add.at(normals, faces[:, k], fn)
        normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-15)
    # Camera UV samples distorted color.png directly, without another undistortion.
    uv = (m.cam[sample] + 0.5) / np.array(m.camera_size)
    # Collisions at camera texel resolution: only nearest depth gets appearance.
    texels = np.rint(m.cam[sample]).astype(int)
    texture_valid = np.ones(len(sample), bool)
    if len(sample):
        key = texels[:, 1] * m.camera_size[0] + texels[:, 0]
        unique, inv = np.unique(key, return_inverse=True)
        nearest = np.full(len(unique), np.inf)
        np.minimum.at(nearest, inv, q.depth_mm[sample, 0])
        texture_valid = q.depth_mm[sample, 0] <= nearest[inv] + 1e-6
    return TexturedMesh(vertices, faces.astype(np.int32), normals, m.color[sample], uv, sample,
                        np.where(grid >= 0, np.r_[sample, -1][grid], -1).astype(np.int32), texture_valid)


def write_mesh(path, mesh: TexturedMesh):
    """PLY binario, RGB+normal+s/t por vértice; NPZ es autoridad para UV/procedencia."""
    fields = [(name, "<f4") for name in ("x", "y", "z", "nx", "ny", "nz", "s", "t")]
    fields += [(name, "u1") for name in ("red", "green", "blue")]
    rec = np.empty(len(mesh.vertices), dtype=fields)
    for names, values in ((('x', 'y', 'z'), mesh.vertices), (('nx', 'ny', 'nz'), mesh.normals),
                          (('s', 't'), mesh.uv), (('red', 'green', 'blue'), np.clip(mesh.color[:, ::-1], 0, 255))):
        for k, name in enumerate(names):
            rec[name] = values[:, k]
    face_rec = np.empty(len(mesh.faces), dtype=[("n", "u1"), ("indices", "<i4", (3,))])
    face_rec["n"] = 3; face_rec["indices"] = mesh.faces
    header = ["ply", "format binary_little_endian 1.0", "comment units mm",
              "comment UV s t top_left; texture reference in mesh.json",
              f"element vertex {len(rec)}"]
    header += [f"property {'uchar' if dtype == 'u1' else 'float'} {name}" for name, dtype in fields]
    header += [f"element face {len(face_rec)}", "property list uchar int vertex_indices", "end_header"]
    with open(path, "wb") as stream:
        stream.write(("\n".join(header) + "\n").encode("ascii"))
        stream.write(rec.tobytes()); stream.write(face_rec.tobytes())


def _distribution(values):
    v = np.asarray(values); v = v[np.isfinite(v)]
    if not len(v):
        return dict(n=0, max=None, rms=None, p95=None, median=None)
    return dict(n=len(v), max=float(v.max()), rms=float(np.sqrt(np.mean(v ** 2))),
                p95=float(np.percentile(v, 95)), median=float(np.median(v)))


def paper_metrics(points, corners, size_mm=(215.9, 279.4), plane=None):
    """Métricas locales sin reescalar. corners: TL,TR,BR,BL medidos independientemente.

    plane=(normal,d) externo si disponible; sin él se ajusta plano y se mide solo
    planitud, no posición absoluta. Lados/diagonales incompletos quedan no verificados.
    """
    points = np.asarray(points, float).reshape(-1, 3)
    corners = np.asarray(corners, float).reshape(4, 3)
    finite = np.isfinite(points).all(1)
    p = points[finite]
    fitted = plane is None
    if plane is None and len(p) >= 3:
        _, s, vh = np.linalg.svd(p - p.mean(0), full_matrices=False)
        if s[1] > 1e-8:
            normal = vh[-1]; plane = (normal, -normal @ p.mean(0))
    residual = np.array([])
    if plane is not None:
        normal, offset = plane
        normal = np.asarray(normal, float)
        norm = np.linalg.norm(normal)
        if not np.isfinite(norm) or norm <= 0 or not np.isfinite(offset):
            raise ValueError("plano inválido")
        residual = np.abs(p @ normal + offset) / norm
    pairs = [(0, 1), (1, 2), (2, 3), (3, 0), (0, 2), (1, 3)]
    w, h = size_mm
    target = np.array([w, h, w, h, np.hypot(w, h), np.hypot(w, h)])
    lengths = np.array([np.linalg.norm(corners[a] - corners[b]) for a, b in pairs])
    errors = np.abs(lengths - target)
    known = np.isfinite(errors).all() and len(residual) > 0 and finite.all()
    passed = known and errors.max() <= 2 and residual.max() <= 2
    return dict(units="mm", status="passed_local" if passed else "failed" if known else "unverified",
                scope="local_dimensions_and_flatness_only", plane_fitted=fitted,
                samples_requested=len(points), samples_finite=int(finite.sum()),
                plane_distance_mm=_distribution(residual), dimension_error_mm=_distribution(errors),
                lengths_mm=[float(x) if np.isfinite(x) else None for x in lengths],
                target_lengths_mm=target.tolist())


def export_reconstruction(out, m, q, mesh, *, evidence, world, rig_sha256,
                          texture_path=None, filters=Filters(), limits=MeshLimits(),
                          inputs=None, elapsed_seconds=None, camera_name="camera", projector_name="projector"):
    """Artefactos P6. Enlace local a textura (sin duplicar imagen); caller publica.

    Las máscaras de matches describen adquisición/agrupación; accepted/rejection
    y cloud_index describen triangulación. Nunca reutilizar measured como accepted.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    common = dict(world=world, rig_sha256=rig_sha256, input_hashes=inputs or {},
                  uncertainty_scope="correspondence_only; rig_covariance_not_available")
    cloud_index = np.flatnonzero(q.accepted)
    rgb = np.clip(m.color[:, ::-1], 0, 255).astype(np.uint8)
    write_ply(out / "cloud.ply", q.xyz[q.accepted], m.color[q.accepted], comment="slscan metric; units mm")
    write_mesh(out / "mesh.ply", mesh)
    save_npz(out / "matches.npz", metadata("matches", evidence, **common, units="px",
             camera=camera_name, projector=projector_name, count=len(m.cam),
             camera_size=list(m.camera_size), projector_size=list(m.projector_size), step=m.step,
             decoder="slscan-gray/2", decoder_stats=m.decoder_stats,
             confidence_kind="measured" if m.bit_contrast is not None else "binary_validity_only",
             confidence_definition="minimum_absolute_bit_contrast_divided_by_255",
             confidence_unknown_value=0,
             occluded_definition="unknown_without_independent_visibility_evidence; mask_false_is_not_visible"),
             camera_xy=m.cam, projector_xy=m.pro, color_rgb=rgb,
             source_code=(m.cells * m.step + m.step // 2).astype(np.int32), cells=m.cells,
             source_bit=np.full(len(m.cam), -1, np.int32), source_index=m.source_index,
             source_match=m.source_match, count=m.count, spread_px=m.spread_px,
             confidence=np.nan_to_num(m.confidence, nan=0), confidence_known=np.isfinite(m.confidence),
             sigma_px=m.sigma_px, measured=np.ones(len(m.cam), bool), interpolated=np.zeros(len(m.cam), bool),
             occluded=np.zeros(len(m.cam), bool), invalid=np.zeros(len(m.cam), bool),
             **{f"camera_{k}": v for k, v in m.masks.items()},
             **({"bit_contrast": m.bit_contrast} if m.bit_contrast is not None else {}))
    save_npz(out / "cloud_quality.npz", metadata("cloud_quality", evidence, **common,
             filters=asdict(filters), point_count=len(cloud_index), sample_count=len(m.cam),
             rejection_codes={str(int(r)): r.name for r in Reject}, rejection_encoding="bitmask"),
             xyz_mm=q.xyz[q.accepted], color_rgb=rgb[q.accepted], sample_index=cloud_index,
             uncertainty_mm=q.sigma_mm[q.accepted], reprojection_px=q.reprojection_px[q.accepted],
             rejection_reason=q.rejection.astype(np.int32),
             xyz_candidates=q.xyz, accepted=q.accepted, depth_mm=q.depth_mm,
             reprojection_candidates_px=q.reprojection_px, ray_angle_deg=q.ray_angle_deg,
             ray_gap_mm=q.ray_gap_mm, sigma_candidates_mm=q.sigma_mm)
    # El contrato P6 requiere assets relativos contenidos, sin symlinks/traversal.
    # Un hardlink local comparte los bytes privados; nunca duplica capturas ni modifica el origen.
    texture = None
    if texture_path is not None:
        import os
        texture_name = "appearance.png"
        os.link(Path(texture_path).resolve(), out / texture_name)
        texture = dict(path=texture_name, sha256=sha256(texture_path))
    save_npz(out / "mesh.npz", metadata("mesh_arrays", evidence, **common,
             method="grid", limits=asdict(limits), vertex_count=len(mesh.vertices), face_count=len(mesh.faces),
             uv_convention="top_left_0_1", uv_formula="(camera_xy+0.5)/camera_size",
             texture_visibility="measured_camera_samples_and_nearest_depth_per_texel; no_visibility_claim_inside_faces"),
             vertices_mm=mesh.vertices, faces=mesh.faces, normals=mesh.normals, color_rgb=rgb[q.accepted],
             uv=mesh.uv, sample_index=mesh.sample_index, grid_sample=mesh.grid_sample,
             measured=np.ones(len(mesh.vertices), bool), texture_valid=mesh.texture_valid)

    def asset(name):
        return dict(path=name, sha256=sha256(out / name))

    all_cells = int(np.prod(mesh.grid_sample.shape))
    cloud_doc = metadata("cloud", evidence, **common, matches_sha256=sha256(out / "matches.npz"),
        point_count=len(cloud_index), sample_count=len(m.cam), coverage=len(cloud_index) / all_cells,
        coverage_scope="projector_cell_grid; not_surface_or_common_ROI", metric_status="unverified",
        ply=asset("cloud.ply"), quality=asset("cloud_quality.npz"))
    save_json(out / "cloud.json", cloud_doc)
    mesh_doc = metadata("mesh", evidence, **common, cloud_sha256=sha256(out / "cloud.json"),
        vertex_count=len(mesh.vertices), face_count=len(mesh.faces), uv_convention="top_left_0_1",
        ply=asset("mesh.ply"), arrays=asset("mesh.npz"), texture=texture,
        texture_kind="appearance" if texture else "none", metric_status="unverified",
        storage="sRGB_assumed_not_measured", texture_storage="local_hardlink_read_only_by_convention")
    save_json(out / "mesh.json", mesh_doc)
    measured_cells = len(m.cam)
    area = 0.0
    if len(mesh.faces):
        tri = mesh.vertices[mesh.faces]
        area = float(np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1).sum() / 2)
    report = metadata("validation", evidence, **common, status="unverified",
        physical_verified=False, threshold_mm=2, scope="P3_geometry_processing_without_physical_controls", checks=[],
        protocol=None,                      # contrato P6 v1: obligatorio (nulo = sin protocolo físico todavía)
        processing_state="passed" if len(cloud_index) else "blocked",
        reason="requires_physical_validation" if len(cloud_index) else "no_correspondences_pass_filters",
        metric_validation="unverified", acceptance_2mm="requires_independent_reserved_physical_controls",
        inputs=inputs or {}, filters=asdict(filters), mesh_limits=asdict(limits),
        candidates=measured_cells, points=int(q.accepted.sum()), triangles=len(mesh.faces),
        camera_pixels_measured=int(m.masks["measured"].sum()), camera_pixels_invalid=int(m.masks["invalid"].sum()),
        camera_pixels_rejected_grouping=int(m.masks["rejected"].sum()),
        projector_cells_total=all_cells, projector_cells_unobserved=all_cells - measured_cells,
        projector_cell_fraction_measured=measured_cells / all_cells,
        projector_cell_fraction_accepted=float(q.accepted.sum() / all_cells),
        common_matte_roi_coverage=None, common_matte_roi_status="not_supplied",
        mesh_area_mm2=area, points_per_mm2_on_mesh=(len(np.unique(mesh.faces)) / area if area else None),
        rejection_counts={r.name: int(((q.rejection & r) != 0).sum()) for r in Reject},
        quality={name: {"before": _distribution(value), "after": _distribution(value[q.accepted])}
                 for name, value in dict(reprojection_px=q.reprojection_px, ray_gap_mm=q.ray_gap_mm,
                                         sigma_mm=q.sigma_mm).items()},
        seconds=elapsed_seconds,
        array_bytes=int(sum(a.nbytes for a in (m.cam, m.pro, m.color, m.source_match, q.xyz,
                       q.depth_mm, q.reprojection_px, mesh.vertices, mesh.faces, mesh.uv))),
        memory_note="selected_resident_arrays_only; not_peak_RSS")
    report["outputs"] = {p.name: dict(sha256=sha256(p), bytes=p.stat().st_size)
                         for p in out.iterdir() if p.is_file()}
    save_json(out / "reconstruction.json", report)
    return report


def reconstruct_recorded(scan, rig_path, manifest_path, out, *, camera,
                         skip_bits=0, threshold=5, filters=Filters(), limits=MeshLimits()):
    """Re-decode offline; entrada inmutable, salida nueva publicada por rename.

    El manifiesto de adquisición debe vincular captura/rig/frame antes de correr.
    Vincular hashes no demuestra inmovilidad: esa afirmación la aporta adquisición.
    No inventar un manifiesto para aplicar una calibración nueva a scan5.
    """
    from .graycode import decode, load_sequence
    import tempfile
    start = time.monotonic()
    scan, rig_path, manifest_path, out = map(Path, (scan, rig_path, manifest_path, out))
    if out.resolve().is_relative_to(scan.resolve()):
        raise ValueError("salida debe quedar fuera del scan de entrada (solo lectura)")
    if out.exists():
        raise FileExistsError(f"salida ya existe: {out}")
    rig = Rig.load(rig_path)
    if rig.units != "mm":
        raise ValueError("rig debe declarar mm")
    cam, pro = rig[camera], rig.projector()
    manifest = json.loads(manifest_path.read_text())
    rig_hash = sha256(rig_path)
    if manifest.get("schema_version") != "1.0" or manifest.get("artifact") != "manifest":
        raise ValueError("se requiere manifest P6 schema_version=1.0")
    if _contracts is not None:
        _contracts.validate(manifest, "manifest")
    if manifest.get("rig_sha256") != rig_hash or not manifest.get("session_id"):
        raise ValueError("manifest no vincula esta captura con el rig (rig_sha256/session_id)")
    frame = manifest.get("devices", {}).get(camera, {})
    if tuple(frame.get("size", ())) != tuple(cam.size):
        raise ValueError("frame de cámara incompatible con el rig")
    if not manifest.get("acquisition_known") or manifest.get("rig_compatibility") != "verified_static_session":
        raise ValueError("falta verificación de sesión estática compatible; no aplicar rig nuevo a capturas antiguas")
    if manifest.get("evidence") not in ("recorded", "live", "synthetic"):
        raise ValueError("evidence ausente o inválida")
    # La ejecución es offline aunque la adquisición original haya sido live.
    evidence = "synthetic" if manifest["evidence"] == "synthetic" else "recorded"
    items = load_sequence(scan)
    inputs = {str(path.resolve()): sha256(path) for _, path in items}
    texture = scan / "color.png"
    if texture.exists():
        inputs[str(texture.resolve())] = sha256(texture)
    expected = manifest.get("input_hashes", {})
    for path, digest in inputs.items():
        try:
            relative = str(Path(path).relative_to(manifest_path.resolve().parent))
        except ValueError:
            relative = Path(path).name
        if expected.get(relative, expected.get(Path(path).name)) != digest:
            raise ValueError(f"manifest no identifica el contenido de {Path(path).name}")
    dec = decode(items, threshold=threshold, skip_bits=skip_bits,
                 projector_size=pro.size, retain_contrast=True)
    m = measured_matches(dec, pro.size)
    q = triangulate_measured(m, cam, pro, filters)
    if texture.exists():
        color = cv2.imread(str(texture))
        if color is None or tuple(color.shape[1::-1]) != tuple(cam.size):
            raise ValueError("color.png no corresponde al frame calibrado")
        if len(m.cam):
            m.color = sample_color(color, m.cam)
    mesh = grid_mesh(m, q, cam, limits)
    inputs[str(rig_path.resolve())] = rig_hash
    inputs[str(manifest_path.resolve())] = sha256(manifest_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # TemporaryDirectory only cleans outputs created by this invocation, never captures.
    with tempfile.TemporaryDirectory(prefix=".p3-", dir=out.parent) as temp:
        stage = Path(temp) / "artifacts"
        result = export_reconstruction(stage, m, q, mesh, evidence=evidence, world=rig.world,
            rig_sha256=rig_hash, texture_path=texture if texture.exists() else None,
            filters=filters, limits=limits, inputs=inputs, elapsed_seconds=time.monotonic() - start,
            camera_name=cam.name, projector_name=pro.name)
        stage.rename(out)
    return result


def add_parser(subparsers):
    parser = subparsers.add_parser("reconstruct", help="offline: Gray medido + rig -> cloud/mesh métricos y UV")
    parser.add_argument("scan", help="directorio de cámara con secuencia Gray y color.png")
    parser.add_argument("--rig", required=True)
    parser.add_argument("--manifest", required=True, help="manifiesto de adquisición que vincula sesión/rig/entradas")
    parser.add_argument("--camera", required=True)
    parser.add_argument("--out", required=True, help="directorio nuevo; nunca modifica capturas")
    parser.add_argument("--skip-bits", type=int, default=0)
    parser.add_argument("--threshold", type=int, default=5)
    for name, value in asdict(Filters()).items():
        parser.add_argument("--" + name.replace("_", "-"), type=float, default=value)
    for name, value in asdict(MeshLimits()).items():
        parser.add_argument("--" + name.replace("_", "-"), type=float, default=value)
    parser.set_defaults(fn=_command)


def _command(args):
    filters = Filters(**{key: getattr(args, key) for key in asdict(Filters())})
    limits = MeshLimits(**{key: getattr(args, key) for key in asdict(MeshLimits())})
    result = reconstruct_recorded(args.scan, args.rig, args.manifest, args.out, camera=args.camera,
                                 skip_bits=args.skip_bits, threshold=args.threshold, filters=filters, limits=limits)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 2 if result["processing_state"] == "blocked" else 0
