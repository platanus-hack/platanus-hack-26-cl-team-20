"""Effective RGB/brilliance transfer, independent of angular BRDF fitting."""
from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares, lsq_linear


def fit_response(patterns, measured, *, high_reference=1., noise=.001):
    """Fit y=b+A*p**gamma on fit patterns only, test mixtures and drift separately.

    One spatial patch per call; callers can tile the UV atlas. This is a composed
    camera/projector/surface response, never a measurement of camera gamma alone.
    """
    y = np.asarray(measured, float)
    if y.shape != (len(patterns), 3) or not np.isfinite(y).all() or not np.isfinite(high_reference) or high_reference <= 0:
        raise ValueError("Finite RGB for every pattern and positive fixed reference required")
    p = np.array([s.rgb for s in patterns], float) / 255
    roles = np.array([s.role for s in patterns])
    base = y[roles == "base"]
    if len(base) < 2 or np.max(np.ptp(base, axis=0)) > max(.004, 5*noise):
        raise ValueError("Missing bases or ambient drift")
    repeats = y[roles == "repeat"]
    if len(repeats) < 2 or np.max(np.ptp(repeats, axis=0)) > max(.004, 5*noise):
        raise ValueError("Reference drift: motion, exposure or illumination changed")
    b = np.median(base, axis=0)
    use = (roles == "fit") & np.all((y < .995) & (y >= 0), axis=-1)
    if use.sum() < 12:
        raise ValueError("Insufficient unsaturated fit samples")
    def predict(q, rgb):
        return b + (rgb ** q[:3]) @ q[3:].reshape(3, 3).T
    fit = least_squares(lambda q: (predict(q, p[use])-y[use]).ravel(),
                        np.r_[[2.]*3, (np.eye(3)*.5 + .01).ravel()],
                        bounds=(np.r_[[.3]*3, [0.]*9], np.r_[[4.]*3, [3.]*9]),
                        loss="soft_l1", f_scale=noise)
    matrix = fit.x[3:].reshape(3, 3)
    hold = (roles == "held_out") & np.all((y < .995) & (y >= 0), axis=-1)
    if hold.sum() < 2:
        raise ValueError("Independent RGB validation missing or saturated")
    error = np.mean(np.abs(predict(fit.x, p[hold])-y[hold]), axis=0) / high_reference
    upper = np.max(p[use], axis=0)
    return dict(gamma=fit.x[:3].tolist(), mixing_rgb=matrix.tolist(), base=b.tolist(),
                response_kind="effective", held_out_mae_rgb=error.tolist(),
                accepted=bool(np.max(error) <= .05 and np.linalg.cond(matrix) < 100),
                saturation_count=int(np.sum(np.any(y >= .995, axis=1))),
                gamut=dict(input_min=[0., 0., 0.], input_max=upper.tolist(),
                           output_black=b.tolist(), output_white=predict(fit.x, upper).tolist()),
                reference=dict(high_minus_base=high_reference, normalization="fixed before validation",
                               reflectance_absolute=False),
                channel_response=[(np.linspace(0, 1, 256)**g).tolist() for g in fit.x[:3]])


def predict_response(model, rgb):
    return np.asarray(model["base"]) + np.asarray(rgb)**model["gamma"] @ np.asarray(model["mixing_rgb"]).T


def compensate(model, target):
    if not model["accepted"]:
        raise ValueError("Unvalidated or ill-conditioned response")
    gamma = np.asarray(model["gamma"])
    upper = np.asarray(model["gamut"]["input_max"])**gamma
    result = lsq_linear(model["mixing_rgb"], np.asarray(target)-model["base"], bounds=(np.zeros(3), upper))
    rgb = result.x**(1/gamma)
    residual = predict_response(model, rgb)-target
    return dict(rgb=rgb.tolist(), residual_rgb=residual.tolist(),
                in_gamut=bool(np.max(np.abs(residual)) <= .003))
