import json
import cv2
import numpy as np
import pytest
from slscan import cli,joint,selfcalib
from slscan.scene_simulator import FurnishedRoom
from slscan.scene_tracks import rigid_tracks
from slscan.rig import Rig


@pytest.mark.parametrize('views',[2,3])
def test_shared_extrinsic_bundle_and_last_projector_pose(views):
    room=FurnishedRoom(scale=.5,motion='rigid')
    X=np.random.default_rng(8).uniform([-220,-200,1100],[220,200,1650],(150,3))
    camera=[];projector=[];base=room.rig.projector()
    for R,t in room.camera_poses[:views]:
        cam=room.rig['osmo'];cam.R,cam.t=R,t
        pro=selfcalib.projector_at(base,cam)
        camera.append(joint.project(cam,X));projector.append(joint.project(pro,X))
        assert np.allclose(pro.R@cam.R.T,room.relative_R)
        assert np.allclose(pro.t-pro.R@cam.R.T@cam.t,room.relative_t)
    c=np.array(camera);p=np.array(projector)
    valid=np.all((c>=0)&(c<room.rig['osmo'].size)&np.isfinite(c),axis=(0,2))
    valid &=np.all((p>=0)&(p<base.size),axis=(0,2))
    rig,points,report,good=selfcalib.fit(c[:,valid],p[:,valid],room.rig['osmo'].size,
                                     motion='rigid',max_nfev=400)
    assert report['optimized']
    assert report['observability']['data_rank']==15+6*(views-1)
    assert max(r['rms'] for r in report['per_camera']+report['per_projector'])<.2
    assert 'data_rank_deficient' not in report['issues']
    assert abs(rig['osmo'].K[0,0]/room.rig['osmo'].K[0,0]-1)<.08
    assert report['motion']=='rigid' and rig.units=='projector_baseline'
    last=selfcalib.projector_at(rig.projector(),rig[f'osmo_{views-1}'])
    assert not np.allclose(last.t,rig.projector().t)
    baseline=np.linalg.norm(last.center_world()-rig[f'osmo_{views-1}'].center_world())
    assert baseline==pytest.approx(1)


def test_rigid_one_view_rejected_before_hardware(tmp_path,capsys):
    assert cli.main(['autocalib',str(tmp_path),'--out',str(tmp_path/'rig.json'),
                     '--display-confirmed','--motion','rigid','--views','1'])==2
    events=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert 'requiere --views 2 o 3' in events[-1]['msg']


def test_repeating_stationary_rig_does_not_create_observability():
    room=FurnishedRoom(scale=.5,motion='rigid')
    X=np.random.default_rng(5).uniform([-150,-100,1100],[150,100,1650],(65,3))
    c=joint.project(room.rig['osmo'],X);p=joint.project(room.rig.projector(),X)
    _,_,report,_=selfcalib.fit(np.stack([c]*3),np.stack([p]*3),room.rig['osmo'].size,
                              motion='rigid',max_nfev=60)
    assert report['observability']['data_rank']<report['observability']['global_parameters']
    assert 'data_rank_deficient' in report['issues']


def test_rigid_replay_requires_physical_photos_and_matching_motion(tmp_path,capsys):
    from slscan.graycode import Decoded,INVALID
    a=np.full((32,32),INVALID,np.uint16)
    dec=Decoded((32,32),a,a.copy(),np.zeros((32,32,3),np.float32),{'skip_bits':2})
    for v in range(3): dec.save(tmp_path/f'view_{v:02d}_map.npz')
    (tmp_path/'autocalib-session.json').write_text(json.dumps({'motion':'rigid','evidence':'synthetic',
                     'camera_size':[32,32],'projector_size':[1920,1080]}))
    args=['autocalib',str(tmp_path),'--replay','--out',str(tmp_path/'rig.json')]
    assert cli.main([*args,'--motion','camera'])==2
    assert 'no coincide' in capsys.readouterr().out
    assert cli.main(args)==2
    assert 'falta color.png' in capsys.readouterr().out
    with pytest.raises(ValueError,match='textura insuficiente'):
        rigid_tracks([dec]*3,[np.zeros((32,32),np.uint8)]*3)
