import numpy as np
import pytest

from slscan.radiometry import plan, pattern_image, plan_manifest
from slscan.graycode import parse_sltk_name


def test_ramps_holdout_bases_and_rgb_orientation():
    specs = plan()
    assert len([p for p in specs if p.role == "fit"]) == 24
    assert len([p for p in specs if p.role == "base"]) == 6
    fit = {p.rgb for p in specs if p.role == "fit"}
    held = {p.rgb for p in specs if p.role == "held_out"}
    assert not fit & held
    assert all(parse_sltk_name(p.name) is None for p in specs)
    red = next(p for p in specs if p.name == "r_240")
    image = pattern_image(red, (8, 4))
    assert image.shape == (4, 8, 3) and np.array_equal(image[0, 0], [0, 0, 240])
    refs = [p.rgb for p in specs if p.role == "repeat"]
    assert refs[0] == refs[1]
    meta = plan_manifest("session", "rig-hash")
    assert meta["minimum_frames"] == 108
    assert meta["status"] == "planned_not_acquired"
    assert meta["units"] == "relative" and meta["digital_encoding"] == "digital_number_8bit"


def test_invalid_levels_do_not_silently_overlap_validation():
    for levels in ((0, 32), (32, 32), (80, 176), (256,), ()):
        with pytest.raises(ValueError):
            plan(levels)
