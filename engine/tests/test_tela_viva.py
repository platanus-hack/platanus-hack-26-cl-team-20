"""Physical invariants and exact clipping, independent of private scan assets."""
import cv2
import numpy as np
import pytest

from playground.effects.base import Canvas
from playground.effects.tela_viva import TelaViva
from playground.scene import Scene, SceneObject
from playground.vectortrace import write_bundle


@pytest.fixture
def setup(tmp_path):
    h, w = 180, 240
    yy, xx = np.mgrid[:h, :w]
    photo = np.stack((xx % 255, yy % 255, (xx+yy) % 255), axis=2).astype(np.uint8)
    mask = np.zeros((h, w), np.uint8)
    cv2.ellipse(mask, (120, 90), (100, 76), 0, 0, 360, 255, -1)
    cv2.circle(mask, (75, 70), 8, 0, -1)  # exact hole must survive warping
    source, maskfile = tmp_path/'scene_pov.png', tmp_path/'mask.png'
    cv2.imwrite(str(source), photo); cv2.imwrite(str(maskfile), mask)
    bundle = tmp_path/'bundle'
    write_bundle(source, maskfile, bundle, target_id='cuadro', colors=3, max_size=120)
    s = Scene(table_mm=(w, h), projector_px=True, scan_dir=str(tmp_path))
    # Deliberately wrong quad: a fallback would leak everywhere.
    s.objects['cuadro'] = SceneObject('cuadro', 'cuadro', 'frame', [0, 0, 0, w, h, 0],
                                     [[0, 0], [w, 0], [w, h], [0, h]])
    return s, {'bundle': str(bundle/'trace.json')}, photo, mask


def effect(setup, **params):
    scene, config, _, _ = setup
    e = TelaViva(scene.objects['cuadro'], {**config, **params})
    e.update(0, scene)
    return e


@pytest.mark.parametrize('shape', ['cara', 'mano', 'esfera'])
def test_collision_pins_and_no_mask_leak(setup, shape):
    scene, _, _, mask = setup
    e = effect(setup, forma=shape)
    for _ in range(90):
        e.update(1/30, scene)
    assert np.isfinite(e.pos).all()
    assert e.pos[..., 2].max() > 3
    np.testing.assert_array_equal(e.pos[~e.free], e.rest[~e.free])
    surface = e._collider(e.pos[..., 0], e.pos[..., 1], e.phase, shape, 1.)
    assert (e.pos[..., 2][e.free] >= surface[e.free]-1e-5).all()
    img = np.full((*mask.shape, 3), 37, np.uint8)
    e.draw(Canvas(img, 1, flip_y=False))
    assert (img[mask == 0] == 37).all()
    assert (img[mask > 0] != 37).any()


def test_zero_force_is_exact_photo_and_boundary_is_fixed(setup):
    scene, _, photo, mask = setup
    e = effect(setup, fuerza=0)
    for _ in range(20):
        e.update(.1, scene)
    np.testing.assert_allclose(e.pos, e.rest, atol=1e-4)
    img = np.zeros_like(photo)
    e.draw(Canvas(img, 1, flip_y=False))
    np.testing.assert_array_equal(img[mask > 0], photo[mask > 0])


def test_audio_pushes_geometry_and_release_has_memory(setup):
    scene, _, _, _ = setup
    quiet = effect(setup, velocidad=0, audio='bass')
    loud = effect(setup, velocidad=0, audio='bass')
    for _ in range(30):
        quiet.update(1/30, scene)
    scene.audio['bass'] = 1.
    for _ in range(30):
        loud.update(1/30, scene)
    assert loud.pos[..., 2].max() > quiet.pos[..., 2].max()+1
    scene.audio['bass'] = 0.
    loud.set('fuerza', 0)
    loud.update(1/60, scene)
    assert loud.pos[..., 2].max() > .1  # withdrawal is dynamic, not a preset image
    for _ in range(180):
        loud.update(1/30, scene)
    assert np.max(np.abs(loud.pos-loud.rest)) < .5


def test_fixed_step_and_stalled_frame_are_bounded(setup):
    scene, _, _, _ = setup
    a, b = effect(setup), effect(setup)
    for _ in range(60): a.update(1/60, scene)
    for _ in range(30): b.update(1/30, scene)
    np.testing.assert_allclose(a.pos, b.pos, atol=1e-5)
    before = a.steps
    a.update(20, scene)
    assert a.steps-before == 2 and np.isfinite(a.pos).all()


def test_force_audio_limits_do_not_push_an_infinite_plane(setup):
    scene, _, _, _ = setup
    e = effect(setup, fuerza=2, rigidez=0, velocidad=3, audio='bass')
    scene.audio['bass'] = 1
    for _ in range(40): e.update(.1, scene)
    assert np.isfinite(e.pos).all()
    for shape in ('cara', 'mano', 'esfera'):
        outside = e._collider(np.array([1e5]), np.array([1e5]), 3, shape, 2)
        assert outside[0] < 0
    e.set('fuerza', float('nan'))
    with pytest.raises(ValueError, match='finite'): e.update(0, scene)


def test_rejects_stale_source_mask_and_alignment(setup):
    scene, config, photo, _ = setup
    e = effect(setup)
    scene.coordinate_space = 'world_mm'
    with pytest.raises(ValueError): e.update(0, scene)
    scene.coordinate_space = 'legacy'
    scene.objects['cuadro'].extra['ajuste'] = [1, 0]
    with pytest.raises(ValueError): e.update(0, scene)
    scene.objects['cuadro'].extra.clear()
    source = scene.scan_dir+'/scene_pov.png'
    cv2.imwrite(source, np.zeros_like(photo))
    with pytest.raises(ValueError, match='another target or scene_pov'): e.update(0, scene)
    cv2.imwrite(source, photo)
    mask_path = config['bundle'].replace('trace.json', 'mask.png')
    cv2.imwrite(mask_path, np.ones((4, 4), np.uint8))
    with pytest.raises(ValueError, match='Stale vector mask'): e.update(0, scene)


def test_runtime_discovers_renders_and_reports_state(setup, tmp_path):
    from playground.runtime import Runtime
    scene, config, photo, _ = setup
    rt = Runtime(scene, canvas_size=scene.table_mm, demo=False, requests_dir=tmp_path/'requests')
    rt.spawn('tela_viva', 'cuadro', config)
    frame = rt.tick(1/30)
    assert frame.shape == photo.shape and frame.any()
    assert not rt.registry.errors
    assert len(rt.instances) == 1
    assert next(iter(rt.instances.values())).state()['mask_source'] == 'verified_pixel_mask'
