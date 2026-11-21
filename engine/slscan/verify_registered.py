"""Offline paired-white/outline verification on prefixed surface controls.

No hardware, image uploads, offsets or calibration fitting. Surface planes must
come from independent/local geometry. A report never certifies an entire scene:
sheet dimensions, planarity, three held-out poses and repeats remain required.
"""
from __future__ import annotations

import cv2
import numpy as np

from . import contracts as c
from .registered import undistort


def surface_points(device, pixels, plane):
    pixels = np.asarray(pixels, float).reshape(-1, 2)
    K, dist, R, t = (np.asarray(device[k], float) for k in ('K', 'dist', 'R', 't'))
    ray = undistort(pixels[:, None], K, dist).reshape(-1, 2)
    ray = np.column_stack([ray, np.ones(len(ray))]) @ R
    center = -R.T@t
    plane = np.asarray(plane, float)
    if plane.shape != (4,) or not np.isfinite(plane).all() or np.linalg.norm(plane[:3]) == 0:
        raise ValueError('invalid local surface plane')
    denominator = ray@plane[:3]
    with np.errstate(divide='ignore', invalid='ignore'):
        depth = -(plane[:3]@center+plane[3])/denominator
    good = np.isfinite(pixels).all(axis=1) & np.isfinite(depth) & (depth > 0) & (abs(denominator) > 1e-8)
    good &= ((pixels >= 0) & (pixels <= np.asarray(device['size'])-1)).all(axis=1)
    points = center+depth[:, None]*ray
    points[~good] = np.nan
    return points


