"""Joint metric calibration from physical paper corners and projected ray observations.

The camera is the fixed world frame. Projected corners are intersections with a
moving paper plane, never independently measured metric object points. All pixel
coordinates follow OpenCV (integer pixel centres), all world coordinates are mm.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import cv2
import numpy as np
from scipy.optimize import least_squares

from .rig import Device, Rig, undistort_points


@dataclass
class PlaneView:
    name: str
    paper_mm: np.ndarray             # four ordered metric corners, Nx3
    paper_cam: np.ndarray            # their observed camera pixels
    projected_cam: np.ndarray        # observed projected corners on this sheet
    projected_px: np.ndarray         # known pattern pixel coordinates
    cam_size: tuple[int, int]
    proj_size: tuple[int, int]
    sigma_paper_px: float = 0.25
    sigma_pattern_px: float = 0.25

    @classmethod
    def from_paper_pose(cls, pose, cam_size, proj_size):
        ch = pose.charuco
        if ch is None:
            raise ValueError(f"{pose.name}: faltan correspondencias proyectadas")
        # A homography consistency test alone admits other coplanar objects.
        # Restrict the new solver to the actual physical paper, with a margin.
        polygon = np.asarray(pose.plane.quad_cam, np.float32)
        keep = np.array([cv2.pointPolygonTest(polygon, tuple(map(float, p)), True) > 2
                         for p in ch['cam']])
        return cls(pose.name, np.column_stack((pose.plane.corners_mm, np.zeros(4))),
                   pose.plane.quad_cam.copy(), np.asarray(ch['cam'])[keep],
                   np.asarray(ch['proj'])[keep], tuple(cam_size), tuple(proj_size))


def project(device, xyz):
    return cv2.projectPoints(np.asarray(xyz, float), cv2.Rodrigues(device.R)[0],
                             device.t, device.K, device.dist)[0].reshape(-1, 2)


def rays(device, pixels):
    xy = undistort_points(pixels, device.K, device.dist).reshape(-1, 2)
    return np.column_stack((xy, np.ones(len(xy)))) @ device.R


def intersect(device, pixels, R, t):
    """Intersect world rays with a paper pose; also return signed ray depth."""
    direction = rays(device, pixels)
    center = device.center_world()
    normal = R[:, 2]
    denominator = direction @ normal
    denominator = np.where(abs(denominator) < 1e-9,
                           np.where(denominator < 0, -1e-9, 1e-9), denominator)
    depth = ((t - center) @ normal) / denominator
    return center + depth[:, None] * direction, depth


def triangulate(cam, pro, c, p):
    """Float64 triangulation in a general OpenCV world frame."""
    def norm(dev, px):
        d = rays(dev, px) @ dev.R.T
        return (d[:, :2] / d[:, 2:]).T
    X = cv2.triangulatePoints(np.column_stack((cam.R, cam.t)),
        np.column_stack((pro.R, pro.t)), norm(cam, c), norm(pro, p))
    return (X[:3] / X[3]).T


def stats(values):
    a = np.asarray(values, float).ravel()
    if not len(a) or not np.isfinite(a).all():
        return {'n': len(a), 'rms': None, 'median': None, 'p95': None, 'max': None}
    return {'n': len(a), 'rms': float(np.sqrt(np.mean(a*a))),
            'median': float(np.median(a)), 'p95': float(np.percentile(a, 95)),
            'max': float(np.max(a))}


def _check(views, initial):
    if len(views) < 4:
        raise ValueError('se necesitan >= 4 poses; 8–12 para la toma real')
    cam, pro = initial[initial.world], initial.projector()
    for d in (cam,pro):
        if (np.shape(d.K)!=(3,3) or not np.isfinite(d.K).all() or d.K[0,0]<=0 or d.K[1,1]<=0
                or not np.allclose(d.K[2],[0,0,1]) or d.K[0,1]!=0 or d.K[1,0]!=0):
            raise ValueError('intrínsecos iniciales inválidos o skew no soportado')
        if len(d.dist)<4 or not np.isfinite(d.dist).all() or np.any(np.asarray(d.dist).ravel()[5:]!=0):
            raise ValueError('distorsión inicial incompatible con Brown5')
    if initial.units != 'mm' or not np.allclose(cam.R, np.eye(3)) or not np.allclose(cam.t, 0):
        raise ValueError('el inicializador debe usar mundo=cámara y mm')
    for v in views:
        if tuple(v.cam_size) != cam.size or tuple(v.proj_size) != pro.size:
            raise ValueError(f'{v.name}: resolución incompatible con el rig')
        for a, shape in ((v.paper_mm, (4, 3)), (v.paper_cam, (4, 2))):
            if np.shape(a) != shape or not np.isfinite(a).all():
                raise ValueError(f'{v.name}: esquinas físicas inválidas')
        if not np.allclose(v.paper_mm[:, 2], 0) or np.linalg.matrix_rank(v.paper_mm[:, :2] - v.paper_mm[0, :2]) != 2:
            raise ValueError(f'{v.name}: hoja no planar o sin dimensiones')
        if v.sigma_paper_px <= 0 or v.sigma_pattern_px <= 0:
            raise ValueError('incertidumbre de observación debe ser positiva')
        if len(v.projected_cam) < 6 or np.shape(v.projected_cam) != np.shape(v.projected_px):
            raise ValueError(f'{v.name}: se necesitan >= 6 correspondencias sobre la hoja')
        for a, size in ((v.paper_cam, cam.size), (v.projected_cam, cam.size), (v.projected_px, pro.size)):
            a = np.asarray(a)
            if a.ndim != 2 or a.shape[1] != 2 or not np.isfinite(a).all() or np.any(a < 0) or np.any(a >= size):
                raise ValueError(f'{v.name}: píxeles fuera de resolución o no finitos')


def _pack_device(d):
    distortion = np.zeros(5)
    distortion[:min(5,len(d.dist))] = np.asarray(d.dist).ravel()[:5]
    return np.r_[np.log([d.K[0, 0], d.K[1, 1]]),
                 d.K[0, 2]/d.size[0], d.K[1, 2]/d.size[1], distortion]


def _device(x, original):
    d = copy.deepcopy(original)
    d.K = np.array([[np.exp(x[0]), 0, x[2]*d.size[0]],
                    [0, np.exp(x[1]), x[3]*d.size[1]], [0, 0, 1.]])
    d.dist = x[4:9].copy()
    return d


def _unpack(x, initial, n):
    cam = _device(x[:9], initial[initial.world])
    pro = _device(x[9:18], initial.projector())
    pro.R = cv2.Rodrigues(x[18:21])[0]; pro.t = x[21:24]*1000
    poses = [(cv2.Rodrigues(x[24+6*i:27+6*i])[0], x[27+6*i:30+6*i]*1000) for i in range(n)]
    return cam, pro, poses


def _residual(x, initial, views):
    cam, pro, poses = _unpack(x, initial, len(views))
    residual = []
    for v, (R, t) in zip(views, poses):
        physical = np.asarray(v.paper_mm) @ R.T + t
        X, depth = intersect(pro, v.projected_px, R, t)
        # Balance each pose so dense projected corners do not swamp the four
        # independently observed physical corners that anchor scale.
        weight = v.sigma_pattern_px * np.sqrt(max(1, len(X)/16))
        residual.extend(((project(cam, physical)-v.paper_cam).ravel()/v.sigma_paper_px,
                         (project(cam, X)-v.projected_cam).ravel()/weight,
                         np.minimum(depth, 0)/10, np.minimum(X[:, 2], 0)/10,
                         np.minimum(physical[:, 2], 0)/10))
    return np.concatenate(residual)


def _fit(x, initial, views, indices, max_nfev):
    lower, upper = np.full(len(x), -np.inf), np.full(len(x), np.inf)
    for j, d in ((0, initial[initial.world]), (9, initial.projector())):
        lower[j:j+2] = np.log(max(d.size)*0.1); upper[j:j+2] = np.log(max(d.size)*10)
        lower[j+2:j+4] = -0.5; upper[j+2:j+4] = 1.5
        lower[j+4:j+9] = [-1, -2, -.2, -.2, -2]
        upper[j+4:j+9] = [1, 2, .2, .2, 2]
    def expand(z):
        full = x.copy(); full[indices] = z
        return full
    result = least_squares(lambda z: _residual(expand(z), initial, views),
        np.clip(x[indices], lower[indices]+1e-10, upper[indices]-1e-10),
        bounds=(lower[indices], upper[indices]), loss='soft_l1', f_scale=2,
        x_scale='jac', max_nfev=max_nfev, ftol=1e-11, xtol=1e-11, gtol=1e-9)
    return expand(result.x), result


def calibrate(views, initial, *, distortion='brown5', restarts=2, bootstrap=0,
              evidence='recorded', seed=0, max_nfev=250):
    """Joint Kc,dc,Kp,dp,relative pose and paper poses, with fixed metric scale.

    Status is only a candidate, never a physical <=2 mm certificate. Bootstrap
    resamples whole views (not correlated points). Bounds and fixed parameters
    are explicit in the report; a poor Jacobian is not promoted to calibrated.
    """
    if evidence not in ('synthetic', 'recorded', 'live') or distortion not in ('k1', 'brown5'):
        raise ValueError('evidence/model inválido')
    if bootstrap < 0 or restarts < 1 or max_nfev < 1:
        raise ValueError('bootstrap >= 0, restarts y max_nfev >= 1')
    _check(views, initial)
    cam, pro = initial[initial.world], initial.projector()
    x = np.r_[_pack_device(cam), _pack_device(pro), cv2.Rodrigues(pro.R)[0].ravel(), pro.t/1000]
    for v in views:
        ok, rv, tv = cv2.solvePnP(np.asarray(v.paper_mm, float), np.asarray(v.paper_cam, float), cam.K, cam.dist)
        if not ok:
            raise ValueError(f'{v.name}: PnP sin solución')
        x = np.r_[x, rv.ravel(), tv.ravel()/1000]
    # Release focal/principal/poses, then radial k1, then Brown's full model.
    fixed_dist = [4,5,6,7,8,13,14,15,16,17]
    if distortion == 'k1':
        x[[5,6,7,8,14,15,16,17]] = 0
    stages = [np.array([i for i in range(len(x)) if i not in fixed_dist]),
              np.array([i for i in range(len(x)) if i not in [5,6,7,8,14,15,16,17]])]
    if distortion == 'brown5':
        stages.append(np.arange(len(x)))
    for active in stages:
        x, result = _fit(x, initial, views, active, max_nfev)
    rng = np.random.default_rng(seed)
    runs = [{'cost': float(result.cost), 'success': bool(result.success)}]
    for _ in range(max(0, restarts-1)):
        trial = x.copy(); trial[[0,1,9,10]] += rng.normal(0, .01, 4)
        trial[18:24] += rng.normal(0, .001, 6)
        trial, res = _fit(trial, initial, views, active, max_nfev)
        runs.append({'cost': float(res.cost), 'success': bool(res.success)})
        if res.cost < result.cost:
            x, result = trial, res
    cam, pro, poses = _unpack(x, initial, len(views))
    rig = Rig({cam.name: cam, pro.name: pro}, world=cam.name)
    per_view, errors_c, errors_p = [], [], []
    normals, xyz_all, invalid = [], [], 0
    for v, (R, t) in zip(views, poses):
        physical = v.paper_mm @ R.T+t
        X, depth = intersect(pro, v.projected_px, R, t)
        Y, _ = intersect(cam, v.projected_cam, R, t)
        ec = np.linalg.norm(project(cam, X)-v.projected_cam, axis=1)
        ep = np.linalg.norm(project(pro, Y)-v.projected_px, axis=1)
        eq = np.linalg.norm(project(cam, physical)-v.paper_cam, axis=1)
        errors_c.extend(ec); errors_c.extend(eq); errors_p.extend(ep)
        invalid += int(np.count_nonzero((depth <= 0) | (X[:, 2] <= 0)))
        normals.append(R[:, 2]); xyz_all.extend(physical)
        per_view.append({'name': v.name, 'paper_camera_px': stats(eq),
            'projected_camera_px': stats(ec), 'projector_px': stats(ep),
            'spatial_residuals': {'camera_pixels':v.projected_cam.tolist(),
                'camera_vectors_px':(project(cam,X)-v.projected_cam).tolist(),
                'projector_pixels':v.projected_px.tolist(),
                'projector_vectors_px':(project(pro,Y)-v.projected_px).tolist()},
            'paper_pose': {'R': R.tolist(), 't': t.tolist()},
            'camera_roi_px': [v.projected_cam.min(0).tolist(), v.projected_cam.max(0).tolist()],
            'projector_roi_px': [v.projected_px.min(0).tolist(), v.projected_px.max(0).tolist()]})
    J = np.asarray(result.jac)
    scale = np.maximum(np.linalg.norm(J, axis=0), 1e-12)
    singular = np.linalg.svd(J/scale, compute_uv=False)
    rank = int(np.count_nonzero(singular > singular[0]*1e-7))
    ratio = float(singular[-1]/singular[0])
    normals = np.asarray(normals)
    spread = np.linalg.svd(normals-normals.mean(0), compute_uv=False)
    issues = []
    if not result.success: issues.append('optimizer_not_converged')
    if rank < len(active) or ratio < 1e-7: issues.append('ill_conditioned')
    if len(spread) < 2 or spread[1] < .03: issues.append('insufficient_tilt_diversity')
    if invalid: issues.append('negative_depth')
    if np.any(result.active_mask): issues.append('parameter_at_bound')
    cam.rms, pro.rms = stats(errors_c)['rms'], stats(errors_p)['rms']
    if cam.rms > .5 or pro.rms > 1: issues.append('high_reprojection_error')
    # Local covariance is a diagnostic, not an independent physical uncertainty.
    variance = float(np.sum(result.fun**2)/max(1, len(result.fun)-len(active)))
    cov = np.linalg.pinv(J.T@J, rcond=1e-12)*variance
    samples, failures = [], []
    for k in range(bootstrap):
        ids = rng.integers(0, len(views), len(views))
        if len(set(ids)) < 4:
            failures.append({'run': k, 'reason': 'fewer_than_four_unique_views'}); continue
        bx = np.r_[x[:24], np.concatenate([x[24+6*i:30+6*i] for i in ids])]
        bx, br = _fit(bx, initial, [views[i] for i in ids], active, max_nfev)
        bc, bp, _ = _unpack(bx, initial, len(views))
        if not br.success:
            failures.append({'run': k, 'reason': 'optimizer_not_converged'}); continue
        samples.append({'camera_K': bc.K.tolist(), 'camera_dist': bc.dist.tolist(),
                        'projector_K': bp.K.tolist(), 'projector_dist': bp.dist.tolist(),
                        'projector_center_mm': bp.center_world().tolist()})
    report = {'schema_version': '1.0', 'artifact': 'rig-calibration-report', 'units': 'mm',
        'evidence': evidence, 'status': 'rejected' if issues else 'candidate', 'issues': issues,
        'method': 'joint_projector_ray_plane', 'pixel_convention': 'opencv_integer_centers',
        'poses': len(views), 'training_view_ids': [v.name for v in views],
        'model': {'distortion': distortion, 'free': 'fx,fy,cx,cy of both devices; relative pose; paper poses; distortion per model',
                  'fixed': ['camera_world_pose', 'paper_dimensions_mm', 'skew=0'] +
                  (['k2=p1=p2=k3=0'] if distortion == 'k1' else []),
                  'bounds': {'focal_image_max_multiple': [.1,10], 'principal_image_fraction': [-.5,1.5],
                             'k1': [-1,1], 'k2_k3': [-2,2], 'p1_p2': [-.2,.2]},
                  'weighting': 'physical / sigma; projected / (sigma * sqrt(max(1,N/16)))',
                  'loss': 'soft_l1, scale=2; no outlier deletion'},
        'baseline_mm': float(np.linalg.norm(pro.center_world())),
        'rms_px': {'cam': cam.rms, 'proj': pro.rms}, 'per_view': per_view,
        'sampled_volume_mm': [np.min(xyz_all, axis=0).tolist(), np.max(xyz_all, axis=0).tolist()],
        'validated_volume_mm': None,
        'observability': {'rank': rank, 'parameters': len(active), 'scaled_singular_ratio': ratio,
                          'normal_spread_singular_values': spread.tolist()},
        'uncertainty': {'kind': 'local_linear_weighted; conditional, not certification',
                        'active_parameter_indices': active.tolist(), 'parameter_std': np.sqrt(np.maximum(0,np.diag(cov))).tolist(),
                        'bootstrap_unit': 'view', 'bootstrap_requested': bootstrap,
                        'bootstrap_samples': samples, 'bootstrap_failures': failures},
        'restarts': runs,
        'physical_validation': {'status': 'unverified', 'tolerance_mm': 2.0,
                                'reason': 'reserved physical checks and independent second calibration required'}}
    rig.extra.update({'evidence': evidence, 'metric_report': report,
                      'pixel_convention': 'opencv_integer_centers'})
    from .metric_validation import pixel_uncertainty
    uncertainty = pixel_uncertainty(rig,np.concatenate([v.projected_cam for v in views]),
                                    np.concatenate([v.projected_px for v in views]))
    report['pixel_sensitivity'] = {'kind':uncertainty['kind'], 'sigma_px':uncertainty['sigma_px'],
                                  'sigma_3d_mm':stats(uncertainty['sigma_3d_mm']),
                                  'warning':'one sigma only; excludes calibration/reference uncertainty'}
    rig.notes = 'Joint metric candidate; physical <=2 mm validation is separate.'
    return rig
