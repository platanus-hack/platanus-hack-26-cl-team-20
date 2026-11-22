import cv2
import numpy as np
from slscan import approx_rig,joint
from slscan.rig import Device
from slscan.selfcalib import intrinsic


def test_one_view_known_projector_centered_camera_recovers_changed_focal():
    camera=Device('osmo','camera',(1080,1920),intrinsic(1700,(1080,1920)),np.zeros(5))
    projector=approx_rig.fixed_projector((1920,1080),1.3,100)
    projector.R=cv2.Rodrigues(np.array([.06,-.12,.02]))[0]
    projector.t=np.array([.27,.03,.02]);baseline=np.linalg.norm(projector.t)
    X=np.random.default_rng(9).uniform([-.18,-.3,1.1],[.18,-.05,2.1],(150,3))
    c=joint.project(camera,X);p=joint.project(projector,X)
    rig,report=approx_rig.fit(c,p,camera.size,projector)
    assert report['optimized'] and report['data_rank']==6
    assert not report['focal_at_bound'] and report['positive_final']
    assert abs(rig['osmo'].K[0,0]/1700-1)<.01
    assert np.linalg.norm(rig.projector().t-projector.t/baseline)<.02
    assert max(report['rms_px'].values())<.05
    assert rig.units=='projector_baseline'
    assert np.allclose(rig['osmo'].K[:2,2],[539.5,959.5])
    assert np.allclose(rig.projector().K,projector.K)
    assert not np.any(rig['osmo'].dist) and not np.any(rig.projector().dist)


def test_offset_convention_is_explicit():
    p=approx_rig.fixed_projector((1920,1080),1.2,100)
    assert p.K[0,0]==2304 and p.K[1,1]==2304
    assert p.K[1,2]==1079.5
    assert approx_rig.fixed_projector((1920,1080),1.2,0).K[1,2]==539.5
