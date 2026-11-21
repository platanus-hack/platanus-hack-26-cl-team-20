"""Small CPU inverse renderer. Units mm; relative power referenced at one metre.

Opaque isotropic dielectric: Lambert diffuse + GGX/Smith/Schlick specular.
This approximation does not identify spectral absorption, metals, transmission,
subsurface scattering, anisotropy or arbitrary area-light emission profiles.
"""
from __future__ import annotations

import cv2
import numpy as np
from scipy.optimize import least_squares


def unit(v):
    a = np.asarray(v, float)
    return a / np.maximum(np.linalg.norm(a, axis=-1, keepdims=True), 1e-12)


def temperature(rgb, calibrated=False):
    """Approximate CCT only for calibrated linear sRGB and near-white emitters."""
    if not calibrated or np.min(rgb) <= 0:
        return None
    xyz = np.array([[.4124564, .3575761, .1804375], [.2126729, .7151522, .0721750],
                    [.0193339, .1191920, .9503041]]) @ np.asarray(rgb)
    x, y = xyz[:2] / xyz.sum()
    if not (.25 < x < .46 and .25 < y < .43) or abs(y - .1858) < 1e-5:
        return None
    n = (x - .3320) / (y - .1858)
    k = -449 * n**3 + 3525 * n**2 - 6823.3 * n + 5520.33
    return float(k) if 2500 <= k <= 10000 else None


def emitter_regions(on, off, K, dist, R, t, *, threshold=.5):
    """Bright on/off connected regions -> rays. Never turn one pixel into a depth.

    Short-exposure bracket expected. Reflections can also be bright: candidates
    must be associated with the switched emitter before photometric localization.
    """
    delta = np.maximum(np.asarray(on, float) - off, 0).mean(axis=-1)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats((delta > threshold).astype(np.uint8))
    out = []
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] < 2:
            continue
        pixel = centroids[i]
        xy = cv2.undistortPoints(pixel.reshape(1, 1, 2), np.asarray(K, float), np.asarray(dist, float))[0, 0]
        direction = unit(np.asarray(R).T @ np.r_[xy, 1.])
        radius_px = np.sqrt(stats[i, cv2.CC_STAT_AREA] / np.pi)
        angular_radius = float(np.arctan(radius_px / min(K[0, 0], K[1, 1])))
        out.append(dict(pixel=pixel.tolist(), origin_mm=(-np.asarray(R).T @ t).tolist(),
                        direction=direction.tolist(), pixels=int(stats[i, cv2.CC_STAT_AREA]),
                        angular_radius_rad=angular_radius,
                        inferred_type="area" if angular_radius > .025 else "point",
                        type_reason="apparent angular extent; point is provisional until photometric validation",
                        saturated=bool(np.any(np.asarray(on)[labels == i] >= .995))))
    return out


def irradiance(xyz, positions, powers):
    delta = np.asarray(positions)[:, None, :] - np.asarray(xyz)[None, :, :]
    distance = np.linalg.norm(delta, axis=-1)
    return unit(delta), np.asarray(powers)[:, None, :] / np.maximum(distance[..., None] / 1000, .001)**2


def render(xyz, normals, albedo, roughness, specular, camera_mm, positions, powers):
    l, e = irradiance(xyz, positions, powers)
    v = unit(np.asarray(camera_mm) - xyz)[None, :, :]
    n = np.asarray(normals)[None, :, :]
    h = unit(l + v)
    nl = np.maximum(np.sum(n * l, axis=-1), 0)
    facing_camera = np.sum(n * v, axis=-1) > 0
    nv = np.maximum(np.sum(n * v, axis=-1), 1e-6)
    nh = np.maximum(np.sum(n * h, axis=-1), 0)
    vh = np.maximum(np.sum(v * h, axis=-1), 0)
    a2 = np.asarray(roughness)[None, :]**4
    d = a2 / np.maximum(np.pi * (nh * nh * (a2 - 1) + 1)**2, 1e-12)
    g_l = 2 * nl / np.maximum(nl + np.sqrt(a2 + (1-a2)*nl*nl), 1e-9)
    g_v = 2 * nv / np.maximum(nv + np.sqrt(a2 + (1-a2)*nv*nv), 1e-9)
    f = np.asarray(specular)[None, :] + (1 - np.asarray(specular)[None, :]) * (1 - vh)**5
    diffuse = np.asarray(albedo)[None, :, :] * (1 - f[..., None]) / np.pi
    brdf = diffuse + (d * g_l * g_v * f / np.maximum(4*nl*nv, 1e-9))[..., None]
    return e * nl[..., None] * brdf * facing_camera[..., None]


