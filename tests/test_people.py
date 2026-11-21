"""Personas (pedido 046): sombreado y geometría sin modelos ni cámara."""

from __future__ import annotations

import numpy as np

from playground.people import david, relight as R


def _flat(h=120, w=160):
    n = np.zeros((h, w, 3), np.float32); n[..., 2] = 1.0       # superficie de frente a la cámara
    return n, np.zeros((h, w), np.float32), np.ones((h, w), np.float32)


def _light(x, y, z=30.0, d=(0, 0, -1), cone=360.0, color=(1, 1, 1)):
    return {"pos": np.array([x, y, z], np.float32), "dir": np.asarray(d, np.float32), "color": np.asarray(color, np.float32),
            "intensity": 1.0, "cone_deg": cone, "radius_px": 60.0}


def test_light_is_brightest_under_the_finger():
    n, z, _ = _flat()
    sh = R.light_map(n, z, [_light(40, 60)], scale=1.0)
    lum = sh.sum(-1)
    yx = np.unravel_index(np.argmax(lum), lum.shape)
    assert abs(yx[0] - 60) <= 2 and abs(yx[1] - 40) <= 2
    assert lum[60, 40] > 4 * lum[60, 150]                       # cae con la distancia


def test_normal_facing_away_gets_no_light():
    n, z, _ = _flat()
    n[:, 80:] = [1, 0, 0]                                          # mitad derecha mira a la derecha
    sh = R.light_map(n, z, [_light(40, 60, z=5)], scale=1.0, wrap=0.0, spec=0.0)
    assert sh[60, 100].sum() < 1e-3                                # la luz está a su izquierda
    n2 = n.copy(); n2[:, 80:] = [-1, 0, 0]                         # mirando hacia la luz: sí
    assert R.light_map(n2, z, [_light(40, 60, z=5)], scale=1.0)[60, 100].sum() > 0.05


def test_cone_limits_the_spot():
    n, z, _ = _flat()
    d = np.array([50, 0, -40], np.float32); d /= np.linalg.norm(d)   # apunta al punto (130, 60) de la superficie
    wide = R.light_map(n, z, [_light(80, 60, z=40, d=d, cone=360)], scale=1.0)
    narrow = R.light_map(n, z, [_light(80, 60, z=40, d=d, cone=40)], scale=1.0)
    assert narrow[60, 40].sum() < 0.05 * wide[60, 40].sum()        # detrás del dedo
    assert narrow[60, 130].sum() > 0.3 * wide[60, 130].sum()       # hacia donde apunta


def test_half_res_matches_full_res():
    n, z, _ = _flat()
    full = R.light_map(n, z, [_light(60, 50)], scale=1.0)
    half = R.light_map(n, z, [_light(60, 50)], scale=0.5)
    assert np.abs(full - half).mean() < 0.02


def test_finger_light_points_along_index():
    lm = np.zeros((21, 3), np.float32)
    lm[6] = [50, 80, 0]; lm[8] = [50, 40, 0]                       # índice hacia arriba en la imagen
    L = R.finger_lights([{"landmarks": lm}], np.zeros((120, 160), np.float32))[0]
    assert L["dir"][1] < -0.99 and np.allclose(L["pos"][:2], [50, 40])


def test_to_image_frame_flips_y_only():
    n = np.array([[[0.1, 0.5, 0.8]]], np.float32)
    assert np.allclose(R.to_image_frame(n), [[[0.1, -0.5, 0.8]]])


def test_compose_shapes_and_dark_background():
    n, z, a = _flat()
    a[:, :40] = 0
    frame = np.full((120, 160, 3), 200, np.uint8)
    L = [_light(100, 60)]
    out = R.compose_screen(frame, a, R.light_map(n, z, L), L)
    assert out.shape == frame.shape and out.dtype == np.uint8
    assert out[10, 10].mean() < 60                                  # fondo casi negro
    proj = R.compose_projector(a, R.light_map(n, z, L))
    assert proj[:, :40].max() == 0                                  # el proyector no pinta fuera de la persona


def test_prepare_unpad_roundtrip():
    yy, xx = np.mgrid[0:90, 0:160]
    img = np.dstack([xx * 255 / 159, yy * 255 / 89, np.full_like(xx, 128)]).astype(np.uint8)   # suave: sobrevive a la escala
    x, meta = david.prepare(img, 64)
    assert x.shape == (1, 3, 64, 64) and x.dtype == np.float32
    back = david.unpad(x[0].transpose(1, 2, 0), meta)
    assert back.shape == (90, 160, 3)
    small = np.asarray(img, np.float32) / 255
    assert np.abs(back - small).mean() < 0.02                      # mismo contenido, bien alineado


def test_beat_gain():
    assert R.beat_gain(None, 0.5) == 1.0
    assert R.beat_gain({"phase": 0.0}, 0.5) > R.beat_gain({"phase": 0.5}, 0.5) > 1.0
