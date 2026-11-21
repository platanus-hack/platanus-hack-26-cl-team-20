"""Independent geometric synthetic observations, never hardware evidence."""
import copy
import json
import cv2
import numpy as np
import pytest

from slscan.joint import PlaneView, calibrate, project, triangulate
from slscan.rig import Device, Rig


def scene(noise=0, n=10, seed=12, degenerate=False):
    rng = np.random.default_rng(seed)
    cam = Device('osmo', 'camera', (1080,1920),
        np.array([[1350.,0,530],[0,1370,945],[0,0,1]]), np.array([-.06,.015,.001,-.0007,.002]))
    pro = Device('proj', 'projector', (1920,1080),
        np.array([[1750.,0,970],[0,1720,565],[0,0,1]]), np.array([-.08,.02,-.0005,.001,.003]),
        cv2.Rodrigues(np.array([.02,-.12,.01]))[0], np.array([240.,-40,30]))
    truth = Rig({'osmo':cam,'proj':pro}, world='osmo')
    paper = np.array([[0.,0,0],[210,0,0],[210,297,0],[0,297,0]])
    xx, yy = np.meshgrid(np.linspace(15,195,6), np.linspace(15,282,8))
    grid = np.column_stack((xx.ravel(),yy.ravel(),np.zeros(xx.size)))
    views, points = [], []
    for i in range(n):
        rv = np.zeros(3) if degenerate else rng.uniform([-.5,-.5,-.12],[.5,.5,.12])
        R = cv2.Rodrigues(rv)[0]
        t = np.array([rng.uniform(-160,120),rng.uniform(-160,160),rng.uniform(850,1400)]) - R@paper.mean(0)
        X, Q = grid@R.T+t, paper@R.T+t
        c, p, q = project(cam,X), project(pro,X), project(cam,Q)
        views.append(PlaneView(f'pose_{i:02}', paper.copy(), q+rng.normal(0,noise,q.shape),
                               c+rng.normal(0,noise,c.shape),p,cam.size,pro.size))
        points.append(X)
    initial = copy.deepcopy(truth)
    for d in initial.devices.values():
        d.K[:2,:2] *= 1.025; d.K[:2,2] += [5,-7]; d.dist[:] = 0
    initial.projector().t += [2,-3,4]
    return truth, initial, views, points


@pytest.fixture(scope='module')
def fitted():
    truth, initial, views, points = scene()
    return truth, calibrate(views, initial, evidence='synthetic', restarts=1), views, points


def test_joint_noiseless_recovers_full_intrinsics_and_mm(fitted, tmp_path):
    truth, rig, views, points = fitted
    rep = rig.extra['metric_report']
    assert rep['status'] == 'candidate', rep['issues']
    assert rep['rms_px']['cam'] < .01 and rep['rms_px']['proj'] < .01
    error = []
    for v, X in zip(views,points):
        recovered = triangulate(rig['osmo'],rig.projector(),v.projected_cam,v.projected_px)
        error.extend(np.linalg.norm(recovered-X,axis=1))
    assert max(error) < .1
    for name in ('osmo','proj'):
        assert np.max(abs(rig[name].K-truth[name].K)) < .1
        assert np.max(abs(rig[name].dist-truth[name].dist)) < .001
    assert rep['physical_validation']['status'] == 'unverified'
    rig.save(tmp_path/'rig.json')
    raw = json.loads((tmp_path/'rig.json').read_text())
    assert raw['evidence'] == 'synthetic' and raw['schema_version'] == '1.0'
    assert np.allclose(raw['devices']['proj']['T_device_from_world'][:3],np.column_stack((rig['proj'].R,rig['proj'].t)))
    back = Rig.load(tmp_path/'rig.json')
    assert back.extra['metric_report'] == rep


def test_joint_rejects_missing_or_mismatched_data():
    _, initial, views, _ = scene(n=4)
    with pytest.raises(ValueError, match='>= 4'):
        calibrate(views[:3], initial)
    views[0].cam_size = (2160,3840)
    with pytest.raises(ValueError, match='resolución'):
        calibrate(views,initial)
    views[0].cam_size = initial['osmo'].size
    views[0].projected_cam = views[0].projected_cam[:2]
    with pytest.raises(ValueError, match='correspondencias'):
        calibrate(views,initial)


def test_degenerate_views_not_promoted():
    _, initial, views, _ = scene(n=4,degenerate=True)
    rig = calibrate(views,initial,distortion='k1',restarts=1,max_nfev=60,evidence='synthetic')
    assert rig.extra['metric_report']['status'] == 'rejected'
    assert 'insufficient_tilt_diversity' in rig.extra['metric_report']['issues']


def test_noise_is_reported_and_not_physical_approval():
    _, initial, views, _ = scene(noise=.15)
    rig = calibrate(views,initial,restarts=1,evidence='synthetic',max_nfev=120)
    r = rig.extra['metric_report']
    assert .05 < r['rms_px']['cam'] < .5
    assert r['physical_validation']['status'] == 'unverified'
    assert len(r['per_view']) == len(views)


def test_systematic_non_geometric_bias_rejected():
    _, initial, views, _ = scene(n=5)
    # A pixel discontinuity cannot be hidden by one global lens/pose model.
    for v in views:
        v.projected_cam[::2,0] += 4
    rig = calibrate(views,initial,restarts=1,distortion='k1',max_nfev=100,evidence='synthetic')
    assert 'high_reprojection_error' in rig.extra['metric_report']['issues']
