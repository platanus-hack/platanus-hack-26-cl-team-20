import json
from types import SimpleNamespace
import cv2
import numpy as np
import pytest
from slscan import selfcalib,joint,cli,autocalib
from slscan.scene_simulator import FurnishedRoom,room_surfaces


@pytest.fixture(scope='module')
def correspondences():
    room=FurnishedRoom(scale=.5)
    rng=np.random.default_rng(2)
    X=rng.uniform([-300,-200,1000],[300,200,1600],(80,3))
    projector=joint.project(room.rig.projector(),X);camera=[]
    for R,t in room.camera_poses:
        room.rig['osmo'].R,room.rig['osmo'].t=R,t
        camera.append(joint.project(room.rig['osmo'],X))
    camera=np.array(camera)
    valid=np.all((camera>=0)&(camera<room.rig['osmo'].size),axis=(0,2))&np.all((projector>=0)&(projector<room.rig.projector().size),axis=1)
    return camera[:,valid],projector[valid],room,X[valid]


@pytest.fixture(scope='module')
def fitted(correspondences):
    camera,projector,room,_=correspondences
    return selfcalib.fit(camera,projector,room.rig['osmo'].size,max_nfev=200)


def test_joint_scene_fit_has_no_metric_scale_or_paper(correspondences,fitted):
    _,_,truth,_=correspondences
    rig,X,report,_=fitted
    assert report['optimized']
    assert report['observability']['prior_rows_excluded']
    assert report['observability']['data_rank']==report['observability']['global_parameters']
    assert report['rms_px']['cam']<.1
    assert abs(rig['osmo'].K[0,0]/truth.rig['osmo'].K[0,0]-1)<.05
    assert rig.units=='projector_baseline'
    assert np.linalg.norm(rig.projector().center_world())==pytest.approx(1)
    assert report['metric_status']=='unverified' and 'scale_unmeasured' in report['issues']
    assert np.ptp(X[:,2])>.5


def test_scale_gauge_reprojection_and_budget(correspondences):
    camera,_,room,X=correspondences
    room.rig['osmo'].R,room.rig['osmo'].t=room.camera_poses[0]
    original=joint.project(room.rig.projector(),X)
    room.rig.projector().t*=3
    assert np.allclose(original,joint.project(room.rig.projector(),X*3))
    assert selfcalib.scale_budget(250,1)['scale_only_error_at_distance_mm']==4
    assert selfcalib.scale_budget(300,.2)['scale_only_error_at_distance_mm']==pytest.approx(2/3)


def test_single_view_cannot_claim_full_observability(correspondences):
    c,p,room,_=correspondences
    _,_,report,_=selfcalib.fit(c[:1],p,room.rig['osmo'].size,max_nfev=60)
    assert 'data_rank_deficient' in report['issues']
    assert report['metric_status']=='unverified'


def test_scene_has_relief_and_no_paper():
    surfaces=room_surfaces()
    names=' '.join(s['name'] for s in surfaces)
    assert 'A4' not in names and 'Soporte' not in names
    assert all(name in names for name in ['Mesa','Tecla','Taza','Cuadro','Pared'])
    z=np.concatenate([s['corners'][:,2] for s in surfaces])
    assert np.ptp(z)>1000


def test_cli_never_opens_hardware_before_confirmation(tmp_path,monkeypatch,capsys):
    from slscan import capture
    def forbidden(*args,**kwargs):pytest.fail('hardware opened')
    monkeypatch.setattr(capture,'Camera',forbidden);monkeypatch.setattr(capture,'PatternWindow',forbidden)
    assert cli.main(['autocalib',str(tmp_path/'session'),'--out',str(tmp_path/'rig.json')])==2
    rows=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert all(r['stage']=='autocalib' and 0<=r['progress']<=1 for r in rows)
    assert rows[-1]['ok'] is False and 'q21' in rows[-1]['msg']
    assert not (tmp_path/'rig.json').exists()


def test_scale_requires_identified_keys_and_preserves_uncertainty(fitted):
    rig,_,_,_=fitted
    args=SimpleNamespace(scale='teclas:19',scale_pixels=None,scale_uncertainty_mm=None)
    with pytest.raises(ValueError,match='scale-pixels'):autocalib.metric_scale(args,rig,[])
    args.scale='huincha:300'
    factor,scale=autocalib.metric_scale(args,rig,[])
    assert factor==pytest.approx(300) and scale['uncertainty_bound_mm'] is None
    assert scale['verified'] is False
    args.scale_uncertainty_mm=1
    _,scale=autocalib.metric_scale(args,rig,[])
    assert scale['budget']['scale_only_error_at_distance_mm']==pytest.approx(10/3)


