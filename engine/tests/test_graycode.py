import numpy as np

from slscan import graycode
from slscan.graycode import AXIS_X, AXIS_Y, INVALID


def test_gray_roundtrip():
    v = np.arange(0, 4096, dtype=np.uint16)
    assert np.array_equal(graycode.gray2bin(graycode.bin2gray(v).astype(np.uint16)), v)


def test_sequence_order_matches_sltk():
    specs = graycode.sequence((1280, 800))
    assert len(specs) == 2 * (11 + 10)
    assert specs[0].tag == "[00-10-00_00]" and specs[1].tag == "[00-10-01_01]"
    assert specs[22].tag == "[01-09-00_22]"
    assert graycode.parse_sltk_name("1481302049.551275_[00-10-00_00]_img.jpg") == specs[0]


def test_decode_synthetic_identity():
    """Un 'proyector' de 64x32 visto por una cámara que lo copia tal cual (con gamma y ruido)."""
    size = (64, 32)
    rng = np.random.default_rng(0)
    items = []
    for spec in graycode.sequence(size):
        pat = graycode.pattern(size, spec.axis, spec.bit, spec.inverse).astype(np.float32)
        img = 40 + 0.6 * pat + rng.normal(0, 1.5, pat.shape)       # contraste parcial + ruido
        bgr = np.repeat(np.clip(img, 0, 255).astype(np.uint8)[..., None], 3, axis=2)
        items.append((spec, bgr))
    dec = graycode.decode(items, threshold=5)
    ys, xs = np.mgrid[0:32, 0:64]
    assert dec.valid.all()
    assert np.array_equal(dec.px, xs.astype(np.uint16))
    assert np.array_equal(dec.py, ys.astype(np.uint16))
    assert dec.stats["valid"] == 64 * 32


def test_decode_marks_low_contrast_invalid():
    size = (16, 8)
    items = []
    for spec in graycode.sequence(size):
        pat = graycode.pattern(size, spec.axis, spec.bit, spec.inverse).astype(np.float32)
        img = (100 + 0.5 * pat).astype(np.uint8)
        img[:, :4] = 100                                   # columnas 0..3 sin señal
        items.append((spec, np.repeat(img[..., None], 3, axis=2)))
    dec = graycode.decode(items)
    assert (dec.px[:, :4] == INVALID).all() and dec.valid[:, 4:].all()
