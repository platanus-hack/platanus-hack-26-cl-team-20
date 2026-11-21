import copy
import json
import cv2
import numpy as np
import pytest

from slscan import cli, metric_session as session
from slscan.calib import CharucoBoard
from test_joint import scene


def test_frame_contract_and_osmo_defaults():
    args = cli.build_parser().parse_args(['metric','capture'])
    assert (args.cam_size,args.rotate,args.downscale)==('3840x2160',270,2)
    scan = cli.build_parser().parse_args(['scan'])
    assert (scan.cam_size,scan.rotate,scan.downscale)==(args.cam_size,args.rotate,args.downscale)
    assert session.frame_size((3840,2160),270,2)==(1080,1920)
    with pytest.raises(ValueError,match='resolución'):
        session.check_frame(np.zeros((3840,2160,3),np.uint8),(1080,1920))
    with pytest.raises(ValueError,match='rotate'):
        session.frame_size((3840,2160),45,2)


def test_projected_pattern_coordinates_are_actual_opencv_centres():
    board = CharucoBoard(7,5,40.,30.)
    quad = np.array([[200,180],[1700,220],[1650,900],[240,840]],np.float32)
    canvas,px = session.adaptive_board(board,(1920,1080),quad)
    found = board.detect(canvas)
    assert found is not None and len(found['ids'])==24
    assert np.max(np.linalg.norm(found['img']-px[found['ids']],axis=1)) < .05
    assert all(cv2.pointPolygonTest(quad,tuple(p),True)>0 for p in px)


def test_reserved_evaluation_uses_no_nominal_scale_fit():
    truth,_,views,_ = scene()
    truth.extra['evidence']='synthetic'
    result = session.evaluate_reserved(truth,views[:3],uncertainty_mm=.2)
    assert result['status']=='unverified'  # outlines/repeatability not measured
    assert all(v['status']=='passed_local_geometry' for v in result['views'])
    assert max(v['dimension_error_mm']['max'] for v in result['views']) < .001
    with pytest.raises(ValueError,match='fuga'):
        session.evaluate_reserved(truth,views[:3],[views[0].name])
    bad = copy.deepcopy(truth)
    bad.projector().t *= 1.03
    result = session.evaluate_reserved(bad,views[:3],uncertainty_mm=.2)
    assert result['status']=='failed'
    assert max(v['dimension_error_mm']['max'] for v in result['views']) > 2


def test_cli_joint_initializes_without_truth_and_separates_holdout(tmp_path):
    _,_,views,_ = scene(n=13)
    root = tmp_path/'session'
    for i,v in enumerate(views):
        session.save_view(root/v.name/'observation.json',v,'train' if i<10 else 'holdout',
                          status='accepted',evidence='synthetic')
    path = tmp_path/'rig.json'
    report = tmp_path/'report.json'
    assert cli.main(['metric','calib',str(root),'--out',str(path),'--report',str(report),
                     '--repeat','2','--restarts','2','--html',''])==0
    data = json.loads(report.read_text())
    assert data['poses']==10
    assert data['heldout']['reserved_poses_available']==3
    assert data['heldout']['status']=='unverified'
    assert data['uncertainty']['bootstrap_requested']==2
    assert len(data['uncertainty']['bootstrap_samples'])==2
    assert data['physical_validation']['status']=='unverified'
    assert max(v['dimension_error_mm']['max'] for v in data['heldout']['views']) < .1
    raw = json.loads(path.read_text())
    assert len(raw['extra']['provenance']['observations'])==13


def test_rejected_pose_or_resolution_is_not_silently_skipped(tmp_path):
    _,_,views,_ = scene(n=4)
    for v in views:
        session.save_view(tmp_path/v.name/'observation.json',v,'train',status='accepted',evidence='synthetic')
    views[0].cam_size=(2160,3840)
    session.save_view(tmp_path/views[0].name/'observation.json',views[0],'train',status='accepted',evidence='synthetic')
    with pytest.raises(ValueError,match='resolución'):
        session.load_views(tmp_path)
    session.save_view(tmp_path/views[0].name/'observation.json',views[0],'train',status='rejected',evidence='synthetic')
    with pytest.raises(ValueError,match='rechazada'):
        session.load_views(tmp_path)


def test_capture_never_opens_hardware_without_display_confirmation(monkeypatch,tmp_path):
    from slscan import capture
    def forbidden(*a,**kw):
        pytest.fail('hardware opened')
    monkeypatch.setattr(capture,'Camera',forbidden)
    monkeypatch.setattr(capture,'PatternWindow',forbidden)
    with pytest.raises(ValueError,match='turno libre'):
        cli.main(['metric','capture','--out',str(tmp_path)])


def test_capture_closes_projector_on_delivered_frame_mismatch(monkeypatch,tmp_path):
    from slscan import capture
    closed=[]
    class Window:
        def __init__(self,*a,**kw): pass
        def close(self): closed.append('projector')
    class Camera:
        def __init__(self,*a,**kw): pass
        def read(self): return np.zeros((108,192,3),np.uint8)
        def close(self): closed.append('camera')
    monkeypatch.setattr(capture,'Camera',Camera)
    monkeypatch.setattr(capture,'PatternWindow',Window)
    monkeypatch.setattr(capture,'list_displays',lambda:[{'index':0,'w':1920,'h':1080}])
    from types import SimpleNamespace
    monkeypatch.setattr(session.shutil,'disk_usage',lambda _:SimpleNamespace(free=3*1024**3))
    with pytest.raises(ValueError,match='resolución entregada'):
        cli.main(['metric','capture','--display-confirmed','--out',str(tmp_path/'capture')])
    assert sorted(closed)==['camera','projector']


def test_low_disk_blocks_before_hardware(monkeypatch,tmp_path):
    from slscan import capture
    from types import SimpleNamespace
    monkeypatch.setattr(capture,'list_displays',lambda:[{'index':0,'w':1920,'h':1080}])
    monkeypatch.setattr(session.shutil,'disk_usage',lambda _:SimpleNamespace(free=200*1024**2))
    def forbidden(*a,**kw): pytest.fail('hardware opened on low disk')
    monkeypatch.setattr(capture,'PatternWindow',forbidden)
    with pytest.raises(ValueError,match='disco insuficiente'):
        cli.main(['metric','capture','--display-confirmed','--out',str(tmp_path/'capture')])