def test_replay_jsonl_metric_contract_and_final_camera(tmp_path,monkeypatch,capsys,correspondences):
    from slscan.rig import Rig
    camera,projector,room,_=correspondences
    root=tmp_path/'session';root.mkdir()
    (root/'autocalib-session.json').write_text(json.dumps({'evidence':'synthetic',
        'camera_size':[540,960],'projector_size':[1920,1080]}))
    # Decoder separately tested on rendered images. Here the adapter is replaced
    # to exercise CLI fitting/serialization against controlled observations.
    for i in range(3): (root/f'view_{i:02d}_map.npz').write_bytes(b'controlled correspondence fixture')
    monkeypatch.setattr(autocalib.graycode.Decoded,'load',lambda _:SimpleNamespace(size=(540,960)))
    monkeypatch.setattr(selfcalib,'dense_tracks',lambda maps:(camera,projector,
                        {'fixture':True,'common_cells':len(projector),'sampling':'controlled_fixture'}))
    out=root/'rig.json'
    args=['autocalib',str(root),'--replay','--out',str(out),'--scale','huincha:300',
          '--scale-uncertainty-mm','1','--max-nfev','250']
    assert cli.main(args)==0
    messages=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert all(r['stage']=='autocalib' and 0<=r['progress']<=1 for r in messages)
    assert messages[-1]['step']=='rig' and messages[-1]['ok']
    data=json.loads(out.read_text());rig=Rig.load(out)
    assert data['format']=='slscan-rig/2' and data['units']=='mm'
    assert data['evidence']=='synthetic' and data['metric_status']=='unverified'
    assert len(data['input_hashes'])==3 and data['validated_range'] is None
    assert rig.world=='osmo_reference'
    assert data['devices']['osmo']['pose_estimated'] is True
    assert data['devices']['osmo_reference']['pose_estimated'] is False
    assert not np.allclose(rig['osmo'].t,0)
    before=out.read_bytes()
    assert cli.main(args)==2 and out.read_bytes()==before


def test_replay_failure_is_jsonl_without_hardware(tmp_path,capsys):
    out=tmp_path/'rig.json'
    assert cli.main(['autocalib',str(tmp_path),'--replay','--out',str(out)])==2
    events=[json.loads(s) for s in capsys.readouterr().out.splitlines()]
    assert events[-1]['ok'] is False and not out.exists()


@pytest.mark.parametrize('answers,complete',[('continue\ncontinue\n',True),('',False)])
def test_live_adapter_uses_requested_devices_and_closes_on_cancel(tmp_path,monkeypatch,capsys,answers,complete):
    import io
    from slscan import capture,graycode
    closed=[];opened=[]
    class Camera:
        name='osmo'
        def __init__(self,*args,**kwargs):opened.append(('camera',args,kwargs))
        def read(self):return np.zeros((1920,1080,3),np.uint8)
        def flush(self,n):pass
        def metadata(self):return {'fixture':'fake hardware'}
        def close(self):closed.append('camera')
    class Window:
        def __init__(self,*args,**kwargs):opened.append(('window',args,kwargs))
        def solid(self,value):pass
        def show(self,image):pass
        def close(self):closed.append('window')
    spec=graycode.sequence((1920,1080))[0]
    def fake_sequence(win,cams,specs,pattern,out,settle,flush,observe):
        path=out/'osmo';path.mkdir();(path/'frame.png').write_bytes(b'fixture')
        observe(spec,cams[0],cams[0].read())
    monkeypatch.setattr(capture,'Camera',Camera);monkeypatch.setattr(capture,'PatternWindow',Window)
    monkeypatch.setattr(capture,'capture_sequence',fake_sequence)
    monkeypatch.setattr(capture,'list_displays',lambda:[{'index':0,'w':1920,'h':1080}])
    monkeypatch.setattr(capture,'resolve_screen',lambda _:0)
    monkeypatch.setattr(graycode,'load_sequence',lambda folder:[(spec,folder/'frame.png')])
    monkeypatch.setattr(graycode,'decode',lambda *a,**kw:SimpleNamespace(save=lambda p:p.write_bytes(b'map fixture')))
    monkeypatch.setattr(autocalib.shutil,'disk_usage',lambda _:SimpleNamespace(free=3*1024**3))
    monkeypatch.setattr(autocalib.sys,'stdin',io.StringIO(answers))
    args=SimpleNamespace(views=3,settle=0)
    root=tmp_path/'capture'
    if complete:
        maps,metadata=autocalib.capture(args,root,None)
        assert len(maps)==3 and metadata['status']=='captured'
        assert metadata['motion']=='rigid' and not metadata['projector_fixed_required']
    else:
        with pytest.raises(ValueError,match='no confirmado'):autocalib.capture(args,root,None)
        assert json.loads((root/'autocalib-session.json').read_text())['status']=='failed'
    assert sorted(closed)==['camera','window']
    assert opened[0][2]['screen']=='1920x1080'
    assert opened[1][2]['size']=='3840x2160' and opened[1][2]['rotate']==270 and opened[1][2]['downscale']==2
    messages=[json.loads(s) for s in capsys.readouterr().out.splitlines()]
    assert any(m['step']=='move_camera' for m in messages)
    movement=next(m for m in messages if m['step']=='move_camera')
    assert movement['motion']=='rigid' and 'conjunto completo' in movement['msg']
