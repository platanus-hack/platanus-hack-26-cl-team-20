"""cv2.remap falla con mapas de más de 32767 filas: sample_color muestrea por bloques y da lo mismo que el remap directo."""

import cv2
import numpy as np

from slscan.metric_recon import sample_color


def test_sample_color_grande_coincide_con_remap_directo():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (120, 160, 3), np.uint8)
    pts = np.column_stack([rng.uniform(0, 159, 40000), rng.uniform(0, 119, 40000)]).astype(np.float32)
    out = sample_color(img, pts)
    assert out.shape == (40000, 3)
    ref = cv2.remap(img, pts[:1000, 0].reshape(-1, 1), pts[:1000, 1].reshape(-1, 1), cv2.INTER_LINEAR).reshape(-1, 3)
    assert np.array_equal(out[:1000], ref)
    assert sample_color(img, np.zeros((0, 2))).shape == (0, 3)
