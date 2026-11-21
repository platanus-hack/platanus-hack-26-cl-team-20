"""Mesh components, noise-scaled RANSAC planes and calibrated pixel visibility.

Only NumPy/SciPy/OpenCV. No assumed world up, tabletop, object colors or offsets.
Visibility ray-tests pixel centers against triangles, including lens distortion.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree


@dataclass(frozen=True)
class Options:
    noise_mm: float = .5
    plane_sigma: float = 3.
    normal_angle_deg: float = 30.
    color_edge: float = 50.  # RGB Euclidean distance, any hue equally
    ransac_trials: int = 100
    max_planes: int = 16
    min_plane_faces: int = 4
    mask_face_fraction: float = .6
    seed: int = 0

    def __post_init__(self):
        if not (np.isfinite([self.noise_mm, self.plane_sigma, self.normal_angle_deg,
                            self.color_edge, self.mask_face_fraction]).all()
                and self.noise_mm > 0 and self.plane_sigma > 0
                and 0 < self.normal_angle_deg < 90 and self.color_edge > 0
                and 0 < self.mask_face_fraction <= 1
                and self.ransac_trials > 0 and self.max_planes > 0 and self.min_plane_faces >= 3):
            raise ValueError("Invalid segmentation options")


def face_geometry(vertices, faces):
    tri = vertices[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(length[:, None], 1e-15)
    return tri.mean(axis=1), normals, length / 2


def plane_fit(points, threshold):
    if len(points) < 3:
        return None, None
    center = points.mean(axis=0)
    _, sv, vh = np.linalg.svd(points - center, full_matrices=False)
    if len(sv) < 2 or sv[1] < 1e-8:
        return None, None
    normal = vh[-1]
    if normal[np.argmax(np.abs(normal))] < 0:
        normal = -normal
    offset = -float(center @ normal)
    residual = np.abs(points @ normal + offset)
    maximum = float(residual.max())
    plane = [*normal.tolist(), offset] if maximum <= threshold else None
    return plane, maximum


def adjacency(vertices, faces):
    # Weld UV seams by exact geometric coordinates; do not bridge nearby gaps.
    _, weld = np.unique(vertices, axis=0, return_inverse=True)
    f = weld[faces]
    edges = np.sort(np.concatenate((f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]])), axis=1)
    owner = np.tile(np.arange(len(f)), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    e, o = edges[order], owner[order]
    same = np.all(e[1:] == e[:-1], axis=1)
    return o[:-1][same], o[1:][same]


def geometric_labels(vertices, faces, options, colors=None):
    """RANSAC on 3D face centroids; connectivity later splits disjoint planes."""
    centers, normals, area = face_geometry(vertices, faces)
    labels = np.full(len(faces), -1, np.int32)
    remaining = area > 1e-9
    threshold = options.noise_mm * options.plane_sigma
    cosine = np.cos(np.deg2rad(options.normal_angle_deg))
    rng = np.random.default_rng(options.seed)
    for plane_id in range(options.max_planes):
        indices = np.flatnonzero(remaining)
        if len(indices) < options.min_plane_faces:
            break
        # Bound hypothesis scoring cost on large meshes; final membership uses all faces.
        sample = rng.choice(indices, min(4096, len(indices)), replace=False)
        best, best_count = None, 0
        for _ in range(options.ransac_trials):
            ids = rng.choice(sample, 3, replace=False)
            p = centers[ids]
            normal = np.cross(p[1] - p[0], p[2] - p[0])
            length = np.linalg.norm(normal)
            if length < 1e-9:
                continue
            normal /= length
            d = -float(p[0] @ normal)
            fit = ((np.abs(centers[sample] @ normal + d) <= threshold)
                   & (np.abs(normals[sample] @ normal) >= cosine))
            count = np.count_nonzero(fit)
            if count > best_count:
                best, best_count = (normal, d), count
        if best is None or best_count < options.min_plane_faces:
            break
        normal, d = best
        fit = (remaining & (np.abs(centers @ normal + d) <= threshold)
               & (np.abs(normals @ normal) >= cosine))
        labels[fit] = plane_id
        remaining[fit] = False
    left, right = adjacency(vertices, faces)
    keep = (np.abs(np.sum(normals[left] * normals[right], axis=1)) >= cosine)
    keep &= labels[left] == labels[right]
    if colors is not None:
        keep &= np.linalg.norm(colors[left] - colors[right], axis=1) <= options.color_edge
    return labels, (left[keep], right[keep])


def split_components(face_labels, edges):
    n = len(face_labels)
    if n == 0:
        return []
    left, right = edges
    same = face_labels[left] == face_labels[right]
    graph = coo_matrix((np.ones(np.count_nonzero(same)), (left[same], right[same])), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    # Stable within an identical mesh, O(F log F) instead of one scan per component.
    order = np.argsort(labels, kind="stable")
    return np.split(order, np.flatnonzero(np.diff(labels[order])) + 1)


def rasterize(vertices, faces, device, progress=None):
    """Face index per distorted pixel; -1 means no observed mesh intersection.

    Query an enclosing circle in an undistorted-ray KD tree, then exact barycentric
    and perspective depth tests. This avoids straight-edge distortion shortcuts.
    Faces crossing/behind the lens plane are omitted, counted in the report.
    """
    width, height = device.size
    yy, xx = np.mgrid[:height, :width]
    pixels = np.column_stack((xx.ravel(), yy.ravel())).astype(float)
    criteria = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 30, 1e-12)
    if hasattr(cv2, "undistortPointsIter"):  # OpenCV 4; merged into undistortPoints in 5
        rays = cv2.undistortPointsIter(pixels.reshape(-1, 1, 2), device.K, device.dist,
                                      None, None, criteria).reshape(-1, 2)
    else:
        rays = cv2.undistortPoints(pixels.reshape(-1, 1, 2), device.K, device.dist,
                                  criteria=criteria).reshape(-1, 2)
    if not np.isfinite(rays).all():
        raise ValueError("Nonfinite undistorted rays")
    # Refuse a distortion inversion that did not converge; it cannot yield trusted masks.
    # Chunk projection to avoid OpenCV's large unused Jacobian for a full HD image.
    for start in range(0, len(rays), 8192):
        r = rays[start:start+8192]
        back = cv2.projectPoints(np.column_stack((r, np.ones(len(r)))), np.zeros(3), np.zeros(3),
                                 device.K, device.dist)[0].reshape(-1, 2)
        if np.max(np.linalg.norm(back - pixels[start:start+8192], axis=1)) > 1e-3:
            raise ValueError("Distortion inversion did not converge within 0.001 px")
    tree = cKDTree(rays)
    xyz = vertices @ device.R.T + device.t
    owner = np.full(width * height, -1, np.int32)
    depth = np.full(width * height, np.inf)
    skipped = 0
    for index, face in enumerate(faces):
        if progress and index % 1000 == 0:
            progress(index / max(1, len(faces)))
        tri = xyz[face]
        if np.any(tri[:, 2] <= 1e-6):
            skipped += 1
            continue
        xy = tri[:, :2] / tri[:, 2, None]
        center = xy.mean(axis=0)
        radius = np.linalg.norm(xy - center, axis=1).max()
        a, b = xy[1] - xy[0], xy[2] - xy[0]
        det = a[0] * b[1] - a[1] * b[0]
        if abs(det) < 1e-15:
            skipped += 1
            continue
        ids = np.asarray(tree.query_ball_point(center, radius + 1e-12), dtype=np.int64)
        p = rays[ids] - xy[0]
        u = (p[:, 0] * b[1] - p[:, 1] * b[0]) / det
        v = (a[0] * p[:, 1] - a[1] * p[:, 0]) / det
        inside = (u >= -1e-10) & (v >= -1e-10) & (u + v <= 1 + 1e-10)
        ids, u, v = ids[inside], u[inside], v[inside]
        z = 1 / ((1 - u - v) / tri[0, 2] + u / tri[1, 2] + v / tri[2, 2])
        closer = z < depth[ids] - 1e-8  # deterministic lowest-face tie break
        depth[ids[closer]] = z[closer]
        owner[ids[closer]] = index
    return owner.reshape(height, width), dict(skipped_faces=skipped,
                                             visible_pixels=int(np.count_nonzero(owner >= 0)))


def regions(mask):
    """Preserve holes and disjoint islands; the PNG remains authoritative."""
    contours, hierarchy = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP,
                                          cv2.CHAIN_APPROX_SIMPLE)
    result = []
    if hierarchy is None:
        return result
    for i, contour in enumerate(contours):
        if hierarchy[0, i, 3] >= 0 or len(contour) < 3:
            continue
        holes = []
        child = hierarchy[0, i, 2]
        while child >= 0:
            holes.append(contours[child].reshape(-1, 2).astype(float).tolist())
            child = hierarchy[0, child, 0]
        result.append(dict(outer=contour.reshape(-1, 2).astype(float).tolist(), holes=holes))
    return sorted(result, key=lambda r: -abs(cv2.contourArea(np.asarray(r["outer"], np.float32))))
