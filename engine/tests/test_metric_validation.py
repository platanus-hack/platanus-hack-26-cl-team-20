import numpy as np
from slscan.rig import Device
from slscan.metric_validation import outline_on_plane, pixel_uncertainty
from test_joint import scene


def test_local_mm_depends_on_depth_and_maximum_not_rms():
    cam=Device('cam','camera',(1000,1000),np.array([[1000.,0,500],[0,1000,500],[0,0,1]]),np.zeros(5))
    physical=np.column_stack((np.linspace(100,400,100),np.full(100,500)))
    line=physical+[0,1.5]
    near=outline_on_plane(cam,physical,line,[0,0,1],[0,0,1000],covered_length_mm=300,
                          expected_length_mm=300,uncertainty_mm=.1)
    far=outline_on_plane(cam,physical,line,[0,0,1],[0,0,2000],covered_length_mm=600,
                         expected_length_mm=600,uncertainty_mm=.1)
    assert near['status']=='passed' and far['status']=='failed'
    assert np.isclose(near['residual_mm']['max'],1.5)
    assert np.isclose(far['residual_mm']['max'],3.)
    line=physical.copy(); line[50,1]+=3
    worst=outline_on_plane(cam,physical,line,[0,0,1],[0,0,1000],covered_length_mm=300,
                          expected_length_mm=300,uncertainty_mm=.1)
    assert worst['residual_mm']['rms']<.5 and worst['status']=='failed'


def test_missing_coverage_or_uncertainty_never_passes():
    rig,_,views,_=scene()
    cam=rig['osmo']; p=views[0].paper_cam
    result=outline_on_plane(cam,p,p,[0,0,1],[0,0,1000],covered_length_mm=100,expected_length_mm=200,
                            uncertainty_mm=.1)
    assert result['status']=='inconclusive'
    result=outline_on_plane(cam,p,p,[0,0,1],[0,0,1000],covered_length_mm=200,expected_length_mm=200)
    assert result['status']=='inconclusive'


def test_pixel_uncertainty_scales_with_noise():
    rig,_,views,_=scene()
    v=views[0]
    a=pixel_uncertainty(rig,v.projected_cam,v.projected_px,.1)
    b=pixel_uncertainty(rig,v.projected_cam,v.projected_px,.2)
    assert np.allclose(b['sigma_3d_mm'],2*a['sigma_3d_mm'])
    assert np.isfinite(a['sigma_xyz_mm']).all()
    assert (a['sigma_3d_mm']>0).all()
