"""Native masks, real holes and composition for the incremental party textures."""
import json

import cv2
import numpy as np
import pytest

from slscan.contracts import digest
from playground.appearance import install_scene_regions
from playground.scene import Scene
from playground.runtime import Runtime


@pytest.fixture
def regions(tmp_path):
    scan = tmp_path/'captures'/'scan'; scan.mkdir(parents=True)
    root = tmp_path/'reports'/'scene_regions'/'scan'; root.mkdir(parents=True)
    masks = {}
    for name, center, axes in [('oreja_izquierda', (100, 55), (18, 48)),
                               ('oreja_superior', (165, 50), (20, 45)),
                               ('conejo', (135, 145), (68, 78)),
                               ('mascara_azteca', (270, 140), (38, 62))]:
        m = np.zeros((240, 320), np.uint8)
        cv2.ellipse(m, center, axes, 0, 0, 360, 255, -1)
        masks[name] = m
    masks['conejo'] |= masks['oreja_izquierda'] | masks['oreja_superior']
    masks['oreja_izquierda'][45:50, 96:101] = 0
    photo = np.full((240, 320, 3), 140, np.uint8)
    for cx in (255, 285): cv2.ellipse(photo, (cx, 120), (9, 5), 0, 0, 360, (12, 12, 12), -1)
    cv2.imwrite(str(scan/'scene_pov.png'), photo)
    cv2.imwrite(str(root/'appearance.png'), photo)
    asset = lambda p: dict(path=p.name, sha256=digest(p))
    objects = {}
    for name, mask in masks.items():
        p = root/(name+'.png'); cv2.imwrite(str(p), mask)
        objects[name] = dict(name=name, kind='prop', mask=asset(p))
    data = dict(schema_version='1.0', coordinate_space='projector_px', projector_size=[320, 240],
                input_hashes={'scene_pov.png':digest(scan/'scene_pov.png')},
                appearance=asset(root/'appearance.png'), objects=objects)
    (root/'manifest.json').write_text(json.dumps(data))
    scene = Scene(table_mm=(320, 240), projector_px=True, scan_dir=str(scan))
    install_scene_regions(scene)
    return scene, masks


def test_tiger_moves_but_never_leaks(regions, tmp_path):
    scene, masks = regions
    rt = Runtime(scene, canvas_size=(320, 240), demo=False, requests_dir=tmp_path/'requests')
    for name in ('oreja_izquierda', 'oreja_superior'): rt.spawn('tigre_animado', name)
    a = rt.tick(.1).copy(); b = rt.tick(.7).copy()
    mask = masks['oreja_izquierda'] | masks['oreja_superior']
    assert a.any() and not np.array_equal(a, b)
    assert not a[mask == 0].any() and not b[mask == 0].any()
    assert len(rt.instances) == 2 and not rt.registry.errors
    assert (a[..., 2] > a[..., 0]).any()  # orange, not BGR-swapped blue


def test_leopard_excludes_ears_even_when_drawn_last(regions, tmp_path):
    scene, masks = regions
    rt = Runtime(scene, canvas_size=(320, 240), demo=False, requests_dir=tmp_path/'requests')
    for target in ('oreja_izquierda', 'oreja_superior'): rt.spawn('tigre_animado', target, {'speed':0, 'audio':''})
    ears = rt.tick(.1).copy()
    rt.spawn('leopardo_fucsia', 'conejo')
    a = rt.tick(.1).copy(); b = rt.tick(.8).copy()
    ear_mask = (masks['oreja_izquierda'] | masks['oreja_superior']) > 0
    np.testing.assert_array_equal(a[ear_mask], ears[ear_mask])
    assert not b[masks['conejo'] == 0].any()
    assert not np.array_equal(a, b) and len(rt.instances) == 3
    body = (masks['conejo'] > 0) & ~ear_mask
    assert np.mean(a[..., 2][body]) > 2*np.mean(a[..., 1][body])


def test_mask_eyes_come_from_texture_and_animation_stays_clipped(regions, tmp_path):
    from playground.effects.mascara_viva import detect_eyes
    scene, masks = regions
    rt = Runtime(scene, canvas_size=(320, 240), demo=False, requests_dir=tmp_path/'requests')
    state = rt.spawn('mascara_viva', 'mascara_azteca')
    a = rt.tick(.1).copy(); b = rt.tick(.9).copy()
    inst = rt.instances[state['id']]
    assert inst.state()['eyes_detected'] == 2
    assert a.any() and not np.array_equal(a, b)
    assert not b[masks['mascara_azteca'] == 0].any()
    assert detect_eyes(np.full((80, 100, 3), 100, np.uint8), np.ones((80, 100), bool)) == []


def test_mask_bass_changes_light_without_touching_background(regions):
    from playground.effects.base import Canvas
    from playground.effects.mascara_viva import MascaraViva
    scene, masks = regions
    effect = MascaraViva(scene.objects['mascara_azteca'])
    effect.update(.1, scene)
    a = Canvas(np.full((240, 320, 3), 37, np.uint8), 1, flip_y=False)
    b = Canvas(a.img.copy(), 1, flip_y=False)
    effect.draw(a)
    scene.audio['bass'] = 1
    effect.update(0, scene); effect.draw(b)
    assert not np.array_equal(a.img, b.img)
    assert (b.img[masks['mascara_azteca'] == 0] == 37).all()
