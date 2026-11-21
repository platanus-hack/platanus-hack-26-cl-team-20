"""Full serialized replay regression: no decoder, fitter or renderer patches."""
import json
import cv2
import numpy as np
import pytest
from slscan import cli,selfcalib
from slscan.graycode import Decoded,INVALID
from slscan.rig import Rig


def sparse_map(offset=0):
    px=np.full((64,64),INVALID,np.uint16);py=px.copy()
    y,x=np.mgrid[0:12,0:12]
    px[y*4+2,x*4+2]=x*4+2+offset
    py[y*4+2,x*4+2]=y*4+2
    return Decoded((64,64),px,py,np.zeros((64,64,3),np.float32),{'skip_bits':2})


def test_single_pixel_correspondences_survive_save_load(tmp_path):
    maps=[]
    for i in range(3):
        path=tmp_path/f'view_{i}_map.npz';sparse_map().save(path);maps.append(Decoded.load(path))
    c,p,report=selfcalib.dense_tracks(maps)
    assert c.shape==(3,144,2) and p.shape==(144,2)
    assert report['common_multi_pixel_cells']==0 and report['common_cells']==144
    assert report['sampling']=='includes_single_pixel_cells'
    assert report['selected_single_pixel_cells_per_view']==[144]*3
    assert np.allclose(c[0]-.5,p)


def test_missing_overlap_reports_counts_without_inventing_matches():
    with pytest.raises(ValueError,match=r'comunes \(0; multipíxel 0\).*vista 1: 144 píxeles'):
        selfcalib.dense_tracks([sparse_map(),sparse_map(400)])
    empty=sparse_map();empty.px[:]=INVALID;empty.py[:]=INVALID
    with pytest.raises(ValueError,match='vista 1: 0 píxeles, 0 celdas'):
        selfcalib.dense_tracks([empty])


@pytest.mark.parametrize('motion',['camera','rigid'])
def test_rendered_images_saved_maps_and_replay_cli_end_to_end(tmp_path,capsys,motion):
    root=tmp_path/'rendered-session'
    assert cli.main(['autocalib-simulate',str(root),'--camera-scale','.5','--motion',motion])==0
    generated=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert generated[-1]['step']=='simulated_session'
    assert sum(e['step']=='render' for e in generated)==132
    meta=json.loads((root/'autocalib-session.json').read_text())
    assert meta['status']=='captured' and meta['camera_size']==[540,960]
    assert meta['generator']=='raycast_gray_images_decode_save'
    maps=[Decoded.load(path) for path in sorted(root.glob('view_*_map.npz'))]
    assert len(maps)==3 and all(dec.valid.sum()>50000 for dec in maps)
    if motion=='rigid':
        from slscan.scene_tracks import rigid_tracks
        c,p,coverage=rigid_tracks(maps,[cv2.imread(str(root/f'view_{v:02d}/color.png')) for v in range(3)])
        assert c.shape==p.shape and c.shape[0]==3
        assert not np.allclose(p[0],p[1])
        assert coverage['common_physical_tracks']>=40
        assert coverage['projector_codes_used_as_temporal_ids'] is False
    else:
        _,_,coverage=selfcalib.dense_tracks(maps)
        assert coverage['common_cells']>=100
    truth=json.loads((root/'simulation-truth.json').read_text())
    out=root/'rig.json'
    assert cli.main(['autocalib',str(root),'--replay','--out',str(out),
                     '--scale',f"huincha:{truth['baseline_mm']}",'--scale-uncertainty-mm','0'])==0
    replay=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert replay[-1]['step']=='rig' and replay[-1]['ok']
    rig=Rig.load(out);raw=json.loads(out.read_text())
    assert rig.units=='mm' and raw['evidence']=='synthetic' and raw['metric_status']=='unverified'
    assert len(raw['input_hashes'])==(6 if motion=='rigid' else 3)
    assert raw['devices']['osmo']['pose_estimated']
    assert raw['extra']['selfcalibration_report']['optimized']
    assert all(np.isfinite(d.K).all() and np.isfinite(d.t).all() for d in rig.devices.values())
    # The generator never substitutes its exact K/poses into the estimator.
    assert raw['extra']['selfcalibration_report']['model']['focal_priors_px'][0]!=truth['devices']['osmo']['K'][0][0]
    if motion=='rigid':
        report=raw['extra']['selfcalibration_report'];cam=rig['osmo'];pro=rig.projector()
        assert report['operating_view']==2 and report['observability']['data_rank']==27
        assert len(report['heldout']['projector_reprojection_px'])==3
        assert np.allclose(pro.R@cam.R.T,report['relative_projector']['R'])
        assert np.allclose(pro.t-pro.R@cam.R.T@cam.t,
                           np.array(report['relative_projector']['t_baseline_units'])*truth['baseline_mm'])
        assert np.linalg.norm(pro.center_world()-cam.center_world())==pytest.approx(truth['baseline_mm'])
        assert rig.world=='osmo_reference' and np.allclose(rig[rig.world].t,0)
        from slscan.rigid_validation import evaluate
        evaluation=evaluate(root,out)
        assert evaluation['physical_metric_status']=='unverified'
        assert evaluation['reserved_xyz_mm']['n']==report['heldout']['tracks']
        assert np.isfinite(evaluation['reserved_xyz_mm']['rms'])