def locate_light(light_id, origin, ray, xyz, normals, reflectance, observed, *,
                 valid=None, bounds_mm=(200., 10000.), noise=.001, calibrated_color=False,
                 source_type="point"):
    """Fit depth along observed emitter ray and RGB power on separate matte references.

    reflectance is known *relative* diffuse reflectance, fixing the power/albedo
    gauge. Three or more spatially distinct patches needed. Flat/remote ambiguity
    is rejected by the data Jacobian, residual and depth uncertainty.
    """
    origin, ray = np.asarray(origin, float), unit(ray)
    xyz, normals, rho, y = map(lambda a: np.asarray(a, float), (xyz, normals, reflectance, observed))
    use = np.all(np.isfinite(y) & (y > noise) & (y < .995), axis=1)
    if valid is not None:
        use &= valid
    base = dict(id=light_id, type=source_type, observation="direction_only", position_mm=None,
                direction=ray.tolist(), intensity_rgb_relative=[0., 0., 0.], temperature_k=None,
                confidence=0., uncertainty=dict(reason="insufficient independent matte references",
                                                ray_origin_mm=origin.tolist(), power_observed=False))
    if use.sum() < 4 or np.linalg.norm(ray) < .9 or source_type != "point":
        base["uncertainty"]["reason"] = "point fit unsupported for extended/group source" if source_type != "point" else base["uncertainty"]["reason"]
        return base
    x, n, r, target = xyz[use], normals[use], rho[use], y[use]
    def residual(q):
        p = origin + ray * np.exp(q[0])
        l, e = irradiance(x, [p], [np.exp(q[1:])])
        pred = r * np.maximum(np.sum(n*l[0], axis=-1), 0)[:, None] * e[0] / np.pi
        return (pred - target).ravel()
    lo = np.r_[np.log(bounds_mm[0]), [-12.] * 3]
    hi = np.r_[np.log(bounds_mm[1]), [8.] * 3]
    candidates = [least_squares(residual, np.r_[np.log(depth), [-1.] * 3], bounds=(lo, hi),
                                loss="soft_l1", f_scale=noise, max_nfev=250)
                  for depth in np.geomspace(bounds_mm[0]*1.01, bounds_mm[1]/1.01, 4)]
    fit = min(candidates, key=lambda f: np.mean(residual(f.x)**2))
    singular = np.linalg.svd(fit.jac, compute_uv=False)
    rms = float(np.sqrt(np.mean(residual(fit.x)**2)))
    covariance = np.linalg.pinv(fit.jac.T @ fit.jac) * max(noise, rms)**2
    depth = np.exp(fit.x[0])
    std = float(depth * np.sqrt(covariance[0, 0]))
    condition = singular[0] / max(singular[-1], 1e-15)
    base["uncertainty"].update(depth_std_mm=std, condition=float(condition), rms_linear=rms,
                               reference_count=int(use.sum()), includes_ray_uncertainty=False)
    if condition > 1e5 or std / depth > .1 or rms > max(.006, 5*noise) or np.any(fit.active_mask):
        base["uncertainty"]["reason"] = "depth/power ambiguous or point source model mismatch"
        return base
    power = np.exp(fit.x[1:])
    base.update(observation="position_estimated", position_mm=(origin + ray*depth).tolist(),
                intensity_rgb_relative=power.tolist(), temperature_k=temperature(power, calibrated_color),
                confidence=float(np.clip(1 - std / depth, 0, 1)))
    base["uncertainty"].update(reason="conditional on emitter association and matte reference geometry",
                               power_observed=True, normalization="relative matte reference; inverse square at 1000 mm")
    return base


def fit_texels(xyz, ng, tangent, bitangent, camera, positions, powers, images, valid, *, noise=.001, progress=None):
    """Fit only training lights. Confidence comes from data, without prior rows.

    Unobserved texels keep preview defaults and zero confidence. Absorption is
    intentionally not fitted: a single-view RGB reflection experiment cannot
    identify a volumetric or spectral absorption coefficient.
    """
    count = len(xyz)
    result = dict(albedo=np.zeros((count, 3)), normal=np.asarray(ng).copy(),
                  roughness=np.full(count, .5), specular=np.full(count, .04),
                  confidence=np.zeros((count, 4)), fit_rms=np.zeros(count),
                  condition=np.full(count, 1e12), samples=valid.sum(axis=0).astype(np.int32))
    directions, _ = irradiance(xyz, positions, powers)
    for i in range(count):
        if progress and i % 16 == 0:
            progress(i / count, "Ajustando albedo/normal/GGX por texel")
        mask = valid[:, i] & np.all(np.isfinite(images[:, i]), axis=-1)
        mask &= (directions[:, i] @ ng[i]) > .05
        mask &= np.dot(ng[i], np.asarray(camera)-xyz[i]) > 0
        result["samples"][i] = int(mask.sum())
        l = directions[mask, i]
        if len(l) < 4 or np.linalg.cond(l) > 30:
            continue
        target, p, power = images[mask, i], np.asarray(positions)[mask], np.asarray(powers)[mask]
        def unpack(q):
            n = unit(ng[i] + tangent[i]*q[3] + bitangent[i]*q[4])
            return n, q[:3], q[5], q[6]
        def residual(q):
            n, rho, rough, f0 = unpack(q)
            pred = render(xyz[i:i+1], n[None], rho[None], [rough], [f0], camera, p, power)[:, 0]
            return (pred - target).ravel()
        candidates = [least_squares(residual, [.4, .4, .4, 0, 0, rough, .04],
                      bounds=([.001]*3 + [-.5, -.5, .08, .001], [.98]*3 + [.5, .5, .95, .2]),
                      loss="soft_l1", f_scale=noise, max_nfev=110) for rough in (.25, .65)]
        fit = min(candidates, key=lambda f: np.mean(residual(f.x)**2))
        n, rho, rough, f0 = unpack(fit.x)
        rms = float(np.sqrt(np.mean(residual(fit.x)**2)))
        singular = np.linalg.svd(fit.jac, compute_uv=False)
        cond = float(singular[0] / max(singular[-1], 1e-15))
        covariance = np.linalg.pinv(fit.jac.T @ fit.jac) * max(noise, rms)**2
        std = np.sqrt(np.maximum(np.diag(covariance), 0))
        # Reject rank deficiency before pinv can make null directions look certain.
        if cond < 1e5 and rms < max(.006, 5*noise) and fit.success:
            conf = [float(np.max(std[:3]) < .08), float(np.max(std[3:5]) < .087),
                    float(std[5] < .1 and not fit.active_mask[5]),
                    float(std[6] < .025 and not fit.active_mask[6])]
            result["confidence"][i] = conf
        result["albedo"][i], result["normal"][i] = rho, n
        result["roughness"][i], result["specular"][i] = rough, f0
        result["fit_rms"][i], result["condition"][i] = rms, min(cond, 1e12)
    return result
