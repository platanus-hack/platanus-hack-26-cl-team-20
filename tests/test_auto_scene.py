import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from playground.auto_scene import build_prepared,select_objects,effect_for,activate_runtime
from playground.automatic_masks import automatic_masks
from playground.appearance import manifest_path
from playground.runtime import Runtime
from playground.scene import Scene,SceneObject
from slscan.contracts import digest


def test_automatic_grid_needs_no_object_names_and_deduplicates():
    mask=np.zeros((60,80),bool);mask[10:50,15:65]=True
    calls=[]
    class Decoder:
        def run(self,_,feed):
            calls.append(feed['point_coords'])
            np.testing.assert_array_equal(feed['point_labels'],[[1,-1]])
            return np.where(mask,4.,-4.)[None,None],np.array([.95]),None
    model=SimpleNamespace(decoder=Decoder(),embedding=np.zeros(1),resized=(80,60),set_image=lambda _:None)
    proposals,report=automatic_masks(model,np.zeros((60,80,3),np.uint8),columns=3,rows=2,min_area=10)
    assert len(calls)==6 and len(proposals)==1 and report['proposals']==6
    np.testing.assert_array_equal(proposals[0]['mask'],mask)
    assert proposals[0]['type']=='unknown'


def test_selection_preserves_known_foreground_and_disjoint_unknown_coverage():
    fg=np.zeros((120,160),bool);fg[20:70,30:90]=True
    all_mask=np.ones_like(fg)
    proposals=[dict(mask=all_mask,area=int(all_mask.sum()),type='unknown'),
               dict(mask=fg,area=int(fg.sum()),type='plant')]
    selected=select_objects(proposals,max_objects=2)
    assert [s['type'] for s in selected]==['plant','unknown']
    assert not (selected[0]['mask']&selected[1]['mask']).any()
    assert (selected[0]['mask']|selected[1]['mask']).all()
    assert effect_for('unknown')[0]=='contour_breathe'
    assert effect_for('wall')[0]=='surface_lines'


@pytest.fixture
def staged(tmp_path):
    scan=tmp_path/'captures'/'new-scene';scan.mkdir(parents=True)
    root=manifest_path(scan).parent;root.mkdir(parents=True)
    run=root/'auto-test';run.mkdir();appearance=run/'appearance';appearance.mkdir()
    image=np.full((120,160,3),(30,60,150),np.uint8);image[:,80:]=(90,180,30)
    cv2.imwrite(str(scan/'scene_pov.png'),image);cv2.imwrite(str(appearance/'appearance.png'),image)
    (appearance/'cleanup.json').write_text(json.dumps(dict(input_hashes={'scene_pov.png':digest(scan/'scene_pov.png')})))
    a=np.zeros((120,160),bool);a[5:110,5:70]=True;a[35:50,30:45]=False
    b=np.zeros_like(a);b[30:110,95:150]=True
    proposals=[dict(mask=a,area=int(a.sum()),type='picture',semantic_score=.8),
               dict(mask=b,area=int(b.sum()),type='cushion',semantic_score=.9)]
    path=build_prepared(scan,appearance/'appearance.png',proposals,root,run,max_objects=2)
    scene=Scene(table_mm=(160,120),projector_px=True,scan_dir=str(scan))
    scene.objects['legacy']=SceneObject('legacy','old object','prop',[0,0,0,100,100,0],[[0,0],[100,0],[100,100],[0,100]])
    rt=Runtime(scene=scene,canvas_size=(160,120),demo=False,requests_dir=tmp_path/'requests')
    rt.spawn('contour_glow','legacy')
    return scan,path,rt,a|b


def test_preparation_does_not_publish_and_activation_is_atomic_and_idempotent(staged):
    scan,path,rt,mask=staged
    assert not manifest_path(scan).exists() and 'legacy' in rt.scene.objects
    out=rt.command('activate_prepared',{'scan':str(scan),'prepared':str(path)})
    assert len(out['effects'])==2 and all(x.startswith('obj_') for x in out['objects'])
    assert 'legacy' not in rt.scene.objects and not rt.demo
    frame=rt.tick(.6);assert frame.any() and not frame[~mask].any()
    ids=list(rt.instances);t=[e.t for e in rt.instances.values()]
    assert activate_runtime(rt,scan,path)['unchanged']
    assert list(rt.instances)==ids and [e.t for e in rt.instances.values()]==t
    assert 'activate_prepared' in rt.command('schema')


def test_failed_activation_keeps_manifest_scene_and_effects(staged):
    scan,path,rt,_=staged;activate_runtime(rt,scan,path)
    previous=manifest_path(scan).read_bytes();scene=rt.scene;instances=rt.instances
    data=json.loads(path.read_text());data['composition'][0]['effect']='missing_effect'
    broken=path.with_name('broken.json');broken.write_text(json.dumps(data))
    with pytest.raises(KeyError):activate_runtime(rt,scan,broken)
    assert manifest_path(scan).read_bytes()==previous
    assert rt.scene is scene and rt.instances is instances
    assert rt.tick(.1).any()


@pytest.mark.parametrize('reason',['wrong_scan','metric','size','stale_source','overlay'])
def test_activation_rejects_misaligned_or_unavailable_runtime(staged,reason):
    scan,path,rt,_=staged;scene=rt.scene;instances=rt.instances
    if reason=='wrong_scan':rt.scene.scan_dir=str(scan.parent/'other')
    if reason=='metric':rt.metric_mode=True
    if reason=='size':rt.canvas_size=(80,60)
    if reason=='stale_source':(scan/'scene_pov.png').write_bytes(b'changed')
    if reason=='overlay':rt.overlay=np.ones((120,160,3),np.uint8);rt.overlay_until=1e20
    with pytest.raises(ValueError):activate_runtime(rt,scan,path)
    assert rt.scene is scene and rt.instances is instances and not manifest_path(scan).exists()


def test_semantic_confidence_preserves_complete_foreground_before_overlapping_cushion():
    face=np.zeros((100,100),bool);face[20:90,20:80]=True
    cushion=np.zeros_like(face);cushion[10:50,10:90]=True
    selected=select_objects([dict(mask=cushion,area=int(cushion.sum()),type='cushion',semantic_score=.33),
                             dict(mask=face,area=int(face.sum()),type='mask',semantic_score=.36)])
    assert selected[0]['type']=='mask'
    np.testing.assert_array_equal(selected[0]['mask'],face)
    assert not selected[1]['mask'][face].any()


def test_nested_conflicting_foreground_labels_remain_unknown_but_wall_is_allowed():
    from playground.grounded_labels import resolve_ambiguity
    result=resolve_ambiguity([dict(type='plush',score=.31,box=[0,0,100,100]),
                              dict(type='mask',score=.36,box=[30,40,90,90]),
                              dict(type='wall',score=.35,box=[0,0,100,100])])
    assert [d['type'] for d in result]==['unknown','unknown','wall']


def test_semantic_timeout_keeps_automatic_unknown_fallback(monkeypatch,tmp_path):
    import subprocess
    from playground.auto_scene import detect_labels
    def timeout(*a,**kw):raise subprocess.TimeoutExpired('worker',240)
    monkeypatch.setattr(subprocess,'run',timeout)
    result=detect_labels(tmp_path/'image.png',tmp_path)
    assert result['status']=='unavailable' and result['detections']==[]
