import copy
import cv2
import numpy as np
from slscan import joint, metric, oficio_fit
from slscan.rig import Device, Rig
from slscan.selfcalib import intrinsic


def test_large_markers_preserve_ids_and_gray_pixel_convention():
    from types import SimpleNamespace
    from slscan.oficio_capture import marker_pattern,detect_markers
    planes=[]
    for x in [200,900]:
        q=np.array([[x,200],[x+250,200],[x+250,600],[x,600]],float)
        planes.append(SimpleNamespace(quad_cam=q,quad_proj=q,H_cam_proj=np.eye(3),fit={}))
    image,known=marker_pattern(planes)
    observations=detect_markers(image,known,planes)
    assert [r['marker_id'] for r in observations]==[2,1]
    for row,plane in zip(observations,planes):
        assert row['gray_pixel_convention']=='opencv_centers'
        assert np.allclose(row['paper_proj_gray'],plane.quad_proj-.5)
        assert np.max(np.linalg.norm(np.array(row['projected_cam'])-row['projected_px'],axis=1))<1.


def test_two_rectangles_recover_nominal_scale_and_do_not_certify_accuracy():
    cam=Device('osmo','camera',(1080,1920),intrinsic(1600,(1080,1920)),np.zeros(5))
    pro=Device('proj','projector',(1920,1080),intrinsic(2600,(1920,1080),(959.5,1000)),
               np.zeros(5),cv2.Rodrigues(np.array([.04,-.07,.02]))[0],np.array([260.,15.,10.]))
    rng=np.random.default_rng(7);rows=[]
    for i,(r,t) in enumerate([([.1,-.2,.1],[-280,-240,1800]),([-.15,.3,-.08],[150,180,2100])]):
        R=cv2.Rodrigues(np.array(r))[0];t=np.array(t)
        corners=np.column_stack((metric.paper_corners_mm((216,330)),np.zeros(4)))@R.T+t
        inside=np.column_stack((rng.uniform([30,30],[186,300],(44,2)),np.zeros(44)))@R.T+t
        rows.append({'name':str(i),'size_mm':[216,330],
            'paper_cam':joint.project(cam,corners),'paper_proj_gray':joint.project(pro,corners),
            'projected_cam':joint.project(cam,inside[:4]),'projected_px':joint.project(pro,inside[:4]),
            'gray_cam':joint.project(cam,inside[4:]),'gray_px':joint.project(pro,inside[4:])})
    X=rng.uniform([-300,-300,1500],[400,500,2600],(100,3))
    initial=Rig({'osmo':copy.deepcopy(cam),'proj':copy.deepcopy(pro)},world='osmo',units='projector_baseline')
    initial['osmo'].K[:2,:2]*=1.03;initial.projector().K[:2,:2]*=.98
    initial.projector().t/=np.linalg.norm(pro.t)
    rig,report=oficio_fit.fit(rows,joint.project(cam,X),joint.project(pro,X),initial)
    assert report['optimized']
    assert report['metric_status']=='unverified'
    assert 'independent_validation_missing' in report['issues']
    assert abs(np.linalg.norm(rig.projector().t)/np.linalg.norm(pro.t)-1)<.01
    assert max(abs(e) for sheet in report['sheets'] for e in sheet['side_errors_mm'])<.5
    assert rig.units=='mm'