def _segment_distances(points, target):
    """Distances to the observed polyline, no bridging a missing control."""
    starts, ends = target[:-1], target[1:]
    ok = np.isfinite(starts).all(axis=1) & np.isfinite(ends).all(axis=1)
    starts, ends = starts[ok], ends[ok]
    if not len(starts): return np.full(len(points), np.nan)
    delta = ends-starts
    norm = np.maximum(np.sum(delta*delta, axis=1), 1e-20)
    result = []
    for block in np.array_split(points, max(1, (len(points)+127)//128)):
        shift = block[:, None]-starts
        fraction = np.clip(np.sum(shift*delta, axis=2)/norm, 0, 1)
        result.extend(np.min(np.linalg.norm(shift-fraction[..., None]*delta, axis=2), axis=1))
    return np.asarray(result)


def diagnose_global(expected_px, observed_px):
    """Raw residual and fitted models for diagnosis ONLY; no unique cause inferred."""
    a, b = np.asarray(expected_px, float), np.asarray(observed_px, float)
    good = np.isfinite(a).all(axis=1) & np.isfinite(b).all(axis=1)
    a, b = a[good], b[good]
    if len(a) < 4 or np.linalg.matrix_rank(np.column_stack([a, np.ones(len(a))])) < 3:
        return {'status': 'insufficient_spatial_controls', 'corrections_applied': False}
    def stats(p):
        e = np.linalg.norm(p-b, axis=1)
        return {'max_px': float(e.max()), 'rms_px': float(np.sqrt(np.mean(e*e)))}
    translation = (b-a).mean(axis=0)
    # similarity least squares, with all prefixed valid samples (no RANSAC pruning)
    A = np.zeros((2*len(a), 4)); rhs = b.ravel()
    A[::2] = np.column_stack([a[:, 0], -a[:, 1], np.ones(len(a)), np.zeros(len(a))])
    A[1::2] = np.column_stack([a[:, 1], a[:, 0], np.zeros(len(a)), np.ones(len(a))])
    x = np.linalg.lstsq(A, rhs, rcond=None)[0]
    affine = np.linalg.lstsq(np.column_stack([a, np.ones(len(a))]), b, rcond=None)[0]
    H, _ = cv2.findHomography(a, b, method=0)
    result = dict(status='diagnostic_only', corrections_applied=False, raw=stats(a),
        translation=dict(vector_px=translation.tolist(), **stats(a+translation)),
        similarity=dict(scale=float(np.hypot(x[0], x[1])), parameters=x.tolist(), **stats((A@x).reshape(-1, 2))),
        affine=dict(matrix=affine.T.tolist(), **stats(np.column_stack([a, np.ones(len(a))])@affine)),
        possible_causes=['display_geometry', 'intrinsics', 'pose', 'distortion', 'motion', 'segmentation'])
    if H is not None:
        predicted = cv2.perspectiveTransform(a[:, None], H)[:, 0]
        if np.isfinite(predicted).all(): result['homography'] = dict(matrix=H.tolist(), **stats(predicted))
    return result


def measure_object(device, physical_px, projected_px, plane, *, object_id,
                   sample_lengths_mm, uncertainty_mm, held_out=False, evidence='recorded'):
    """Bidirectional outline distances; coverage is length in the prefixed ROI.

    Arrays have one row per prefixed ordered control; missing rows are NaN.
    Length weights are fixed BEFORE observing either photo. Independent controls
    and uncertainty are supplied by the measurement protocol, not guessed here.
    """
    physical, projected = np.asarray(physical_px, float), np.asarray(projected_px, float)
    weights = np.asarray(sample_lengths_mm, float)
    if physical.shape != projected.shape or physical.ndim != 2 or physical.shape[1] != 2 or len(physical) < 2:
        raise ValueError('expected matching ordered Nx2 controls')
    if weights.shape != (len(physical),) or not np.isfinite(weights).all() or np.any(weights <= 0):
        raise ValueError('prefixed positive sample lengths required')
    if uncertainty_mm is not None and (not np.isfinite(uncertainty_mm) or uncertainty_mm < 0):
        raise ValueError('invalid uncertainty')
    p, q = surface_points(device, physical, plane), surface_points(device, projected, plane)
    valid = np.isfinite(p).all(axis=1) & np.isfinite(q).all(axis=1)
    # Both sides use the same declared coverage, never a bridge across a missing row.
    p[~valid] = np.nan; q[~valid] = np.nan
    forward, reverse = _segment_distances(p[valid], q), _segment_distances(q[valid], p)
    errors = np.r_[forward, reverse]
    errors = errors[np.isfinite(errors)]
    coverage = float(weights[valid].sum()/weights.sum()) if len(errors) else 0.
    maximum = float(errors.max()) if len(errors) else None
    status = 'inconclusive'
    if maximum is not None and maximum > 2: status = 'failed'
    elif maximum is not None and uncertainty_mm is not None and coverage == 1 and held_out:
        status = 'passed' if maximum+uncertainty_mm <= 2 else 'inconclusive'
    if evidence == 'synthetic' and status == 'passed': status = 'unverified'
    check = dict(name='object_outlines', max_mm=maximum,
        rms_mm=float(np.sqrt(np.mean(errors*errors))) if len(errors) else None,
        p95_mm=float(np.percentile(errors, 95)) if len(errors) else None,
        samples=len(errors), coverage=coverage, uncertainty_mm=uncertainty_mm,
        held_out=held_out, status=status)
    return dict(object_id=object_id, check=check, covered_length_mm=float(weights[valid].sum()),
        prefixed_length_mm=float(weights.sum()), missing_controls=np.flatnonzero(~valid).tolist(),
        physical_verified=False, diagnostics=diagnose_global(physical, projected))


def extract_paired_controls(white, outlined, samples_px, normals_px, *, radius_px=8,
                            step_px=.25, min_edge=8., min_darkening=12.):
    """Localise physical edge in white, dark line in white-minus-outlined.

    Uses independently prefixed sample normals/strips. Rejects ambiguous double
    lines and out-of-frame strips. Camera must be fixed, linearised or radiometry
    stable, with identical exposure. No automatic alignment or exposure fitting.
    Return NaN for missing controls; caller must report those in coverage.
    """
    if white.shape != outlined.shape or white.ndim != 2:
        raise ValueError('paired same-size grayscale photographs required')
    if not 0 < step_px <= 1 or radius_px < 2:
        raise ValueError('invalid prefixed profile search')
    samples, normals = np.asarray(samples_px, float), np.asarray(normals_px, float)
    if samples.shape != normals.shape or samples.ndim != 2 or samples.shape[1] != 2:
        raise ValueError('expected Nx2 samples and normals')
    if not np.isfinite(samples).all() or not np.isfinite(normals).all() or np.any(np.linalg.norm(normals, axis=1) < 1e-8):
        raise ValueError('invalid samples/normals')
    normals = normals/np.linalg.norm(normals, axis=1)[:, None]
    offsets = np.arange(-radius_px, radius_px+step_px/2, step_px)
    positions = samples[:, None]+normals[:, None]*offsets[None, :, None]
    h, w = white.shape
    inside = ((positions >= [0, 0]) & (positions <= [w-1, h-1])).all(axis=(1, 2))
    maps = positions.astype(np.float32)
    base = cv2.remap(white.astype(np.float32), maps[..., 0], maps[..., 1], cv2.INTER_LINEAR)
    line = cv2.remap(outlined.astype(np.float32), maps[..., 0], maps[..., 1], cv2.INTER_LINEAR)
    physical = np.full_like(samples, np.nan); projected = physical.copy()
    def centroid(profile, threshold):
        active = profile >= max(threshold, float(profile.max())*.5)
        indices = np.flatnonzero(active)
        if not len(indices) or indices[0] == 0 or indices[-1] == len(offsets)-1 or np.any(np.diff(indices) > 1): return None
        return float(np.sum(offsets[indices]*profile[indices])/profile[indices].sum())
    for i in np.flatnonzero(inside):
        edge = centroid(abs(np.gradient(base[i], step_px)), min_edge)
        dark = centroid(np.maximum(base[i]-line[i], 0), min_darkening)
        if edge is not None: physical[i] = samples[i]+normals[i]*edge
        if dark is not None: projected[i] = samples[i]+normals[i]*dark
    return physical, projected


def validation_report(results, *, evidence, input_hashes, protocol=None):
    checks = [r['check'] for r in results]
    status = 'failed' if any(x['status'] == 'failed' for x in checks) else 'unverified'
    report = dict(**c.header('validation', evidence, input_hashes=input_hashes),
        status=status, physical_verified=False, threshold_mm=2, scope='prefixed_object_outline_controls',
        reason='Object outlines only; requires independent sheet dimensions, planarity, held-out poses and repeats.',
        protocol=protocol, checks=checks, objects=results)
    return c.validate(report)


def verify_packet(packet_path, output_path):
    """A reviewable local JSON packet binds prefixed controls and paired photos.

    Paths resolve relative to the packet. Images are read-only; output contains
    metrics/hashes only. No capture, alignment or reclassification of missing data.
    """
    import json
    from pathlib import Path
    from .registered import RegisteredScene
    packet_path, output_path = Path(packet_path).resolve(), Path(output_path).resolve()
    packet = json.loads(packet_path.read_text())
    if output_path.exists(): raise ValueError('validation output must be new')
    def path(key): return (packet_path.parent/packet[key]).resolve()
    scene = RegisteredScene(path('scene'))
    if packet['scene_sha256'] != scene.sha: raise ValueError('stale verification scene')
    device = scene.doc['devices'][packet['camera']]
    if device['kind'] != 'camera': raise ValueError('verification needs a calibrated camera')
    if not packet.get('stationary') or not packet.get('exposure_verified') or not packet.get('controls_prefixed'):
        raise ValueError('stationary scene, exposure and prefixed controls must be verified')
    white = cv2.imread(str(path('white')), cv2.IMREAD_GRAYSCALE)
    outlined = cv2.imread(str(path('outlined')), cv2.IMREAD_GRAYSCALE)
    shape = tuple(reversed(device['size']))
    if white is None or outlined is None or white.shape != shape or outlined.shape != shape:
        raise ValueError('paired photos must match the calibrated camera frame')
    ids = {o['id'] for o in scene.objects}
    controls = packet['objects']
    if not controls or len({o['id'] for o in controls}) != len(controls):
        raise ValueError('unique prefixed objects required')
    evidence = 'synthetic' if scene.doc['evidence'] == 'synthetic' else packet['evidence']
    results = []
    for control in controls:
        if control['id'] not in ids: raise ValueError('control references an absent object')
        p, q = extract_paired_controls(white, outlined, control['samples_px'], control['normals_px'],
                                       **packet.get('extraction', {}))
        results.append(measure_object(device, p, q, control['surface_plane_world'],
            object_id=control['id'], sample_lengths_mm=control['sample_lengths_mm'],
            uncertainty_mm=control['uncertainty_mm'], held_out=control['held_out'], evidence=evidence))
    hashes = {key: c.digest(path(key)) for key in ('scene','white','outlined')}
    hashes['packet'] = c.digest(packet_path)
    report = validation_report(results, evidence=evidence, input_hashes=hashes, protocol=packet.get('protocol'))
    c.atomic_json(output_path, report)
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('packet')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    report = verify_packet(args.packet, args.out)
    print(f"{report['status']}: {len(report['objects'])} objects; physical_verified=false")
